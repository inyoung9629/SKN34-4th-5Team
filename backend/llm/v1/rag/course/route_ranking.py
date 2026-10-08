"""Bounded comparison of complete, condition-checked itineraries. No LLM calls."""
import math
import time
from functools import partial

from ...progress import ProgressCancelled, ProgressStorageError
from . import geo, parallel

MAX_VARIANTS = 4
BEAM_WIDTH = 12
MAX_LEGS = 18
TIME_BUDGET_S = 8


def identity(place):
    return str(place.get("placeId") or ""), place.get("name", ""), round(place["lat"], 6), round(place["lng"], 6)


def edge_key(a, b):
    return tuple(round(p[k], 6) for p in (a, b) for k in ("lat", "lng"))


def valid_leg(leg):
    return (isinstance(leg, dict) and leg.get("status") == "ok" and not leg.get("stale")
            and all(isinstance(leg.get(k), (int, float)) and not isinstance(leg[k], bool)
                    and math.isfinite(leg[k]) and leg[k] >= 0 for k in ("seconds", "distance"))
            and (leg["distance"] == 0 or bool(leg.get("paths"))))


def points_of(steps, origin, anchor):
    return [origin] + [s["place"] if s["place"] else anchor for s in steps]


def distance_key(steps, origin, anchor, relevance, *, unfinished=False):
    points = points_of(steps, origin, anchor)
    if unfinished and not any(s["phase"] == "GAME" for s in steps):
        points.append(anchor)  # Completion cost, never a reward for moving closer.
    distances = geo.leg_meters(points)
    # Equal-length routes prefer nearby earlier stops instead of clustering
    # everything by the stadium. Twenty-five metre buckets suppress float noise.
    return (round(sum(distances) / 25), tuple(round(d / 25) for d in distances),
            -sum(relevance(s["place"]) for s in steps if s["place"]))


def variants(steps, choices, origin, anchor, relevance):
    beam = [[]]
    for step, options in zip(steps, choices):
        expanded = []
        for prefix in beam:
            used = {identity(s["place"]) for s in prefix if s["place"]}
            names = {s["place"]["name"] for s in prefix if s["place"]}
            brands = {name.split()[0] for name in names}
            for place in options if step["place"] else [None]:
                if place and (identity(place) in used or place["name"] in names or place["name"].split()[0] in brands):
                    continue
                expanded.append(prefix + [{**step, "place": place}])
        beam = sorted(expanded, key=lambda rows: distance_key(rows, origin, anchor, relevance, unfinished=True))[:BEAM_WIDTH]
    # Always retain the original valid itinerary if pruning exhausted a later slot.
    beam.append(steps)
    # Retain single-stop alternatives too: beam pruning must not leave only
    # different parks while every route has the same meal and cafe.
    for i, options in enumerate(choices):
        for place in options:
            trial = [{**s, "place": place} if j == i else s for j, s in enumerate(steps)]
            brands = [s["place"]["name"].split()[0] for s in trial if s["place"]]
            if len(brands) == len(set(brands)):
                beam.append(trial)
    unique = {}
    for rows in sorted(beam, key=lambda rows: distance_key(rows, origin, anchor, relevance)):
        key = tuple(identity(s["place"]) if s["place"] else ("GAME",) for s in rows)
        unique.setdefault(key, rows)
    candidates = list(unique.values())
    selected = candidates[:1]
    for i, step in enumerate(steps):
        if not step["place"] or len(selected) >= MAX_VARIANTS:
            continue
        seen = {identity(rows[i]["place"]) for rows in selected}
        alternate = next((rows for rows in candidates if identity(rows[i]["place"]) not in seen), None)
        if alternate is not None:
            selected.append(alternate)
    for rows in candidates:
        if len(selected) >= MAX_VARIANTS:
            break
        if rows not in selected:
            selected.append(rows)
    return selected


def stadium_fallback(steps, choices, origin, anchor, relevance):
    """Only after time comparison fails, advance toward the stadium with valid candidates."""
    rows, used, prev = [], set(), origin
    for step, options in zip(steps, choices):
        if step["place"] is None:
            rows.append(step)
            prev = anchor
            continue
        available = [p for p in options if p["name"].split()[0] not in used]
        if not available:
            return steps
        if step["phase"] == "BEFORE":
            advancing = [p for p in available if geo.progress_m(p, prev, anchor) >= 0]
            available = advancing or available
        def score(p):
            advance = geo.progress_m(p, prev, anchor) / 800 if step["phase"] == "BEFORE" else 0
            return relevance(p) - geo._dist(prev, p) / 1000 + advance
        place = max(available, key=score)
        rows.append({**step, "place": place})
        used.add(place["name"].split()[0])
        prev = place
    return rows


class RouteRanker:
    def __init__(self, invoke, mode=None):
        self.invoke, self.mode = invoke, mode or "walk"
        self.cache = {}
        self.calls = 0
        self.deadline = None
        self.stopped = False
        self.failures = 0
        self.failed_edges = set()
        self.report = {}

    def _fetch_leg(self, a, b):
        # Workers only fetch. Deduplication, budgets and ranking stay on the caller.
        try:
            result = self.invoke("course", "get_directions", {"mode": self.mode,
                "points": [{"lat": p["lat"], "lng": p["lng"]} for p in (a, b)]})
            legs = result.get("legs", []) if isinstance(result, dict) else []
            leg = legs[0] if len(legs) == 1 else None
        except (ProgressCancelled, ProgressStorageError):
            raise
        except Exception:
            leg = None
        return leg if valid_leg(leg) else None

    def _legs(self, points):
        edges = {edge_key(a, b): (a, b) for a, b in zip(points, points[1:])}
        for key in edges:
            if key[:2] == key[2:]:
                self.cache[key] = {"status": "ok", "distance": 0, "seconds": 0, "paths": []}
        while True:
            failed = next((key for key in edges if key in self.cache and self.cache[key] is None), None)
            if failed is not None:
                # A speculative later edge can fail too. Count only the first
                # blocking edge, as serial traversal did; otherwise two errors
                # in one unusable route could suppress a valid alternative.
                if failed not in self.failed_edges:
                    self.failed_edges.add(failed)
                    self.failures += 1
                self.stopped = self.failures >= 2
                break
            if all(key in self.cache for key in edges):
                break
            if self.stopped or self.calls >= MAX_LEGS or time.monotonic() >= self.deadline:
                self.stopped = True
                break
            limit = min(parallel.WORKERS, MAX_LEGS - self.calls, 2 - self.failures)
            batch = [(key, pair) for key, pair in edges.items() if key not in self.cache][:limit]
            self.calls += len(batch)
            results = parallel.reads(partial(self._fetch_leg, *pair) for _, pair in batch)
            for (key, _), leg in zip(batch, results):
                self.cache[key] = leg
        return self.known_legs(points)

    def optimize(self, steps, choices, origin, anchor, relevance):
        routes = variants(steps, choices, origin, anchor, relevance)
        if len(routes) < 2:
            return routes[0] if routes else steps
        self.deadline = time.monotonic() + TIME_BUDGET_S
        measured = []
        for rows in routes:
            points = points_of(rows, origin, anchor)
            legs = self._legs(points)
            if all(leg is not None for leg in legs):
                seconds = sum(leg["seconds"] for leg in legs)
                meters = sum(leg["distance"] for leg in legs)
                measured.append(((seconds, meters, tuple(leg["seconds"] for leg in legs)), rows))
            if self.stopped:
                break
        # Never compare a cheap straight-line estimate against actual road time.
        chosen = min(measured, key=lambda item: item[0])[1] if measured else stadium_fallback(steps, choices, origin, anchor, relevance)
        self.report = {"variants": len(routes), "measured": len(measured), "calls": self.calls,
                       "limited": len(measured) < len(routes),
                       "strategy": "duration" if measured else "stadium_fallback",
                       "seconds": min(measured, key=lambda item: item[0])[0][0] if measured else None}
        return chosen

    def known_legs(self, points):
        return [self.cache.get(edge_key(a, b)) for a, b in zip(points, points[1:])]

    def notice(self):
        if not self.report:
            return ""
        if not self.report["measured"]:
            return "후보들의 이동시간을 확인하지 못해 구장 방향의 후보를 우선 확인해 코스를 이었어요. 표시된 이동시간에는 추정값이 포함될 수 있어요."
        if self.report["limited"]:
            return "일부 후보의 경로 조회가 완료되지 않아, 이동시간이 확인된 후보로 코스를 이었어요."
        return "조건에 맞는 후보 코스들의 출발지부터 마지막 장소까지 이동시간을 비교해 골랐어요."
