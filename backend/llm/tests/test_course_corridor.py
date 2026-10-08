from unittest.mock import Mock, patch
from contextlib import ExitStack

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from llm.serializer.message import _validate_context
from llm.tools import assistant
from llm.v1.rag.course import agent, arrival, corridor, geo, slots


ANCHOR = {"lat": 37.5, "lng": 127.0}
WEST = {"lat": 37.5, "lng": 126.98}
PATH = {"points": [WEST, ANCHOR], "source": "drawn", "label": "지도에 만든 전체 경로"}


def place(name, lat, lng, category="CAFE", detail="카페"):
    return {"name": name, "lat": lat, "lng": lng, "category": category, "detail": detail, "distance": 1,
            "dist": .1, "placeId": name, "address": "", "placeUrl": "", "doc_id": name}


class CorridorTests(SimpleTestCase):
    def test_clip_preserves_actual_north_entry_from_west_departure(self):
        route = [[{"lat": 37.5, "lng": 126.94}, {"lat": 37.54, "lng": 126.94}, {"lat": 37.54, "lng": 127}, ANCHOR]]
        segments = corridor.clip(route, ANCHOR)
        self.assertEqual(len(segments), 1)
        self.assertAlmostEqual(segments[0][0]["lng"], 127)
        self.assertAlmostEqual(geo._dist(segments[0][0], ANCHOR), 2500, delta=.01)

    def test_disconnected_paths_never_get_a_fictional_line(self):
        path = {**PATH, "points": [{"lat": 37.54, "lng": 126.94}, {"lat": 37.54, "lng": 127},
                                  {"lat": 37.46, "lng": 127}, {"lat": 37.46, "lng": 127.05}], "breaks": [2]}
        self.assertEqual(corridor.clip(corridor.paths_of(path), ANCHOR), [])

    def test_projection_uses_entire_segment_instead_of_only_vertices(self):
        distance, position = corridor.distance_position({"lat": 37.501, "lng": 126.99}, [(WEST, ANCHOR)])
        self.assertAlmostEqual(distance, 111.195, delta=1)
        self.assertGreater(position, 800)

    def course(self, text, pool, **extra):
        sl = {**slots.parse(text), **extra}
        with patch.object(agent, "_kakao_step", return_value=[]) as search:
            steps = agent.build_origin_course(WEST, ANCHOR, pool, sl, False, corridor.clip([[WEST, ANCHOR]], ANCHOR))
        return steps, search

    def test_narrow_corridor_wins_even_if_other_place_is_closer_to_stadium(self):
        near = place("경로카페", 37.501, 126.983)
        far = place("구장카페", 37.51, 127)
        steps, search = self.course("경기 전에 카페만", [far, near])
        self.assertEqual(steps[0]["place"]["name"], "경로카페")
        self.assertLess(steps[0]["place"]["routeDistance"], 300)
        self.assertLess(max(call.args[2] for call in search.call_args_list), 800)

    def test_widening_stays_in_stadium_circle_and_reports_large_detour(self):
        outside = place("반경밖카페", 37.5001, 126.968)
        inside = place("먼카페", 37.510, 126.99)
        steps, search = self.course("경기 전에 카페만", [outside, inside])
        self.assertEqual(steps[0]["place"]["name"], "먼카페")
        self.assertGreater(max(call.args[2] for call in search.call_args_list), 1500)
        self.assertIn("조건에 맞는 장소가 부족", corridor.notice([steps[0]["place"]]))
        self.assertIn("1.1km", corridor.notice([steps[0]["place"]]))

    def test_outside_and_wrong_activity_never_replace_missing_request(self):
        steps, _ = self.course("경기 전에 카페만", [place("밖", 37.53, 127), place("식당", 37.501, 126.99, "FOOD_OUT", "한식")])
        self.assertIsNone(steps)

    def test_food_condition_is_preserved_while_widening(self):
        steps, _ = self.course("경기 전에 초밥 먹고 갈래", [place("치킨", 37.5001, 126.99, "FOOD_OUT", "치킨"),
                      place("스시", 37.51, 126.99, "FOOD_OUT", "일식 초밥")])
        self.assertEqual(steps[0]["place"]["name"], "스시")

    def test_low_cost_cafe_does_not_turn_into_any_nearby_cafe(self):
        steps, _ = self.course("경기 전에 저가 카페만", [place("고가카페", 37.5001, 126.99),
                    place("메가MGC커피 잠실점", 37.51, 126.99)], cheap_cafe=True)
        self.assertEqual(steps[0]["place"]["name"], "메가MGC커피 잠실점")

    def test_activity_order_count_and_no_unsolicited_after_food(self):
        pool = [place("식당", 37.5001, 126.982, "FOOD_OUT", "한식"), place("카페", 37.5001, 126.99),
                place("공원", 37.501, 127, "WALK", "공원"), place("야식", 37.501, 127, "FOOD_OUT", "치킨")]
        steps, _ = self.course("경기 전 밥 먹고 커피 마시고 경기 후 산책만", pool)
        self.assertEqual([s["place"]["name"] if s["place"] else "GAME" for s in steps], ["식당", "카페", "GAME", "공원"])


class RouteContextTests(SimpleTestCase):
    def test_normalized_provider_url_reaches_course_candidate(self):
        document = {"id": "123", "place_name": "카페", "x": "126.988", "y": "37.501", "category_name": "카페",
                    "url": "http://place.map.kakao.com/123"}
        with patch.object(agent, "invoke_domain_tool", return_value={"places": [document]}):
            actual = agent._kakao_step("CAFE", WEST, 800, ANCHOR, slots.parse("카페"))
        self.assertEqual(actual[0]["placeUrl"], document["url"])

    def test_request_context_keeps_only_public_bounded_geometry(self):
        actual = _validate_context({"routePath": {**PATH, "secret": "drop", "points": [{**WEST, "secret": "drop"}, ANCHOR]}})
        self.assertNotIn("secret", str(actual))
        self.assertEqual(actual["routePath"]["points"], [WEST, ANCHOR])

    def test_bad_coordinates_and_oversized_paths_are_rejected(self):
        for points in ([WEST], [WEST] * 129, [{"lat": True, "lng": 127}, ANCHOR],
                       [{"lat": float("nan"), "lng": 127}, ANCHOR], [{"lat": 10 ** 1000, "lng": 127}, ANCHOR]):
            with self.subTest(points=str(points)[:60]), self.assertRaises(ValidationError):
                _validate_context({"routePath": {**PATH, "points": points}})

    def test_route_is_passed_to_course_and_cleared_when_user_changes_stadium(self):
        for question, expected in (("이 경로로 카페만 추천", True), ("이번에는 창원에서 코스 짜줘", False)):
            fake = Mock()
            fake.answer.return_value = {"answer": "ok"}
            with assistant.request_state("JAMSIL", question, route_path=PATH):
                assistant.plan_course(question, _course=fake)
            self.assertEqual("route_path" in fake.answer.call_args.kwargs, expected)

    def test_selected_route_entirely_outside_returns_no_course(self):
        path = {**PATH, "points": [{"lat": 37.54, "lng": 126.94}, {"lat": 37.54, "lng": 127}]}
        self.assertFalse(corridor.clip(corridor.paths_of(path), ANCHOR))

    def test_entire_pipeline_keeps_path_places_notice_and_saved_content_consistent(self):
        stadium = {**place("잠실야구장", **ANCHOR, category="STADIUM"), "key": "STADIUM"}
        wanted = place("먼카페", 37.51, 126.99)
        forbidden = place("반경밖카페", 37.5, 126.967)
        game = {"date": "2027-10-06", "time": "18:30", "home": "LG", "away": "삼성", "status": "scheduled"}
        mocks = {"load_schedule": ({}, 1), "find_game": (game, False, [game]), "embed_many": ([.1], [.2]),
                 "stadium_anchor": stadium, "_live_candidates": ([wanted, forbidden], {}), "search_places": [],
                 "invoke_domain_tool": {}, "_kakao_step": [], "call_llm": ('{"course": []}', 0)}
        with ExitStack() as stack:
            # Route geometry fixtures have no real shop rating/interior evidence.
            stack.enter_context(patch.object(agent.place_quality, "active", return_value=False))
            for name, result in mocks.items():
                stack.enter_context(patch.object(agent, name, return_value=result))
            stack.enter_context(patch.object(agent.transport, "info", return_value={"mode": "walk", "label": "도보", "taxi": False, "lines": []}))
            result = agent.answer("경기 전에 카페만", hint_stadium="JAMSIL", route_path=PATH)
        for places in (result["places"], result["coursePayload"]["stops"]):
            self.assertEqual([p["name"] for p in places], ["먼카페", "잠실야구장"])
        for text in (result["answer"], result["coursePayload"]["content"], result["approachNotice"]):
            self.assertIn("조건에 맞는 장소가 부족", text)
            self.assertNotIn("반경밖카페", text)
