"""부분 수정의 경로 근접 조건은 장소 후기 대신 좌표로 판정한다."""
import math

from llm.v1.progress import ProgressCancelled, ProgressStorageError
from . import arrival, corridor

NEAR_ROUTE_M = 800


def prepare(preference, origin, anchor, mode, route_path, invoke):
    if not preference:
        return None
    if preference == "selected_route":
        paths = corridor.paths_of(route_path)
        if not paths:
            raise ValueError("기준으로 삼을 지도 경로가 없어요. 경로를 선택하거나 출발지에서 구장으로 가는 길을 기준으로 요청해 주세요.")
        basis = "actual" if route_path.get("source") == "directions" else "drawn"
    else:
        origin = arrival.coordinate(origin)
        if not origin:
            raise ValueError("출발지 위치가 없어 경로와의 거리를 계산하지 못했어요. 출발지를 입력하거나 지도에 찍어 주세요.")
        # 기존 식당을 경유하는 지도 선은 교체 기준에 섞지 않는다.
        try:
            result = invoke("course", "get_directions", {"mode": mode or "walk", "points": [origin, arrival.coordinate(anchor)]})
        except (ProgressCancelled, ProgressStorageError):
            raise
        except Exception:
            result = {}
        legs = result.get("legs", []) if isinstance(result, dict) else []
        leg = legs[0] if len(legs) == 1 and isinstance(legs[0], dict) else {}
        paths = leg.get("paths") if leg.get("status") == "ok" and not leg.get("stale") else None
        basis = "actual" if paths else "straight"
        paths = paths or [[origin, anchor]]
    # 잘못된 좌표나 분리된 경로 사이에 가상의 선분을 만들지 않는다.
    cleaned = []
    for path in paths:
        part = []
        for raw in path:
            if point := arrival.coordinate(raw):
                part.append(point)
            elif part:
                cleaned.append(part)
                part = []
        if part:
            cleaned.append(part)
    segments = corridor.clip(cleaned, anchor)
    if not segments:
        raise ValueError("기준 경로에서 구장 반경 2.5km 안의 구간을 찾지 못했어요. 출발지와 지도 경로를 확인해 주세요.")
    return {"segments": segments, "basis": basis, "preference": preference}


def search_areas(route):
    centers, gap = corridor.centers(route["segments"])
    return [(center, min(arrival.RADIUS_M, math.ceil(NEAR_ROUTE_M + gap))) for center in centers]


def rank(candidates, route):
    result = []
    for place in candidates:
        distance, position = corridor.distance_position(place, route["segments"])
        if distance <= NEAR_ROUTE_M:
            result.append({**place, "routeDistance": round(distance), "routePosition": round(position),
                           "routeBasis": route["basis"]})
    return sorted(result, key=lambda p: (p["routeDistance"], p["routePosition"]))


def notice(place, route):
    distance, _ = corridor.distance_position(place, route["segments"])
    basis = {"actual": "조회한 이동 경로", "drawn": "지도에 그린 경로", "straight": "출발지–구장 직선 구간"}[route["basis"]]
    text = f"{basis}에서 직선 약 {round(distance)}m인 후보를 골랐어요. 경로와 매장 좌표로 계산한 거리이며 실제 진입 도보 거리와는 달라요."
    if route["basis"] == "straight":
        text += " 실제 경로 조회에 실패해 직선 구간을 기준으로 비교했어요."
    return text
