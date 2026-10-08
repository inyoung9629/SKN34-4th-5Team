from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase

from llm.tests.test_course_editing import CURRENT, GAME, PLACES, REPLACEMENT, plan
from llm.v1.rag.course import agent, editing, edit_context, memory, progress
from llm.v2.agent.course_output import public_course


class RemainingCourseTest(SimpleTestCase):
    def setUp(self):
        for module, name, kwargs in (
            (editing, "route_legs", {"side_effect": lambda points, *args: [{"minutes": 5, "meters": 400, "by": "walk"}] * (len(points) - 1)}),
            (agent, "load_schedule", {"return_value": ({}, 1)}),
            (agent, "find_game", {"return_value": (GAME, False, [])}),
            (agent, "stadium_anchor", {"return_value": PLACES[2]}),
        ):
            patcher = patch.object(module, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.initial = self.edit(plan("preferences", []))
        self.current = self.initial["courseMemory"]["current"]

    def edit(self, operation, current=CURRENT, saved=None, origin=None):
        return editing.answer("남은 일정 수정", [], deepcopy(current), "JAMSIL", origin, saved, operation)

    def following(self, operation, result):
        return self.edit(operation, result["courseMemory"]["current"], result["courseMemory"])

    def test_departure_delay_accumulates_without_moving_game_or_post_game(self):
        changed = self.edit(plan("delay", [], delay_minutes=30), self.current)
        again = self.following(plan("delay", [], delay_minutes=15), changed)
        self.assertEqual(again["game"], self.current["game"])
        for i in (0, 1):
            self.assertEqual(progress.minute(again["places"][i]["time"]), progress.minute(self.current["places"][i]["time"]) + 45)
        self.assertEqual(again["places"][3]["time"], self.current["places"][3]["time"])
        self.assertEqual([p["placeId"] for p in again["places"]], ["1", "2", "3", "4"])

    def test_late_arrival_keeps_places_and_warns_first_infeasible_visit(self):
        result = self.edit(plan("delay", [], delay_minutes=75), self.current)
        self.assertIn("1번째 기존 식당", result["timeWarning"])
        self.assertEqual(len(result["places"]), 4)

    def test_origin_departure_clock_includes_inbound_leg(self):
        result = self.edit(plan("delay", [], at_time="16:00"), self.current, origin={"lat": 37.5, "lng": 127.08})
        self.assertEqual(result["places"][0]["time"], "16:05")

    def test_game_overrun_changes_only_post_game_then_survives_other_edits(self):
        result = self.edit(plan("game_delay", [], delay_minutes=45), self.current)
        self.assertEqual([p["time"] for p in result["places"][:3]], [p["time"] for p in self.current["places"][:3]])
        self.assertEqual(result["places"][3]["time"], "22:30")
        later = self.following(plan("duration", [], durations=[{"visit_id": "park", "minutes": 20}]), result)
        self.assertEqual(later["places"][3]["time"], "22:30")
        self.assertEqual(later["places"][3]["until"], "22:50")

    def test_unknown_delay_or_invalid_clock_never_guesses(self):
        for operation in (plan("delay", []), plan("game_delay", []), plan("game_delay", [], at_time="09:00")):
            result = self.edit(operation, self.current)
            self.assertEqual(result["places"], [])
            self.assertIn("기존 코스는 그대로", result["answer"])

    def test_next_day_finish_is_not_wrapped_to_previous_day(self):
        result = self.edit(plan("game_delay", [], at_time="익일 00:20"), self.current)
        self.assertEqual(result["places"][3]["time"], "익일 00:25")
        following = self.following(plan("game_delay", [], delay_minutes=10), result)
        self.assertEqual(following["places"][3]["time"], "익일 00:35")

    def test_completed_food_frozen_during_delay_and_cafe_replacement(self):
        done = self.edit(plan("complete", ["food"]), self.current)
        fixed = deepcopy(done["places"][0])
        self.assertTrue(fixed["completed"])
        self.assertIn("기존 시간표", done["answer"])
        delayed = self.following(plan("delay", [], delay_minutes=30), done)
        self.assertEqual(delayed["places"][0], fixed)
        self.assertEqual(progress.minute(delayed["places"][1]["time"]), progress.minute(done["places"][1]["time"]) + 30)
        with patch.object(editing, "candidates", return_value=[REPLACEMENT]), patch.object(editing, "choose", return_value=REPLACEMENT):
            replaced = self.following(plan("replace", ["cafe"]), delayed)
        self.assertEqual(replaced["places"][0], fixed)
        self.assertEqual(replaced["places"][1]["time"], delayed["places"][1]["time"])

    def test_complete_after_delay_uses_changed_time_and_explicit_finish(self):
        delayed = self.edit(plan("delay", [], delay_minutes=20), self.current)
        completed = self.following(plan("complete", ["food"], at_time="17:10"), delayed)
        self.assertEqual(completed["places"][0]["time"], delayed["places"][0]["time"])
        self.assertEqual(completed["places"][0]["until"], "17:10")
        self.assertEqual(completed["places"][1]["time"], "17:15")

    def test_done_prefix_cannot_be_removed_reordered_or_changed(self):
        done = self.edit(plan("complete", ["food"]), self.current)
        for operation in (plan("remove", ["food"]), plan("swap", ["food", "cafe"]),
                          plan("duration", [], durations=[{"visit_id": "food", "minutes": 10}]),
                          plan("move", ["park"], position="first")):
            result = self.following(operation, done)
            self.assertEqual(result["places"], [])
            self.assertIn("완료", result["answer"])
        # Never invent completion for earlier places when user only mentions a later stop.
        out_of_order = self.edit(plan("complete", ["cafe"]), self.current)
        self.assertEqual(out_of_order["places"], [])
        self.assertIn("앞선", out_of_order["answer"])

    def test_game_completion_and_all_completed_are_supported(self):
        result = self.edit(plan("complete", ["food", "cafe", "game"], at_time="22:00"), self.current)
        self.assertEqual(result["places"][3]["time"], "22:05")
        self.assertEqual(result["travel"]["summary"], "남은 이동 약 0.4km · 5분")
        completed = self.following(plan("complete", ["park"]), result)
        self.assertTrue(all(p["completed"] for p in completed["places"]))
        self.assertEqual(completed["travel"]["summary"], "남은 이동 약 0.0km · 0분")
        self.assertIn("남은 일정이 없어요", self.following(plan("delay", [], delay_minutes=30), completed)["answer"])

    def test_indoor_replacement_keeps_others_and_completed_visits(self):
        done = self.edit(plan("complete", ["food"]), self.current)
        indoor = {**REPLACEMENT, "name": "잠실 볼링장", "category": "INDOOR"}
        with patch.object(editing, "candidates", return_value=[indoor]) as candidates, patch.object(editing, "choose", return_value=indoor):
            result = self.following(plan("indoor", ["park"], category="INDOOR", query="볼링장"), done)
        self.assertEqual(candidates.call_args.args[0]["category"], "INDOOR")
        self.assertEqual([p["name"] for p in result["places"][:3]], [p["name"] for p in done["places"][:3]])
        self.assertEqual(result["places"][0], done["places"][0])
        self.assertEqual(result["places"][3]["category"], "INDOOR")
        self.assertEqual(result["places"][3]["visitId"], "park")

    def test_no_indoor_candidate_keeps_course_and_memory_atomically(self):
        with patch.object(editing, "candidates", return_value=[]):
            result = self.following(plan("indoor", ["park"], category="INDOOR"), self.initial)
        self.assertEqual(result["places"], [])
        self.assertEqual(result["courseMemory"], self.initial["courseMemory"])
        self.assertIn("2.5km", result["answer"])

    def test_public_history_map_context_and_undo_keep_progress(self):
        done = self.edit(plan("complete", ["food"]), self.current)
        public = public_course(done)
        raw = {**public, "travelMode": "walk", "legModes": {}, "places": [{**p, "label": str(i)} for i, p in enumerate(public["places"])]}
        restored = edit_context.validate(raw)
        self.assertEqual(restored["progress"], done["progress"])
        self.assertTrue(restored["places"][0]["completed"])
        self.assertEqual(memory.fingerprint(restored), memory.fingerprint(done["courseMemory"]["current"]))
        undone = self.edit(plan("undo", []), restored, done["courseMemory"])
        self.assertNotIn("completed", undone["places"][0])
        self.assertNotIn("progress", undone)
        for invalid in ({"startMinute": True}, {"startMinute": -1}, {"gameEndMinute": 2880}, {"startMinute": 1.5}):
            with self.assertRaises(ValueError):
                edit_context.validate({**raw, "progress": invalid})

    def test_selected_game_is_kept_when_schedule_returns_another_game(self):
        with patch.object(agent, "find_game", return_value=({**GAME, "date": "2026-10-09"}, False, [])):
            result = self.edit(plan("game_delay", [], delay_minutes=30), self.current)
        self.assertEqual(result["game"], self.current["game"])
