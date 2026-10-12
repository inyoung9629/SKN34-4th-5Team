"""Preview delegation boundaries; provider mocks do not establish real accuracy."""
import tempfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from llm.tests.test_v2_chat import CheckpointTestCase
from langchain_core.messages import HumanMessage, ToolMessage

from llm.highlight_multimodal import DIM, Specialist
from llm.v2.agent import highlight_sub_agent
from llm.v2.agent.chat_ui import highlight_videos


class MultimodalHTTPTests(CheckpointTestCase):
    def test_guest_sse_and_saved_card_contract(self):
        from langchain_core.messages import AIMessage
        from rest_framework.test import APIClient
        from llm.models import ChatSession
        from llm.v2.agent import chain, sub_agents
        from types import SimpleNamespace

        class Graph:
            def stream(self, inputs, **kwargs):
                runtime = SimpleNamespace(state=inputs, config={}, tool_call_id="highlight-call")
                agent = highlight_sub_agent.build(None, {})
                result = sub_agents._delegate("ask_highlight", "highlight", agent).func(
                    inputs["messages"][-1].text, runtime)
                yield (), "updates", {"tools": {"messages": [result]}}
                yield (), "updates", {"model": {"messages": [AIMessage(result.content)]}}

        client = APIClient()
        session = ChatSession.objects.create(guest="99999999-1111-2222-3333-999999999999")
        client.cookies["guest_id"] = str(session.guest)
        url = f"/api/v2/chat/sessions/{session.id}/messages/"
        with tempfile.TemporaryDirectory() as root, override_settings(HIGHLIGHT_MULTIMODAL_CORPUS=root), \
                patch.object(chain, "get_graph", return_value=Graph()), \
                patch("llm.highlight_multimodal.Google"), patch("llm.highlight_multimodal.Specialist") as specialist:
            for complete in (True, False):
                specialist.return_value.ask.return_value = {"complete": complete, "no_match": False if complete else None,
                    "decisions": [] if complete else [{"provider_error": "Google generateContent HTTP 429"}],
                    "matches": [{"video_id": "SlvDBmr4Bd8", "playback_seconds": 123.75, "evidence": "observed contact"}] if complete else []}
                response = client.post(url, {"content": "홈런"}, format="json")
                self.assertEqual(response.status_code, 200)
                body = b"".join(response.streaming_content).decode()
                self.assertIn("event: done", body)
                history = client.get(url).json()
                answer = history[-1]
                if complete:
                    self.assertEqual(answer["videos"][0]["start"], 123)
                    self.assertEqual(answer["videos"][0]["videoId"], "SlvDBmr4Bd8")
                else:
                    self.assertFalse(answer.get("videos"))
                    self.assertIn("Google generateContent HTTP 429", answer["content"])
                    self.assertIn("조회 실패", body)


class MultimodalWebTests(SimpleTestCase):
    def records(self):
        return [m for update in highlight_sub_agent.MultimodalAgent().stream({"messages": [HumanMessage("홈런")]})
                for node in update.values() for m in node["messages"]]

    @override_settings(HIGHLIGHT_MULTIMODAL_CORPUS="")
    def test_opt_out_uses_original_agent(self):
        tools = {name: object() for name in highlight_sub_agent.TOOLS}
        with patch.object(highlight_sub_agent, "build_agent", return_value="original") as build:
            self.assertEqual(highlight_sub_agent.build(None, tools), "original")
            build.assert_called_once_with(None, list(tools.values()), highlight_sub_agent.HIGHLIGHT_RULES)

    @override_settings(HIGHLIGHT_MULTIMODAL_CORPUS="")
    def test_opt_out_error_then_success_preserves_card(self):
        from types import SimpleNamespace
        from langchain_core.messages import AIMessage
        from llm.v2.agent import sub_agents

        card = {"videoId": "SlvDBmr4Bd8", "start": 123, "label": "recovered scene"}

        class RecoveredAgent:
            def stream(self, inputs, **kwargs):
                yield {"tools": {"messages": [ToolMessage("search failed", name="search_highlight_rag",
                       tool_call_id="search", status="error")]}}
                yield {"tools": {"messages": [ToolMessage("selected", name="select_highlight_scenes",
                       tool_call_id="select", artifact={"videos": [card]})]}}
                yield {"model": {"messages": [AIMessage("recovered answer")]}}

        tools = {name: object() for name in highlight_sub_agent.TOOLS}
        with patch.object(highlight_sub_agent, "build_agent", return_value=RecoveredAgent()):
            agent = highlight_sub_agent.build(None, tools)
        runtime = SimpleNamespace(state={}, config={}, tool_call_id="outer")
        result = sub_agents._delegate("ask_highlight", "highlight", agent).func("홈런", runtime)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.content, "recovered answer")
        self.assertEqual(highlight_videos([result]), [card])

    def test_cards_original_time_errors_and_no_match(self):
        with tempfile.TemporaryDirectory() as root, override_settings(HIGHLIGHT_MULTIMODAL_CORPUS=root):
            self.assertIsInstance(highlight_sub_agent.build(None, {}), highlight_sub_agent.MultimodalAgent)
            with patch("llm.highlight_multimodal.Specialist") as specialist, patch("llm.highlight_multimodal.Google"):
                instance = specialist.return_value
                instance.ask.return_value = {"complete": True, "no_match": False, "decisions": [], "matches": [
                    {"video_id": "SlvDBmr4Bd8", "playback_seconds": 123.75, "evidence": "실제 타구 확인"}]}
                records = self.records()
                nested = ToolMessage("answer", name="ask_highlight", tool_call_id="outer", artifact=records)
                self.assertEqual(highlight_videos([nested]), [{"videoId": "SlvDBmr4Bd8", "start": 123, "label": "실제 타구 확인"}])
                instance.close.assert_called_once()
                instance.ask.assert_called_once_with("홈런", candidate_limit=3)
                for failure in ({"provider_error": "Google generateContent HTTP 429"}, {"rejected": "malformed provider output"}):
                    instance.ask.return_value = {"complete": False, "no_match": None, "matches": [], "decisions": [failure]}
                    records = self.records()
                    self.assertEqual(records[0].status, "error")
                    self.assertEqual(highlight_videos(records), [])
                    self.assertIn("장면 없음으로 판단할 수 없습니다", records[-1].text)
                instance.ask.side_effect = RuntimeError("Google embedContent HTTP 429")
                self.assertIn("Google embedContent HTTP 429", self.records()[-1].text)
                instance.ask.side_effect = None
                instance.ask.return_value = {"complete": True, "no_match": True, "matches": [], "decisions": []}
                self.assertIn("전체 경기 검색 아님", self.records()[-1].text)

    def test_mounted_corpus_concurrent_requests_close_local_client(self):
        from django.conf import settings
        root = settings.HIGHLIGHT_MULTIMODAL_CORPUS
        if not root:
            self.skipTest("requires mounted preview corpus")

        class OfflineGoogle:
            def embed(self, part):
                return [1.] * DIM
            def verify(self, query, row, video, audio):
                assert video.exists() and audio.exists()
                return {"candidate_id": row["id"], "match": True, "local_seconds": 2.5, "evidence": "offline routing test"}

        with patch("llm.highlight_multimodal.Google", OfflineGoogle):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.records(), range(2)))
            for records in results:
                result = records[0].artifact["multimodal"]
                self.assertTrue(result["complete"])
                match = result["matches"][0]
                cards = highlight_videos(records)
                self.assertEqual(cards[0]["start"], int(match["playback_seconds"]))
            specialist = Specialist(root, OfflineGoogle())
            try:
                self.assertEqual(len(specialist.rows), 12)
                self.assertEqual(specialist.db.count("highlight_multimodal_experiment_v1").count, 12)
                row = specialist.rows[results[0][0].artifact["multimodal"]["matches"][0]["candidate_id"]]
                self.assertAlmostEqual(results[0][0].artifact["multimodal"]["matches"][0]["playback_seconds"], row["start"] + 2.5)
            finally:
                specialist.close()
