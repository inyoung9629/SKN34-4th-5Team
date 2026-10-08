import json
from contextlib import ExitStack
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from travel.stadium_facilities import _root
from llm.tests.test_course_editing import CURRENT, plan
from llm.v1.rag.course import agent, arrival, editing, geo, memory
from llm.v1.rag.course.default_origins import STATIONS, station_origin
from llm.v2.agent.course_output import public_course


class DefaultOriginTests(SimpleTestCase):
    def setUp(self):
        self.centers = json.loads((_root() / "stadium_locations.json").read_text(encoding="utf-8"))["stadiums"]
        self.anchor = self.centers["DAEJEON"]
        self.invoke = Mock(return_value={"places": []})

    def resolve(self, question="카페 들르는 코스", history=None, supplied=None, code="DAEJEON"):
        return arrival.resolve_course_origin(question, history or [], supplied, self.anchor, self.invoke, code)

    def test_all_nine_defaults_are_within_stadium_radius_and_need_no_search(self):
        self.assertEqual(set(STATIONS), set(self.centers))
        for code, center in self.centers.items():
            with self.subTest(code=code):
                point, name, error, notice = arrival.resolve_course_origin("코스 짜줘", [], None, center, self.invoke, code)
                self.assertLessEqual(geo._dist(point, center), arrival.RADIUS_M)
                self.assertEqual(name, STATIONS[code]["name"])
                self.assertEqual(error, "")
                self.assertIn(name, notice)
        self.invoke.assert_not_called()
        self.assertEqual(station_origin("SUWON")["name"], "화서역 6번 출구")
        self.assertEqual(station_origin("DAEGU")["name"], "수성알파시티역")

    def test_gps_or_named_pin_wins_over_default_and_old_history(self):
        for supplied in ({"lat": 36.32, "lng": 127.43}, {"lat": 36.32, "lng": 127.43, "name": "내가 찍은 출발지"}):
            with self.subTest(supplied=supplied):
                point, _, error, notice = self.resolve(supplied=supplied, history=[{"role": "user", "content": "서울역에서 출발"}])
                self.assertEqual(point, arrival.coordinate(supplied))
                self.assertEqual((error, notice), ("", ""))
        self.invoke.assert_not_called()

    def test_current_named_origin_and_user_history_are_resolved_before_default(self):
        self.invoke.return_value = {"places": [{"place_name": "서대전역", "y": "36.32", "x": "127.4"}]}
        for question, history in (("서대전역에서 출발", []), ("카페 코스", [{"role": "user", "content": "서대전역에서 출발"}])):
            point, name, error, notice = self.resolve(question, history)
            self.assertEqual((point, name), ({"lat": 36.32, "lng": 127.4}, "서대전역"))
            self.assertEqual((error, notice), ("", ""))

    def test_explicitly_missing_origin_uses_default_without_searching_absence_words(self):
        for question in ("출발지는 없어. 카페 코스", "출발지 없이 코스 짜줘", "출발지는 아직 미정이야. 코스 짜줘"):
            with self.subTest(question=question):
                point, name, error, notice = self.resolve(question, [{"role": "user", "content": "서울역에서 출발"}])
                self.assertEqual(name, "대전역")
                self.assertEqual(error, "")
                self.assertTrue(point and notice)
        self.invoke.assert_not_called()

    def test_unresolved_or_ambiguous_named_origin_is_not_replaced_with_station(self):
        for places in ([], [{"place_name": "중앙역", "y": "37.5", "x": "127"},
                            {"place_name": "중앙역", "y": "35.2", "x": "129"}]):
            self.invoke.return_value = {"places": places}
            point, name, error, notice = self.resolve("중앙역에서 출발")
            self.assertIsNone(point)
            self.assertEqual(name, "중앙역")
            self.assertTrue(error)
            self.assertEqual(notice, "")

    def test_relative_origin_needs_location_and_stadium_origin_is_explicit(self):
        for question in ("집에서 출발", "현재 위치에서 시작", "출발지는 회사야"):
            with self.subTest(question=question):
                # '회사야' is a named unresolved expression; either way, never insert a default.
                point, _, error, notice = self.resolve(question)
                self.assertIsNone(point)
                self.assertTrue(error)
                self.assertEqual(notice, "")
        supplied = {"lat": 36.32, "lng": 127.43}
        self.assertEqual(self.resolve("현재 위치에서 출발", supplied=supplied)[0], supplied)
        self.assertEqual(self.resolve("구장에서 출발", supplied=supplied)[0], arrival.coordinate(self.anchor))

    def test_unknown_stadium_does_not_guess_a_default(self):
        self.assertEqual(self.resolve(code="OTHER"), (None, "", "", ""))

    def test_independent_requests_do_not_share_default_or_explicit_origins(self):
        first = self.resolve(code="GWANGJU")
        station_origin("GWANGJU")["name"] = "변경 시도"
        second = self.resolve(code="JAMSIL")
        self.assertEqual((first[1], second[1]), ("광주역", "종합운동장역"))
        self.assertEqual(station_origin("GWANGJU")["name"], "광주역")

    def test_existing_course_edits_and_explicit_origin_deletion_do_not_insert_default(self):
        with patch.object(editing, "interpret", return_value=plan("origin", [], clear_origin=True)), \
                patch.object(editing, "answer", return_value={"places": []}) as edit, \
                patch.object(arrival, "resolve_course_origin") as resolve:
            agent.answer("출발지 삭제해", hint_stadium="JAMSIL", current_course=CURRENT, course_request="EDIT")
        self.assertIsNone(edit.call_args.args[4])
        resolve.assert_not_called()


class DefaultOriginPipelineTests(SimpleTestCase):
    def generate(self, **kwargs):
        station = station_origin("DAEJEON")
        center = json.loads((_root() / "stadium_locations.json").read_text(encoding="utf-8"))["stadiums"]["DAEJEON"]
        base = {"placeUrl": "", "address": "", "distance": 500, "dist": .5, "doc_id": ""}
        cafe = {**base, "name": "역에서 구장 가는 길 카페", "lat": 36.327, "lng": 127.432,
                "placeId": "test-cafe", "category": "CAFE", "detail": "카페"}
        stadium = {**base, "name": "대전 한화생명 볼파크", "lat": center["lat"], "lng": center["lng"],
                   "placeId": None, "category": "STADIUM", "key": "STADIUM"}
        game = {"date": "2027-10-06", "time": "18:30", "home": "한화", "away": "삼성", "status": "scheduled"}
        mocks = {"load_schedule": ({}, 1), "find_game": (game, False, [game]), "embed_many": ([.1], [.2]),
                 "stadium_anchor": stadium, "_live_candidates": ([cafe], {}), "search_places": [],
                 "invoke_domain_tool": {}, "_kakao_step": [], "call_llm": ('{"course": []}', 0)}
        with ExitStack() as stack:
            for name, result in mocks.items():
                stack.enter_context(patch.object(agent, name, return_value=result))
            stack.enter_context(patch.object(agent.transport, "info", return_value={"mode": "walk", "label": "도보", "taxi": False, "lines": []}))
            build = stack.enter_context(patch.object(agent, "build_origin_course", wraps=agent.build_origin_course))
            result = agent.answer("경기 전에 카페만 들르는 코스", hint_stadium="DAEJEON", **kwargs)
        return result, build, station

    def test_default_reaches_planner_map_checkpoint_and_saved_description(self):
        with patch.object(editing, "interpret", return_value=plan("new", [])):
            result, build, station = self.generate(course_request="NEW", course_memory=memory.empty())
        self.assertEqual(build.call_args.args[0], arrival.coordinate(station))
        self.assertEqual(result["origin"], station)
        self.assertEqual(result["writerState"]["origin"], station)
        self.assertEqual(result["courseMemory"]["origin"], station)
        self.assertEqual(public_course(result)["origin"], station)
        self.assertEqual([p["category"] for p in result["places"]], ["CAFE", "STADIUM"])
        for content in (result["answer"], result["approachNotice"], result["coursePayload"]["content"]):
            self.assertIn("출발지가 지정되지 않아 대전역", content)

    def test_selected_drawn_path_start_is_not_replaced_with_default(self):
        start = {"lat": 36.326, "lng": 127.432}
        path = {"points": [start, {"lat": 36.3161788, "lng": 127.43127175}], "source": "drawn"}
        result, build, _ = self.generate(route_path=path)
        self.assertEqual(build.call_args.args[0], start)
        self.assertEqual(arrival.coordinate(result["origin"]), start)
        self.assertNotIn("출발지가 지정되지 않아", result["answer"])
