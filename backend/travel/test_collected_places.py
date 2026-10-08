import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from . import collected_places as service
from .collected_place_views import CollectedPlaceListView


class CollectedPlaceTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        (self.folder / "JAMSIL").mkdir()
        self.path = self.folder / "JAMSIL/public_places.jsonl"
        self.row = {"source": "PARK", "source_id": "park-1", "name": "수집 공원", "address": "서울",
                    "kind": "walk_candidate", "category_small": "근린공원", "lat": 37.5, "lng": 127.0,
                    "distance_m": 0, "cafe_type": "unverified", "source_fields": {"private_raw": "not exposed"}}
        self.meta = {"schema_version": 2, "snapshot_id": "fixture", "radius_m": 2500, "distance_type": "straight_line",
                     "stadiums": {"JAMSIL": {"code": "JAMSIL", "lat": 37.5, "lng": 127.0, "sources": {
                         "PARK": {"status": "ok", "completed_at": "2026-09-27T09:30:00Z", "reference_month": "202606"},
                     }}}, "files": {}}
        self.write([self.row])
        self.override = override_settings(COLLECTED_PLACES_DIR=self.folder)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(service._catalogue.cache_clear)
        self.addCleanup(service._manifest.cache_clear)

    def write(self, rows):
        raw = ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode()
        self.path.write_bytes(raw)
        self.meta["files"]["JAMSIL/public_places.jsonl"] = {"bytes": len(raw), "rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest()}
        (self.folder / "manifest.json").write_text(json.dumps(self.meta), encoding="utf-8")
        service._catalogue.cache_clear()
        service._manifest.cache_clear()

    def request(self, query):
        return CollectedPlaceListView.as_view()(APIRequestFactory().get("/api/v1/places/collected/", query))

    def test_api_serves_only_public_fields_with_provenance(self):
        response = self.request({"stadium": "JAMSIL"})
        self.assertEqual(response.status_code, 200)
        place = response.data["places"][0]
        self.assertEqual(place["placeId"], "collected:PARK:park-1")
        self.assertEqual(place["referenceMonth"], "202606")
        self.assertEqual(place["verificationStatus"], "unverified")
        self.assertNotIn("source_fields", place)
        self.assertEqual(response.data["lodging"]["status"], "details_unavailable")
        self.assertIn("max-age=300", response["Cache-Control"])

    def test_unknown_missing_and_path_queries_are_rejected(self):
        for query in ({}, {"stadium": "../JAMSIL"}, {"stadium": "UNKNOWN"}, {"stadium": "JAMSIL", "path": "/tmp"}, {"stadium": ["JAMSIL", "JAMSIL"]}):
            with self.subTest(query=query):
                self.assertEqual(self.request(query).status_code, 400)

    def test_missing_snapshot_is_unavailable_not_empty_success(self):
        with override_settings(COLLECTED_PLACES_DIR=self.folder / "missing"):
            response = self.request({"stadium": "JAMSIL"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.folder), str(response.data))

    def test_checksum_is_checked_and_changed_file_invalidates_cache(self):
        service.catalogue("JAMSIL")
        self.path.write_bytes(self.path.read_bytes() + b" ")
        self.assertEqual(self.request({"stadium": "JAMSIL"}).status_code, 503)

    def test_cache_avoids_rereading_unchanged_jsonl(self):
        first = service.catalogue("JAMSIL")
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected reread")):
            self.assertIs(service.catalogue("JAMSIL"), first)

    def test_review_google_duplicate_and_bad_coordinates_fail_closed(self):
        invalid = (
            {**self.row, "source": "GOOGLE_PLACES"},
            {**self.row, "source": "SBIZ"},
            {**self.row, "kind": "convenience_store", "brand_status": "needs_review"},
            {**self.row, "lat": 0}, {**self.row, "lat": True},
        )
        for row in invalid:
            self.write([row])
            self.assertEqual(self.request({"stadium": "JAMSIL"}).status_code, 503)
        self.write([self.row, self.row])
        self.assertEqual(self.request({"stadium": "JAMSIL"}).status_code, 503)

    def test_search_is_bounded_filters_and_keeps_stable_ids(self):
        self.write([{**self.row, "source_id": str(i), "name": f"수집 공원 {i:02d}"} for i in range(20)])
        query = {"method": "category", "category": "AT4", "lat": 37.5, "lng": 127.0, "stadium": "JAMSIL"}
        with patch("travel.place_service._request_kakao", side_effect=AssertionError("external search")) as kakao:
            first = service.search_collected_places(query)
            second = service.search_collected_places({**query, "page": 2})
            no_match = service.search_collected_places({**query, "keyword": "없는공원"})
        kakao.assert_not_called()
        self.assertEqual((len(first["places"]), len(second["places"])), (15, 5))
        self.assertTrue(first["hasNextPage"])
        self.assertFalse(second["hasNextPage"])
        self.assertEqual(first["places"][0]["id"], first["places"][0]["placeId"])
        self.assertEqual(no_match["total"], 0)
        self.assertEqual(service.catalogue("JAMSIL")["places"][0]["distance"], 0)

    def test_lodging_never_falls_back_to_kakao(self):
        result = service.search_collected_places({"category": "AD5", "lat": 37.5, "lng": 127.0})
        self.assertEqual(result["places"], [])
        self.assertEqual(result["lodging"]["status"], "details_unavailable")

    def test_outside_collection_area_is_not_silently_reassigned(self):
        with self.assertRaises(service.CatalogueQueryError):
            service.search_collected_places({"lat": 35.0, "lng": 129.0})

    def test_bounded_tourism_uses_only_saved_walk_candidates(self):
        self.write([{**self.row, "source_id": "park", "name": "공원", "kind": "walk_candidate"}])
        result = service.search_collected_tourism({"stadium": "JAMSIL", "lat": 37.5, "lng": 127.0})
        self.assertEqual([p["name"] for p in result["places"]], ["공원"])


class CheckedInSnapshotTests(SimpleTestCase):
    def test_all_nine_stadiums_have_verified_public_records(self):
        root = Path(__file__).resolve().parents[2] / "data/staging/stadium_places" / service.DEFAULT_SNAPSHOT
        with override_settings(COLLECTED_PLACES_DIR=root):
            _, _, manifest = service._metadata()
            results = [service.catalogue(code) for code in manifest["stadiums"]]
        self.assertEqual(len(results), 9)
        # The revised snapshot excludes SBIZ; serving still re-centers its circle.
        raw_count = sum(meta["rows"] for name, meta in manifest["files"].items() if name.endswith("/public_places.jsonl"))
        self.assertEqual(raw_count, 527)
        self.assertGreater(sum(result["count"] for result in results), 0)
        self.assertLessEqual(sum(result["count"] for result in results), raw_count)
        self.assertTrue(all(p["source"] in service.SOURCES and p["kind"] != "stay" for result in results for p in result["places"]))
