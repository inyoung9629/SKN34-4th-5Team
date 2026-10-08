from unittest.mock import patch

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from llm.serializer.message import done_payload, project_history, public_frame
from llm.service.chat_v2 import _frames, _model_history
from llm.v2.agent.chat_ui import present_planning_questions, public_ui


class PlanningUiTest(SimpleTestCase):
    def test_tool_stream_history_and_public_projection(self):
        payload = {"offer_writer": True, "questions": [
            {"question": "동행은?", "choices": ["혼자", "친구"]},
            {"question": "이동은?", "choices": ["도보", "대중교통", "차", "미정"]},
        ]}
        result = present_planning_questions.invoke({"type": "tool_call", "id": "ui", "name": present_planning_questions.name, "args": payload})
        self.assertEqual(result.artifact, payload)
        human, answer = HumanMessage("계획 짜줘", id="h"), AIMessage("조건을 알려주세요", id="a")
        call = AIMessage("", tool_calls=[{"id": "ui", "name": present_planning_questions.name, "args": payload}])
        messages = [human, call, result, answer]
        turns = {"h": {"status": "completed", "answer_id": "a"}}
        self.assertEqual(project_history(messages, turns)[-1]["planning"], payload)
        self.assertEqual(done_payload(messages, turns)["planning"], payload)
        self.assertEqual(public_frame("done", done_payload(messages, turns), False)[1]["planning"], payload)
        self.assertIn("동행은?: 혼자 / 친구", _model_history(messages, turns)[-1].content)

        class Graph:
            def stream(self, *args, **kwargs):
                yield (), "updates", {"model": {"messages": [call]}}
                yield (), "updates", {"tools": {"messages": [result]}}
                yield (), "updates", {"model": {"messages": [answer]}}
        with patch("llm.v2.agent.chain.get_graph", return_value=Graph()):
            frames = list(_frames({"messages": [human]}, {"answer": "", "messages": []}))
        self.assertEqual([data for event, data in frames if event == "planning"], [payload])

    def test_invalid_optional_payload_is_omitted_without_losing_text(self):
        for choices in [["one"], ["a", "b", "c", "d", "e"], ["same", "same"], ["", "ok"], [1, "ok"]]:
            self.assertIsNone(public_ui({"offer_writer": True, "questions": [{"question": "조건", "choices": choices}]}))
        payload = {"offer_writer": True, "questions": [], "url": "javascript:bad", "private": "not public"}
        self.assertEqual(public_ui(payload), {"offer_writer": True, "questions": []})
        human, answer = HumanMessage("조건", id="h"), AIMessage("텍스트 답변", id="a")
        invalid = ToolMessage("tool", name=present_planning_questions.name, tool_call_id="ui", artifact={"offer_writer": "yes"})
        rows = project_history([human, invalid, answer], {"h": {"status": "completed", "answer_id": "a"}})
        self.assertNotIn("planning", rows[-1])
        self.assertEqual(rows[-1]["content"], "텍스트 답변")
