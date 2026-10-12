from contextvars import ContextVar
from datetime import datetime
from threading import Event, get_ident
from unittest.mock import patch

from django.test import SimpleTestCase

from llm.service import chat_runs
from llm.service.chat_runs import Stopped
from llm.v1 import progress
from llm.tests.test_course_creation import QUESTION, creation_result
from llm.tests.test_course_editing import GAME, PLACES
from llm.v1.rag.course import agent, editing, parallel, preparation
from llm.v1.rag.course import venue_policy, place_quality


class PreparationTests(SimpleTestCase):
    def test_raw_preparation_is_reused_only_for_the_exact_search_and_still_requires_quality(self):
        from unittest.mock import Mock
        args = {"method": "category", "category": "CE7", "latitude": 35.166, "longitude": 126.902,
                "radius": 800, "sort": "distance", "limit": 15}
        row = {"id": "999991", "place_name": "미확인카페", "x": "126.902", "y": "35.166",
               "road_address_name": "광주 북구 중흥로 1", "category_name": "음식점 > 카페"}
        snapshot = {venue_policy.discovery_key(args, "CAFE"): {"places": [row]}}
        invoke = Mock(return_value={"places": []})
        with place_quality.request_scope("GWANGJU"), venue_policy.request_policy("", "GWANGJU"), \
                venue_policy.prepared_discovery(snapshot), patch.object(place_quality, "search_candidates", return_value=[]), \
                patch.object(place_quality, "verify_candidates", return_value=[]) as verify:
            self.assertEqual(venue_policy.search_candidates(invoke, args, "CE7"), [])
            invoke.assert_not_called()
            self.assertEqual(verify.call_args.args[0][0]["id"], row["id"])
            for changes in ({"longitude": 126.901}, {"radius": 1500}, {"query": "메가커피"}):
                venue_policy.discover_candidates(invoke, {**args, **changes}, "CAFE")
            self.assertEqual(invoke.call_count, 3)
        self.assertEqual(venue_policy._DISCOVERY.get(), {})

    def test_internal_cafe_never_consumes_prepared_external_candidates(self):
        from unittest.mock import Mock
        from baseball.stadium_locations import reviewed_venue
        center = reviewed_venue("GWANGJU")
        args = {"latitude": center["lat"], "longitude": center["lng"], "radius": 2500}
        snapshot = {venue_policy.discovery_key(args, "CAFE"): {"places": [{"id": "outside"}]}}
        invoke = Mock()
        with venue_policy.request_policy("구장 안 카페", "GWANGJU", [{"category": "CAFE", "expression": "구장 안 카페"}]), \
                venue_policy.prepared_discovery(snapshot):
            rows = venue_policy.discover_candidates(invoke, args, "CAFE")
        self.assertTrue(rows)
        self.assertTrue(all(p.get("source") == "MYSEATCHECK" for p in rows))
        invoke.assert_not_called()

    def test_origin_is_forwarded_to_preparation_but_invalid_snapshot_is_not_activated(self):
        origin = {"lat": 35.166, "lng": 126.902}
        snapshot = {"question": "다른 질문", "code": "GWANGJU", "day": "1900-01-01",
                    "discovery": {"wrong": "data"}, "elapsed_ms": 1}
        def generate(*args, **kwargs):
            self.assertEqual(venue_policy._DISCOVERY.get(), {})
            return {"answer": "ok", "places": []}
        with patch.object(preparation, "read", return_value=snapshot) as read, \
                patch.object(editing, "interpret", return_value={"operation": "new", **creation_result()}), \
                patch.object(agent, "_answer", side_effect=generate):
            agent.answer(QUESTION, hint_stadium="GWANGJU", origin=origin, course_request="NEW")
        read.assert_called_once_with(QUESTION, "GWANGJU", origin=origin)

    def test_interpreter_and_read_overlap_with_context_and_main_thread_mutations(self):
        local = ContextVar("preparation_test", default="missing")
        local.set("request")
        reader_started, interpreted = Event(), Event()
        main = get_ident()
        def read():
            self.assertNotEqual(get_ident(), main)
            self.assertEqual(local.get(), "request")
            reader_started.set()
            self.assertTrue(interpreted.wait(2))
            return "schedule"
        def interpret():
            self.assertEqual(get_ident(), main)
            self.assertTrue(reader_started.wait(2))
            local.set("interpreted")
            interpreted.set()
            return "plan"
        with patch.object(parallel.connections, "close_all") as close:
            plan, snapshot, _ = parallel.alongside(read, interpret)
        self.assertEqual((plan, snapshot, local.get()), ("plan", "schedule", "interpreted"))
        close.assert_called_once()

    def test_action_errors_stop_reader_at_next_boundary_and_join_cleanup(self):
        for error in (Stopped, progress.ProgressCancelled, RuntimeError):
            entered, release, cleaned = Event(), Event(), Event()
            calls = []
            def query(*args):
                calls.append(args[1])
                entered.set()
                self.assertTrue(release.wait(2))
                for _ in range(200):
                    if chat_runs._cancelled.get()():
                        break
                    Event().wait(.01)
                self.assertTrue(chat_runs._cancelled.get()())
                return {"item": {"id": 1}}
            def interpret():
                self.assertTrue(entered.wait(2))
                release.set()
                raise error("interpret failed")
            with self.subTest(error=error), \
                    patch.object(agent, "invoke_domain_tool", side_effect=query), \
                    patch.object(parallel.connections, "close_all", side_effect=cleaned.set):
                with self.assertRaises(error):
                    parallel.alongside(lambda: preparation.read(QUESTION, "JAMSIL"), interpret)
            self.assertEqual(calls, ["get_stadium"])
            self.assertTrue(cleaned.is_set())

    def test_waiting_on_http_observes_chat_and_progress_cancel_before_next_page(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        for source in ("chat", "progress", "error"):
            entered, release, cancelled, cleaned = Event(), Event(), Event(), Event()
            collector = SimpleNamespace(cancelled=False)
            response = Mock()
            def body():
                entered.set()
                self.assertTrue(release.wait(2))
                if source == "chat":
                    cancelled.set()
                if source == "progress":
                    collector.cancelled = True
                if source == "error":
                    for _ in range(200):
                        if chat_runs._cancelled.get()():
                            break
                        Event().wait(.01)
                    self.assertTrue(chat_runs._cancelled.get()())
                return b'{"documents": [], "meta": {"is_end": false}}'
            response.read.side_effect = body
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            def interpret():
                self.assertTrue(entered.wait(2))
                release.set()
                if source == "error":
                    raise RuntimeError("interpret failed")
                return "plan"
            token = chat_runs._cancelled.set(cancelled.is_set)
            try:
                with self.subTest(source=source), patch.object(progress, "current", return_value=collector), \
                        patch.object(parallel, "current", return_value=collector), \
                        patch.object(agent.kakao, "_cache", return_value=None), \
                        patch.object(agent.kakao, "enabled", return_value=True), \
                        patch.object(agent.kakao, "resolve_stadium", return_value={"lat": 37.5, "lng": 127, "address": ""}), \
                        patch.object(agent.kakao.urllib.request, "urlopen", return_value=response) as http, \
                        patch.object(parallel.connections, "close_all", side_effect=cleaned.set):
                    with self.assertRaises(RuntimeError if source == "error" else (Stopped, progress.ProgressCancelled)):
                        parallel.alongside(lambda: agent.kakao.nearby("JAMSIL", "indoor"), interpret)
                    http.assert_called_once()
                    self.assertEqual(http.call_args.kwargs["timeout"], 6)
                    self.assertTrue(cleaned.is_set())
            finally:
                chat_runs._cancelled.reset(token)

    def test_new_request_passes_prepared_data_once_and_edit_does_not_prepare(self):
        snapshot = {"elapsed_ms": 12}
        with patch.object(preparation, "read", return_value=snapshot) as read, \
                patch.object(editing, "interpret", return_value={"operation": "new", **creation_result()}), \
                patch.object(agent, "_answer", return_value={"answer": "ok", "places": []}) as generate:
            result = agent.answer(QUESTION, hint_stadium="JAMSIL", course_request="NEW")
        read.assert_called_once_with(QUESTION, "JAMSIL")
        self.assertIs(generate.call_args.kwargs["prepared"], snapshot)
        self.assertEqual(result["timing"]["preparation_ms"], 12)
        with patch.object(preparation, "read") as read, \
                patch.object(editing, "interpret", return_value={"operation": "clarify"}), \
                patch.object(editing, "answer", return_value={"answer": "clarify", "places": []}):
            agent.answer("카페만 바꿔줘", hint_stadium="JAMSIL", course_memory={}, course_request="EDIT")
        read.assert_not_called()

    def test_reader_only_fetches_requested_raw_kinds_and_keeps_empty_results(self):
        with patch.object(agent, "load_schedule", return_value=({}, 1)), \
                patch.object(agent, "find_game", return_value=(GAME, False, [])), \
                patch.object(agent, "stadium_anchor", return_value=PLACES[2]), \
                patch.object(agent.kakao, "nearby", return_value=[]) as nearby, \
                patch.object(agent.lodging, "verify") as verify:
            result = preparation.read("경기 관람 후 숙박만", "JAMSIL")
        nearby.assert_called_once_with("JAMSIL", "stay")
        verify.assert_not_called()
        self.assertEqual(result["extras"], {"stay": []})

    def test_failed_schedule_does_not_speculate_on_lodging_and_cancellation_propagates(self):
        with patch.object(agent, "load_schedule", return_value=({"lookup_failed": True}, 1)), \
                patch.object(agent.kakao, "nearby") as nearby:
            snapshot = preparation.read(QUESTION, "JAMSIL")
        self.assertTrue(snapshot["schedule"]["lookup_failed"])
        nearby.assert_not_called()
        with patch.object(preparation, "check_cancelled", side_effect=Stopped):
            with self.assertRaises(Stopped):
                preparation.read(QUESTION, "JAMSIL")
        with patch.object(preparation, "read", side_effect=Stopped), \
                patch.object(editing, "interpret", return_value={"operation": "new"}):
            with self.assertRaises(Stopped):
                agent.answer(QUESTION, hint_stadium="JAMSIL", course_request="NEW")

    def test_snapshot_cannot_cross_question_stadium_or_date_and_failed_reads_can_fall_back(self):
        snapshot = {"question": QUESTION, "code": "JAMSIL", "day": "2026-10-11"}
        self.assertTrue(preparation.matches(snapshot, QUESTION, "JAMSIL", "2026-10-11"))
        for q, code, day in (("다른 질문", "JAMSIL", "2026-10-11"), (QUESTION, "SAJIK", "2026-10-11"), (QUESTION, "JAMSIL", "2026-10-12")):
            self.assertFalse(preparation.matches(snapshot, q, code, day))
        with patch.object(agent, "load_schedule", side_effect=TimeoutError):
            self.assertNotIn("schedule", preparation.read(QUESTION, "JAMSIL"))
