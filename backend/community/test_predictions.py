import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone as datetime_timezone
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import GamePrediction, PredictionGame
from .prediction_source import PredictionSourceError, fetch_prediction_snapshot, sync_prediction_games


class SourceResponse:
    status = 200

    def __init__(self, payload, *, url="http://127.0.0.1:8000/api/v1/tving/daily/", content_length=None):
        self.body = BytesIO(json.dumps(payload).encode())
        self.headers = {"Content-Type": "application/json"}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size):
        return self.body.read(size)

    def geturl(self):
        return self.url


def source_payload(now, **game_changes):
    today = now.astimezone(datetime_timezone(timedelta(hours=9))).date().isoformat()
    game = {
        "id": "20260915-LG-OB-1",
        "date": today,
        "startsAt": now.astimezone(datetime_timezone(timedelta(hours=9))).replace(hour=18, minute=30, second=0, microsecond=0).isoformat(),
        "stadium": "잠실",
        "away": {"code": "LG", "name": "LG", "score": None},
        "home": {"code": "OB", "name": "두산", "score": None},
        "status": "scheduled",
    }
    game.update(game_changes)
    return {"data": {"date": today, "games": [game], "fetchedAt": now.isoformat(), "nextCheckAt": (now + timedelta(minutes=5)).isoformat(), "stale": False}, "error": None}


class PredictionSourceTests(TestCase):
    def test_default_source_calls_tving_service_without_http_loop(self):
        now = datetime(2026, 9, 15, 1, tzinfo=datetime_timezone.utc)
        with patch.dict(os.environ, {"PREDICTION_SOURCE_URL": ""}), patch("tving.service.refresh_daily", return_value=source_payload(now)["data"]) as refresh:
            games = fetch_prediction_snapshot(now)
        refresh.assert_called_once_with("2026-09-15")
        self.assertEqual(games[0]["source_id"], "20260915-LG-OB-1")

    def test_source_rejects_stale_and_keeps_stable_game_id(self):
        now = datetime(2026, 9, 15, 1, tzinfo=datetime_timezone.utc)
        games = fetch_prediction_snapshot(now, lambda *_args, **_kwargs: SourceResponse(source_payload(now)))
        self.assertEqual(games[0]["source_id"], "20260915-LG-OB-1")
        stale = source_payload(now)
        stale["data"]["stale"] = True
        with self.assertRaisesRegex(PredictionSourceError, "지연"):
            fetch_prediction_snapshot(now, lambda *_args, **_kwargs: SourceResponse(stale))

    def test_malformed_metadata_is_a_source_error(self):
        now = datetime(2026, 9, 15, 1, tzinfo=datetime_timezone.utc)
        invalid = source_payload(now, status=[])
        with self.assertRaises(PredictionSourceError):
            fetch_prediction_snapshot(now, lambda *_args, **_kwargs: SourceResponse(invalid))

    def test_redirect_and_nonnumeric_content_length_are_source_errors(self):
        now = datetime(2026, 9, 15, 1, tzinfo=datetime_timezone.utc)
        with self.assertRaises(PredictionSourceError):
            fetch_prediction_snapshot(now, lambda *_args, **_kwargs: SourceResponse(source_payload(now), url="http://other/kbo-api"))
        with self.assertRaises(PredictionSourceError):
            fetch_prediction_snapshot(now, lambda *_args, **_kwargs: SourceResponse(source_payload(now), content_length="many"))

    def test_missing_game_and_changed_identity_reject_the_whole_snapshot(self):
        now = datetime(2026, 9, 15, 1, tzinfo=datetime_timezone.utc)
        PredictionGame.objects.create(
            source_id="missing-game", game_date=now.astimezone(datetime_timezone(timedelta(hours=9))).date(),
            starts_at=now + timedelta(hours=2), stadium="잠실", away_team_code="LG", away_team_name="LG",
            home_team_code="OB", home_team_name="두산", status="scheduled", source_fetched_at=now,
        )
        with self.assertRaisesRegex(PredictionSourceError, "누락"):
            sync_prediction_games(now, lambda *_args, **_kwargs: SourceResponse(source_payload(now)))
        PredictionGame.objects.all().delete()
        payload = source_payload(now)
        PredictionGame.objects.create(
            source_id=payload["data"]["games"][0]["id"], game_date=now.astimezone(datetime_timezone(timedelta(hours=9))).date(),
            starts_at=now + timedelta(hours=2), stadium="잠실", away_team_code="LG", away_team_name="LG",
            home_team_code="SS", home_team_name="삼성", status="scheduled", source_fetched_at=now,
        )
        with self.assertRaisesRegex(PredictionSourceError, "대진"):
            sync_prediction_games(now, lambda *_args, **_kwargs: SourceResponse(payload))

    def test_final_draw_and_cancelled_game_are_fail_closed(self):
        now = datetime(2026, 9, 15, 1, tzinfo=datetime_timezone.utc)
        final = source_payload(now, status="final", away={"code": "LG", "name": "LG", "score": 2}, home={"code": "OB", "name": "두산", "score": 2})
        sync_prediction_games(now, lambda *_args, **_kwargs: SourceResponse(final))
        game = PredictionGame.objects.get()
        self.assertEqual(game.result, "draw")
        self.assertIsNotNone(game.locked_at)
        PredictionGame.objects.all().delete()
        cancelled = source_payload(now, status="cancelled")
        sync_prediction_games(now, lambda *_args, **_kwargs: SourceResponse(cancelled))
        game = PredictionGame.objects.get()
        self.assertIsNotNone(game.locked_at)
        self.assertIsNotNone(game.voided_at)
        invalid = source_payload(now)
        invalid["data"]["fetchedAt"] = "2026-02-30T00:00:00Z"
        with self.assertRaises(PredictionSourceError):
            fetch_prediction_snapshot(now, lambda *_args, **_kwargs: SourceResponse(invalid))


class PredictionApiTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.game = PredictionGame.objects.create(
            source_id="game-1", game_date=self.now.astimezone(datetime_timezone(timedelta(hours=9))).date(),
            starts_at=self.now + timedelta(hours=1), stadium="잠실", away_team_code="LG", away_team_name="LG",
            home_team_code="OB", home_team_name="두산", status="scheduled", source_fetched_at=self.now,
        )
        self.user = get_user_model().objects.create_user(username="prediction-user", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch("community.predictions.sync_prediction_games", return_value=1)
    def test_vote_changes_cancels_and_never_duplicates(self, _sync):
        url = f"/api/v1/community/predictions/games/{self.game.pk}/vote/"
        self.assertEqual(self.client.post(url, {"choice": "home"}, format="json").status_code, 200)
        self.assertEqual(self.client.post(url, {"choice": "home"}, format="json").json()["votes"]["total"], 1)
        self.assertEqual(self.client.post(url, {"choice": "away"}, format="json").json()["myChoice"], "away")
        self.assertEqual(GamePrediction.objects.filter(game=self.game, user=self.user).count(), 1)
        self.assertEqual(self.client.post(url, {"choice": None}, format="json").json()["votes"]["total"], 0)

    @patch("community.predictions.sync_prediction_games", return_value=1)
    def test_server_cutoff_is_fail_closed(self, _sync):
        self.game.starts_at = self.now
        self.game.save(update_fields=("starts_at",))
        response = self.client.post(f"/api/v1/community/predictions/games/{self.game.pk}/vote/", {"choice": "home"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.game.refresh_from_db()
        self.assertIsNotNone(self.game.locked_at)

    @patch("community.predictions.sync_prediction_games", side_effect=PredictionSourceError("경기 원천 갱신이 지연되었습니다."))
    def test_source_failure_blocks_vote(self, _sync):
        response = self.client.post(f"/api/v1/community/predictions/games/{self.game.pk}/vote/", {"choice": "home"}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(GamePrediction.objects.count(), 0)

    @patch("community.predictions.sync_prediction_games", return_value=1)
    def test_date_team_filter_and_zero_vote_percentages(self, _sync):
        PredictionGame.objects.create(
            source_id="other-game", game_date=self.game.game_date, starts_at=self.now + timedelta(hours=2), stadium="대구",
            away_team_code="HH", away_team_name="한화", home_team_code="SS", home_team_name="삼성",
            status="scheduled", source_fetched_at=self.now,
        )
        response = self.client.get(f"/api/v1/community/predictions/games/?date={self.game.game_date}&team=LG")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["votes"], {"home": 0, "away": 0, "total": 0, "homePercent": 0, "awayPercent": 0})

    @patch("community.predictions.sync_prediction_games", return_value=1)
    def test_unhashable_choice_is_rejected(self, _sync):
        response = self.client.post(f"/api/v1/community/predictions/games/{self.game.pk}/vote/", {"choice": []}, format="json")
        self.assertEqual(response.status_code, 400)


class PredictionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.game = PredictionGame.objects.create(
            source_id="locked-game", game_date=self.now.astimezone(datetime_timezone(timedelta(hours=9))).date(),
            starts_at=self.now + timedelta(hours=1), stadium="잠실", away_team_code="LG", away_team_name="LG",
            home_team_code="OB", home_team_name="두산", status="scheduled", source_fetched_at=self.now,
        )
        self.user = get_user_model().objects.create_user(username="lock-user", password="test-password")

    @patch("community.predictions.sync_prediction_games", return_value=1)
    def test_vote_waiting_on_row_lock_rechecks_server_deadline(self, _sync):
        started = threading.Event()
        result = []

        def vote():
            close_old_connections()
            client = APIClient()
            client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
            started.set()
            result.append(client.post(f"/api/v1/community/predictions/games/{self.game.pk}/vote/", {"choice": "home"}, format="json").status_code)
            close_old_connections()

        with transaction.atomic():
            game = PredictionGame.objects.select_for_update().get(pk=self.game.pk)
            worker = threading.Thread(target=vote)
            worker.start()
            self.assertTrue(started.wait(2))
            time.sleep(0.2)
            game.starts_at = timezone.now()
            game.save(update_fields=("starts_at",))
        worker.join(5)
        self.assertEqual(result, [409])
        self.assertEqual(GamePrediction.objects.count(), 0)

    def test_parallel_first_sync_creates_one_game(self):
        PredictionGame.objects.all().delete()
        barrier = threading.Barrier(2)
        errors = []

        def opener(*_args, **_kwargs):
            barrier.wait(2)
            return SourceResponse(source_payload(self.now))

        def synchronize():
            close_old_connections()
            try:
                sync_prediction_games(self.now, opener)
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        workers = [threading.Thread(target=synchronize) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(PredictionGame.objects.count(), 1)
