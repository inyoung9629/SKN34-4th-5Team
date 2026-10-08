from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from llm.serializer.message import _validate_context
from llm.tests.test_course_editing import CURRENT, PLACES, GAME, plan
from llm.v1.rag.course import agent, editing, memory
from llm.v1.rag.course.selection import selected_relative_request
from llm.v2.middleware.jev_guidelines import selected_context_text, _context_text


class CourseSelectionTests(SimpleTestCase):
    def setUp(self):
        self.calls = []
        for owner, name, value in ((agent, "stadium_anchor", PLACES[2]),
                                   (agent, "load_schedule", ({}, 1)),
                                   (agent, "find_game", (GAME, False, []))):
            patcher = patch.object(owner, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(editing, "candidates", side_effect=self.candidates)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(editing, "choose", side_effect=lambda options, *args: options[0] if options else None)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(agent, "invoke_domain_tool", return_value={"legs": []})
        patcher.start()
        self.addCleanup(patcher.stop)

    def candidates(self, target, anchor, action, fixed):
        self.calls.append(deepcopy(action))
        return [{"placeId": f"new:{len(self.calls)}", "name": "추가한 장소", "lat": 37.512,
                 "lng": 127.075, "category": action["category"]}]

    def edit(self, current, action, saved=None):
        return editing.answer("여기 앞뒤에 추가해줘", [], current, "JAMSIL", None,
                              saved, parsed=action)

    def test_before_and_after_every_manual_category_without_chat_history(self):
        for category in ("FOOD", "CAFE", "WALK", "SPOT", "INDOOR", "STAY", "CONVENIENCE", "STADIUM"):
            for position in ("before", "after"):
                with self.subTest(category=category, position=position):
                    selected = {**PLACES[0], "category": category}
                    current = {**CURRENT, "places": [selected], "selectedPlace": selected}
                    before = deepcopy(current)
                    result = self.edit(current, plan("add", [], category="CAFE", reference=selected["visitId"], position=position))
                    self.assertEqual(len(result["places"]), 2)
                    self.assertEqual(result["places"][position == "before"]["placeId"], selected["placeId"])
                    self.assertEqual(current, before)

    def test_preview_anchor_is_added_once_only_when_referenced_and_all_changes_are_atomic(self):
        selected = {**PLACES[3], "visitId": "selection:park"}
        current = {**CURRENT, "places": deepcopy(PLACES[:3]), "selectedPlace": selected}
        before = deepcopy(current)
        action = plan("batch", [], actions=[
            plan("add", [], category="CAFE", reference=selected["visitId"], position="before"),
            plan("add", [], category="CONVENIENCE", reference=selected["visitId"], position="after"),
        ])
        result = self.edit(current, action)
        self.assertEqual([p["placeId"] for p in result["places"][:3]], [p["placeId"] for p in PLACES[:3]])
        self.assertEqual([p["category"] for p in result["places"][3:]], ["CAFE", "WALK", "CONVENIENCE"])
        self.assertNotIn("selectedPlace", result["courseMemory"]["current"])
        self.assertEqual(current, before)
        with patch.object(editing, "choose", side_effect=[self.candidates({}, {}, {"category": "CAFE"}, [])[0], None]):
            failed = self.edit(current, action, memory.empty())
        self.assertFalse(failed["places"])
        self.assertEqual(failed["courseMemory"], memory.empty())
        unrelated = self.edit(current, plan("remove", ["cafe"]))
        self.assertEqual([p["visitId"] for p in unrelated["places"]], ["food", "game"])
        self.assertEqual(current, before)

    def test_preview_can_start_empty_manual_course_but_does_not_override_new_generation(self):
        current = {"places": [], "selectedPlace": PLACES[0], "stadiumCode": "JAMSIL", "travelMode": "walk", "legModes": {},
                   "writerState": {"title": "직접 고른 코스", "origin": None, "completed": False}}
        current = _validate_context({"currentCourse": current})["currentCourse"]
        result = self.edit(current, plan("add", [], category="WALK", reference="food", position="after"))
        self.assertEqual([p["category"] for p in result["places"]], ["FOOD", "WALK"])
        self.assertIsNone(self.edit(current, plan("new", [])))

    def test_multiple_visits_on_both_sides_keep_requested_order_and_other_visits(self):
        for preview in (False, True):
            with self.subTest(preview=preview):
                selected = ({**PLACES[1], "visitId": "selection:cafe", "placeId": "preview"}
                            if preview else PLACES[1])
                current = {**CURRENT, "places": deepcopy(PLACES), "selectedPlace": selected}
                before = deepcopy(current)
                actions = [plan("add", [], category=category, reference=selected["visitId"], position=position)
                           for category, position in (("FOOD", "before"), ("WALK", "after"),
                                                      ("SPOT", "before"), ("CONVENIENCE", "after"))]
                result = self.edit(current, plan("batch", [], actions=actions))
                places = result["places"]
                index = next(i for i, p in enumerate(places) if p["visitId"] == selected["visitId"])
                self.assertEqual([p["category"] for p in places[index - 2:index + 3]],
                                 ["FOOD", "SPOT", "CAFE", "WALK", "CONVENIENCE"])
                self.assertEqual(sum(p["visitId"] == selected["visitId"] for p in places), 1)
                self.assertEqual([p["visitId"] for p in places if p["visitId"] in {s["visitId"] for s in PLACES}],
                                 [s["visitId"] for s in PLACES])
                self.assertEqual(current, before)

    def test_selected_relative_request_preserves_preview_even_with_incoming_new_classification(self):
        current = {**CURRENT, "places": [], "selectedPlace": PLACES[1],
                   "writerState": {"title": "직접 고른 코스", "origin": None, "completed": False}}
        question = "가기 전에 식사하고 다녀온 뒤에는 산책하는 코스 짜줘"
        action = plan("batch", [], actions=[
            plan("add", [], category="FOOD", reference="cafe", position="before"),
            plan("add", [], category="WALK", reference="cafe", position="after"),
        ])
        with patch.object(editing, "interpret", return_value=action) as interpret, patch.object(agent, "_answer") as generate:
            result = agent.answer(question, hint_stadium="JAMSIL", current_course=current, course_request="NEW")
        self.assertEqual(interpret.call_args.args[1]["selectedPlace"], PLACES[1])
        self.assertEqual([p["category"] for p in result["places"]], ["FOOD", "CAFE", "WALK"])
        self.assertNotIn("courseHistoryReset", result)
        generate.assert_not_called()
        with patch.object(editing, "interpret", return_value=plan("new", [])), patch.object(agent, "_answer") as generate:
            failed = agent.answer(question, hint_stadium="JAMSIL", current_course=current, course_request="NEW")
        self.assertFalse(failed["places"])
        self.assertIn("선택한 장소", failed["answer"])
        generate.assert_not_called()

    def test_relative_routing_covers_korean_expressions_without_capturing_unrelated_new_course(self):
        current = {**CURRENT, "selectedPlace": PLACES[1]}
        for question in ("가기 전에 식사 코스 짜줘", "여기 전에 식당 추가해줘", "여기 후에 산책",
                         "먹고 난 다음 카페 추천해줘", "마시고 나서 공원 들러줘", "다녀온 뒤에 편의점 넣어줘",
                         "방문하기 이전에 밥 먹고 방문한 이후에 산책", "여기 앞뒤에 일정 추가해줘",
                         "방문 후 산책 코스", "방문 전 식사 추가", "들른 다음 편의점 추가해줘"):
            with self.subTest(question=question):
                self.assertTrue(selected_relative_request(question, current))
                self.assertFalse(selected_relative_request(question, CURRENT))
        for question in ("오전에 카페 코스 짜줘", "오후에 카페 코스 짜줘", "경기 전에 카페 코스 짜줘",
                         "선택한 장소는 무시하고 처음부터 코스 짜줘", "여기 주소 알려줘"):
            with self.subTest(question=question):
                self.assertFalse(selected_relative_request(question, current))

    def test_selection_boundary_is_validated_and_existing_visit_is_authoritative(self):
        current = {**CURRENT, "selectedPlace": {**PLACES[1], "name": "오래된 이름", "category": "FOOD"}}
        value = _validate_context({"currentCourse": current})["currentCourse"]
        self.assertEqual(value["selectedPlace"], PLACES[1])
        self.assertIn("기존 카페", selected_context_text({"currentCourse": value}))
        self.assertIn("day_plan/EDIT", _context_text({"currentCourse": value}))
        for selected in (None, {}, {**PLACES[1], "lat": float("nan")}, {**PLACES[1], "visitId": ""}):
            with self.assertRaises(ValidationError):
                _validate_context({"currentCourse": {**CURRENT, "selectedPlace": selected}})
