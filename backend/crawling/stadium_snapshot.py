"""Build and validate a portable, immutable snapshot without calling provider APIs."""
import argparse
import hashlib
import json
import math
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from collect_stadium_pilot import distance

FILES = ("public_places.jsonl", "convenience_review.jsonl", "google_lodging_ids.jsonl")
SOURCE_RUN = {"SBIZ": "SBIZ", "PARK": "PARK", "TOUR": "TOUR_WALK"}
KINDS = {"restaurant", "bar", "cafe", "convenience_store", "play_facility", "walk_candidate"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def valid_coordinate(lat, lng):
    return (isinstance(lat, (float, int)) and isinstance(lng, (float, int))
            and math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180)


def validate(folder):
    """Return fully checked data in memory so files cannot change during DB import."""
    raw_manifest = (folder / "manifest.json").read_bytes()
    manifest = json.loads(raw_manifest)
    require(manifest.get("schema_version") == 1, "Unsupported snapshot version")
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", manifest.get("snapshot_id", "")), "Invalid snapshot ID")
    require(manifest.get("radius_m") == 2500 and manifest.get("distance_type") == "straight_line",
            "Expected a 2500m straight-line collection")
    require(manifest.get("lodging_source") == "GOOGLE_PLACES", "Expected Google-only lodging")
    require(bool(manifest.get("stadiums")), "No stadiums")
    expected_paths = {f"{code}/{name}" for code in manifest["stadiums"] for name in FILES}
    require(set(manifest["files"]) == expected_paths, "Unexpected or missing snapshot files")
    records, totals = {}, Counter()
    for code, stadium in manifest["stadiums"].items():
        require(re.fullmatch(r"[A-Z0-9_]+", code), "Invalid stadium code")
        require(stadium["code"] == code and valid_coordinate(stadium["lat"], stadium["lng"]),
                "Invalid stadium location")
        sources = stadium["sources"]
        require(set(sources) == {"SBIZ", "PARK", "TOUR_WALK", "GOOGLE"}, "Incomplete sources")
        for meta in sources.values():
            require(meta.get("status") == "ok", "Source collection did not complete")
            require(datetime.fromisoformat(meta["completed_at"]).tzinfo is not None, "Missing collection timezone")
        seen, selected_by_source = set(), Counter()
        for filename in FILES:
            relative = f"{code}/{filename}"
            data = (folder / relative).read_bytes()
            file_meta = manifest["files"][relative]
            require(digest(data) == file_meta["sha256"] and len(data) == file_meta["bytes"],
                    f"Checksum mismatch: {relative}")
            rows = [json.loads(line) for line in data.splitlines()]
            require(len(rows) == file_meta["rows"], f"Row count mismatch: {relative}")
            records[relative] = rows
            for row in rows:
                if filename == "google_lodging_ids.jsonl":
                    require(set(row) == {"source", "place_id"} and row["source"] == "GOOGLE_PLACES",
                            "Google snapshot must contain IDs only")
                    require(isinstance(row["place_id"], str) and bool(row["place_id"].strip()), "Missing Place ID")
                    identity = ("GOOGLE_PLACES", row["place_id"])
                else:
                    require(row.get("source") in SOURCE_RUN and row.get("kind") in KINDS,
                            "Unexpected public source or category (lodging is excluded)")
                    require(isinstance(row.get("source_id"), str) and bool(row["source_id"]), "Missing source ID")
                    require(isinstance(row.get("name"), str) and isinstance(row.get("address"), str), "Missing name/address")
                    require(isinstance(row.get("source_fields"), dict), "Missing source provenance")
                    require(valid_coordinate(row["lat"], row["lng"]), "Invalid place location")
                    actual = distance(stadium["lat"], stadium["lng"], row["lat"], row["lng"])
                    require(actual <= 2500 and math.isfinite(row["distance_m"])
                            and abs(actual - row["distance_m"]) <= 1, "Place outside radius or incorrect distance")
                    identity = (row["source"], row["source_id"])
                    if filename == "convenience_review.jsonl":
                        require(row["source"] == "SBIZ" and row["kind"] == "convenience_store"
                                and row.get("brand_status") in {"unidentified", "needs_review", "legacy_name"},
                                "Review file must contain only unselected convenience stores")
                    else:
                        if row["kind"] == "convenience_store":
                            require(row.get("brand_status") == "name_identified", "Unreviewed convenience store selected")
                        selected_by_source[row["source"]] += 1
                require(identity not in seen, f"Duplicate source ID in {code}")
                seen.add(identity)
            totals[filename] += len(rows)
        for source, run in SOURCE_RUN.items():
            require(selected_by_source[source] == sources[run]["selected_records"], "Source count mismatch")
        require(len(records[f"{code}/google_lodging_ids.jsonl"]) == sources["GOOGLE"]["within_radius_ids"],
                "Google ID count mismatch")
        require(len(records[f"{code}/convenience_review.jsonl"]) == sources["SBIZ"]["convenience_needs_review"],
                "Convenience review count mismatch")
    require(dict(totals) == manifest["totals"], "Snapshot total mismatch")
    return manifest, records, digest(raw_manifest)


def build(source, output):
    require(not output.exists(), "Output already exists; use a new snapshot directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Publish only after every file validates; failed builds leave no partial snapshot.
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=output.parent) as temporary:
        folder = Path(temporary)
        result = write_snapshot(source, folder)
        folder.rename(output)
    return result


def write_snapshot(source, output):
    summary = read_json(source / "summary.json")
    manifest = {**summary, "snapshot_id": summary["started_at"], "files": {}, "totals": {}}
    totals = Counter()
    for code in summary["stadiums"]:
        require(re.fullmatch(r"[A-Z0-9_]+", code), "Invalid stadium code")
        public = read_json(source / code / "public_places.json")
        selected = {(r["source"], r["source_id"]) for r in public}
        review = [r for r in read_json(source / code / "convenience_review.json")
                  if (r["source"], r["source_id"]) not in selected]
        google = read_json(source / code / "google_lodging_ids.json")
        for filename, rows in zip(FILES, (public, review, google)):
            rows.sort(key=lambda r: (r["source"], r.get("source_id", r.get("place_id"))))
            data = "".join(encode(r) + "\n" for r in rows).encode("utf-8")
            path = output / code / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            manifest["files"][f"{code}/{filename}"] = {"sha256": digest(data), "bytes": len(data), "rows": len(rows)}
            totals[filename] += len(rows)
    manifest["totals"] = dict(totals)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return validate(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Snapshot to validate, or output directory when building")
    parser.add_argument("--from-collection", type=Path, help="Existing collector output; no API calls")
    args = parser.parse_args()
    try:
        result = build(args.from_collection, args.folder) if args.from_collection else validate(args.folder)
    except (ValueError, KeyError, TypeError, OSError):
        parser.exit(1, "Snapshot validation failed; check files, schema, counts and checksums.\n")
    print(json.dumps({"snapshot_id": result[0]["snapshot_id"], "stadiums": len(result[0]["stadiums"]),
                      "totals": result[0]["totals"], "manifest_sha256": result[2]}))


if __name__ == "__main__":
    main()
