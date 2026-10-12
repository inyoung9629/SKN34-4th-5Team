from datetime import date, time
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from baseball.management.commands.demo_games import FIXTURES, demo_rows
from baseball.models import Game, Stadium, Team


class DemoGamesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Game.objects.all().delete()
        for pk, code in enumerate(("KIA", "NC", "DOOSAN", "LOTTE", "LG", "KT"), 1):
            Team.objects.get_or_create(team_code=code, defaults={"id": pk, "team_name_ko": code})
        for pk, code in enumerate(("GWANGJU", "JAMSIL", "SUWON"), 1):
            Stadium.objects.get_or_create(stadium_code=code, defaults={
                "id": pk, "stadium_name_ko": code, "address": "", "latitude": 37,
                "longitude": 127, "geocode_source": "test", "collected_at": timezone.now(),
            })

    def run_command(self, action="status"):
        output = StringIO()
        call_command("demo_games", action, stdout=output)
        return output.getvalue()

    def official_game(self, **changes):
        values = dict(id=91, game_code="official", source="tving", source_external_code="official-91",
                      game_date=date(2026, 10, 16), game_time=time(18, 30), stadium=Stadium.objects.get(stadium_code="SUWON"),
                      home_team=Team.objects.get(team_code="LG"), away_team=Team.objects.get(team_code="KT"),
                      status_code="scheduled", collected_at=timezone.now())
        return Game.objects.create(**(values | changes))

    def test_apply_is_idempotent_and_has_exact_date_times_teams_and_markers(self):
        self.assertIn("created=2", self.run_command("apply"))
        before = list(Game.objects.order_by("pk").values())
        self.assertIn("created=0", self.run_command("apply"))
        self.assertEqual(before, list(Game.objects.order_by("pk").values()))
        self.assertEqual(list(demo_rows().order_by("game_time").values_list(
            "game_date", "game_time", "stadium__stadium_code", "home_team__team_code", "away_team__team_code", "status_code"
        )), [
            (date(2026, 10, 16), time(14), "GWANGJU", "KIA", "NC", "scheduled"),
            (date(2026, 10, 16), time(17), "JAMSIL", "DOOSAN", "LOTTE", "scheduled"),
        ])

    def test_status_is_readonly_and_remove_preserves_other_games(self):
        official = self.official_game()
        other_demo = self.official_game(id=92, game_code="another-demo", source="demo", source_external_code="another-demo", game_type="DEMO")
        before = list(Game.objects.order_by("pk").values())
        self.assertIn("active_demo_games=0", self.run_command())
        self.assertEqual(before, list(Game.objects.order_by("pk").values()))
        self.run_command("apply")
        self.assertIn("removed=2", self.run_command("remove"))
        self.assertIn("removed=0", self.run_command("remove"))
        self.assertEqual(set(Game.objects.values_list("pk", flat=True)), {official.pk, other_demo.pk})
        self.assertEqual(before, list(Game.objects.order_by("pk").values()))
        self.assertIn("created=2", self.run_command("apply"))

    def test_conflicting_real_game_rolls_back_the_entire_batch(self):
        official = self.official_game(stadium=Stadium.objects.get(stadium_code="JAMSIL"),
                                      home_team=Team.objects.get(team_code="DOOSAN"), away_team=Team.objects.get(team_code="LOTTE"))
        before = Game.objects.values().get(pk=official.pk)
        with self.assertRaises(CommandError):
            self.run_command("apply")
        self.assertEqual(Game.objects.count(), 1)
        self.assertEqual(Game.objects.values().get(pk=official.pk), before)

    def test_reapply_rejects_official_schedule_inserted_after_demo(self):
        self.run_command("apply")
        official = self.official_game(stadium=Stadium.objects.get(stadium_code="JAMSIL"),
                                      home_team=Team.objects.get(team_code="DOOSAN"), away_team=Team.objects.get(team_code="LOTTE"))
        before = list(Game.objects.order_by("pk").values())
        with self.assertRaises(CommandError):
            self.run_command("apply")
        self.assertEqual(before, list(Game.objects.order_by("pk").values()))
        self.assertTrue(Game.objects.filter(pk=official.pk, source="tving").exists())

    def test_missing_team_rolls_back_the_first_fixture(self):
        Team.objects.filter(team_code="LOTTE").update(team_code="MISSING_LOTTE")
        with self.assertRaises(CommandError):
            self.run_command("apply")
        self.assertFalse(Game.objects.exists())

    def test_id_collision_cannot_overwrite_existing_game(self):
        official = self.official_game()
        with patch("baseball.management.commands.demo_games.stable_id", return_value=official.pk), self.assertRaises(CommandError):
            self.run_command("apply")
        self.assertEqual(Game.objects.get().game_code, "official")

    def test_remove_requires_all_ownership_markers(self):
        self.run_command("apply")
        Game.objects.filter(game_code=FIXTURES[0][0]).update(source="tving")
        with self.assertRaises(CommandError):
            self.run_command("apply")
        self.assertIn("removed=1", self.run_command("remove"))
        self.assertEqual(Game.objects.get().source, "tving")

    def test_official_ingestion_does_not_adopt_the_demo_game(self):
        from tving.relational import persist_month
        self.run_command("apply")
        before = list(demo_rows().order_by("pk").values())
        persist_month({"month": "2026-10", "games": [{
            "id": "20261016NCHT00001", "date": "2026-10-16", "time": "14:00", "stadium": "광주",
            "status": "scheduled", "statusLabel": "경기 예정",
            "home": {"code": "HT", "name": "KIA"}, "away": {"code": "NC", "name": "NC"},
        }], "days": [{"date": "2026-10-16", "status": "ready", "gameCount": 1}]}, timezone.now())
        self.assertEqual(before, list(demo_rows().order_by("pk").values()))
        self.run_command("remove")
        self.assertEqual(Game.objects.get().source_external_code, "20261016NCHT00001")

    def test_normal_chat_schedule_tool_reads_games_and_stops_after_removal(self):
        from llm.tools.baseball import create_baseball_domain_tools
        tool = next(tool for tool in create_baseball_domain_tools() if tool.name == "get_games")
        self.run_command("apply")
        with patch("tving.service.get_game_range_freshness", return_value={}):
            for stadium, home, clock in (("GWANGJU", "KIA", "14:00:00"), ("JAMSIL", "DOOSAN", "17:00:00")):
                result = tool.invoke({"start_date": "2026-10-16", "end_date": "2026-10-16", "stadium": stadium})
                self.assertEqual(result["count"], 1)
                self.assertEqual((result["items"][0]["home_team__team_code"], result["items"][0]["game_time"]), (home, clock))
                self.assertEqual(result["items"][0]["source"], "demo")
            self.run_command("remove")
            self.assertEqual(tool.invoke({"start_date": "2026-10-16", "end_date": "2026-10-16"})["count"], 0)
