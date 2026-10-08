"""Read-only place catalogue from the explicitly selected collection snapshot.

No provider calls, DB writes, unreviewed records or Google place details. Files
are checksum-checked and cached per process; a broken catalogue fails closed.
"""
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from baseball.stadium_locations import reviewed_locations
from .stadium_affiliation import stadium_affiliation_hint


DEFAULT_SNAPSHOT = "20260927T092810Z-no-sbiz"
SOURCES = {"PARK": "전국도시공원 표준데이터", "TOUR": "한국관광공사"}
KINDS = {"restaurant": "food", "bar": "food", "cafe": "cafe", "convenience_store": "store", "play_facility": "indoor", "walk_candidate": "walk"}
LABELS = {"food": "먹거리", "cafe": "카페·디저트", "store": "편의점", "indoor": "실내 놀거리", "walk": "산책"}
GROUPS = {"FD6": {"food"}, "CE7": {"cafe"}, "CS2": {"store"}, "CT1": {"indoor"}, "AT4": {"walk"}, "AD5": set()}
WARNING = "수집 당시의 후보 정보입니다. 현재 영업·메뉴·예약 가능 여부는 미확인입니다. 산책 장소는 대표 지점이며 보행 경로·입구가 아닙니다. 출처 간 중복이 남아 있을 수 있습니다."
LODGING_NOTE = "숙박 수집본에는 Google Place ID만 있어 이름·좌표를 제공할 수 없습니다. 숙박 상세 조회는 미연결이며 다른 출처로 대체하지 않습니다."


class CatalogueUnavailable(Exception):
    message = "수집 장소 데이터를 불러올 수 없습니다. 스냅샷 경로와 파일 검증 상태를 확인해 주세요."


class CatalogueQueryError(ValueError):
    pass


def _root():
    return Path(getattr(settings, "COLLECTED_PLACES_DIR", Path(__file__).resolve().parents[2] / "data/staging/stadium_places" / DEFAULT_SNAPSHOT))


def _version(path):
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _coordinates(lat, lng):
    return all(type(v) in (int, float) and math.isfinite(v) for v in (lat, lng)) and abs(lat) <= 90 and abs(lng) <= 180


def distance(a_lat, a_lng, b_lat, b_lng):
    lat1, lat2 = math.radians(a_lat), math.radians(b_lat)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(b_lng - a_lng) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, h)))


@lru_cache(maxsize=2)
def _manifest(folder, version):
    data = json.loads((Path(folder) / "manifest.json").read_bytes())
    if data["schema_version"] != 2 or data["radius_m"] != 2500 or data["distance_type"] != "straight_line":
        raise ValueError("Unsupported snapshot")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", data["snapshot_id"]) or not data["stadiums"]:
        raise ValueError("Invalid snapshot")
    for code, stadium in data["stadiums"].items():
        if not re.fullmatch(r"[A-Z0-9_]{1,30}", code) or stadium["code"] != code or not _coordinates(stadium["lat"], stadium["lng"]):
            raise ValueError("Invalid stadium")
    return data


def _metadata():
    try:
        root = _root()
        version = _version(root / "manifest.json")
        return root, version, _manifest(str(root), version)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CatalogueUnavailable from error


def _cuisine(category):
    for word, value in (("한식", "한식"), ("중식", "중식"), ("일식", "일식"), ("서양", "양식"), ("양식", "양식"), ("분식", "분식"), ("치킨", "치킨")):
        if word in category:
            return value
    return "기타"


@lru_cache(maxsize=18)
def _catalogue(folder, manifest_version, code, file_version):
    manifest = _manifest(folder, manifest_version)
    stadium = manifest["stadiums"][code]
    relative = f"{code}/public_places.jsonl"
    data = (Path(folder) / relative).read_bytes()
    meta = manifest["files"][relative]
    if len(data) != meta["bytes"] or hashlib.sha256(data).hexdigest() != meta["sha256"]:
        raise ValueError("Checksum mismatch")
    rows = [json.loads(line) for line in data.splitlines()]
    if len(rows) != meta["rows"]:
        raise ValueError("Row count mismatch")
    places, seen = [], set()
    for row in rows:
        source, raw_kind = row["source"], row["kind"]
        if source not in SOURCES or raw_kind not in KINDS or not _coordinates(row["lat"], row["lng"]):
            raise ValueError("Invalid public record")
        if not isinstance(row["source_id"], str) or not row["source_id"] or not isinstance(row["name"], str) or not row["name"].strip() or not isinstance(row["address"], str):
            raise ValueError("Invalid identity")
        if raw_kind == "convenience_store" and row.get("brand_status") != "name_identified":
            raise ValueError("Unreviewed store")
        place_id = f"collected:{source}:{row['source_id']}"
        if place_id in seen or len(place_id) > 255:
            raise ValueError("Duplicate or invalid identity")
        seen.add(place_id)
        actual_distance = distance(stadium["lat"], stadium["lng"], row["lat"], row["lng"])
        if actual_distance > 2500 or abs(actual_distance - row["distance_m"]) > 1:
            raise ValueError("Invalid distance")
        run = stadium["sources"]["TOUR_WALK" if source == "TOUR" else source]
        if run["status"] != "ok":
            raise ValueError("Incomplete source")
        kind = KINDS[raw_kind]
        subcategory = row.get("category_small") or LABELS[kind]
        if source == "TOUR":
            # TourAPI taxonomy IDs are not human-readable subcategory names.
            subcategory = "산책길 후보" if row.get("walk_type") == "trail_candidate" else "공원·정원 후보"
        places.append({
            "placeId": place_id, "name": row["name"], "lat": row["lat"], "lng": row["lng"],
            "kind": kind, "category": LABELS[kind], "subcategory": subcategory,
            "cuisine": _cuisine(f"{subcategory} {row.get('category_middle') or ''}") if kind == "food" else "기타",
            "address": row["address"], "phone": "", "distance": round(actual_distance, 1),
            "detail": f"{subcategory} · {SOURCES[source]}", "source": source,
            "collectedAt": run["completed_at"], "referenceMonth": run.get("reference_month"),
            "verificationStatus": "unverified", "cafeType": row.get("cafe_type"),
            "stadiumAffiliation": stadium_affiliation_hint(code, row),
        })
    places.sort(key=lambda p: (p["distance"], p["placeId"]))
    return {"status": "ok", "snapshotId": manifest["snapshot_id"], "stadium": code, "radiusM": 2500,
            "places": places, "count": len(places), "warning": WARNING,
            "lodging": {"status": "details_unavailable", "message": LODGING_NOTE}}


def catalogue(stadium_code):
    if not isinstance(stadium_code, str) or not re.fullmatch(r"[A-Z0-9_]{1,30}", stadium_code):
        raise CatalogueQueryError("구장 코드를 확인해 주세요.")
    root, version, manifest = _metadata()
    if stadium_code not in manifest["stadiums"]:
        raise CatalogueQueryError("수집 데이터가 없는 구장입니다.")
    try:
        original = _catalogue(str(root), version, stadium_code, _version(root / stadium_code / "public_places.jsonl"))
        # Validate immutable snapshot distances against its original center first.
        # Then re-filter around the reviewed venue; this does not invent missing data.
        point = reviewed_locations(root.parents[2]).get(stadium_code) if len(root.parents) > 2 else None
        if not point:
            return original
        places = []
        for place in original["places"]:
            d = distance(point["lat"], point["lng"], place["lat"], place["lng"])
            if d <= original["radiusM"]:
                places.append({**place, "distance": round(d, 1)})
        places.sort(key=lambda p: (p["distance"], p["placeId"]))
        return {**original, "places": places, "count": len(places), "warning": WARNING + " 구장 중심점을 보정해 재필터링했습니다. 이전 수집 원 밖에서 새 반경에 들어온 지역은 아직 미수집입니다."}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CatalogueUnavailable from error


def search_collected_places(query):
    """Compatibility-shaped bounded results for the chatbot, without a live fallback."""
    lat, lng = query.get("lat"), query.get("lng")
    if not _coordinates(lat, lng):
        raise CatalogueQueryError("장소 검색 좌표를 확인해 주세요.")
    _, _, manifest = _metadata()
    code = query.get("stadium")
    if not code:
        code = min(manifest["stadiums"], key=lambda c: distance(lat, lng, manifest["stadiums"][c]["lat"], manifest["stadiums"][c]["lng"]))
    result = catalogue(code)
    stadium = manifest["stadiums"][code]
    if distance(lat, lng, stadium["lat"], stadium["lng"]) > 5000:
        raise CatalogueQueryError("수집 범위 밖입니다. 선택한 구장의 좌표로 검색해 주세요.")
    page, size, radius = query.get("page", 1), query.get("size", 15), query.get("radius", 2500)
    category, keyword = query.get("category"), query.get("keyword", "")
    if type(page) is not int or not 1 <= page <= 3 or type(size) is not int or not 1 <= size <= 15 or type(radius) is not int or not 1 <= radius <= 20000:
        raise CatalogueQueryError("검색 범위를 확인해 주세요.")
    if category is not None and category not in GROUPS or not isinstance(keyword, str) or len(keyword) > 100:
        raise CatalogueQueryError("검색 조건을 확인해 주세요.")
    tokens = keyword.strip().lower().split()
    aliases = {"맛집": "먹거리", "식당": "먹거리", "음식점": "먹거리", "커피": "카페", "산책로": "산책", "둘레길": "산책"}
    matches = []
    from .stadium_scope import classify_stadium_point, reviewed_zones
    zones = reviewed_zones()
    for place in result["places"]:
        if classify_stadium_point(place, zones)["scope"] != "external":
            continue
        if category is not None and place["kind"] not in GROUPS[category]:
            continue
        text = " ".join((place["name"], place["category"], place["detail"], place["address"], place["cuisine"])).lower()
        if any(aliases.get(token, token) not in text for token in tokens):
            continue
        d = distance(lat, lng, place["lat"], place["lng"])
        if d <= radius:
            matches.append((d, place))
    matches.sort(key=lambda item: (item[0], item[1]["placeId"]))
    start = (page - 1) * size
    items = [{**place, "id": place["placeId"], "place_name": place["name"], "x": str(place["lng"]), "y": str(place["lat"]),
              "road_address_name": place["address"], "category_name": place["detail"], "distance": round(d, 1)}
             for d, place in matches[start:start + size]]
    return {"places": items, "hasNextPage": start + size < len(matches) and page < 3,
            "total": len(matches), "snapshotId": result["snapshotId"], "source": "collected_snapshot",
            "warning": WARNING, "lodging": result["lodging"]}


def search_collected_tourism(query):
    result = search_collected_places({**query, "category": "AT4", "size": 15})
    return {**result, "status": "ok", "truncated": result["total"] > len(result["places"])}


def planning_catalogue(stadium_code):
    """Internal planner input, not a new public API or provider query.

    Coverage is the intersection of both circles, not the union. Missing areas
    after venue corrections must never be reported as an exhaustive empty search.
    All local candidates are available here, independent of UI/API pagination.
    """
    result = catalogue(stadium_code)
    root, _, manifest = _metadata()
    original = manifest["stadiums"][stadium_code]
    reviewed = reviewed_locations(root.parents[2]).get(stadium_code) if len(root.parents) > 2 else None
    centers = [original] + ([reviewed] if reviewed else [])
    return {**result, "coverage": [{"lat": c["lat"], "lng": c["lng"], "radius_m": result["radiusM"]}
                                   for c in centers]}
