"""Explicit local-file importers. Never recursively ingest logs, secrets or chats."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from . import collected_places, stadium_facilities
from .place_rag import STADIUMS

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ANALYSIS = REPO / "output/evals/serper-verify-20261001-153927"


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def collected_documents():
    """Reuse the catalogue's checksum/coordinate checks and reviewed stadium centers."""
    root, _, manifest = collected_places._metadata()
    docs = []
    for code in sorted(manifest["stadiums"]):
        if code not in STADIUMS:
            raise ValueError("Unknown stadium in snapshot")
        data = collected_places.catalogue(code)
        for p in data["places"]:
            scope = "stadium_unknown" if p.get("stadiumAffiliation") else "external_candidate"
            docs.append({
                "id": f"place:{code}:{p['placeId']}", "place_id": p["placeId"],
                "document_type": "place", "stadium": code, "kind": p["kind"], "scope": scope,
                "name": p["name"], "address": p["address"], "lat": p["lat"], "lng": p["lng"],
                "category": p["category"], "subcategory": p["subcategory"], "cuisine": p["cuisine"],
                "distance_m": p["distance"], "source": p["source"], "snapshot_id": data["snapshotId"],
                "source_record": f"{code}/public_places.jsonl#{p['placeId']}",
                "collected_at": p["collectedAt"], "reference_month": p["referenceMonth"],
                "current_operation": "unverified", "menu_verified": False, "review_verified": False,
            })
    return docs, {"snapshot_id": manifest["snapshot_id"], "manifest_sha256": fingerprint(root / "manifest.json")}


def facility_documents():
    docs = []
    for code in STADIUMS:
        data = stadium_facilities.facility_catalogue(code)
        docs.extend(stadium_facilities.facility_document(p) for p in data["records"])
    root = stadium_facilities._root()
    return docs, {name: fingerprint(root / name) for name in stadium_facilities.FILES}


def _citation(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme in {"https", "http"} and bool(parts.hostname)
                and "." in parts.hostname and not parts.username and not parts.password)
    except ValueError:
        return False


def analysis_records(folder, documents):
    """Our audited research status, not review text or an approval to reuse facts.

    This adapter supports the audited Serper experiment only. Older unaudited
    trials are deliberately not merged into a stronger or 'latest' truth.
    """
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    audit = json.loads((folder / "audit.json").read_text(encoding="utf-8"))
    results = [json.loads(line) for line in (folder / "results.jsonl").read_text(encoding="utf-8").splitlines() if line]
    cases = {c["case_id"]: c for c in manifest["cases"]}
    if len(cases) != len(manifest["cases"]) or {r["case_id"] for r in results} != set(cases) or len(results) != len(cases):
        raise ValueError("Incomplete or duplicate analysis cases")
    identities = {(d["place_id"], d["name"], d.get("address")) for d in documents if d["document_type"] == "place"}
    records, counts = [], {"pass": 0, "fail": 0, "unknown": 0}
    supported = set(audit["supported_menu_case_ids"])
    for result in results:
        case_id = result["case_id"]
        case = cases[case_id]
        if (case["placeId"], case["name"], case["address"]) not in identities:
            raise ValueError(f"Analysis place identity mismatch: {case_id}")
        raw_status = result["verdict"]["status"]
        override = audit.get("status_overrides", {}).get(case_id)
        if override and override["raw_status"] != raw_status:
            raise ValueError("Audit no longer matches raw results")
        status = override["reviewed_status"] if override else raw_status
        if status not in counts or (status == "pass" and (case_id not in supported or case["goal"] != "menu")):
            raise ValueError("Unsupported audited verdict")
        counts[status] += 1
        # Read page URLs are a research trail, NOT automatically positive menu evidence.
        urls = sorted({p.get("final_url") or p["url"] for r in result["rounds"] for p in r["page_attempts"]
                       if p.get("body_read") is True and _citation(p.get("final_url") or p["url"])})
        records.append({
            "id": f"{folder.name}:{case_id}", "place_id": case["placeId"], "name": case["name"],
            "address": case["address"], "attribute": case["goal"],
            "requested_term": case["requested_menu"] if case["goal"] == "menu" else
                              {"quietness": "조용함", "cleanliness": "깔끔함"}[case["goal"]],
            "status": status, "raw_status": raw_status, "audit_corrected": override is not None,
            "run_started_at": manifest["prepared_at"], "checked_on": manifest["today_kst"],
            "audited_on": audit["review_date"],
            "usable_as_fact": False, "reuse_policy": "unreviewed", "needs_recheck": True,
            "reason": "audit_corrected_unknown" if override else result["verdict"]["reason"],
            "reviewed_page_urls": urls, "search_rounds": len(result["rounds"]),
            "source_record": f"{folder.name}/results.jsonl#{case_id}",
            "warning": "우리 실험의 판정 기록입니다. pass도 현재 판매 확정이 아니며 unknown은 미판매·특징 부재가 아닙니다. 읽은 모든 URL이 조건 근거인 것은 아닙니다.",
        })
    if counts != audit["reviewed_final_counts"]:
        raise ValueError("Audited counts mismatch")
    return records, {"run": folder.name, "files": {name: fingerprint(folder / name)
                     for name in ("manifest.json", "audit.json", "results.jsonl")}}
