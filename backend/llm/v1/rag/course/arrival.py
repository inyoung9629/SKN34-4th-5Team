"""출발지 확인과 실제 이동 경로의 구장 반경 진입점. 방향을 직선으로 추측하지 않는다."""
import math
import re

from . import geo, transport
from .default_origins import station_origin
from ...progress import ProgressCancelled, ProgressStorageError

RADIUS_M = 2500
RELATIVE_ORIGINS = {"여기", "내 위치", "현재 위치", "현위치", "지도", "집", "회사"}
STADIUM_ORIGINS = {"구장", "경기장", "야구장"}
NO_ORIGIN = re.compile(r"^(?:아직\s*)?(?:없|없이|미정|미지정|안\s*(?:정|골|찍|지정)|정하지\s*않|지정하지\s*않)")


def coordinate(value):
    if not isinstance(value, dict):
        return None
    try:
        if any(isinstance(value.get(key), bool) for key in ("lat", "lng")):
            return None
        lat, lng = float(value["lat"]), float(value["lng"])
        if math.isfinite(lat) and math.isfinite(lng) and 33 <= lat <= 39.5 and 124 <= lng <= 132:
            return {"lat": lat, "lng": lng}
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return None


def _origin_reference(question):
    """사용자가 직접 쓴 출발 표현만 읽는다. '경기 후 구장에서 출발'은 새 출발지가 아니다."""
    text = question or ""
    # 마침표/쉼표 단위로 좁혀 앞 문장의 경기·취향을 장소 검색어에 섞지 않는다.
    for clause in reversed(re.split(r"[.!?\n,]", text)):
        if re.search(r"경기\s*(?:후|끝)|관람\s*후", clause):
            continue
        explicit = re.search(r"출발(?:지|장소|위치)\s*(?:는|은|:|를|을)?\s*(.+?)(?=\s*(?:에서|으로|로|이고|이야|입니다|이거든)|$)", clause)
        match = re.search(r"(.+?)(?:에서|부터)\s*(?:(?:도보로?|걸어서|자차로?|자동차로?|차로|택시로|대중교통으로|지하철로|버스로)\s*)?(?:출발|시작|갈\s*거|가려고|갈게)", clause)
        value = explicit[1] if explicit else match[1] if match else None
        if not value:
            continue
        value = re.sub(r"^(?:(?:나는|저는|난|전|우리(?:는)?|오늘|내일|이번엔|지금)\s*)+", "", value.strip())
        value = re.sub(r"^(?:(?:오전|오후)?\s*\d{1,2}(?::\d{2}|시(?:\s*\d{1,2}분)?)\s*(?:에|쯤)?\s*)", "", value)
        value = value.strip(" '“”\"")
        if value in RELATIVE_ORIGINS | STADIUM_ORIGINS or 1 < len(value) <= 100:
            return value
    return None


def origin_query(question):
    value = _origin_reference(question)
    if not value or value in RELATIVE_ORIGINS | STADIUM_ORIGINS or NO_ORIGIN.search(value):
        return None
    return value


def resolve_origin(question, history, supplied, anchor, invoke):
    reference = _origin_reference(question)
    query = origin_query(question)
    if reference in STADIUM_ORIGINS:
        return coordinate(anchor), anchor.get("name", "구장"), ""
    if not query and coordinate(supplied):
        name = supplied.get("name")
        label = name.strip()[:100] if isinstance(name, str) and name.strip() else "선택한 출발지"
        return coordinate(supplied), label, ""
    if not query and not reference:
        for message in reversed((history or [])[-8:]):
            if message.get("role") == "user" and (reference := _origin_reference(message.get("content"))):
                query = origin_query(message.get("content"))
                break
    if reference in STADIUM_ORIGINS:
        return coordinate(anchor), anchor.get("name", "구장"), ""
    if reference in RELATIVE_ORIGINS:
        return None, reference, f"출발지 ‘{reference}’의 위치가 필요해요. 주소를 알려 주시거나 GPS·지도 핀으로 출발지를 지정해 주세요."
    if not query:
        return None, "", ""
    try:
        result = invoke("course", "search_places", {"method": "keyword", "query": query,
            "latitude": float(anchor["lat"]), "longitude": float(anchor["lng"]), "sort": "accuracy", "limit": 5})
    except (ProgressCancelled, ProgressStorageError):
        raise
    except Exception:
        result = {}
    items = result.get("places", []) if isinstance(result, dict) else []
    compact = lambda value: re.sub(r"\s+", "", value)
    matches = []
    for item in items:
        name = str(item.get("place_name") or "")
        point = coordinate({"lat": item.get("y"), "lng": item.get("x")})
        if point and compact(query) in compact(name + " " + str(item.get("road_address_name") or item.get("address_name") or "")):
            matches.append((point, name))
    exact = [item for item in matches if compact(item[1]) == compact(query)]
    candidates = exact or matches
    # 이름이 같은 먼 지점이 여럿이면 모델이 임의의 도시/지점을 택하지 않는다.
    if candidates and all(geo._dist(candidates[0][0], item[0]) < 400 for item in candidates[1:]):
        return candidates[0][0], candidates[0][1], ""
    return None, query, f"출발지 ‘{query}’의 위치를 하나로 확인하지 못했어요. 지역이 포함된 장소 이름이나 주소를 알려 주시거나 지도에서 출발지를 지정해 주세요."


def resolve_course_origin(question, history, supplied, anchor, invoke, code):
    """명시한 출발지의 조회 실패는 기본 역으로 대체하지 않는다. 새 생성에서만 사용한다."""
    point, label, error = resolve_origin(question, history, supplied, anchor, invoke)
    if point or error:
        return point, label, error, ""
    station = station_origin(code)
    if not station:
        return None, "", "", ""
    notice = f"출발지가 지정되지 않아 {station['name']}에서 출발하는 코스로 구성했어요."
    return coordinate(station), station["name"], "", notice


def _segment_entry(a, b, anchor, radius):
    """경로 선분과 원의 최초 교차점. 양 끝이 밖인 긴 선분도 놓치지 않는다."""
    scale = 111195 * math.cos(math.radians(anchor["lat"]))
    ax, ay = (a["lng"] - anchor["lng"]) * scale, (a["lat"] - anchor["lat"]) * 111195
    dx, dy = (b["lng"] - a["lng"]) * scale, (b["lat"] - a["lat"]) * 111195
    length2 = dx * dx + dy * dy
    if not length2:
        return None
    closest = max(0, min(1, -(ax * dx + ay * dy) / length2))
    point_at = lambda t: {"lat": a["lat"] + (b["lat"] - a["lat"]) * t, "lng": a["lng"] + (b["lng"] - a["lng"]) * t}
    if geo._dist(point_at(closest), anchor) > radius:
        return None
    low, high = 0.0, closest
    for _ in range(45):
        mid = (low + high) / 2
        if geo._dist(point_at(mid), anchor) <= radius:
            high = mid
        else:
            low = mid
    return point_at(high)


def first_entry(paths, anchor, radius=RADIUS_M):
    """제공자가 반환한 선분 위의 진입점만 반환. 분리된 경로 사이에 가상의 선을 잇지 않는다."""
    if not isinstance(paths, list) or not coordinate(anchor):
        return None
    for path in paths:
        if not isinstance(path, list):
            continue
        previous = None
        for raw in path:
            point = coordinate(raw)
            if not point:
                previous = None
                continue
            if previous:
                hit = _segment_entry(previous, point, anchor, radius)
                if hit:
                    return hit
            elif geo._dist(point, anchor) <= radius:
                return point
            previous = point
    return None


def approach(origin, anchor, mode, invoke):
    """반경 내면 출발지를 그대로 쓴다. 밖이면 선택한 이동수단 경로를 1회 조회한다."""
    if not origin:
        return None, ""
    if geo._dist(origin, anchor) <= RADIUS_M:
        return origin, ""
    mode = mode or "walk"
    try:
        result = invoke("course", "get_directions", {"mode": mode,
            "points": [origin, {"lat": float(anchor["lat"]), "lng": float(anchor["lng"])}]})
    except (ProgressCancelled, ProgressStorageError):
        raise
    except Exception:
        result = {}
    legs = result.get("legs", []) if isinstance(result, dict) else []
    leg = legs[0] if len(legs) == 1 and isinstance(legs[0], dict) else {}
    entry = first_entry(leg.get("paths"), anchor) if leg.get("status") == "ok" and not leg.get("stale") else None
    if not entry:
        return None, f"출발지가 구장 반경 2.5km 밖이지만 {transport.LABEL[mode]} 경로를 확인하지 못해 진입점을 정하지 못했어요. 아래는 구장 주변의 임시 코스이며 출발 방향은 반영되지 않았어요. 이동수단을 바꾸거나 반경 안의 출발지를 지정하면 다시 연결할 수 있어요."
    direction = geo.bearing_label(anchor, entry)
    text = f"출발지에서 구장까지의 {transport.LABEL[mode]} 경로가 반경 2.5km 안으로 처음 들어오는 {direction}쪽 지점부터 코스를 골랐어요. 반경 밖에는 방문지를 추가하지 않았어요."
    if mode == "transit":
        text += " 진입점은 장소 검색 기준이며 하차 지점을 뜻하지 않아요. 실제 이동은 안내된 정류장·역을 이용해 주세요."
    return entry, text
