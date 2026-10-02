"""Offline geometry and search-path regression tests; no paid API calls."""
import json
from copy import deepcopy
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase

from .collected_places import CatalogueUnavailable
from .stadium_scope import classify_stadium_point, reviewed_zones, filter_provider_places
from .stadium_facilities import _root
from .kakao_course_candidates import KakaoCourseCandidates
from .test_kakao_course_candidates import FakeKakao, FAST, document
from .place_service import search_live_places, search_live_lodging
from .place_rag import build_index, retrieve


def provider_doc(identity, point, category="FD6"):
    return {**document(identity, category=category, name="가상 매장"), "y": str(point["lat"]), "x": str(point["lng"])}


class StadiumScopeTests(SimpleTestCase):
    def setUp(self):
        self.locations = json.loads((_root() / "stadium_locations.json").read_text(encoding="utf-8"))["stadiums"]
        self.zones = reviewed_zones()
        self.main = self.locations["DAEJEON"]
        self.old = self.main["excluded"][0]
        self.external = {"lat": 36.3195, "lng": 127.4300}

    def test_all_nine_main_centers_and_green_edges_are_internal(self):
        self.assertEqual(set(self.zones), set(self.locations))
        for code, location in self.locations.items():
            with self.subTest(code=code):
                self.assertEqual(classify_stadium_point(location), {"scope": "internal", "stadium": code})
                for lat, lng in self.zones[code]["main"]:
                    self.assertEqual(classify_stadium_point({"lat": lat, "lng": lng})["scope"], "internal")

    def test_neighboring_stadiums_excluded_and_ordinary_outside_place_kept(self):
        for code in ("DAEJEON", "MUNHAK", "SUWON", "GWANGJU", "CHANGWON"):
            with self.subTest(code=code):
                point = self.locations[code]["excluded"][0]
                self.assertEqual(classify_stadium_point(point), {"scope": "excluded_complex", "stadium": code})
        self.assertEqual(classify_stadium_point(self.external)["scope"], "external")

    def test_green_priority_shared_edges_and_green_protruding_outside_red(self):
        zones = {"TEST": {"main": [[1, 1], [1, 4], [2, 4], [2, 1]],
                          "outers": [[[0, 0], [0, 3], [3, 3], [3, 0], [0, 0]]]}}
        for lat, lng, expected in ((1.5, 2, "internal"), (1, 1, "internal"), (1.5, 3.5, "internal"),
                                   (2.5, 2, "excluded_complex"), (0, 0, "excluded_complex"), (4, 4, "external")):
            with self.subTest(point=(lat, lng)):
                self.assertEqual(classify_stadium_point({"lat": lat, "lng": lng}, zones)["scope"], expected)
        self.assertEqual(classify_stadium_point({"lat": float("nan"), "lng": 2}, zones)["scope"], "unknown")

    def test_disconnected_sajik_block_is_also_excluded(self):
        self.assertEqual(classify_stadium_point({"lat": 35.1900, "lng": 129.0583})["scope"], "excluded_complex")

    def test_missing_geometry_does_not_silently_disable_filter(self):
        with patch("travel.stadium_scope._root", return_value=Path("/missing-test-geometry")):
            with self.assertRaises(CatalogueUnavailable):
                filter_provider_places([provider_doc(1, self.external)])

    def test_live_search_keeps_internal_tag_but_never_returns_red_only_all_categories(self):
        for category in ("FD6", "CE7", "CS2", "CT1", "AT4", "AD5"):
            with self.subTest(category=category):
                query = {"method": "category", "category": category, "lat": self.main["lat"],
                         "lng": self.main["lng"], "radius": 2500, "page": 1, "size": 15, "sort": "distance"}
                docs = [provider_doc(1, self.main, category), provider_doc(2, self.old, category), provider_doc(3, self.external, category)]
                original = deepcopy(docs)
                with patch("travel.place_service._request_kakao", return_value={"meta": {"is_end": False}, "documents": docs}):
                    result = (search_live_lodging if category == "AD5" else search_live_places)(query)
                self.assertEqual([p["id"] for p in result["places"]], ["1", "3"])
                self.assertEqual(result["places"][0]["stadiumArea"]["scope"], "internal")
                self.assertTrue(result["hasNextPage"])
                self.assertEqual(docs, original)

    def test_filtered_empty_page_still_has_next_page(self):
        query = {"method": "category", "category": "FD6", "lat": self.main["lat"], "lng": self.main["lng"],
                 "radius": 2500, "page": 1, "size": 15, "sort": "distance"}
        with patch("travel.place_service._request_kakao", return_value={"meta": {"is_end": False}, "documents": [provider_doc(1, self.old)]}):
            result = search_live_places(query)
        self.assertEqual(result["places"], [])
        self.assertTrue(result["hasNextPage"])

    def test_adaptive_retrieval_counts_raw_pages_then_filters_before_ranking(self):
        docs = [provider_doc(1, self.main), provider_doc(2, self.old), provider_doc(3, self.external)]
        source = KakaoCourseCandidates("DAEJEON", fetch=FakeKakao(docs), policy=FAST)
        self.assertEqual([p["placeId"] for p in source.search(self.main, 1500, "food")], ["3"])
        audit = source.audit()
        self.assertEqual(audit["retrieved_unique_count"], 3)
        self.assertEqual(audit["excluded_complex_count"], 1)
        self.assertEqual(audit["excluded_internal_count"], 1)
        self.assertEqual(audit["status"], "complete")
        # Named-origin search cannot reintroduce red-only facilities either.
        self.assertEqual({p["placeId"] for p in source.search(self.main, 1500, "origin", origin_label="가상")}, {"1", "3"})

    def test_existing_rag_is_refiltered_before_limit_without_rebuild(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "index.sqlite3"
            docs = [{"id": str(i), "place_id": str(i), "document_type": "place", "stadium": "DAEJEON",
                     "name": "가상 매장", "kind": "food", "scope": "external_candidate",
                     "lat": point["lat"], "lng": point["lng"]}
                    for i, point in enumerate((self.old, self.main, self.external))]
            analyses = [{"id": f"research:{i}", "place_id": str(i), "status": "unknown",
                         "usable_as_fact": False, "requested_term": "돈까스"} for i in range(3)]
            build_index(path, docs, analyses)
            original = path.read_bytes()
            self.assertEqual([p["place_id"] for p in retrieve(stadium_code="DAEJEON", path=path, limit=1)["items"]], ["2"])
            self.assertEqual([p["place_id"] for p in retrieve(stadium_code="DAEJEON", path=path, scope="internal")["items"]], ["1"])
            self.assertEqual({p["place_id"] for p in retrieve(stadium_code="DAEJEON", path=path, scope="all")["items"]}, {"1", "2"})
            research = retrieve("돈까스", stadium_code="DAEJEON", path=path, scope="all", include_research=True)
            self.assertEqual({p["place_id"] for p in research["research_records"]}, {"1", "2"})
            self.assertEqual(path.read_bytes(), original)
