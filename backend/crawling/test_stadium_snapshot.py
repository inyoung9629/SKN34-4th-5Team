import copy
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

import stadium_snapshot as snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.output = Path(self.temp.name) / "snapshot"
        (self.source / "TEST").mkdir(parents=True)
        self.place = {"source": "SBIZ", "source_id": "one", "name": "fixture", "address": "fixture",
                      "kind": "convenience_store", "brand_status": "name_identified", "lat": 37.5, "lng": 127,
                      "distance_m": 0, "source_fields": {"category": "fixture"}}
        self.review = {**self.place, "source_id": "review", "brand_status": "unidentified"}
        sources = {source: {"status": "ok", "completed_at": "2026-09-27T09:30:00+00:00", "selected_records": 0}
                   for source in ("SBIZ", "PARK", "TOUR_WALK", "GOOGLE")}
        sources["SBIZ"].update(selected_records=1, convenience_needs_review=1, reference_month="202606")
        sources["GOOGLE"]["within_radius_ids"] = 1
        self.summary = {"schema_version": 1, "started_at": "fixture", "radius_m": 2500,
                        "distance_type": "straight_line", "lodging_source": "GOOGLE_PLACES",
                        "stadiums": {"TEST": {"code": "TEST", "lat": 37.5, "lng": 127, "sources": sources}}}
        self.write(self.source / "summary.json", self.summary)
        self.write(self.source / "TEST/public_places.json", [self.place])
        self.write(self.source / "TEST/convenience_review.json", [self.place, self.review])
        self.write(self.source / "TEST/google_lodging_ids.json", [{"source": "GOOGLE_PLACES", "place_id": "fixture-id"}])

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_build_preserves_source_fields_and_separates_review_without_duplicates(self):
        manifest, records, _ = snapshot.build(self.source, self.output)
        self.assertEqual(manifest["totals"], dict.fromkeys(snapshot.FILES, 1))
        self.assertEqual(records["TEST/public_places.jsonl"], [self.place])
        self.assertEqual(records["TEST/convenience_review.jsonl"], [self.review])
        self.assertNotIn(b"\r", (self.output / "manifest.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "already exists"):
            snapshot.build(self.source, self.output)

    def test_file_tampering_is_rejected(self):
        snapshot.build(self.source, self.output)
        with (self.output / "TEST/public_places.jsonl").open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            snapshot.validate(self.output)

    def test_google_details_are_rejected_even_with_a_valid_checksum(self):
        self.write(self.source / "TEST/google_lodging_ids.json",
                   [{"source": "GOOGLE_PLACES", "place_id": "fixture-id", "name": "must not persist"}])
        with self.assertRaisesRegex(ValueError, "IDs only"):
            snapshot.build(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_lodging_cannot_enter_public_places(self):
        self.write(self.source / "TEST/public_places.json", [{**self.place, "kind": "lodging"}])
        with self.assertRaisesRegex(ValueError, "lodging is excluded"):
            snapshot.build(self.source, self.output)

    def test_outside_radius_is_rejected_despite_forged_distance(self):
        self.write(self.source / "TEST/public_places.json", [{**self.place, "lat": 38}])
        with self.assertRaisesRegex(ValueError, "outside radius"):
            snapshot.build(self.source, self.output)

    def test_duplicates_are_rejected(self):
        self.write(self.source / "TEST/public_places.json", [self.place, self.place])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            snapshot.build(self.source, self.output)

    def test_incomplete_sources_are_rejected(self):
        self.summary["stadiums"]["TEST"]["sources"]["PARK"]["status"] = "error"
        self.write(self.source / "summary.json", self.summary)
        with self.assertRaisesRegex(ValueError, "did not complete"):
            snapshot.build(self.source, self.output)

    def test_source_counts_are_checked_against_metadata(self):
        self.summary["stadiums"]["TEST"]["sources"]["SBIZ"]["selected_records"] = 2
        self.write(self.source / "summary.json", self.summary)
        with self.assertRaisesRegex(ValueError, "Source count mismatch"):
            snapshot.build(self.source, self.output)

    def test_manifest_cannot_traverse_directories(self):
        snapshot.build(self.source, self.output)
        manifest = snapshot.read_json(self.output / "manifest.json")
        manifest["files"]["../outside.jsonl"] = manifest["files"].pop("TEST/public_places.jsonl")
        self.write(self.output / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "Unexpected or missing"):
            snapshot.validate(self.output)

    @unittest.skipUnless(os.getenv("STADIUM_STAGING_TEST_DSN"), "Set a disposable PostgreSQL test DSN")
    def test_postgres_import_idempotence_conflict_and_transaction_rollback(self):
        import psycopg
        from load_stadium_snapshot import load

        manifest, records, sha = snapshot.build(self.source, self.output)
        manifest["snapshot_id"] = "test_" + uuid.uuid4().hex
        # Simple protocol also supports disposable PGlite test servers.
        with psycopg.connect(os.environ["STADIUM_STAGING_TEST_DSN"], autocommit=True,
                             cursor_factory=psycopg.ClientCursor) as connection:
            # Roll back this outer transaction, including all test data and DDL.
            with connection.transaction(force_rollback=True):
                self.assertEqual(load(connection, manifest, records, sha), "loaded")
                self.assertEqual(load(connection, manifest, records, sha), "already_loaded")
                with self.assertRaisesRegex(ValueError, "different content"):
                    load(connection, manifest, records, "changed")
                broken = copy.deepcopy(records)
                broken["TEST/public_places.jsonl"][0]["kind"] = "invalid"
                next_manifest = {**manifest, "snapshot_id": manifest["snapshot_id"] + "_failed"}
                with self.assertRaises(psycopg.errors.CheckViolation):
                    load(connection, next_manifest, broken, sha)
                result = connection.execute("SELECT count(*) FROM place_staging.snapshots WHERE snapshot_id=%s",
                                            (next_manifest["snapshot_id"],)).fetchone()
                self.assertEqual(result[0], 0)
                connection.execute("DELETE FROM place_staging.google_lodging_ids WHERE snapshot_id=%s", (manifest["snapshot_id"],))
                with self.assertRaisesRegex(ValueError, "Google row count mismatch"):
                    load(connection, manifest, records, sha)


if __name__ == "__main__":
    unittest.main()
