import json
import threading
from io import StringIO
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from baseball.models import Game, Player, PlayerSeasonRecord, ProviderSnapshot, ScheduleDay, StandingHistory, Team
from baseball.data_loader import stable_id

from tving.parsers import TvingValidationError, parse_calendar, parse_schedule, parse_standings
from tving.service import TvingInputError, TvingUpstreamError, _NoRedirect, _persist_if_due, create_snapshot, read_snapshot, refresh_athlete, refresh_daily, refresh_month, refresh_team, search_entities, search_snapshots
from tving.relational import daily_sync_time, persist_athlete, persist_daily, persist_team, read_athlete, read_daily, read_team


FIXTURES = Path(__file__).parents[3] / "frontend" / "tests" / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def daily_payload(day="2026-09-15"):
    standings = [{"teamCode": code} for code in ("SS", "KT", "LG", "HT", "OB", "NC", "HH", "LT", "SK", "WO")]
    return {"date": day, "games": [], "standings": standings, "individualRankings": {"pitchers": [{"playerCode": "1"}], "hitters": [{"playerCode": "2"}]}, "sourceUpdatedAt": None}


def relational_daily(day="2026-09-15"):
    names = {"SS": "삼성", "KT": "KT", "LG": "LG", "HT": "KIA", "OB": "두산", "NC": "NC", "HH": "한화", "LT": "롯데", "SK": "SSG", "WO": "키움"}
    standings = [{"rank": rank, "teamCode": code, "team": names[code], "played": 10, "wins": 5, "draws": 0, "losses": 5, "winRate": "0.500", "gamesBehind": "0", "streak": "1승", "battingAverage": "0.250", "era": "3.50", "lastTen": "5승5패"} for rank, code in enumerate(names, 1)]
    ranking = lambda code, player, team: {"rank": 1, "playerCode": code, "player": player, "teamCode": team, "team": names[team], "games": "10"}
    return {"date": day, "games": [], "standings": standings, "individualRankings": {"pitchers": [ranking("10001", "투수", "SS")], "hitters": [ranking("20001", "타자", "KT")]}, "sourceUpdatedAt": None, "mode": "fixed-interval"}


def relational_game_day():
    payload = relational_daily("2026-09-08")
    payload["games"] = parse_schedule(fixture("tving-schedule-20260908.json"), "2026-09-08")
    return payload


def relational_team(code="SS", name="삼성 라이온즈"):
    return {"code": code, "teamName": name, "shortName": "삼성", "teamImageUrl": None, "backgroundImage": None, "seasonTitle": "2026 시즌", "mainRecords": [], "boxRecords": [], "schedule": [], "rankings": {"pitcher": [], "hitter": []}, "rosters": {"pitcher": [], "infielder": [], "outfielder": [], "catcher": []}, "shortcuts": []}


def relational_athlete(code="10001"):
    return {"profile": {"code": code, "name": "선수", "imageUrl": None, "positions": ["투수"], "backNumber": "NO.1", "joinDate": "", "birthDate": "", "body": [], "education": "", "draftOrder": "", "team": {"name": "삼성 라이온즈", "code": "SS", "color": "", "logoUrl": None}}, "seasonTitle": "시즌", "seasonRecords": [{"title": "평균자책", "value": "1.00", "rank": "1위", "isFirstRank": True, "graphs": []}, {"title": "승", "value": "10", "rank": None, "isFirstRank": False, "graphs": []}], "careerTitle": "통산", "careerColumns": [{"name": "시즌", "key": "season"}], "careerRows": [{"season": "2025"}, {"season": "2026"}]}


class ParserFixtureTests(TestCase):
    def test_existing_schedule_fixture_preserves_doubleheader_and_suspended(self):
        games = parse_schedule(fixture("tving-schedule-20260908.json"), "2026-09-08")
        self.assertEqual(len({game["id"] for game in games}), len(games))
        self.assertTrue(all(game["date"] == "2026-09-08" for game in games))

    def test_existing_standings_fixture_has_all_ten_teams(self):
        rows = parse_standings(fixture("tving-standings-2026.json"), "2026-09-15")
        self.assertEqual(len(rows), 10)
        self.assertEqual(len({row["teamCode"] for row in rows}), 10)

    def test_off_day_requires_calendar_proof(self):
        redirected = fixture("tving-schedule-20260909.json")
        for candidate in redirected["data"]["bands"]:
            if candidate.get("bandType") == "SPORTS_SCHEDULE":
                candidate.pop("calendar", None)
        with self.assertRaises(TvingValidationError):
            parse_schedule(redirected, "2026-09-07")
        calendar = {"code": "0000", "data": {"calendar": [8, 9]}}
        self.assertEqual(parse_schedule(redirected, "2026-09-07", calendar), [])
        self.assertEqual(parse_calendar(calendar, "2026-09"), [8, 9])


@override_settings(EXTERNAL_DATA_SYNC_INTERVAL_SECONDS=600)
class SyncPolicyTests(TestCase):
    def test_new_fresh_exact_stale_null_and_future(self):
        now = datetime(2026, 9, 15, 1, tzinfo=datetime_timezone.utc)
        snapshot, wrote = _persist_if_due("daily", "2026-09-15", daily_payload(), now)
        self.assertTrue(wrote)
        first_updated = snapshot.updated_at
        snapshot, wrote = _persist_if_due("daily", "2026-09-15", daily_payload(), now + timedelta(seconds=599))
        self.assertFalse(wrote)
        self.assertEqual(snapshot.updated_at, first_updated)
        with patch.object(ProviderSnapshot, "save", side_effect=AssertionError("fresh row must not UPDATE")):
            self.assertFalse(_persist_if_due("daily", "2026-09-15", daily_payload(), now + timedelta(seconds=599))[1])
        snapshot, wrote = _persist_if_due("daily", "2026-09-15", daily_payload(), now + timedelta(seconds=600))
        self.assertTrue(wrote)
        snapshot, wrote = _persist_if_due("daily", "2026-09-15", daily_payload(), now + timedelta(seconds=601))
        self.assertFalse(wrote)
        snapshot.last_synced_at = None
        snapshot.save(update_fields=("last_synced_at",))
        self.assertTrue(_persist_if_due("daily", "2026-09-15", daily_payload(), now + timedelta(seconds=602))[1])
        snapshot.refresh_from_db()
        snapshot.last_synced_at = now + timedelta(days=1)
        snapshot.save(update_fields=("last_synced_at",))
        self.assertFalse(_persist_if_due("daily", "2026-09-15", daily_payload(), now + timedelta(seconds=603))[1])

    @override_settings(EXTERNAL_DATA_SYNC_INTERVAL_SECONDS=1)
    def test_config_override(self):
        now = timezone.now()
        _persist_if_due("daily", "2026-09-15", daily_payload(), now)
        self.assertTrue(_persist_if_due("daily", "2026-09-15", daily_payload(), now + timedelta(seconds=2))[1])


class ConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        for index, (code, name) in enumerate((("SAMSUNG", "삼성 라이온즈"), ("KT", "KT 위즈"), ("LG", "LG 트윈스"), ("KIA", "KIA 타이거즈"), ("DOOSAN", "두산 베어스"), ("NC", "NC 다이노스"), ("HANWHA", "한화 이글스"), ("LOTTE", "롯데 자이언츠"), ("SSG", "SSG 랜더스"), ("KIWOOM", "키움 히어로즈")), 1):
            if not Team.objects.filter(team_code=code).exists():
                Team.objects.create(id=100 + index, team_code=code, team_name_ko=name)

    def test_concurrent_create_has_one_resource(self):
        barrier = threading.Barrier(2)
        now = timezone.now()
        def write():
            close_old_connections()
            barrier.wait()
            try:
                return _persist_if_due("daily", "2026-09-15", daily_payload(), now)[1]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: write(), range(2)))
        self.assertEqual(ProviderSnapshot.objects.filter(resource_kind="daily", resource_key="2026-09-15").count(), 1)
        self.assertEqual(results.count(True), 1)

    def test_concurrent_relational_refresh_keeps_one_row_per_identity(self):
        barrier = threading.Barrier(2)
        now = timezone.now()
        def write():
            close_old_connections()
            barrier.wait()
            try:
                persist_daily(relational_daily(), now)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: write(), range(2)))
        self.assertEqual(ScheduleDay.objects.count(), 1)
        self.assertEqual(StandingHistory.objects.filter(source="tving").count(), 10)
        self.assertEqual(PlayerSeasonRecord.objects.count(), 2)


class ToolAndApiTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.snapshot = ProviderSnapshot.objects.create(resource_kind="daily", resource_key="2026-09-15", payload=daily_payload(), source_fetched_at=self.now, last_synced_at=self.now)
        self.client = APIClient()

    def test_db_only_read_and_search_do_not_call_provider(self):
        with patch("tving.service._provider_json", side_effect=AssertionError("provider called")):
            self.assertEqual(read_snapshot("daily", "2026-09-15").pk, self.snapshot.pk)
            rows, count = search_snapshots(kind="daily", key="2026-09-15")
        self.assertEqual((len(rows), count), (1, 1))

    def test_public_search_and_admin_only_mutations(self):
        self.assertEqual(self.client.get("/api/v1/tving/snapshots/?kind=daily").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/tving/snapshots/{self.snapshot.pk}/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/tving/snapshots/999999/").status_code, 404)
        payload = {"profile": {"code": "68220", "name": "테스트", "positions": ["투수"], "imageUrl": None, "team": {"code": "OB", "logoUrl": None}}, "seasonTitle": "시즌", "seasonRecords": [], "careerTitle": "통산", "careerColumns": [], "careerRows": []}
        body = {"resourceKind": "athlete", "resourceKey": "68220", "payload": payload, "sourceFetchedAt": self.now.isoformat()}
        self.assertEqual(self.client.post("/api/v1/tving/snapshots/", body, format="json").status_code, 401)
        admin = get_user_model().objects.create_user(username="tving-admin", password="password", is_staff=True)
        self.client.force_authenticate(admin)
        created = self.client.post("/api/v1/tving/snapshots/", body, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertIsNone(created.data["data"]["lastSyncedAt"])
        self.assertEqual(self.client.delete(f"/api/v1/tving/snapshots/{created.data['data']['id']}/").status_code, 204)

    def test_invalid_manual_payload_and_request_are_sanitized(self):
        admin = get_user_model().objects.create_user(username="tving-admin2", password="password", is_staff=True)
        self.client.force_authenticate(admin)
        response = self.client.post("/api/v1/tving/snapshots/", {"resourceKind": "daily", "resourceKey": "bad", "payload": {}, "sourceFetchedAt": self.now.isoformat()}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Traceback", response.content.decode())
        response = self.client.post("/api/v1/tving/snapshots/", ["not", "an", "object"], format="json")
        self.assertEqual(response.status_code, 400)
        for path in ("/api/v1/tving/daily/?date=bad", "/api/v1/tving/schedule/?month=bad", "/api/v1/tving/details/teams/XX/", "/api/v1/tving/details/athletes/not-a-code/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("Traceback", response.content.decode())

    def test_db_search_rejects_boolean_pages_and_unknown_teams(self):
        with self.assertRaises(TvingInputError):
            search_snapshots(page=True)
        with self.assertRaises(TvingInputError):
            search_snapshots(team="XX")

    def test_manual_payload_rejects_nan_and_non_tving_images(self):
        admin = get_user_model().objects.create_user(username="tving-admin3", password="password", is_staff=True)
        payload = {"profile": {"code": "68220", "name": "테스트", "positions": [], "imageUrl": "https://evil.example/a.png", "team": {"code": "OB", "logoUrl": None}}, "seasonTitle": "시즌", "seasonRecords": [float("nan")], "careerTitle": "통산", "careerColumns": [], "careerRows": []}
        with self.assertRaises(TvingInputError):
            create_snapshot(kind="athlete", key="68220", payload=payload, source_fetched_at=self.now, actor=admin)

    @patch("tving.views.search_snapshots", side_effect=RuntimeError("database secret"))
    def test_database_errors_are_sanitized(self, _search):
        response = self.client.get("/api/v1/tving/snapshots/")
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "database secret", status_code=503)


@override_settings(EXTERNAL_DATA_SYNC_INTERVAL_SECONDS=600)
class RelationalPersistenceTests(TestCase):
    def test_player_records_are_baseball_canonical_models(self):
        persist_daily(relational_daily(), timezone.now())
        self.assertEqual(Player.objects.count(), 2)
        self.assertEqual(PlayerSeasonRecord.objects.count(), 2)
        self.assertIs(Player, Player)
        self.assertIs(PlayerSeasonRecord, PlayerSeasonRecord)

    def test_daily_uses_fk_entities_and_does_not_mark_ranking_players_profile_fresh(self):
        now = datetime(2026, 9, 15, 1, tzinfo=datetime_timezone.utc)
        persist_daily(relational_daily(), now)
        self.assertEqual(StandingHistory.objects.filter(source="tving").count(), 10)
        self.assertEqual(PlayerSeasonRecord.objects.count(), 2)
        self.assertEqual(Player.objects.filter(profile_last_synced_at__isnull=True).count(), 2)
        self.assertEqual(read_daily("2026-09-15")["standings"][0]["teamCode"], "SS")

    def test_per_entity_null_exact_stale_and_future_freshness(self):
        now = datetime(2026, 9, 15, 1, tzinfo=datetime_timezone.utc)
        payload = relational_daily()
        persist_daily(payload, now)
        samsung = StandingHistory.objects.get(team__team_code="SAMSUNG", source="tving")
        kt = StandingHistory.objects.get(team__team_code="KT", source="tving")
        samsung_updated, kt_updated = samsung.updated_at, kt.updated_at
        persist_daily(payload, now + timedelta(seconds=600))
        samsung.refresh_from_db(); self.assertNotEqual(samsung.updated_at, samsung_updated)
        kt.refresh_from_db(); kt_updated = kt.updated_at
        samsung.last_synced_at = None; samsung.save(update_fields=("last_synced_at",))
        kt.last_synced_at = now + timedelta(days=1); kt.save(update_fields=("last_synced_at",))
        persist_daily(payload, now + timedelta(seconds=601))
        samsung.refresh_from_db(); kt.refresh_from_db()
        self.assertEqual(samsung.last_synced_at, now + timedelta(seconds=601))
        self.assertEqual(kt.updated_at, kt_updated)

    def test_malformed_mapping_rolls_back_every_entity(self):
        payload = relational_daily()
        payload["standings"][1]["teamCode"] = "XX"
        with self.assertRaises(Exception):
            persist_daily(payload, timezone.now())
        self.assertFalse(ScheduleDay.objects.exists())
        self.assertFalse(StandingHistory.objects.filter(source="tving").exists())

    def test_legacy_backfill_is_idempotent_and_preserves_malformed_rows(self):
        now = timezone.now()
        ProviderSnapshot.objects.create(resource_kind="daily", resource_key="2026-09-15", payload=relational_daily(), source_fetched_at=now, last_synced_at=now)
        ProviderSnapshot.objects.create(resource_kind="daily", resource_key="2026-09-16", payload={"broken": True}, source_fetched_at=now, last_synced_at=now)
        first, second = StringIO(), StringIO()
        call_command("backfill_tving_snapshots", stdout=first, stderr=StringIO())
        counts = (ScheduleDay.objects.count(), StandingHistory.objects.filter(source="tving").count(), Player.objects.count())
        call_command("backfill_tving_snapshots", stdout=second, stderr=StringIO())
        self.assertEqual(counts, (ScheduleDay.objects.count(), StandingHistory.objects.filter(source="tving").count(), Player.objects.count()))
        self.assertIn("migrated=1 skipped=1 preserved=2", first.getvalue())
        self.assertEqual(ProviderSnapshot.objects.count(), 2)

    def test_absent_team_and_athlete_children_remain_stored_but_leave_current_projection(self):
        now = timezone.now()
        team = relational_team()
        team["rosters"]["pitcher"] = [{"code": "10001", "name": "선수1", "imageUrl": None, "backNumber": "NO.1"}, {"code": "10002", "name": "선수2", "imageUrl": None, "backNumber": "NO.2"}]
        team["rankings"]["pitcher"] = [{"title": "다승", "athletes": [{"code": "10001", "name": "선수1", "imageUrl": None, "rank": 1, "value": "10"}, {"code": "10002", "name": "선수2", "imageUrl": None, "rank": 2, "value": "9"}]}]
        persist_team(team, now)
        team["rosters"]["pitcher"].pop()
        team["rankings"]["pitcher"][0]["athletes"].pop()
        persist_team(team, now + timedelta(seconds=601))
        self.assertEqual(Player.objects.filter(pk__in=("10001", "10002")).count(), 2)
        self.assertEqual([row["code"] for row in read_team("SS")["rosters"]["pitcher"]], ["10001"])
        self.assertEqual([row["code"] for row in read_team("SS")["rankings"]["pitcher"][0]["athletes"]], ["10001"])

        athlete = relational_athlete()
        persist_athlete(athlete, now)
        athlete["seasonRecords"].pop(); athlete["careerRows"].pop()
        persist_athlete(athlete, now + timedelta(seconds=601))
        player = Player.objects.get(pk="10001")
        self.assertEqual(player.season_records.filter(record_kind="detail").count(), 2)
        self.assertEqual(player.career_records.count(), 2)
        projected = read_athlete("10001")
        self.assertEqual((len(projected["seasonRecords"]), len(projected["careerRows"])), (1, 1))


class TvingCsvPriorityTests(TestCase):
    data_dir = Path(__file__).resolve().parents[3] / "data"

    def test_csv_game_is_promoted_in_place_then_replay_cannot_overwrite(self):
        original = Game.objects.get(game_code="schedule_56ea63868b602cdd")
        original_id, original_code = original.pk, original.game_code
        persist_daily(relational_game_day(), timezone.now())
        promoted = Game.objects.get(source_external_code="20260908HTSS02026")
        self.assertEqual((promoted.pk, promoted.game_code, promoted.source), (original_id, original_code, "tving"))
        synced_at, score = promoted.last_synced_at, promoted.home_score
        call_command("import_baseball_data", data_dir=self.data_dir, stdout=StringIO())
        promoted.refresh_from_db()
        self.assertEqual((promoted.pk, promoted.game_code, promoted.source, promoted.last_synced_at, promoted.home_score), (original_id, original_code, "tving", synced_at, score))
        self.assertEqual(Game.objects.filter(game_date="2026-09-08", home_team__team_code="SAMSUNG", away_team__team_code="KIA", game_time="18:30").count(), 1)

    def test_provider_missing_fields_preserve_existing_values_but_zero_updates(self):
        payload = relational_game_day()
        target = next(game for game in payload["games"] if game["id"] == "20260908HTSS02026")
        now = timezone.now()
        persist_daily(payload, now)
        row = Game.objects.get(source_external_code=target["id"])
        row.home_starting_pitcher = "보존 투수"
        row.source_status_label = "보존 상태"
        row.home_score = 7
        row.save(update_fields=("home_starting_pitcher", "source_status_label", "home_score"))
        target["home"]["startingPitcher"] = None
        target["statusLabel"] = ""
        target["home"]["score"] = 0
        persist_daily(payload, now + timedelta(seconds=601))
        row.refresh_from_db()
        self.assertEqual((row.home_starting_pitcher, row.source_status_label, row.home_score), ("보존 투수", "보존 상태", 0))

    def test_tving_first_then_csv_uses_one_canonical_game_and_csv_only_survives(self):
        matching = Game.objects.get(game_code="schedule_56ea63868b602cdd")
        matching.delete()
        csv_only = Game.objects.exclude(game_date="2026-09-08").first()
        occupied_id = stable_id(Game, "tving:20260908HTSS02026")
        if not Game.objects.filter(pk=occupied_id).exists():
            Game.objects.create(id=occupied_id, game_code="occupied-stable-id", game_date="2026-01-01", game_time="12:00", status_code="scheduled", game_type="REGULAR", collected_at=timezone.now())
        persist_daily(relational_game_day(), timezone.now())
        created = Game.objects.get(source_external_code="20260908HTSS02026")
        self.assertTrue(created.game_code.startswith("tving:"))
        self.assertNotEqual(created.pk, occupied_id)
        created.home_score = 0
        created.status_code = "live"
        created.save(update_fields=("home_score", "status_code"))
        count = Game.objects.count()
        call_command("import_baseball_data", data_dir=self.data_dir, stdout=StringIO())
        created.refresh_from_db()
        first_state = (created.pk, created.stadium_id)
        call_command("import_baseball_data", data_dir=self.data_dir, stdout=StringIO())
        self.assertEqual(Game.objects.count(), count)
        created.refresh_from_db(); csv_only.refresh_from_db()
        self.assertEqual((created.source, created.home_score, created.status_code), ("tving", 0, "live"))
        self.assertEqual((created.pk, created.stadium_id), first_state)
        self.assertIsNotNone(created.stadium_id)
        self.assertEqual(csv_only.source, "csv")

    def test_existing_csv_standing_and_team_are_promoted_without_duplicates(self):
        payload = relational_daily("2026-09-07")
        original = StandingHistory.objects.get(team__team_code="SAMSUNG", snapshot_date="2026-09-07")
        original_id = original.pk
        persist_daily(payload, timezone.now())
        promoted = StandingHistory.objects.get(team__team_code="SAMSUNG", snapshot_date="2026-09-07")
        self.assertEqual((promoted.pk, promoted.source), (original_id, "tving"))
        self.assertEqual(promoted.team.source, "csv")
        persist_team(relational_team(name="TVING 삼성 라이온즈"), timezone.now())
        promoted.team.refresh_from_db()
        self.assertEqual((promoted.team.source, promoted.team.team_name_ko), ("tving", "TVING 삼성 라이온즈"))
        call_command("import_baseball_data", data_dir=self.data_dir, stdout=StringIO())
        self.assertEqual(StandingHistory.objects.filter(team__team_code="SAMSUNG", snapshot_date="2026-09-07").count(), 1)
        promoted.refresh_from_db()
        self.assertEqual(promoted.source, "tving")

    def test_rescheduled_unique_csv_match_promotes_in_place_but_doubleheader_ambiguity_refuses(self):
        payload = relational_game_day()
        target = next(game for game in payload["games"] if game["id"] == "20260908HTSS02026")
        original = Game.objects.get(game_code="schedule_56ea63868b602cdd")
        target["time"] = "19:00"; target["startsAt"] = "2026-09-08T19:00:00+09:00"
        persist_daily(payload, timezone.now())
        promoted = Game.objects.get(source_external_code=target["id"])
        self.assertEqual((promoted.pk, promoted.game_time.strftime("%H:%M")), (original.pk, "19:00"))
        promoted.source_external_code = None; promoted.source = "csv"; promoted.save(update_fields=("source_external_code", "source"))
        Game.objects.create(id=2_000_000_001, game_code="other-doubleheader", home_team=promoted.home_team, away_team=promoted.away_team, stadium=None, postseason_stage=None, game_date=promoted.game_date, game_time="17:00", status_code="scheduled", game_type="REGULAR", collected_at=timezone.now())
        target["time"] = "20:00"; target["startsAt"] = "2026-09-08T20:00:00+09:00"
        with self.assertRaisesRegex(Exception, "ambiguous"):
            persist_daily(payload, timezone.now() + timedelta(hours=1))
        self.assertFalse(Game.objects.filter(source_external_code=target["id"]).exists())

    def test_distinct_doubleheader_ids_keep_two_games_and_reserve_exact_csv_for_second(self):
        payload = relational_game_day()
        base = next(game for game in payload["games"] if game["id"] == "20260908HTSS02026")
        first, second = json.loads(json.dumps(base)), json.loads(json.dumps(base))
        first.update(id="double-a", time="17:00", startsAt="2026-09-08T17:00:00+09:00")
        second.update(id="double-b", time="18:30", startsAt="2026-09-08T18:30:00+09:00")
        payload["games"] = [first, second]
        csv = Game.objects.get(game_code="schedule_56ea63868b602cdd")
        now = timezone.now()
        persist_daily(payload, now)
        rows = list(Game.objects.filter(source_external_code__in=("double-a", "double-b")).order_by("source_external_code"))
        self.assertEqual(len(rows), 2)
        ids = {row.source_external_code: row.pk for row in rows}
        self.assertEqual(ids["double-b"], csv.pk)
        self.assertNotEqual(ids["double-a"], ids["double-b"])
        persist_daily(payload, now + timedelta(seconds=601))
        self.assertEqual({row.source_external_code: row.pk for row in Game.objects.filter(source_external_code__in=ids)}, ids)

    def test_missing_provider_game_is_retained_but_not_in_current_day_projection(self):
        payload = relational_game_day(); now = timezone.now()
        persist_daily(payload, now)
        removed = payload["games"].pop()
        persist_daily(payload, now + timedelta(seconds=601))
        self.assertTrue(Game.objects.filter(source_external_code=removed["id"]).exists())
        self.assertNotIn(removed["id"], {game["id"] for game in read_daily("2026-09-08")["games"]})

    def test_ranking_transfer_updates_player_identity_without_profile_freshness(self):
        payload = relational_daily(); now = timezone.now()
        persist_daily(payload, now)
        pitcher = payload["individualRankings"]["pitchers"][0]
        pitcher.update(teamCode="KT", team="KT", player="이적 투수")
        persist_daily(payload, now + timedelta(seconds=1))
        player = Player.objects.get(pk="10001")
        self.assertEqual((player.team.team_code, player.name), ("SAMSUNG", "투수"))
        persist_daily(payload, now + timedelta(seconds=601))
        player.refresh_from_db()
        self.assertEqual((player.team.team_code, player.name), ("KT", "이적 투수"))
        self.assertIsNone(player.profile_last_synced_at)


class RelationalToolApiTests(TestCase):
    def setUp(self):
        persist_daily(relational_daily(), timezone.now())
        self.client = APIClient()

    def test_public_typed_filters_are_db_only_and_bounded(self):
        with patch("tving.service._provider_json", side_effect=AssertionError("provider called")):
            rows, count = search_entities(kind="standing", team="SS", date="2026-09-15")
            response = self.client.get("/api/v1/tving/entities/?kind=player&team=SS&page_size=10")
        self.assertEqual((len(rows), count), (1, 1))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["count"], 1)
        with self.assertRaises(TvingInputError): search_entities(kind="unknown")
        with self.assertRaises(TvingInputError): search_entities(kind="player", page_size=101)

    def test_relational_player_crud_is_admin_only(self):
        body = {"externalCode": "68220", "teamCode": "OB", "name": "테스트 선수"}
        self.assertEqual(self.client.post("/api/v1/tving/entities/players/", body, format="json").status_code, 401)
        admin = get_user_model().objects.create_user(username="entity-admin", password="password", is_staff=True)
        self.client.force_authenticate(admin)
        created = self.client.post("/api/v1/tving/entities/players/", body, format="json")
        self.assertEqual(created.status_code, 201)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/v1/tving/entities/players/68220/").status_code, 200)
        self.client.force_authenticate(admin)
        changed = self.client.patch("/api/v1/tving/entities/players/68220/", {"teamCode": "LG", "name": "변경 선수"}, format="json")
        self.assertEqual((changed.status_code, changed.data["data"]["teamCode"]), (200, "LG"))
        self.assertEqual(self.client.delete("/api/v1/tving/entities/players/68220/").status_code, 204)


class FallbackTests(TestCase):
    def test_malformed_provider_uses_relational_fallback_and_marks_stale(self):
        now = timezone.now()
        persist_daily(relational_daily(), now - timedelta(seconds=600))
        from tving.service import _fresh_or_fallback
        day = datetime.fromisoformat("2026-09-15").date()
        result = _fresh_or_fallback(lambda _previous: (_ for _ in ()).throw(TvingValidationError("partial")), persist_daily, lambda: read_daily("2026-09-15"), lambda: daily_sync_time(day), daily=True)
        self.assertTrue(result["stale"])
        self.assertIsNotNone(result["warning"])
        self.assertEqual(result["standings"][0]["teamCode"], "SS")

    def test_malformed_provider_without_db_is_typed(self):
        from tving.service import _fresh_or_fallback
        with self.assertRaises(TvingUpstreamError):
            _fresh_or_fallback(lambda _previous: (_ for _ in ()).throw(TvingValidationError("partial")), persist_daily, lambda: None, lambda: None, daily=True)

    def test_failed_write_rolls_back_complete_existing_payload(self):
        now = timezone.now()
        original = daily_payload()
        ProviderSnapshot.objects.create(resource_kind="daily", resource_key="2026-09-15", payload=original, source_fetched_at=now, last_synced_at=now - timedelta(hours=1))
        with patch.object(ProviderSnapshot, "save", side_effect=RuntimeError("disk full")), self.assertRaises(RuntimeError):
            _persist_if_due("daily", "2026-09-15", {**original, "sourceUpdatedAt": "changed"}, now)
        self.assertEqual(ProviderSnapshot.objects.get().payload, original)

    def test_redirect_handler_refuses_followup_request(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://evil.example"))


@override_settings(EXTERNAL_DATA_SYNC_INTERVAL_SECONDS=600)
class CacheFirstRefreshTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for index, (code, name) in enumerate((("SAMSUNG", "삼성 라이온즈"), ("KT", "KT 위즈"), ("LG", "LG 트윈스"), ("KIA", "KIA 타이거즈"), ("DOOSAN", "두산 베어스"), ("NC", "NC 다이노스"), ("HANWHA", "한화 이글스"), ("LOTTE", "롯데 자이언츠"), ("SSG", "SSG 랜더스"), ("KIWOOM", "키움 히어로즈")), 1):
            Team.objects.get_or_create(team_code=code, defaults={"id": 800 + index, "team_name_ko": name})

    def test_all_frontend_entrypoints_return_complete_fresh_db_without_provider(self):
        now = timezone.now()
        persist_daily(relational_daily(), now)
        month = {"year": 2026, "month": "2026-08", "today": "2026-09-16", "games": [], "days": [{"date": f"2026-08-{day:02d}", "status": "empty", "gameCount": 0} for day in range(1, 32)], "loading": False}
        from tving.relational import persist_month
        persist_month(month, now)
        persist_team(relational_team(), now)
        persist_athlete(relational_athlete(), now)
        provider = Mock(side_effect=AssertionError("fresh DB must not call TVING"))
        self.assertFalse(refresh_daily("2026-09-15", provider)["stale"])
        self.assertFalse(refresh_month("2026-08", provider)["stale"])
        self.assertFalse(refresh_team("SS", provider)["stale"])
        self.assertFalse(refresh_athlete("10001", provider)["stale"])
        provider.assert_not_called()
        with patch("tving.service._provider_json", side_effect=AssertionError("fresh HTTP entry must not call TVING")) as http_provider:
            client = APIClient()
            for path in ("/api/v1/tving/daily/?date=2026-09-15", "/api/v1/tving/schedule/?month=2026-08", "/api/v1/tving/details/teams/SS/", "/api/v1/tving/details/athletes/10001/"):
                self.assertEqual(client.get(path).status_code, 200)
        http_provider.assert_not_called()

    def test_exact_boundary_refreshes_and_persists_daily_rows(self):
        now = timezone.now()
        persist_daily(relational_daily(), now)
        changed = relational_daily()
        changed["standings"][0]["wins"] = 99
        provider = Mock(return_value={})
        with patch("tving.service.timezone.now", return_value=now + timedelta(seconds=600)), patch("tving.service.parse_schedule", return_value=[]), patch("tving.service.parse_standings", return_value=changed["standings"]), patch("tving.service.parse_rankings", side_effect=[changed["individualRankings"]["pitchers"], changed["individualRankings"]["hitters"]]):
            result = refresh_daily("2026-09-15", provider)
        self.assertGreater(provider.call_count, 0)
        self.assertEqual(result["standings"][0]["wins"], 99)
        self.assertEqual(StandingHistory.objects.get(snapshot_date="2026-09-15", rank=1).wins, 99)

    def test_absent_month_loads_and_valid_empty_month_is_cached(self):
        provider = Mock(return_value={})
        with patch("tving.service.parse_calendar", return_value=[]):
            first = refresh_month("2026-07", provider)
            calls = provider.call_count
            second = refresh_month("2026-07", provider)
        self.assertEqual(calls, 1)
        self.assertEqual(provider.call_count, calls)
        self.assertEqual((first["games"], second["games"]), ([], []))

    def test_failed_stale_refresh_keeps_timestamp_and_marks_fallback_stale(self):
        old = timezone.now() - timedelta(seconds=600)
        persist_team(relational_team(), old)
        before = read_team("SS")
        with patch("tving.service.timezone.now", return_value=old + timedelta(seconds=600)):
            result = refresh_team("SS", Mock(side_effect=TvingUpstreamError("down")))
        self.assertTrue(result["stale"])
        self.assertEqual(result["code"], before["code"])
        self.assertEqual(Team.objects.get(team_code="SAMSUNG").provider_profile.last_synced_at, old)

    def test_ranking_only_player_is_not_a_fresh_athlete_profile(self):
        persist_daily(relational_daily(), timezone.now())
        self.assertIsNone(read_athlete("10001"))
        provider = Mock(return_value={})
        with patch("tving.service.parse_athlete_detail", return_value=relational_athlete()):
            refresh_athlete("10001", provider)
        provider.assert_called_once()
        self.assertIsNotNone(read_athlete("10001"))
