from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase

from llm.tests.test_course_editing import CURRENT, GAME, PLACES, plan
from llm.v1.rag.course import agent, editing, memory
from llm.v2.agent.course_output import public_course


class ActivityReplacementTests(SimpleTestCase):
    def setUp(self):
        self.searches = []
        self.choices = []
        self.unavailable = set()
        for name, value in (("stadium_anchor", PLACES[2]), ("load_schedule", ({}, 1)),
                            ("find_game", (GAME, False, []))):
            patcher = patch.object(agent, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for owner, name, side_effect in ((agent, "invoke_domain_tool", self.provider),
                                         (editing, "choose", self.choose)):
            patcher = patch.object(owner, name, side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)

    def provider(self, domain, tool, args):
        if tool == "get_directions":
            return {"legs": [{"status": "ok", "distance": 400, "seconds": 300}
                             for _ in args["points"][1:]]}
        self.assertEqual(tool, "search_places")
        self.searches.append(args)
        raw = [
            {"id": "walk:new", "place_name": "새 산책공원", "category_name": "여행 > 공원", "category_group_code": "AT4"},
            {"id": "cafe:new", "place_name": "공원카페", "category_name": "음식점 > 카페", "category_group_code": "CE7"},
            {"id": "food:new", "place_name": "새 식당", "category_name": "음식점 > 일식", "category_group_code": "FD6"},
        ]
        return {"places": [{**p, "x": "127.079", "y": "37.513", "road_address_name": "서울 송파구 백제고분로 10"}
                           for p in raw if p["category_group_code"] not in self.unavailable], "hasNextPage": False}

    def choose(self, options, operation, question):
        self.choices.append(deepcopy(operation))
        return {**options[0], "reason": "확인한 후보"} if options else None

    def edit(self, operation, current=CURRENT, saved=None):
        return editing.answer("요청한 활동으로 바꿔줘", [], current, "JAMSIL", None,
                              saved, parsed=operation)

    def test_cafe_to_walk_uses_park_search_and_preserves_other_visits_and_game(self):
        before = deepcopy(CURRENT)
        result = self.edit(plan("replace", ["cafe"], category="WALK", query="공원"))
        self.assertEqual([p["placeId"] for p in result["places"]], ["1", "walk:new", "3", "4"])
        self.assertEqual(result["places"][1]["category"], "WALK")
        self.assertEqual(result["places"][1]["visitId"], "cafe")
        self.assertEqual(result["places"][1]["phase"], "BEFORE")
        self.assertEqual(result["game"], CURRENT["game"])
        self.assertEqual(CURRENT, before)
        self.assertTrue(all(p.get("time") for p in result["places"]))
        self.assertEqual(self.searches[0]["query"], "공원")
        self.assertNotIn("category", self.searches[0])
        self.assertEqual(public_course(result)["places"][1]["category"], "WALK")

    def test_other_activity_replacements_search_the_new_category(self):
        for target, category, expected, group in (("food", "CAFE", "cafe:new", "CE7"),
                                                   ("park", "FOOD", "food:new", "FD6")):
            with self.subTest(category=category):
                result = self.edit(plan("replace", [target], category=category))
                changed = next(p for p in result["places"] if p["visitId"] == target)
                self.assertEqual(changed["placeId"], expected)
                self.assertEqual(changed["category"], category)
                self.assertEqual(self.searches[-1]["category"], group)

    def test_unspecified_category_preserves_food_instead_of_defaulting_to_cafe(self):
        for fields in ({}, {"category": ""}):
            result = self.edit(plan("replace", ["food"], **fields))
            self.assertEqual(result["places"][0]["placeId"], "food:new")
            self.assertEqual(result["places"][0]["category"], "FOOD")
        parsed = editing.EditPlan.model_validate(plan("replace", ["food"], preferences=[], forget_conditions=[]))
        self.assertEqual(parsed.category, "")

    def test_changed_activity_uses_its_own_preferences_without_erasing_cafe_preferences(self):
        saved = {**memory.empty(), "conditions": [{"scope": "CAFE", "text": "카페는 프랜차이즈"},
                  {"scope": "WALK", "text": "산책은 공원"}, {"scope": "ALL", "text": "주차 가능"}]}
        before = deepcopy(saved)
        result = self.edit(plan("replace", ["cafe"], category="WALK", query="공원"), saved=saved)
        self.assertEqual(self.choices[0]["conditions"], ["산책은 공원", "주차 가능"])
        self.assertEqual(result["courseMemory"]["conditions"], saved["conditions"])
        self.assertEqual(saved, before)

    def test_replace_before_game_and_add_after_game_are_atomic_and_undo_together(self):
        current = {**deepcopy(CURRENT), "places": deepcopy(PLACES[:3])}
        before = deepcopy(current)
        operation = plan("batch", [], actions=[
            plan("replace", ["cafe"], category="WALK", query="공원"),
            plan("add", [], category="CAFE", reference="game", position="after"),
        ])
        with patch.object(editing, "rebuild", wraps=editing.rebuild) as rebuild:
            result = self.edit(operation, current)
        rebuild.assert_called_once()
        self.assertEqual(rebuild.call_args.kwargs["availability_plans"]["cafe"]["category"], "WALK")
        self.assertEqual([p["category"] for p in result["places"]], ["FOOD", "WALK", "STADIUM", "CAFE"])
        self.assertEqual([p["phase"] for p in result["places"]], ["BEFORE", "BEFORE", "GAME", "AFTER"])
        self.assertEqual(result["places"][0]["placeId"], current["places"][0]["placeId"])
        self.assertEqual(result["game"], current["game"])
        self.assertTrue(all(p.get("time") for p in public_course(result)["places"]))
        self.assertEqual(current, before)
        saved = result["courseMemory"]
        restored = self.edit(plan("undo", []), saved["current"], saved)
        self.assertEqual([p["placeId"] for p in restored["places"]], [p["placeId"] for p in before["places"]])

    def test_failed_second_action_does_not_leave_a_half_changed_course(self):
        self.unavailable.add("CE7")
        current = deepcopy(CURRENT)
        saved = memory.empty()
        result = self.edit(plan("batch", [], actions=[
            plan("replace", ["cafe"], category="WALK", query="공원"),
            plan("add", [], category="CAFE", reference="game", position="after"),
        ]), current, saved)
        self.assertEqual(result["places"], [])
        self.assertEqual(result["courseMemory"], saved)
        self.assertIn("전체 변경을 취소", result["answer"])
        self.assertEqual(current, CURRENT)

    def test_activity_change_cannot_bypass_stadium_lock_or_completed_guards(self):
        locked = {**memory.empty(), "locked": [PLACES[1]]}
        completed = deepcopy(CURRENT)
        completed["places"][1]["completed"] = True
        for target, current, saved in (("game", CURRENT, None), ("cafe", CURRENT, locked), ("cafe", completed, None)):
            result = self.edit(plan("replace", [target], category="WALK"), current, saved)
            self.assertEqual(result["places"], [])
        self.assertEqual(self.searches, [])
