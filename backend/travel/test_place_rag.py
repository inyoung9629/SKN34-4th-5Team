"""Offline RAG tests. Fixtures are fictional; live catalogue is tested separately."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .place_rag import RagUnavailable, build_index, index_report, retrieve
from .place_rag_import import analysis_records, collected_documents, facility_documents


def doc(identity="one", name="가상 동경", stadium="JAMSIL", scope="external_candidate", kind="food"):
    return {"id": f"{stadium}:{identity}", "place_id": identity, "name": name, "address": "테스트로 1",
            "stadium": stadium, "scope": scope, "kind": kind, "document_type": "place",
            "subcategory": "일식", "current_operation": "unverified", "menu_verified": False}


def analysis(identity="one", case="V01", status="pass"):
    return {"id": case, "place_id": identity, "name": "가상 동경", "address": "테스트로 1",
            "requested_term": "돈카츠", "attribute": "menu", "status": status,
            "usable_as_fact": False, "reuse_policy": "unreviewed", "needs_recheck": True}


class PlaceRagTest(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "index.sqlite3"
        self.docs = [doc(), doc("two", "가상 동경"), doc("away", "가상 동경", "SUWON"),
                     doc("inside", "가상 내부 매점", scope="internal"),
                     doc("attached", "가상 외부 부속 매점", scope="stadium_exterior"),
                     doc("unknown", "가상 소속 매점", scope="stadium_unknown"),
                     doc("cafe", "가상 커피전문점", kind="cafe")]
        self.docs[1]["address"] = "테스트로 2"
        self.report = build_index(self.path, self.docs, [analysis(), analysis("two", "V02", "unknown")])

    def search(self, query="", **kwargs):
        return retrieve(query, path=self.path, **kwargs)

    def test_actual_sqlite_persistence_and_counts(self):
        self.assertEqual(index_report(self.path)["unique_place_ids"], 7)
        self.assertEqual(self.report["counts"]["analysis"], 2)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_stadium_kind_and_and_keywords(self):
        result = self.search("가상 동경", stadium_code="JAMSIL", kind="food")
        self.assertEqual({r["place_id"] for r in result["items"]}, {"one", "two"})
        self.assertEqual(self.search("동경 커피", stadium_code="JAMSIL")["count"], 0)

    def test_korean_substring_matching(self):
        result = self.search("커피전문", stadium_code="JAMSIL")
        self.assertEqual(result["items"][0]["place_id"], "cafe")

    def test_duplicate_name_does_not_merge_branches(self):
        result = self.search(place_id="two", include_research=True)
        self.assertEqual(result["items"][0]["address"], "테스트로 2")
        self.assertEqual(result["research_records"][0]["status"], "unknown")

    def test_internal_and_uncertain_are_not_external(self):
        self.assertEqual(self.search("매점", stadium_code="JAMSIL")["count"], 0)
        result = self.search("매점", stadium_code="JAMSIL", scope="internal")
        self.assertEqual(result["items"], [])  # Non-collected internal rows are never eligible.
        self.assertEqual(self.search("매점", stadium_code="JAMSIL", scope="all")["count"], 2)

    def test_research_is_separate_and_never_fact(self):
        result = self.search("돈카츠", stadium_code="JAMSIL", include_research=True)
        self.assertEqual(result["items"], [])  # An attempted menu search is NOT a menu tag.
        self.assertEqual(len(result["research_records"]), 2)
        self.assertTrue(all(not r["usable_as_fact"] for r in result["research_records"]))
        self.assertEqual(self.search("돈카츠", stadium_code="SUWON", include_research=True)["research_records"], [])
        self.assertEqual(self.search("동경", stadium_code="JAMSIL")["research_records"], [])

    def test_missing_and_corrupt_index_fail_without_creating_file(self):
        missing = self.path.parent / "missing.sqlite3"
        self.assertEqual(retrieve("동경", path=missing)["status"], "index_unavailable")
        self.assertFalse(missing.exists())
        missing.write_bytes(b"not sqlite")
        self.assertEqual(retrieve("동경", path=missing)["status"], "index_unavailable")

    def test_bounds_and_invalid_filters(self):
        for kwargs in ({"limit": 21}, {"limit": True}, {"stadium_code": "INVALID"},
                       {"scope": "invalid"}, {"kind": "invalid"}, {"query": "가" * 161}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.search(**kwargs)
        self.assertEqual(self.search("동경", limit=1)["count"], 1)

    def test_query_operators_cannot_change_sql_or_fts_filters(self):
        self.assertEqual(self.search('동경" OR 1=1 --', stadium_code="JAMSIL")["status"], "ok")
        self.assertEqual(index_report(self.path)["unique_place_ids"], 7)

    def test_failed_build_preserves_old_index(self):
        before = self.path.read_bytes()
        with self.assertRaises(sqlite3.IntegrityError):
            build_index(self.path, [doc(), doc()])
        self.assertEqual(before, self.path.read_bytes())
        with self.assertRaises(ValueError):
            build_index(self.path, [])
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(list(self.path.parent.glob(".place-rag-*")))

    def test_rebuild_does_not_duplicate_records(self):
        report = build_index(self.path, self.docs, [analysis(), analysis("two", "V02", "unknown")])
        self.assertEqual(report["counts"], self.report["counts"])

    def test_refuse_unrelated_target_and_unsupported_version(self):
        other = self.path.parent / "unrelated.sqlite3"
        other.write_bytes(b"user data")
        with self.assertRaises(RagUnavailable):
            build_index(other, self.docs)
        self.assertEqual(other.read_bytes(), b"user data")
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("PRAGMA user_version=999")
        self.assertEqual(self.search("동경")["status"], "index_unavailable")

    def test_unmatched_or_promoted_analysis_rejected(self):
        for item in (analysis("missing"), {**analysis(), "usable_as_fact": True}):
            with self.subTest(item=item), self.assertRaises(ValueError):
                build_index(self.path, self.docs, [item])

    def test_no_network_for_build_and_query(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            build_index(self.path, self.docs)
            self.assertEqual(self.search("동경", stadium_code="JAMSIL")["count"], 2)

    def test_model_tool_cannot_request_unreviewed_analysis(self):
        from llm.tools.place_rag import create_place_rag_tool
        from llm.tools.assistant import build_tools
        with override_settings(PLACE_RAG_PATH=self.path):
            result = create_place_rag_tool().invoke({"query": "동경", "stadium_code": "JAMSIL",
                                                    "include_research": True})
        self.assertEqual(result["research_records"], [])
        self.assertIn("search_place_knowledge", [tool.name for tool in build_tools()])
        self.assertNotIn("include_research", create_place_rag_tool().args_schema.model_fields)


class AnalysisImporterTest(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.manifest = {"prepared_at": "2026-10-01T15:00:00+09:00", "today_kst": "2026-10-01", "cases": [
            {"case_id": "V01", "placeId": "one", "name": "가상 동경", "address": "테스트로 1",
             "goal": "menu", "requested_menu": "돈카츠"}]}
        self.audit = {"review_date": "2026-10-01", "supported_menu_case_ids": [],
                      "status_overrides": {"V01": {"raw_status": "fail", "reviewed_status": "unknown"}},
                      "reviewed_final_counts": {"pass": 0, "fail": 0, "unknown": 1}}
        self.result = {"case_id": "V01", "verdict": {"status": "fail", "reason": "menu_contradiction"},
                       "raw_review": "DO_NOT_INDEX_RAW_TEXT", "api_key": "DO_NOT_INDEX_SECRET",
                       "rounds": [{"page_attempts": [{"body_read": True, "url": "https://example.com/menu",
                                                      "raw_body": "DO_NOT_INDEX_RAW_TEXT"},
                                                     {"body_read": False, "url": "https://example.com/blocked"}]}]}

    def load(self):
        for filename, value in (("manifest.json", self.manifest), ("audit.json", self.audit), ("results.jsonl", self.result)):
            (self.folder / filename).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return analysis_records(self.folder, [doc()])

    def test_audit_override_and_allowlisted_fields_only(self):
        rows, meta = self.load()
        self.assertEqual(rows[0]["status"], "unknown")
        self.assertEqual(rows[0]["raw_status"], "fail")
        self.assertFalse(rows[0]["usable_as_fact"])
        self.assertEqual(rows[0]["reviewed_page_urls"], ["https://example.com/menu"])
        self.assertNotIn("DO_NOT_INDEX", json.dumps(rows))
        self.assertEqual(len(meta["files"]["audit.json"]), 64)

    def test_mismatched_branch_rejected(self):
        self.manifest["cases"][0]["address"] = "다른로 2"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.load()

    def test_mismatched_audit_counts_rejected(self):
        self.audit["reviewed_final_counts"]["unknown"] = 2
        with self.assertRaisesRegex(ValueError, "counts mismatch"):
            self.load()

    def test_unaudited_pass_rejected(self):
        self.result["verdict"]["status"] = "pass"
        self.audit["status_overrides"] = {}
        with self.assertRaisesRegex(ValueError, "Unsupported audited"):
            self.load()

    def test_real_snapshot_and_facilities_import_offline(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            docs, provenance = collected_documents()
            facilities, facility_sources = facility_documents()
        self.assertGreater(len(docs), 0)
        self.assertLessEqual(len(docs), 527)
        self.assertTrue(all(d["source"] in {"PARK", "TOUR"} for d in docs))
        self.assertEqual(len({d["stadium"] for d in docs}), 9)
        self.assertTrue(all(d["scope"] in {"external_candidate", "stadium_unknown"} for d in docs))
        self.assertGreater(len(facilities), 400)
        self.assertTrue(all(d["scope"] != "external_candidate" for d in facilities))
        self.assertEqual(len(provenance["manifest_sha256"]), 64)
        self.assertEqual(len(facility_sources), 4)
