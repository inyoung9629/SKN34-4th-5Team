from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from baseball.stadium_locations import reviewed_venue
from llm.v1.rag.course import agent, editing, entry_timing, feasibility, progress, timeline
from travel.stadium_food import food_candidates


def stadium(code="JAMSIL"):
    return {**reviewed_venue(code), "key": "game", "visitId": "game", "category": "STADIUM", "name": "구장", "phase": "GAME"}


def tenant(code="JAMSIL", category="FOOD", key="food"):
    place = next(p for p in food_candidates(code) if p["category"] == category)
    return {**place, "key": key, "visitId": key, "phase": "BEFORE"}


def course_of(points):
    return [{"key": p["key"], "phase": p["phase"]} for p in points], {p["key"]: p for p in points}


def build(points, legs=None, **kwargs):
    return timeline.build(*course_of(points), "18:30", legs, **kwargs)


class CourseEntryTimingTests(SimpleTestCase):
    def test_default_stadium_arrival_is_twenty_minutes_before_game(self):
        points = [dict(key="food", category="FOOD", phase="BEFORE"),
                  dict(key="cafe", category="CAFE", phase="BEFORE"), stadium()]
        result = build(points, [10, 10])
        self.assertEqual([r["time"] for r in result["rows"]], ["16:20", "17:20", "18:10"])
        self.assertEqual(result["gameStart"], "18:30")
        self.assertEqual(result["gameEnd"], "21:40")
        self.assertEqual(build([stadium()])["rows"][0]["time"], "18:10")

    def test_collected_food_and_cafes_in_all_nine_stadiums_lead_directly_to_game(self):
        for code in ("JAMSIL", "GOCHEOK", "MUNHAK", "SUWON", "DAEJEON", "GWANGJU", "DAEGU", "CHANGWON", "SAJIK"):
            for category in ("FOOD", "CAFE"):
                with self.subTest(code=code, category=category):
                    points = [tenant(code, category), stadium(code)]
                    result = build(points, [13])  # Display pin's street route must not shift arrival.
                    food, game = result["rows"]
                    self.assertEqual((food["time"], food["until"], food["stayMin"], food["legMin"]), ("18:00", "18:30", 30, 0))
                    self.assertEqual(game["time"], "18:30")
                    lines = timeline.text_lines(*course_of(points), result)
                    self.assertIn("경기 관람", lines[1])
                    self.assertNotIn("입장", lines[1])

    def test_consecutive_internal_stops_share_thirty_minutes_and_preserve_external_approach(self):
        outside = {"key": "walk", "category": "WALK", "phase": "BEFORE"}
        points = [outside, tenant(), tenant(category="CAFE", key="cafe"), stadium()]
        original_legs = [{"minutes": m, "meters": 400, "by": "walk", "estimated": True} for m in (12, 7, 8)]
        legs = timeline.scheduled_legs(points, original_legs)
        self.assertEqual([leg["minutes"] for leg in legs], [12, 0, 0])
        self.assertEqual([leg["minutes"] for leg in original_legs], [12, 7, 8])
        result = build(points, [12, 7, 8])
        self.assertEqual([r["time"] for r in result["rows"]], ["17:08", "18:00", "18:15", "18:30"])

    def test_leaving_stadium_before_game_restores_normal_arrival_and_duration(self):
        points = [tenant(), {"key": "outside", "phase": "BEFORE", "category": "CAFE"}, stadium()]
        result = build(points, [10, 10])
        self.assertFalse(result["internalFoodBeforeGame"])
        self.assertEqual(result["rows"][0]["stayMin"], 50)
        self.assertEqual(result["rows"][-1]["time"], "18:10")

    def test_internal_rule_requires_catalogue_identity_and_matching_stadium(self):
        source = tenant()
        for invalid in ({**source, "placeId": "kakao:fake", "source": "MYSEATCHECK", "scope": "internal"},
                        {**source, "name": "가짜 매장"}, {**source, "lat": source["lat"] + .01}, tenant("GWANGJU")):
            self.assertFalse(build([invalid, stadium()])["internalFoodBeforeGame"])
        after = {**source, "phase": "AFTER"}
        result = build([stadium(), after], [5])
        self.assertEqual(result["rows"][1]["time"], "21:45")
        self.assertEqual(result["rows"][1]["stayMin"], 50)

    def test_explicit_arrival_or_stay_takes_priority(self):
        self.assertEqual(build([stadium()], entry_minute=17 * 60)["rows"][0]["time"], "17:00")
        result = build([tenant(), stadium()], [12], entry_minute=17 * 60 + 30)
        self.assertEqual([r["time"] for r in result["rows"]], ["17:30", "18:30"])
        self.assertEqual(result["rows"][0]["stayMin"], 60)
        result = build([{**tenant(), "stayOverride": 45}, stadium()], [12])
        self.assertEqual(result["rows"][0]["time"], "17:45")
        self.assertEqual(result["rows"][0]["stayMin"], 45)
        result = build([{**tenant(), "stayOverride": 15}, stadium()], [12], entry_minute=17 * 60)
        self.assertEqual((result["rows"][0]["time"], result["rows"][0]["until"]), ("17:00", "17:15"))
        self.assertEqual(result["rows"][1]["time"], "18:30")

    def test_explicit_relative_and_clock_requests_do_not_confuse_game_or_cafe_time(self):
        for question, expected in (("경기 시작 40분 전까지 구장에 도착", 17 * 60 + 50),
                                   ("구장 입장은 경기 시작 한 시간 전에", 17 * 60 + 30),
                                   ("구장에 오후 5시 도착", 17 * 60),
                                   ("오후 5시까지 구장에 도착", 17 * 60),
                                   ("입장 시간을 17:15로", 17 * 60 + 15),
                                   ("경기는 18:30 시작, 카페는 경기 30분 전에 도착", None),
                                   ("경기 시작 18:30에 맞춰 입장 코스", None),
                                   ("구장에 들어가기 전 카페에서 30분 쉬자", None)):
            with self.subTest(question=question):
                self.assertEqual(entry_timing.requested_entry(question, "18:30"), expected)
        history = [{"role": "user", "content": "구장에 오후 5시 도착"},
                   {"role": "assistant", "content": "구장에 오후 4시 도착"}]
        self.assertEqual(entry_timing.requested_entry("카페 삭제", "18:30", history), 17 * 60)
        self.assertEqual(entry_timing.requested_entry("경기 10분 전 입장", "18:30", history), 18 * 60 + 20)
        self.assertIsNone(entry_timing.requested_entry("입장은 기본으로", "18:30", history))
        for question in ("구장에 오후 5시 도착", "오후 5시까지 구장에 도착"):
            self.assertIsNone(feasibility.start_minute(question))

    def test_generation_and_editing_use_identical_internal_times_and_travel(self):
        points = [tenant(), stadium()]
        actual = {"legs": [{"status": "ok", "distance": 960, "seconds": 720}]}
        ranker = Mock(cache={})
        ranker.known_legs.return_value = [None]
        with patch.object(agent, "invoke_domain_tool", return_value=actual):
            generated = agent.course_timing(points, "18:30", "walk", ranker)
            current = {"places": points, "travelMode": "walk", "legModes": {}}
            edited = editing.rebuild(deepcopy(points), current, "JAMSIL", None, "코스 수정", [],
                _availability_check=False, _game={"date": "2030-10-07", "time": "18:30", "home": "LG", "away": "두산"})
        self.assertEqual([p["time"] for p in edited["places"]], [r["time"] for r in generated["tl"]["rows"]])
        self.assertEqual([p["until"] for p in edited["places"]], ["18:30", "21:40"])
        self.assertEqual(generated["legs"][0]["minutes"], 0)
        self.assertEqual(edited["travel"]["legs"][0]["minutes"], 0)
        self.assertNotIn("도보 약 12분", edited["answer"])
        self.assertIn("별도 구장 이동 시간을 더하지", edited["answer"])

    def test_delays_preserve_internal_game_transition_and_warn_when_late(self):
        points = [tenant(), stadium()]
        tl = build(points, [12])
        for place, row in zip(points, tl["rows"]):
            place.update(time=row["time"], until=row["until"])
        changed, _, warning, _ = progress.apply(points, {"places": deepcopy(points)}, tl, {"operation": "delay", "delay_minutes": 15})
        self.assertEqual(changed["rows"][0]["time"], "18:15")
        self.assertEqual(changed["rows"][1]["time"], "18:45")
        self.assertTrue(warning)
        changed, _, _, _ = progress.apply(points, {"places": deepcopy(points)}, tl, {"operation": "game_delay", "delay_minutes": 30})
        self.assertEqual(changed["rows"][1]["time"], "18:30")
        self.assertEqual(changed["gameEnd"], "22:10")
