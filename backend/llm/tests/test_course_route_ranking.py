from copy import deepcopy
from contextlib import ExitStack
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from llm.v1.progress import ProgressCancelled
from llm.v1.rag.course import agent, geo, route_ranking as ranking, slots, transport


ANCHOR = {"lat": 37.5, "lng": 127.0}
ORIGIN = {"lat": 37.516, "lng": 127.0}


def place(name, lat, kind="FOOD_OUT", lng=127.0):
    return {"name": name, "placeId": name, "lat": lat, "lng": lng, "category": kind,
            "dist": .3, "detail": {"FOOD_OUT": "중식", "CAFE": "카페", "WALK": "공원"}[kind]}


def step(p, phase="BEFORE"):
    return {"place": p, "phase": phase, "reason": "test"}


GAME = step(None, "GAME")
NEAR = place("출발옆식당", 37.514)
FAR = place("구장옆식당", 37.502)
CAFE = place("중간카페", 37.508, "CAFE")
PARK = place("산책공원", 37.501, "WALK", 127.005)


def provider(costs=None):
    def invoke(domain, name, args):
        a, b = args["points"]
        distance = geo._dist(a, b)
        seconds = (costs or {}).get(ranking.edge_key(a, b), distance)
        if seconds is None:
            return {"legs": [{"status": "error"}]}
        return {"legs": [{"status": "ok", "seconds": seconds, "distance": distance, "paths": [[a, b]]}]}
    return Mock(side_effect=invoke)


class RouteDurationTests(SimpleTestCase):
    def run_ranker(self, invoke, steps=None, choices=None, mode=None):
        steps = steps or [step(NEAR), step(CAFE), GAME, step(PARK, "AFTER")]
        choices = choices or [[NEAR, FAR], [CAFE], [None], [PARK]]
        ranker = ranking.RouteRanker(invoke, mode)
        original = deepcopy(steps)
        chosen = ranker.optimize(steps, choices, ORIGIN, ANCHOR, lambda p: 0)
        self.assertEqual(steps, original)
        self.assertEqual([s["phase"] for s in chosen], [s["phase"] for s in steps])
        return ranker, chosen

    def test_same_line_does_not_reward_skipping_origin_area_for_stadium_proximity(self):
        self.assertGreater(geo.step_score(NEAR, ORIGIN, ANCHOR, lambda p: 0),
                           geo.step_score(FAR, ORIGIN, ANCHOR, lambda p: 0))
        ranker, chosen = self.run_ranker(provider())
        self.assertEqual(chosen[0]["place"], NEAR)
        self.assertEqual(ranker.report["strategy"], "duration")
        self.assertEqual(ranker.report["measured"], 2)

    def test_actual_road_time_overrides_geometrically_better_route(self):
        invoke = provider({ranking.edge_key(ORIGIN, NEAR): 6000})
        ranker, chosen = self.run_ranker(invoke)
        self.assertEqual(chosen[0]["place"], FAR)
        self.assertEqual(chosen[-1]["place"], PARK)
        points = ranking.points_of(chosen, ORIGIN, ANCHOR)
        self.assertEqual(ranker.report["seconds"], sum(x["seconds"] for x in ranker.known_legs(points)))

    def test_total_time_includes_next_stop_not_only_first_hop(self):
        invoke = provider({ranking.edge_key(ORIGIN, NEAR): 10, ranking.edge_key(NEAR, CAFE): 6000})
        _, chosen = self.run_ranker(invoke)
        self.assertEqual(chosen[0]["place"], FAR)

    def test_failed_candidate_does_not_prevent_measuring_other_route(self):
        ranker, chosen = self.run_ranker(provider({ranking.edge_key(ORIGIN, NEAR): None}))
        self.assertEqual(chosen[0]["place"], FAR)
        self.assertEqual(ranker.report["measured"], 1)
        self.assertTrue(ranker.report["limited"])

    def test_no_directions_falls_back_toward_stadium_without_extra_stops(self):
        invoke = Mock(return_value={"legs": [{"status": "error"}]})
        ranker, chosen = self.run_ranker(invoke)
        self.assertEqual(chosen[0]["place"], FAR)
        self.assertEqual([s["place"] for s in chosen[1:]], [CAFE, None, PARK])
        self.assertEqual(ranker.report["strategy"], "stadium_fallback")
        self.assertIn("추정값", ranker.notice())
        self.assertLessEqual(invoke.call_count, 3)  # At most one already-started parallel edge.

    def test_only_fully_measured_routes_compete_with_each_other(self):
        # The second route appears cheap but its last changed edge is unknown.
        invoke = provider({ranking.edge_key(ORIGIN, FAR): 1, ranking.edge_key(FAR, CAFE): None})
        ranker, chosen = self.run_ranker(invoke)
        self.assertEqual(chosen[0]["place"], NEAR)
        self.assertEqual(ranker.report["measured"], 1)

    def test_default_walk_and_explicit_modes_are_sent_to_provider(self):
        for mode in (None, "walk", "transit", "car"):
            invoke = provider()
            self.run_ranker(invoke, mode=mode)
            self.assertTrue(all(c.args[2]["mode"] == (mode or "walk") for c in invoke.call_args_list))

    def test_shared_edges_are_fetched_once_and_reverse_edge_is_distinct(self):
        invoke = provider()
        ranker, chosen = self.run_ranker(invoke)
        keys = [ranking.edge_key(*c.args[2]["points"]) for c in invoke.call_args_list]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 6)
        self.assertIsNone(ranker.known_legs([PARK, ANCHOR])[0])
        self.assertTrue(all(ranker.known_legs(ranking.points_of(chosen, ORIGIN, ANCHOR))))

    def test_request_budget_stops_queries_and_uses_direction_fallback(self):
        for limit in (0, 1):
            invoke = provider()
            with patch.object(ranking, "MAX_LEGS", limit):
                ranker, _ = self.run_ranker(invoke)
            self.assertLessEqual(invoke.call_count, limit)
            self.assertEqual(ranker.report["strategy"], "stadium_fallback")
        invoke = provider()
        with patch.object(ranking, "TIME_BUDGET_S", 0):
            ranker, _ = self.run_ranker(invoke)
        invoke.assert_not_called()

    def test_stale_and_invalid_values_never_count_as_measured(self):
        base = {"status": "ok", "distance": 100, "seconds": 80, "paths": [[ORIGIN, NEAR]]}
        for bad in ({"stale": True}, {"seconds": float("nan")}, {"seconds": True},
                    {"distance": -1}, {"seconds": float("inf")}, {"paths": []}):
            ranker, _ = self.run_ranker(Mock(return_value={"legs": [{**base, **bad}]}))
            self.assertEqual(ranker.report["measured"], 0)

    def test_cancellation_is_propagated(self):
        with self.assertRaises(ProgressCancelled):
            self.run_ranker(Mock(side_effect=ProgressCancelled()))

    def test_only_one_valid_route_does_not_spend_comparison_calls(self):
        invoke = provider()
        ranker, chosen = self.run_ranker(invoke, choices=[[NEAR], [CAFE], [None], [PARK]])
        invoke.assert_not_called()
        self.assertEqual(chosen[0]["place"], NEAR)
        self.assertEqual(ranker.notice(), "")

    def test_variants_cover_meal_and_cafe_not_only_alternate_parks(self):
        cafe2 = place("다른카페", 37.504, "CAFE")
        parks = [place(f"공원{i}", 37.501 + i / 10000, "WALK", 127.005) for i in range(4)]
        rows = ranking.variants([step(NEAR), step(CAFE), GAME, step(parks[0], "AFTER")],
                                [[NEAR, FAR], [CAFE, cafe2], [None], parks], ORIGIN, ANCHOR, lambda p: 0)
        self.assertEqual(len({r[0]["place"]["name"] for r in rows}), 2)
        self.assertEqual(len({r[1]["place"]["name"] for r in rows}), 2)
        self.assertGreater(len({r[3]["place"]["name"] for r in rows}), 1)
        self.assertLessEqual(len(rows), ranking.MAX_VARIANTS)


class OriginFallbackTests(SimpleTestCase):
    def test_no_previous_area_matches_expands_to_stadium_with_same_filters(self):
        outside = place("반경밖", 37.53)
        rejected = place("제외식당", 37.502)
        wrong = place("한식집", 37.503)
        wrong["detail"] = "한식"
        calls = []
        def search(kind, center, radius, anchor, sl, **kwargs):
            calls.append((center, radius))
            return [outside, rejected, wrong, FAR] if center == ANCHOR else []
        sl = slots.parse("경기 전에 중식집만")
        sl["itinerary"] = {"BEFORE": ["FOOD"], "AFTER": []}
        sl["exclude"] = {rejected["name"]}
        with patch.object(agent, "_kakao_step", side_effect=search):
            result = agent.build_origin_course(ORIGIN, ANCHOR, [], sl, False,
                       candidate_filter=lambda items: [p for p in items if p["detail"] == "중식"],
                       route_ranker=ranking.RouteRanker(provider()))
        self.assertEqual(result[0]["place"]["name"], FAR["name"])
        self.assertEqual(calls[-1], (ANCHOR, 2500))
        self.assertTrue(all(c[0] == ORIGIN for c in calls[:-1]))
        self.assertIn("direction_fallback_places", sl)

    def test_no_matching_fallback_omits_visit_instead_of_relaxing_conditions(self):
        with patch.object(agent, "_kakao_step", return_value=[FAR]):
            self.assertIsNone(agent.build_origin_course(ORIGIN, ANCHOR, [], slots.parse("경기 전 중식집만"), False,
                                                       candidate_filter=lambda items: []))

    def test_manual_path_keeps_corridor_selection_instead_of_duration_reranking(self):
        ranker = Mock()
        with patch.object(agent.corridor, "choose", return_value=NEAR):
            rows = agent.build_origin_course(ORIGIN, ANCHOR, [], slots.parse("경기 전에 식사만"), False,
                                            route_segments=[[ORIGIN, ANCHOR]], route_ranker=ranker)
        ranker.optimize.assert_not_called()
        self.assertEqual(rows[0]["place"], NEAR)

    def test_fallback_prefers_advancing_candidate_over_high_relevance_backtrack(self):
        backwards = place("역방향식당", 37.52)
        result = ranking.stadium_fallback([step(backwards), GAME], [[backwards, NEAR], [None]],
                                         ORIGIN, ANCHOR, lambda p: 100 if p == backwards else 0)
        self.assertEqual(result[0]["place"], NEAR)


class MeasuredTravelLabelTests(SimpleTestCase):
    def test_car_provider_time_does_not_claim_parking_is_included(self):
        text = transport.summary([{"meters": 1000, "seconds": 300, "minutes": 5, "by": "car"}], "car")
        self.assertIn("주차 별도", text)
        self.assertNotIn("주차 포함", text)

    def test_transit_provider_time_is_not_labelled_as_zero_minute_walk(self):
        text = transport.summary([{"meters": 1000, "seconds": 600, "minutes": 10, "by": "transit"}], "transit")
        self.assertIn("대중교통 10분", text)
        self.assertNotIn("도보", text)


class RouteDurationPipelineTests(SimpleTestCase):
    def test_selected_route_reaches_card_map_and_timetable_with_no_duplicate_directions(self):
        def complete(p):
            return {"placeUrl": "https://example.com/place", "address": "테스트 주소", "distance": 500,
                    "doc_id": "", **p}
        near, far, cafe, park = [complete(p) for p in (NEAR, FAR, CAFE, PARK)]
        stadium = complete({**ANCHOR, "name": "잠실야구장", "key": "STADIUM", "placeId": None, "category": "STADIUM"})
        game = {"date": "2027-10-06", "time": "18:30", "home": "LG", "away": "삼성", "status": "scheduled"}
        invoke = provider({ranking.edge_key(ORIGIN, near): 6000})
        mocks = {"load_schedule": ({}, 1), "find_game": (game, False, [game]), "embed_many": ([.1], [.2]),
                 "stadium_anchor": stadium, "_live_candidates": ([near, far, cafe, park], {}), "search_places": [],
                 "call_llm": ('{"course": []}', 0)}
        with ExitStack() as stack:
            # These synthetic places test route timing; quality evidence is tested separately.
            stack.enter_context(patch.object(agent.place_quality, "active", return_value=False))
            for name, result in mocks.items():
                stack.enter_context(patch.object(agent, name, return_value=result))
            describe = stack.enter_context(patch.object(agent, "call_llm"))
            stack.enter_context(patch.object(agent, "invoke_domain_tool", invoke))
            stack.enter_context(patch.object(agent.kakao, "nearby", return_value=[]))
            stack.enter_context(patch.object(agent, "_kakao_step", side_effect=lambda kind, *a, **kw: {
                "FOOD": [near, far], "CAFE": [cafe], "WALK": [park]}[kind]))
            result = agent.answer("경기 전 식사하고 카페 갔다가 경기 후 산책만", hint_stadium="JAMSIL", origin=ORIGIN)
        describe.assert_not_called()
        self.assertEqual(result["timing"]["llm_ms"], 0)
        names = [FAR["name"], CAFE["name"], "잠실야구장", PARK["name"]]
        self.assertEqual([p["name"] for p in result["places"]], names)
        self.assertEqual([p["name"] for p in result["coursePayload"]["stops"]], names)
        self.assertEqual(result["origin"]["lat"], ORIGIN["lat"])
        self.assertEqual(result["origin"]["lng"], ORIGIN["lng"])
        self.assertEqual(invoke.call_count, result["routeComparison"]["calls"])
        self.assertEqual([p["phase"] for p in result["places"]], ["BEFORE", "BEFORE", "GAME", "AFTER"])
        for p, nxt in zip(result["places"], result["places"][1:]):
            self.assertAlmostEqual(p["nextLeg"]["seconds"], geo._dist(p, nxt))
            self.assertRegex(p["time"], r"^\d\d:\d\d$")
