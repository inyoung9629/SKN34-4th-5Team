"""DeepEval 4.2.8 offline contracts over real compiled graphs/reader boundaries.
Run with llm.v2.tests.run_attachment_offline; no judge/provider/upload.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import uuid

os.environ.update(DEEPEVAL_TELEMETRY_OPT_OUT="1", DEEPEVAL_DISABLE_DOTENV="1",
                  DEEPEVAL_DISABLE_LEGACY_KEYFILE="1", PYTHON_DOTENV_DISABLED="1",
                  LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false",
                  DEEPEVAL_RESULTS_FOLDER=str(Path.home() / ".hermes/cache/scratch/attachment-shared-implementation/results"),
                  DEEPEVAL_CACHE_FOLDER=str(Path.home() / ".hermes/cache/scratch/attachment-shared-implementation/cache"))
from deepeval import assert_test
from deepeval.dataset import EvaluationDataset
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase
from django.test import TransactionTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langsmith import tracing_context

from llm.models import ChatAttachment, ChatSession
from llm.v2.agent import browser_research as browser, chain, sub_agents, web_sub_agent
from llm.v2.middleware.attachment_context import ATTACHMENT_EVIDENCE_RULES
from llm.v2.middleware.dynamic_tools import DynamicToolMiddleware
from llm.v2.tests.test_chain import ScriptedModel, call, fake_tools
from llm.v2.tests import test_web_mcp_direct as mcp_tests
from llm.v2.tests.test_web_specialist import observed

CASES = json.loads(Path(__file__).with_name("attachment_shared.json").read_text())


class SharedAttachmentMetric(BaseMetric):
    """Only transport/contract assertions; never scores scripted text as semantic quality."""
    threshold = 1
    async_mode = False
    strict_mode = True
    evaluation_model = "offline-runtime-contract"

    def measure(self, test_case, *args, **kwargs):
        trace = json.loads(test_case.actual_output)
        expected = test_case.additional_metadata
        failures = []
        if trace["reads"] != expected["expected_reads"]:
            failures.append("reader count")
        if expected.get("foreign"):
            if trace.get("error") != "missing conversation attachment":
                failures.append("ownership")
        else:
            if trace["status"] != expected["status"]:
                failures.append("retained status")
            if trace["reader_status"] != ("read" if expected["status"] == "ok" else "paste_body_needed"):
                failures.append("conservative evidence")
            if trace["direct_status"] != ("success" if expected["status"] == "ok" else "error"):
                failures.append("direct MCP status")
            if not trace["newest"]:
                failures.append("newest attachment identity")
            if expected.get("expired") and not trace["expired_absent"]:
                failures.append("recent window")
            for entry in trace["inputs"]:
                if ATTACHMENT_EVIDENCE_RULES not in entry["system"] or "공식 홈페이지에서만" not in entry["text"]:
                    failures.append("official-only main/delegate input")
                if expected["status"] in {"ok", "partial"} and "OBSERVED_BODY" not in entry["text"]:
                    failures.append("original input")
                if expected["status"] == "blocked" and '"available_body": false' not in entry["text"]:
                    failures.append("failure input")
            if not trace["inputs"] or not trace["delegated"]:
                failures.append("missing real model inputs")
            if trace["public_body"]:
                failures.append("body leaked to public history")
            if expected["status"] != "ok" and trace["complete"]:
                failures.append("incomplete claimed as complete")
        self.score = 0 if failures else 1
        self.success = self.score == 1
        self.reason = ", ".join(failures) or "actual runtime inputs/reader counts satisfy contract"
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case)

    def is_successful(self):
        return self.success

    @property
    def __name__(self):
        return "Shared attachment runtime contract"


class SharedAttachmentContracts(TransactionTestCase):
    def trace(self, case):
        session = ChatSession.objects.create(guest=uuid.uuid4())
        url = "https://www.diningcode.com/profile.php?rid=attached"
        row = ChatAttachment.objects.create(session=session, kind="url", name="unofficial reference", source_url=url)
        message = HumanMessage("공식 홈페이지에서만 잠실 정보를 확인; 외부 조회 금지", id="current",
                               additional_kwargs={"attachment_ids": [str(row.id)]})
        history = []
        if case.get("old_failure") or case.get("expired"):
            old = ChatAttachment.objects.create(session=session, kind="url", name="old", source_url=url,
                                                url_read_result={"status": "blocked"})
            history = [HumanMessage("old", id="old", additional_kwargs={"attachment_ids": [str(old.id)]})]
            if case.get("expired"):
                history += [HumanMessage(f"intermediate {i}", id=f"mid{i}") for i in range(3)]
        calls = []
        direct = mcp_tests.DirectMCPTests()
        direct.setUp()
        discovered = direct.discovered()
        # Build real specialists with the actual MCP adapter; remote boundary is a counted mock.
        script = [call("ask_web_research", {"task": "확인"}, "delegate"),
                  call("jev_read_body", {"url": url}, "specialist-read"),
                  AIMessage("SCRIPTED_SPECIALIST_NOT_SEMANTIC"), AIMessage("SCRIPTED_FINAL_NOT_SEMANTIC")]
        model = ScriptedModel(script=script, calls=calls)
        status = case.get("status", "ok")
        response = observed("OBSERVED_BODY", status=status, source_url=url, requested_url=url, final_url=url)
        if status == "blocked":
            response = {"status": status}
        data = {"messages": [*history, message], "attachment_session_id": str(uuid.uuid4() if case.get("foreign") else session.id)}
        with tracing_context(enabled=False), patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "true"}), patch.object(
                browser, "discover_tools", AsyncMock(return_value=discovered)), patch.object(
                web_sub_agent, "research_model", return_value=model), patch(
                "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                browser, "browse", AsyncMock(return_value=response)) as fetch:
            graph = chain.build_graph(model, fake_tools([]))
            try:
                out = graph.invoke(data)
            except ValueError as error:
                return {"reads": fetch.call_count, "error": str(error)}
            script.extend([call("ask_web_research", {"task": "same attachment followup"}, "delegate2"),
                           call("jev_read_body", {"url": url}, "specialist-read2"),
                           AIMessage("SCRIPTED_SPECIALIST_FOLLOWUP"), AIMessage("SCRIPTED_FINAL_FOLLOWUP")])
            out = graph.invoke(data)
            retained = out["attachment_read_results"]
            token = browser.attachment_read_results.set(retained)
            try:
                from travel.public_page_reader import PublicReader
                network = Mock(side_effect=AssertionError("retained attachment must not fetch"))
                reader = PublicReader(client=Mock(), dns_check=network)
                with patch.object(reader, "get", network):
                    page = browser.read_evidence(reader, url, [])
                    self.assertEqual(reader.read(url, [])["status"], page["status"])
                    self.assertEqual(reader.read("http://127.0.0.1/", [])["status"], "unsupported_host_or_scheme")
                network.assert_not_called()
                shared = browser.web_body(url)
                self.assertEqual(shared["status"], status)
            finally:
                browser.attachment_read_results.reset(token)
        self.assertEqual(direct.executed, [])
        tool = next(m for m in out["messages"] if isinstance(m, ToolMessage) and m.name == "ask_web_research")
        read = next(m for m in tool.artifact if isinstance(m, ToolMessage) and m.name == "jev_read_body")
        return {"reads": fetch.call_count, "status": retained[url]["status"], "reader_status": page["status"],
                "direct_status": read.status, "newest": retained[url]["attachment_id"] == str(row.id),
                "expired_absent": str(old.id) not in json.dumps(retained) if case.get("expired") else True,
                "complete": not out["attachment_source_incomplete"],
                "delegated": len(tool.artifact) > 1,
                "public_body": "OBSERVED_BODY" in json.dumps([m.model_dump() for m in out["messages"]], default=str),
                "inputs": [{"system": c["system"], "text": "\n".join(m.text for m in c["messages"])} for c in calls]}

    def test_deepeval_runtime_dataset_and_known_bad_mutations(self):
        dataset = EvaluationDataset()
        for case in CASES:
            with self.subTest(case=case["id"]):
                self.assertEqual(case["review_status"], "implementation-reviewed")
                trace = self.trace(case)
                output = Path.home() / ".hermes/cache/scratch/attachment-shared-implementation"
                output.mkdir(parents=True, exist_ok=True)
                (output / (case["id"] + "-trace.json")).write_text(json.dumps(trace, ensure_ascii=False, indent=2))
                test = LLMTestCase(input=case["id"], actual_output=json.dumps(trace, ensure_ascii=False), additional_metadata=case)
                dataset.add_test_case(test)
                with tracing_context(enabled=False):
                    assert_test(test, [SharedAttachmentMetric()])
                # Mutate observations from an actual execution, not invented success events.
                bad = deepcopy(trace)
                bad["reads"] += 1
                negative = LLMTestCase(input=case["id"], actual_output=json.dumps(bad), additional_metadata=case)
                self.assertEqual(SharedAttachmentMetric().measure(negative), 0)
                if not case.get("foreign"):
                    for mutation in ("inputs", "newest", "direct_status", "reader_status", "public_body"):
                        bad = deepcopy(trace)
                        bad[mutation] = {"inputs": [], "newest": False, "direct_status": "wrong",
                                         "reader_status": "wrong", "public_body": True}[mutation]
                        self.assertEqual(SharedAttachmentMetric().measure(LLMTestCase(
                            input=case["id"], actual_output=json.dumps(bad), additional_metadata=case)), 0)
        self.assertEqual(len(dataset.test_cases), len(CASES))

    def test_course_success_partial_original_absent_at_persistence_boundary(self):
        from contextlib import contextmanager
        from langchain_core.tools import tool
        from llm.service import chat_v2
        from llm.service.chat_thread import ChatThread
        from llm.serializer.message import project_history

        for status in ("ok", "partial"):
            with self.subTest(status=status):
                session = ChatSession.objects.create(guest=uuid.uuid4())
                url = "https://www.diningcode.com/profile.php?rid=attached"
                row = ChatAttachment.objects.create(session=session, kind="url", name="reference", source_url=url)
                human = HumanMessage("공식 홈페이지에서만 잠실 확인; 외부 조회 금지", id="question",
                                     additional_kwargs={"attachment_ids": [str(row.pk)]})
                received = []
                @tool("plan_course", description="Offline course execution boundary")
                def plan_course(request: str = "") -> str:
                    received.append(request)
                    self.assertIn("COURSE_ORIGINAL_ONLY", request)
                    self.assertEqual(browser.shared_body(url)["status"], status)
                    return "FIXED_COURSE_RESULT"
                tools = fake_tools([])
                tools["plan_course"] = plan_course
                calls = []
                graph = chain.build_graph(ScriptedModel(script=[call("ask_course", {"task": "확인"}, "delegate"),
                                                               AIMessage("FINAL")], calls=calls), tools)
                run = {"answer": "", "messages": []}
                response = observed("COURSE_ORIGINAL_ONLY", status=status, source_url=url, requested_url=url, final_url=url)
                with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                        browser, "browse", AsyncMock(return_value=response)) as fetch, patch.object(chain, "get_graph", return_value=graph):
                    events = list(chat_v2._frames({"messages": [human], "attachment_session_id": str(session.pk)}, run))
                self.assertEqual(fetch.await_count, 1)
                self.assertEqual(len(received), 1)
                self.assertIn("COURSE_ORIGINAL_ONLY", "\n".join(m.text for m in calls[0]["messages"]))
                delegate = next(m for m in run["messages"] if isinstance(m, ToolMessage) and m.name == "ask_course")
                planned = next(m for m in delegate.artifact if isinstance(m, AIMessage))
                result = next(m for m in delegate.artifact if isinstance(m, ToolMessage))
                self.assertEqual(planned.tool_calls[0]["id"], result.tool_call_id)
                self.assertEqual((planned.tool_calls[0]["name"], result.status, delegate.content),
                                 ("plan_course", "success", "FIXED_COURSE_RESULT"))
                self.assertIn("공식 홈페이지에서만", planned.tool_calls[0]["args"]["request"])
                final = AIMessage(run["answer"], id="final")
                turns = {human.id: {"status": "completed", "answer_id": final.id}}
                checkpoint = Mock()
                checkpoint.get_state.return_value = NS(values={"revision": 0, "messages": [human], "wire": {}})
                checkpoint.update_state.return_value = {"configurable": {"checkpoint_id": "offline"}}
                @contextmanager
                def fake_checkpoint():
                    yield checkpoint
                with patch.object(ChatThread, "_graph", staticmethod(fake_checkpoint)):
                    self.assertTrue(ChatThread(session.pk).update([*run["messages"], final], turns))
                written = checkpoint.update_state.call_args.args[1]
                boundaries = [events, [m.model_dump() for m in run["messages"]],
                              project_history([human, *run["messages"], final], turns, detail=True),
                              [m.model_dump() for m in written["messages"]]]
                for boundary in boundaries:
                    serialized = json.dumps(boundary, default=str)
                    self.assertNotIn("COURSE_ORIGINAL_ONLY", serialized)
                    self.assertNotIn("<attachment_sources_", serialized)
                self.assertIsNone(browser.attachment_read_results.get())

    def test_context_sync_async_parallel_nested_exception_cleanup(self):
        middleware = DynamicToolMiddleware(["read"])
        def request(body):
            return NS(state={"attachment_read_results": {"https://example.com/": {"status": "ok", "body": body}}},
                      tool_call={"name": "read", "id": body, "args": {}})
        def work(body):
            def handler(req):
                self.assertEqual(browser.shared_body("https://example.com/")["body"], body)
                inner = middleware.wrap_tool_call(request("nested"), lambda _: browser.shared_body("https://example.com/")["body"])
                self.assertEqual(inner, "nested")
                self.assertEqual(browser.shared_body("https://example.com/")["body"], body)
                return body
            result = middleware.wrap_tool_call(request(body), handler)
            self.assertIsNone(browser.attachment_read_results.get())
            return result
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(list(pool.map(work, ["A", "B"])), ["A", "B"])
        async def run():
            async def handler(req):
                await asyncio.sleep(.001)
                return browser.shared_body("https://example.com/")["body"]
            return await asyncio.gather(*(middleware.awrap_tool_call(request(body), handler) for body in ("C", "D")))
        self.assertEqual(asyncio.run(run()), ["C", "D"])
        with self.assertRaisesRegex(RuntimeError, "exception"):
            middleware.wrap_tool_call(request("failed"), lambda _: (_ for _ in ()).throw(RuntimeError("exception")))
        self.assertIsNone(browser.attachment_read_results.get())
        self.assertTrue(browser.allow_new_source_search.get())
        async def failed_async():
            async def handler(req):
                self.assertEqual(browser.shared_body("https://example.com/")["body"], "async-failed")
                raise RuntimeError("async exception")
            return await middleware.awrap_tool_call(request("async-failed"), handler)
        with self.assertRaisesRegex(RuntimeError, "async exception"):
            asyncio.run(failed_async())
        self.assertIsNone(browser.attachment_read_results.get())

    def test_delegate_exception_does_not_return_original_artifact(self):
        from langchain.tools import ToolRuntime
        class FailingAgent:
            def stream(self, data, **kwargs):
                yield {"model": {"messages": [call("jev_read_body", {"url": "https://example.com/", "nested": ["PRIVATE_ORIGINAL"]}, "r")]}}
                yield {"tools": {"messages": [ToolMessage("PRIVATE_ORIGINAL", name="jev_read_body", tool_call_id="r")]}}
                raise RuntimeError("specialist failed")
        runtime = NS(state={"messages": [HumanMessage("official only")], "attachment_read_results": {
                     "https://example.com/": {"status": "ok", "body": "PRIVATE_ORIGINAL"}}},
                     config={}, tool_call_id="delegate")
        delegate = sub_agents._delegate("ask_test", "offline exception check", FailingAgent())
        result = delegate.func(task="check", runtime=runtime)
        self.assertEqual(result.status, "error")
        self.assertNotIn("PRIVATE_ORIGINAL", json.dumps(result.model_dump(), default=str))
