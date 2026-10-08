from datetime import date, timedelta
from unittest.mock import call, patch

from django.test import TestCase
from django.utils import timezone

from baseball.models import Game, ScheduleDay, Team
from crawling.common.cron_tving import collect_schedule
from tving.parsers import TvingValidationError
from tving.relational import read_month
from tving.service import TvingUpstreamError, get_game_range_freshness


def calendar_payload(days):
    return {"code": "0000", "data": {"calendar": days}}


def schedule_payload(day="20261005"):
    return {"code": "0000", "data": {"bands": [{
        "bandType": "SPORTS_SCHEDULE", "focusDate": day,
        "items": [{
            "code": f"{day}HTLG02026", "dateTime": f"{day}1400",
            "status": "PREV", "stadium": "잠실",
            "away": {"code": "HT", "name": "KIA"},
            "home": {"code": "LG", "name": "LG"},
        }],
    }]}}


class ScheduleCrawlerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for pk, code, name in ((901, "KIA", "KIA 타이거즈"), (902, "LG", "LG 트윈스")):
            Team.objects.get_or_create(team_code=code, defaults={"id": pk, "team_name_ko": name})

    def setUp(self):
        self.setup_patch = patch("crawling.common.cron_tving._setup")
        self.setup_patch.start()
        self.addCleanup(self.setup_patch.stop)

    def test_collection_repairs_incomplete_month_and_removes_false_warning(self):
        old_sync = timezone.now() - timedelta(days=1)
        ScheduleDay.objects.create(date="2026-10-05", status="empty", game_count=0,
                                   game_codes=[], source_fetched_at=old_sync, last_synced_at=old_sync)
        self.assertIsNone(read_month("2026-10", "2026-10-05"))
        with patch("tving.service._provider_json", side_effect=[calendar_payload([5]), schedule_payload()]) as provider:
            self.assertEqual(collect_schedule("2026-10"), 1)
        self.assertEqual(provider.call_args_list, [
            call("/kbo/schedule/day", {"date": "202610"}),
            call("/kbo/schedule", {"date": "20261005"}),
        ])
        month = read_month("2026-10", "2026-10-05")
        self.assertEqual(len(month["days"]), 31)
        self.assertEqual(len(month["games"]), 1)
        self.assertEqual(month["days"][4], {"date": "2026-10-05", "status": "ready", "gameCount": 1})
        self.assertEqual(ScheduleDay.objects.filter(status="empty", game_count=0, game_codes=[]).count(), 30)
        # 채팅의 최신성 검사는 수집된 DB만 읽고 추가 API 요청을 하지 않는다.
        with patch("tving.service._provider_json", side_effect=AssertionError("unexpected live lookup")):
            self.assertEqual(get_game_range_freshness(date(2026, 10, 5), date(2026, 10, 5)),
                             {"stale": False, "warning": None})

    def test_empty_leap_month_is_complete_without_daily_requests(self):
        with patch("tving.service._provider_json", return_value=calendar_payload([])) as provider:
            self.assertEqual(collect_schedule("2024-02"), 0)
        provider.assert_called_once_with("/kbo/schedule/day", {"date": "202402"})
        month = read_month("2024-02", "2024-02-01")
        self.assertEqual(len(month["days"]), 29)
        self.assertEqual(month["days"][-1], {"date": "2024-02-29", "status": "empty", "gameCount": 0})
        self.assertEqual(month["games"], [])

    def test_failed_daily_fetch_preserves_existing_month(self):
        with patch("tving.service._provider_json", side_effect=[calendar_payload([5]), schedule_payload()]):
            collect_schedule("2026-10")
        before_days = list(ScheduleDay.objects.order_by("date").values())
        before_games = list(Game.objects.order_by("pk").values())
        with patch("tving.service._provider_json", side_effect=[
            calendar_payload([5, 6]), schedule_payload(), TvingUpstreamError("unavailable"),
        ]):
            with self.assertRaises(TvingUpstreamError):
                collect_schedule("2026-10")
        self.assertEqual(list(ScheduleDay.objects.order_by("date").values()), before_days)
        self.assertEqual(list(Game.objects.order_by("pk").values()), before_games)

    def test_invalid_calendar_does_not_mark_month_empty(self):
        with patch("tving.service._provider_json", return_value={"code": "0000", "data": {}}):
            with self.assertRaises(TvingValidationError):
                collect_schedule("2026-10")
        self.assertFalse(ScheduleDay.objects.exists())
