"""Stadium tenants stay separate from public nearby places and course policy.

Source rows are historical observations, not live business listings. Never infer
ticket access from 'outdoors', or manufacture a pin from the stadium centroid.
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

FILES = ("구장먹거리_위치_자리어때.csv", "구장편의시설_위치_자리어때.csv", "stadium_facility_pins.json")
SCOPES = {"internal": "구장 내부", "exterior": "구장 외부 부속", "unknown": "구장 소속 · 내외부 미확인"}


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
        return {"Y": "internal", "N": "exterior"}.get(row["in_stadium_flag"], "unknown")
    # A roofless outfield facility is NOT necessarily outside the ticket gates.
    text = " ".join(row.get(key, "") for key in ("floor", "location_detail"))
    return "exterior" if "외부" in text else "unknown"


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
                })
    by_id = {row["id"]: row for row in all_rows}
    pin_ids = set()
    for pin in audit["pins"]:
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
            if pins and all(classify_stadium_point(p, zones) == {"scope": "internal", "stadium": code} for p in pins):
                item.update(sourceScope=row["scope"], scope="internal", scopeLabel=SCOPES["internal"], scopeBasis="reviewed_main_frame")
            records.append(item)
        return {"stadium": code, "records": records, "count": len(records),
                "pinCount": sum(len(row["pins"]) for row in records), "checkedAt": audit["checkedAt"],
                "warning": "구장 소속 자료를 주변 상점과 분리한 목록입니다. 핀은 안내도·구역 기반 근사 위치이며 층·통로를 함께 확인하세요. 현재 영업 및 입장권 필요 여부는 미확인입니다. 핀이 없는 매장은 임의 좌표를 표시하지 않습니다.",
                "review": {**audit["stadiums"][code], "source": audit["sources"][audit["stadiums"][code]["sourceId"]]}}
    except CatalogueQueryError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CatalogueUnavailable from error
