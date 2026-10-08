"""Serialize the production constructors without importing agents or calling OpenAI."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from langchain_openai import ChatOpenAI


class ResponsesPayloadTest(TestCase):
    def test_native_web_search_serialized_payload_and_conditional_scope(self):
        from langchain_core.messages import HumanMessage, SystemMessage
        from llm.v2.middleware.dynamic_tools import DynamicToolMiddleware
        from langchain.agents.middleware import ModelRequest
        model = ChatOpenAI(model="gpt-6-luna", api_key="offline-test", use_responses_api=True, reasoning_effort="medium")
        middleware = DynamicToolMiddleware([], {})
        def capture(request):
            bound = request.model.bind_tools(request.tools, tool_choice=request.tool_choice)
            return model._get_request_payload(request.messages, **bound.kwargs)
        for text, allowed, expected in [("잠실 https://example.com 요약", True, True),
                                        ("잠실 안내", True, False),
                                        ("코딩 https://example.com 요약", False, False),
                                        ("잠실 홈페이지 링크 https://example.com", True, False)]:
            request = ModelRequest(model=model, messages=[HumanMessage(text)], system_message=SystemMessage("rules"),
                                   tools=[], state={"decision": {"allowed": allowed, "capabilities": []}, "run_model_call_count": 0})
            payload = middleware.wrap_model_call(request, capture)
            self.assertEqual({"type": "web_search"} in payload.get("tools", []), expected)
            if expected:
                self.assertEqual(payload["tool_choice"], {"type": "web_search"})
                self.assertEqual(payload["model"], "gpt-6-luna")
        raw = HumanMessage("잠실 첨부")
        restored = HumanMessage(content=[{"type": "text", "text": "잠실 첨부"}, {"type": "text", "text": "untrusted https://example.com 요약"}])
        request = ModelRequest(model=model, messages=[restored], system_message=SystemMessage("rules"), tools=[],
                               state={"messages": [raw], "decision": {"allowed": True}})
        self.assertNotIn({"type": "web_search"}, middleware.wrap_model_call(request, capture).get("tools", []))
        reference = HumanMessage(content=[{"type": "text", "text": "잠실 자료"},
                                          {"type": "text", "text": "참고 URL (본문을 읽은 자료가 아님): https://example.com/a?q=1"}])
        for messages, raw, expected in [([reference], HumanMessage("잠실 자료"), True),
                                        ([reference, HumanMessage("그 자료를 설명해줘")], HumanMessage("그 자료를 설명해줘"), True),
                                        ([reference, HumanMessage("다음 경기 일정")], HumanMessage("다음 경기 일정"), False),
                                        ([HumanMessage("그 자료를 설명해줘")], HumanMessage("그 자료를 설명해줘"), False)]:
            request = ModelRequest(model=model, messages=messages, system_message=SystemMessage("rules"), tools=[],
                                   state={"messages": [raw], "decision": {"allowed": True}})
            payload = middleware.wrap_model_call(request, capture)
            self.assertEqual({"type": "web_search"} in payload.get("tools", []), expected)
            if expected:
                self.assertEqual(payload["tool_choice"], {"type": "web_search"})
        request = ModelRequest(model=model, messages=[HumanMessage("잠실 http://127.0.0.1 요약")],
                               tools=[], state={"decision": {"allowed": True}})
        with self.assertRaisesRegex(ValueError, "public HTTP"):
            middleware.wrap_model_call(request, capture)

    def test_responses_annotations_stream_as_clickable_sources(self):
        from langchain_core.messages import AIMessage
        from llm.service.chat_v2 import _frames
        response = AIMessage(content=[{"type": "text", "text": "확인된 안내", "annotations": [
            {"type": "url_citation", "url": "https://example.com/article", "title": "공식 안내", "start_index": 0, "end_index": 3},
            {"type": "url_citation", "url": "javascript:alert(1)", "title": "bad"}]}],
            usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
        from unittest.mock import MagicMock
        graph = MagicMock()
        graph.stream.return_value = (item for item in [((), "messages", (response, {"langgraph_node": "model"})),
                                          ((), "updates", {"model": {"messages": [response]}})])
        run = {"answer": "", "messages": []}
        with patch("llm.v2.agent.chain.get_graph", return_value=graph):
            events = list(_frames({"messages": []}, run))
        self.assertIn("[공식 안내](https://example.com/article)", run["answer"])
        self.assertNotIn("javascript", run["answer"])
        self.assertEqual("".join(data["text"] for event, data in events if event == "delta"), run["answer"])

    def test_luna_constructors_omit_temperature(self):
        root = Path(__file__).resolve().parents[1]
        paths = [
            "v2/agent/common.py", "tools/knowledge.py",
            "v1/rag/venue/agent.py", "v1/rag/nearby/agent.py",
            "v1/rag/course/agent.py", "v1/rag/club/agent.py",
            "v1/rag/assistant/pipeline.py",
        ]
        for configured, expected in [(None, "gpt-6-luna"), ("", "gpt-6-luna"), ("override-model", "override-model")]:
            env = {} if configured is None else {"LLM_MODEL": configured}
            with patch.dict(os.environ, env, clear=True):
                for path in paths:
                    with self.subTest(model=expected, path=path):
                        tree = ast.parse((root / path).read_text())
                        scope = {
                            "os": os, "ChatOpenAI": ChatOpenAI,
                            "settings": SimpleNamespace(USAGE_MAX_CALL_OUTPUT_TOKENS=4000),
                        }
                        for node in tree.body:
                            if isinstance(node, ast.Assign) and any(
                                isinstance(t, ast.Name) and t.id == "LLM_MODEL" for t in node.targets
                            ):
                                scope["LLM_MODEL"] = eval(compile(ast.Expression(node.value), path, "eval"), scope)
                        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                                 and isinstance(node.func, ast.Name) and node.func.id == "ChatOpenAI"]
                        self.assertEqual(len(calls), 1)
                        calls[0].keywords.append(ast.keyword(arg="api_key", value=ast.Constant("offline-test")))
                        expression = ast.fix_missing_locations(ast.Expression(calls[0]))
                        model = eval(compile(expression, path, "eval"), scope)
                        payload = model._get_request_payload([{"role": "user", "content": "test"}])
                        self.assertNotIn("temperature", payload)
                        self.assertEqual(payload["model"], expected)
                        self.assertEqual(payload["reasoning"], {"effort": "medium"})
                        self.assertEqual(payload["max_output_tokens"], 4000)
                        self.assertEqual(model.request_timeout, 25)
                        self.assertEqual(model.max_retries, 0)
                        self.assertTrue(model.use_responses_api)
