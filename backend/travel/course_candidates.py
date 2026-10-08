"""RAG-first external course candidates; no network or factual claim promotion.

The source catalogue remains the integrity/coverage authority. The RAG index
selects external identities, but its copied fields must agree with that source.
Research outcomes, stadium facilities and lodging URL-only records are never
used as external route stops. Missing/stale indexes fall back only to the same
local source, with the same affiliation exclusions and an explicit audit reason.
"""
from contextlib import closing
import json
import sqlite3

from .collected_places import planning_catalogue
from .place_rag import RagUnavailable, STADIUMS, _connect, index_path
from .stadium_scope import classify_stadium_point, reviewed_zones


MAX_STADIUM_DOCUMENTS = 10000
FIELD_MAP = {
    "place_id": "placeId", "name": "name", "address": "address",
    "lat": "lat", "lng": "lng", "kind": "kind", "source": "source",
    "category": "category", "subcategory": "subcategory", "cuisine": "cuisine",
}


def _read_candidates(code, path):
    # One SQLite read transaction sees a consistent metadata/documents snapshot.
    with closing(_connect(path)) as conn:
        conn.execute("BEGIN")
        metadata = conn.execute("SELECT value FROM metadata WHERE key='report'").fetchone()
        if not metadata:
            raise RagUnavailable("Missing index metadata")
        report = json.loads(metadata[0])
        rows = conn.execute(
            "SELECT place_id, payload FROM documents WHERE stadium=? AND scope=? ORDER BY id LIMIT ?",
            (code, "external_candidate", MAX_STADIUM_DOCUMENTS + 1),
        ).fetchall()
        if len(rows) > MAX_STADIUM_DOCUMENTS:
            raise RagUnavailable("Candidate set exceeds planner bound")
        return report, [(identity, json.loads(payload)) for identity, payload in rows]


def load_course_candidates(stadium_code, *, path=None):
    """All external candidates, not the public text-search API's top 20 hits.

The planner still owns radius growth, condition verification and travel time.
An empty/missing menu or review field cannot mean that a place meets a request.
"""
    if stadium_code not in STADIUMS:
        # Keep the catalogue's existing input error contract.
        from .collected_places import CatalogueQueryError
        raise CatalogueQueryError("지원하지 않는 구장 코드입니다.")
    data = planning_catalogue(stadium_code)
    external = [place for place in data["places"] if not place.get("stadiumAffiliation")]
    expected = {place["placeId"]: place for place in external}
    audit = {
        "source": "collected_snapshot", "rag_status": "index_unavailable",
        "candidate_count": len(external),
        "excluded_affiliation_count": len(data["places"]) - len(external),
        "network_used": False, "research_used_as_fact": False,
    }
    try:
        report, rows = _read_candidates(stadium_code, path or index_path())
        if report["provenance"]["public"]["snapshot_id"] != data["snapshotId"]:
            audit["rag_status"] = "snapshot_mismatch"
        else:
            seen = set()
            valid = len(rows) == len(expected)
            for identity, row in rows:
                source = expected.get(identity)
                if (identity in seen or source is None or not isinstance(row, dict)
                        or row.get("document_type") != "place"
                        or row.get("stadium") != stadium_code or row.get("scope") != "external_candidate"
                        or row.get("snapshot_id") != data["snapshotId"]
                        or any(row.get(rag_key) != source.get(source_key) for rag_key, source_key in FIELD_MAP.items())):
                    valid = False
                    break
                seen.add(identity)
            if valid and seen == expected.keys():
                # Return canonical source objects (incl. coverage-related metadata),
                # not arbitrary fields copied into an index payload.
                external = [place for place in external if place["placeId"] in seen]
                audit.update(source="local_place_rag", rag_status="ready", rag_built_at=report["built_at"])
            else:
                audit["rag_status"] = "candidate_mismatch"
    except (RagUnavailable, sqlite3.Error, OSError, ValueError, KeyError, TypeError):
        # Never turn an unavailable index into an empty successful candidate list
        # or a hidden search/model call. Source failures above still propagate.
        pass
    zones = reviewed_zones()
    classified = [(place, classify_stadium_point(place, zones)["scope"]) for place in external]
    external = [place for place, scope in classified if scope == "external"]
    audit.update(candidate_count=len(external),
                 excluded_complex_count=sum(scope == "excluded_complex" for _, scope in classified),
                 excluded_internal_count=sum(scope == "internal" for _, scope in classified))
    return {**data, "places": external, "count": len(external), "candidate_retrieval": audit}
