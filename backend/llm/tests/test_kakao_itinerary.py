"""Live-candidate wiring with fake Kakao/directions; no LLM or network costs."""
import json
from dataclasses import replace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from travel.kakao_course_candidates import KakaoCourseCandidates
from travel.test_kakao_course_candidates import FAST, FakeKakao, crowded_docs, document, provider
from .test_itinerary_planner import GAME, NOW, STADIUM, request, route
from llm.v2.course.itinerary_planner import ItineraryPlanner
from llm.v2.course.itinerary_service import generate_itinerary, load_planning_data, render_itinerary


class KakaoItineraryTests(SimpleTestCase):
    def test_all_81_ranked_before_picking_match_beyond_original_45(self):
        docs = crowded_docs()
        docs[-1]["place_name"] = "특별식당"
        source, engine = provider(docs)
        directions = Mock(side_effect=route)
        spec = request(stops=[{"kind": "food", "required_keywords": ["특별식당"]}])
        result = ItineraryPlanner([], STADIUM, [], directions, candidate_source=source).plan(spec, GAME, now=NOW)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["placeId"], "81")
        self.assertEqual(result["search_trace"][0]["pool_count"], 81)
        self.assertEqual(result["search_trace"][0]["matching_count"], 1)
        self.assertTrue(result["search_trace"][0]["coverage_complete"])
        self.assertEqual(result["candidate_retrieval"]["candidate_count"], 81)
        self.assertEqual(directions.call_count, 1)
        self.assertEqual(result["stadium_arrival_at"], GAME.replace(minute=10).isoformat())
        self.assertFalse(result["origin_included"])
        self.assertGreater(len(engine.calls), 3)
        json.dumps(result)  # no live provider/raw pool object saved in the state

    def test_partial_pool_does_not_proceed_to_routes_or_no_match(self):
        source = KakaoCourseCandidates("JAMSIL", fetch=FakeKakao(crowded_docs()), frames=[],
                                       policy=replace(FAST, max_calls=1))
        directions = Mock(side_effect=route)
        spec = request()
        result = ItineraryPlanner([], STADIUM, [], directions, candidate_source=source).plan(spec, GAME, now=NOW)
        self.assertEqual(result["status"], "candidate_search_incomplete")
        self.assertEqual(result["stops"], [])
        self.assertEqual(result["search_trace"][0]["matching_count"], None)
        directions.assert_not_called()
        self.assertIn("없다는 뜻은 아니", render_itinerary(result, spec))

    def test_complete_empty_expands_all_radii_before_no_match(self):
        source, _ = provider()
        result = ItineraryPlanner([], STADIUM, [], Mock(), candidate_source=source).plan(request(), GAME, now=NOW)
        self.assertEqual(result["status"], "no_match")
        self.assertEqual([q["radius_m"] for q in result["search_trace"]], [100, 300, 500, 800, 1000, 1500])
        self.assertTrue(all(q["coverage_complete"] for q in result["search_trace"]))

    def test_second_stop_uses_previous_place_not_stadium_search_center(self):
        source, _ = provider([document(1, north=250), document(2, north=330, category="CE7", detail="음식점 > 카페")])
        spec = request(stops=[{"kind": "food"}, {"kind": "cafe"}])
        result = ItineraryPlanner([], STADIUM, [], route, candidate_source=source).plan(spec, GAME, now=NOW)
        self.assertEqual(result["status"], "ok")
        cafe = [t for t in result["search_trace"] if t["index"] == 1][0]
        self.assertAlmostEqual(cafe["center"]["lat"], result["stops"][0]["lat"])
        self.assertEqual([s["placeId"] for s in result["stops"][:2]], ["1", "2"])

    def test_required_evidence_guard_does_not_trigger_provider_or_paid_search(self):
        source, engine = provider(crowded_docs())
        spec = request(stops=[{"kind": "food", "cuisine": "일식"}])
        directions = Mock()
        result = ItineraryPlanner([], STADIUM, [], directions, candidate_source=source,
                                   local_evidence_only=True).plan(spec, GAME, now=NOW)
        self.assertEqual(result["status"], "knowledge_insufficient")
        self.assertEqual(engine.calls, [])
        self.assertEqual(result["candidate_retrieval"]["status"], "not_started")
        directions.assert_not_called()

    @override_settings(COURSE_WEB_VERIFICATION_ENABLED=False)
    def test_active_service_selects_kakao_provider_not_sbiz_rag(self):
        source, _ = provider([document(1, north=30)])
        inputs = {"question": "식당 한 곳 코스", "course_anchor": {"stadium_code": "JAMSIL",
                  "stadium_name": "잠실", "starts_at": GAME.isoformat()}, "course_state": {}}
        with patch("travel.course_candidates.load_course_candidates", side_effect=AssertionError("must not use SBIZ")), \
                patch("baseball.stadium_locations.reviewed_venue", return_value=STADIUM), \
                patch("travel.kakao_course_candidates.KakaoCourseCandidates", return_value=source), \
                patch("llm.v2.course.itinerary_service.extract_itinerary", return_value=request()), \
                patch("llm.v2.course.itinerary_service.timezone.now", return_value=NOW), \
                patch("travel.directions_provider.fetch_directions", side_effect=route), \
                patch("llm.v2.course.itinerary_service.KnowledgeFoodVerifier") as food, \
                patch("llm.v2.course.itinerary_service.KnowledgeReviewVerifier") as review:
            text = "".join(generate_itinerary(inputs))
        result = inputs["course_state"]["itinerary_result"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidate_retrieval"]["source"], "kakao_adaptive")
        self.assertEqual(result["stops"][0]["source"], "KAKAO")
        self.assertIsNone(result["snapshot_id"])
        self.assertFalse(result["verification_policy"]["automatic_web_search_enabled"])
        self.assertIn("첫 장소부터", text)
        food.assert_not_called()
        review.assert_not_called()

    def test_provider_construction_is_lazy_and_needs_no_catalogue(self):
        with patch("travel.place_service._request_kakao") as fetch:
            data, stadium = load_planning_data({"stadium_code": "JAMSIL", "stadium_name": "잠실"})
        self.assertEqual(data["places"], [])
        self.assertEqual(stadium["placeId"], "stadium:JAMSIL")
        self.assertEqual(data["candidate_source"].calls, 0)
        fetch.assert_not_called()

    @override_settings(COURSE_WEB_VERIFICATION_ENABLED=False)
    def test_unique_exact_name_origin_can_be_resolved_without_sbiz(self):
        source, _ = provider([document(1, north=30, name="정확한출발지"), document(2, north=70)])
        spec = request(origin="정확한출발지", stops=[{"kind": "food", "excluded_keywords": ["정확한출발지"]}])
        inputs = {"question": "정확한출발지에서 식당으로", "course_anchor": {"stadium_code": "JAMSIL",
                  "stadium_name": "잠실", "starts_at": GAME.isoformat()}, "course_state": {}}
        with patch("baseball.stadium_locations.reviewed_venue", return_value=STADIUM), \
                patch("travel.kakao_course_candidates.KakaoCourseCandidates", return_value=source), \
                patch("llm.v2.course.itinerary_service.extract_itinerary", return_value=spec), \
                patch("llm.v2.course.itinerary_service.timezone.now", return_value=NOW), \
                patch("travel.directions_provider.fetch_directions", side_effect=route):
            list(generate_itinerary(inputs))
        result = inputs["course_state"]["itinerary_result"]
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["origin_included"])
        self.assertEqual(result["legs"][0]["from"], "1")
        self.assertEqual(result["stops"][0]["placeId"], "2")
