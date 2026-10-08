"""코스 기본 경기 선택: 실제 제공자·DB 없이 일정 선택과 요청 경계를 검증한다."""
from datetime import date, datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase

from llm.v1.rag.course import agent as course


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 4, 16, 0, tzinfo=KST)


def game(day="2026-10-04", time="18:30", status="scheduled", stadium="GWANGJU", stadium_id=7):
    return {"game_date": day, "game_time": time, "status_code": status,
            "stadium_id": stadium_id, "stadium__stadium_code": stadium,
            "stadium__stadium_name_ko": "광주-KIA 챔피언스 필드",
            "home_team__team_name_ko": "KIA", "away_team__team_name_ko": "LG"}


class CourseScheduleToolTest(TestCase):
    def test_actual_upcoming_tool_and_consumer_accept_all_canonical_states(self):
        from baseball.models import Game, Stadium, Team
        from llm.tools.baseball import create_baseball_domain_tools
        stadium = Stadium.objects.create(id=980099, stadium_code="FIX", stadium_name_ko="테스트 구장", address="", longitude=127, latitude=37, collected_at=NOW)
        team = Team.objects.create(id=980099, team_code="FIX", team_name_ko="테스트")
        tool = next(t for t in create_baseball_domain_tools() if t.name == "get_games")
        now = datetime(2099, 10, 4, 16, tzinfo=KST)
        for index, status in enumerate(("scheduled", "PREV", "READY", "final", "cancelled", "postponed")):
            Game.objects.create(id=980090 + index, game_code=f"FIX-{index}", game_date=date(2099, 10, 4),
                                game_time=time(18, index), status_code=status, stadium=stadium, home_team=team, collected_at=NOW)
        for index, clock in enumerate((time(15), time(16))):
            Game.objects.create(id=980080 + index, game_code=f"PAST-{index}", game_date=now.date(),
                                game_time=clock, status_code="READY", stadium=stadium, home_team=team, collected_at=NOW)
        with patch("tving.service.get_game_range_freshness", return_value={}):
            result = tool.invoke({"start_date": "2099-10-04", "end_date": "2099-10-05", "stadium_id": stadium.id,
                                  "upcoming_only": True, "as_of": now.isoformat(), "limit": 3})
        self.assertEqual([r["status_code"] for r in result["items"]], ["scheduled", "PREV", "READY"])
        chosen, _, upcoming = course.find_game("GWANGJU", "", "2099-10-04", result, now=now, stadium_id=stadium.id)
        self.assertEqual(chosen["time"], "18:00")
        self.assertEqual([r["status"] for r in upcoming], ["scheduled", "PREV", "READY"])
        for row in result["items"]:
            with self.subTest(status=row["status_code"]):
                self.assertIsNotNone(course.find_game("GWANGJU", "", "2099-10-04", {"items": [row]}, now=now, stadium_id=stadium.id)[0])


class CourseScheduleTest(SimpleTestCase):
    def choose(self, rows, question="", now=NOW):
        return course.find_game("GWANGJU", question, now.date().isoformat(), {"items": rows}, now=now)

    def test_nearest_future_game_sorted_and_filtered(self):
        rows = [game("2026-10-06"), game(time="14:00"), game(time="17:00", status="cancelled"),
                game(time="17:00", status="postponed"), game(time="17:00", status="live"),
                game(time="17:00", status="final"), game(time="17:00", stadium="JAMSIL"),
                game(time="19:00"), game(time="18:30")]
        chosen, assumed, upcoming = self.choose(rows)
        self.assertEqual(chosen["time"], "18:30")
        self.assertFalse(assumed)
        self.assertEqual([g["time"] for g in upcoming], ["18:30", "19:00", "18:30"])

    def test_at_start_time_is_not_upcoming(self):
        chosen, _, _ = self.choose([game(time="16:00"), game("2026-10-05")])
        self.assertEqual(chosen["date"], "2026-10-05")

    def test_korea_midnight_boundary(self):
        now = datetime(2026, 10, 5, 0, 1, tzinfo=KST)
        chosen, _, _ = self.choose([game(time="23:59"), game("2026-10-05")], now=now)
        self.assertEqual(chosen["date"], "2026-10-05")

    def test_explicit_date_overrides_nearest(self):
        chosen, assumed, _ = self.choose([game(), game("2026-10-06")], "2026-10-06")
        self.assertEqual(chosen["date"], "2026-10-06")
        self.assertFalse(assumed)

    def test_day_only_and_month_day_select_requested_game_not_nearest(self):
        rows = [game("2026-10-09"), game("2026-10-15", time="14:00")]
        for question in ("15일에 광주 코스 짜줘", "10월 15일 광주 코스", "2026-10-15 광주 코스"):
            with self.subTest(question=question):
                chosen, assumed, _ = self.choose(rows, question)
                self.assertEqual((chosen["date"], chosen["time"]), ("2026-10-15", "14:00"))
                self.assertFalse(assumed)

    def test_explicit_date_beats_history_and_incidental_today(self):
        history = [{"role": "user", "content": "10월 9일 광주 코스"}]
        self.assertEqual(course.requested_date("15일로 바꿔줘", history, "2026-10-08"), "2026-10-15")
        self.assertEqual(course.requested_date("오늘 말고 10월 15일", history, "2026-10-08"), "2026-10-15")
        self.assertEqual(course.requested_date("오늘 말고 15일에", history, "2026-10-08"), "2026-10-15")

    def test_day_only_month_boundaries_and_durations(self):
        parse = course.structured.date_in
        self.assertEqual(parse("다음 달 15일", "2026-12-08"), "2027-01-15")
        self.assertEqual(parse("지난달 15일", "2026-01-08"), "2025-12-15")
        self.assertEqual(parse("이번 달 5일", "2026-10-08"), "2026-10-05")
        for text in ("2박3일 코스", "2박 3일 코스", "3일 동안 여행", "15일 후에", "3일째", "3일간"):
            with self.subTest(text=text):
                self.assertIsNone(parse(text, "2026-10-08"))
        self.assertEqual(parse("2박 3일 여행을 15일에 시작", "2026-10-08"), "2026-10-15")

    def test_invalid_date_requests_clarification_without_search_or_fallback(self):
        for question in ("2026년 2월 30일 코스", "2월 30일 광주 코스", "32일에 갈래"):
            with self.subTest(question=question), patch.object(course, "_answer") as generate:
                result = course.answer(question, hint_stadium="GWANGJU")
                self.assertEqual(result["route"], "course:invalid_date")
                self.assertEqual(result["places"], [])
                generate.assert_not_called()
        history = [{"role": "user", "content": "2월 30일"}]
        self.assertIsNone(course.requested_date("다시 코스 짜줘", history, "2026-10-08"))

    def test_day_only_no_game_does_not_generate_for_a_different_date(self):
        with patch.object(course, "load_schedule", return_value=({"items": []}, 7)) as schedule, \
                patch.object(course, "embed_many") as embed:
            result = course.answer("15일 광주 코스 짜줘", hint_stadium="GWANGJU")
        self.assertEqual(schedule.call_args.args[1][-3:], "-15")
        self.assertIn(":no_game:", result["route"])
        self.assertEqual(result["places"], [])
        embed.assert_not_called()

    def test_missing_explicit_date_does_not_substitute_another_game(self):
        chosen, assumed, _ = self.choose([game()], "10월 6일")
        self.assertIsNone(chosen)
        self.assertFalse(assumed)

    def test_official_stadium_code_matches_renamed_venue(self):
        row = game(stadium="MUNHAK")
        row["stadium__stadium_name_ko"] = "인천 SSG 랜더스필드"
        chosen, _, _ = course.find_game("MUNHAK", "", "2026-10-04", {"items": [row]}, now=NOW)
        self.assertIsNotNone(chosen)

    def test_no_scheduled_game_does_not_fabricate_one(self):
        self.assertEqual(self.choose([game(status="cancelled")]), (None, True, []))

    def test_date_history_and_explicit_year(self):
        history = [{"role": "user", "content": "2027년 4월 3일 광주 코스"},
                   {"role": "assistant", "content": "10월 6일 경기도 있어요"}]
        self.assertEqual(course.requested_date("카페도 들를게", history, "2026-10-04"), "2027-04-03")
        self.assertEqual(course.requested_date("2027-04-04로 바꿔줘", history, "2026-10-04"), "2027-04-04")
        self.assertIsNone(course.requested_date("날짜는 미정이야", history, "2026-10-04"))
        self.assertEqual(course.requested_date("가장 가까운 식당으로 해줘", history, "2026-10-04"), "2027-04-03")
        history.append({"role": "user", "content": "날짜는 미정으로 할게"})
        self.assertIsNone(course.requested_date("카페도 들를게", history, "2026-10-04"))

    def test_query_filters_stadium_before_limit_and_crosses_off_season(self):
        with patch.object(course, "invoke_domain_tool", side_effect=[{"item": {"id": 7}}, {"items": []}]) as invoke:
            course.load_schedule("GWANGJU", None, NOW)
        args = invoke.call_args.args[2]
        self.assertEqual(args["stadium_id"], 7)
        self.assertEqual(args["end_date"], "2027-10-05")
        self.assertTrue(args["upcoming_only"])

    def test_explicit_date_query_does_not_use_automatic_filter(self):
        with patch.object(course, "invoke_domain_tool", side_effect=[{"item": {"id": 7}}, {"items": []}]) as invoke:
            course.load_schedule("GWANGJU", "2026-10-06", NOW)
        args = invoke.call_args.args[2]
        self.assertEqual(args["start_date"], args["end_date"])
        self.assertFalse(args["upcoming_only"])

    def test_unavailable_schedule_distinguishes_empty_and_failure_without_fake_time(self):
        for schedule, expected in [({"items": []}, "예정 경기를 확인하지 못했어요"),
                                   ({"items": [], "warning": "경기 일정 조회에 실패했어요.", "lookup_failed": True}, "조회에 실패")]:
            with self.subTest(expected=expected), patch.object(course, "load_schedule", return_value=(schedule, 7)), \
                    patch.object(course, "embed_many") as embed:
                result = course.answer("코스 짜줘", hint_stadium="GWANGJU")
            self.assertIn(expected, result["answer"])
            self.assertIn(course.DATE_RECOMMENDATION, result["answer"])
            self.assertNotIn("18:30", result["answer"])
            embed.assert_not_called()
