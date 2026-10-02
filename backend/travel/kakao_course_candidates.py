"""Request-local, adaptive Kakao retrieval BEFORE course filtering/ranking.

Completeness means API-reported results for our categories/queries, not every real
business or every possible keyword. No persistent provider dump and no web/LLM.
"""
from dataclasses import dataclass
import math
import re
import time

from .collected_places import distance
from .place_service import (PlaceConfigurationError, PlaceError, PlaceRateLimitError,
                            search_candidate_cell)
from .stadium_scope import classify_stadium_point, outside_stadium, reviewed_frames, reviewed_zones


# Broad restaurant/category queries must not be replaced with the requested menu.
# Walk/indoor have no exhaustive Kakao category: their explicit query scope is audited.
SEARCHES = {
    "food": (("FD6", ""),),
    "cafe": (("CE7", ""), ("FD6", "제과점"), ("FD6", "디저트")),
    "walk": (("", "공원"), ("", "산책로"), ("", "둘레길"), ("AT4", "")),
    "sight": (("AT4", ""),),
    "indoor": (("CT1", ""), ("", "볼링장"), ("", "보드게임카페"), ("", "방탈출"), ("", "영화관")),
    "store": (("CS2", ""),),
    "stay": (("AD5", ""),),  # Retrieval capability only; planner still rejects unsupported stays.
}
WALK = re.compile(r"도보여행|도시근린공원|도시공원|산책로|둘레길|수변공원|어린이공원|체육공원|유원지|(?:^| > )공원(?:$| > )")
INDOOR = re.compile(r"보드게임|방탈출|볼링|영화관|박물관|미술관|전시관|실내.*(?:스포츠|놀이터)|오락실")
TENANT = re.compile(r"야구장|스카이돔|랜더스필드|위즈파크|볼파크|라이온즈파크|챔피언스필드|NC파크|NC구장", re.I)
CUISINES = ("한식", "중식", "일식", "양식", "분식", "치킨")


@dataclass(frozen=True)
class CandidatePolicy:
    max_calls: int = 120
    wall_seconds: float = 45
    min_cell_m: float = 10
    max_depth: int = 12
    interval_seconds: float = .25


class CandidateSearchError(Exception):
    def __init__(self, reason, *, unavailable=False):
        self.reason = reason
        self.status = "candidate_search_unavailable" if unavailable else "candidate_search_incomplete"
        super().__init__(reason)


def circle_box(center, radius):
    angle = (radius + .01) / 6371000  # conservative 1 cm boundary pad
    dy = math.degrees(angle)
    dx = math.degrees(math.asin(math.sin(angle) / math.cos(math.radians(center["lat"]))))
    return (center["lng"] - dx, center["lat"] - dy, center["lng"] + dx, center["lat"] + dy)


def in_box(document, rect):
    return rect[0] - 1e-9 <= float(document["x"]) <= rect[2] + 1e-9 and rect[1] - 1e-9 <= float(document["y"]) <= rect[3] + 1e-9


def subtract_box(rect, covered):
    """At most four uncovered strips; shared edges are queried then ID-deduped."""
    w, s, e, n = rect
    a, b, c, d = max(w, covered[0]), max(s, covered[1]), min(e, covered[2]), min(n, covered[3])
    if a >= c or b >= d:
        return [rect]
    return [r for r in ((w, s, a, n), (c, s, e, n), (a, s, c, b), (a, d, c, n))
            if r[0] < r[2] and r[1] < r[3]]


def kind_of(doc):
    group, detail = doc["category_group_code"], doc["category_name"]
    text = detail + " " + doc["place_name"]
    if group in {"HP8", "PM9"}:
        return None
    if INDOOR.search(text) or group == "CT1":
        return "indoor"
    if group == "CS2":
        return "store"
    if group == "AD5":
        return "stay"
    if group == "CE7" or re.search(r"카페|제과|디저트", detail):
        return "cafe"
    if group == "FD6":
        return "food"
    if WALK.search(detail):
        return "walk"
    if group == "AT4":
        return "sight"
    return None


def normalize(doc, kind):
    detail = doc["category_name"]
    return {"placeId": doc["id"], "name": doc["place_name"], "lat": float(doc["y"]), "lng": float(doc["x"]),
            "kind": kind, "category": detail, "detail": detail,
            "cuisine": next((c for c in CUISINES if c in [p.strip() for p in detail.split(">")]), "기타"),
            "address": doc["road_address_name"] or doc["address_name"], "source": "KAKAO",
            "sourceUrl": f"https://place.map.kakao.com/{doc['id']}"}


class KakaoCourseCandidates:
    def __init__(self, stadium_code, *, policy=None, fetch=search_candidate_cell,
                 clock=time.monotonic, sleep=time.sleep, frames=None, zones=None):
        self.policy = policy or CandidatePolicy()
        self.fetch, self.clock, self.sleep = fetch, clock, sleep
        self.frames = reviewed_frames(stadium_code) if frames is None else frames
        self.zones = reviewed_zones() if zones is None else zones
        self.calls, self.elapsed, self.last_call = 0, 0., None
        self.completed, self.seen, self.eligible, self.excluded = [], set(), set(), set()
        self.queries, self.splits, self.cache_hits = [], 0, 0
        self.complex_excluded, self.internal_excluded = set(), set()
        self.active_started = None

    def audit(self):
        return {"source": "kakao_adaptive", "network_used": self.calls > 0, "calls": self.calls,
                "max_calls": self.policy.max_calls, "wall_seconds": self.policy.wall_seconds,
                "elapsed_seconds": round(self.elapsed, 3), "split_count": self.splits,
                "cache_hits": self.cache_hits, "retrieved_unique_count": len(self.seen),
                "candidate_count": len(self.eligible), "excluded_affiliation_count": len(self.excluded),
                "excluded_complex_count": len(self.complex_excluded),
                "excluded_internal_count": len(self.internal_excluded),
                "status": self.queries[-1]["status"] if self.queries else "not_started",
                "coverage_scope": "api_reported_results_for_declared_queries_not_all_businesses",
                "persistent_cache": False, "research_used_as_fact": False, "queries": self.queries[-160:]}

    def remaining(self):
        active = self.clock() - self.active_started if self.active_started is not None else 0
        return self.policy.wall_seconds - self.elapsed - active

    def page(self, rect, spec, page):
        if self.calls >= self.policy.max_calls:
            raise CandidateSearchError("call_budget")
        delay = max(0., self.policy.interval_seconds - (self.clock() - self.last_call)) if self.last_call is not None else 0
        if self.remaining() <= delay:
            raise CandidateSearchError("time_budget")
        if delay:
            self.sleep(delay)
        remaining = self.remaining()
        if remaining <= 0:
            raise CandidateSearchError("time_budget")
        self.calls += 1
        self.last_call = self.clock()
        try:
            result = self.fetch(rect=rect, category=spec[0], keyword=spec[1], page=page, timeout=min(8., remaining))
        except PlaceRateLimitError as exc:
            raise CandidateSearchError("rate_limit") from exc
        except PlaceConfigurationError as exc:
            raise CandidateSearchError("configuration", unavailable=True) from exc
        except PlaceError as exc:
            if self.remaining() <= 0:
                raise CandidateSearchError("time_budget") from exc
            raise CandidateSearchError("upstream_or_invalid_metadata", unavailable=True) from exc
        if self.remaining() <= 0:
            raise CandidateSearchError("time_budget")
        self.seen.update(d["id"] for d in result["places"])
        return result

    def cell(self, rect, spec, depth=0):
        first = self.page(rect, spec, 1)
        count, pageable = first["total_count"], first["pageable_count"]
        if count > pageable:
            w, s, e, n = rect
            width = distance((s + n) / 2, w, (s + n) / 2, e)
            height = distance(s, (w + e) / 2, n, (w + e) / 2)
            if depth >= self.policy.max_depth or max(width, height) <= self.policy.min_cell_m:
                raise CandidateSearchError("dense_cell_unresolved")
            self.splits += 1
            x, y = (w + e) / 2, (s + n) / 2
            # Tiny seam overlap avoids floating point serialization gaps.
            eps = min(1e-9, (e - w) / 4, (n - s) / 4)
            boxes = ((w, s, x + eps, y + eps), (x - eps, s, e, y + eps),
                     (w, y - eps, x + eps, n), (x - eps, y - eps, e, n))
            found = {}
            for box in boxes:
                found.update((d["id"], d) for d in self.cell(box, spec, depth + 1))
            if len(found) != count or not {d["id"] for d in first["places"]} <= found.keys():
                raise CandidateSearchError("counts_changed_during_split")
        else:
            found = {}
            pages = max(1, math.ceil(count / 15))
            for number in range(1, pages + 1):
                response = first if number == 1 else self.page(rect, spec, number)
                expected = min(15, max(0, count - (number - 1) * 15))
                if (response["total_count"] != count or response["pageable_count"] != pageable
                        or response["is_end"] != (number == pages) or len(response["places"]) != expected
                        or any(d["id"] in found for d in response["places"])):
                    raise CandidateSearchError("inconsistent_pages")
                found.update((d["id"], d) for d in response["places"])
            if len(found) != count:
                raise CandidateSearchError("inconsistent_pages")
        documents = list(found.values())
        self.completed.append((spec, rect, documents))
        return documents

    def collect(self, rect, spec):
        remaining, found = [rect], {}
        # Parent rectangles precede their children so a complete previous radius
        # can satisfy or trim the next lookup without re-fetching its interior.
        cached = sorted((entry for entry in self.completed if entry[0] == spec),
                        key=lambda e: (e[1][2] - e[1][0]) * (e[1][3] - e[1][1]), reverse=True)
        for _, covered, docs in cached:
            next_remaining = [part for r in remaining for part in subtract_box(r, covered)]
            if next_remaining != remaining:
                self.cache_hits += 1
                found.update((d["id"], d) for d in docs if in_box(d, rect))
            remaining = next_remaining
            if not remaining:
                break
        # Sparse/empty inner areas do not justify several strip requests per
        # keyword at every radius. Query the expanded rectangle once instead;
        # cell() still pages/splits it and validates completeness. Never merge
        # stale cached rows into this fresh full-area response.
        if len(remaining) > 1 and len(found) <= 15:
            return self.cell(rect, spec)
        for box in remaining:
            found.update((d["id"], d) for d in self.cell(box, spec))
        return found.values()

    def search(self, center, radius, kind, *, origin_label=None):
        if (type(radius) not in (int, float) or not 0 < radius <= 20000
                or not 33 <= center["lat"] <= 39 or not 124 <= center["lng"] <= 132):
            raise CandidateSearchError("unsupported_search_area")
        specs = (("", origin_label),) if origin_label else SEARCHES.get(kind)
        if not specs:
            raise CandidateSearchError("unsupported_kind")
        entry = {"kind": kind, "center": {k: center[k] for k in ("lat", "lng")}, "radius_m": radius,
                 "queries": [{"category": c, "keyword": q} for c, q in specs],
                 "status": "in_progress", "candidate_count": 0, "calls": 0}
        self.queries.append(entry)
        self.active_started, started_calls = self.clock(), self.calls
        try:
            rect, documents = circle_box(center, radius), {}
            for spec in specs:
                documents.update((d["id"], d) for d in self.collect(rect, spec))
            places = []
            for doc in documents.values():
                actual = kind_of(doc)
                if not origin_label and actual != kind:
                    continue
                place = normalize(doc, "origin" if origin_label else actual)
                if distance(center["lat"], center["lng"], place["lat"], place["lng"]) > radius:
                    continue
                area = classify_stadium_point(place, self.zones)
                if area["scope"] == "excluded_complex":
                    self.complex_excluded.add(place["placeId"])
                    self.excluded.add(place["placeId"])
                    continue
                if area["scope"] == "internal":
                    place["stadiumAffiliation"] = {**area, "basis": "reviewed_main_frame"}
                    if not origin_label:
                        self.internal_excluded.add(place["placeId"])
                        self.excluded.add(place["placeId"])
                        continue
                tenant = TENANT.search(place["name"]) and not re.search(r"역|사거리|앞점|입구점", place["name"])
                if not origin_label and (tenant or not outside_stadium(place, self.frames)):
                    self.excluded.add(place["placeId"])
                    continue
                places.append(place)
            self.eligible.update(p["placeId"] for p in places if not origin_label)
            entry.update(status="complete", candidate_count=len(places))
            return places
        except CandidateSearchError as exc:
            entry.update(status=exc.status, reason=exc.reason)
            raise
        finally:
            entry["calls"] = self.calls - started_calls
            self.elapsed += self.clock() - self.active_started
            self.active_started = None
