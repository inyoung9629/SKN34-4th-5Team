from copy import deepcopy
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from langchain_core.messages import HumanMessage
from llm.v1.rag.course import recommendation_policy as policy, place_quality as quality, catalog_conditions as catalog, editing, agent, visit_requests, memory, timeline
from llm.tests.test_course_place_warmer import PLACE, proof
from llm.v2.middleware.course_answer import CourseAnswerMiddleware


@override_settings(COURSE_RECOMMENDATION_MODE="balanced")
class RecommendationPolicyTests(SimpleTestCase):
    def test_optional_interior_research_is_deferred_without_approving_unknown_places(self):
        with policy.request("경기 전 카페, 경기 후 술집 코스"), quality.request_scope("GWANGJU"), \
                patch.object(quality, "resolve", return_value=None), \
                patch("travel.course_place_warmer.remember"), \
                patch("llm.v1.rag.course.quality_research.discover") as discover:
            result = quality.verify_candidates([PLACE], "FOOD")
            self.assertTrue(result[0]["qualityUnverified"])
            self.assertEqual(quality._RESEARCH.get()["approved"], set())
            self.assertIn("아직 확인되지", quality.missing_notice())
        discover.assert_not_called()
        self.assertFalse(policy.balanced())

    def test_known_rejection_and_entertainment_never_return_in_balanced_mode(self):
        bad = proof(status="rejected", interior={**proof()["interior"], "status": "poor"})
        def resolve(place, *, include_rejected=False):
            return bad if include_rejected else None
        with policy.request("술집 코스"), quality.request_scope("GWANGJU"), patch.object(quality, "resolve", side_effect=resolve):
            self.assertIsNone(quality.filter_place(PLACE, "FOOD"))
        with policy.request("술집 코스"), quality.request_scope("GWANGJU"), patch.object(quality, "resolve", return_value=None):
            self.assertIsNone(quality.filter_place({**PLACE, "detail": "술집 > 룸싸롱"}, "FOOD"))
            self.assertIsNone(quality.filter_place({**PLACE, "placeId": "18774818"}, "FOOD"))

    def test_explicit_research_preserves_strict_gate_and_context_is_reset(self):
        for question in ("평점 있는 술집", "깔끔한 카페", "실제 메뉴 근거를 확인해줘", "주차 가능한 식당"):
            with self.subTest(question=question), policy.request(question), quality.request_scope("GWANGJU"), patch.object(quality, "resolve", return_value=None):
                self.assertFalse(policy.balanced())
                self.assertIsNone(quality.filter_place(PLACE, "FOOD"))
        with policy.request("카페 코스"):
            self.assertTrue(policy.balanced())
        self.assertFalse(policy.balanced())

    def test_remote_cached_place_has_no_priority_over_local_unreviewed_place(self):
        far = {**PLACE, "placeId": "9002", "lat": PLACE["lat"] + .014}
        def resolve(place, **kwargs):
            return proof(**far) if place["placeId"] == "9002" else None
        with policy.request("식당 코스"), quality.request_scope("GWANGJU"), patch.object(quality, "resolve", side_effect=resolve), \
                patch("travel.course_place_warmer.remember"):
            rows = quality.verify_candidates([far, PLACE], "FOOD", center=PLACE)
        self.assertEqual([p["placeId"] for p in rows], ["9001", "9002"])

    def test_specialty_category_is_a_recommendation_hint_not_verified_menu_fact(self):
        katsudon = {**PLACE, "name": "돈카츠 전문점", "category": "FOOD", "detail": "음식점 > 일식 > 돈까스"}
        with policy.request("돈카츠 식당"):
            rows = catalog.choose([katsudon], {"conditions": ["돈카츠"]}, "돈카츠")
        self.assertEqual(rows[0]["catalogMenuHint"], ["돈까스"])
        self.assertNotIn("conditionChecks", rows[0])
        self.assertNotIn("근거 확인", rows[0]["reason"])

    def test_menu_and_brand_conditions_are_not_relaxed(self):
        chinese = {**PLACE, "category": "FOOD"}
        cafe = {**PLACE, "name": "다른커피", "detail": "음식점 > 카페 > 커피전문점", "category": "CAFE"}
        with policy.request("자장면 식당과 메가커피"):
            self.assertIsNone(catalog.choose([chinese], {"conditions": ["자장면"]}, "자장면"))
            self.assertEqual(catalog.choose([chinese], {"conditions": ["자장면"]}, "자장면", verified=True), [])
            self.assertEqual(catalog.choose([cafe], {"conditions": ["메가커피"]}, "메가커피"), [])
        # The full edit request must still guard against a lossy plan summary.
        with policy.request("메가커피로 바꿔줘"):
            self.assertIsNone(catalog.choose([cafe], {"conditions": ["카페"]}, "메가커피로 바꿔줘"))

    def test_fast_edit_cannot_hide_a_menu_brand_or_modifier_missing_from_the_plan(self):
        self.assertTrue(catalog.simple_edit_matches("식당이 좀 별로다. 돈카츠 가게로 수정해줘", ["돈까스"]))
        self.assertTrue(catalog.simple_edit_matches("경기 전 자장면 식당만 돈카츠 가게로 수정해줘.", ["돈까스"]))
        self.assertTrue(catalog.simple_edit_matches("카페는 메가커피로 바꿔줘", ["메가MGC커피"]))
        for question, terms in (("돈카츠 가게로 수정해줘", ["식당"]),
                                ("스타벅스로 바꿔줘", ["카페"]),
                                ("메가커피로 바꿔줘", ["카페"]),
                                ("매콤한 자장면 식당으로 바꿔줘", ["짜장면"]),
                                ("돈카츠 말고 카페로 바꿔줘", ["카페"]),
                                ("메가커피로 바꿔줘. 주차도 가능해야 해", ["메가MGC커피"])):
            with self.subTest(question=question):
                self.assertFalse(catalog.simple_edit_matches(question, terms))

    def test_real_replacement_path_keeps_raw_question_separate_from_saved_conditions(self):
        from llm.tests.test_course_editing import CURRENT, PLACES, plan
        question = "식당이 좀 별로다. 돈카츠 가게로 수정해줘"
        kat = {"name": "카츠 전문점", "placeId": "901", "lat": 37.511, "lng": 127.077,
               "category": "FOOD", "detail": "음식점 > 일식 > 돈까스"}
        operation = plan("replace", ["food"], category="FOOD", query="돈카츠", conditions=["돈카츠"], course_only=True)
        with policy.request(question), patch.object(editing, "candidates", return_value=[kat]), \
                patch.object(agent, "stadium_anchor", return_value=PLACES[2]), \
                patch.object(agent, "requested_date", return_value=None), \
                patch.object(editing.availability, "check", return_value=None), \
                patch("llm.v1.rag.course.evidence_memory.enrich", return_value=[]) as enrich:
            result = editing.answer(question, [], deepcopy(CURRENT), "JAMSIL", None, parsed=operation, _staged=True)
        enrich.assert_not_called()
        self.assertEqual(result["places"][0]["placeId"], kat["placeId"])
        self.assertEqual([p["placeId"] for p in result["places"][1:]], [p["placeId"] for p in PLACES[1:]])

    def test_bar_keyword_results_do_not_relabel_restaurants_in_either_discovery_path(self):
        pizza = {**PLACE, "category": "FOOD", "name": "피자전문점", "detail": "음식점 > 양식 > 피자"}
        bar = {**pizza, "placeId": "9002", "name": "호프집", "detail": "음식점 > 술집 > 호프,요리주점"}
        request = {"id": "AFTER:0", "kind": "BAR", "phase": "AFTER", "internal": False,
                   "expression": "술집", "query": "술집", "conditions": []}
        with policy.request("술집 코스"), patch.object(editing, "candidates", return_value=[pizza, bar]):
            result = visit_requests.Selector([request], {}, "GWANGJU").search(PLACE, origin=PLACE)
        self.assertEqual([p["placeId"] for p in result], ["9002"])
        raw = [{"id": p["placeId"], "place_name": p["name"], "category_name": p["detail"],
                "y": p["lat"], "x": p["lng"]} for p in (pizza, bar)]
        with policy.request("술집 코스"), patch.object(agent.venue_policy, "search_candidates", return_value=raw):
            result = agent._kakao_step("BAR", PLACE, 800, PLACE, {"prefs": []})
        self.assertEqual([p["placeId"] for p in result], ["9002"])
        self.assertEqual(result[0]["detail"], bar["detail"])

    def test_public_map_edit_preserves_bar_subcategory_without_restoring_deleted_stops(self):
        bar = {**PLACE, "category": "FOOD_OUT", "detail": "음식점 > 술집 > 호프,요리주점"}
        saved = {"current": {"stadiumCode": "GWANGJU", "places": [bar, {**bar, "placeId": "removed"}]}}
        public = {k: v for k, v in bar.items() if k != "detail"}
        current = {"stadiumCode": "GWANGJU", "places": [{**public, "category": "FOOD", "label": "3"}]}
        restored = memory.restore_place_details(current, saved)
        self.assertEqual(len(restored["places"]), 1)
        self.assertEqual(restored["places"][0]["label"], "3")
        self.assertEqual(timeline.kind_of(restored["places"][0]), "BAR")
        self.assertEqual(timeline.stay_min(restored["places"][0], "AFTER"), 80)
        self.assertNotIn("detail", current["places"][0])
        for changed in ({"category": "CAFE"}, {"lat": bar["lat"] + .01}, {"placeId": "new"}):
            updated = {**current, "places": [{**current["places"][0], **changed}]}
            self.assertNotIn("detail", memory.restore_place_details(updated, saved)["places"][0])

    def test_edit_dispatch_requires_current_map_and_keeps_mixed_questions(self):
        middleware = CourseAnswerMiddleware()
        base = {"decision": {"allowed": True, "course_request": "EDIT", "capabilities": ["day_plan"]},
                "context": {"intent": "route", "stadium": "JAMSIL", "currentCourse": {"places": []}},
                "messages": [HumanMessage("카페 체류시간만 20분으로 수정해줘")]}
        self.assertEqual(middleware.before_model(base, None)["jump_to"], "tools")
        for extra in ("그리고 날씨도 알려줘", "이유도 설명해줘", "표로 작성해줘"):
            state = deepcopy(base)
            state["messages"][0].content += extra
            self.assertIsNone(middleware.before_model(state, None))
        state = deepcopy(base)
        state["context"].pop("currentCourse")
        self.assertIsNone(middleware.before_model(state, None))
