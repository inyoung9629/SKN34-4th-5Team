"""선택한 경로 주변 검색. 검색 폭을 넓혀도 구장 반경은 2.5km로 고정한다."""
import math

from . import arrival, geo

WIDTHS = (300, 800, 1500, 5000)


def paths_of(value):
    if not isinstance(value, dict) or not isinstance(value.get("points"), list):
        return []
    points = [arrival.coordinate(p) for p in value["points"]]
    if not 2 <= len(points) <= 128 or not all(points):
        return []
    breaks = set(value.get("breaks") or [])
    paths, current = [], []
    for i, point in enumerate(points):
        if i in breaks and current:
            paths.append(current)
            current = []
        current.append(point)
    return paths + [current]


def clip(paths, anchor):
    """반경 밖 선분과 경로의 빈 구간을 연결하지 않고 안쪽 선분만 남긴다."""
    segments = []
    for path in paths:
        for a, b in zip(path, path[1:]):
            start = a if geo._dist(a, anchor) <= arrival.RADIUS_M else arrival._segment_entry(a, b, anchor, arrival.RADIUS_M)
            end = b if geo._dist(b, anchor) <= arrival.RADIUS_M else arrival._segment_entry(b, a, anchor, arrival.RADIUS_M)
            if start and end and geo._dist(start, end) > 0.01:
                segments.append((start, end))
    return segments


def distance_position(point, segments):
    """경로까지의 최단 직선거리와 경로 진행 거리(m)."""
    best, position, walked = float("inf"), 0, 0
    scale = 111195 * math.cos(math.radians(point["lat"]))
    for a, b in segments:
        dx, dy = (b["lng"] - a["lng"]) * scale, (b["lat"] - a["lat"]) * 111195
        px, py = (point["lng"] - a["lng"]) * scale, (point["lat"] - a["lat"]) * 111195
        length = math.hypot(dx, dy)
        t = max(0, min(1, (px * dx + py * dy) / (length * length))) if length else 0
        distance = math.hypot(px - t * dx, py - t * dy)
        if distance < best:
            best, position = distance, walked + t * length
        walked += length
    return best, position


def centers(segments):
    lengths = [geo._dist(a, b) for a, b in segments]
    total = sum(lengths)
    count = min(7, max(2, math.ceil(total / 600) + 1))
    result = []
    for i in range(count):
        remaining = total * i / (count - 1)
        for (a, b), length in zip(segments, lengths):
            if remaining <= length + .001:
                t = min(1, remaining / length) if length else 0
                result.append({"lat": a["lat"] + t * (b["lat"] - a["lat"]), "lng": a["lng"] + t * (b["lng"] - a["lng"])})
                break
            remaining -= length
    return result, total / max(1, count - 1) / 2


def choose(segments, pool, search, allowed, score, minimum_position=0):
    """같은 조건을 유지하며 폭만 확대. 앞선 방문지보다 뒤쪽 경로를 우선한다."""
    search_centers, gap = centers(segments)
    found = list(pool)
    for width in WIDTHS:
        # 시작점 한 곳이 아니라 선택한 선 전체를 따라 검색한다.
        for center in search_centers:
            found.extend(search(center, min(6000, math.ceil(width + gap))))
        candidates = []
        for place in found:
            if not allowed(place):
                continue
            distance, position = distance_position(place, segments)
            if distance <= width:
                candidates.append((place, distance, position))
        if candidates:
            forward = [item for item in candidates if item[2] + 100 >= minimum_position]
            place, distance, position = max(forward or candidates, key=lambda item: (
                score(item[0]) - item[1] / 500 - abs(item[2] - minimum_position) / 3000))
            if not place.get("placeUrl"):
                match = next((p for p in found if p.get("placeUrl") and p.get("placeId") and p.get("placeId") == place.get("placeId")), None)
                if match:
                    place = {**place, "placeUrl": match["placeUrl"]}
            return {**place, "routeDistance": round(distance), "routePosition": position}
    return None


def notice(places):
    far = [p for p in places if p.get("routeDistance", 0) > 800]
    text = "선택한 경로 가까이부터 찾고, 추천 장소는 모두 구장 반경 2.5km 안으로 제한했어요."
    if far:
        names = " · ".join(f"{p['name']}(경로에서 직선 약 {p['routeDistance'] / 1000:.1f}km)" for p in far)
        text += f" 경로 가까이에 조건에 맞는 장소가 부족해 {names}까지 범위를 넓혔어요. 선택한 경로에서 다소 멀어졌으며 실제 이동 거리는 더 길 수 있어요."
    return text
