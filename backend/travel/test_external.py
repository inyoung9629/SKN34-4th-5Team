from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
from datetime import datetime, timedelta
from http.client import HTTPException
from urllib.error import HTTPError
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.apps import apps
from django.db import connection, connections
from django.utils import timezone
from rest_framework.test import APIClient

from .directions_provider import DirectionsError, _NoRedirect, _get, _slots, fetch_directions, parse_directions
from .directions_models import DirectionsRoute
from .directions_service import delete_route, get_route, list_routes, replace_route, sync_route
from .external_snapshot_service import delete_snapshot, get_snapshot, replace_snapshot, sync_snapshot
from .models import ExternalProviderSnapshot


POINTS = [{"lat": 37.5, "lng": 127.1}, {"lat": 37.6, "lng": 127.2}]
STEP = {"path": {"points": [[127.1, 37.5], [127.2, 37.6]]}, "properties": {"guidance": "횡단보도를 건너세요"}}
WALK = {"status": "OK", "route": {"properties": {"totalDistance": 100, "totalTime": 70}, "legs": [{"steps": [STEP]}]}}
SNAPSHOT_KEY = "walk:127.100000,37.500000:127.200000,37.600000"
SNAPSHOT_REQUEST = {"mode": "walk", "start": POINTS[0], "end": POINTS[1]}
SNAPSHOT_PAYLOAD = {"status": "ok", "distance": 100, "seconds": 70, "paths": [[POINTS[0], POINTS[1]]], "instructions": ["이동"]}


class SnapshotSyncTests(TestCase):
    def test_fresh_exact_and_future_rows_are_not_updated(self):
        initial = timezone.now()
        snapshot = sync_snapshot("directions", SNAPSHOT_KEY, SNAPSHOT_REQUEST, SNAPSHOT_PAYLOAD, fetched_at=initial)
        for moment in (initial + timedelta(seconds=599), initial + timedelta(seconds=600), initial - timedelta(seconds=1)):
            with CaptureQueriesContext(connection) as queries:
                returned = sync_snapshot("directions", SNAPSHOT_KEY, SNAPSHOT_REQUEST, {**SNAPSHOT_PAYLOAD, "seconds": 80}, fetched_at=moment)
            self.assertEqual(returned.pk, snapshot.pk)
            self.assertFalse(any(query["sql"].lstrip().upper().startswith("UPDATE") for query in queries))
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.payload, SNAPSHOT_PAYLOAD)
        self.assertEqual(snapshot.last_synced_at, initial)

    def test_null_stale_and_custom_interval_update(self):
        initial = timezone.now()
        snapshot = ExternalProviderSnapshot.objects.create(kind="directions", key=SNAPSHOT_KEY, request={}, payload={}, last_synced_at=None)
        sync_snapshot("directions", snapshot.key, SNAPSHOT_REQUEST, SNAPSHOT_PAYLOAD, fetched_at=initial)
        with override_settings(EXTERNAL_DATA_SYNC_INTERVAL_SECONDS=120):
            sync_snapshot("directions", snapshot.key, SNAPSHOT_REQUEST, {**SNAPSHOT_PAYLOAD, "seconds": 80}, fetched_at=initial + timedelta(seconds=121))
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.payload["seconds"], 80)

    def test_validation_and_trusted_manual_mutations(self):
        for args in (("weather", "key", {}, {}), ("directions", "", {}, {}), ("directions", "key", [], {}), ("directions", SNAPSHOT_KEY, SNAPSHOT_REQUEST, {**SNAPSHOT_PAYLOAD, "distance": float("nan")})):
            with self.assertRaises(ValueError):
                sync_snapshot(*args)
        user = get_user_model().objects.create_user(username="snapshot-user", email="user@example.com", password="test-pass")
        with self.assertRaises(PermissionError):
            replace_snapshot(actor=user, kind="directions", key=SNAPSHOT_KEY, request=SNAPSHOT_REQUEST, payload=SNAPSHOT_PAYLOAD)
        user.is_staff = True
        user.is_active = False
        with self.assertRaises(PermissionError):
            replace_snapshot(actor=user, kind="directions", key=SNAPSHOT_KEY, request=SNAPSHOT_REQUEST, payload=SNAPSHOT_PAYLOAD)
        with self.assertRaises(PermissionError):
            delete_snapshot(actor=user, snapshot_id=1)
        user.is_active = True
        snapshot = replace_snapshot(actor=user, kind="directions", key=SNAPSHOT_KEY, request=SNAPSHOT_REQUEST, payload=SNAPSHOT_PAYLOAD)
        self.assertIsNone(snapshot.fetched_at)
        self.assertIsNone(snapshot.last_synced_at)
        synced_at = timezone.now()
        snapshot.last_synced_at = snapshot.fetched_at = synced_at
        snapshot.save()
        replace_snapshot(actor=user, kind="directions", key=SNAPSHOT_KEY, request=SNAPSHOT_REQUEST, payload={**SNAPSHOT_PAYLOAD, "seconds": 80})
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.fetched_at, synced_at)
        self.assertEqual(snapshot.last_synced_at, synced_at)
        self.assertEqual(delete_snapshot(actor=user, snapshot_id=snapshot.pk), 1)

    def test_fetched_at_and_provider_payloads_are_strict(self):
        for value in (False, "2026-09-15", datetime.now()):
            with self.assertRaises(ValueError):
                sync_snapshot("directions", SNAPSHOT_KEY, SNAPSHOT_REQUEST, SNAPSHOT_PAYLOAD, fetched_at=value)
        poisoned = ExternalProviderSnapshot.objects.create(kind="directions", key=SNAPSHOT_KEY, request=SNAPSHOT_REQUEST, payload={"status": "ok"}, last_synced_at=timezone.now())
        self.assertIsNone(get_snapshot("directions", poisoned.key))
        sync_snapshot("directions", SNAPSHOT_KEY, SNAPSHOT_REQUEST, SNAPSHOT_PAYLOAD)
        poisoned.refresh_from_db()
        self.assertEqual(poisoned.payload, SNAPSHOT_PAYLOAD)
        tourism_request = {"stadium": "JAMSIL", "lat": 37.516199, "lng": 127.075941, "radius": 2500, "contentTypes": ["12", "14", "28"], "pageSize": 100, "maxPages": 3}
        tourism_key = hashlib.sha256(json.dumps(tourism_request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        tourism_payload = {"status": "ok", "places": [{"placeId": "tour:1", "tourContentId": "1", "name": "공원", "lat": 37.51, "lng": 127.07, "category": "산책", "kind": "walk", "cuisine": "기타", "address": "", "phone": "", "detail": "산책 · 한국관광공사", "distance": 100.0}], "truncated": False}
        self.assertEqual(sync_snapshot("tourism", tourism_key, tourism_request, tourism_payload).kind, "tourism")
        with self.assertRaises(ValueError):
            sync_snapshot("tourism", "bad-key", tourism_request, tourism_payload)
        with self.assertRaises(ValueError):
            sync_snapshot("tourism", tourism_key, {**tourism_request, "radius": 2500.0}, tourism_payload)


@override_settings(KAKAO_REST_API_KEY="test-key")
class DirectionsTests(TestCase):
    def test_modes_parse_provider_values_without_estimates(self):
        walk = parse_directions("walk", WALK)
        self.assertEqual((walk["distance"], walk["seconds"]), (100, 70))
        transit = parse_directions("transit", {"status": "OK", "routes": [{"properties": {"totalDistance": 500, "totalTime": 900}, "steps": [STEP]}, {"properties": {"totalDistance": 300, "totalTime": 450}, "steps": [STEP]}]})
        self.assertEqual(transit["seconds"], 450)
        car = parse_directions("car", {"routes": [{"result_code": 0, "summary": {"duration": 300, "distance": 850}, "sections": [{"roads": [{"name": "올림픽로", "vertexes": [127.1, 37.5, 127.2, 37.6]}]}]}]})
        self.assertEqual(car["paths"][0][0], {"lat": 37.5, "lng": 127.1})
        with self.assertRaises(DirectionsError):
            parse_directions("car", {"routes": [{"result_code": 104}]})
        for malformed in ({"status": []}, {"status": {}}, [], None):
            with self.assertRaises(DirectionsError):
                parse_directions("walk", malformed)
        for vertices in ([127.1, 37.5, 127.2], [127.1, 37.5, "bad", 37.6]):
            with self.assertRaises(DirectionsError):
                parse_directions("car", {"routes": [{"result_code": 0, "summary": {"duration": 1, "distance": 1}, "sections": [{"roads": [{"vertexes": vertices}]}]}]})
        with self.assertRaises(DirectionsError):
            parse_directions("walk", {"status": "OK", "route": {"properties": {"totalDistance": 1, "totalTime": 1}, "legs": [{"steps": [{"path": {"points": [[127.1, 37.5], [127.2]]}}]}]}})
        with self.assertRaises(DirectionsError):
            parse_directions("walk", {"status": "OK", "route": {"properties": {"totalDistance": 0, "totalTime": 0}, "legs": [{"steps": []}]}})

    def test_request_fetches_even_when_fresh_but_does_not_slide_timestamp(self):
        initial = timezone.now()
        calls = []
        fetcher = lambda host, path, params, key: calls.append((host, path, key)) or WALK
        fetch_directions("walk", POINTS, fetcher=fetcher, fetched_at=initial)
        for moment in (initial + timedelta(seconds=30), initial + timedelta(seconds=600), initial - timedelta(seconds=1)):
            with CaptureQueriesContext(connection) as queries:
                fetch_directions("walk", POINTS, fetcher=fetcher, fetched_at=moment)
            self.assertFalse(any(query["sql"].lstrip().upper().startswith("UPDATE") for query in queries))
        route = DirectionsRoute.objects.get()
        self.assertEqual(len(calls), 4)
        self.assertEqual(route.last_synced_at, initial)
        fetch_directions("walk", POINTS, fetcher=fetcher, fetched_at=initial + timedelta(seconds=601))
        route.refresh_from_db()
        self.assertEqual(route.last_synced_at, initial + timedelta(seconds=601))
        self.assertEqual(calls[0][:2], ("dapi.kakao.com", "/v2/routing/walk"))

    def test_failure_preserves_prior_payload_and_nulls_totals(self):
        initial = timezone.now()
        fetch_directions("walk", POINTS, fetcher=lambda *args: WALK, fetched_at=initial)
        result = fetch_directions("walk", POINTS, fetcher=lambda *args: (_ for _ in ()).throw(DirectionsError()))
        self.assertEqual(result["legs"][0]["status"], "error")
        self.assertTrue(result["legs"][0]["stale"])
        self.assertEqual(result["legs"][0]["distance"], 100)
        self.assertIsNone(result["distance"])
        self.assertIsNone(result["seconds"])

    def test_same_point_never_calls_provider(self):
        with override_settings(KAKAO_REST_API_KEY=""):
            result = fetch_directions("car", [POINTS[0], POINTS[0]], fetcher=lambda *args: self.fail("provider called"))
        self.assertEqual(result["distance"], 0)

    def test_direct_calls_validate_before_key_or_network(self):
        called = False
        def fetcher(*args):
            nonlocal called
            called = True
        with override_settings(KAKAO_REST_API_KEY=""):
            for mode, points in (("fly", POINTS), ("walk", POINTS[:1]), ("walk", [POINTS[0], {"lat": float("inf"), "lng": 127}]), ("walk", [POINTS[0], {"lat": True, "lng": 127}])):
                with self.assertRaises(DirectionsError) as error:
                    fetch_directions(mode, points, fetcher=fetcher)
                self.assertEqual(error.exception.status, 400)
        self.assertFalse(called)

    def test_http_client_rejects_redirects_and_preserves_rate_limit_status(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.test"))
        opener = type("Opener", (), {"open": lambda self, *args, **kwargs: (_ for _ in ()).throw(HTTPError("redacted", 429, "limited", {}, None))})()
        with patch("travel.directions_provider.build_opener", return_value=opener), self.assertRaises(DirectionsError) as error:
            _get("car", {}, "secret")
        self.assertEqual(error.exception.status, 429)
        with patch.object(_slots, "acquire", return_value=False), self.assertRaises(DirectionsError) as busy:
            _get("car", {}, "secret", lambda *args: {})
        self.assertEqual(busy.exception.status, 429)
        with patch.object(_slots, "acquire", return_value=True), patch.object(_slots, "release") as release, self.assertRaises(DirectionsError):
            _get("car", {}, "secret", lambda *args: (_ for _ in ()).throw(HTTPException("private transport detail")))
        release.assert_called_once_with()

    def test_http_validation_rejects_non_finite_bounds_and_malformed_values(self):
        client = APIClient()
        good = {"mode": "walk", "points": POINTS}
        with patch("travel.views.fetch_directions", return_value={"mode": "walk", "legs": [], "distance": 0, "seconds": 0}) as fetch:
            self.assertEqual(client.post("/api/v1/travel/directions/", good, format="json").status_code, 200)
            for body in ({**good, "mode": {"x": 1}}, {**good, "mode": "fly"}, {**good, "unexpected": True}, {**good, "points": POINTS[:1]}, {**good, "points": [POINTS[0], {"lat": "37.6", "lng": 127}]}, {**good, "points": [POINTS[0], {"lat": True, "lng": 127}]}, {**good, "points": [POINTS[0], {"lat": "NaN", "lng": 127}]}, {**good, "points": [POINTS[0], {"lat": 91, "lng": 127}]}):
                self.assertEqual(client.post("/api/v1/travel/directions/", body, format="json").status_code, 400)
            self.assertEqual(fetch.call_count, 1)
        with override_settings(KAKAO_REST_API_KEY=""):
            self.assertEqual(client.post("/api/v1/travel/directions/", good, format="json").status_code, 503)


class SnapshotConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_first_sync_has_one_identity(self):
        when = timezone.now()
        def create(value):
            try:
                return sync_route("walk", POINTS[0], POINTS[1], {**SNAPSHOT_PAYLOAD, "seconds": value}, fetched_at=when).pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(create, (1, 2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(DirectionsRoute.objects.count(), 1)


class DirectionsRouteServiceTests(TestCase):
    def test_canonical_identity_filters_and_staff_crud_preserve_timestamps(self):
        initial = timezone.now()
        route = sync_route("walk", POINTS[0], POINTS[1], SNAPSHOT_PAYLOAD, fetched_at=initial)
        same = get_route("walk", {"lat": 37.5000004, "lng": 127.1000004}, {"lat": 37.6000004, "lng": 127.2000004})
        self.assertEqual(same.pk, route.pk)
        self.assertEqual([item.pk for item in list_routes(mode="walk", min_distance=50, max_distance=150)], [route.pk])
        staff = get_user_model().objects.create_user(username="route-admin", password="test-pass", is_staff=True)
        replace_route(actor=staff, mode="walk", start=POINTS[0], end=POINTS[1], payload={**SNAPSHOT_PAYLOAD, "seconds": 80})
        route.refresh_from_db()
        self.assertEqual(route.duration, 80)
        self.assertEqual(route.last_synced_at, initial)
        staff.is_active = False
        with self.assertRaises(PermissionError):
            delete_route(actor=staff, route_id=route.pk)
        staff.is_active = True
        self.assertEqual(delete_route(actor=staff, route_id=route.pk), 1)

    def test_legacy_backfill_is_repeatable_and_preserves_unknown_rows(self):
        valid = ExternalProviderSnapshot.objects.create(kind="directions", key=SNAPSHOT_KEY, request=SNAPSHOT_REQUEST, payload=SNAPSHOT_PAYLOAD, fetched_at=timezone.now(), last_synced_at=timezone.now())
        unknown = ExternalProviderSnapshot.objects.create(kind="directions", key="unknown", request={}, payload={})
        backfill = importlib.import_module("travel.migrations.0007_directionsroute").backfill_directions
        backfill(apps, None)
        backfill(apps, None)
        self.assertEqual(DirectionsRoute.objects.count(), 1)
        self.assertEqual(ExternalProviderSnapshot.objects.filter(pk__in=(valid.pk, unknown.pk)).count(), 2)
