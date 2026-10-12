from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import PointTransaction, PointWallet
from baseball.models import Game, Player, Team
from fantasy.models import (
    FantasyBattingGameStat,
    FantasyPitchingGameStat,
    FantasyPlayerScore,
    FantasySelection,
    FantasySettlement,
    FantasyUserResult,
    FantasyWeek,
)
from fantasy.services.settlement import FantasySettlementNotReady, _point_amount, settle_week
from fantasy.services.scoring import BASE_SCORE, NEUTRAL_WEIGHT, PITCHER_STATS, WEIGHT_MAX, WEIGHT_MIN, score_selection
from fantasy.services.weeks import week_bounds


class FantasySelectionTypeMigrationTests(TransactionTestCase):
    def test_historical_and_already_backfilled_types_follow_saved_weights(self):
        final = ("fantasy", "0005_repair_selection_type_from_weights")
        other_targets = [target for target in MigrationExecutor(connection).loader.graph.leaf_nodes() if target[0] != "fantasy"]
        try:
            for start in ("0003_replace_innings_with_batters_faced", "0004_fantasyselection_type_snapshot"):
                with self.subTest(start=start):
                    executor = MigrationExecutor(connection)
                    executor.migrate([("fantasy", start)])
                    apps = executor.loader.project_state([*other_targets, ("fantasy", start)]).apps
                    User = apps.get_model(get_user_model()._meta.app_label, get_user_model()._meta.model_name)
                    TeamModel = apps.get_model("baseball", "Team")
                    PlayerModel = apps.get_model("baseball", "Player")
                    WeekModel = apps.get_model("fantasy", "FantasyWeek")
                    Selection = apps.get_model("fantasy", "FantasySelection")
                    user = User.objects.create(username=f"migration-{start}")
                    team = TeamModel.objects.create(id=10000 + user.pk, team_code=f"M{user.pk}", team_name_ko="마이그레이션")
                    week = WeekModel.objects.create(week_start=date(2026, 5, 4), week_end=date(2026, 5, 10), settlement_date=date(2026, 5, 12))
                    ids = []
                    for intended, positions, weights in (
                        ("PITCHER", ["외야수"], dict.fromkeys(PITCHER_STATS, 5)),
                        ("BATTER", ["투수"], dict.fromkeys(("at_bats", "hits", "rbi", "runs"), 5)),
                        ("BATTER", ["외야수"], {}),
                    ):
                        player = PlayerModel.objects.create(external_code=f"migration-{user.pk}-{intended}-{len(ids)}", name="선수", team=team, positions=positions)
                        fields = {"fantasy_type": "BATTER" if intended == "PITCHER" else "PITCHER"} if start.startswith("0004") and weights else {}
                        selection = Selection.objects.create(user=user, week=week, player=player, stat_weights=weights, is_confirmed=bool(weights), **fields)
                        ids.append((selection.pk, intended, weights))
                    executor = MigrationExecutor(connection)
                    executor.migrate([final])
                    game = Game.objects.create(id=20000 + user.pk, game_code=f"migration-game-{user.pk}", game_date=date(2026, 5, 6), game_time="18:30", home_team_id=team.pk, away_team_id=team.pk, status_code="FINAL", game_type="REGULAR", collected_at=timezone.now())
                    for pk, intended, weights in ids:
                        selection = FantasySelection.objects.get(pk=pk)
                        self.assertEqual(selection.fantasy_type, intended)
                        self.assertEqual(selection.stat_weights, weights)
                        if weights and intended == "PITCHER":
                            FantasyPitchingGameStat.objects.create(game=game, player=selection.player, team_id=team.pk, saves=1, batters_faced=20, strikeouts=6, pitch_count=90)
                            self.assertEqual(score_selection(selection), Decimal("77"))
                        elif weights:
                            FantasyBattingGameStat.objects.create(game=game, player=selection.player, team_id=team.pk, hits=1)
                            self.assertEqual(score_selection(selection), Decimal("4"))
                        else:
                            self.assertEqual(score_selection(selection), Decimal("0"))
                    from importlib import import_module
                    migration = import_module("fantasy.migrations.0005_repair_selection_type_from_weights")
                    with connection.schema_editor() as editor:
                        migration.repair_selection_type(executor.loader.project_state([*other_targets, final]).apps, editor)
                    for pk, intended, weights in ids:
                        self.assertEqual(FantasySelection.objects.get(pk=pk).stat_weights, weights)
                    FantasyWeek.objects.filter(pk=week.pk).delete()
        finally:
            MigrationExecutor(connection).migrate([final])


class FantasyPolicyTests(SimpleTestCase):
    def test_week_is_monday_to_sunday(self):
        self.assertEqual(week_bounds(date(2026, 10, 6)), (date(2026, 10, 5), date(2026, 10, 11)))

    def test_weight_policy_has_valid_range_and_neutral_value(self):
        self.assertLessEqual(WEIGHT_MIN, NEUTRAL_WEIGHT)
        self.assertLessEqual(NEUTRAL_WEIGHT, WEIGHT_MAX)
        self.assertEqual(BASE_SCORE["hits"], Decimal("4"))
        self.assertEqual(BASE_SCORE["batters_faced"], Decimal("1"))
        self.assertNotIn("innings", PITCHER_STATS)
        self.assertIn("batters_faced", PITCHER_STATS)

    @override_settings(FANTASY_POINT_PAYOUT_RATE=Decimal("0.10"))
    def test_point_payout_rate_gives_zero_below_ten_score_and_one_at_ten(self):
        self.assertEqual(_point_amount(Decimal("9.99")), 0)
        self.assertEqual(_point_amount(Decimal("10")), 1)
        self.assertEqual(_point_amount(Decimal("100")), 10)

    @override_settings(FANTASY_POINT_PAYOUT_RATE=Decimal("0.05"))
    def test_point_payout_rate_can_be_changed_through_settings(self):
        self.assertEqual(_point_amount(Decimal("19.99")), 0)
        self.assertEqual(_point_amount(Decimal("20")), 1)


class FantasyFlowTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="fantasy-user", password="test-password")
        cls.team = Team.objects.create(id=901, team_code="FT", team_name_ko="환상팀")
        cls.other_team = Team.objects.create(id=902, team_code="OT", team_name_ko="다른 팀")
        cls.pitcher = Player.objects.create(
            external_code="pitcher-1", team=cls.team, name="투수", positions=["투수"]
        )
        cls.other_pitcher = Player.objects.create(
            external_code="pitcher-2", team=cls.team, name="다른 투수", positions=["투수"]
        )
        cls.hitter = Player.objects.create(
            external_code="hitter-1", team=cls.team, name="타자", positions=["1루수"]
        )
        cls.catcher = Player.objects.create(
            external_code="hitter-2", team=cls.team, name="포수", positions=["포수"]
        )
        cls.other_hitter = Player.objects.create(
            external_code="hitter-3", team=cls.other_team, name="외야수", positions=["외야수"]
        )

    def setUp(self):
        self.client.force_authenticate(self.user)

    def test_player_api_separates_pitchers_from_all_other_positions(self):
        batters = self.client.get("/api/v1/fantasy/players/?fantasy_type=batter&team=FT")
        pitchers = self.client.get("/api/v1/fantasy/players/?fantasy_type=pitcher&team=FT")
        searched = self.client.get("/api/v1/fantasy/players/?fantasy_type=batter&q=포수")

        self.assertEqual(batters.status_code, 200)
        self.assertEqual(
            {player["external_code"] for player in batters.data["results"]},
            {"hitter-1", "hitter-2"},
        )
        all_batters = self.client.get("/api/v1/fantasy/players/?fantasy_type=batter")
        self.assertEqual(all_batters.status_code, 200)
        self.assertEqual(len(all_batters.data["results"]), 3)
        self.assertEqual(pitchers.status_code, 200)
        self.assertEqual(
            {player["external_code"] for player in pitchers.data["results"]},
            {"pitcher-1", "pitcher-2"},
        )
        self.assertEqual(
            [player["external_code"] for player in searched.data["results"]],
            ["hitter-2"],
        )

    def test_player_api_paginates_and_rejects_invalid_page_values(self):
        for index in range(7):
            Player.objects.create(
                external_code=f"page-player-{index}", team=self.team,
                name=f"페이지선수{index}", positions=["외야수"],
            )
        first = self.client.get("/api/v1/fantasy/players/?fantasy_type=batter&page=1")
        second = self.client.get("/api/v1/fantasy/players/?fantasy_type=batter&page=2")
        self.assertEqual(first.data["count"], 10)
        self.assertEqual(len(first.data["results"]), 8)
        self.assertIsNotNone(first.data["next"])
        self.assertEqual(len(second.data["results"]), 2)
        for page in ("abc", "0", "999999999999999999999999", "1&page=2"):
            response = self.client.get(f"/api/v1/fantasy/players/?page={page}")
            self.assertIn(response.status_code, (400, 404))

    def test_selection_api_rejects_malformed_and_overflow_week_query(self):
        for query in ("abc", "0", "9223372036854775808", "1&week=2", "9" * 5000):
            response = self.client.get(f"/api/v1/fantasy/selections/?week={query}")
            self.assertEqual(response.status_code, 400)

    def test_point_api_returns_empty_wallet_without_creating_one(self):
        response = self.client.get("/api/v1/fantasy/points/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"balance": 0, "transactions": []})
        self.assertFalse(PointWallet.objects.filter(user=self.user).exists())

    def test_pitcher_score_uses_batters_faced_instead_of_innings(self):
        week = FantasyWeek.objects.create(
            week_start=date(2040, 5, 7),
            week_end=date(2040, 5, 13),
            settlement_date=date(2040, 5, 15),
        )
        game = Game.objects.create(
            id=918,
            game_code="fantasy-pitcher-batters-faced",
            game_date=date(2040, 5, 8),
            game_time="18:30",
            home_team=self.other_team,
            away_team=self.team,
            status_code="final",
            game_type="REGULAR",
            collected_at=timezone.now(),
        )
        selection = FantasySelection.objects.create(
            week=week,
            user=self.user,
            player=self.pitcher,
            stat_weights={key: NEUTRAL_WEIGHT for key in PITCHER_STATS},
            is_confirmed=True,
            fantasy_type=FantasySelection.PITCHER,
        )
        FantasyPitchingGameStat.objects.create(
            game=game,
            player=self.pitcher,
            team=self.team,
            saves=1,
            batters_faced=20,
            strikeouts=6,
            pitch_count=90,
        )

        self.assertEqual(score_selection(selection), Decimal("77"))

    def test_selection_type_snapshot_survives_player_position_changes(self):
        week = FantasyWeek.objects.create(
            week_start=date(2040, 5, 7), week_end=date(2040, 5, 13),
            settlement_date=date(2040, 5, 15),
        )
        game = Game.objects.create(
            id=919, game_code="fantasy-position-snapshot", game_date=date(2040, 5, 8),
            game_time="18:30", home_team=self.other_team, away_team=self.team,
            status_code="final", game_type="REGULAR", collected_at=timezone.now(),
        )
        selection = FantasySelection.objects.create(
            week=week, user=self.user, player=self.pitcher, fantasy_type=FantasySelection.PITCHER,
            stat_weights={key: NEUTRAL_WEIGHT for key in PITCHER_STATS}, is_confirmed=True,
        )
        self.pitcher.positions = ["외야수"]
        self.pitcher.save(update_fields=("positions",))
        FantasyPitchingGameStat.objects.create(
            game=game, player=self.pitcher, team=self.team, saves=1, batters_faced=20,
            strikeouts=6, pitch_count=90,
        )
        self.assertEqual(score_selection(selection), Decimal("77"))

    @override_settings(FANTASY_LOCAL_REQUEST_JOBS_ENABLED=True)
    def test_current_week_request_runs_local_jobs_once_per_day(self):
        cache.delete("fantasy:local_daily_jobs:last_run_date")
        cache.delete("fantasy:local_daily_jobs:lock")
        try:
            with patch("fantasy.services.local_scheduler.call_command") as run_command:
                first = self.client.get("/api/v1/fantasy/week/current/")
                second = self.client.get("/api/v1/fantasy/week/current/")

            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(
                [call.args[0] for call in run_command.call_args_list],
                ["confirm_fantasy_selections", "settle_fantasy_week"],
            )
        finally:
            cache.delete("fantasy:local_daily_jobs:last_run_date")
            cache.delete("fantasy:local_daily_jobs:lock")

    @override_settings(FANTASY_LOCAL_REQUEST_JOBS_ENABLED=False)
    def test_current_week_request_does_not_run_local_jobs_when_disabled(self):
        with patch("fantasy.services.local_scheduler.call_command") as run_command:
            response = self.client.get("/api/v1/fantasy/week/current/")

        self.assertEqual(response.status_code, 200)
        run_command.assert_not_called()

    @override_settings(FANTASY_LOCAL_REQUEST_JOBS_ENABLED=True)
    def test_unready_local_settlement_is_retried_on_next_day_request(self):
        cache.delete("fantasy:local_daily_jobs:last_run_date")
        cache.delete("fantasy:local_daily_jobs:lock")
        first_day = date(2045, 5, 1)
        second_day = first_day + timedelta(days=1)
        try:
            with (
                patch(
                    "fantasy.services.local_scheduler.fantasy_today",
                    side_effect=(first_day, first_day, second_day),
                ),
                patch(
                    "fantasy.services.local_scheduler.call_command",
                    side_effect=(
                        None,
                        CommandError(
                            "1개 주차가 준비되지 않았습니다. 기록 상태를 확인하세요."
                        ),
                        None,
                        None,
                    ),
                ) as run_command,
            ):
                first = self.client.get("/api/v1/fantasy/week/current/")
                same_day = self.client.get("/api/v1/fantasy/week/current/")
                next_day = self.client.get("/api/v1/fantasy/week/current/")

            self.assertEqual(first.status_code, 200)
            self.assertEqual(same_day.status_code, 200)
            self.assertEqual(next_day.status_code, 200)
            self.assertEqual(run_command.call_count, 4)
        finally:
            cache.delete("fantasy:local_daily_jobs:last_run_date")
            cache.delete("fantasy:local_daily_jobs:lock")

    def test_selection_can_be_created_before_week_then_is_locked(self):
        week_start = timezone.localdate() + timedelta(days=7)
        week = FantasyWeek.objects.create(
            week_start=week_start,
            week_end=week_start + timedelta(days=6),
            settlement_date=week_start + timedelta(days=8),
        )
        Game.objects.create(
            id=903,
            game_code="fantasy-selection-game",
            game_date=week_start,
            game_time="18:30",
            home_team=self.team,
            away_team=self.other_team,
            status_code="SCHEDULED",
            game_type="REGULAR",
            collected_at=timezone.now(),
        )
        with patch("fantasy.views.next_week", return_value=week):
            created = self.client.post(
                "/api/v1/fantasy/selections/",
                {"player": self.hitter.external_code},
                format="json",
            )
        self.assertEqual(created.status_code, 201)
        selection_id = created.data["id"]

        with patch("fantasy.views._confirmable", return_value=False):
            locked = self.client.delete(f"/api/v1/fantasy/selections/{selection_id}/")
        self.assertEqual(locked.status_code, 400)
        self.assertTrue(FantasySelection.objects.filter(pk=selection_id).exists())

    def test_selection_is_rejected_when_target_week_has_no_games(self):
        week_start = timezone.localdate() + timedelta(days=7)
        week = FantasyWeek.objects.create(
            week_start=week_start,
            week_end=week_start + timedelta(days=6),
            settlement_date=week_start + timedelta(days=8),
        )
        Game.objects.create(
            id=905,
            game_code="fantasy-cancelled-selection-game",
            game_date=week_start,
            game_time="18:30",
            home_team=self.team,
            away_team=self.other_team,
            status_code="CANCELLED",
            game_type="REGULAR",
            collected_at=timezone.now(),
        )
        with patch("fantasy.views.next_week", return_value=week):
            week_response = self.client.get("/api/v1/fantasy/week/next/")
            response = self.client.post(
                "/api/v1/fantasy/selections/",
                {"player": self.hitter.external_code},
                format="json",
            )

        self.assertFalse(week_response.data["has_games"])
        self.assertEqual(response.status_code, 400)
        self.assertIn("예정된 경기가 없어", response.data["detail"])
        self.assertFalse(FantasySelection.objects.filter(week=week, user=self.user).exists())

    def test_selection_list_is_week_specific_and_next_week_selection_can_be_cancelled(self):
        current_week = FantasyWeek.objects.create(
            week_start=date(2026, 10, 5),
            week_end=date(2026, 10, 11),
            settlement_date=date(2026, 10, 13),
        )
        upcoming_week = FantasyWeek.objects.create(
            week_start=date(2026, 10, 12),
            week_end=date(2026, 10, 18),
            settlement_date=date(2026, 10, 20),
        )
        current_selection = FantasySelection.objects.create(
            week=current_week, user=self.user, player=self.pitcher, stat_weights={}
        )
        upcoming_selection = FantasySelection.objects.create(
            week=upcoming_week, user=self.user, player=self.hitter, stat_weights={}
        )

        current_response = self.client.get(
            f"/api/v1/fantasy/selections/?week={current_week.pk}"
        )
        upcoming_response = self.client.get(
            f"/api/v1/fantasy/selections/?week={upcoming_week.pk}"
        )
        self.assertEqual([item["id"] for item in current_response.data], [current_selection.pk])
        self.assertEqual([item["id"] for item in upcoming_response.data], [upcoming_selection.pk])

        with patch("fantasy.views._confirmable", return_value=True):
            deleted = self.client.delete(f"/api/v1/fantasy/selections/{upcoming_selection.pk}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertTrue(FantasySelection.objects.filter(pk=current_selection.pk).exists())
        self.assertFalse(FantasySelection.objects.filter(pk=upcoming_selection.pk).exists())

    def test_selecting_same_type_replaces_only_that_slot(self):
        week_start = timezone.localdate() + timedelta(days=7)
        week = FantasyWeek.objects.create(
            week_start=week_start,
            week_end=week_start + timedelta(days=6),
            settlement_date=week_start + timedelta(days=8),
        )
        Game.objects.create(
            id=904,
            game_code="fantasy-selection-replacement-game",
            game_date=week_start,
            game_time="18:30",
            home_team=self.team,
            away_team=self.other_team,
            status_code="SCHEDULED",
            game_type="REGULAR",
            collected_at=timezone.now(),
        )
        with patch("fantasy.views.next_week", return_value=week), patch(
            "fantasy.views._confirmable", return_value=True
        ):
            first_batter = self.client.post(
                "/api/v1/fantasy/selections/",
                {"player": self.hitter.external_code},
                format="json",
            )
            pitcher = self.client.post(
                "/api/v1/fantasy/selections/",
                {"player": self.pitcher.external_code},
                format="json",
            )
            replacement_batter = self.client.post(
                "/api/v1/fantasy/selections/",
                {"player": self.catcher.external_code},
                format="json",
            )
            repeated_batter = self.client.post(
                "/api/v1/fantasy/selections/",
                {"player": self.catcher.external_code},
                format="json",
            )
            replacement_pitcher = self.client.post(
                "/api/v1/fantasy/selections/",
                {"player": self.other_pitcher.external_code},
                format="json",
            )

        self.assertEqual(first_batter.status_code, 201)
        self.assertEqual(pitcher.status_code, 201)
        self.assertEqual(replacement_batter.status_code, 201)
        self.assertEqual(repeated_batter.status_code, 200)
        self.assertEqual(replacement_pitcher.status_code, 201)
        selections = FantasySelection.objects.filter(week=week, user=self.user).select_related("player")
        self.assertEqual(selections.count(), 2)
        self.assertEqual(
            {selection.player_id for selection in selections},
            {self.catcher.pk, self.other_pitcher.pk},
        )

    def test_settlement_waits_for_data_and_grants_points_exactly_once(self):
        start = date(2040, 5, 7)
        week = FantasyWeek.objects.create(
            week_start=start,
            week_end=date(2040, 5, 13),
            settlement_date=date(2040, 5, 15),
        )
        game = Game.objects.create(
            id=901,
            game_code="fantasy-test-game",
            game_date=date(2040, 5, 8),
            game_time="18:30",
            home_team=self.other_team,
            away_team=self.team,
            status_code="live",
            game_type="REGULAR",
            collected_at=timezone.now(),
        )
        selection = FantasySelection.objects.create(
            week=week,
            user=self.user,
            player=self.hitter,
            stat_weights={"at_bats": 5, "hits": 5, "rbi": 5, "runs": 5},
            is_confirmed=True,
        )
        FantasyBattingGameStat.objects.create(
            game=game,
            player=self.hitter,
            team=self.team,
            hits=25,
        )
        other_pitcher = Player.objects.create(
            external_code="settlement-opponent-pitcher",
            team=self.other_team,
            name="상대투수",
            positions=["투수"],
        )
        FantasyPitchingGameStat.objects.create(game=game, player=self.pitcher, team=self.team)
        FantasyPitchingGameStat.objects.create(game=game, player=other_pitcher, team=self.other_team)

        with self.assertRaises(FantasySettlementNotReady):
            settle_week(week.pk)
        self.assertFalse(FantasySettlement.objects.filter(week=week).exists())

        FantasyBattingGameStat.objects.create(
            game=game,
            player=self.other_hitter,
            team=self.other_team,
        )
        week.stats_finalized_at = timezone.now()
        week.save(update_fields=("stats_finalized_at", "updated_at"))
        settlement = settle_week(week.pk)
        same_settlement = settle_week(week.pk)

        self.assertEqual(settlement.pk, same_settlement.pk)
        result = settlement.results.get(user=self.user)
        self.assertEqual(result.final_score, Decimal("100"))
        self.assertEqual(result.point_amount, 10)
        self.assertEqual(PointWallet.objects.get(user=self.user).balance, 10)
        self.assertEqual(PointTransaction.objects.filter(user=self.user).count(), 1)
        self.assertTrue(selection.is_confirmed)
        wallet_response = self.client.get("/api/v1/fantasy/points/")
        self.assertEqual(wallet_response.status_code, 200)
        self.assertEqual(wallet_response.data["balance"], 10)
        self.assertEqual(len(wallet_response.data["transactions"]), 1)

    def test_settlement_waits_for_stats_from_every_game_in_the_week(self):
        start = date(2041, 5, 6)
        week = FantasyWeek.objects.create(
            week_start=start,
            week_end=date(2041, 5, 12),
            settlement_date=date(2041, 5, 14),
            stats_finalized_at=timezone.now(),
        )
        teams = ((self.team, self.other_team), (self.other_team, self.team))
        games = []
        for index, (home, away) in enumerate(teams, start=1):
            games.append(Game.objects.create(
                id=910 + index,
                game_code=f"fantasy-week-complete-{index}",
                game_date=date(2041, 5, 6 + index),
                game_time="18:30",
                home_team=home,
                away_team=away,
                status_code="final",
                game_type="REGULAR",
                collected_at=timezone.now(),
            ))

        FantasyBattingGameStat.objects.create(game=games[0], player=self.hitter, team=self.team)
        FantasyBattingGameStat.objects.create(game=games[0], player=self.other_hitter, team=self.other_team)
        opponent_pitcher = Player.objects.create(
            external_code="week-complete-opponent-pitcher",
            team=self.other_team,
            name="상대투수",
            positions=["투수"],
        )
        FantasyPitchingGameStat.objects.create(game=games[0], player=self.pitcher, team=self.team)
        FantasyPitchingGameStat.objects.create(game=games[0], player=opponent_pitcher, team=self.other_team)

        with self.assertRaisesRegex(FantasySettlementNotReady, "경기.*타자 기록"):
            settle_week(week.pk)
        self.assertFalse(FantasySettlement.objects.filter(week=week).exists())

        FantasyBattingGameStat.objects.create(game=games[1], player=self.hitter, team=self.team)
        FantasyBattingGameStat.objects.create(game=games[1], player=self.other_hitter, team=self.other_team)
        FantasyPitchingGameStat.objects.create(game=games[1], player=self.pitcher, team=self.team)
        FantasyPitchingGameStat.objects.create(game=games[1], player=opponent_pitcher, team=self.other_team)
        settlement = settle_week(week.pk)
        self.assertEqual(settlement.week_id, week.pk)

    def test_settlement_waits_until_all_selection_weights_are_confirmed(self):
        start = date(2042, 5, 5)
        week = FantasyWeek.objects.create(
            week_start=start,
            week_end=date(2042, 5, 11),
            settlement_date=date(2042, 5, 13),
            stats_finalized_at=timezone.now(),
        )
        game = Game.objects.create(
            id=914,
            game_code="fantasy-unconfirmed-game",
            game_date=date(2042, 5, 6),
            game_time="18:30",
            home_team=self.other_team,
            away_team=self.team,
            status_code="final",
            game_type="REGULAR",
            collected_at=timezone.now(),
        )
        FantasySelection.objects.create(
            week=week,
            user=self.user,
            player=self.hitter,
            stat_weights={},
            is_confirmed=False,
        )
        opponent_pitcher = Player.objects.create(
            external_code="unconfirmed-opponent-pitcher",
            team=self.other_team,
            name="미확정 상대투수",
            positions=["투수"],
        )
        FantasyBattingGameStat.objects.create(game=game, player=self.hitter, team=self.team)
        FantasyBattingGameStat.objects.create(
            game=game, player=self.other_hitter, team=self.other_team
        )
        FantasyPitchingGameStat.objects.create(
            game=game, player=self.pitcher, team=self.team
        )
        FantasyPitchingGameStat.objects.create(
            game=game, player=opponent_pitcher, team=self.other_team
        )

        with self.assertRaisesRegex(FantasySettlementNotReady, "확정되지 않은 선수 배율"):
            settle_week(week.pk)
        self.assertFalse(FantasySettlement.objects.filter(week=week).exists())

    def test_confirmation_command_can_target_and_safely_rerun_a_week(self):
        start = date(2043, 5, 4)
        week = FantasyWeek.objects.create(
            week_start=start,
            week_end=date(2043, 5, 10),
            settlement_date=date(2043, 5, 12),
        )
        selection = FantasySelection.objects.create(
            week=week, user=self.user, player=self.hitter, stat_weights={}
        )

        with patch("fantasy.management.commands.confirm_fantasy_selections.fantasy_today", return_value=date(2043, 5, 10)), patch("fantasy.management.commands.confirm_fantasy_selections.randbelow", return_value=3):
            call_command("confirm_fantasy_selections", week_start=start, stdout=StringIO())
        selection.refresh_from_db()
        confirmed_weights = selection.stat_weights
        self.assertTrue(selection.is_confirmed)
        self.assertEqual(confirmed_weights, {"at_bats": 4, "hits": 4, "rbi": 4, "runs": 4})

        with patch("fantasy.management.commands.confirm_fantasy_selections.fantasy_today", return_value=date(2043, 5, 10)), patch("fantasy.management.commands.confirm_fantasy_selections.randbelow", return_value=8):
            call_command("confirm_fantasy_selections", week_start=start, stdout=StringIO())
        selection.refresh_from_db()
        self.assertEqual(selection.stat_weights, confirmed_weights)

    def test_confirmation_command_requires_existing_monday_week_and_open_status(self):
        start = date(2044, 5, 2)
        week = FantasyWeek.objects.create(
            week_start=start,
            week_end=date(2044, 5, 8),
            settlement_date=date(2044, 5, 10),
            status=FantasyWeek.SETTLED,
        )

        with self.assertRaisesRegex(CommandError, "월요일"):
            call_command("confirm_fantasy_selections", week_start=date(2044, 5, 3))
        with self.assertRaisesRegex(CommandError, "주차가 없습니다"):
            call_command("confirm_fantasy_selections", week_start=date(2044, 4, 25))
        output = StringIO()
        call_command("confirm_fantasy_selections", week_start=week.week_start, stdout=output)
        self.assertIn("건너뜁니다", output.getvalue())

    def test_confirmation_command_rejects_future_week(self):
        start = date(2045, 5, 1)
        FantasyWeek.objects.create(
            week_start=start, week_end=start + timedelta(days=6),
            settlement_date=start + timedelta(days=8),
        )
        with patch("fantasy.management.commands.confirm_fantasy_selections.fantasy_today", return_value=date(2045, 4, 30)):
            with self.assertRaisesRegex(CommandError, "미래 주차"):
                call_command("confirm_fantasy_selections", week_start=start)


class FantasyAdminStatImportTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.member = user_model.objects.create_user(username="fantasy-import-member")
        cls.admin = user_model.objects.create_user(username="fantasy-import-admin", is_staff=True)
        cls.away_team = Team.objects.create(id=981, team_code="FT", team_name_ko="환상구단")
        cls.home_team = Team.objects.create(id=982, team_code="OT", team_name_ko="상대구단")
        cls.away_hitter = Player.objects.create(
            external_code="import-away-hitter", team=cls.away_team, name="원정타자", positions=["유격수"]
        )
        cls.home_hitter = Player.objects.create(
            external_code="import-home-hitter", team=cls.home_team, name="홈타자", positions=["포수"]
        )
        cls.away_pitcher = Player.objects.create(
            external_code="import-away-pitcher", team=cls.away_team, name="원정투수", positions=["투수"]
        )
        cls.home_pitcher = Player.objects.create(
            external_code="import-home-pitcher", team=cls.home_team, name="홈투수", positions=["투수"]
        )
        cls.week = FantasyWeek.objects.create(
            week_start=date(2026, 5, 4),
            week_end=date(2026, 5, 10),
            settlement_date=date(2026, 5, 12),
            stats_finalized_at=timezone.now(),
        )
        cls.game = Game.objects.create(
            id=983,
            game_code="fantasy-import-game",
            game_date=date(2026, 5, 6),
            game_time="18:30",
            home_team=cls.home_team,
            away_team=cls.away_team,
            status_code="LIVE",
            game_type="REGULAR",
            collected_at=timezone.now(),
        )

    def setUp(self):
        self.client.force_authenticate(self.admin)

    def _payload(self):
        batting = (
            "타자\n"
            "FT\n"
            "타순\t포지션\t선수명\t타수\t안타\t타점\t득점\t타율\t요약\n"
            "1\t유격\t원정타자\t4\t2\t1\t1\t0.300\t중안\n"
            "OT\n"
            "타순\t포지션\t선수명\t타수\t안타\t타점\t득점\t타율\t요약\n"
            "1\t포수\t홈타자\t3\t1\t0\t0\t0.250\t좌안\n"
        )
        pitching = (
            "투수\n"
            "FT\n"
            "선수명\t등판\t결과\t승\t패\t세\t이닝\t타자\t투구수\t타수\t피안타\t홈런\t4사구\t삼진\t실점\t자책\tERA\n"
            "원정투수\t선발\t패\t1\t2\t0\t5\t20\t88\t19\t4\t1\t3\t6\t4\t4\t3.00\n"
            "OT\n"
            "선수명\t등판\t결과\t승\t패\t세\t이닝\t타자\t투구수\t타수\t피안타\t홈런\t4사구\t삼진\t실점\t자책\tERA\n"
            "홈투수\t선발\t승\t2\t1\t0\t2/3\t8\t30\t7\t2\t0\t1\t2\t1\t1\t2.50\n"
        )
        return {"game_id": self.game.pk, "source_game_id": self.game.pk, "batting": batting, "pitching": pitching}

    def test_import_rejects_stale_game_draft_and_integer_overflow(self):
        payload = self._payload()
        payload["source_game_id"] = self.game.pk + 1
        stale = self.client.post("/api/v1/fantasy/admin/stats/", payload, format="json")
        self.assertEqual(stale.status_code, 400)
        payload = self._payload()
        payload["batting"] = payload["batting"].replace("4\t2\t1\t1", "2147483648\t1\t1\t1")
        overflow = self.client.post("/api/v1/fantasy/admin/stats/", payload, format="json")
        self.assertEqual(overflow.status_code, 400)
        self.assertFalse(FantasyBattingGameStat.objects.filter(game=self.game).exists())

    def test_import_requires_unique_name_or_matching_external_player_code(self):
        Player.objects.create(
            external_code="import-away-hitter-duplicate", team=self.away_team,
            name=self.away_hitter.name, positions=["유격수"],
        )
        payload = self._payload()
        payload["batting"] = payload["batting"].replace("원정타자", self.away_hitter.name)
        ambiguous = self.client.post("/api/v1/fantasy/admin/stats/", payload, format="json")
        self.assertEqual(ambiguous.status_code, 400)
        self.assertIn("여러 명", ambiguous.data["errors"][0])

        payload["batting"] = payload["batting"].replace(
            "선수명\t타수", "선수코드\t선수명\t타수"
        ).replace(
            f"{self.away_hitter.name}\t4", f"{self.away_hitter.external_code}\t{self.away_hitter.name}\t4"
        )
        identified = self.client.post("/api/v1/fantasy/admin/stats/", payload, format="json")
        self.assertEqual(identified.status_code, 200)

        mismatch = self._payload()
        mismatch["batting"] = mismatch["batting"].replace(
            "선수명\t타수", "선수코드\t선수명\t타수"
        ).replace("원정타자\t4", "잘못된코드\t원정타자\t4")
        mismatch_response = self.client.post("/api/v1/fantasy/admin/stats/", mismatch, format="json")
        self.assertEqual(mismatch_response.status_code, 400)

    def test_pitcher_identifier_header_accepts_legacy_and_coded_rows_with_trailing_era(self):
        for coded in (False, True):
            with self.subTest(coded=coded):
                payload = self._payload()
                payload["pitching"] = (
                    "FT\n선수코드\t선수명\t세\t타자\t삼진\t투구수\tERA\n"
                    + (f"{self.away_pitcher.external_code}\t" if coded else "")
                    + "원정투수\t0\t20\t6\t88\t3\n"
                    + "OT\n선수코드\t선수명\t세\t타자\t삼진\t투구수\tERA\n"
                    + f"{self.home_pitcher.external_code}\t홈투수\t1\t8\t2\t30\t2\n"
                )
                response = self.client.post("/api/v1/fantasy/admin/stats/", payload, format="json")
                self.assertEqual(response.status_code, 200, response.data)
                row = FantasyPitchingGameStat.objects.get(game=self.game, player=self.away_pitcher)
                self.assertEqual((row.saves, row.batters_faced, row.strikeouts, row.pitch_count), (0, 20, 6, 88))

    def test_staff_can_import_late_live_game_records_and_reset_finalized_flag(self):
        response = self.client.post(
            "/api/v1/fantasy/admin/stats/", self._payload(), format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["batting_count"], 2)
        self.assertEqual(response.data["pitching_count"], 2)
        self.assertFalse(response.data["stats_finalized"])
        self.assertEqual(self.week.__class__.objects.get(pk=self.week.pk).stats_finalized_at, None)
        away_batting = FantasyBattingGameStat.objects.get(game=self.game, player=self.away_hitter)
        self.assertEqual((away_batting.at_bats, away_batting.hits, away_batting.rbi, away_batting.runs), (4, 2, 1, 1))
        home_pitching = FantasyPitchingGameStat.objects.get(game=self.game, player=self.home_pitcher)
        self.assertEqual(
            (home_pitching.saves, home_pitching.batters_faced, home_pitching.strikeouts, home_pitching.pitch_count),
            (0, 8, 2, 30),
        )

        payload = self._payload()
        payload["batting"] = payload["batting"].replace("4\t2\t1\t1", "4\t1\t2\t0")
        repeated = self.client.post("/api/v1/fantasy/admin/stats/", payload, format="json")
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(FantasyBattingGameStat.objects.filter(game=self.game).count(), 2)
        self.assertEqual(
            FantasyBattingGameStat.objects.get(game=self.game, player=self.away_hitter).hits,
            1,
        )

    def test_staff_can_view_existing_game_records_in_readable_fields(self):
        FantasyBattingGameStat.objects.create(
            game=self.game,
            player=self.away_hitter,
            team=self.away_team,
            at_bats=4,
            hits=2,
            rbi=1,
            runs=1,
        )
        FantasyPitchingGameStat.objects.create(
            game=self.game,
            player=self.home_pitcher,
            team=self.home_team,
            saves=0,
            batters_faced=8,
            strikeouts=2,
            pitch_count=30,
        )

        with patch("fantasy.admin_views.fantasy_today", return_value=date(2026, 5, 6)):
            response = self.client.get(
                f"/api/v1/fantasy/admin/stats/?game_id={self.game.pk}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["game_id"], self.game.pk)
        self.assertEqual(response.data["batting"], [{
            "external_code": self.away_hitter.external_code,
            "player_name": "원정타자",
            "team_name": "환상구단",
            "at_bats": 4,
            "hits": 2,
            "rbi": 1,
            "runs": 1,
        }])
        self.assertEqual(response.data["pitching"], [{
            "external_code": self.home_pitcher.external_code,
            "player_name": "홈투수",
            "team_name": "상대구단",
            "saves": 0,
            "batters_faced": 8,
            "strikeouts": 2,
            "pitch_count": 30,
        }])

    def test_staff_can_delete_game_stats_without_deleting_the_game(self):
        FantasyBattingGameStat.objects.create(
            game=self.game, player=self.away_hitter, team=self.away_team
        )
        FantasyPitchingGameStat.objects.create(
            game=self.game, player=self.home_pitcher, team=self.home_team
        )

        with patch("fantasy.admin_views.fantasy_today", return_value=date(2026, 5, 6)):
            response = self.client.delete(
                f"/api/v1/fantasy/admin/stats/?game_id={self.game.pk}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["batting_count"], 1)
        self.assertEqual(response.data["pitching_count"], 1)
        self.assertFalse(FantasyBattingGameStat.objects.filter(game=self.game).exists())
        self.assertFalse(FantasyPitchingGameStat.objects.filter(game=self.game).exists())
        self.assertTrue(Game.objects.filter(pk=self.game.pk).exists())
        self.assertIsNone(FantasyWeek.objects.get(pk=self.week.pk).stats_finalized_at)

    def test_staff_cannot_delete_stats_from_settled_week(self):
        FantasyBattingGameStat.objects.create(
            game=self.game, player=self.away_hitter, team=self.away_team
        )
        self.week.status = FantasyWeek.SETTLED
        self.week.save(update_fields=("status",))

        with patch("fantasy.admin_views.fantasy_today", return_value=date(2026, 5, 6)):
            response = self.client.delete(
                f"/api/v1/fantasy/admin/stats/?game_id={self.game.pk}"
            )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(FantasyBattingGameStat.objects.filter(game=self.game).exists())


    def test_invalid_row_does_not_partially_save_records(self):
        payload = self._payload()
        payload["batting"] = payload["batting"].replace("원정타자", "없는선수")

        response = self.client.post("/api/v1/fantasy/admin/stats/", payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("없는선수", response.data["errors"][0])
        self.assertFalse(FantasyBattingGameStat.objects.filter(game=self.game).exists())
        self.assertFalse(FantasyPitchingGameStat.objects.filter(game=self.game).exists())

    def test_non_staff_cannot_list_or_import_games(self):
        self.client.force_authenticate(self.member)

        with patch("fantasy.admin_views.fantasy_today", return_value=date(2026, 5, 6)):
            game_list = self.client.get("/api/v1/fantasy/admin/games/?date=2026-05-06")
            game_stats = self.client.get(
                f"/api/v1/fantasy/admin/stats/?game_id={self.game.pk}"
            )
            deleted = self.client.delete(
                f"/api/v1/fantasy/admin/stats/?game_id={self.game.pk}"
            )
            imported = self.client.post(
                "/api/v1/fantasy/admin/stats/", self._payload(), format="json"
            )

        self.assertEqual(game_list.status_code, 403)
        self.assertEqual(game_stats.status_code, 403)
        self.assertEqual(deleted.status_code, 403)
        self.assertEqual(imported.status_code, 403)
        self.assertFalse(FantasyBattingGameStat.objects.filter(game=self.game).exists())

    def test_game_list_includes_scheduled_games_and_rejects_future_dates(self):
        with patch("fantasy.admin_views.fantasy_today", return_value=date(2026, 5, 6)):
            listed = self.client.get("/api/v1/fantasy/admin/games/?date=2026-05-06")
            outside_week = self.client.get("/api/v1/fantasy/admin/games/?date=2026-05-11")

        self.assertEqual(listed.status_code, 200)
        self.assertIn(self.game.pk, [item["id"] for item in listed.data])
        self.assertTrue(all(str(item["game_date"]) == "2026-05-06" for item in listed.data))
        self.assertEqual(outside_week.status_code, 400)


class FantasyAdminTestSettlementTests(APITestCase):
    endpoint = "/api/v1/fantasy/admin/test-settlement/"

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.member = user_model.objects.create_user(username="fantasy-test-settlement-member")
        cls.admin = user_model.objects.create_user(
            username="fantasy-test-settlement-admin", is_staff=True
        )
        cls.team = Team.objects.create(id=991, team_code="TS", team_name_ko="테스트구단")
        cls.player = Player.objects.create(
            external_code="test-settlement-hitter",
            team=cls.team,
            name="테스트타자",
            positions=["1루수"],
        )
        cls.week = FantasyWeek.objects.create(
            week_start=date(2026, 5, 4),
            week_end=date(2026, 5, 10),
            settlement_date=date(2026, 5, 12),
        )
        FantasySelection.objects.create(
            week=cls.week,
            user=cls.member,
            player=cls.player,
            stat_weights={"at_bats": 5, "hits": 5, "rbi": 5, "runs": 5},
            is_confirmed=True,
        )
        cls.game = Game.objects.create(
            id=992,
            game_code="fantasy-test-settlement-game",
            game_date=date(2026, 5, 6),
            game_time="18:30",
            home_team=cls.team,
            away_team=cls.team,
            status_code="live",
            game_type="REGULAR",
            collected_at=timezone.now(),
        )
        FantasyBattingGameStat.objects.create(
            game=cls.game,
            player=cls.player,
            team=cls.team,
            hits=25,
        )

    def setUp(self):
        self.client.force_authenticate(self.admin)

    def test_staff_can_pay_current_partial_scores_once(self):
        with patch("fantasy.admin_views.current_week", return_value=self.week):
            response = self.client.post(self.endpoint)
            repeated = self.client.post(self.endpoint)
            state = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_points"], 10)
        self.assertEqual(response.data["user_count"], 1)
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(state.status_code, 200)
        self.assertTrue(state.data["is_test_settlement"])
        self.assertEqual(FantasyUserResult.objects.get(user=self.member).final_score, Decimal("100"))
        self.assertEqual(PointWallet.objects.get(user=self.member).balance, 10)
        self.assertEqual(
            PointTransaction.objects.filter(user=self.member).values_list("amount", flat=True).get(),
            10,
        )
        self.assertTrue(FantasyPlayerScore.objects.filter(user_result__user=self.member).exists())
        self.assertEqual(FantasyWeek.objects.get(pk=self.week.pk).status, FantasyWeek.SETTLED)

    def test_staff_can_cancel_test_payment_and_reopen_week_with_ledger_reversal(self):
        next_week = FantasyWeek.objects.create(
            week_start=date(2026, 5, 11), week_end=date(2026, 5, 17),
            settlement_date=date(2026, 5, 19),
        )
        target = f"{self.endpoint}?week_id={self.week.pk}"
        with patch("fantasy.admin_views.current_week", return_value=next_week):
            paid = self.client.post(target)
            state = self.client.get(target)
            reloaded = self.client.get(self.endpoint)
            self.assertEqual(reloaded.data["week_id"], next_week.pk)
            self.assertEqual([item["week_id"] for item in reloaded.data["cancellable_weeks"]], [self.week.pk])
            cancelled = self.client.delete(target)
            after = self.client.get(self.endpoint)
            self.assertEqual(after.data["cancellable_weeks"], [])
            repeated_cancel = self.client.delete(target)

        self.assertEqual(paid.status_code, 200)
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(state.data["week_id"], self.week.pk)
        self.assertEqual(cancelled.data["week_id"], self.week.pk)
        self.assertEqual(cancelled.data["reversed_points"], 10)
        self.assertEqual(repeated_cancel.status_code, 409)
        self.assertEqual(FantasyWeek.objects.get(pk=self.week.pk).status, FantasyWeek.OPEN)
        self.assertIsNone(FantasyWeek.objects.get(pk=self.week.pk).settled_at)
        self.assertFalse(FantasySettlement.objects.filter(week=self.week).exists())
        self.assertFalse(FantasyUserResult.objects.filter(user=self.member).exists())
        self.assertFalse(FantasyPlayerScore.objects.filter(user_result__user=self.member).exists())
        self.assertEqual(PointWallet.objects.get(user=self.member).balance, 0)
        self.assertCountEqual(
            PointTransaction.objects.filter(user=self.member).values_list("amount", flat=True),
            [10, -10],
        )

    def test_member_cannot_read_pay_or_cancel_test_settlement(self):
        self.client.force_authenticate(self.member)
        with patch("fantasy.admin_views.current_week", return_value=self.week):
            read = self.client.get(self.endpoint)
            pay = self.client.post(self.endpoint)
            cancel = self.client.delete(self.endpoint)

        self.assertEqual(read.status_code, 403)
        self.assertEqual(pay.status_code, 403)
        self.assertEqual(cancel.status_code, 403)
        self.assertFalse(FantasySettlement.objects.filter(week=self.week).exists())
        self.assertFalse(PointWallet.objects.filter(user=self.member).exists())

    def test_staff_cannot_cancel_regular_settlement_with_test_button(self):
        settlement = FantasySettlement.objects.create(
            week=self.week,
            settled_at=timezone.now(),
        )
        result = FantasyUserResult.objects.create(
            settlement=settlement,
            user=self.member,
            final_score=Decimal("100"),
            point_amount=10,
        )
        wallet = PointWallet.objects.create(user=self.member, balance=10)
        PointTransaction.objects.create(
            user=self.member,
            wallet=wallet,
            amount=10,
            transaction_type=PointTransaction.FANTASY_SETTLEMENT,
            source_type="fantasy.FantasySettlement",
            source_key=f"{settlement.pk}:{self.member.pk}",
        )
        self.week.status = FantasyWeek.SETTLED
        self.week.settled_at = timezone.now()
        self.week.save(update_fields=("status", "settled_at"))

        with patch("fantasy.admin_views.current_week", return_value=self.week):
            response = self.client.delete(self.endpoint)
            state = self.client.get(self.endpoint)

        self.assertEqual(state.data["cancellable_weeks"], [])
        self.assertEqual(response.status_code, 409)
        self.assertTrue(FantasySettlement.objects.filter(pk=settlement.pk).exists())
        self.assertEqual(FantasyUserResult.objects.get(pk=result.pk).point_amount, 10)
        self.assertEqual(PointWallet.objects.get(user=self.member).balance, 10)
        self.assertEqual(PointTransaction.objects.filter(user=self.member).count(), 1)
