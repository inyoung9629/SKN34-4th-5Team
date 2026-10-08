"""Offline web ownership and genuine body admission regressions."""
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, TransactionTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from llm.models import ChatAttachment, ChatSession
from llm.service import attachments
from llm.v2.agent import browser_research, chain, travel_sub_agent
from llm.v2.tests.test_chain import ScriptedModel, fake_tools, call


class WebOwnershipTests(SimpleTestCase):
    def test_keyword_delegation_and_denied_assignment(self):
        for allowed in (True, False):
            calls = []
            script = [call("ask_web_research", {"task": "잠실 메뉴 검색"}, "web")]
            if allowed:
                script.append(AIMessage("검색 근거 https://example.com/menu; 시간 미확인"))
            script.append(AIMessage("메인 안내"))
            with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch(
                    "llm.v2.middleware.jev_guidelines.classify",
                    return_value={"allowed": True, "capabilities": ["web_research"] if allowed else ["schedule"]}):
                graph = chain.build_graph(ScriptedModel(script=script, calls=calls), fake_tools([]))
                out = graph.invoke({"messages": [HumanMessage("잠실 검색")]})
            self.assertNotIn("web_search", calls[0]["tools"])
            self.assertNotIn("research_public_web", calls[0]["tools"])
            result = next(m for m in out["messages"] if isinstance(m, ToolMessage))
            if allowed:
                self.assertIn("https://example.com/menu", result.content)
                self.assertNotIn("research_public_web", calls[1]["tools"])
                self.assertNotIn("ask_web_research", calls[1]["tools"])
            else:
                self.assertEqual(result.status, "error")
            self.assertEqual(out["messages"][-1].text, "메인 안내")
        self.assertNotIn("research_public_web", travel_sub_agent.TOOLS)

    def test_body_rejects_generated_partial_private_overflow_and_busy(self):
        for result in ({"final_text": "made up", "status": "ok"},
                       {"status": "partial", "body": "short"}, {"status": "blocked"},
                       {"status": "ok", "body": "x" * (attachments.MAX_TEXT + 1), "source_kind": "rendered_dom_snapshot"}):
            with patch.object(browser_research, "browse", AsyncMock(return_value=result)):
                self.assertNotEqual(browser_research.web_body("https://example.com/")["status"], "ok")
        with patch.object(browser_research, "browse", AsyncMock()) as browse:
            with self.assertRaises(Exception):
                browser_research.web_body("http://127.0.0.1/")
            browse.assert_not_called()
            browser_research._admission.acquire()
            try:
                self.assertEqual(browser_research.web_body("https://example.com/")["status"], "busy")
            finally:
                browser_research._admission.release()
            browse.assert_not_called()


class URLContextTests(TransactionTestCase):
    def setUp(self):
        self.session = ChatSession.objects.create(guest=uuid.uuid4())
        self.row = ChatAttachment.objects.create(session=self.session, kind="url", name="article",
                                                 source_url="https://example.com/article")

    def invoke(self):
        calls = []
        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}):
            graph = chain.build_graph(ScriptedModel(script=[AIMessage("main")], calls=calls), fake_tools([]))
            graph.invoke({"messages": [HumanMessage("잠실 첨부", id="q", additional_kwargs={"attachment_ids": [str(self.row.id)]})],
                          "attachment_session_id": str(self.session.id)})
        return next(m for m in calls[0]["messages"] if isinstance(m, HumanMessage)).text

    def test_actual_beginning_middle_end_reaches_main_then_cache_reused(self):
        body = "BEGIN " + "baseball " * 1500 + " MIDDLE " + "seats " * 1500 + " END"
        with patch.object(browser_research, "web_body", return_value={"status": "ok", "body": body}) as fetch:
            self.assertIn(body, self.invoke())
            self.assertIn(body, self.invoke())
            self.assertEqual(fetch.call_count, 1)
        self.row.refresh_from_db()
        self.assertEqual(self.row.extracted_text, body)

    def test_failed_cancelled_and_overflow_never_cache_body(self):
        from llm.v2.middleware.attachment_context import DirectContextLimit
        for result, error in (({"status": "blocked"}, ValueError),
                              ({"status": "partial", "body": "fabricated"}, ValueError),
                              ({"status": "overflow"}, attachments.AttachmentProcessingLimit),
                              ({"status": "ok", "body": "word " * 21000}, DirectContextLimit)):
            with patch.object(browser_research, "web_body", return_value=result), self.assertRaises(error):
                self.invoke()
            self.row.refresh_from_db()
            self.assertEqual(self.row.extracted_text, "")
        with patch.object(browser_research, "web_body", side_effect=RuntimeError("cancelled")), self.assertRaises(RuntimeError):
            self.invoke()
        self.row.refresh_from_db()
        self.assertEqual(self.row.extracted_text, "")
