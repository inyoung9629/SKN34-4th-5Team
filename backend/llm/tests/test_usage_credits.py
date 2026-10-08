"""채팅 토큰 사용량(llm.service.usage): 예약·계량·정산·소유권·월 경계·HTTP/SSE. 실제 provider 호출 없음."""
import threading
import uuid
from datetime import datetime, timezone as dt_tz
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import RunnableLambda
from rest_framework.test import APIClient

from llm.models import ChatSession, UsageCharge, UsageWallet
from llm.service import chat as chat_service
from llm.service import usage
from llm.service.chat_thread import ChatThread
from llm.tests.test_v2_chat import PricedModel, CheckpointTestCase, FakeChain, _read_sse_body, patch_chain

User = get_user_model()


def _model(input_tokens=None, output_tokens=None, text="답"):
    meta = None if input_tokens is None else {"input_tokens": input_tokens, "output_tokens": output_tokens,
                                             "total_tokens": input_tokens + output_tokens}
    return FakeModel(messages=iter([AIMessage(text, usage_metadata=meta)] * 50))


class FakeModel(PricedModel, GenericFakeChatModel):
    pass


class UsageGraph:
    """v2 get_graph() 가짜: 메인 model 호출 + 중첩 runnable 안의 하위 model 호출(하위 Agent 흉내) 뒤 답."""

    def __init__(self, calls, fail_after=None, gate=None):
        self.calls, self.fail_after, self.gate = calls, fail_after, gate

    def stream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
        if self.gate:
            yield (), "messages", (AIMessageChunk("…"), {"langgraph_node": "model"})
        for i, (model, nested) in enumerate(self.calls):
            if self.fail_after == i:
                raise RuntimeError("provider exploded")
            if nested:
                RunnableLambda(lambda _: model.invoke("x")).invoke(None)
            else:
                model.invoke("x")
            if self.gate:
                self.gate.wait(5)
        yield (), "messages", (AIMessageChunk("답"), {"langgraph_node": "model"})
        yield (), "updates", {"model": {"messages": [AIMessage("답")]}}


def _charge(session):
    return UsageCharge.objects.get(session_id=session.id)


class AllowancePolicyTest(TestCase):
    def test_settings_defaults_and_environment_overrides(self):
        import ast
        import os
        from pathlib import Path
        names = {"USAGE_GUEST_TOKENS", "USAGE_MEMBER_MONTHLY_TOKENS"}
        tree = ast.parse((Path(__file__).resolve().parents[2] / "config/settings.py").read_text())
        assignments = ast.Module(body=[node for node in tree.body if isinstance(node, ast.Assign)
                                      and any(isinstance(target, ast.Name) and target.id in names
                                              for target in node.targets)], type_ignores=[])
        for env, expected in (({}, (500_000, 999_999_000)),
                              ({"USAGE_GUEST_TOKENS": "12345", "USAGE_MEMBER_MONTHLY_TOKENS": "67890"},
                               (12345, 67890))):
            namespace = {"os": os}
            with patch.dict(os.environ, env, clear=True):
                exec(compile(assignments, "usage settings", "exec"), namespace)
            with override_settings(**{name: namespace[name] for name in names}):
                self.assertEqual((usage._limit(False), usage._limit(True)), expected)
                guest = usage.balance()
                self.assertEqual(guest["remaining_credits"], f"{expected[0] / 1000:.3f}")
                self.assertIsNone(guest["resets_at"])
        with patch.object(usage, "settings", object()):
            self.assertEqual((usage._limit(False), usage._limit(True)), (500_000, 999_999_000))
        self.assertEqual(usage.TOKENS_PER_CREDIT, 1000)

    def test_increased_allowance_preserves_existing_wallet_and_charges(self):
        user = User.objects.create_user(username="policy-member")
        for owner, session_owner, old_limit, new_limit in (
            ({"user": user}, {"user": user}, 100_000, 999_999_000),
            ({"guest": (guest := uuid.uuid4())}, {"guest": guest}, 10_000, 500_000),
        ):
            member = "user" in owner
            session = ChatSession.objects.create(**session_owner)
            wallet = UsageWallet.objects.create(**owner, period=usage.period_for(member), used_tokens=1234)
            charge = UsageCharge.objects.create(wallet=wallet, session_id=session.id, period=wallet.period,
                                                reserved_tokens=2000)
            name = "USAGE_MEMBER_MONTHLY_TOKENS" if member else "USAGE_GUEST_TOKENS"
            with override_settings(**{name: old_limit}):
                self.assertEqual(usage.balance(**owner)["remaining_tokens"], old_limit - 1234)
            with override_settings(**{name: new_limit}):
                dto = usage.balance(**owner)
                self.assertEqual((dto["limit_tokens"], dto["used_tokens"], dto["reserved_tokens"],
                                  dto["remaining_tokens"]), (new_limit, 1234, 2000, new_limit - 1234))
            wallet.refresh_from_db()
            charge.refresh_from_db()
            self.assertEqual(wallet.used_tokens, 1234)
            self.assertEqual((charge.status, charge.reserved_tokens, charge.charged_tokens),
                             (UsageCharge.RESERVED, 2000, 0))
            self.assertTrue(ChatSession.objects.filter(pk=session.pk, **session_owner).exists())


@override_settings(USAGE_GUEST_TOKENS=10_000, USAGE_MEMBER_MONTHLY_TOKENS=100_000)
class MeterUnitTest(TestCase):
    def test_duplicate_run_id_counted_once_and_unknown_flagged(self):
        meter = usage.Meter()
        run = uuid.uuid4()
        result = LLMResult(generations=[[ChatGeneration(message=AIMessage("a", usage_metadata={
            "input_tokens": 3, "output_tokens": 4, "total_tokens": 7}))]])
        meter.on_llm_end(result, run_id=run)
        meter.on_llm_end(result, run_id=run)
        meter.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage("b"))]]), run_id=uuid.uuid4())
        self.assertEqual(meter.totals(), (3, 4, 2, 1))

    def test_token_usage_fallback_and_invalid_values(self):
        self.assertEqual(usage._usage(LLMResult(generations=[[]], llm_output={
            "token_usage": {"prompt_tokens": 5, "completion_tokens": 6}})), (5, 6))
        bad = AIMessage("x")
        bad.usage_metadata = {"input_tokens": -1, "output_tokens": 2, "total_tokens": 1}
        self.assertEqual(usage._usage(LLMResult(generations=[[ChatGeneration(message=bad)]])), (None, 2))

    def test_admitted_turn_not_limited_by_actual_or_predicted_usage(self):
        meter = usage.Meter()
        meter.record((100_000, 50_000))
        meter.check(run_id="next")
        meter.record((1, 1), "next")
        self.assertEqual(meter.totals(), (100001, 50001, 2, 0))

    def test_large_history_and_tools_do_not_predict_exhaustion(self):
        from langchain_core.messages import HumanMessage, SystemMessage
        meter = usage.Meter()
        meter.on_chat_model_start({}, [[SystemMessage("규칙 " * 10000), HumanMessage("질문 " * 10000)]],
                                  run_id="large", invocation_params={"model": "unknown-tokenizer", "max_tokens": 4000,
                                                                      "tools": [{"description": "x " * 10000}]})
        meter.record((90000, 1), "large")
        self.assertEqual(meter.totals(), (90000, 1, 1, 0))

    def test_partial_usage_is_preserved_with_unknown_state(self):
        meter = usage.Meter()
        meter.record((11, None), "partial")
        meter.record((None, 2), "other")
        self.assertEqual(meter.totals(), (11, 2, 2, 2))

    def test_output_safety_cap_is_not_credit_exhaustion(self):
        for params in ({}, {"max_tokens": 10**6}, {"max_tokens": 0}):
            with self.assertRaises(usage.UnsafeOutputLimit):
                usage.Meter().on_chat_model_start({}, [[]], invocation_params=params)
        with self.assertRaises(usage.UnsafeOutputLimit):
            usage.Meter().on_llm_start({}, ["hi"])

    def test_every_chat_openai_has_server_output_cap(self):
        from django.conf import settings
        from llm.tools import knowledge
        from llm.v1.rag.assistant import pipeline
        from llm.v1.rag.club import agent as club
        from llm.v1.rag.course import agent as course
        from llm.v1.rag.nearby import agent as nearby
        from llm.v1.rag.venue import agent as venue
        from llm.v2.agent import common
        seen = []
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-only"}), \
                patch("langchain_openai.ChatOpenAI.__init__", lambda self, **kw: seen.append(kw)) as _:
            common.llm.cache_clear()
            common.llm()
            common.llm.cache_clear()
            for module in (pipeline, club, course, nearby, venue):
                old, module._llm = module._llm, None
                try:
                    module.llm()
                finally:
                    module._llm = old
        self.assertEqual(len(seen), 6)
        self.assertTrue(all(kw["max_tokens"] == settings.USAGE_MAX_CALL_OUTPUT_TOKENS for kw in seen))
        import inspect
        self.assertIn("max_tokens=settings.USAGE_MAX_CALL_OUTPUT_TOKENS", inspect.getsource(knowledge))

    def test_final_empty_stream_chunk_bills_exact_usage_once(self):
        from langchain_core.outputs import ChatGenerationChunk

        class StreamingUsage(FakeModel):
            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                yield ChatGenerationChunk(message=AIMessageChunk("답"))
                yield ChatGenerationChunk(message=AIMessageChunk("", usage_metadata={
                    "input_tokens": 45000, "output_tokens": 23, "total_tokens": 45023}))

        meter = usage.Meter()
        token = usage._meter.set(meter)
        try:
            chunks = list(StreamingUsage(messages=iter([])).stream("긴 문맥 " * 5000))
        finally:
            usage._meter.reset(token)
        self.assertEqual("".join(chunk.content for chunk in chunks), "답")
        self.assertEqual(meter.totals(), (45000, 23, 1, 0))

    def test_error_with_partial_usage_counts_it_else_unknown(self):
        from langchain_core.outputs import ChatGenerationChunk
        meter = usage.Meter()
        partial = LLMResult(generations=[[ChatGeneration(message=AIMessage("부", usage_metadata={
            "input_tokens": 7, "output_tokens": 3, "total_tokens": 10}))]])
        meter.on_llm_error(RuntimeError(), run_id=uuid.uuid4(), response=partial)
        meter.on_llm_error(GeneratorExit(), run_id=uuid.uuid4(), response=LLMResult(generations=[[
            ChatGenerationChunk(message=AIMessageChunk("부분"))]]))
        meter.on_llm_error(RuntimeError(), run_id=uuid.uuid4())
        self.assertEqual(meter.totals(), (7, 3, 3, 2))

    def test_legacy_active_guard_survives_month_rollover_without_debit(self):
        user = User.objects.create_user(username="legacy")
        session = ChatSession.objects.create(user=user)
        wallet = UsageWallet.objects.create(user=user, period="2020-01", used_tokens=99999)
        charge = UsageCharge.objects.create(wallet=wallet, period="2020-01", reserved_tokens=20000,
                                           input_tokens=7, output_tokens=3, calls=1)
        dto = usage.balance(user=user)
        self.assertEqual((dto["used_tokens"], dto["remaining_tokens"], dto["active_turn"]), (0, 100000, True))
        with self.assertRaises(usage.WalletBusy):
            usage.reserve(session)
        usage.settle_abandoned(charge)
        charge.refresh_from_db()
        self.assertEqual((charge.charged_tokens, charge.status), (10, UsageCharge.SETTLED_UNKNOWN))
        self.assertTrue(usage.balance(user=user)["can_send"])

    def test_unknown_preserves_known_billing_and_releases_positive_wallet(self):
        session = ChatSession.objects.create(guest=uuid.uuid4())
        charge = usage.reserve(session)
        meter = usage.Meter()
        meter.record((30, 2), "known")
        meter.record(None, "missing")
        usage.settle(charge, meter)
        usage.settle(charge, meter)
        dto = usage.balance(guest=session.guest)
        self.assertEqual((dto["used_tokens"], dto["unknown_calls"], dto["accounting_state"], dto["can_send"]),
                         (32, 1, "unknown", True))

    def test_credit_decimals_are_exact(self):
        session = ChatSession.objects.create(guest=uuid.uuid4())
        charge = usage.reserve(session)
        meter = usage.Meter()
        meter.record((1234, 1))
        usage.settle(charge, meter)
        dto = usage.balance(guest=session.guest)
        self.assertEqual((dto["used_tokens"], dto["remaining_tokens"], dto["remaining_credits"]), (1235, 8765, "8.765"))

    def test_month_boundary_resets_member_only(self):
        with override_settings(USAGE_TIMEZONE="Asia/Seoul"):
            # 2026-09-30 15:30 UTC = 2026-10-01 00:30 KST
            self.assertEqual(usage.period_for(True, datetime(2026, 9, 30, 14, 59, tzinfo=dt_tz.utc)), "2026-09")
            self.assertEqual(usage.period_for(True, datetime(2026, 9, 30, 15, 0, tzinfo=dt_tz.utc)), "2026-10")
            self.assertEqual(usage.period_for(False), "lifetime")
            user = User.objects.create_user(username="m1", password="x-test-only")
            UsageWallet.objects.create(user=user, period="2026-08", used_tokens=100_000)
            UsageWallet.objects.create(guest=(g := uuid.uuid4()), period="lifetime", used_tokens=10_000)
            with patch.object(usage, "_now", return_value=datetime(2026, 9, 30, 15, 0, tzinfo=dt_tz.utc)):
                dto = usage.balance(user=user)
            self.assertEqual((dto["period"], dto["used_tokens"], dto["resets_at"]), ("2026-10", 0, "2026-11-01T00:00:00+09:00"))
            self.assertFalse(usage.balance(guest=g)["can_send"])


    def test_charge_from_previous_month_does_not_hit_new_month(self):
        user = User.objects.create_user(username="m2", password="x-test-only")
        session = ChatSession.objects.create(user=user)
        with patch.object(usage, "_now", return_value=datetime(2026, 9, 30, 14, 0, tzinfo=dt_tz.utc)):
            charge = usage.reserve(session)
        with patch.object(usage, "_now", return_value=datetime(2026, 9, 30, 15, 1, tzinfo=dt_tz.utc)):
            usage.balance(user=user)  # 새 달로 넘어감
            meter = usage.Meter()
            meter.record((500, 500))
            usage.settle(charge, meter)
            self.assertEqual(usage.balance(user=user)["used_tokens"], 0)
        self.assertEqual(_charge(session).charged_tokens, 1000)

    def test_crashed_reservation_stays_locked_until_manual_settle(self):
        session = ChatSession.objects.create(guest=uuid.uuid4())
        charge = usage.reserve(session)
        UsageCharge.objects.filter(pk=charge.pk).update(created_at=datetime(2020, 1, 1, tzinfo=dt_tz.utc))
        dto = usage.balance(guest=session.guest)  # 자동 만료 없음: 아직 쓰는 중일 수 있다. 무료로 풀지도 않는다.
        self.assertEqual((dto["reserved_tokens"], dto["used_tokens"], dto["can_send"]), (0, 0, False))
        usage.settle_abandoned(charge)
        usage.settle_abandoned(charge)  # 멱등
        dto = usage.balance(guest=session.guest)
        self.assertEqual((dto["reserved_tokens"], dto["used_tokens"]), (0, 0))


@override_settings(USAGE_GUEST_TOKENS=10_000, USAGE_MEMBER_MONTHLY_TOKENS=100_000)
class MeteredTurnTest(CheckpointTestCase):
    def setUp(self):
        self.session = ChatSession.objects.create(guest=uuid.uuid4())

    def _send(self, graph, version="v2"):
        with patch_chain(return_value=graph):
            return list(chat_service.send_message(self.session, "질문", version=version))

    def test_nested_calls_aggregate_and_charge_actual(self):
        frames = self._send(UsageGraph([(_model(100, 20), False), (_model(30, 5), True)]))
        self.assertEqual(frames[-1][0], "done")
        charge = _charge(self.session)
        self.assertEqual((charge.status, charge.input_tokens, charge.output_tokens, charge.calls, charge.charged_tokens),
                         ("settled", 130, 25, 2, 155))
        self.assertEqual(usage.balance(guest=self.session.guest)["used_tokens"], 155)

    def test_history_and_get_are_not_charged_again(self):
        self._send(UsageGraph([(_model(100, 20), False)]))
        self._send(UsageGraph([(_model(1, 1), False)]))  # 두 번째 턴에 이전 대화가 들어가도 새 호출분만
        self.assertEqual(usage.balance(guest=self.session.guest)["used_tokens"], 122)
        client = APIClient()
        client.cookies["guest_id"] = str(self.session.guest)
        client.get(f"/api/v2/chat/sessions/{self.session.id}/messages/")
        client.get("/api/v2/chat/usage/")
        self.assertEqual(UsageCharge.objects.count(), 2)

    def test_missing_metadata_preserves_unknown_not_fabricated_charge(self):
        self._send(UsageGraph([(_model(), False)]))
        charge = _charge(self.session)
        self.assertEqual((charge.status, charge.unknown_calls, charge.charged_tokens), (UsageCharge.SETTLED_UNKNOWN, 1, 0))

    def test_graph_error_after_finished_calls_charges_known_tokens(self):
        frames = self._send(UsageGraph([(_model(40, 2), False), (_model(9, 9), False)], fail_after=1))
        self.assertEqual(frames[-1][0], "error")
        self.assertEqual(_charge(self.session).charged_tokens, 42)

    def test_provider_error_mid_call_is_explicitly_unknown(self):
        class Broken(FakeModel):
            def _generate(self, *a, **kw):
                raise RuntimeError("provider exploded")

        class Retrying:  # 실패 뒤 재시도도 허용; 각 run은 미확인으로 보존
            def stream(self, *a, **kw):
                for _ in range(2):
                    try:
                        RunnableLambda(lambda _: Broken(messages=iter([])).invoke("x")).invoke(None)
                    except RuntimeError:
                        pass
                raise RuntimeError("gave up")
                yield

        self.assertEqual(self._send(Retrying())[-1][0], "error")
        charge = _charge(self.session)
        self.assertEqual((charge.calls, charge.unknown_calls, charge.charged_tokens), (2, 2, 0))

    def test_async_stop_mid_stream_is_unknown(self):
        import asyncio
        charge = usage.reserve(self.session)
        meter = usage.Meter()
        model = FakeModel(messages=iter([AIMessage("가 " * 200)]))

        async def run():
            token = usage._meter.set(meter)
            try:
                stream = model.astream("x")
                await anext(stream)
                await stream.aclose()  # 사용자 Stop: usage 청크 전에 닫힘
            finally:
                usage._meter.reset(token)

        asyncio.run(run())
        usage.settle(charge, meter)
        charge = _charge(self.session)
        self.assertEqual((charge.status, charge.unknown_calls, charge.charged_tokens), (UsageCharge.SETTLED_UNKNOWN, 1, 0))

    def test_sync_stop_mid_stream_is_unknown(self):
        charge = usage.reserve(self.session)
        meter = usage.Meter()
        token = usage._meter.set(meter)
        try:
            stream = FakeModel(messages=iter([AIMessage("가 " * 200)])).stream("x")
            next(stream)
            stream.close()
        finally:
            usage._meter.reset(token)
        usage.settle(charge, meter)
        self.assertEqual(_charge(self.session).charged_tokens, charge.reserved_tokens)

    def test_v1_path_is_metered(self):
        frames = self._send(FakeChain(), version="v1")
        self.assertEqual(frames[-1][0], "done")
        self.assertEqual(_charge(self.session).status, "settled")

    def test_admitted_turn_finishes_all_nested_calls_even_over_remainder(self):
        UsageWallet.objects.create(guest=self.session.guest, period="lifetime", used_tokens=9999)
        frames = self._send(UsageGraph([(_model(1000, 500), False), (_model(1, 1), True)]))
        self.assertEqual(frames[-1][0], "done")
        self.assertEqual(_charge(self.session).charged_tokens, 1502)
        dto = usage.balance(guest=self.session.guest)
        self.assertEqual((dto["remaining_tokens"], dto["remaining_credits"], dto["can_send"]), (0, "0.000", False))
        with self.assertRaises(usage.InsufficientCredits):
            chat_service.send_message(self.session, "다음")

    def test_member_small_positive_balance_is_admitted(self):
        user = User.objects.create_user(username="tiny-member")
        self.session = ChatSession.objects.create(user=user)
        UsageWallet.objects.create(user=user, period=usage.period_for(True), used_tokens=99999)
        frames = self._send(UsageGraph([(_model(20000, 1), False)]))
        self.assertEqual(frames[-1][0], "done")
        self.assertEqual(_charge(self.session).charged_tokens, 20001)
        self.assertEqual(usage.balance(user=user)["remaining_tokens"], 0)

    def test_insufficient_balance_does_not_generate_or_store_question(self):
        UsageWallet.objects.create(guest=self.session.guest, period="lifetime", used_tokens=10_000)
        graph = UsageGraph([(_model(1, 1), False)])
        with patch_chain(return_value=graph) as (_v1, v2), self.assertRaises(usage.InsufficientCredits):
            chat_service.send_message(self.session, "질문")
        v2.assert_not_called()
        self.assertEqual(ChatThread(self.session.id).state(), ([], {}))

@override_settings(USAGE_GUEST_TOKENS=10_000, USAGE_MEMBER_MONTHLY_TOKENS=100_000)
class UsageHttpTest(CheckpointTestCase):
    def test_owner_is_derived_from_session_and_spoofing_is_ignored(self):
        victim = uuid.uuid4()
        UsageWallet.objects.create(guest=victim, period="lifetime", used_tokens=0)
        attacker = APIClient()
        attacker.cookies["guest_id"] = str(uuid.uuid4())
        session = ChatSession.objects.create(guest=victim)
        # 남의 세션: 404, 차감 없음. body 의 owner/balance/cost 는 무시된다.
        response = attacker.post(f"/api/v2/chat/sessions/{session.id}/messages/",
                                 {"content": "x", "guest": str(victim), "cost": 0, "balance": 999999}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(UsageCharge.objects.count(), 0)
        self.assertEqual(attacker.get("/api/v2/chat/usage/").json()["used_tokens"], 0)

    def test_member_cannot_use_guest_wallet_and_guest_bad_cookie(self):
        user = User.objects.create_user(username="m3", password="x-test-only")
        client = APIClient()
        client.force_authenticate(user)
        client.cookies["guest_id"] = str(uuid.uuid4())
        dto = client.get("/api/v1/chat/usage/").json()
        self.assertEqual((dto["plan"], dto["limit_tokens"]), ("member", 100_000))
        anon = APIClient()
        anon.cookies["guest_id"] = "not-a-uuid"
        self.assertEqual(anon.get("/api/v2/chat/usage/").json()["plan"], "guest")

    def test_exhausted_post_and_put_return_stable_402(self):
        guest = uuid.uuid4()
        session = ChatSession.objects.create(guest=guest)
        UsageWallet.objects.create(guest=guest, period="lifetime", used_tokens=10_000)
        client = APIClient()
        client.cookies["guest_id"] = str(guest)
        for method, body in (("post", {"content": "x"}), ("put", {"message_id": 1, "content": "x"})):
            response = getattr(client, method)(f"/api/v2/chat/sessions/{session.id}/messages/", body, format="json",
                                               HTTP_ACCEPT="text/event-stream")
            self.assertEqual(response.status_code, 402)
            self.assertEqual(response.json()["code"], "usage_exhausted")

    def test_busy_post_and_regeneration_are_409_for_both_versions_and_owners(self):
        for member in (False, True):
            user = User.objects.create_user(username="busy-member") if member else None
            session = ChatSession.objects.create(user=user) if member else ChatSession.objects.create(guest=uuid.uuid4())
            usage.reserve(session)
            client = APIClient()
            if member:
                client.force_authenticate(user)
            else:
                client.cookies["guest_id"] = str(session.guest)
            for version in ("v1", "v2"):
                for method, body in (("post", {"content": "x"}), ("put", {"message_id": 1, "content": "x"})):
                    response = getattr(client, method)(f"/api/{version}/chat/sessions/{session.id}/messages/", body,
                                                       format="json", HTTP_ACCEPT="text/event-stream")
                    self.assertEqual((response.status_code, response.json()["code"]), (409, usage.BUSY_CODE))

    def test_small_positive_balance_post_and_regeneration_shared_versions(self):
        for version in ("v1", "v2"):
            for member in (False, True):
                user = User.objects.create_user(username=f"small-{version}") if member else None
                session = ChatSession.objects.create(user=user) if member else ChatSession.objects.create(guest=uuid.uuid4())
                owner = {"user": user} if member else {"guest": session.guest}
                wallet = UsageWallet.objects.create(**owner, period=usage.period_for(member),
                                                    used_tokens=(100000 if member else 10000) - 1)
                client = APIClient()
                if member:
                    client.force_authenticate(user)
                else:
                    client.cookies["guest_id"] = str(session.guest)
                with patch_chain(return_value=FakeChain()):
                    response = client.post(f"/api/{version}/chat/sessions/{session.id}/messages/",
                                           {"content": "x"}, format="json", HTTP_ACCEPT="text/event-stream")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(_read_sse_body(b"".join(response.streaming_content).decode())[-1][0], "done")
                    human = client.get(f"/api/{version}/chat/sessions/{session.id}/messages/").json()[0]["id"]
                    response = client.put(f"/api/{version}/chat/sessions/{session.id}/messages/",
                                          {"message_id": human, "content": "regenerate"}, format="json",
                                          HTTP_ACCEPT="text/event-stream")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(_read_sse_body(b"".join(response.streaming_content).decode())[-1][0], "done")
                self.assertEqual(UsageCharge.objects.filter(wallet=wallet).count(), 2)

    def test_sse_success_and_usage_refresh(self):
        guest = uuid.uuid4()
        session = ChatSession.objects.create(guest=guest)
        client = APIClient()
        client.cookies["guest_id"] = str(guest)
        with patch_chain(return_value=UsageGraph([(_model(700, 300), False)])):
            response = client.post(f"/api/v1/chat/sessions/{session.id}/messages/".replace("v1", "v2"),
                                   {"content": "x"}, format="json", HTTP_ACCEPT="text/event-stream")
            frames = _read_sse_body(b"".join(response.streaming_content).decode())
        self.assertEqual(frames[-1][0], "done")
        dto = client.get("/api/v2/chat/usage/").json()
        self.assertEqual((dto["used_tokens"], dto["remaining_credits"]), (1000, "9.000"))


@override_settings(USAGE_GUEST_TOKENS=10_000, USAGE_MEMBER_MONTHLY_TOKENS=100_000)
class DisconnectTest(TransactionTestCase):
    """close() 를 다른 스레드에서 부르므로 commit 된 행이 필요하다."""

    def setUp(self):
        ChatThread.setup()
        self.session = ChatSession.objects.create(guest=uuid.uuid4())

    def test_disconnect_keeps_reservation_until_provider_stops(self):
        gate = threading.Event()
        graph = UsageGraph([(_model(10, 1), False)], gate=gate)
        with patch_chain(return_value=graph):
            events = chat_service.send_message(self.session, "질문")
            self.assertEqual(next(events)[0], "delta")
            closer = threading.Thread(target=events.close)  # 클라이언트 끊김: close 는 provider 가 멈출 때까지 정산을 미룬다
            closer.start()
            closer.join(0.5)
            self.assertTrue(closer.is_alive())
            self.assertEqual(_charge(self.session).status, "reserved")
            gate.set()
            closer.join(5)
        self.assertEqual(_charge(self.session).charged_tokens, 11)

    @override_settings(USAGE_SETTLE_WAIT_SECONDS=0.2)
    def test_worker_settles_after_request_gives_up(self):
        gate = threading.Event()
        graph = UsageGraph([(_model(10, 1), False)], gate=gate)
        with patch_chain(return_value=graph):
            events = chat_service.send_message(self.session, "질문")
            self.assertEqual(next(events)[0], "delta")
            events.close()  # 요청 스레드는 0.2s 기다리고 포기
            self.assertEqual(_charge(self.session).status, "reserved")
            gate.set()
            for _ in range(50):
                if _charge(self.session).status == "settled":
                    break
                threading.Event().wait(0.1)
        self.assertEqual(_charge(self.session).charged_tokens, 11)



class GuestIdentTest(TestCase):
    def _ident(self):
        from rest_framework import settings as drf_settings
        from rest_framework.test import APIRequestFactory
        from rest_framework.request import Request
        from llm.views.message import GuestChatThrottle
        drf_settings.api_settings.reload()
        request = Request(APIRequestFactory().post("/", REMOTE_ADDR="10.0.0.9", HTTP_X_FORWARDED_FOR="6.6.6.6, 1.2.3.4"))
        return GuestChatThrottle().get_ident(request)

    def test_xff_only_trusted_when_proxy_headers_configured(self):
        from django.conf import settings
        for trusted, expected in ((False, "10.0.0.9"), (True, "1.2.3.4")):
            rf = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1 if trusted else 0}
            with override_settings(REST_FRAMEWORK=rf):
                self.assertEqual(self._ident(), expected)


@override_settings(USAGE_GUEST_TOKENS=10_000, USAGE_MEMBER_MONTHLY_TOKENS=100_000)
class ConcurrentReservationTest(TransactionTestCase):
    @override_settings(USAGE_TIMEZONE="Asia/Seoul")
    def test_delayed_month_boundary_reservation_preserves_new_month_usage(self):
        from django.db import transaction
        user = User.objects.create_user(username="month-lock-order")
        session = ChatSession.objects.create(user=user)
        wallet = UsageWallet.objects.create(user=user, period="2026-09", used_tokens=500)
        before = datetime(2026, 9, 30, 14, 59, 59, tzinfo=dt_tz.utc)
        after = datetime(2026, 9, 30, 15, 0, 1, tzinfo=dt_tz.utc)
        boundary, reached_lock = threading.Event(), threading.Event()
        manager = UsageWallet.objects
        select = manager.select_for_update
        results, errors = [], []

        def delayed_select(*args, **kwargs):
            if threading.current_thread().name == "old-month-request":
                reached_lock.set()
            return select(*args, **kwargs)

        def reserve():
            try:
                results.append(usage.reserve(session))
            except BaseException as error:
                errors.append(error)
            finally:
                connection.close()

        worker = threading.Thread(target=reserve, name="old-month-request", daemon=True)
        with patch.object(usage, "_now", side_effect=lambda: after if boundary.is_set() else before), \
                patch.object(manager, "select_for_update", side_effect=delayed_select):
            try:
                with transaction.atomic():
                    select().get(pk=wallet.pk)  # 실제 행 잠금 뒤 오래된 요청이 대기하도록 순서를 고정한다
                    worker.start()
                    self.assertTrue(reached_lock.wait(5))
                    boundary.set()
                    settled = usage.reserve(session)
                    meter = usage.Meter()
                    meter.record((1000, 234))
                    usage.settle(settled, meter)
                    active = usage.reserve(session)
                    wallet.refresh_from_db()
                    self.assertEqual((wallet.period, wallet.used_tokens), ("2026-10", 1234))
            finally:
                if worker.ident is not None:
                    worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], usage.WalletBusy)
            self.assertEqual(len(results), 0)
            wallet.refresh_from_db()
            self.assertEqual((wallet.period, wallet.used_tokens), ("2026-10", 1234))
            active.refresh_from_db()
            self.assertEqual((active.status, active.period, active.reserved_tokens), (UsageCharge.RESERVED, "2026-10", 0))
            dto = usage.balance(user=user)
            self.assertEqual((dto["used_tokens"], dto["reserved_tokens"], dto["remaining_tokens"]), (1234, 0, 98_766))
            usage.settle(active, usage.Meter())
            later = usage.reserve(session)
            meter = usage.Meter()
            meter.record((10, 1))
            usage.settle(later, meter)
            self.assertEqual(usage.balance(user=user)["remaining_tokens"], 98_755)

    def test_parallel_reservations_never_overdraft(self):
        guest = uuid.uuid4()
        sessions = [ChatSession.objects.create(guest=guest) for _ in range(8)]
        results, barrier = [], threading.Barrier(8)

        def go(session):
            barrier.wait()
            try:
                usage.reserve(session)
                results.append(1)
            except usage.WalletBusy:
                results.append(0)
            finally:
                connection.close()

        with override_settings(USAGE_TURN_RESERVE_TOKENS=3000):
            threads = [threading.Thread(target=go, args=(s,)) for s in sessions]
            [t.start() for t in threads]
            [t.join() for t in threads]
        self.assertLessEqual(sum(results), 10_000)
        self.assertEqual(UsageWallet.objects.filter(guest=guest).count(), 1)
        self.assertEqual(sorted(results, reverse=True)[:4], [1, 0, 0, 0])


class JevUsageTest(TestCase):
    def test_invoked_failure_and_cancellation_charge_unknown_once(self):
        from llm.v2.middleware import jev_guidelines as jev
        for error in (TimeoutError("test-only SDK timeout"), GeneratorExit("test-only cancellation")):
            with self.subTest(error=type(error).__name__):
                session = ChatSession.objects.create(guest=uuid.uuid4())
                charge = usage.reserve(session)
                meter = usage.Meter()

                def produce():
                    jev.classify("질문")
                    yield

                with patch.object(jev, "_client") as client:
                    client.return_value.invoke.side_effect = error
                    with self.assertRaises(type(error)) as raised:
                        list(usage.metered(produce, meter, charge))
                    self.assertIs(raised.exception, error)
                    client.return_value.invoke.assert_called_once()
                usage.finish(charge, meter)
                charge.refresh_from_db()
                self.assertEqual(meter.totals(), (0, 0, 1, 1))
                self.assertEqual((charge.status, charge.calls, charge.unknown_calls, charge.charged_tokens),
                                 (UsageCharge.SETTLED_UNKNOWN, 1, 1, 0))

    def test_rejection_and_preparation_failures_do_not_invoke_or_charge(self):
        from llm.v2.middleware import jev_guidelines as jev
        for stage in ("client", "payload"):
            with self.subTest(stage=stage):
                charge = usage.reserve(ChatSession.objects.create(guest=uuid.uuid4()))
                meter = usage.Meter()
                token = usage._meter.set(meter)
                error = KeyError("test-only preparation")
                try:
                    with patch.object(jev, "_client") as client, patch.object(jev, "state_text") as payload:
                        if stage == "client":
                            client.side_effect = error
                        elif stage == "payload":
                            payload.side_effect = error
                        with self.assertRaises(KeyError):
                            jev.classify("질문")
                        client.return_value.invoke.assert_not_called()
                finally:
                    usage._meter.reset(token)
                usage.settle(charge, meter)
                charge.refresh_from_db()
                self.assertEqual(meter.totals(), (0, 0, 0, 0))
                self.assertEqual((charge.calls, charge.unknown_calls, charge.charged_tokens), (0, 0, 0))

    def _result(self, i, o):
        from types import SimpleNamespace as N
        return N(usage=N(input_tokens=i, output_tokens=o), choices={"guard": N(choice="PASS"), "course_request": N(choice="NONE")},
                 nouls={name: N(noul=0.0) for name in __import__("llm.v2.middleware.jev_guidelines", fromlist=["x"]).CAPABILITIES})

    def test_classifier_usage_is_metered_and_missing_is_unknown(self):
        from llm.v2.middleware import jev_guidelines as jev
        meter = usage.Meter()
        token = usage._meter.set(meter)
        try:
            for i, o in ((11, 1), (None, 2)):
                with patch.object(jev, "_client") as client:
                    client.return_value.invoke.return_value = self._result(i, o)
                    jev.classify("질문")
        finally:
            usage._meter.reset(token)
        self.assertEqual(meter.totals(), (11, 3, 2, 1))


class MeterInflightTests(TestCase):
    def test_unfinished_run_is_unknown_not_exact_zero(self):
        meter = usage.Meter()
        meter.check(run_id="unfinished")
        self.assertEqual(meter.totals(), (0, 0, 1, 1))
        meter.record((10, 2), "unfinished")
        meter.record((10, 2), "unfinished")
        self.assertEqual(meter.totals(), (10, 2, 1, 0))

    def test_unknown_call_does_not_stop_admitted_answer(self):
        meter = usage.Meter()
        meter.on_llm_error(RuntimeError(), run_id="a")
        meter.check(run_id="b")
        meter.record((10, 2), "b")
        self.assertEqual(meter.totals(), (10, 2, 2, 1))
