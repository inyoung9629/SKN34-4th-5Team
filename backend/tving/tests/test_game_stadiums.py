from copy import deepcopy
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from baseball.models import Game, Stadium, Team
from llm.v1.rag.course import agent as course
from tving.relational import persist_daily, persist_month, stadium_for


class GameStadiumTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for pk, code in ((901, "HANWHA"), (902, "NC")):
            Team.objects.get_or_create(team_code=code, defaults={"id": pk, "team_name_ko": code})
        cls.stadium, _ = Stadium.objects.get_or_create(stadium_code="DAEJEON", defaults={
            "id": 903, "stadium_name_ko": "대전 한화생명 볼파크", "address": "대전",
            "latitude": 36.317, "longitude": 127.428, "geocode_source": "test",
            "collected_at": timezone.now(),
        })

    def setUp(self):
        self.now = timezone.now()
        self.game = {
            "id": "20990417NCHH02099", "date": "2099-04-17", "time": "17:00",
            "stadium": "대전", "status": "scheduled", "statusLabel": "경기 예정",
            "home": {"code": "HH", "name": "한화"}, "away": {"code": "NC", "name": "NC"},
        }

    def save_month(self, game=None, now=None):
        game = game or self.game
        persist_month({"month": "2099-04", "games": [game], "days": [
            {"date": game["date"], "status": "ready", "gameCount": 1},
        ]}, now or self.now)
        return Game.objects.get(source_external_code=game["id"])

    def test_new_month_game_is_available_to_nearest_stadium_query(self):
        row = self.save_month()
        self.assertEqual(row.stadium_id, self.stadium.pk)
        now = datetime(2099, 4, 16, 9, tzinfo=ZoneInfo("Asia/Seoul"))
        with patch("django.utils.timezone.now", return_value=now), \
                patch("tving.service.get_game_range_freshness", return_value={"stale": False, "warning": None}):
            schedule, stadium_id = course.load_schedule("DAEJEON", None, now)
        chosen, assumed, _ = course.find_game("DAEJEON", "", "2099-04-16", schedule,
                                              now=now, stadium_id=stadium_id)
        self.assertFalse(assumed)
        self.assertEqual((chosen["date"], chosen["time"]), ("2099-04-17", "17:00"))

    def test_daily_ingestion_also_links_canonical_venue_name(self):
        game = {**self.game, "stadium": self.stadium.stadium_name_ko}
        persist_daily({"date": game["date"], "games": [game], "standings": [],
                       "individualRankings": {"pitchers": [], "hitters": []}}, self.now)
        self.assertEqual(Game.objects.get(source_external_code=game["id"]).stadium_id, self.stadium.pk)

    def test_unknown_secondary_venue_is_not_inferred_from_home_team(self):
        for name in ("청주", "미정", "대전 제2구장", ""):
            with self.subTest(name=name):
                self.assertIsNone(stadium_for(name))
        row = self.save_month({**self.game, "stadium": "청주"})
        self.assertIsNone(row.stadium_id)

    def test_refresh_repairs_missing_link_without_replacing_game(self):
        row = self.save_month()
        Game.objects.filter(pk=row.pk).update(stadium=None)
        refreshed = self.save_month(now=self.now + timedelta(seconds=3600))
        self.assertEqual(refreshed.pk, row.pk)
        self.assertEqual(refreshed.stadium_id, self.stadium.pk)

    def test_backfill_preview_apply_and_repeat_preserve_game_data(self):
        row = self.save_month()
        Game.objects.filter(pk=row.pk).update(stadium=None)
        before = Game.objects.values().get(pk=row.pk)
        call_command("backfill_game_stadiums", stdout=StringIO())
        self.assertEqual(Game.objects.values().get(pk=row.pk), before)
        call_command("backfill_game_stadiums", apply=True, stdout=StringIO())
        after = Game.objects.values().get(pk=row.pk)
        self.assertEqual(after, {**before, "stadium_id": self.stadium.pk})
        output = StringIO()
        call_command("backfill_game_stadiums", apply=True, stdout=output)
        self.assertIn("updated=0", output.getvalue())

    def test_backfill_leaves_unresolved_and_already_linked_games_untouched(self):
        row = self.save_month({**self.game, "stadium": "청주"})
        linked = deepcopy(self.game)
        linked["id"] += "-linked"
        linked["date"] = "2099-04-18"
        existing = self.save_month(linked)
        Game.objects.filter(pk=existing.pk).update(source_stadium_name="다른 구장")
        before = list(Game.objects.filter(pk__in=[row.pk, existing.pk]).order_by("pk").values())
        call_command("backfill_game_stadiums", apply=True, stdout=StringIO())
        self.assertEqual(list(Game.objects.filter(pk__in=[row.pk, existing.pk]).order_by("pk").values()), before)
