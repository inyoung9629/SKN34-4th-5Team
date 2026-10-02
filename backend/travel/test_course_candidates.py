"""Fictional candidates; no provider calls, credentials or application DB."""
from copy import deepcopy
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase

from .collected_places import CatalogueUnavailable
from .course_candidates import FIELD_MAP, load_course_candidates
from .place_rag import build_index


def place(ident, *, affiliation=None, kind="food"):
    return {"placeId": "collected:SBIZ:" + ident, "name": "가상 매장 " + ident,
            "address": "가상 주소", "lat": 37.510, "lng": 127.080, "kind": kind,
            "source": "SBIZ", "category": "먹거리", "subcategory": "일식",
            "cuisine": "일식", "stadiumAffiliation": affiliation,
            "verificationStatus": "unverified"}


def document(p, *, scope="external_candidate", stadium="JAMSIL"):
    return {**{target: p[source] for target, source in FIELD_MAP.items()},
            "id": f"place:{stadium}:{p['placeId']}", "stadium": stadium,
            "scope": scope, "document_type": "place", "snapshot_id": "fixture",
            "menu_verified": False, "review_verified": False}


class CourseCandidatesTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "index.sqlite3"
        self.external = [place("one"), place("two", kind="cafe")]
        self.inside = place("inside", affiliation={"scope": "internal"})
        self.uncertain = place("uncertain", affiliation={"scope": "unknown"})
        self.data = {"snapshotId": "fixture", "places": self.external + [self.inside, self.uncertain],
                     "coverage": [{"lat": 37.512, "lng": 127.071, "radius_m": 2500}], "count": 4}

    def build(self, docs=None, analyses=(), snapshot="fixture"):
        docs = docs if docs is not None else [document(p) for p in self.external] + [
            document(self.inside, scope="internal"), document(self.uncertain, scope="stadium_unknown")]
        build_index(self.path, docs, analyses, provenance={"public": {"snapshot_id": snapshot}})

    def load(self):
        with patch("travel.course_candidates.planning_catalogue", return_value=self.data):
            return load_course_candidates("JAMSIL", path=self.path)

    def test_rag_candidates_preserve_original_coverage_and_identity(self):
        self.build()
        before = deepcopy(self.data)
        result = self.load()
        self.assertEqual(result["places"], self.external)
        self.assertEqual(result["coverage"], self.data["coverage"])
        self.assertEqual(result["candidate_retrieval"]["source"], "local_place_rag")
        self.assertEqual(result["candidate_retrieval"]["excluded_affiliation_count"], 2)
        self.assertFalse(result["candidate_retrieval"]["network_used"])
        self.assertEqual(self.data, before)

    def test_no_top_twenty_truncation(self):
        self.external = [place(str(i)) for i in range(125)]
        self.data["places"] = self.external
        self.build([document(p) for p in self.external])
        result = self.load()
        self.assertEqual(result["count"], 125)
        self.assertEqual(result["candidate_retrieval"]["rag_status"], "ready")

    def test_missing_and_corrupt_index_fall_back_without_creating_or_rewriting_it(self):
        for raw in (None, b"not sqlite"):
            if raw:
                self.path.write_bytes(raw)
            result = self.load()
            self.assertEqual(result["places"], self.external)
            self.assertEqual(result["candidate_retrieval"]["source"], "collected_snapshot")
            self.assertEqual(result["candidate_retrieval"]["rag_status"], "index_unavailable")
            self.assertEqual(self.path.exists(), raw is not None)
            if raw:
                self.assertEqual(self.path.read_bytes(), raw)

    def test_stale_snapshot_falls_back(self):
        self.build(snapshot="old")
        self.assertEqual(self.load()["candidate_retrieval"]["rag_status"], "snapshot_mismatch")

    def test_different_branch_location_missing_and_duplicate_ids_fall_back(self):
        original = [document(p) for p in self.external]
        changed = [original[:1], [original[0], {**original[1], "name": "다른 지점"}],
                   [original[0], {**original[1], "lat": 37.7}],
                   [original[0], {**original[1], "place_id": original[0]["place_id"]}]]
        for docs in changed:
            with self.subTest(docs=docs):
                self.build(docs)
                result = self.load()
                self.assertEqual(result["candidate_retrieval"]["rag_status"], "candidate_mismatch")
                self.assertEqual(result["places"], self.external)

    def test_research_pass_and_arbitrary_index_tags_never_become_route_facts(self):
        docs = [document(p) for p in self.external]
        docs[0].update(menu_verified=True, quietness=True, menu=["돈까스"])
        research = {"id": "analysis:one", "place_id": self.external[0]["placeId"],
                    "status": "pass", "usable_as_fact": False, "requested_term": "돈까스"}
        self.build(docs, [research])
        result = self.load()
        self.assertEqual(result["candidate_retrieval"]["rag_status"], "ready")
        for p in result["places"]:
            self.assertNotIn("quietness", p)
            self.assertNotIn("menu", p)
            self.assertNotIn("menu_verified", p)
        self.assertFalse(result["candidate_retrieval"]["research_used_as_fact"])

    def test_internal_unknown_and_other_stadium_never_enter_external_candidates(self):
        self.build([document(p) for p in self.external] + [document(self.inside, scope="internal"),
                   document(self.uncertain, scope="stadium_unknown"), document(place("other"), stadium="SUWON")])
        self.assertEqual(self.load()["places"], self.external)

    def test_mislabelled_facility_in_rag_cannot_override_source_affiliation(self):
        self.build([document(p) for p in self.external + [self.inside]])
        result = self.load()
        self.assertEqual(result["candidate_retrieval"]["rag_status"], "candidate_mismatch")
        self.assertEqual(result["places"], self.external)

    def test_url_only_lodging_cannot_supply_a_coordinate_or_classification(self):
        fake = document(place("lodging-url"))
        fake.update(place_id="kakao-lodging:123", lat=None, lng=None,
                    place_url="https://place.map.kakao.com/123")
        self.build([document(p) for p in self.external] + [fake])
        result = self.load()
        self.assertEqual(result["places"], self.external)
        self.assertEqual(result["candidate_retrieval"]["rag_status"], "candidate_mismatch")

    def test_broken_source_is_not_hidden_by_a_working_rag(self):
        self.build()
        with patch("travel.course_candidates.planning_catalogue", side_effect=CatalogueUnavailable):
            with self.assertRaises(CatalogueUnavailable):
                load_course_candidates("JAMSIL", path=self.path)
