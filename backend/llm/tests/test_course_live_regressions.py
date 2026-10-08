from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from llm.v1.rag.course import agent, editing, slots, transport


class BudgetCafeConditionTest(SimpleTestCase):
    def test_parking_amenity_does_not_change_default_walk_mode(self):
        question = "경기 관람 후 주차 가능하고 금연 객실이 있는 호텔로 가는 코스를 짜줘"
        self.assertIsNone(transport.mode_of(question))
        self.assertIsNone(slots.parse(question)["mode"])
        self.assertEqual(transport.mode_of("자가용으로 이동할 거야. " + question), "car")
        self.assertEqual(transport.mode_of("대중교통으로 이동할 거야. " + question), "transit")
        self.assertTrue(transport.is_taxi("택시로 갈래. " + question))

    def test_origin_filters_outside_radius_before_condition_selection(self):
        anchor = {"lat": 37.5, "lng": 127.0}
        origin = {"lat": 37.5, "lng": 126.972}
        def food(name, lng):
            return {"name": name, "lat": 37.5, "lng": lng, "category": "FOOD_OUT", "detail": "한식", "dist": .1}
        outside, inside = food("반경 밖 식당", 126.970), food("반경 안 식당", 126.974)
        filtered = []
        def select(items):
            filtered.extend(items)
            return items[:1]
        with patch.object(agent, "_kakao_step", return_value=[outside, inside]):
            steps = agent.build_origin_course(origin, anchor, [], agent.slots.parse("경기 전에 식사만"), False, candidate_filter=select)
        self.assertEqual(steps[0]["place"]["name"], "반경 안 식당")
        self.assertNotIn(outside, filtered)

    def test_hearty_meal_is_a_meal_type_preference_not_a_portion_guarantee(self):
        candidates = [{"category": "FOOD_OUT", "name": "분식집", "detail": "분식", "placeId": "1"},
                      {"category": "FOOD_OUT", "name": "삼계탕집", "detail": "음식점 > 한식", "placeId": "2"}]
        candidates = [{**p, "lat": 37.515, "lng": 127.079} for p in candidates]
        with patch.object(agent, "llm") as model:
            actual = editing.choose(candidates, {"query": "", "conditions": ["든든한 식사"]}, "든든한 식사")
        model.assert_not_called()
        self.assertEqual(actual["placeId"], "2")
        self.assertIn("양은 방문 전에 확인", actual["reason"])
        with patch.object(agent, "llm") as model:
            actual = editing.choose(candidates, {"query": "", "conditions": ["식사는 든든한 식사"]}, "식사는 든든한 식사")
        model.assert_not_called()
        self.assertEqual(actual["placeId"], "2")

    def test_budget_brand_preference_does_not_require_unavailable_menu_price(self):
        candidates = [{"category": "CAFE", "name": "스타벅스", "placeId": "1"},
                      {"category": "CAFE", "name": "메가MGC커피 테스트점", "placeId": "2"}]
        candidates = [{**p, "lat": 37.515, "lng": 127.079} for p in candidates]
        with patch.object(agent, "llm") as model:
            actual = editing.choose(candidates, {"query": "", "conditions": ["저가 카페"]}, "저가 카페")
        model.assert_not_called()
        self.assertEqual(actual["placeId"], "2")
        self.assertIn("가격은 확인", actual["reason"])
        with patch.object(agent, "llm") as model:
            actual = editing.choose(candidates, {"query": "", "conditions": ["저가 카페, 경기 전"]}, "저가 카페, 경기 전")
        model.assert_not_called()
        self.assertEqual(actual["placeId"], "2")

    def test_additional_facility_or_brand_requirement_still_needs_verification(self):
        candidate = {"category": "CAFE", "name": "메가MGC커피 테스트점", "placeId": "2", "lat": 37.515, "lng": 127.079}
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = editing.CandidateChoice(place_id="", evidence="미확인")
        with patch.object(agent, "llm", return_value=model), patch("llm.v1.rag.course.evidence_memory.enrich", return_value=[]) as verify:
            result = editing.choose([candidate], {"query": "", "conditions": ["저가 카페", "반려동물 허용"]}, "저가 카페; 반려동물 허용")
        self.assertIsNone(result)
        verify.assert_called_once()
        model.with_structured_output.assert_not_called()

    def test_unknown_brand_is_not_called_budget_without_evidence(self):
        with patch.object(agent, "llm") as model:
            result = editing.choose([{"category": "CAFE", "name": "동네카페", "placeId": "3", "lat": 37.515, "lng": 127.079}],
                                    {"query": "", "conditions": ["저가 카페"]}, "저가 카페")
        self.assertIsNone(result)
        model.assert_not_called()
