from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from llm.v1.rag.course import agent, corridor, edit_route, editing
from llm.tests.test_course_editing import CURRENT, PLACES, plan


ANCHOR = {"lat": 37.5, "lng": 127.0}
ORIGIN = {"lat": 37.52, "lng": 127.0}
ROUTE = {"segments": [(ORIGIN, ANCHOR)], "basis": "actual", "preference": "origin_to_stadium"}


class EditRouteTests(SimpleTestCase):
    def test_closer_replacement_uses_stadium_distance_not_distance_to_old_shop(self):
        target = {**ANCHOR, "lat": 37.518, "category": "FOOD", "name": "기존", "placeId": "old"}
        def raw(identifier, lat):
            return {"id": identifier, "place_name": identifier, "x": str(ANCHOR['lng']), "y": str(lat),
                    "category_group_code": "FD6", "category_name": "음식점 > 일식 > 초밥,롤"}
        rows = [raw("farther", 37.520), raw("old-neighbour", 37.516), raw("stadium-near", 37.501)]
        with patch.object(agent, "invoke_domain_tool", return_value={"places": rows}):
            result = editing.candidates(target, ANCHOR, {"query": "초밥", "closer_to_stadium": True}, [target])
        self.assertEqual([p['placeId'] for p in result], ["stadium-near", "old-neighbour"])
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = {
            "place_id": "old-neighbour", "matching_ids": ["old-neighbour", "stadium-near"], "evidence": "초밥"}
        with patch.object(agent, "llm", return_value=model):
            chosen = editing.choose(result, {"query": "초밥", "conditions": [], "closer_to_stadium": True}, "더 가까운 초밥집")
        self.assertEqual(chosen['placeId'], "stadium-near")
        self.assertIn("구장까지 직선", chosen['reason'])
        # Closing-time fallback must compare against the original shop, not
        # the rejected first replacement (which may itself be much closer).
        rejected = {**target, "lat": 37.501}
        with patch.object(agent, "invoke_domain_tool", return_value={"places": rows}):
            fallback = editing.candidates(rejected, ANCHOR,
                {"query": "초밥", "closer_to_stadium": True, "_distance_limit": 2000}, [rejected])
        self.assertIn("old-neighbour", [p['placeId'] for p in fallback])

    def test_origin_route_uses_direct_provider_path_not_old_restaurant_detour(self):
        bend = {"lat": 37.51, "lng": 127.01}
        invoke = Mock(return_value={"legs": [{"status": "ok", "paths": [[ORIGIN, bend, ANCHOR]]}]})
        old_path = {"points": [ORIGIN, {"lat": 37.51, "lng": 126.98}, ANCHOR], "source": "directions"}
        route = edit_route.prepare("origin_to_stadium", ORIGIN, ANCHOR, "walk", old_path, invoke)
        self.assertEqual(invoke.call_args.args[2]["points"], [ORIGIN, ANCHOR])
        self.assertEqual(route["basis"], "actual")
        self.assertAlmostEqual(corridor.distance_position(bend, route["segments"])[0], 0)
        self.assertGreater(corridor.distance_position(bend, ROUTE["segments"])[0], 800)

    def test_no_route_response_uses_explicitly_labelled_geometry_not_missing_web_evidence(self):
        for result in ({}, {"legs": [{"status": "ok", "stale": True, "paths": [[ORIGIN, ANCHOR]]}]}):
            route = edit_route.prepare("origin_to_stadium", ORIGIN, ANCHOR, "walk", None, Mock(return_value=result))
            self.assertEqual(route["basis"], "straight")
            self.assertIn("실제 경로 조회에 실패", edit_route.notice(ANCHOR, route))
        with self.assertRaisesRegex(ValueError, "출발지 위치"):
            edit_route.prepare("origin_to_stadium", None, ANCHOR, "walk", None, Mock())

    def test_selected_route_preserves_breaks(self):
        path = {"points": [{"lat": 37.49, "lng": 126.98}, {"lat": 37.51, "lng": 126.98},
                           {"lat": 37.49, "lng": 127.02}, {"lat": 37.51, "lng": 127.02}], "breaks": [2], "source": "drawn"}
        invoke = Mock()
        route = edit_route.prepare("selected_route", None, ANCHOR, "walk", path, invoke)
        self.assertEqual(edit_route.rank([{**ANCHOR, "name": "가상의 연결선 위"}], route), [])
        invoke.assert_not_called()

    def test_candidate_search_follows_route_and_excludes_far_existing_and_outside_places(self):
        target = {**ANCHOR, "lng": 126.99, "category": "FOOD", "name": "기존", "placeId": "old"}
        def raw(identifier, lat, lng):
            return {"id": identifier, "place_name": identifier, "x": str(lng), "y": str(lat),
                    "category_group_code": "FD6", "category_name": "음식점 > 한식 > 국밥"}
        response = {"places": [raw("near", 37.515, 127.0002), raw("old-neighbor", 37.50, 126.989),
                               raw("outside", 37.55, 127), raw("old", 37.5, 126.99)]}
        with patch.object(agent, "invoke_domain_tool", return_value=response) as invoke:
            result = editing.candidates(target, ANCHOR,
                {"query": "국밥", "conditions": ["국밥"], "_route": ROUTE}, [target])
        self.assertEqual([p["placeId"] for p in result], ["near"])
        self.assertLess(result[0]["routeDistance"], 20)
        self.assertGreater(len({c.args[2]["latitude"] for c in invoke.call_args_list}), 1)
        self.assertTrue(all(c.args[2]["radius"] <= 2500 for c in invoke.call_args_list))

    def test_selector_keeps_menu_verification_and_uses_computed_distance_order(self):
        candidates = [{**ANCHOR, "name": n, "placeId": n, "category": "FOOD", "detail": "국밥",
                       "routeDistance": d} for n, d in (("near", 15), ("far", 650))]
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = {
            "place_id": "far", "matching_ids": ["far", "near"], "evidence": "국밥 판매 확인"}
        query = {"query": "국밥", "conditions": ["국밥"], "_route": ROUTE, "route_preference": "origin_to_stadium"}
        with patch.object(agent, "llm", return_value=model), \
                patch("llm.v1.rag.course.evidence_memory.enrich", return_value=candidates) as enrich:
            result = editing.choose(candidates, query, "출발지에서 구장 가는 길 근처 국밥집")
        self.assertEqual(result["placeId"], "near")
        self.assertEqual(enrich.call_args.args[1], ["국밥"])
        with patch("llm.v1.rag.course.evidence_memory.enrich", return_value=[]):
            self.assertIsNone(editing.choose(candidates, query, "경로 근처 국밥집"))

    def test_replacement_keeps_other_stops_origin_game_and_route_validation_plan(self):
        current = deepcopy(CURRENT)
        current["writerState"] = {"title": "내 코스", "origin": ORIGIN, "completed": True}
        original = deepcopy(current)
        action = plan("replace", ["food"], query="국밥", conditions=["국밥"], route_preference="origin_to_stadium")
        replacement = {**PLACES[0], "name": "경로 위 국밥집", "placeId": "new", "lat": 37.51, "lng": 127.0}
        with patch.object(agent, "stadium_anchor", return_value=ANCHOR), \
                patch.object(edit_route, "prepare", return_value=ROUTE), \
                patch.object(editing, "candidates", return_value=[replacement]) as candidates, \
                patch.object(editing, "choose", return_value=replacement), \
                patch.object(editing, "rebuild", side_effect=lambda places, *args, **kwargs: {"places": places, "answer": "", "game": CURRENT["game"]}) as rebuild:
            result = editing.answer("국밥집만 경로 근처로 바꿔", [], current, "JAMSIL", ORIGIN, parsed=action)
        self.assertEqual(current, original)
        self.assertEqual(result["places"][0]["placeId"], "new")
        self.assertEqual(result["places"][1:], PLACES[1:])
        self.assertEqual(result["origin"], ORIGIN)
        self.assertEqual(result["game"], CURRENT["game"])
        self.assertEqual(candidates.call_args.args[2]["_route"], ROUTE)
        self.assertEqual(rebuild.call_args.kwargs["availability_plans"]["food"]["_route"], ROUTE)
        self.assertIn("조회한 이동 경로", result["answer"])

    def test_addition_notice_measures_added_stop_not_the_reference_and_staging_keeps_route(self):
        replacement = {**PLACES[0], "name": "새 국밥집", "placeId": "new", "lat": 37.51, "lng": 127.0}
        action = plan("add", [], reference="cafe", category="FOOD", query="국밥", conditions=["국밥"],
                      route_preference="origin_to_stadium")
        with patch.object(agent, "stadium_anchor", return_value=ANCHOR), \
                patch.object(edit_route, "prepare", return_value=ROUTE), \
                patch.object(editing, "candidates", return_value=[replacement]), \
                patch.object(editing, "choose", return_value=replacement), \
                patch.object(edit_route, "notice", return_value="계산한 거리") as notice:
            result = editing.answer("카페 전에 경로 근처 국밥집 추가", [], deepcopy(CURRENT), "JAMSIL", ORIGIN,
                                    parsed=action, _staged=True)
        self.assertEqual(notice.call_args.args[0]["placeId"], "new")
        self.assertEqual(result["_availability_plan"]["_route"], ROUTE)
        self.assertIn("계산한 거리", result["answer"])
