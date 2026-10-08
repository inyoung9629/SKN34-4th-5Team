"""A manual map anchor must not bypass the requested game's schedule lookup."""
import json
from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from llm.tests.test_course_editing import CURRENT, PLACES, plan
from llm.tests.test_course_entry_timing import stadium, tenant
from llm.v1.rag.course import agent, editing, memory
from llm.v1.rag.course.selection import connect_game_context, selected_relative_request


class SelectedGameTests(SimpleTestCase):
    def setUp(self):
        self.game = {"date": "2026-10-16", "time": "17:00", "home": "두산", "away": "롯데"}
        for owner, name, options in (
            (agent, "stadium_anchor", {"side_effect": stadium}),
            (agent, "load_schedule", {"return_value": ({}, 1)}),
            (agent, "find_game", {"side_effect": lambda *a, **kw: (self.game, False, [])}),
            (agent, "invoke_domain_tool", {"return_value": {"legs": []}}),
            (editing, "candidates", {"side_effect": self.candidates}),
            (editing, "choose", {"side_effect": lambda options, *a: options[0] if options else None}),
        ):
            patcher = patch.object(owner, name, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    def candidates(self, target, anchor, action, fixed):
        return [{"placeId": f"added:{len(fixed)}", "name": "추가 장소", "lat": anchor["lat"] + .001,
                 "lng": anchor["lng"], "category": action["category"]}]

    def current(self, category="FOOD", preview=False, code="JAMSIL"):
        location = stadium(code)
        selected = {**PLACES[0], "category": category, "lat": location["lat"] + .002, "lng": location["lng"]}
        return {"places": [] if preview else [selected], "selectedPlace": selected, "stadiumCode": code,
                "travelMode": "walk", "legModes": {},
                "writerState": {"title": "직접 고른 코스", "origin": None, "completed": False}}

    def run_request(self, current, question="10월 16일 여기 들르고 카페 갔다 구장 갈 코스 짜줘", interpret=None):
        def add_cafe(question, context, *args):
            game_id = next(p["visitId"] for p in context["places"] if p["category"] == "STADIUM")
            return plan("add", [], category="CAFE", reference=game_id, position="before")
        with patch.object(editing, "interpret", side_effect=interpret or add_cafe), patch.object(agent, "_answer") as generate:
            result = agent.answer(question, hint_stadium=current["stadiumCode"], current_course=current,
                                  course_memory=memory.empty(), course_request="NEW")
        generate.assert_not_called()
        return result

    def test_october_16_game_connects_all_manual_pin_categories_and_preserves_input(self):
        for category in ("FOOD", "CAFE", "WALK", "SPOT", "INDOOR", "STAY", "CONVENIENCE"):
            for preview in (False, True):
                with self.subTest(category=category, preview=preview):
                    current = self.current(category, preview)
                    before = deepcopy(current)
                    result = self.run_request(current)
                    self.assertEqual(result["game"], {"date": "2026-10-16", "time": "17:00"})
                    self.assertEqual([p["category"] for p in result["places"]], [category, "CAFE", "STADIUM"])
                    self.assertEqual(result["places"][-1]["time"], "16:40")
                    self.assertEqual(result["places"][0]["visitId"], "food")
                    self.assertEqual(agent.load_schedule.call_args.args[1], "2026-10-16")
                    self.assertEqual(current, before)
                    self.assertEqual(result["writerState"]["title"], "직접 고른 코스")
                    self.assertEqual(result["courseMemory"]["undo"]["before"], before)
                    self.assertNotIn("gameConnection", result["courseMemory"]["current"])
                    self.assertNotIn("courseHistoryReset", result)

    def test_gwangju_and_jamsil_internal_food_arrive_30_minutes_before_game(self):
        for code, game_time, food_time in (("JAMSIL", "17:00", "16:30"), ("GWANGJU", "14:00", "13:30")):
            with self.subTest(code=code):
                self.game = {**self.game, "time": game_time}
                food = tenant(code)
                def add_food(question, context, *args):
                    return plan("add", [], category="FOOD", reference=context["gameConnection"]["visitId"],
                                position="before", internal_venue_requests=[{"category": "FOOD", "expression": "구장 안에서 간식", "signature_default": True}])
                with patch.object(editing, "candidates", return_value=[food]):
                    result = self.run_request(self.current(code=code), "10월 16일 여기 들르고 구장 안에서 간식 먹을 코스 짜줘", add_food)
                self.assertEqual(result["game"]["time"], game_time)
                self.assertEqual(result["places"][-2]["placeId"], food["placeId"])
                self.assertEqual(result["places"][-2]["time"], food_time)
                self.assertEqual(result["places"][-1]["time"], game_time)

    def test_batch_handles_both_sides_of_selected_place_and_after_game(self):
        def actions(question, context, *args):
            return plan("batch", [], actions=[
                plan("add", [], category="CAFE", reference="food", position="before"),
                plan("add", [], category="WALK", reference="food", position="after"),
                plan("add", [], category="BAR", reference=context["gameConnection"]["visitId"], position="after"),
            ])
        result = self.run_request(self.current(preview=True), "16일 여기 전에는 카페, 뒤에는 산책, 경기 후에는 술집 코스 짜줘", actions)
        self.assertEqual([p["category"] for p in result["places"]], ["CAFE", "FOOD", "WALK", "STADIUM", "BAR"])
        self.assertEqual([p["phase"] for p in result["places"]], ["BEFORE", "BEFORE", "BEFORE", "GAME", "AFTER"])
        self.assertEqual(result["game"]["date"], "2026-10-16")

    def test_selected_place_can_be_moved_after_game_before_other_additions(self):
        def actions(question, context, *args):
            return plan("batch", [], actions=[
                plan("move", ["food"], reference=context["gameConnection"]["visitId"], position="after"),
                plan("add", [], category="CAFE", reference="food", position="after"),
            ])
        result = self.run_request(self.current(preview=True), "16일 경기 후 여기 갔다가 카페 가는 코스 짜줘", actions)
        self.assertEqual([p["category"] for p in result["places"]], ["STADIUM", "FOOD", "CAFE"])
        self.assertEqual([p["phase"] for p in result["places"]], ["GAME", "AFTER", "AFTER"])

    def test_game_only_request_rebuilds_date_and_undo_restores_manual_places(self):
        current = self.current()
        result = self.run_request(current, "10월 16일 코스로 맞춰줘", lambda *a: plan("date", []))
        self.assertEqual([p["category"] for p in result["places"]], ["FOOD", "STADIUM"])
        restored = editing.answer("방금 수정 취소", [], result["courseMemory"]["current"], "JAMSIL", None,
                                  result["courseMemory"], plan("undo", []))
        self.assertEqual([p["visitId"] for p in restored["places"]], ["food"])
        self.assertNotIn("game", restored)

    def test_missing_game_or_candidate_does_not_apply_prepared_stadium(self):
        for failure in ("game", "candidate", "interpret"):
            with self.subTest(failure=failure):
                current = self.current(preview=True)
                before = deepcopy(current)
                patcher = (patch.object(agent, "find_game", return_value=(None, False, [])) if failure == "game"
                           else patch.object(editing, "choose", return_value=None))
                with patcher:
                    result = self.run_request(current, interpret=(lambda *a: plan("new", [])) if failure == "interpret" else None)
                self.assertEqual(result["places"], [])
                self.assertNotIn("current", result["courseMemory"])
                self.assertIn("기존 코스는 그대로", result["answer"])
                self.assertEqual(current, before)

    def test_undated_game_request_looks_up_nearest_game_and_rejects_unknown_time(self):
        current = {**self.current(), "game": {"date": "2026-10-06", "time": "18:30"}}
        self.run_request(current, "여기 들르고 경기 보러 가는 코스 짜줘")
        self.assertIsNone(agent.load_schedule.call_args.args[1])
        with patch.object(agent, "find_game", return_value=(None, False, [])):
            result = self.run_request(current, "여기 들르고 경기 보러 가는 코스 짜줘")
        self.assertFalse(result["places"])

    def test_simple_relative_requests_exclusions_and_new_stadium_do_not_connect_game(self):
        current = self.current()
        for question in ("가기 전에 카페 추가해줘", "여기 가기 전에 구장 근처 카페 추가해줘", "경기는 빼고 여기 후에 카페 코스 짜줘",
                         "16일 구장에 안 가고 카페 코스 짜줘", "16일 경기 없이 코스 짜줘",
                         "처음부터 새로 16일 코스 짜줘", "16일 사직 구장 가는 코스 짜줘"):
            with self.subTest(question=question):
                prepared, connected = connect_game_context(question, current, "JAMSIL")
                self.assertFalse(connected)
                self.assertIs(prepared, current)
        self.assertFalse(selected_relative_request("16일 여기 들르고 사직 구장 가는 코스 짜줘", current))

    def test_existing_or_selected_stadium_is_not_duplicated(self):
        prepared, connected = connect_game_context("16일 코스 짜줘", CURRENT, "JAMSIL")
        self.assertFalse(connected)
        self.assertIs(prepared, CURRENT)
        current = {**self.current(preview=True), "selectedPlace": stadium()}
        result = self.run_request(current, "16일 코스 짜줘", lambda *a: plan("date", []))
        self.assertEqual([p["category"] for p in result["places"]], ["STADIUM"])

    def test_route_limit_and_missing_stadium_coordinates_leave_original_unchanged(self):
        full = {**self.current(), "places": [{**PLACES[0], "visitId": str(i)} for i in range(12)]}
        for current, anchor in ((full, stadium()), (self.current(), None)):
            with patch.object(agent, "stadium_anchor", return_value=anchor), patch.object(editing, "interpret") as interpret:
                result = agent.answer("16일 코스 짜줘", hint_stadium="JAMSIL", current_course=current)
            self.assertFalse(result["places"])
            interpret.assert_not_called()

    def test_interpreter_receives_real_stadium_reference_and_connection_instructions(self):
        prepared, _ = connect_game_context("16일 코스 짜줘", self.current(preview=True), "JAMSIL")
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = plan("date", [], preferences=[], forget_conditions=[])
        with patch.object(agent, "llm", return_value=model):
            editing.interpret("16일 코스 짜줘", prepared, [], memory.empty())
        messages = model.with_structured_output.return_value.invoke.call_args.args[0]
        data = json.loads(messages[1].content)
        game_id = data["current"]["gameConnection"]["visitId"]
        self.assertEqual(next(p["category"] for p in data["current"]["places"] if p["visitId"] == game_id), "STADIUM")
        self.assertIn("date로 구장 방문과 시간표를 연결", messages[0].content)
