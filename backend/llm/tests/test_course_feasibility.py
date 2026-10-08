from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from llm.v1.rag.course import feasibility, timeline, save, slots, agent

KST = ZoneInfo("Asia/Seoul")
COURSE = [{"key": "food", "phase": "BEFORE"}, {"key": "cafe", "phase": "BEFORE"},
          {"key": "stadium", "phase": "GAME"}, {"key": "park", "phase": "AFTER"}]
LOOKUP = {"food": {"name": "든든식당", "category": "FOOD"},
          "cafe": {"name": "저가카페", "category": "CAFE"},
          "stadium": {"name": "잠실야구장", "category": "STADIUM"},
          "park": {"name": "산책공원", "category": "WALK"}}


class CourseFeasibilityTest(SimpleTestCase):
    def warning(self, hour=12, minute=50, *, day="2026-10-04", question="", now=None):
        tl = timeline.build(COURSE, LOOKUP, "14:00", [10, 10, 15])
        return feasibility.time_warning(COURSE, LOOKUP, tl, {"date": day, "time": "14:00"}, question,
                                        now or datetime(2026, 10, 4, hour, minute, tzinfo=KST), [10, 10])

    def test_second_stop_no_longer_fits_but_course_and_recommended_times_remain(self):
        before = deepcopy(COURSE)
        warning = self.warning()
        self.assertIn("2번째 장소(저가카페)", warning)
        self.assertIn("14:40", warning)
        self.assertIn("약 40분 늦어요", warning)
        self.assertIn("역산한 권장 시각", warning)
        self.assertEqual(COURSE, before)

    def test_first_stop_no_longer_fits(self):
        self.assertIn("1번째 장소(든든식당)", self.warning(13, 10))

    def test_kickoff_not_recommended_early_entry_is_the_cutoff(self):
        # 식사 50 + 이동 10 + 커피 40 + 구장 이동 10 = 110분. 정확히 경기 시작에 도착하는 경계도 포함한다.
        self.assertEqual(self.warning(12, 9), "")
        self.assertEqual(self.warning(12, 10), "")
        self.assertIn("2번째 장소", self.warning(12, 11))

    def test_future_game_without_known_arrival_does_not_compare_clock_times_across_days(self):
        self.assertEqual(self.warning(23, 55, day="2026-10-05"), "")

    def test_user_start_time_applies_to_future_game_day(self):
        warning = self.warning(day="2026-10-05", question="오후 12시 50분부터 코스 시작할게")
        self.assertIn("2026-10-05 12:50", warning)
        self.assertIn("2번째 장소", warning)

    def test_already_past_user_start_is_clamped_to_now(self):
        self.assertIn("2026-10-04 13:10", self.warning(13, 10, question="오전 11시 도착할게"))

    def test_current_time_is_converted_to_korea_and_seconds_round_up(self):
        self.assertIn("2026-10-04 12:51", self.warning(now=datetime(2026, 10, 4, 3, 50, 1, tzinfo=timezone.utc)))

    def test_start_parser_does_not_confuse_game_time_or_invalid_times_with_arrival(self):
        for text, expected in [("오후 1시 반 도착", 810), ("13:30부터", 810), ("오전 12시 출발", 0),
                               ("오후 12시 도착", 720), ("경기는 오후 2시 시작", None),
                               ("경기 14:00부터 시작", None), ("25:70 도착", None),
                               ("경기는 오후 2시 시작이고 오후 1시 도착할게", 780)]:
            with self.subTest(text=text):
                self.assertEqual(feasibility.start_minute(text), expected)

    def test_after_only_course_needs_no_before_game_warning(self):
        course = COURSE[2:]
        tl = timeline.build(course, LOOKUP, "14:00", [15])
        self.assertEqual(feasibility.time_warning(course, LOOKUP, tl, {"date": "2026-10-04", "time": "14:00"},
                                                  "", datetime(2026, 10, 4, 13, 55, tzinfo=KST), []), "")

    def test_short_on_time_keeps_requested_meal_and_cafe(self):
        plan = slots.parse("시간이 촉박해도 경기 전에 식사하고 카페도 들르고 싶어")
        self.assertEqual(plan["spare"], "tight")
        self.assertEqual(agent.plan_steps(plan, False)[0], ["FOOD", "CAFE"])

    def test_saved_course_keeps_timing_warning_and_all_places(self):
        warning = self.warning()
        places = [{**LOOKUP[c["key"]], "phase": c["phase"], "lat": 37.5, "lng": 127.0} for c in COURSE]
        payload = save.course_payload(places, time_warning=warning)
        self.assertEqual(len(payload["stops"]), 4)
        self.assertIn(warning, payload["content"])
