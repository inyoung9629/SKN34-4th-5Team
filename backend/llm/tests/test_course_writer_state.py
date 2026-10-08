from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase
from langchain_core.messages import HumanMessage, ToolMessage
from rest_framework.exceptions import ValidationError

from llm.serializer.message import _validate_context
from llm.tests.test_course_editing import CURRENT, PLACES, plan
from llm.v1.rag.course import agent, editing, memory
from llm.v2.agent.course_output import public_course


class CourseWriterStateTests(SimpleTestCase):
    def setUp(self):
        self.writer = {"title": "친구와 잠실 나들이", "origin": {"lat": 37.50, "lng": 127.06, "name": "잠실새내역"}, "completed": False}
        self.current = {**deepcopy(CURRENT), "writerState": deepcopy(self.writer)}

    def test_context_and_public_checkpoint_preserve_custom_title_origin_and_edit_mode(self):
        current = _validate_context({"currentCourse": self.current})["currentCourse"]
        result = memory.attach({"places": PLACES, "stadiumCode": "JAMSIL", "edit": True}, memory.empty(), current, self.writer["origin"])
        self.assertEqual(public_course(result)["writerState"], self.writer)
        self.assertEqual(result["courseMemory"]["current"]["writerState"], self.writer)
        restored = memory.restore([HumanMessage("수정", id="h"), ToolMessage("완료", name="plan_course", tool_call_id="t",
                                  artifact={"course_memory": result["courseMemory"], "course": public_course(result)})],
                                  {"h": {"status": "completed"}})
        self.assertEqual(restored["current"]["writerState"], self.writer)

    def test_origin_only_map_is_authoritative_over_the_previous_conversation(self):
        current = {**self.current, "places": []}
        validated = _validate_context({"currentCourse": current})["currentCourse"]
        saved = {**memory.empty(), "current": deepcopy(CURRENT), "origin": None}
        result = {"places": deepcopy(PLACES), "stadiumCode": "JAMSIL", "origin": self.writer["origin"]}
        with patch.object(editing, "interpret", return_value=plan("new", [])) as interpret, \
                patch.object(agent, "_answer", return_value=result) as generate:
            actual = agent.answer("초밥 먹고 산책하다 구장 갈 코스 짜줘", hint_stadium="JAMSIL",
                                  current_course=validated, course_memory=saved)
        self.assertEqual(interpret.call_args.args[1]["places"], [])
        self.assertEqual(generate.call_args.args[3], self.writer["origin"])
        self.assertFalse(generate.call_args.kwargs["origin_cleared"])
        self.assertEqual(actual["writerState"]["origin"], self.writer["origin"])
        self.assertNotIn("undo", actual["courseMemory"])  # 새 코스 이전의 대화/상태를 되살리지 않는다.

    def test_new_map_origin_wins_over_restored_writer_for_older_clients(self):
        for previous in (None, {"lat": 37.51, "lng": 127.08}):
            with self.subTest(previous=previous):
                saved = {**memory.empty(), "current": {**self.current,
                         "writerState": {**self.writer, "origin": previous}}, "origin": previous}
                with patch.object(editing, "interpret", return_value=plan("new", [])), \
                        patch.object(agent, "_answer", return_value={"places": []}) as generate:
                    agent.answer("새 코스", hint_stadium="JAMSIL", origin=self.writer["origin"], course_memory=saved)
                self.assertEqual(generate.call_args.args[3], self.writer["origin"])
                self.assertFalse(generate.call_args.kwargs["origin_cleared"])

    def test_cleared_map_does_not_restore_previous_visits_or_origin(self):
        current = {**self.current, "places": [], "writerState": {**self.writer, "origin": None}}
        saved = {**memory.empty(), "current": self.current, "origin": self.writer["origin"]}
        with patch.object(editing, "interpret", return_value=plan("new", [])) as interpret, \
                patch.object(agent, "_answer", return_value={"places": []}) as generate:
            agent.answer("새 코스", hint_stadium="JAMSIL", current_course=current, course_memory=saved)
        self.assertEqual(interpret.call_args.args[1]["places"], [])
        self.assertIsNone(generate.call_args.args[3])
        self.assertTrue(generate.call_args.kwargs["origin_cleared"])
        for bad in ({**CURRENT, "places": []}, {**current, "writerState": {"origin": None}}):
            with self.assertRaises(ValidationError):
                _validate_context({"currentCourse": bad})
        self.assertFalse(public_course({**current, "edit": True}))

    def test_explicitly_cleared_origin_does_not_reuse_saved_origin(self):
        self.current["writerState"].update(title="", origin=None)
        saved = {**memory.empty(), "current": self.current, "origin": self.writer["origin"]}
        with patch.object(editing, "interpret", return_value=plan("move", ["cafe"])), \
                patch.object(editing, "answer", return_value={"places": []}) as edit:
            agent.answer("카페를 앞으로", hint_stadium="JAMSIL", current_course=self.current, course_memory=saved)
        self.assertIsNone(edit.call_args.args[4])
        with patch.object(editing, "interpret", return_value=plan("new", [])), \
                patch.object(editing, "answer", return_value=None), patch.object(agent, "_answer", return_value={"places": []}) as generate:
            agent.answer("다시 짜줘", hint_stadium="JAMSIL", current_course=self.current, course_memory=saved)
        self.assertTrue(generate.call_args.kwargs["origin_cleared"])

    def test_metadata_validation_does_not_accept_bad_coordinates_or_completion(self):
        for writer in ({**self.writer, "completed": "true"}, {**self.writer, "origin": {"lat": True, "lng": 127}},
                       {**self.writer, "title": "x" * 81}, {**self.writer, "origin": {"lat": 91, "lng": 127}}):
            with self.subTest(writer=writer), self.assertRaises(ValidationError):
                _validate_context({"currentCourse": {**self.current, "writerState": writer}})

    def test_initial_title_and_named_origin_are_kept_without_model_calls(self):
        result = memory.attach({"places": PLACES, "stadiumCode": "JAMSIL", "coursePayload": {"title": "첫 추천 코스"},
                                "origin": self.writer["origin"]}, memory.empty())
        self.assertEqual(result["writerState"], {"title": "첫 추천 코스", "origin": self.writer["origin"], "completed": True})
        self.assertEqual(agent.valid_origin(self.writer["origin"]), self.writer["origin"])

    def test_legacy_edits_recover_title_from_same_course_history(self):
        first = {"places": PLACES, "stadiumCode": "JAMSIL", "coursePayload": {"title": "옛 코스 제목"}}
        second = {"places": PLACES, "stadiumCode": "JAMSIL", "edit": True}
        messages = []
        for i, course in enumerate((first, second)):
            messages.extend([HumanMessage("코스", id=f"h{i}"), ToolMessage("코스", name="plan_course", tool_call_id=f"t{i}",
                            artifact={"course": course, "course_memory": {"current": deepcopy(CURRENT), "origin": None}})])
        restored = memory.restore(messages, {"h0": {"status": "completed"}, "h1": {"status": "completed"}})
        self.assertEqual(restored["current"]["writerState"]["title"], "옛 코스 제목")
