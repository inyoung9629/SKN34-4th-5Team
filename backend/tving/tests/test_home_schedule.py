from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from baseball.models import ScheduleDay, Team
from tving.relational import persist_month
from tving.service import with_next_games


class HomeScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for pk, code in ((901, "KIA"), (902, "LG")):
            Team.objects.get_or_create(team_code=code, defaults={"id": pk, "team_name_ko": code})

    def setUp(self):
        self.synced = timezone.now() - timedelta(hours=1)
        self.snapshot = {
            "date": "2026-10-02", "games": [], "standings": [{"teamCode": "LG", "rank": 1}],
            "fetchedAt": timezone.now().isoformat(), "stale": False, "warning": None,
            "source": {"name": "TVING", "url": "https://www.tving.com/sports/kbo"},
        }

    def save_day(self, day, *statuses):
        games = [{
            "id": f"{day}-{index}", "date": day, "time": f"{14 + index}:00",
            "stadium": "잠실", "status": status, "statusLabel": status,
            "home": {"code": "LG", "name": "LG"}, "away": {"code": "HT", "name": "KIA"},
        } for index, status in enumerate(statuses)]
        persist_month({"month": day[:7], "games": games,
                       "days": [{"date": day, "status": "ready", "gameCount": len(games)}]}, self.synced)

    def test_off_day_shows_all_next_games_and_preserves_standings_date(self):
        self.save_day("2026-10-03", "cancelled")
        self.save_day("2026-10-04", "scheduled", "cancelled", "scheduled")
        self.save_day("2026-10-05", "scheduled")
        original = deepcopy(self.snapshot)
        with patch("tving.service._provider_json", side_effect=AssertionError("DB lookup only")):
            result = with_next_games(self.snapshot)
        self.assertEqual([game["time"] for game in result["games"]], ["14:00", "16:00"])
        self.assertEqual({game["date"] for game in result["games"]}, {"2026-10-04"})
        self.assertEqual(result["date"], "2026-10-02")
        self.assertEqual(result["standings"], self.snapshot["standings"])
        self.assertEqual(parse_datetime(result["fetchedAt"]), self.synced)
        self.assertEqual(self.snapshot, original)

    def test_cross_month_and_year_find_first_scheduled_day(self):
        self.snapshot["date"] = "2026-12-31"
        self.save_day("2027-01-01", "final")
        self.save_day("2027-03-22", "scheduled")
        result = with_next_games(self.snapshot)
        self.assertEqual(result["games"][0]["date"], "2027-03-22")

    def test_today_results_remain_visible_without_fallback_query(self):
        self.snapshot["games"] = [{"id": "today", "status": "final"}]
        with self.assertNumQueries(0):
            self.assertIs(with_next_games(self.snapshot), self.snapshot)

    def test_no_future_schedule_returns_empty_without_inventing_games(self):
        self.save_day("2026-10-03", "cancelled")
        self.assertEqual(with_next_games(self.snapshot)["games"], [])

    def test_games_removed_from_source_calendar_are_not_recommended(self):
        self.save_day("2026-10-03", "scheduled")
        ScheduleDay.objects.filter(date="2026-10-03").update(status="empty", game_count=0, game_codes=[])
        self.save_day("2026-10-04", "scheduled")
        self.assertEqual(with_next_games(self.snapshot)["games"][0]["date"], "2026-10-04")

    def test_daily_api_requires_opt_in_for_home_fallback(self):
        self.save_day("2026-10-03", "scheduled")
        with patch("tving.views._run_local_crawler_if_needed"), \
                patch("tving.views.DailyView.refresh", return_value=self.snapshot):
            client = APIClient()
            daily = client.get("/api/v1/tving/daily/?date=2026-10-02")
            home = client.get("/api/v1/tving/daily/?date=2026-10-02&next_if_empty=true")
        self.assertEqual((daily.status_code, home.status_code), (200, 200))
        self.assertEqual(daily.data["data"]["games"], [])
        self.assertEqual(home.data["data"]["games"][0]["date"], "2026-10-03")
        self.assertEqual(home.data["data"]["date"], "2026-10-02")
        self.assertEqual(home["Cache-Control"], "no-store")
