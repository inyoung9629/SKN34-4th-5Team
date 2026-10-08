from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_from_dict, messages_to_dict

from llm.service import chat_runs, chat_v2
from llm.tests.test_course_editing import CURRENT, PLACES, plan
from llm.v1.rag.course import agent, editing, memory
from llm.v1.rag.course.conversation_scope import new_context, scoped_messages
from llm.v2.agent import chain
from llm.v2.middleware import jev_guidelines
from llm.v2.tests.test_chain import ScriptedModel, fake_tools


class CourseMemoryScopeTests(SimpleTestCase):
    def setUp(self):
        self.origin = {"lat": 37.51, "lng": 127.08, "name": "내가 찍은 출발지"}
        self.current = {**deepcopy(CURRENT), "writerState": {"title": "이전 제목", "origin": self.origin, "completed": True}}
        self.saved = {"conditions": [{"scope": "STAY", "text": "조용한 호텔"}, {"scope": "TRAVEL", "text": "자가용"}],
                      "locked": [PLACES[1]], "rejected": [PLACES[0]], "current": self.current,
                      "origin": {"lat": 35.17, "lng": 129.08}, "undo": {"old": True}}
        self.history = [HumanMessage("호텔과 카페, 스테이크를 넣어줘", id="old"), AIMessage("옛 코스", id="old-answer")]
        self.context = {"stadium": "JAMSIL", "intent": "route", "origin": self.origin,
                        "currentCourse": self.current, "routePath": {"points": [self.origin, PLACES[2]]}}

    def graph(self, scope):
        calls = []
        graph = chain.build_graph(ScriptedModel(script=[AIMessage("완료")], calls=calls), fake_tools([]))
        with patch.object(jev_guidelines, "classify", return_value={"allowed": True, "capabilities": [], "course_request": scope}):
            result = graph.invoke({"messages": [*self.history, HumanMessage("이번 요청")],
                                   "course_memory": self.saved, "context": self.context})
        return result, calls

    def test_new_graph_removes_history_memory_and_old_map_course_before_model(self):
        result, calls = self.graph("NEW")
        self.assertEqual(len(calls), 1)
        texts = [m.content for m in calls[0]["messages"]]
        self.assertNotIn(self.history[0].content, texts)
        self.assertNotIn(self.history[1].content, texts)
        self.assertNotIn("기존 카페", calls[0]["system"])
        self.assertEqual(result["course_memory"], memory.empty())
        self.assertEqual(result["context"], new_context(self.context))
        self.assertEqual(result["context"]["origin"], self.origin)
        self.assertEqual(result["context"]["routePath"], self.context["routePath"])
        self.assertIn("day_plan", result["decision"]["capabilities"])
        self.assertIn("currentCourse", self.context)  # 화면/저장 기록 원본은 불변

    def test_edit_graph_keeps_history_memory_and_current_course(self):
        result, calls = self.graph("EDIT")
        self.assertIn(self.history[0].content, [m.content for m in calls[0]["messages"]])
        self.assertIn("기존 카페", calls[0]["system"])
        self.assertEqual(result["context"], self.context)
        self.assertEqual(result["course_memory"], self.saved)

    def test_new_generation_uses_only_current_question_and_map_origin(self):
        before = deepcopy(self.saved)
        new_plan = plan("new", [], request_preferences=[{"scope": "FOOD", "text": "초밥"}])
        generated = {"places": deepcopy([PLACES[0], PLACES[2], PLACES[3]]), "stadiumCode": "JAMSIL"}
        with patch.object(editing, "interpret", return_value=new_plan) as interpret, \
                patch.object(agent, "_answer", return_value=generated) as generate:
            result = agent.answer("초밥 먹고 산책 코스 짜줘", history=[{"role": "user", "content": "호텔도"}],
                                  hint_stadium="JAMSIL", current_course=self.current, course_memory=self.saved,
                                  route_path=self.context["routePath"], course_request="NEW")
        interpret.assert_called_once()
        self.assertEqual(interpret.call_args.args[1]["places"], [])
        self.assertEqual(interpret.call_args.args[2:], ([], {**memory.empty(), "stadiumCode": "JAMSIL"}))
        self.assertEqual(generate.call_args.args[1], [])
        self.assertEqual(generate.call_args.args[3], self.origin)
        self.assertEqual(generate.call_args.args[5], self.context["routePath"])
        self.assertEqual(memory.relevant(generate.call_args.kwargs["course_memory"], "FOOD"), ["초밥"])
        self.assertEqual(memory.core(result["courseMemory"]), memory.empty())
        self.assertNotIn("undo", result["courseMemory"])
        self.assertTrue(result["courseHistoryReset"])
        self.assertEqual(self.saved, before)

    def test_generator_reinterprets_new_operation_without_old_context_if_classifier_disagrees(self):
        contaminated = plan("new", [], preferences=[{"scope": "STAY", "text": "호텔"}])
        clean = plan("new", [])
        with patch.object(editing, "interpret", side_effect=[contaminated, clean]) as interpret, \
                patch.object(agent, "_answer", return_value={"places": []}) as generate:
            result = agent.answer("새 코스 짜줘", hint_stadium="JAMSIL", current_course=self.current,
                                  course_memory=self.saved, history=[{"role": "user", "content": "호텔도"}])
        self.assertEqual(interpret.call_count, 2)
        self.assertEqual(interpret.call_args.args[2:], ([], memory.empty()))
        self.assertEqual(generate.call_args.args[1], [])
        self.assertEqual(result["courseMemory"], memory.empty())

    def test_edit_keeps_current_visits_conditions_and_origin(self):
        with patch.object(editing, "interpret", return_value=plan("move", ["cafe"])) as interpret, \
                patch.object(editing, "answer", return_value={"edit": True}) as edit, patch.object(agent, "_answer") as generate:
            result = agent.answer("카페만 맨 뒤로", hint_stadium="JAMSIL", current_course=self.current,
                                  course_memory=self.saved, history=[{"role": "user", "content": "같은 조건"}], course_request="EDIT")
        self.assertTrue(result["edit"])
        self.assertEqual(interpret.call_args.args[1], self.current)
        self.assertEqual(edit.call_args.args[4], self.origin)
        self.assertEqual(edit.call_args.args[5]["conditions"], self.saved["conditions"])
        generate.assert_not_called()

    def test_cleared_origin_and_missing_stadium_are_not_restored_from_history(self):
        self.context["currentCourse"]["writerState"]["origin"] = None
        self.assertIsNone(new_context(self.context)["origin"])
        with patch.object(editing, "interpret", return_value=plan("new", [])), \
                patch.object(agent, "_answer", return_value={"places": []}) as generate:
            agent.answer("새로 짜줘", course_memory=self.saved, course_request="NEW")
        self.assertIsNone(generate.call_args.args[2])
        self.assertIsNone(generate.call_args.args[3])

    def transcript(self, marker="answer", status="completed", deleted=False):
        old_tool = ToolMessage("old", name="plan_course", tool_call_id="old-tool", artifact={"course_memory": self.saved})
        reset = {"course_history_reset": True}
        result = [self.history[0], old_tool, self.history[1], HumanMessage("새 코스", id="new")]
        if marker == "tool":
            result.append(ToolMessage("new", name="plan_course", tool_call_id="new-tool", artifact=reset))
        result += [AIMessage("새 답변", id="new-answer", response_metadata=reset if marker == "answer" else {}),
                   HumanMessage("산책 시간만 20분", id="edit"), AIMessage("수정 완료", id="edit-answer")]
        turns = {"old": {"status": "completed", "answer_id": "old-answer"},
                 "new": {"status": status, "answer_id": "new-answer", "answer_deleted": deleted},
                 "edit": {"status": "completed", "answer_id": "edit-answer"}}
        return messages_from_dict(messages_to_dict(result)), turns

    def test_completed_boundary_survives_reload_and_limits_later_edits(self):
        for marker in ("answer", "tool"):
            messages, turns = self.transcript(marker)
            selected = scoped_messages(messages, turns)
            self.assertEqual(selected[0].id, "new")
            self.assertEqual(memory.restore(messages, turns), {})
            self.assertEqual(messages[0].id, "old")
            with patch.object(chat_runs, "stream_turn") as stream:
                from types import SimpleNamespace
                chat_v2._stream_turn(SimpleNamespace(thread_id="scope-test"), messages, turns, HumanMessage("순서 바꿔줘"), self.context, None)
            # _frames 캡처로 서비스가 실제 사용하는 모델 입력을 확인한다.
            with patch.object(chat_v2, "_frames") as frames:
                stream.call_args.args[4]()
            self.assertEqual([m.content for m in frames.call_args.args[0]["messages"]],
                             ["새 코스", "새 답변", "산책 시간만 20분", "수정 완료", "순서 바꿔줘"])
            self.assertEqual(frames.call_args.args[0]["course_memory"], {})

    def test_failed_cancelled_deleted_turn_does_not_reset_completed_memory(self):
        for status, deleted in (("failed", False), ("cancelled", False), ("completed", True)):
            messages, turns = self.transcript(status=status, deleted=deleted)
            self.assertEqual(scoped_messages(messages, turns)[0].id, "old")
            self.assertEqual(memory.restore(messages, turns), self.saved)

    def test_stream_records_new_boundary_even_when_model_asks_for_missing_stadium(self):
        updates = (update for update in [((), "updates", {"before_agent": {"decision": {"course_request": "NEW"}}}),
                                         ((), "updates", {"model": {"messages": [AIMessage("구장을 선택해 주세요")]}})])
        run = {"answer": "", "messages": []}
        with patch("llm.v2.agent.chain.get_graph", return_value=SimpleNamespace(stream=lambda *a, **kw: updates)):
            list(chat_v2._frames({}, run))
        self.assertTrue(run["course_history_reset"])
        self.assertEqual(run["answer"], "구장을 선택해 주세요")

    def test_completed_stream_persists_boundary_without_removing_transcript(self):
        thread = SimpleNamespace(thread_id="scope-unit-test", wire={}, update=Mock(return_value=True))
        run = {"answer": "새 코스예요", "messages": [], "course_history_reset": True}
        human = HumanMessage("새 코스", id="new")
        with patch("llm.serializer.message.wire_done", return_value={"ok": True}) as done:
            list(chat_runs.stream_turn(thread, self.history, {}, human,
                                       lambda: (frame for frame in [("delta", {"text": run["answer"]})]), run, label="v2"))
        final = thread.update.call_args.args[0][-1]
        self.assertTrue(final.response_metadata["course_history_reset"])
        self.assertEqual(done.call_args.args[0][:2], self.history)
        self.assertEqual(scoped_messages(done.call_args.args[0], done.call_args.args[1])[0].id, "new")
