from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from ..v1.rag.course import arrival, geo

ANCHOR = {"lat": 37.5, "lng": 127.0}
WEST = {"lat": 37.5, "lng": 126.94}
NORTHWEST = {"lat": 37.54, "lng": 126.94}
NORTH = {"lat": 37.54, "lng": 127.0}


class ArrivalGeometryTests(SimpleTestCase):
    def test_named_map_origin_keeps_its_label_instead_of_previous_conversation(self):
        supplied = {**ANCHOR, "name": "잠실새내역"}
        invoke = Mock()
        point, label, error = arrival.resolve_origin("카페 코스", [{"role": "user", "content": "부산역에서 출발"}], supplied, ANCHOR, invoke)
        self.assertEqual((point, label, error), (ANCHOR, "잠실새내역", ""))
        invoke.assert_not_called()

    def test_west_departure_entering_from_north_follows_road_not_radial_line(self):
        entry = arrival.first_entry([[WEST, NORTHWEST, NORTH, ANCHOR]], ANCHOR)
        self.assertAlmostEqual(entry["lng"], 127.0, places=7)
        self.assertGreater(entry["lat"], ANCHOR["lat"])
        self.assertAlmostEqual(geo._dist(entry, ANCHOR), 2500, delta=.01)

    def test_sparse_segment_with_both_endpoints_outside_still_has_an_entry(self):
        entry = arrival.first_entry([[WEST, {"lat": 37.5, "lng": 127.06}]], ANCHOR)
        self.assertLess(entry["lng"], 127)
        self.assertAlmostEqual(geo._dist(entry, ANCHOR), 2500, delta=.01)

    def test_does_not_invent_a_line_between_separate_paths(self):
        self.assertIsNone(arrival.first_entry([[WEST, NORTHWEST], [NORTH, {"lat": 37.54, "lng": 127.04}]], ANCHOR))

    def test_uses_first_entry_even_when_route_later_leaves_radius(self):
        first = arrival.first_entry([[WEST, ANCHOR, NORTH, ANCHOR]], ANCHOR)
        self.assertLess(first["lng"], 127)

    def test_within_radius_keeps_origin_and_skips_directions(self):
        invoke = Mock()
        start = {"lat": 37.51, "lng": 127}
        self.assertEqual(arrival.approach(start, ANCHOR, None, invoke), (start, ""))
        invoke.assert_not_called()

    def test_requested_mode_is_used_and_missing_mode_defaults_to_walk(self):
        for mode in (None, "walk", "car", "transit"):
            invoke = Mock(return_value={"legs": [{"status": "ok", "paths": [[WEST, NORTHWEST, NORTH, ANCHOR]]}]})
            entry, notice = arrival.approach(WEST, ANCHOR, mode, invoke)
            self.assertEqual(invoke.call_args.args[2]["mode"], mode or "walk")
            self.assertGreater(entry["lat"], ANCHOR["lat"])
            self.assertIn("북쪽", notice)

    def test_failure_and_stale_geometry_do_not_create_false_entry(self):
        for leg in ({"status": "error"}, {"status": "ok", "stale": True}, {"status": "ok", "paths": []}):
            invoke = Mock(return_value={"legs": [{"paths": [[WEST, ANCHOR]], **leg}]})
            point, message = arrival.approach(WEST, ANCHOR, None, invoke)
            self.assertIsNone(point)
            self.assertIn("임시 코스", message)
            self.assertIn("출발 방향은 반영되지 않았어요", message)


class NamedOriginTests(SimpleTestCase):
    def test_explicit_departure_expressions_and_after_game_exclusion(self):
        for question in ("서울역에서 출발할 거야", "나는 서울역에서 대중교통으로 출발해. 식사랑 카페 갈래", "출발지는 서울역이야", "지금 서울역부터 시작하자"):
            self.assertEqual(arrival.origin_query(question), "서울역")
        self.assertIsNone(arrival.origin_query("경기 후 잠실역에서 출발해서 집에 갈래"))
        self.assertIsNone(arrival.origin_query("경기 전에 카페 가고 산책해"))

    def test_current_explicit_place_overrides_map_origin(self):
        invoke = Mock(return_value={"places": [{"place_name": "서울역", "x": "126.97", "y": "37.55"}]})
        point, label, error = arrival.resolve_origin("서울역에서 출발", [], ANCHOR, ANCHOR, invoke)
        self.assertEqual(point, {"lat": 37.55, "lng": 126.97})
        self.assertEqual((label, error), ("서울역", ""))
        self.assertNotIn("radius", invoke.call_args.args[2], "nationwide departures must not be clipped to the stadium's radius")

    def test_new_map_origin_overrides_old_history_without_place_lookup(self):
        invoke = Mock()
        point, _, error = arrival.resolve_origin("카페만 들러", [{"role": "user", "content": "서울역에서 출발"}], ANCHOR, ANCHOR, invoke)
        self.assertEqual((point, error), (ANCHOR, ""))
        invoke.assert_not_called()

    def test_followup_keeps_user_departure_but_never_reads_assistant_location(self):
        invoke = Mock(return_value={"places": [{"place_name": "서울역", "x": "126.97", "y": "37.55"}]})
        history = [{"role": "user", "content": "서울역에서 출발"}, {"role": "assistant", "content": "부산역에서 출발"}]
        point, label, _ = arrival.resolve_origin("카페도 추가해", history, None, ANCHOR, invoke)
        self.assertEqual(label, "서울역")
        self.assertIsNotNone(point)

    def test_ambiguous_place_does_not_silently_use_old_map_location(self):
        invoke = Mock(return_value={"places": [
            {"place_name": "중앙역", "x": "126.97", "y": "37.55"},
            {"place_name": "중앙역", "x": "129.03", "y": "35.10"},
        ]})
        point, _, error = arrival.resolve_origin("중앙역에서 출발", [], ANCHOR, ANCHOR, invoke)
        self.assertIsNone(point)
        self.assertIn("하나로 확인하지 못했어요", error)

    def test_invalid_coordinates_are_rejected(self):
        for value in (None, {"lat": True, "lng": 127}, {"lat": float("nan"), "lng": 127}, {"lat": 10**400, "lng": 127}, {"lat": 0, "lng": 0}):
            self.assertIsNone(arrival.coordinate(value))


class ArrivalPlanningTests(SimpleTestCase):
    def test_first_search_uses_real_entry_and_places_stay_within_radius(self):
        from ..v1.rag.course import agent, slots
        entry, _ = arrival.approach(WEST, ANCHOR, "walk", Mock(return_value={"legs": [
            {"status": "ok", "paths": [[WEST, NORTHWEST, NORTH, ANCHOR]]}]}))
        calls = []

        def search(_domain, _name, args):
            calls.append(args)
            # 경계 밖의 더 가까운 장소도 반환해, 실제 좌표 필터가 배제하는지 확인한다.
            return {"places": [
                {"id": "outside", "place_name": "반경 밖 카페", "category_name": "음식점 > 카페", "x": "127", "y": "37.524"},
                {"id": "inside", "place_name": "북쪽 진입 카페", "category_name": "음식점 > 카페", "x": "127", "y": "37.519"},
            ]}

        with patch.object(agent, "invoke_domain_tool", side_effect=search):
            steps = agent.build_origin_course(entry, ANCHOR, [], slots.parse("경기 전에 카페만"), False)
        self.assertAlmostEqual(calls[0]["latitude"], entry["lat"])
        self.assertEqual(calls[0]["longitude"], entry["lng"])
        self.assertEqual([step["place"]["name"] for step in steps if step["place"]], ["북쪽 진입 카페"])
        self.assertTrue(all(geo._dist(step["place"], ANCHOR) <= 2500 for step in steps if step["place"]))
