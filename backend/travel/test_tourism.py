import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from threading import Barrier, Thread
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from .models import Place
from .place_service import update_place as update_common_place
from .tourism_models import TourismPlace
from .tourism_provider import MAX_RESPONSE_BYTES, STADIUMS, TourismProviderError, _RejectRedirects, fetch_tourism, normalize_item, parse_page
from .tourism_service import search_tourism, sync_tourism_places
from .tourism_tools import create_tourism_place, delete_tourism_place, get_tourism_place, list_tourism_places, update_tourism_place


RAW = {"contentid": "1603175", "contenttypeid": "12", "title": "아시아공원", "mapx": "127.0767", "mapy": "37.51008", "addr1": "서울특별시 송파구 올림픽로 44"}


def page(items=None, total=None):
    items = [] if items is None else items
    return {"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": items} if items else "", "totalCount": len(items) if total is None else total}}}


class Headers:
    def __init__(self, content_type="application/json"):
        self.content_type = content_type

    def get_content_type(self):
        return self.content_type


class FakeResponse(BytesIO):
    def __init__(self, value, content_type="application/json"):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.headers = Headers(content_type)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []
        self.timeouts = []

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        self.timeouts.append(timeout)
        return next(self.responses)


class TourismProviderTests(TestCase):
    def test_normalization_categories_distance_and_malformed_pages(self):
        stadium = STADIUMS["JAMSIL"]
        place = normalize_item(RAW, stadium)
        self.assertEqual((place["placeId"], place["kind"]), ("tour:1603175", "walk"))
        self.assertIsNone(normalize_item({**RAW, "mapx": "Infinity"}, stadium))
        self.assertIsNone(normalize_item({**RAW, "title": "잠실야구장"}, stadium))
        self.assertEqual(parse_page(page([RAW], "1"))[1], 1)
        for invalid in ({}, page([], 1), {"response": {"header": {"resultCode": "30"}}}, {"response": {"header": "bad", "body": {}}}):
            with self.assertRaises(TourismProviderError):
                parse_page(invalid)

    def test_fixed_url_once_encoded_key_bounded_pages_partial_and_truncated(self):
        responses = [FakeResponse(page([RAW], 400)) for _ in range(9)]
        opener = FakeOpener(responses)
        _, _, lat, lng = STADIUMS["JAMSIL"]
        result = fetch_tourism("JAMSIL", lat, lng, "test%2Bkey%2F%3D", opener=opener)
        self.assertEqual((result["status"], result["truncated"], len(result["places"])), ("ok", True, 1))
        self.assertEqual(len(opener.urls), 9)
        self.assertEqual(opener.timeouts, [8] * 9)
        self.assertIn("serviceKey=test%2Bkey%2F%3D", opener.urls[0])
        self.assertNotIn("test%252B", opener.urls[0])
        self.assertTrue(all(url.startswith("https://apis.data.go.kr/B551011/KorService2/locationBasedList2?") for url in opener.urls))

        malformed = FakeOpener([FakeResponse(page([{**RAW, "mapx": "NaN"}])), FakeResponse(page()), FakeResponse(page())])
        self.assertEqual(fetch_tourism("JAMSIL", lat, lng, "key", opener=malformed)["status"], "partial")

    def test_redirect_oversize_content_type_and_errors_never_expose_key(self):
        with self.assertRaises(TourismProviderError):
            _RejectRedirects().redirect_request(None, None, 302, "redirect secret", {}, "https://evil.test/key")
        _, _, lat, lng = STADIUMS["JAMSIL"]
        for value, content_type in ((b"x" * (MAX_RESPONSE_BYTES + 1), "application/json"), (page(), "text/html")):
            with self.assertRaises(TourismProviderError) as raised:
                fetch_tourism("JAMSIL", lat, lng, "private-key", opener=FakeOpener([FakeResponse(value, content_type) for _ in range(3)]))
            self.assertNotIn("private-key", str(raised.exception))


@override_settings(TOUR_API_KEY="tourism-test-fixture")
class TourismPersistenceTests(TestCase):
    def setUp(self):
        self.query = {"stadium": "JAMSIL", "lat": STADIUMS["JAMSIL"][2], "lng": STADIUMS["JAMSIL"][3]}
        self.calls = 0

    def place(self, **overrides):
        return normalize_item({**RAW, **overrides}, STADIUMS["JAMSIL"])

    def fetch(self, *_, place=None, status="ok", truncated=False):
        self.calls += 1
        return {"status": status, "places": [place or self.place()], "truncated": truncated}

    def test_fetches_every_request_and_syncs_only_new_null_or_strictly_stale(self):
        start = datetime(2026, 9, 15, tzinfo=timezone.utc)
        first = search_tourism(self.query, fetcher=self.fetch, now=start)
        row = TourismPlace.objects.get()
        self.assertEqual((first["fetchedAt"], first["lastSyncedAt"]), (start.isoformat(), start.isoformat()))

        for moment in (start + timedelta(seconds=1), start + timedelta(seconds=600), start - timedelta(seconds=1)):
            with CaptureQueriesContext(connection) as queries:
                search_tourism(self.query, fetcher=self.fetch, now=moment)
            self.assertFalse(any(query["sql"].lstrip().upper().startswith("UPDATE") for query in queries))
            row.refresh_from_db()
            self.assertEqual(row.last_synced_at, start)

        changed = self.place(title="아시아공원 새 이름")
        search_tourism(self.query, fetcher=lambda *_: self.fetch(place=changed), now=start + timedelta(seconds=601))
        row.refresh_from_db()
        self.assertEqual((row.place.name, row.last_synced_at), ("아시아공원 새 이름", start + timedelta(seconds=601)))
        row.last_synced_at = None
        row.save(update_fields=("last_synced_at",))
        search_tourism(self.query, fetcher=self.fetch, now=start + timedelta(seconds=602))
        row.refresh_from_db()
        self.assertEqual(row.last_synced_at, start + timedelta(seconds=602))
        self.assertEqual(self.calls, 6)
        with self.assertRaises(ValidationError):
            search_tourism({**self.query, "unexpected": True}, fetcher=self.fetch, now=start)

    @override_settings(EXTERNAL_DATA_SYNC_INTERVAL_SECONDS=120)
    def test_partial_and_truncated_valid_entities_sync_without_deleting_absent_rows(self):
        start = datetime(2026, 9, 15, tzinfo=timezone.utc)
        search_tourism(self.query, fetcher=self.fetch, now=start)
        partial = self.place(title="부분 응답 장소")
        response = search_tourism(self.query, fetcher=lambda *_: self.fetch(place=partial, status="partial"), now=start + timedelta(seconds=121))
        self.assertEqual(response["status"], "partial")
        self.assertEqual(TourismPlace.objects.get(content_id="1603175").place.name, "부분 응답 장소")
        other = self.place(contentid="200", title="다른 장소", mapx="127.078", mapy="37.511")
        response = search_tourism(self.query, fetcher=lambda *_: self.fetch(place=other, truncated=True), now=start + timedelta(seconds=242))
        self.assertTrue(response["truncated"])
        self.assertEqual(TourismPlace.objects.count(), 2)
        with self.assertRaises(TourismProviderError):
            search_tourism(self.query, fetcher=lambda *_: (_ for _ in ()).throw(TourismProviderError()), now=start + timedelta(seconds=400))
        self.assertEqual(TourismPlace.objects.count(), 2)

    def test_mixed_batch_updates_only_the_stale_entity(self):
        start = datetime(2026, 9, 15, tzinfo=timezone.utc)
        first = self.place(title="fresh")
        second = self.place(contentid="200", title="stale", mapx="127.078", mapy="37.511")
        sync_tourism_places([first, second], start)
        TourismPlace.objects.filter(content_id="200").update(last_synced_at=start - timedelta(seconds=601))
        changed = [{**first, "name": "fresh changed"}, {**second, "name": "stale changed"}]
        with CaptureQueriesContext(connection) as queries:
            sync_tourism_places(changed, start + timedelta(seconds=1))
        self.assertEqual(TourismPlace.objects.get(content_id="1603175").place.name, "fresh")
        self.assertEqual(TourismPlace.objects.get(content_id="200").place.name, "stale changed")
        updates = [query for query in queries if query["sql"].lstrip().upper().startswith("UPDATE")]
        self.assertEqual(len(updates), 2)

    def test_malformed_batch_rolls_back_without_overwriting_known_good_place(self):
        valid = self.place()
        fetched_at = datetime(2026, 9, 15, tzinfo=timezone.utc)
        sync_tourism_places([valid], fetched_at)
        new = self.place(contentid="200", title="new", mapx="127.078", mapy="37.511")
        with self.assertRaises(ValidationError):
            sync_tourism_places([new, {**valid, "name": "poison", "lat": "NaN"}], fetched_at + timedelta(seconds=601))
        self.assertEqual(TourismPlace.objects.get().place.name, "아시아공원")
        self.assertEqual(TourismPlace.objects.count(), 1)

    def test_tourism_refresh_never_marks_or_overwrites_a_kakao_linked_place(self):
        start = datetime(2026, 9, 15, tzinfo=timezone.utc)
        sync_tourism_places([self.place()], start)
        tourism = TourismPlace.objects.get()
        marker = start - timedelta(days=1)
        Place.objects.filter(pk=tourism.place_id).update(kakao_place_id="kakao-1", name="카카오 이름", last_synced_at=marker)
        changed = self.place(title="관광 새 이름")
        sync_tourism_places([changed], start + timedelta(seconds=601))
        tourism.refresh_from_db()
        place = Place.objects.get(pk=tourism.place_id)
        self.assertEqual((place.name, place.last_synced_at), ("카카오 이름", marker))
        self.assertEqual(tourism.last_synced_at, start + timedelta(seconds=601))
        metadata = (tourism.content_id, tourism.content_type, tourism.category, tourism.image_url, tourism.last_synced_at)
        staff = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=True)
        update_common_place(place.pk, {"name": "카카오 수동 수정"}, actor=staff)
        tourism.refresh_from_db()
        self.assertEqual((tourism.content_id, tourism.content_type, tourism.category, tourism.image_url, tourism.last_synced_at), metadata)

    def test_callable_entity_crud_filters_are_db_only_strict_and_preserve_provider_times(self):
        denied = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=False)
        inactive = SimpleNamespace(is_authenticated=True, is_active=False, is_staff=True)
        staff = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=True)
        arguments = {"contentId": "99", "contentType": "14", "name": "작은 박물관", "category": "indoor", "lat": 37.51, "lng": 127.07, "address": "서울", "phone": ""}
        for actor in (denied, inactive):
            with self.assertRaises(PermissionDenied):
                create_tourism_place(arguments, actor)
        row = create_tourism_place(arguments, staff)
        self.assertIsNone(row["fetchedAt"])
        self.assertEqual(get_tourism_place("99")["name"], "작은 박물관")
        result = list_tourism_places({"page": 1, "pageSize": 1, "q": "박물", "category": "indoor", "minLat": 37.0, "maxLat": 38.0})
        self.assertEqual(result["items"][0]["contentId"], "99")
        for invalid in ({"pageSize": 101}, {"pageSize": True}, {"page": "1"}, {"minLat": 38.0, "maxLat": 37.0}):
            with self.assertRaises(ValidationError):
                list_tourism_places(invalid)
        update_tourism_place("99", {"name": "수정 박물관"}, staff)
        updated = get_tourism_place("99")
        self.assertEqual(updated["name"], "수정 박물관")
        self.assertIsNone(updated["lastSyncedAt"])
        with self.assertRaises(ValidationError):
            update_tourism_place(True, {"name": "bad"}, staff)
        self.assertTrue(delete_tourism_place("99", staff))


class TourismMigrationTests(TransactionTestCase):
    migrate_from = [("travel", "0006_place"), ("travel", "0008_tourismplace")]
    migrate_to = [("travel", "0009_tourismplace_use_common_place")]

    def test_transition_preserves_kakao_and_draft_rows_without_numeric_id_merging(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldPlace = old_apps.get_model("travel", "Place")
        OldTourism = old_apps.get_model("travel", "TourismPlace")
        marker = datetime(2026, 9, 14, tzinfo=timezone.utc)
        kakao = OldPlace.objects.create(kakao_place_id="1603175", name="카카오 장소", address="기존 주소", lat=37.5, lng=127.0, last_synced_at=marker)
        draft = OldTourism.objects.create(
            content_id="1603175", content_type="12", name="관광 장소", category="sight", address="관광 주소",
            latitude=37.51, longitude=127.01, phone="0" * 80, image_url="https://example.test/image.jpg",
            source_url="https://example.test/source", fetched_at=marker, last_synced_at=marker,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewPlace = new_apps.get_model("travel", "Place")
        NewTourism = new_apps.get_model("travel", "TourismPlace")
        migrated = NewTourism.objects.get(pk=draft.pk)
        common = NewPlace.objects.get(pk=migrated.place_id)
        kakao.refresh_from_db()
        self.assertNotEqual(common.pk, kakao.pk)
        self.assertEqual((common.kakao_place_id, common.name, common.address, len(common.phone), common.url), (None, "관광 장소", "관광 주소", 80, "https://example.test/source"))
        self.assertEqual((migrated.content_id, migrated.image_url, migrated.fetched_at, migrated.last_synced_at), ("1603175", "https://example.test/image.jpg", marker, marker))
        self.assertEqual((kakao.name, kakao.address, kakao.last_synced_at), ("카카오 장소", "기존 주소", marker))

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()


class TourismConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_new_entity_upsert_keeps_one_row(self):
        barrier, errors = Barrier(2), []
        place = normalize_item(RAW, STADIUMS["JAMSIL"])
        fetched_at = datetime(2026, 9, 15, tzinfo=timezone.utc)

        def worker():
            close_old_connections()
            try:
                barrier.wait()
                sync_tourism_places([place], fetched_at)
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(errors)
        self.assertEqual(TourismPlace.objects.filter(content_id="1603175").count(), 1)

    def test_opposite_page_orders_lock_entities_in_one_stable_order(self):
        first = normalize_item(RAW, STADIUMS["JAMSIL"])
        second = normalize_item({**RAW, "contentid": "200", "title": "다른 장소", "mapx": "127.078", "mapy": "37.511"}, STADIUMS["JAMSIL"])
        start = datetime(2026, 9, 15, tzinfo=timezone.utc)
        sync_tourism_places([first, second], start)
        barrier, errors = Barrier(2), []

        def worker(places, fetched_at):
            close_old_connections()
            try:
                barrier.wait()
                sync_tourism_places(places, fetched_at)
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [
            Thread(target=worker, args=([first, second], start + timedelta(seconds=601))),
            Thread(target=worker, args=([second, first], start + timedelta(seconds=602))),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(set(TourismPlace.objects.values_list("last_synced_at", flat=True))), 1)


@override_settings(TOUR_API_KEY="tourism-test-fixture")
class TourismHttpTests(TestCase):
    def test_public_search_validation_unconfigured_and_safe_upstream_error(self):
        client = APIClient()
        _, _, lat, lng = STADIUMS["JAMSIL"]
        self.assertEqual(client.get("/api/v1/tourism/", {"stadium": "BAD", "lat": lat, "lng": lng}).status_code, 400)
        with override_settings(TOUR_API_KEY=""):
            self.assertEqual(client.get("/api/v1/tourism/", {"stadium": "JAMSIL", "lat": lat, "lng": lng}).json()["status"], "unconfigured")
        with patch("travel.tourism_views.search_tourism", side_effect=TourismProviderError("upstream_rate_limited", 429)):
            response = client.get("/api/v1/tourism/", {"stadium": "JAMSIL", "lat": lat, "lng": lng})
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("test%2Bkey", response.content.decode())
        with patch("travel.tourism_views._tourism_slots") as slots:
            slots.acquire.return_value = False
            self.assertEqual(client.get("/api/v1/tourism/", {"stadium": "JAMSIL", "lat": lat, "lng": lng}).status_code, 429)
