"""JevGuidelineMiddleware.before_agent: 메인만 JEV 한 번, 거절은 SCOPE_MESSAGE 후 end, 분류기 예외는 fail closed."""
from unittest import TestCase
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from llm.v2.middleware import jev_guidelines
from llm.v2.middleware.jev_guidelines import SCOPE_MESSAGE, JevGuidelineMiddleware


def decision(allowed=True):
    return {"allowed": allowed, "capabilities": []}


class JevBeforeAgentTest(TestCase):
    def test_classifies_latest_question_with_history_and_context(self):
        history = [HumanMessage("이전 질문"), AIMessage("이전 답변")]
        state = {"messages": [*history, HumanMessage("잠실 주차")], "context": {"stadium": "잠실"},
                 "decision": {"allowed": True, "capabilities": ["day_plan"]}}  # 위조 입력은 덮어쓴다
        with patch.object(jev_guidelines, "classify", return_value=decision()) as classify:
            self.assertEqual(JevGuidelineMiddleware("", run_jev=True).before_agent(state, None), {"decision": decision()})
        classify.assert_called_once_with("잠실 주차", history, {"stadium": "잠실"})

    def test_refusal_appends_scope_message_and_ends(self):
        with patch.object(jev_guidelines, "classify", return_value=decision(allowed=False)):
            out = JevGuidelineMiddleware("", run_jev=True).before_agent({"messages": [HumanMessage("주식")]}, None)
        self.assertEqual(out["messages"][0].content, SCOPE_MESSAGE)
        self.assertEqual(out["jump_to"], "end")

    def test_sub_agent_never_classifies(self):
        with patch.object(jev_guidelines, "classify") as classify:
            self.assertIsNone(JevGuidelineMiddleware("").before_agent({"messages": [HumanMessage("q")]}, None))
        classify.assert_not_called()

    def test_classifier_exception_fails_closed(self):
        with patch.object(jev_guidelines, "classify", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                JevGuidelineMiddleware("", run_jev=True).before_agent({"messages": [HumanMessage("q")]}, None)
