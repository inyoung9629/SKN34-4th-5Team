import os
import subprocess
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, BoundedSemaphore
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from .models import Place
from .place_service import (
    PlaceAuthorizationError,
    PlaceConfigurationError,
    PlaceConflictError,
    PlaceNotFoundError,
    PlaceRateLimitError,
    PlaceUpstreamError,
    PlaceValidationError,
    create_place,
    delete_place,
    get_place,
    list_places,
    _request_kakao,
    search_and_sync_places,
    update_place,
)


def manual_place(**changes):
    data = {
        "name": "수동 장소",
        "address": "서울 송파구",
        "lat": 37.5,
        "lng": 127.1,
        "url": "https://place.map.kakao.com/1",
    }
    data.update(changes)
    return data


def query(**changes):
    data = {"method": "keyword", "keyword": "야구장", "lat": 37.5, "lng": 127.1, "page": 1, "size": 15, "sort": "distance"}
    data.update(changes)
    return data


def document(place_id="1", name="외부 장소", x="127.0800", y="37.5100"):
    return {
        "id": place_id,
        "place_name": name,
        "road_address_name": "서울 송파구 올림픽로 25",
        "address_name": "서울 송파구 잠실동 10",
        "category_group_name": "문화시설",
        "category_group_code": "CT1",
        "category_name": "스포츠 > 경기장",
        "phone": "02-123-4567",
        "place_url": f"https://place.map.kakao.com/{place_id}",
        "x": x,
        "y": y,
    }


def payload(*documents, is_end=True):
    return {"meta": {"is_end": is_end}, "documents": list(documents)}


class PlaceServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(username="place-staff", is_staff=True)
        cls.member = get_user_model().objects.create_user(username="place-member")

    def test_crud_requires_explicit_staff_and_keeps_ids_and_timestamps_immutable(self):
        with self.assertRaises(PlaceAuthorizationError):
            create_place(manual_place(), actor=self.member)
        created = create_place(manual_place(kakao_place_id="manual-1"), actor=self.staff)
        self.assertEqual(get_place(created["id"])["name"], "수동 장소")
        self.assertEqual(list_places({"name": "수동"})["count"], 1)
        with self.assertRaises(PlaceValidationError):
            update_place(created["id"], {"kakao_place_id": "changed"}, actor=self.staff)
        with self.assertRaises(PlaceValidationError):
            update_place(created["id"], {"updated_at": "2020-01-01"}, actor=self.staff)
        updated = update_place(created["id"], {"name": "변경 장소"}, actor=self.staff)
        self.assertEqual((updated["id"], updated["kakao_place_id"], updated["name"]), (created["id"], "manual-1", "변경 장소"))
        delete_place(created["id"], actor=self.staff)
        self.assertFalse(Place.objects.filter(pk=created["id"]).exists())
        with self.assertRaises(PlaceNotFoundError):
            get_place(created["id"])
        create_place(manual_place(kakao_place_id="duplicate"), actor=self.staff)
        with self.assertRaises(PlaceConflictError):
            create_place(manual_place(kakao_place_id="duplicate"), actor=self.staff)
        create_place(manual_place(name="수동 1"), actor=self.staff)
        create_place(manual_place(name="수동 2"), actor=self.staff)
        self.assertEqual(Place.objects.filter(kakao_place_id__isnull=True).count(), 2)
        for invalid in (False, [], ""):
            with self.subTest(invalid=invalid), self.assertRaises(PlaceValidationError):
                list_places(invalid)

    def test_search_validates_before_upstream_and_returns_current_provider_page(self):
        invalid = (
            query(lat=True), query(lng="127.1"), query(page=4), query(size=16), query(radius=0),
            query(sort="other"), query(method="category", keyword="", category="XX"), {**query(), "action": "places"},
        )
        with patch("travel.place_service._request_kakao") as upstream:
            for item in invalid:
                with self.subTest(item=item), self.assertRaises(PlaceValidationError):
                    search_and_sync_places(item)
            upstream.assert_not_called()

        with patch("travel.place_service._request_kakao", return_value=payload(document(), is_end=False)):
            first = search_and_sync_places(query())
        self.assertEqual((first["places"][0]["id"], first["places"][0]["x"], first["hasNextPage"]), ("1", "127.0800", True))
        self.assertEqual(first["places"][0]["place_url"], "https://place.map.kakao.com/1")
        saved = Place.objects.get(kakao_place_id="1")
        first_sync = saved.last_synced_at
        with patch("travel.place_service._request_kakao", return_value=payload(document(name="새 이름"))):
            second = search_and_sync_places(query())
        saved.refresh_from_db()
        self.assertEqual((Place.objects.filter(kakao_place_id="1").count(), saved.name), (1, "외부 장소"))
        self.assertEqual(saved.last_synced_at, first_sync)
        self.assertEqual(second["places"][0]["place_name"], "새 이름")
        self.assertIn("syncedAt", second)

    @override_settings(EXTERNAL_DATA_SYNC_INTERVAL_SECONDS=600)
    def test_search_sync_interval_handles_fresh_exact_future_stale_null_new_and_mixed_rows(self):
        self.assertEqual(settings.EXTERNAL_DATA_SYNC_INTERVAL_SECONDS, 600)
        now = datetime(2026, 9, 15, 3, tzinfo=UTC)
        marker = now - timedelta(days=1)
        ages = {
            "fresh": now - timedelta(minutes=9, seconds=59),
            "exact": now - timedelta(minutes=10),
            "future": now + timedelta(days=1),
            "stale": now - timedelta(minutes=10, microseconds=1),
            "null": None,
        }
        for place_id, last_synced_at in ages.items():
            place = Place.objects.create(**manual_place(kakao_place_id=place_id, name=f"기존 {place_id}"))
            Place.objects.filter(pk=place.pk).update(updated_at=marker, last_synced_at=last_synced_at)

        documents = [document(place_id, name=f"공급자 {place_id}") for place_id in (*ages, "new")]
        with (
            patch("travel.place_service.timezone.now", return_value=now),
            patch("travel.place_service._request_kakao", return_value=payload(*documents)),
            CaptureQueriesContext(connection) as queries,
        ):
            result = search_and_sync_places(query())

        updates = [item["sql"] for item in queries.captured_queries if item["sql"].lstrip().upper().startswith("UPDATE")]
        self.assertEqual(len(updates), 2)
        self.assertEqual([item["place_name"] for item in result["places"]], [f"공급자 {place_id}" for place_id in (*ages, "new")])
        for place_id in ("fresh", "exact", "future"):
            saved = Place.objects.get(kakao_place_id=place_id)
            self.assertEqual((saved.name, saved.updated_at, saved.last_synced_at), (f"기존 {place_id}", marker, ages[place_id]))
        for place_id in ("stale", "null", "new"):
            saved = Place.objects.get(kakao_place_id=place_id)
            self.assertEqual((saved.name, saved.last_synced_at), (f"공급자 {place_id}", now))

        next_check = now + timedelta(minutes=10)
        with (
            patch("travel.place_service.timezone.now", return_value=next_check),
            patch("travel.place_service._request_kakao", return_value=payload(document("stale", name="아직 갱신 안 함"))),
            CaptureQueriesContext(connection) as queries,
        ):
            search_and_sync_places(query())
        self.assertFalse(any(item["sql"].lstrip().upper().startswith("UPDATE") for item in queries.captured_queries))
        refreshed = Place.objects.get(kakao_place_id="stale")
        self.assertEqual((refreshed.name, refreshed.last_synced_at), ("공급자 stale", now))

    def test_internal_kakao_rows_are_neither_returned_nor_synced_even_when_cached(self):
        old = Place.objects.create(**manual_place(kakao_place_id="inside", name="기존 내부 장소"))
        inside = document("inside", x="127.0719", y="37.5122")
        unseen = document("new-inside", x="127.0719", y="37.5122")
        with patch("travel.place_service._request_kakao", return_value=payload(inside, unseen, document("outside"), is_end=False)):
            result = search_and_sync_places(query())
        self.assertEqual([p["id"] for p in result["places"]], ["outside"])
        self.assertTrue(result["hasNextPage"])
        self.assertFalse(Place.objects.filter(kakao_place_id="new-inside").exists())
        old.refresh_from_db()
        self.assertEqual(old.name, "기존 내부 장소")
        self.assertIsNone(old.last_synced_at)

    @override_settings(EXTERNAL_DATA_SYNC_INTERVAL_SECONDS=120)
    def test_search_sync_interval_honors_runtime_override_and_exact_boundary(self):
        now = datetime(2026, 9, 15, 3, tzinfo=UTC)
        for place_id, age in (("exact", timedelta(seconds=120)), ("stale", timedelta(seconds=120, microseconds=1))):
            Place.objects.create(**manual_place(kakao_place_id=place_id, name=f"기존 {place_id}"), last_synced_at=now - age)

        with patch("travel.place_service.timezone.now", return_value=now), patch(
            "travel.place_service._request_kakao",
            return_value=payload(document("exact", name="경계"), document("stale", name="갱신")),
        ):
            search_and_sync_places(query())

        self.assertEqual(Place.objects.get(kakao_place_id="exact").name, "기존 exact")
        self.assertEqual(Place.objects.get(kakao_place_id="stale").name, "갱신")

    def test_sync_interval_rejects_non_positive_or_malformed_environment_values(self):
        for value in ("0", "-1", "invalid"):
            environment = os.environ | {"EXTERNAL_DATA_SYNC_INTERVAL_SECONDS": value}
            loaded = subprocess.run(
                [sys.executable, "-c", "import config.settings"],
                cwd=settings.BASE_DIR,
                env=environment,
                capture_output=True,
                text=True,
            )
            with self.subTest(value=value):
                self.assertNotEqual(loaded.returncode, 0)
                self.assertIn("EXTERNAL_DATA_SYNC_INTERVAL_SECONDS must be a positive integer", loaded.stderr)

    def test_invalid_upstream_or_mid_page_failure_never_reports_partial_success(self):
        malformed = (
            None, {}, {"meta": {"is_end": "yes"}, "documents": []}, payload({"id": "bad"}),
            payload(document(x="NaN")), payload(document() | {"place_url": "https://evil.example/1"}),
            payload(document(), document()),
        )
        for value in malformed:
            with self.subTest(value=value), patch("travel.place_service._request_kakao", return_value=value), self.assertRaises(PlaceUpstreamError):
                search_and_sync_places(query())
        self.assertEqual(Place.objects.count(), 0)

        locked = Place.objects.select_for_update()
        real = locked.get_or_create
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("db failure")
            return real(*args, **kwargs)

        with patch("travel.place_service._request_kakao", return_value=payload(document("1"), document("2"))), patch.object(Place.objects, "select_for_update", return_value=locked), patch.object(locked, "get_or_create", side_effect=fail_second), self.assertRaises(RuntimeError):
            search_and_sync_places(query())
        self.assertEqual(Place.objects.count(), 0)

    def test_empty_result_does_not_delete_existing_place(self):
        existing = Place.objects.create(**manual_place(kakao_place_id="keep"))
        with patch("travel.place_service._request_kakao", return_value=payload()):
            result = search_and_sync_places(query())
        self.assertEqual(result["places"], [])
        self.assertTrue(Place.objects.filter(pk=existing.pk).exists())

    @override_settings(KAKAO_REST_API_KEY="test-key")
    def test_upstream_request_is_fixed_bounded_non_redirecting_and_safe_on_transport_errors(self):
        class Response:
            headers = {"Content-Length": "46"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                self.limit = limit
                return b'{"meta":{"is_end":true},"documents":[]}'

        response = Response()
        opener = type("Opener", (), {"open": lambda self, request, timeout: response})()
        with patch("travel.place_service.build_opener", return_value=opener) as builder:
            result = _request_kakao(query())
        handler = builder.call_args.args[0]
        self.assertEqual(handler.redirect_request(None, None, 302, "", {}, "https://evil.example"), None)
        self.assertTrue(builder.called)
        self.assertTrue(result["meta"]["is_end"])
        self.assertEqual(response.limit, 262145)

        for error in (HTTPError("https://dapi.kakao.com", 429, "secret", {}, None), URLError("secret"), TimeoutError("secret")):
            failing = type("Opener", (), {"open": lambda self, request, timeout, error=error: (_ for _ in ()).throw(error)})()
            with self.subTest(error=type(error).__name__), patch("travel.place_service.build_opener", return_value=failing), self.assertRaises(PlaceUpstreamError):
                _request_kakao(query())

        oversized = Response()
        oversized.headers = {"Content-Length": "262145"}
        opener = type("Opener", (), {"open": lambda self, request, timeout: oversized})()
        with patch("travel.place_service.build_opener", return_value=opener), self.assertRaises(PlaceUpstreamError):
            _request_kakao(query())

    @override_settings(KAKAO_REST_API_KEY="")
    def test_missing_key_fails_without_network(self):
        with patch("travel.place_service.build_opener") as opener, self.assertRaises(PlaceConfigurationError):
            _request_kakao(query())
        opener.assert_not_called()

    def test_local_limits_are_distinct_and_every_acquired_slot_is_released(self):
        occupied = BoundedSemaphore(1)
        occupied.acquire()
        with patch("travel.place_service._SEARCH_SLOTS", occupied), self.assertRaises(PlaceRateLimitError):
            search_and_sync_places(query())
        occupied.release()

        rate_slot = BoundedSemaphore(1)
        with (
            patch("travel.place_service._SEARCH_SLOTS", rate_slot),
            patch("travel.place_service._SEARCH_STARTED", deque([100.0] * 240)),
            patch("travel.place_service.monotonic", return_value=100.0),
            self.assertRaises(PlaceRateLimitError),
        ):
            search_and_sync_places(query())
        self.assertTrue(rate_slot.acquire(blocking=False))
        rate_slot.release()

        upstream_slot = BoundedSemaphore(1)
        with (
            patch("travel.place_service._SEARCH_SLOTS", upstream_slot),
            patch("travel.place_service._SEARCH_STARTED", deque()),
            patch("travel.place_service._request_kakao", side_effect=PlaceUpstreamError),
            self.assertRaises(PlaceUpstreamError),
        ):
            search_and_sync_places(query())
        self.assertTrue(upstream_slot.acquire(blocking=False))
        upstream_slot.release()


class PlaceApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(username="api-place-staff", is_staff=True)
        cls.member = get_user_model().objects.create_user(username="api-place-member")

    def setUp(self):
        self.client = APIClient()

    def test_public_reads_search_and_staff_only_mutations(self):
        self.assertEqual(self.client.post("/api/v1/places/", manual_place(), format="json").status_code, 401)
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.post("/api/v1/places/", manual_place(), format="json").status_code, 403)
        self.client.force_authenticate(self.staff)
        created = self.client.post("/api/v1/places/", manual_place(), format="json")
        self.assertEqual(created.status_code, 201, created.data)
        place_id = created.data["id"]
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/api/v1/places/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/places/{place_id}/").status_code, 200)
        with patch("travel.place_service._request_kakao", return_value=payload(document())):
            searched = self.client.post("/api/v1/places/search/", query(), format="json")
        self.assertEqual(searched.status_code, 200, searched.data)
        self.assertEqual(set(searched.data), {"places", "hasNextPage", "syncedAt"})

    def test_api_rejects_unknown_read_fields_immutable_patch_and_large_or_non_json_bodies(self):
        self.assertEqual(self.client.get("/api/v1/places/?unknown=x").status_code, 400)
        self.assertEqual(self.client.generic("POST", "/api/v1/places/search/", b"{}", content_type="text/plain").status_code, 415)
        self.assertEqual(self.client.generic("POST", "/api/v1/places/search/", b"x" * 12001, content_type="application/json").status_code, 413)
        self.client.force_authenticate(self.staff)
        created = self.client.post("/api/v1/places/", manual_place(), format="json")
        response = self.client.patch(f"/api/v1/places/{created.data['id']}/", {"id": 9}, format="json")
        self.assertEqual((response.status_code, response.data), (400, {"error": "입력값을 확인해 주세요."}))

    def test_database_error_is_safe_json_and_search_rolls_back(self):
        with patch.object(Place.objects, "all", side_effect=OperationalError("SELECT secret")):
            response = self.client.get("/api/v1/places/")
        self.assertEqual((response.status_code, response.data), (503, {"error": "장소 저장소를 사용할 수 없습니다."}))
        self.assertNotIn(b"secret", response.content)

        locked = Place.objects.select_for_update()
        real = locked.get_or_create
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OperationalError("INSERT secret")
            return real(*args, **kwargs)

        with patch("travel.place_service._request_kakao", return_value=payload(document("1"), document("2"))), patch.object(Place.objects, "select_for_update", return_value=locked), patch.object(locked, "get_or_create", side_effect=fail_second):
            response = self.client.post("/api/v1/places/search/", query(), format="json")
        self.assertEqual((response.status_code, response.data), (503, {"error": "장소 저장소를 사용할 수 없습니다."}))
        self.assertEqual(Place.objects.count(), 0)

    @override_settings(KAKAO_REST_API_KEY="")
    def test_api_distinguishes_limits_upstream_configuration_conflict_and_missing_ids(self):
        with patch("travel.place_service._enter_search", side_effect=PlaceRateLimitError):
            limited = self.client.post("/api/v1/places/search/", query(), format="json")
        with patch("travel.place_service._request_kakao", side_effect=PlaceUpstreamError):
            upstream = self.client.post("/api/v1/places/search/", query(), format="json")
        unconfigured = self.client.post("/api/v1/places/search/", query(), format="json")
        self.assertEqual((limited.status_code, upstream.status_code, unconfigured.status_code), (429, 502, 503))
        self.assertEqual(limited.data, {"error": "장소 검색 요청이 많아요. 잠시 후 다시 시도해 주세요."})
        self.assertEqual(upstream.data, {"error": "일부 장소를 불러오지 못했어요."})

        self.client.force_authenticate(self.staff)
        first = self.client.post("/api/v1/places/", manual_place(kakao_place_id="duplicate-api"), format="json")
        conflict = self.client.post("/api/v1/places/", manual_place(kakao_place_id="duplicate-api"), format="json")
        self.assertEqual((first.status_code, conflict.status_code), (201, 409))
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/api/v1/places/999999/").status_code, 404)


class PlaceConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_same_kakao_id_upsert_keeps_one_row(self):
        barrier = Barrier(2)

        def upstream(_query):
            barrier.wait(timeout=10)
            return payload(document())

        def run():
            close_old_connections()
            try:
                return search_and_sync_places(query())
            finally:
                close_old_connections()

        with patch("travel.place_service._request_kakao", side_effect=upstream), ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=20) for future in (pool.submit(run), pool.submit(run))]
        self.assertEqual(len(results), 2)
        self.assertEqual(Place.objects.filter(kakao_place_id="1").count(), 1)
