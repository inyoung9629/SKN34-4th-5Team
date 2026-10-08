"""야간 경기의 자동 야식을 막고, 명시한 경기 후 활동은 지도·저장용 코스까지 보존한다."""
import json
import re
from contextlib import ExitStack
from unittest.mock import patch

from django.test import SimpleTestCase

from llm.v1.rag.course import agent, slots


QUESTION = "경기 시작전에 든든한 거 좀 먹고 카페 가서 커피 좀 마시고 구장 갔다가 끝나고 산책 좀 하고 싶어. 루트 짜줘"
REWRITTEN_REQUEST = (
    "잠실야구장 기준 직관 코스. 경기 시작 전에 든든한 식사를 하고 카페에서 커피를 마신 뒤 구장에 가고, "
    "경기 끝난 후에는 산책만 하고 싶어요. 경기 후 야식·술집·추가 식사나 카페는 넣지 말아 주세요. 날짜는 지정하지 않았어요."
)


def place(key, category, name, detail=""):
    return {"key": key, "category": category, "name": name, "detail": detail,
            "lat": 35.169 if category == "STADIUM" else 35.174, "lng": 126.889, "dist": 0.1, "distance": 550,
            "placeId": key, "address": "광주", "placeUrl": "", "doc_id": key}


class RequestedAfterActivitiesTest(SimpleTestCase):
    def test_food_exclusions_are_not_positive_preferences(self):
        for text, excluded, included in (
            ("한식 말고 일식으로 추천해줘", "한식", "일식"),
            ("치킨은 싫어해. 피자로", "치킨", "피자"),
            ("한식과 중식은 제외하고 일식", "중식", "일식"),
        ):
            with self.subTest(text=text):
                parsed = slots.parse(text)
                self.assertIn(excluded, parsed["ban"])
                self.assertNotIn(excluded, parsed["prefs"])
                self.assertIn(included, parsed["prefs"])
        self.assertNotIn("한식", slots.parse("한식은 제외하지 마")['ban'])
        self.assertNotIn("일식", slots.parse("초밥은 빼고 돈까스 추천")['ban'])

    def test_natural_after_game_expressions_preserve_walk_phase(self):
        for marker in ("야구 본 다음", "경기 본 뒤", "직관 본 후", "경기 관람 후", "야구 보고 나서"):
            question = f"경기 시작 전에 밥 먹고 저가 카페에서 커피 마시고 {marker} 산책만 할래"
            with self.subTest(marker=marker):
                parsed = slots.parse(question)
                self.assertEqual(parsed["scope"], "both")
                self.assertEqual(parsed["itinerary"], {"BEFORE": ["FOOD", "CAFE"], "AFTER": ["WALK"]})
                self.assertEqual(parsed["extra_phases"]["walk"], "AFTER")
                self.assertEqual(parsed["after_kinds"], ["WALK"])

    def test_requested_sequence_controls_both_phases_without_default_extras(self):
        cases = [
            ("경기 전에 카페만", (["CAFE"], [])),
            ("경기 후 산책만", ([], ["WALK"])),
            ("경기 전에 카페 갔다가 밥 먹고 경기 후 산책", (["CAFE", "FOOD"], ["WALK"])),
            ("경기 전 커피, 경기 후 커피", (["CAFE"], ["CAFE"])),
            ("경기 전에 카페 두 곳 가고 경기 후 귀가", (["CAFE", "CAFE"], [])),
            ("경기 전 카페 갔다가 밥 먹고 다시 카페", (["CAFE", "FOOD", "CAFE"], [])),
            ("아침부터 여유롭게 경기 전에 카페만 갈래", (["CAFE"], [])),
            ("시간이 촉박하지만 경기 전 명소 구경하고 커피", (["SPOT", "CAFE"], [])),
            ("카페에서 디저트 먹고 구장 갈래", (["CAFE"], [])),
            ("경기 전후 카페에서 커피", (["CAFE"], ["CAFE"])),
            ("경기 전에 커피 한잔 마시고 경기 후 박물관 구경", (["CAFE"], ["INDOOR"])),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(agent.plan_steps(slots.parse(text), True), expected)

    def test_followup_inherits_only_user_activities_and_can_remove_or_replace(self):
        history = [{"role": "user", "content": QUESTION},
                   {"role": "assistant", "content": "야식과 술집과 카페도 들러요"}]
        for text, expected in [
            ("이번엔 롯데로 짜줘", (["FOOD", "CAFE"], ["WALK"])),
            ("다른 곳으로 다시 짜줘", (["FOOD", "CAFE"], ["WALK"])),
            ("카페는 빼줘", (["FOOD"], ["WALK"])),
            ("경기 후는 카페로 바꿔줘", (["FOOD", "CAFE"], ["CAFE"])),
            ("경기 전에 카페만 갈래", (["CAFE"], [])),
        ]:
            with self.subTest(text=text):
                self.assertEqual(agent.plan_steps(slots.parse(text, history), True), expected)

    def test_final_guard_removes_extras_restores_order_and_reports_missing(self):
        candidates = [place("food", "FOOD_OUT", "식당"), place("cafe", "CAFE", "카페"),
                      place("cafe2", "CAFE", "카페2"), place("park", "WALK", "산책공원"),
                      place("STADIUM", "STADIUM", "구장")]
        lookup = {p["key"]: p for p in candidates}
        wrong = [{"key": p["key"], "phase": "AFTER"} for p in candidates]
        for text, keys, missing in [
            (QUESTION, ["food", "cafe", "STADIUM", "park"], []),
            ("경기 전 카페 먹고 밥, 경기 후 커피", ["cafe", "food", "STADIUM", "cafe2"], []),
            ("경기 전 카페 두 곳만", ["cafe", "cafe2", "STADIUM"], []),
            ("경기 전 카페 세 곳만", ["cafe", "cafe2", "STADIUM"], ["경기 전 3번째 카페"]),
            ("경기 후 실내 놀거리만", ["STADIUM"], ["경기 후 1번째 실내 활동"]),
        ]:
            with self.subTest(text=text):
                course, absent = agent.enforce_itinerary(wrong, lookup, candidates, slots.parse(text), True)
                self.assertEqual([s["key"] for s in course], keys)
                self.assertEqual(absent, missing)

    def test_original_request_only_plans_walk_after_day_or_night_game(self):
        sl = slots.parse(QUESTION)
        self.assertEqual(sl["after_kinds"], ["WALK"])
        for evening in (False, True):
            self.assertEqual(agent.plan_steps(sl, evening), (["FOOD", "CAFE"], ["WALK"]))

    def test_before_and_after_without_particles_keep_the_full_requested_course(self):
        sl = slots.parse("경기 전 식사와 카페, 경기 후 산책")
        self.assertEqual(sl["scope"], "both")
        self.assertEqual(agent.plan_steps(sl, True), (["FOOD", "CAFE"], ["WALK"]))

    def test_explicit_meal_cafe_and_drinks_still_work(self):
        for text, expected in [("경기 전에 식사하고 경기 후 산책하고 야식 먹자", ["WALK", "FOOD"]),
                               ("경기 후 카페랑 산책", ["CAFE", "WALK"]),
                               ("경기 후 맥주 한잔", ["BAR"]),
                               ("경기 후 뒤풀이", ["BAR"]),
                               ("경기 후 산책, 저녁 경기 기준으로", ["WALK"]),
                               ("야식 포함해서 코스 짜줘", ["FOOD"])]:
            with self.subTest(text=text):
                self.assertEqual(slots.parse(text)["after_kinds"], expected)

    def test_negated_meals_and_return_home_do_not_add_food(self):
        for text, expected in [("경기 끝나고 야식 말고 산책만", ["WALK"]),
                               ("경기 후 야식은 빼고 카페만", ["CAFE"]),
                               ("경기 후 바로 집에 갈게", []),
                               ("경기 전에 식사하고 커피 마실래", [])]:
            with self.subTest(text=text):
                self.assertEqual(slots.parse(text)["after_kinds"], expected)

    def test_actual_model_exclusion_list_is_not_an_activity_request(self):
        self.assertEqual(slots.parse(REWRITTEN_REQUEST)["after_kinds"], ["WALK"])
        self.assertNotIn("술집", slots.parse(REWRITTEN_REQUEST)["prefs"])
        self.assertIn("카페", slots.parse(REWRITTEN_REQUEST)["prefs"])  # 경기 전 커피 요청은 유지한다.
        for text, expected in [
            ("경기 후 산책만. 식사, 카페, 술집은 추가하지 말아줘", ["WALK"]),
            ("경기 후 야식과 카페를 제외하고 공원에서 산책", ["WALK"]),
            ("경기 후 산책하고 식사나 카페는 추천하지 마", ["WALK"]),
            ("경기 후 식사와 카페는 넣고 술집은 넣지 말아줘", ["FOOD", "CAFE"]),
            ("경기 후 야식, 카페도 포함해줘", ["FOOD", "CAFE"]),
        ]:
            with self.subTest(text=text):
                self.assertEqual(slots.after_activities(text), expected)

    def test_unspecified_evening_does_not_default_to_food_or_drinks(self):
        sl = slots.parse("광주 직관 코스 추천")
        self.assertIsNone(sl["after_kinds"])
        self.assertNotIn("BAR", agent.plan_steps(sl, True)[1])
        self.assertNotIn("FOOD", agent.plan_steps(sl, True)[1])

    def test_fallback_preserves_meal_cafe_game_walk_without_night_meal(self):
        candidates = [place("food", "FOOD_OUT", "식당"), place("cafe", "CAFE", "카페"),
                      place("night", "FOOD_OUT", "야식집", "술집"), place("park", "WALK", "산책공원")]
        result = agent.fallback_course(candidates, True, slots.parse(QUESTION))
        self.assertEqual([s["key"] for s in result], ["food", "cafe", "STADIUM", "park"])

    def test_model_added_night_meal_is_absent_from_answer_map_and_saved_course(self):
        self.assert_only_requested_stops(QUESTION)

    def test_actual_negated_request_keeps_answer_map_and_saved_course_consistent(self):
        self.assert_only_requested_stops(REWRITTEN_REQUEST)

    def test_original_user_constraint_wins_even_if_model_adds_positive_meal_request(self):
        self.assert_only_requested_stops(REWRITTEN_REQUEST.replace("넣지 말아 주세요", "넣어 주세요"), ["WALK"])

    def test_changed_request_changes_all_outputs_not_just_the_description(self):
        for question, expected in [
            ("여자친구랑 초밥 먹고 산책 하다가 구장 갈건데 코스 짜줘", ["식당", "산책공원", "광주-KIA 챔피언스 필드"]),
            ("경기 전에 카페만 가고 경기 후 귀가", ["카페", "광주-KIA 챔피언스 필드"]),
            ("경기 전에 카페 가고 식사, 경기 후 산책", ["카페", "식당", "광주-KIA 챔피언스 필드", "산책공원"]),
            ("경기 후 산책만", ["광주-KIA 챔피언스 필드", "산책공원"]),
        ]:
            with self.subTest(question=question):
                self.assert_only_requested_stops(question, expected=expected)

    def test_missing_requested_cafe_is_reported_in_answer_and_saved_course(self):
        result = self.assert_only_requested_stops(
            QUESTION, omit_cafe=True, expected=["식당", "광주-KIA 챔피언스 필드", "산책공원"])
        for text in (result["answer"], result["coursePayload"]["content"]):
            self.assertIn("경기 전 2번째 카페는 이번 검색에서 메뉴·방문 조건을 확인하지 못해", text)
            self.assertIn("주변에 해당 매장이 없다는 뜻은 아니", text)

    def assert_only_requested_stops(self, question, requested_after=None, expected=None, omit_cafe=False):
        food, cafe = place("food", "FOOD_OUT", "식당"), place("cafe", "CAFE", "카페")
        night = place("night", "FOOD_OUT", "불필요한야식집", "치킨")
        park = place("park", "WALK", "산책공원", "공원")
        stadium = place("STADIUM", "STADIUM", "광주-KIA 챔피언스 필드")
        game = {"date": "2027-10-06", "time": "18:30", "home": "KIA", "away": "삼성", "status": "scheduled"}

        def generated(_question, _game, cands, *_args):
            by_name = {p["name"]: p["key"] for p in cands}
            return json.dumps({"intro": "불필요한야식집도 들러요", "course": [
                {"place_key": by_name.get(food["name"], "nonexistent-food"), "phase": "BEFORE"},
                {"place_key": by_name.get(cafe["name"], "nonexistent"), "phase": "BEFORE"},
                {"place_key": "STADIUM", "phase": "GAME"},
                {"place_key": by_name.get(night["name"], "nonexistent-night"), "phase": "AFTER"},
                {"place_key": by_name.get(park["name"], "nonexistent"), "phase": "AFTER"},
            ]}), 0

        mocks = {"load_schedule": ({}, 7), "find_game": (game, False, [game]),
                 "embed_many": ([0.1], [0.2]), "stadium_anchor": stadium,
                 "_live_candidates": ([food, night] if omit_cafe else [food, cafe, night], {}), "search_places": [],
                 "invoke_domain_tool": {}}
        with ExitStack() as stack:
            # These synthetic places exercise itinerary enforcement; their IDs
            # deliberately have no external ratings or production seed matches.
            stack.enter_context(patch.object(agent.place_quality, "active", return_value=False))
            for name, result in mocks.items():
                stack.enter_context(patch.object(agent, name, return_value=result))
            stack.enter_context(patch.object(agent.kakao, "nearby", return_value=[park]))
            stack.enter_context(patch.object(agent.nearby_agent, "narrow", side_effect=lambda places, *_: places))
            stack.enter_context(patch.object(agent.transport, "info", return_value={"mode": "walk", "label": "도보", "taxi": False, "lines": []}))
            stack.enter_context(patch.object(agent, "call_llm", side_effect=generated))
            result = agent.answer(question, hint_stadium="GWANGJU", requested_after=requested_after)
        expected = expected or [food["name"], cafe["name"], stadium["name"], park["name"]]
        self.assertEqual([p["name"] for p in result["places"]], expected)
        self.assertNotIn(night["name"], result["answer"])
        self.assertEqual([p["name"] for p in result["coursePayload"]["stops"]], expected)
        # 실제 사용자 답변에도 지도·저장 목록과 같은 방문만 같은 순서로 나타나야 한다.
        timeline_lines = [line for line in result["answer"].splitlines() if re.match(r"\s*(?:익일 )?\d{1,2}:\d{2}  ", line)]
        self.assertEqual(len(timeline_lines), len(expected))
        for line, name in zip(timeline_lines, expected):
            self.assertIn(name, line)
        self.assertNotIn(night["name"], result["coursePayload"]["content"])
        return result

    def test_guard_preserves_explicit_food_and_drops_unrequested_cafe(self):
        candidates = [place("night", "FOOD_OUT", "야식집"), place("cafe", "CAFE", "카페"),
                      place("park", "SPOT", "산책공원", "공원")]
        lookup = {p["key"]: p for p in candidates}
        steps = [{"key": p["key"], "phase": "AFTER"} for p in candidates]
        result = agent.enforce_after_activities(steps, lookup, candidates, slots.parse("경기 후 야식 먹고 산책"), True)
        self.assertEqual([s["key"] for s in result], ["night", "park"])

    def test_driver_does_not_get_a_bar_even_when_requested(self):
        sl = slots.parse("차를 몰고 가서 경기 후 술집과 산책")
        self.assertEqual(agent.plan_steps(sl, True)[1], ["WALK"])
