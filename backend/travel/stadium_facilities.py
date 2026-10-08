"""Stadium tenants stay separate from public nearby places and course policy.

Source rows are observations, not live business listings. Food pins are display
references beside the stadium marker; only source text describes store location.
"""
import csv
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings

from .collected_places import CatalogueQueryError, CatalogueUnavailable

FILES = ("구장먹거리_위치_자리어때.csv", "구장편의시설_위치_자리어때.csv", "stadium_facility_pins.json", "stadium_locations.json")
SCOPES = {"internal": "구장 내부", "exterior": "구장 외부 부속", "unknown": "구장 소속 · 내외부 미확인"}


def facility_document(row):
    """Canonical RAG facts shared by import and read-time source enforcement."""
    return {
        "id": f"facility:{row['id']}", "place_id": row["id"], "document_type": "facility",
        "stadium": row["stadium"], "kind": "cafe" if row.get("foodCategory") == "CAFE" else row["kind"],
        "scope": {"internal": "internal", "exterior": "stadium_exterior", "unknown": "stadium_unknown"}[row["scope"]],
        "name": row["name"], "floor": row["floor"], "zone": row["zone"],
        "food_category": row.get("foodCategory"), "source_location": row.get("sourceLocation"),
        "category": "카페·디저트" if row.get("foodCategory") == "CAFE" else "먹거리" if row["kind"] == "food" else "편의시설",
        "source": "자리어때 수집 목록", "source_url": row["sourceUrl"],
        "checked_at": row["sourceCheckedAt"], "evidence_type": row["evidenceType"],
        "pins": [{k: pin.get(k) for k in ("id", "lat", "lng", "quality", "uncertaintyM")} for pin in row["pins"]],
        "location_status": row["locationStatus"], "current_operation": "unverified",
        "ticket_required": "unverified", "menu_verified": False, "review_verified": False,
    }


def _root():
    default = Path("/data/preprocessed") if Path("/data/preprocessed").is_dir() else Path(__file__).resolve().parents[2] / "data/preprocessed"
    return Path(getattr(settings, "STADIUM_FACILITIES_DIR", default))


def _source_url(value):
    url = urlparse(value)
    if url.scheme != "https" or url.hostname != "myseatcheck.com":
        raise ValueError("Invalid facility source")
    return value


def _scope(row, kind):
    if kind == "food":
        return "internal"  # All MySeatCheck food listings, including source-labelled exterior stores.
    # A roofless outfield facility is NOT necessarily outside the ticket gates.
    text = " ".join(row.get(key, "") for key in ("floor", "location_detail"))
    return "exterior" if "외부" in text else "unknown"


def _food_pin(row, center):
    # Stable visual spreading, deliberately unrelated to floors/aisles in the source.
    index = int(row["id"].rsplit("_", 1)[1])
    angle = math.radians(index * 137.507764)
    radius = 55 + (index % 3) * 5
    return {
        "id": f"{row['id']}-reference", "recordId": row["id"],
        "lat": round(center["lat"] + radius * math.sin(angle) / 111320, 7),
        "lng": round(center["lng"] + radius * math.cos(angle) / (111320 * math.cos(math.radians(center["lat"]))), 7),
        "label": "구장 옆 표시 핀", "quality": "display_reference", "uncertaintyM": 0,
        "checkedAt": row["sourceCheckedAt"],
        "source": {"stadium": row["stadium"], "pageUrl": row["sourceUrl"],
                   "note": "구장 핀과 겹치지 않게 배치한 표시 위치입니다. 실제 매장 위치는 상세 링크의 층·구역을 확인하세요."},
    }


@lru_cache(maxsize=2)
def _load(folder, versions):
    root = Path(folder)
    audit = json.loads((root / FILES[2]).read_text(encoding="utf-8"))
    if audit["schemaVersion"] != 1:
        raise ValueError("Invalid facility pin schema")
    sources = audit["sources"]
    for source in sources.values():
        _source_url(source["pageUrl"])
        if source.get("imageUrl"):
            _source_url(source["imageUrl"])
    all_rows, seen = [], set()
    for file, kind in zip(FILES[:2], ("food", "facility")):
        with (root / file).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                identity, code = row["record_id"], row["stadium_code"]
                prefix = "FOOD" if kind == "food" else "FAC"
                if identity in seen or not re.fullmatch(rf"SC_{prefix}_{re.escape(code)}_\d{{3}}", identity) or code not in audit["stadiums"]:
                    raise ValueError("Invalid or duplicate facility identity")
                seen.add(identity)
                scope = _scope(row, kind)
                all_rows.append({
                    "id": identity, "stadium": code, "name": row["store_facility" if kind == "food" else "facility_name"],
                    "kind": kind, "affiliation": "stadium", "scope": scope, "scopeLabel": SCOPES[scope],
                    "floor": row["floor"], "zone": row["zone_location" if kind == "food" else "location_detail"],
                    "sourceUrl": _source_url(row["source_url"]), "sourceCheckedAt": row["verified_at"],
                    "evidenceType": "UNOFFICIAL", "operatingStatus": "unverified", "pin": None,
                    "locationStatus": "zone_only", "pins": [],
                    **({"foodCategory": row.get("food_category") or "FOOD",
                        "sourceLocation": row.get("source_location") or row["zone_location"],
                        "sourceScope": {"Y": "internal", "N": "exterior"}.get(row["in_stadium_flag"], "unknown"),
                        "scopeBasis": "myseatcheck_food_listing"} if kind == "food" else {}),
                })
    by_id = {row["id"]: row for row in all_rows}
    centers = json.loads((root / FILES[3]).read_text(encoding="utf-8"))["stadiums"]
    for row in all_rows:
        if row["kind"] == "food":
            normalize = lambda value: re.sub(r"[\s()]", "", value)
            location = row["sourceLocation"]
            row["locationLabel"] = location if normalize(row["zone"]) in normalize(location) else " · ".join(dict.fromkeys(filter(None, [row["zone"], location])))
            row["pin"] = _food_pin(row, centers[row["stadium"]])
            row["pins"] = [row["pin"]]
            row["locationStatus"] = "reference_pin"
    pin_ids = set()
    for pin in audit["pins"]:
        if pin["recordId"].startswith("SC_FOOD_"):
            continue
        row = by_id[pin["recordId"]]
        source = sources[pin["sourceId"]]
        lat, lng = pin["lat"], pin["lng"]
        if (pin["id"] in pin_ids or source["stadium"] != row["stadium"]
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in (lat, lng))
                or not (33 < lat < 39 and 124 < lng < 132)
                or pin["quality"] not in ("diagram_approximate", "section_approximate")
                or not 10 <= pin["uncertaintyM"] <= 100):
            raise ValueError("Invalid stadium pin")
        pin_ids.add(pin["id"])
        row["pins"].append({**pin, "source": source})
        row["locationStatus"] = "approximate_pin"
        row["pin"] = row["pins"][0]
    return all_rows, audit


def facility_catalogue(code):
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2,20}", code):
        raise CatalogueQueryError("구장 코드를 확인해 주세요.")
    root = _root()
    try:
        versions = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in (root / file for file in FILES))
        rows, audit = _load(str(root), versions)
        if code not in audit["stadiums"]:
            raise CatalogueQueryError("지원하지 않는 구장입니다.")
        from .stadium_scope import classify_stadium_point, reviewed_zones
        zones = reviewed_zones()
        records = []
        for row in rows:
            if row["stadium"] != code:
                continue
            # Keep cached source rows immutable. A red-only pin may never be
            # selected via the separate internal-facility path either.
            pins = [p for p in row["pins"] if classify_stadium_point(p, zones)["scope"] != "excluded_complex"]
            if row["pins"] and not pins:
                continue
            item = {**row, "pins": pins, "pin": pins[0] if pins else None}
            if row["kind"] != "food" and pins and all(classify_stadium_point(p, zones) == {"scope": "internal", "stadium": code} for p in pins):
                item.update(sourceScope=row["scope"], scope="internal", scopeLabel=SCOPES["internal"], scopeBasis="reviewed_main_frame")
            records.append(item)
        return {"stadium": code, "records": records, "count": len(records),
                "pinCount": sum(len(row["pins"]) for row in records), "checkedAt": max([audit["checkedAt"]] + [row["sourceCheckedAt"] for row in records]),
                "warning": "자리어때 먹거리 목록의 외부 표기 매장도 내부 먹거리로 분류합니다. 먹거리 핀은 구장 옆 표시 위치이며 실제 매장 위치가 아닙니다. 매장 상세 위치 링크에서 층·구역을 확인하세요. 현재 영업 및 입장권 필요 여부는 미확인입니다.",
                "review": {**audit["stadiums"][code], "status": "food_details_complete",
                           "note": "먹거리 목록의 매장별 상세 URL·층·구역을 대조했습니다. 먹거리 핀은 위치 안내용 표시이며 실제 매장 좌표가 아닙니다.",
                           "source": audit["sources"][audit["stadiums"][code]["sourceId"]]}}
    except CatalogueQueryError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CatalogueUnavailable from error
