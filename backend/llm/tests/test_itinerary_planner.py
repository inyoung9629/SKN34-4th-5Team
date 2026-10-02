"""Offline deterministic tests: no real model, route API, keys, or production DB."""
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from llm.v2.course.itinerary_request import ItineraryRequest, KST, StopRequest, extract_itinerary
from llm.v2.course.itinerary_planner import ItineraryPlanner, PlanningPolicy, meters
from llm.v2.course.itinerary_service import generate_itinerary, render_itinerary, resolve_origin

NOW = datetime(2026, 10, 1, 12, tzinfo=KST)
GAME = NOW.replace(hour=18, minute=30)


def place(identity, north_m=0, kind="food", **extra):
    return {"placeId": f"collected:SBIZ:{identity}", "name": identity, "lat": 37.5 + north_m / 111195,
            "lng": 127., "kind": kind, "cuisine": "한식", "address": "서울", **extra}


STADIUM = place("stadium", kind="stadium")
COVERAGE = [{**STADIUM, "radius_m": 2500}]


def route(mode, points):
    length = round(meters(*points))
    leg = {"status": "ok", "seconds": 300, "distance": length}
    return {"mode": mode, "legs": [leg], "seconds": 300, "distance": length}


def request(**extra):
    return ItineraryRequest.model_validate({"stops": [{"kind": "food"}], **extra})


class ItineraryPlannerTests(SimpleTestCase):
    def plan(self, places, spec=None, *, directions=route, coverage=None, policy=None, **kwargs):
        planner = ItineraryPlanner(places, STADIUM, COVERAGE if coverage is None else coverage,
                                   directions, policy=policy)
        return planner.plan(spec or request(), GAME, now=NOW, **kwargs)

    def test_expands_only_until_a_complete_course_exists(self):
        result = self.plan([place("near", 210)])
        self.assertEqual(result["status"], "ok")
        self.assertEqual([x["radius_m"] for x in result["search_trace"]], [100, 300])
        self.assertEqual(result["stops"][0]["search_radius_m"], 300)

    def test_no_expansion_when_100m_has_usable_candidate(self):
        result = self.plan([place("near", 50), place("far", 1100)])
        self.assertEqual(len(result["search_trace"]), 1)
        self.assertEqual(result["stops"][0]["name"], "near")

    def test_legacy_cuisine_needs_web_evidence_not_just_catalogue_labels(self):
        spec = request(stops=[{"kind": "food", "cuisine": "일식", "excluded_keywords": ["싫은매장"]}])
        result = self.plan([place("한식", 20), place("싫은매장", 60, cuisine="일식"),
                            place("일식식당", 650, cuisine="일식")], spec)
        self.assertEqual(result["status"], "food_search_unavailable")
        self.assertEqual(result["stops"], [])
        self.assertEqual(result["direction_calls"], 0)

    def test_next_search_uses_previous_stop_and_may_move_away_from_stadium(self):
        spec = request(stops=[{"kind": "food"}, {"kind": "cafe"}])
        food, cafe = place("식당", 250), place("카페", 470, "cafe")
        result = self.plan([food, cafe], spec)
        self.assertEqual(result["status"], "ok")
        cafe_trace = [x for x in result["search_trace"] if x["index"] == 1]
        self.assertAlmostEqual(cafe_trace[0]["center"]["lat"], food["lat"])
        self.assertGreater(result["stops"][1]["lat"], result["stops"][0]["lat"])

    def test_whole_course_counts_dwell_all_legs_and_buffers(self):
        spec = request(stops=[{"kind": "food"}, {"kind": "cafe"}], start_at=GAME.replace(hour=16))
        result = self.plan([place("식당", 20), place("카페", 40, "cafe")], spec)
        # 16:30 + 60 + 40 + 2 * (5 travel + 5 buffer) = 18:30 > 18:10.
        self.assertEqual(result["status"], "time_infeasible")
        self.assertEqual(result["stops"], [])

    def test_deadline_boundary_and_user_arrival_override(self):
        spec = request(start_at=GAME.replace(hour=17, minute=0))
        result = self.plan([place("식당", 50)], spec)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stadium_arrival_at"], GAME.replace(hour=18, minute=10).isoformat())
        tighter = spec.model_copy(update={"arrival_at": GAME.replace(hour=18, minute=9)})
        self.assertEqual(self.plan([place("식당", 50)], tighter)["status"], "time_infeasible")

    def test_no_origin_means_no_invented_stadium_to_first_place_leg(self):
        result = self.plan([place("식당", 50)])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["legs"]), 1)
        self.assertEqual(result["legs"][0]["from"], "collected:SBIZ:식당")
        self.assertTrue(result["suggested_start"])
        self.assertEqual(result["start_at"], GAME.replace(hour=17, minute=0).isoformat())

    def test_no_origin_food_cafe_course_starts_with_first_stay_and_only_two_legs(self):
        food, cafe = place("식당", 50), place("카페", 80, "cafe")
        directions = Mock(side_effect=route)
        spec = request(stops=[{"kind": "food"}, {"kind": "cafe"}])
        result = self.plan([food, cafe], spec, directions=directions)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["origin_included"])
        self.assertEqual(result["start_at"], result["stops"][0]["arrive_at"])
        self.assertEqual(result["start_at"], GAME.replace(hour=16, minute=10).isoformat())
        self.assertEqual(result["stops"][0]["depart_at"], GAME.replace(hour=17, minute=10).isoformat())
        self.assertEqual(result["stops"][1]["arrive_at"], GAME.replace(hour=17, minute=20).isoformat())
        self.assertEqual(result["stadium_arrival_at"], GAME.replace(hour=18, minute=10).isoformat())
        self.assertEqual([(leg["from"], leg["to"]) for leg in result["legs"]],
                         [(food["placeId"], cafe["placeId"]), (cafe["placeId"], STADIUM["placeId"])])
        self.assertEqual(directions.call_count, 2)
        self.assertEqual(result["total_travel_seconds"], 600)
        self.assertEqual(result["total_distance_m"], sum(leg["distance_m"] for leg in result["legs"]))

    def test_time_without_origin_is_first_place_use_time_not_a_hidden_departure(self):
        starts_at = GAME.replace(hour=15, minute=0)
        spec = request(start_at=starts_at)
        result = self.plan([place("식당", 50)], spec)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["arrive_at"], starts_at.isoformat())
        self.assertEqual(result["stops"][0]["depart_at"], (starts_at + timedelta(hours=1)).isoformat())
        self.assertFalse(result["suggested_start"])
        self.assertFalse(result["origin_included"])
        self.assertEqual(len(result["legs"]), 1)

    def test_given_origin_is_search_center_and_counts_first_leg(self):
        origin = place("출발지", 500)
        result = self.plan([place("식당", 550)], origin=origin)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["legs"]), 2)
        self.assertEqual(result["search_trace"][0]["center"]["lat"], origin["lat"])
        self.assertEqual(result["start_at"], GAME.replace(hour=16, minute=50).isoformat())

    def test_backtracks_earlier_food_when_later_course_is_too_slow(self):
        first, alternative, cafe = place("느린식당", 20), place("다른식당", 40), place("카페", 70, "cafe")

        def timed(mode, points):
            result = route(mode, points)
            if abs(points[0]["lat"] - first["lat"]) < 1e-10:
                result["seconds"] = result["legs"][0]["seconds"] = 3600
            return result

        spec = request(stops=[{"kind": "food"}, {"kind": "cafe"}],
                       start_at=GAME.replace(hour=15, minute=40))
        result = self.plan([first, alternative, cafe], spec, directions=timed)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["name"], "다른식당")

    def test_expands_after_near_candidate_is_time_infeasible(self):
        first, alternative = place("느린식당", 40), place("다른식당", 220)

        def timed(mode, points):
            result = route(mode, points)
            if abs(points[0]["lat"] - first["lat"]) < 1e-10:
                result["seconds"] = result["legs"][0]["seconds"] = 3600
            return result

        result = self.plan([first, alternative], request(start_at=GAME.replace(hour=16, minute=20)), directions=timed)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["search_radius_m"], 300)

    def test_default_maximum_is_1500_not_a_silent_widening(self):
        result = self.plan([place("멀리", 1600)])
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["search_trace"][-1]["radius_m"], 1500)
        self.assertEqual(result["stops"], [])

    def test_explicit_expansion_beyond_default_is_permitted_not_automatic(self):
        result = self.plan([place("멀리", 1600)], request(max_leg_m=2000))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["search_radius_m"], 2000)

    def test_same_place_is_not_used_twice(self):
        result = self.plan([place("카페", 50, "cafe")], request(stops=[{"kind": "cafe"}, {"kind": "cafe"}]))
        self.assertEqual(result["status"], "no_match")

    def test_wall_time_limit_does_not_become_a_zero_result_search(self):
        result = self.plan([place("식당", 20)], policy=PlanningPolicy(wall_seconds=0))
        self.assertEqual(result["status"], "search_limited")

    def test_same_ring_prefers_actual_travel_time_over_stadium_direction(self):
        near, farther = place("구장쪽", 480), place("반대쪽", 530)

        def timed(mode, points):
            result = route(mode, points)
            if abs(points[1]["lat"] - near["lat"]) < 1e-10:
                result["seconds"] = result["legs"][0]["seconds"] = 1800
            return result

        result = self.plan([near, farther], origin=place("출발", 500), directions=timed)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["name"], "반대쪽")

    def test_before_and_after_timing_are_joined_without_missing_legs(self):
        spec = request(stops=[{"kind": "food"}, {"kind": "cafe", "phase": "after"}])
        result = self.plan([place("식당", 50), place("카페", 70, "cafe")], spec)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([s["phase"] for s in result["stops"]], ["before", "game", "after"])
        self.assertEqual(len(result["legs"]), 2)
        self.assertEqual(result["stops"][2]["arrive_at"], GAME.replace(hour=21, minute=40).isoformat())

    def test_explicit_distance_limit_applies_to_route_not_just_straight_distance(self):
        def winding(mode, points):
            result = route(mode, points)
            result["distance"] = result["legs"][0]["distance"] = 800
            return result

        result = self.plan([place("직선100실제800", 90)], request(max_leg_m=500), directions=winding)
        self.assertEqual(result["status"], "distance_limit")
        self.assertEqual(result["radius_cap_m"], 500)

    def test_explicit_cap_between_steps_is_honored(self):
        result = self.plan([place("식당", 610)], request(max_leg_m=650))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["search_trace"][-1]["radius_m"], 650)

    def test_total_distance_is_separate_from_leg_limit(self):
        result = self.plan([place("식당", 400)], request(max_total_m=300))
        self.assertEqual(result["status"], "distance_limit")

    def test_uncollected_region_is_not_reported_as_no_places(self):
        result = self.plan([], coverage=[{**STADIUM, "radius_m": 500}])
        self.assertEqual(result["status"], "coverage_incomplete")

    def test_overlap_of_original_and_reviewed_coverage_is_required(self):
        coverage = [{**STADIUM, "radius_m": 2500}, {**place("old", -2400), "radius_m": 2500}]
        result = self.plan([], coverage=coverage)
        self.assertEqual(result["status"], "coverage_incomplete")

    def test_no_route_or_stale_route_cannot_produce_a_successful_course(self):
        for leg in ({"status": "error"}, {"status": "ok", "stale": True}):
            with self.subTest(leg=leg):
                result = self.plan([place("식당", 50)], directions=lambda *_: {
                    "legs": [leg], "seconds": 300, "distance": 200})
                self.assertEqual(result["status"], "directions_unavailable")
                self.assertEqual(result["stops"], [])

    def test_route_result_rejects_nan_negative_and_boolean(self):
        for seconds in (float("nan"), -1, True, None):
            result = self.plan([place("식당", 50)], directions=lambda *_: {
                "legs": [{"status": "ok"}], "seconds": seconds, "distance": 200})
            self.assertEqual(result["status"], "directions_unavailable")

    def test_direction_budget_is_not_reported_as_no_match(self):
        result = self.plan([place("식당", 50)], policy=PlanningPolicy(max_direction_calls=0))
        self.assertEqual(result["status"], "search_limited")

    def test_same_leg_is_cached_within_one_planning_request(self):
        spy = Mock(side_effect=route)
        planner = ItineraryPlanner([place("식당", 50)], STADIUM, COVERAGE, spy)
        planner.plan(request(), GAME, now=NOW)
        planner.leg(place("식당", 50), STADIUM)
        self.assertEqual(spy.call_count, 1)

    def test_after_game_is_conditional_and_checks_finish_deadline(self):
        spec = request(stops=[{"kind": "cafe", "phase": "after"}])
        result = self.plan([place("카페", 70, "cafe")], spec)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["timing_status"], "conditional_game_end")
        self.assertEqual(result["finish_at"], GAME.replace(hour=22, minute=20).isoformat())
        spec.finish_by = GAME.replace(hour=22, minute=19)
        self.assertEqual(self.plan([place("카페", 70, "cafe")], spec)["status"], "time_infeasible")

    def test_after_only_explicit_start_does_not_become_a_pre_game_start(self):
        spec = request(stops=[{"kind": "cafe", "phase": "after"}], start_at=GAME.replace(hour=22, minute=0))
        result = self.plan([place("카페", 70, "cafe")], spec)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["finish_at"], GAME.replace(hour=22, minute=50).isoformat())

    def test_user_dwell_time_is_preserved_not_shortened(self):
        result = self.plan([place("식당", 50)], request(stops=[{"kind": "food", "stay_minutes": 90}]))
        self.assertEqual(result["stops"][0]["stay_minutes"], 90)
        self.assertFalse(result["stops"][0]["stay_is_default"])

    def test_required_unknown_property_is_not_claimed_verified(self):
        result = self.plan([place("조용한 식당", 20)], request(unverified_requirements=["반드시 조용함"]))
        self.assertEqual(result["status"], "constraints_unverified")
        self.assertEqual(result["direction_calls"], 0)

    def test_lodging_is_not_silently_removed(self):
        result = self.plan([place("식당", 50)], request(stops=[{"kind": "food"}, {"kind": "stay", "phase": "after"}]))
        self.assertEqual(result["status"], "unsupported_lodging")
        self.assertEqual(result["stops"], [])

    def test_affiliation_annotation_does_not_yet_exclude_stores(self):
        result = self.plan([place("구장매장", 30, stadiumAffiliation={"status": "candidate"})])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["stadiumAffiliation"]["status"], "candidate")

    def test_historical_edit_is_separate_from_new_visit_timing(self):
        spec = request(start_at=GAME.replace(hour=16, minute=20))
        future_now = NOW + timedelta(days=1)
        planner = ItineraryPlanner([place("식당", 50)], STADIUM, COVERAGE, route)
        self.assertEqual(planner.plan(spec, GAME, now=future_now)["status"], "time_infeasible")
        self.assertEqual(planner.plan(spec, GAME, now=future_now, historical=True)["status"], "ok")

    def test_depart_now_does_not_turn_into_a_past_start_during_planning(self):
        result = self.plan([place("식당", 50)], request(start_now=True))
        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(datetime.fromisoformat(result["start_at"]), NOW)
        self.assertFalse(result["suggested_start"])


class ItinerarySchemaTests(SimpleTestCase):
    def test_invalid_inputs_cannot_supply_fake_coordinates_or_relax_bounds(self):
        for values in ({"stops": []}, {"origin_lat": 37.5}, {"max_leg_m": True}, {"max_leg_m": 20001},
                       {"start_at": "2026-10-01T14:00:00"}, {"stops": [{"kind": "food", "stay_minutes": -1}]},
                       {"stops": [{"kind": "cafe", "cuisine": "일식"}]},
                       {"stops": [{"kind": "cafe", "phase": "after"}, {"kind": "food"}]}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                request(**values)

    def test_model_extracts_schema_not_final_itinerary_and_excludes_assistant_prose(self):
        import json
        from langchain_core.messages import AIMessage, HumanMessage
        model = Mock()
        model.invoke.return_value = AIMessage(content=request().model_dump_json())
        with patch("llm.v2.agent.common.llm", return_value=model):
            extracted = extract_itinerary("코스", {"id": 1}, {}, history=[
                AIMessage(content="모델이 지어낸 출발지"), HumanMessage(content="식사할래")], now=NOW)
        self.assertEqual(extracted.stops[0].kind, "food")
        payload = json.loads(model.invoke.call_args.args[0][1].content)
        self.assertEqual(payload["recent_user_messages"], ["식사할래"])

    def test_origin_requires_explicit_coordinates_or_exact_unique_name(self):
        self.assertEqual(resolve_origin("위도 37.5, 경도 127", {}, [], STADIUM,
                                        coordinate_sources=["37.5,127"])["lat"], 37.5)
        with self.assertRaises(ValueError):
            resolve_origin("37.5,127", {}, [], STADIUM)  # Model-invented coordinates.
        with self.assertRaises(ValueError):
            resolve_origin("잠실역 근처 아무 곳", {}, [], STADIUM)
        with self.assertRaises(ValueError):
            resolve_origin("식당", {}, [place("식당", 10), place("식당", 50)], STADIUM)

    def test_no_origin_is_optional_and_does_not_resolve_to_stadium_or_candidate(self):
        self.assertIsNone(resolve_origin(None, {}, [place("식당", 50)], STADIUM))
        self.assertIsNone(resolve_origin(None, {"origin": None}, [], STADIUM))

    def test_extraction_rules_do_not_require_or_invent_missing_origin(self):
        from llm.v2.course.conditions import EXTRACTION_RULES
        from llm.v2.course.itinerary_request import RULES
        self.assertIn("출발지는 필수 정보가 아니다", EXTRACTION_RULES)
        self.assertIn("DO NOT ask for an origin", RULES)
        self.assertIn("excluding travel to that first place", RULES)


class ItineraryServiceTests(SimpleTestCase):
    def inputs(self):
        return {"question": "식사 코스", "course_anchor": {"id": 1, "stadium_code": "JAMSIL",
            "stadium_name": "잠실야구장", "starts_at": GAME.isoformat()}, "course_preferences": {}, "course_state": {}}

    def test_validated_result_and_request_are_saved_in_same_room_state(self):
        inputs = self.inputs()
        data = {"places": [place("식당", 50)], "coverage": COVERAGE, "snapshotId": "test-snapshot"}
        with patch("llm.v2.course.itinerary_service.extract_itinerary", return_value=request()), \
                patch("llm.v2.course.itinerary_service.load_planning_data", return_value=(data, STADIUM)), \
                patch("llm.v2.course.itinerary_service.timezone.now", return_value=NOW), \
                patch("travel.directions_provider.fetch_directions", side_effect=route):
            answer = "".join(generate_itinerary(inputs))
        self.assertIn("식당", answer)
        self.assertIn("영업시간", answer)
        self.assertEqual(inputs["course_state"]["itinerary_result"]["status"], "ok")
        self.assertEqual(inputs["course_state"]["itinerary_request"]["stops"][0]["kind"], "food")
        self.assertIsNone(inputs["course_state"]["itinerary_origin"])
        self.assertIsNone(inputs["course_state"]["itinerary_request"]["origin"])
        self.assertIn("출발지가 없어 첫 장소부터 시작", answer)

    def test_extraction_failure_does_not_fall_back_to_an_unconstrained_course(self):
        inputs = self.inputs()
        with patch("llm.v2.course.itinerary_service.extract_itinerary", side_effect=ValueError), \
                patch("llm.v2.course.itinerary_service.load_planning_data") as data:
            answer = "".join(generate_itinerary(inputs))
        data.assert_not_called()
        self.assertIn("정확히 해석하지", answer)
        self.assertEqual(inputs["course_state"]["itinerary_result"]["status"], "extraction_failed")

    def test_preference_reset_prevents_old_history_from_resurrecting_constraints(self):
        inputs = self.inputs()
        inputs.update(chat_history=["old constraints"])
        inputs["course_state"]["itinerary_reset"] = True
        with patch("llm.v2.course.itinerary_service.extract_itinerary", return_value=request()) as extraction, \
                patch("llm.v2.course.itinerary_service.load_planning_data", side_effect=OSError):
            list(generate_itinerary(inputs))
        self.assertEqual(extraction.call_args.args[4], ())

    def test_failure_renderer_never_outputs_partial_places(self):
        text = render_itinerary({"status": "time_infeasible", "stops": [{"name": "unvalidated-place"}]}, request())
        self.assertNotIn("unvalidated-place", text)


from llm.tests.course_checkpoint import CourseCheckpointTestCase


class ItineraryChatIntegrationTests(CourseCheckpointTestCase):
    def test_real_course_chain_sse_and_database_keep_only_validated_result(self):
        from copy import deepcopy
        from llm.models import ChatSession
        from llm.service.chat import send_message
        from llm.v2.agent.course_chain import course_chain
        from llm.v2.course.policy import CourseDecision
        from llm.v2.course.state import empty_state
        from llm.v2.agent.chain import build_graph
        from llm.v2.tests.test_chain import ScriptedModel, fake_tools

        session = ChatSession.objects.create(guest="dcdcdcdc-dcdc-dcdc-dcdc-dcdcdcdcdcdc")
        anchor = {"id": 1, "stadium_code": "JAMSIL", "stadium_name": "잠실", "starts_at": GAME.isoformat()}

        def resolve(question, previous, *args):
            state = deepcopy(previous) if previous else empty_state()
            state["selected_game"] = anchor
            return CourseDecision(state, "기준 경기 안내\n", anchor)

        data = {"places": [place("식당", 50)], "coverage": COVERAGE, "snapshotId": "test"}
        graph = build_graph(ScriptedModel(script=[], calls=[]), fake_tools([]))
        with patch("llm.v2.agent.chain.get_graph", return_value=graph), \
                patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": ["day_plan"]}), \
                patch("llm.v2.course.policy.resolve_course", side_effect=resolve), \
                patch("llm.v2.course.itinerary_service.extract_itinerary", return_value=request()) as extraction, \
                patch("llm.v2.course.itinerary_service.load_planning_data", return_value=(data, STADIUM)), \
                patch("llm.v2.course.itinerary_service.timezone.now", return_value=NOW), \
                patch("travel.directions_provider.fetch_directions", side_effect=route):
            frames = list(send_message(session, "식당 코스", version="v2"))
            self.assertEqual(frames[0], ("delta", {"text": "기준 경기 안내\n"}))
            self.assertEqual(frames[-1][0], "done")
            self.assertEqual(self.memory(session)["itinerary_result"]["status"], "ok")
            self.assertIn("식당", frames[-1][1]["assistant_message"])
            list(send_message(session, "그 조건 그대로", version="v2"))
            self.assertEqual(extraction.call_args.args[3]["stops"][0]["kind"], "food")
