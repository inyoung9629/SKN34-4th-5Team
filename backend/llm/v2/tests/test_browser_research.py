"""Offline specialist delegation/allowlist contract; no paid calls."""
import os
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from llm.v2.tests import test_chain
from llm.v2.tests.test_chain import call, decision
from llm.v2.agent import travel_sub_agent, web_sub_agent


class BrowserResearchTest(unittest.TestCase):
    run_graph = test_chain.ChainTest.run_graph
    def test_nearby_evidence_returns_to_main(self):
        with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}):
            out = self.run_graph([
                call("ask_travel_research", {"task": "Find food evidence"}, "research"),
                AIMessage("후보 근거: https://example.com/menu; 영업시간 미확인"),
                AIMessage("메인 추천: 후보와 https://example.com/menu 출처, 영업시간 미확인"),
            ], decision(capabilities=["nearby_places"]), [HumanMessage("잠실 맛집 추천")])
        evidence = next(m for m in out["messages"] if isinstance(m, ToolMessage))
        self.assertIn("https://example.com/menu", evidence.content)
        self.assertIsInstance(evidence.artifact, list)
        self.assertTrue(out["messages"][-1].content.startswith("메인 추천"))

    def test_hidden_specialist_is_denied_before_execution(self):
        out = self.run_graph([
            call("ask_travel_research", {"task": "forged call"}, "hidden"), AIMessage("차단됨"),
        ], decision(capabilities=["schedule"]), [HumanMessage("경기 일정")])
        denied = next(m for m in out["messages"] if isinstance(m, ToolMessage))
        self.assertEqual(denied.status, "error")
        self.assertEqual(self.executed, [])

    def test_browser_without_token_fails_before_connecting(self):
        import asyncio
        from llm.v2.agent.browser_research import browse
        with patch.dict(os.environ, {"JEV_MCP_TOKEN": ""}):
            with self.assertRaisesRegex(RuntimeError, "JEV_MCP_TOKEN"):
                asyncio.run(browse("https://example.com", "read"))

    def test_research_model_params_satisfy_meter_output_cap(self):
        from django.conf import settings
        from llm.service.usage import Meter, UnsafeOutputLimit
        from llm.v2.agent.browser_research import research_model
        research_model.cache_clear()
        self.addCleanup(research_model.cache_clear)
        with patch.dict(os.environ, {"RESEARCH_API_KEY": "offline-test-key"}):
            params = research_model()._get_invocation_params()
        self.assertEqual(params["max_completion_tokens"], settings.USAGE_MAX_CALL_OUTPUT_TOKENS)
        meter = Meter()
        meter.on_chat_model_start({}, [[]], run_id="research", invocation_params=params)
        self.assertEqual(meter.totals(), (0, 0, 1, 1))
        uncapped = {k: v for k, v in params.items()
                    if k not in ("max_tokens", "max_completion_tokens", "max_output_tokens")}
        with self.assertRaises(UnsafeOutputLimit):
            meter.on_chat_model_start({}, [[]], invocation_params=uncapped)

    def test_enabled_specialist_uses_research_model_and_tool(self):
        from llm.v2.tests.test_chain import ScriptedModel, fake_tools
        model = ScriptedModel(script=[], calls=[])
        with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "true"}), patch.object(web_sub_agent, "research_model", return_value=model), patch.object(web_sub_agent, "direct_tools", return_value=[]), patch.object(web_sub_agent, "build_agent") as build:
            web_sub_agent.build(object(), fake_tools([]))
        self.assertIs(build.call_args.args[0], model)
        self.assertEqual(build.call_args.args[1], [])
