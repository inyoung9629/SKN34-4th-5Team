"""Course candidates from the collected MySeatCheck food catalogue only."""
import math
import re
import json
from functools import lru_cache
from pathlib import Path

from .stadium_facilities import facility_catalogue, _root

PLACE_ID = re.compile(r"stadium-facility:(SC_FOOD_([A-Z]+)_\d{3}):(.+)")
_CHURROS_ALIAS = re.compile(r"(?<![a-z])churros?(?![a-z])|추러스|츄로스|추로스", re.I)


def menu_catalogue():
    path = _root() / "myseatcheck_menus.json"
    if not path.exists():
        return {}
    stat = path.stat()
    return _menu_catalogue(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=2)
def _menu_catalogue(path, modified, size):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["records"]
    return {row["facilityId"]: row for row in rows if row.get("reviewStatus") in
            ("VISUALLY_REVIEWED", "VISUALLY_REVIEWED_PARTIAL", "VISION_DOUBLE_READ") and row.get("items")}


def menu_text(value):
    # 사진의 영문 메뉴명은 그대로 보존하고 검색할 때만 같은 음식 표기를 묶는다.
    # 영문 단어 경계를 먼저 검사해 churros와 무관한 상호/단어까지 치환하지 않는다.
    value = _CHURROS_ALIAS.sub("츄러스", value.casefold())
    value = value.replace("돈가스", "돈까스").replace("돈카츠", "돈까스").replace("자장", "짜장")
    return re.sub(r"[^가-힣a-z0-9]", "", value)


def matching_menu_items(place, query):
    term = menu_text(query)
    if len(term) < 2:
        return []
    return [item for item in place.get("menuEvidence", {}).get("items", [])
            if term in menu_text(" ".join((item["name"], item.get("option", ""), item.get("description", ""))))]


def food_category(name):
    # Only classify labels present in the source; do not invent menus or hours.
    if re.search(r"편의점|GS25|세븐일레븐|이마트24|\bCU\b", name, re.I):
        return "CONVENIENCE"
    if re.search(r"카페|커피|coffee|cafe|스타벅스|이디야|투썸|공차|탐앤탐스|폴바셋|파스쿠찌", name, re.I):
        return "CAFE"
    return "FOOD"


def food_candidates(code):
    from .stadium_scope import classify_stadium_point, reviewed_zones
    zones = reviewed_zones()
    places, menus = [], menu_catalogue()
    for row in facility_catalogue(code)["records"]:
        if row["kind"] != "food" or row["scope"] != "internal":
            continue
        menu = menus.get(row["id"])
        if menu and (menu["stadium"] != code or menu["store"] != row["name"] or menu["sourceUrl"] != row["sourceUrl"]):
            menu = None
        for pin in row["pins"]:
            area = classify_stadium_point(pin, zones)
            if area != {"scope": "internal", "stadium": code}:
                continue
            places.append({
                "placeId": f"stadium-facility:{row['id']}:{pin['id']}",
                "name": row["name"], "lat": pin["lat"], "lng": pin["lng"],
                "category": row.get("foodCategory") or food_category(row["name"]), "stadium": code,
                "scope": "internal", "stadiumArea": area, "source": "MYSEATCHECK",
                "placeUrl": row["sourceUrl"], "collectedAt": row["sourceCheckedAt"],
                "address": row.get("locationLabel") or row["zone"],
                "detail": f"구장 내부 먹거리 · 자리어때 수집 목록(외부 표기 매장 포함) · {row['floor']} · {row['zone']}",
                "sourceNotice": "핀은 구장 옆 표시 위치이며 실제 매장 좌표가 아닙니다. 매장 상세 위치 링크에서 층·구역을 확인하세요. 현재 영업·메뉴·입장권 필요 여부는 미확인입니다.",
                **({"menuEvidence": menu,
                    "sourceNotice": f"자리어때 메뉴판 사진 확인일: {menu['checkedAt']}. 메뉴·가격은 사진에 표기된 정보이며 현재 판매·가격·영업 여부는 미확인입니다. 핀은 구장 옆 표시 위치입니다."} if menu else {}),
            })
    return places


def resolve_food_place(place, catalogues=None):
    """An ID/source label alone cannot turn a Kakao or stale RAG row into a source row."""
    match = PLACE_ID.fullmatch(str(place.get("placeId") or place.get("id") or ""))
    if not match:
        return None
    catalogues = {} if catalogues is None else catalogues
    code = match[2]
    if code not in catalogues:
        catalogues[code] = {p["placeId"]: p for p in food_candidates(code)}
    canonical = catalogues[code].get(match[0])
    if not canonical or canonical["name"] != (place.get("name") or place.get("place_name")):
        return None
    try:
        if any(not math.isfinite(float(place.get(key, place.get(raw))))
               or abs(float(place.get(key, place.get(raw))) - canonical[key]) > 1e-7
               for key, raw in (("lat", "y"), ("lng", "x"))):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return dict(canonical)


def provider_shape(place):
    """Adapt the collected row to the existing course search interface, without Kakao."""
    return {**place, "id": place["placeId"], "place_name": place["name"],
            "y": str(place["lat"]), "x": str(place["lng"]),
            "category_group_code": {"FOOD": "FD6", "CAFE": "CE7", "CONVENIENCE": "CS2"}[place["category"]],
            "category_name": place["detail"], "road_address_name": place["address"],
            "place_url": place["placeUrl"]}
