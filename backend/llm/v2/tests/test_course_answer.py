"""Real graph/stream contracts for direct course answers and mixed-request fallback."""
from copy import deepcopy
from datetime import date
from unittest import TestCase
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from llm.tests.test_course_output import COURSE
from llm.v2.agent import chain
from llm.v2.middleware import jev_guidelines as classifier
from llm.v2.middleware.course_answer import CourseAnswerMiddleware, course_text
from llm.v2.tests.test_chain import ScriptedModel, call, decision
from llm.v2.tests.test_migrated_tools import tools_with_real_migrated


TEXT = "경기 코스예요.\n18:10 잠실야구장\n숙소 [NOL](https://nol.yanolja.com/stay/domestic/123)\n시간 안내: 도착이 늦어요.\n미확인 조건은 확인해 주세요."


class CourseAnswerTest(TestCase):
    def test_balanced_edit_returns_verified_tool_result_without_dispatch_or_rewrite(self):
        from django.test import override_settings
        with override_settings(COURSE_RECOMMENDATION_MODE="balanced"):
            for course_only in (True, False):
                calls = []
                graph = chain.build_graph(ScriptedModel(script=[AIMessage("추가 질문 답변")], calls=calls), tools_with_real_migrated([]))
                verdict = {**decision(capabilities=["day_plan"]), "course_request": "EDIT"}
                current = {**deepcopy(COURSE), "travelMode": "walk", "legModes": {}}
                for i, place in enumerate(current["places"]):
                    place.update(label=str(i + 1), visitId=f"test-{i}")
                with patch.object(classifier, "classify", return_value=verdict), \
                        patch("llm.v1.rag.course.agent.answer", return_value={**COURSE, "edit": True, "route": "course:edit:replace", "answer": TEXT, "courseOnly": course_only}) as course:
                    result = graph.invoke({"messages": [HumanMessage("식당만 돈카츠로 수정해줘")],
                                           "context": {"intent": "route", "stadium": "JAMSIL", "currentCourse": current}})
                course.assert_called_once()
                self.assertEqual(len(calls), 0 if course_only else 1)
                self.assertEqual(result["messages"][-1].text, TEXT if course_only else "추가 질문 답변")

    def test_route_screen_dispatch_keeps_tool_validation_and_mixed_final_model(self):
        for direct in (True, False):
            calls = []
            model = ScriptedModel(script=[AIMessage("복합 질문 최종 안내")], calls=calls)
            graph = chain.build_graph(model, tools_with_real_migrated([]))
            verdict = {**decision(capabilities=["day_plan"]), "course_request": "NEW"}
            question = "잠실 코스와 준비물도 알려줘" if not direct else "잠실 코스 짜줘"
            with patch.object(classifier, "classify", return_value=verdict), \
                    patch("llm.v1.rag.course.agent.answer", return_value={**COURSE, "answer": TEXT, "courseOnly": direct}) as course:
                result = graph.invoke({"messages": [HumanMessage(question)],
                                       "context": {"intent": "route", "stadium": "JAMSIL"}})
            course.assert_called_once()
            self.assertEqual(len(calls), 0 if direct else 1)
            self.assertEqual(result["messages"][-1].text, TEXT if direct else "복합 질문 최종 안내")
            tool_calls = [c for m in result["messages"] if isinstance(m, AIMessage) for c in m.tool_calls]
            self.assertEqual([c["name"] for c in tool_calls], ["ask_course"])
            self.assertEqual(tool_calls[0]["args"]["task"], question)

    def test_route_dispatch_requires_guard_new_known_stadium_and_no_extra_or_attachment(self):
        boundary = CourseAnswerMiddleware()
        base = {"decision": {"allowed": True, "course_request": "NEW", "capabilities": ["day_plan"]},
                "context": {"intent": "route", "stadium": "JAMSIL"}, "messages": [HumanMessage("잠실 코스 짜줘")]}
        self.assertEqual(boundary.before_model(base, None)["jump_to"], "tools")
        for variation in ("guard", "edit", "stadium", "intent", "capability", "weather", "attachment", "started"):
            state = deepcopy(base)
            if variation == "guard": state["decision"]["allowed"] = False
            if variation == "edit": state["decision"]["course_request"] = "EDIT"
            if variation == "stadium": state["context"]["stadium"] = "UNKNOWN"
            if variation == "intent": state["context"]["intent"] = "general"
            if variation == "capability": state["decision"]["capabilities"] = []
            if variation == "weather": state["messages"][0].content += " 날씨도 알려줘"
            if variation == "attachment": state["messages"][0].additional_kwargs["attachment_ids"] = ["owned-test"]
            if variation == "started": state["messages"].append(call("ask_course", {"task": "코스"}, "c"))
            with self.subTest(variation=variation):
                self.assertIsNone(boundary.before_model(state, None))

    def graph(self):
        self.calls = []
        self.model = ScriptedModel(script=[call("ask_course", {"task": "코스"}, "c"), AIMessage("복합 질문 최종 안내")], calls=self.calls)
        return chain.build_graph(self.model, tools_with_real_migrated([]))

    def execute(self, question="경기 후 호텔 포함 코스 짜줘", *, direct=True, capabilities=None):
        graph = self.graph()
        verdict = {**decision(capabilities=capabilities or ["day_plan"]), "course_request": "NEW"}
        with patch.object(classifier, "classify", return_value=verdict), \
                patch("llm.v1.rag.course.agent.answer", return_value={**COURSE, "answer": TEXT, "courseOnly": direct}):
            return graph.invoke({"messages": [HumanMessage(question)]})

    def test_course_only_skips_final_model_and_preserves_all_text_and_public_course(self):
        result = self.execute()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["messages"][-1].text, TEXT)
        self.assertTrue(result["messages"][-1].response_metadata["course_direct"])
        from llm.v2.agent.course_output import course_artifacts
        self.assertEqual(next(course_artifacts(result["messages"]))["course"], COURSE)

    def test_mixed_or_ambiguous_requests_keep_final_model(self):
        for question, direct, caps in (
            ("코스와 날씨도 알려줘", True, ["day_plan", "weather"]),
            ("코스와 홈런 영상도 보여줘", True, ["day_plan", "highlight"]),
            ("코스와 볼넷 뜻을 설명해줘", True, ["day_plan"]),
            ("호텔 코스와 구장 주차 요금도", True, ["day_plan"]),
            ("코스를 표로 정리해줘", True, ["day_plan"]),
            ("코스와 준비물도 알려줘", False, ["day_plan"]),
        ):
            with self.subTest(question=question):
                result = self.execute(question, direct=direct, capabilities=caps)
                self.assertEqual(len(self.calls), 2)
                self.assertEqual(result["messages"][-1].text, "복합 질문 최종 안내")

    def test_partial_failed_other_tool_or_attachment_never_uses_direct_path(self):
        base = {"decision": {"allowed": True, "course_request": "NEW", "capabilities": ["day_plan"]}, "messages": [
            HumanMessage("코스 짜줘"), call("ask_course", {"task": "코스"}, "c"),
            ToolMessage(TEXT, name="ask_course", tool_call_id="c", artifact=[
                ToolMessage(TEXT, name="plan_course", tool_call_id="inner", artifact={"course": COURSE, "course_direct_answer": TEXT})])]}
        boundary = CourseAnswerMiddleware()
        self.assertEqual(boundary.before_model(base, None)["jump_to"], "end")
        base["attachment_messages"] = {"question": base["messages"][0]}
        self.assertEqual(boundary.before_model(base, None)["jump_to"], "end")
        for variation in ("attachment", "pending", "failed", "extra_tool", "no_course", "edit", "guard"):
            state = deepcopy(base)
            if variation == "attachment": state["messages"][0].additional_kwargs["attachment_ids"] = ["owned-test"]
            if variation == "pending": state["messages"].pop()
            if variation == "failed": state["messages"][-1].status = "error"
            if variation == "extra_tool": state["messages"].append(call("get_weather", {}, "w"))
            if variation == "no_course": state["messages"][-1].artifact[0].artifact["course"] = None
            if variation == "edit": state["decision"]["course_request"] = "EDIT"
            if variation == "guard": state["decision"]["allowed"] = False
            with self.subTest(variation=variation):
                self.assertIsNone(boundary.before_model(state, None))

    def test_past_date_note_and_existing_warnings_are_retained(self):
        with patch("llm.v2.middleware.course_answer.timezone.localdate", return_value=date(2026, 10, 11)):
            text = course_text({"courseOnly": True, "answer": TEXT, "game": {"date": "2026-10-07"}})
        self.assertIn("이미 지난 날짜", text)
        self.assertTrue(text.endswith(TEXT))

    def test_direct_answer_is_streamed_once_without_a_final_model_call(self):
        from llm.service.chat_v2 import _frames
        graph = self.graph()
        run = {"answer": "", "messages": []}
        with patch.object(chain, "get_graph", return_value=graph), \
                patch.object(classifier, "classify", return_value={**decision(capabilities=["day_plan"]), "course_request": "NEW"}), \
                patch("llm.v1.rag.course.agent.answer", return_value={**COURSE, "answer": TEXT, "courseOnly": True}):
            frames = list(_frames({"messages": [HumanMessage("숙박 코스 짜줘")]}, run))
        self.assertEqual(run["answer"], TEXT)
        self.assertEqual([d["text"] for e, d in frames if e == "delta"], [TEXT])
        self.assertEqual(len(self.calls), 1)

    def test_route_screen_stream_preserves_course_and_answer_without_dispatch_model(self):
        from llm.service.chat_v2 import _frames
        graph = self.graph()
        run = {"answer": "", "messages": []}
        with patch.object(chain, "get_graph", return_value=graph), \
                patch.object(classifier, "classify", return_value={**decision(capabilities=["day_plan"]), "course_request": "NEW"}), \
                patch("llm.v1.rag.course.agent.answer", return_value={**COURSE, "answer": TEXT, "courseOnly": True}):
            frames = list(_frames({"messages": [HumanMessage("숙박 코스 짜줘")],
                                  "context": {"intent": "route", "stadium": "JAMSIL"}}, run))
        self.assertEqual(run["answer"], TEXT)
        self.assertEqual([d["text"] for e, d in frames if e == "delta"], [TEXT])
        self.assertEqual(len(self.calls), 0)
