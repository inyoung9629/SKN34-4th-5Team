"""Local-first service regressions: no real LLM/search/directions calls."""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from llm.v2.course.itinerary_planner import ItineraryPlanner
from llm.v2.course.itinerary_service import generate_itinerary
from llm.v2.course.local_knowledge import missing_required_evidence
from .test_itinerary_planner import COVERAGE, GAME, NOW, STADIUM, place, request, route


@override_settings(COURSE_WEB_VERIFICATION_ENABLED=False)
class LocalCourseKnowledgeTests(SimpleTestCase):
    def run_course(self, spec):
        inputs = {"question": "가상 코스", "course_anchor": {"stadium_code": "JAMSIL",
                  "stadium_name": "잠실", "starts_at": GAME.isoformat()}, "course_state": {}}
        data = {"places": [place("식당", 50), place("카페", 80, "cafe")], "coverage": COVERAGE,
                "snapshotId": "fixture", "candidate_retrieval": {"source": "local_place_rag", "network_used": False}}
        with patch("llm.v2.course.itinerary_service.extract_itinerary", return_value=spec), \
                patch("llm.v2.course.itinerary_service.load_planning_data", return_value=(data, STADIUM)), \
                patch("llm.v2.course.itinerary_service.timezone.now", return_value=NOW), \
                patch("llm.v2.course.evidence_memory.EvidenceMemory.load", return_value=([], "ok")), \
                patch("llm.v2.course.serper_evidence.SerperResearch.run") as research, \
                patch("travel.directions_provider.fetch_directions", side_effect=route) as directions:
            text = "".join(generate_itinerary(inputs))
        research.assert_not_called()
        return inputs["course_state"], text, directions

    def test_basic_food_cafe_course_keeps_twenty_minute_arrival_and_rag_audit(self):
        state, text, _ = self.run_course(request(stops=[{"kind": "food"}, {"kind": "cafe"}]))
        result = state["itinerary_result"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stadium_arrival_at"], GAME.replace(minute=10).isoformat())
        self.assertEqual(result["candidate_retrieval"]["source"], "local_place_rag")
        self.assertFalse(result["verification_policy"]["automatic_web_search_enabled"])
        self.assertEqual([s["kind"] for s in result["stops"]], ["food", "cafe", "stadium"])
        self.assertIn("첫 장소부터", text)

    def test_menu_requirement_scans_local_memory_without_paid_web_or_routes(self):
        spec = request(stops=[{"kind": "food", "food": {"all_of": [{"any_of": [
            {"kind": "menu", "name": "돈까스"}]}]}}])
        state, text, directions = self.run_course(spec)
        result = state["itinerary_result"]
        self.assertEqual(result["status"], "knowledge_insufficient")
        self.assertEqual(result["stops"], [])
        self.assertTrue(result["search_trace"])
        directions.assert_not_called()
        self.assertIn("돈까스", text)
        self.assertIn("자동 유료 웹검색은 꺼져", text)
        self.assertEqual(state["itinerary_request"], spec.model_dump(mode="json"))

    def test_required_review_unknown_is_not_no_match(self):
        spec = request(stops=[{"kind": "food", "reviews": {"all_of": [
            {"aspect": "quietness", "priority": "required"}]}}])
        state, text, directions = self.run_course(spec)
        self.assertEqual(state["itinerary_result"]["status"], "knowledge_insufficient")
        directions.assert_not_called()
        self.assertIn("조용", text)

    def test_optional_review_remains_unknown_but_does_not_block_course(self):
        spec = request(stops=[{"kind": "food", "reviews": {"all_of": [
            {"aspect": "quietness", "priority": "preferred"}]}}])
        state, text, _ = self.run_course(spec)
        result = state["itinerary_result"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["review_verification"]["status"], "unknown")
        self.assertEqual(result["review_search"]["lookups"], 0)
        self.assertIn("별도 웹검색은 하지 않았어요", text)
        self.assertNotIn("공개된 일부 자료를 모델이 해석", text)

    def test_required_gate_cannot_be_bypassed_with_an_injected_paid_verifier(self):
        paid = Mock()
        paid.policy.wall_seconds = 0
        spec = request(stops=[{"kind": "food", "cuisine": "일식"}])
        planner = ItineraryPlanner([place("일식집", 20)], STADIUM, COVERAGE, route,
                                   food_verifier=paid, local_evidence_only=True)
        self.assertEqual(planner.plan(spec, GAME, now=NOW)["status"], "knowledge_insufficient")
        paid.verify.assert_not_called()

    def test_internal_requests_stay_in_the_internal_validation_branch(self):
        spec = request(stops=[{"kind": "food", "phase": "inside", "food": {"all_of": [
            {"any_of": [{"kind": "menu", "name": "치킨"}]}]}}])
        self.assertEqual(missing_required_evidence(spec), [])

    def test_unsupported_lodging_is_not_silently_omitted_or_invented_from_url(self):
        state, text, directions = self.run_course(request(stops=[{"kind": "stay", "phase": "after"}]))
        self.assertEqual(state["itinerary_result"]["status"], "unsupported_lodging")
        self.assertEqual(state["itinerary_request"]["stops"][0]["kind"], "stay")
        directions.assert_not_called()

    def test_no_candidate_does_not_trigger_search(self):
        planner = ItineraryPlanner([], STADIUM, COVERAGE, Mock(), local_evidence_only=True)
        result = planner.plan(request(), GAME, now=NOW)
        self.assertEqual(result["status"], "no_match")
        planner.directions.assert_not_called()
