"""Bounded, deterministic course search with injected candidate/direction providers.

Whole-route feasibility is checked before returning any stops. A failed later
visit backtracks to earlier choices; hard constraints are never silently relaxed.
This is bounded search, not a claim of globally optimal routing.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import time

from travel.collected_places import distance
from travel.kakao_course_candidates import CandidateSearchError
from .itinerary_request import ItineraryRequest, StopRequest, KST
from .internal_visits import choose_internal
from .review_verification import evaluate_reviews
from .local_knowledge import missing_required_evidence


@dataclass(frozen=True)
class PlanningPolicy:
    radii: tuple[int, ...] = (100, 300, 500, 800, 1000, 1500)
    arrival_margin_minutes: int = 20
    leg_buffer_minutes: int = 5
    internal_transfer_minutes: int = 10
    estimated_game_minutes: int = 180
    max_nodes: int = 160
    max_direction_calls: int = 24
    wall_seconds: float = 25


class SearchLimit(Exception):
    pass


class RoutingUnavailable(Exception):
    pass


def point(place):
    return {key: float(place[key]) for key in ("lat", "lng")}


def meters(a, b):
    return distance(a["lat"], a["lng"], b["lat"], b["lng"])


def text_of(place):
    return " ".join(str(place.get(key) or "") for key in
                    ("name", "category", "subcategory", "detail", "address", "cuisine")).casefold()


def matches(place, stop):
    text = text_of(place)
    return (place["kind"] == stop.kind
            and (stop.food is not None or not stop.cuisine or place.get("cuisine") == stop.cuisine)
            and all(term.casefold() in text for term in stop.required_keywords)
            and not any(term.casefold() in text for term in stop.excluded_keywords))


def preferred(place, stop):
    text = text_of(place)
    # Catalogue labels only prioritize paid lookups; they do not prove a menu claim.
    food_hint = sum(term.name.casefold() in text for term in stop.food.conditions().values()
                    if not term.exclude) if stop.food else 0
    return sum(term.casefold() in text for term in stop.preferred_keywords) + food_hint


def evidence_quality(food, reviews):
    """Deterministic tie-break, never a substitute for hard-condition checks.

    Count independently checked evidence, capped so many links cannot dominate.
    Conditional review traits are scored only during final time validation.
    """
    menu = min(2, len({s['url'] for s in (food or {}).get('sources', [])})) if (food or {}).get('status') == 'pass' else 0
    general = [o for o in (reviews or {}).get('observations', []) if o['context'] == 'general']
    from .review_verification import independent_groups
    return menu + min(2, len(independent_groups(general)))


def radius_steps(cap, policy):
    return tuple(sorted({r for r in policy.radii if r <= cap} | {cap}))


class ItineraryPlanner:
    def __init__(self, places, stadium, coverage, directions, *, internal_records=(), gate=None,
                 policy=None, clock=time.monotonic, food_verifier=None, review_verifier=None,
                 local_evidence_only=False, candidate_source=None, pinned_stops=None, excluded_place_ids=()):
        self.places = places
        self.stadium = stadium
        # Intersection of the original collection circle and the reviewed circle.
        self.coverage = coverage
        self.directions = directions
        self.policy = policy or PlanningPolicy()
        self.clock = clock
        self.internal_records = internal_records
        self.gate = gate
        self.food_verifier = food_verifier
        self.review_verifier = review_verifier
        self.local_evidence_only = local_evidence_only
        self.candidate_source = candidate_source
        self.pinned_stops = pinned_stops or {}
        self.excluded_place_ids = set(excluded_place_ids)

    def plan(self, request: ItineraryRequest, game_start: datetime, *, origin=None, now=None, historical=False,
             reuse_routes=False):
        self.request = request
        self.mode = request.mode or "walk"
        self.cap = request.max_leg_m or self.policy.radii[-1]
        previous_cache = self.cache if reuse_routes else {}
        previous_calls = self.calls if reuse_routes else 0
        previous_start = self.started if reuse_routes else self.clock()
        self.trace, self.cache, self.reasons = [], previous_cache, set()
        self.nodes, self.calls = 0, previous_calls
        self.started = previous_start
        self.has_food_requirements = any(s.food and s.phase != "inside" for s in request.stops)
        self.has_review_requirements = any(s.reviews and s.phase != "inside" for s in request.stops)
        self.wall_seconds = self.policy.wall_seconds + (
            self.food_verifier.policy.wall_seconds if self.has_food_requirements and self.food_verifier else 0) + (
            self.review_verifier.policy.wall_seconds if self.has_review_requirements and self.review_verifier else 0) + (
            self.candidate_source.policy.wall_seconds if self.candidate_source else 0)
        self.origin = origin
        self.game_start = game_start.astimezone(KST)
        self.now = (now or datetime.now(KST)).astimezone(KST)
        self.historical = historical
        self.deadline = request.arrival_at or self.game_start - timedelta(minutes=self.policy.arrival_margin_minutes)
        self.before = [s for s in request.stops if s.phase == "before"]
        self.inside = [s for s in request.stops if s.phase == "inside"]
        self.default_internal = request.arrive_at_open and not self.inside
        if self.default_internal:
            self.inside = [StopRequest(kind="food", phase="inside")]
        self.after = [s for s in request.stops if s.phase == "after"]
        self.after_start = request.after_start_at or self.game_start + timedelta(minutes=self.policy.estimated_game_minutes)
        if request.clarification:
            return self.result("clarification", message=request.clarification)
        if request.unverified_requirements or any(s.unverified_requirements for s in request.stops):
            return self.result("constraints_unverified")
        if any(s.kind == "stay" for s in request.stops):
            return self.result("unsupported_lodging")
        self.internal_chosen = []
        if self.inside:
            if not self.gate or not self.gate.get("opens_at"):
                return self.result("gate_unavailable", gate=self.gate)
            self.opens_at = datetime.fromisoformat(self.gate["opens_at"]).astimezone(KST)
            self.internal_chosen, problem = choose_internal(self.inside, self.internal_records)
            if problem:
                return self.result(problem)
            self.internal_seconds = sum((s.duration + self.policy.internal_transfer_minutes) * 60 for s in self.inside)
            # When the last pre-game stop is inside, kickoff itself is the deadline.
            # No 20-minute deduction and no extra inside→stadium road leg.
            latest_entry = self.game_start - timedelta(seconds=self.internal_seconds)
            self.deadline = min(request.arrival_at or latest_entry, latest_entry)
            if request.arrive_at_open:
                self.deadline = min(self.deadline, self.opens_at)
            if self.opens_at > latest_entry:
                return self.result("gate_window_too_short")
        if (self.deadline > self.game_start or self.deadline.date() != self.game_start.date()
                or (request.arrival_at and request.arrival_at > self.game_start)
                or (self.after and self.after_start < self.game_start)
                or (self.after and self.after_start.date() > (self.game_start + timedelta(days=1)).date())
                or (request.start_at and request.start_at.date() != self.game_start.date())
                or (request.finish_by and not self.game_start.date() <= request.finish_by.date()
                    <= (self.game_start + timedelta(days=1)).date())):
            return self.result("clarification", message="출발·구장 도착·종료 날짜와 시각을 확인해 주세요.")
        if request.start_at and not historical and request.start_at < self.now:
            return self.result("time_infeasible")
        if self.local_evidence_only and (missing := missing_required_evidence(request)):
            # Data insufficiency is not no_match and must not trigger radius
            # expansion, route calls or an automatic paid verifier fallback.
            return self.result("knowledge_insufficient", missing_evidence=missing)
        try:
            found = self.visit(0, origin or self.stadium, [], [], set())
        except CandidateSearchError as exc:
            self.reasons.add(exc.reason)
            return self.result(exc.status)
        except SearchLimit:
            return self.result("search_limited")
        except RoutingUnavailable:
            return self.result("directions_unavailable")
        if found:
            return found
        status = next((name for name in ("food_search_limited", "food_search_unavailable", "food_unverified",
                                        "review_search_limited", "review_search_unavailable", "review_unverified", "review_mismatch",
                                        "coverage_incomplete", "time_infeasible", "distance_limit")
                       if name in self.reasons), "no_match")
        return self.result(status)

    def check_budget(self):
        if self.nodes >= self.policy.max_nodes or self.clock() - self.started >= self.wall_seconds:
            raise SearchLimit

    def leg(self, start, end):
        self.check_budget()
        key = (*point(start).values(), *point(end).values(), self.mode)
        if key not in self.cache:
            if self.calls >= self.policy.max_direction_calls:
                raise SearchLimit
            self.calls += 1
            value = self.directions(self.mode, [point(start), point(end)])
            legs = value.get("legs") or []
            if (len(legs) != 1 or legs[0].get("status") != "ok" or legs[0].get("stale")
                    or any(type(value.get(k)) not in (int, float) or not math.isfinite(value[k])
                           or value[k] < 0 for k in ("seconds", "distance"))):
                raise RoutingUnavailable
            self.cache[key] = {"from": start.get("placeId", "origin"), "to": end.get("placeId", "stadium"),
                               "seconds": math.ceil(value["seconds"]), "distance_m": math.ceil(value["distance"]),
                               "buffer_seconds": self.policy.leg_buffer_minutes * 60}
        leg = self.cache[key]
        if self.request.max_leg_m and leg["distance_m"] > self.request.max_leg_m:
            self.reasons.add("distance_limit")
            return None
        return leg

    def circle_covered(self, center, radius):
        # Unknown coverage is not evidence of a complete search.
        return bool(self.coverage) and all(meters(center, c) + radius <= c["radius_m"] for c in self.coverage)

    def visit(self, index, center, chosen, legs, used):
        self.check_budget()
        if self.request.start_at and (self.before or self.inside):
            # Safe lower bound: all requested stays and already confirmed travel.
            # Do not spend route calls when the remaining zero-travel schedule fails.
            minimum = sum(s.duration * 60 for s in self.before) + sum(
                leg["seconds"] + leg["buffer_seconds"] for leg in legs)
            if self.request.start_at + timedelta(seconds=minimum) > self.deadline:
                self.reasons.add("time_infeasible")
                return None
        if index == len(self.before):
            return self.finish_before(center, chosen, legs, used)
        return self.expand(self.before[index], index, center, chosen, legs, used, after=False)

    def expand(self, stop, index, center, chosen, legs, used, *, after):
        examined = set()
        original_index = next(i for i, s in enumerate(self.request.stops) if s is stop)
        pinned = self.pinned_stops.get(original_index)
        for radius in radius_steps(self.cap, self.policy):
            self.check_budget()
            pool = self.places
            if pinned:
                pool = [pinned]
            elif self.candidate_source:
                try:
                    # Finish retrieving this radius BEFORE keyword filtering,
                    # verification shortlists or route calls. No top-45 slice.
                    pool = self.candidate_source.search(center, radius, stop.kind)
                except CandidateSearchError as exc:
                    self.trace.append({"phase": stop.phase, "index": index, "center": point(center),
                                       "radius_m": radius, "matching_count": None,
                                       "coverage_complete": False, "reason": exc.reason})
                    raise
            complete = True if pinned or self.candidate_source else self.circle_covered(center, radius)
            if not complete:
                self.reasons.add("coverage_incomplete")
            candidates = [p for p in pool if p["placeId"] not in used | examined | self.excluded_place_ids
                          and matches(p, stop) and meters(center, p) <= radius]
            candidates.sort(key=lambda p: (-preferred(p, stop), meters(center, p), meters(p, self.stadium), p["placeId"]))
            self.trace.append({"phase": stop.phase, "index": index, "center": point(center), "radius_m": radius,
                               "pool_count": len(pool), "matching_count": len(candidates), "coverage_complete": complete})
            # Distance is a pre-ranking only. Compare provider travel time within each ring.
            # Small batches avoid spending every API call on one crowded ring.
            # Rank actual travel times within the batch; no global optimum claim.
            # Try a verified candidate's complete route before paying to verify another.
            # A small, same-ring shortlist for soft preferences. Conditional reviews
            # are not pre-ranking proof; assess them at actual visit times below.
            batch_size = 2 if stop.reviews and stop.reviews.has_preferences else 1 if stop.food or stop.reviews else 4
            for offset in range(0, len(candidates), batch_size):
                ranked = []
                for candidate in candidates[offset:offset + batch_size]:
                    self.check_budget()
                    examined.add(candidate["placeId"])
                    self.nodes += 1
                    food_report = None
                    if stop.food:
                        food_report = (self.food_verifier.verify(candidate, stop.food) if self.food_verifier else
                                       {"status": "unknown", "reason": "food_search_unavailable"})
                        self.check_budget()
                        if food_report["status"] != "pass":
                            if food_report["status"] == "unknown":
                                reason = food_report["reason"]
                                self.reasons.add(reason if reason in ("food_search_limited", "food_search_unavailable") else "food_unverified")
                            continue
                    review_report = None
                    review_score = 0
                    if stop.reviews:
                        review_report = (self.review_verifier.verify(candidate, stop.reviews) if self.review_verifier else
                                         {"reason": "review_search_unavailable", "identity_verified": False,
                                          "sources": [], "observations": []})
                        self.check_budget()
                        if review_report["reason"] == "internal_restaurant":
                            continue
                        if not review_report.get("identity_verified") and any(c.priority == "required" for c in stop.reviews.all_of):
                            self.review_failure(review_report)
                            continue
                        review_score = evaluate_reviews(review_report, stop.reviews)["preference_score"]
                    # Without an origin the first place is the start, not a stadium→place trip.
                    travel = None if not after and index == 0 and self.origin is None else self.leg(center, candidate)
                    if travel is None and (after or index > 0 or self.origin is not None):
                        continue
                    # Last pre-game visit: compare the REAL remaining trip to
                    # the stadium too. Cache reuses it in full-route validation.
                    onward = None
                    if not after and index == len(self.before) - 1:
                        if meters(candidate, self.stadium) > self.cap:
                            self.reasons.add('distance_limit')
                            continue
                        onward = self.leg(candidate, self.stadium)
                        if onward is None:
                            continue
                    seconds = sum(l['seconds'] for l in (travel, onward) if l)
                    ranked.append((candidate, travel, food_report, review_report, review_score, seconds))
                ranked.sort(key=lambda pair: (-pair[4], -preferred(pair[0], stop),
                                              -evidence_quality(pair[2], pair[3]), pair[5],
                                              meters(pair[0], self.stadium), pair[0]["placeId"]))
                for candidate, travel, food_report, review_report, review_score, _seconds in ranked:
                    next_legs = legs + ([travel] if travel else [])
                    if self.over_total_distance(next_legs):
                        continue
                    next_chosen = chosen + [{"place": candidate, "request": stop, "radius_m": radius,
                                            "food_verification": food_report, "review_verification": review_report}]
                    next_used = used | {candidate["placeId"]}
                    if after:
                        result = self.visit_after(index + 1, candidate, next_chosen, next_legs, next_used)
                    else:
                        result = self.visit(index + 1, candidate, next_chosen, next_legs, next_used)
                    if result:
                        return result
            # A near candidate with an impossible suffix is not a usable candidate.
            # Try other near candidates first, then the next ring, then backtrack.
        return None

    def review_failure(self, report):
        reason = report.get("reason")
        self.reasons.add(reason if reason in ("review_search_limited", "review_search_unavailable", "review_mismatch")
                         else "review_unverified")

    def over_total_distance(self, legs):
        if self.request.max_total_m and sum(l["distance_m"] for l in legs) > self.request.max_total_m:
            self.reasons.add("distance_limit")
            return True
        return False

    def finish_before(self, center, chosen, legs, used):
        if chosen or self.origin:
            if meters(center, self.stadium) > self.cap:
                self.reasons.add("distance_limit")
                return None
            travel = self.leg(center, self.stadium)
            if travel is None:
                return None
            legs = legs + [travel]
        if self.over_total_distance(legs):
            return None
        duration = sum(s.duration * 60 for s in self.before) + sum(l["seconds"] + l["buffer_seconds"] for l in legs)
        current_time = self.now + timedelta(seconds=max(0, self.clock() - self.started))
        explicit_start = (current_time if self.request.start_now else self.request.start_at) if self.before or self.inside or self.origin else None
        start = explicit_start or self.deadline - timedelta(seconds=duration)
        arrival = start + timedelta(seconds=duration)
        if (arrival > self.deadline or (not self.historical and start < current_time)
                or start.date() != self.game_start.date()
                or (self.request.finish_by and arrival > self.request.finish_by)):
            self.reasons.add("time_infeasible")
            return None
        self.before_timing = (start, arrival, len(legs))
        self.internal_start = max(arrival, self.opens_at) if self.inside else arrival
        self.internal_end = self.internal_start + timedelta(seconds=self.internal_seconds) if self.inside else arrival
        if self.internal_end > self.game_start:
            self.reasons.add("time_infeasible")
            return None
        return self.visit_after(0, self.stadium, chosen, legs, used)

    def visit_after(self, index, center, chosen, legs, used):
        self.check_budget()
        start, arrival, before_legs_count = self.before_timing
        after_legs = legs[before_legs_count:]
        after_start = max(self.after_start, self.request.start_at or self.after_start) if not self.before and not self.inside and not self.origin else self.after_start
        finish = (after_start + timedelta(seconds=sum(s.duration * 60 for s in self.after)
                  + sum(l["seconds"] + l["buffer_seconds"] for l in after_legs))) if self.after else self.internal_end
        if self.request.finish_by and finish > self.request.finish_by:
            self.reasons.add("time_infeasible")
            return None
        if index < len(self.after):
            return self.expand(self.after[index], index, center, chosen, legs, used, after=True)
        # Food searches for later visits may have consumed time after finish_before.
        # Recheck against completion time, not the clock before the last paid lookup.
        completed_now = self.now + timedelta(seconds=max(0, self.clock() - self.started))
        if self.request.start_now and (self.before or self.inside or self.origin):
            arrival += completed_now - start
            start = completed_now
            self.internal_start = max(arrival, self.opens_at) if self.inside else arrival
            self.internal_end = self.internal_start + timedelta(seconds=self.internal_seconds) if self.inside else arrival
            if not self.after:
                finish = self.internal_end
        if (arrival > self.deadline or self.internal_end > self.game_start
                or (not self.historical and start < completed_now)
                or (self.request.finish_by and finish > self.request.finish_by)):
            self.reasons.add("time_infeasible")
            return None
        # Build timestamps only after the complete itinerary passed validation.
        visits, cursor, leg_index = [], start, 0
        for i, entry in enumerate(chosen[:len(self.before)]):
            if i or self.origin:
                leg = legs[leg_index]
                cursor += timedelta(seconds=leg["seconds"] + leg["buffer_seconds"])
                leg_index += 1
            visits.append(self.stop_payload(entry, cursor))
            cursor += timedelta(minutes=entry["request"].duration)
        visits.append({**self.stadium, "phase": "game", "arrive_at": arrival.isoformat(),
                       "starts_at": self.game_start.isoformat(), "depart_at": None, "stay_minutes": None})
        cursor = self.internal_start
        for entry in self.internal_chosen:
            cursor += timedelta(minutes=self.policy.internal_transfer_minutes)
            visits.append(self.stop_payload(entry, cursor))
            cursor += timedelta(minutes=entry["request"].duration)
        cursor = after_start
        for entry, leg in zip(chosen[len(self.before):], after_legs):
            cursor += timedelta(seconds=leg["seconds"] + leg["buffer_seconds"])
            visits.append(self.stop_payload(entry, cursor))
            cursor += timedelta(minutes=entry["request"].duration)
        # Different route prefixes/backtracking may change the visit time. Reuse
        # evidence, NOT an old verdict. Never run another network call here.
        for visit in visits:
            review = visit.get("review_verification")
            if review and not review["eligible"]:
                self.review_failure(review)
                return None
        return self.result("ok", stops=visits, legs=legs, start_at=start.isoformat(),
                           stadium_arrival_at=arrival.isoformat(), finish_at=finish.isoformat(),
                           timing_status="conditional_game_end" if self.after else "conditional_indoor" if self.inside else "travel_estimate_checked",
                           suggested_start=self.request.start_at is None and not self.request.start_now,
                           origin_included=self.origin is not None,
                           leg_buffer_minutes=self.policy.leg_buffer_minutes,
                           after_start_at=after_start.isoformat() if self.after else None,
                           historical=self.historical,
                           gate=self.gate if self.inside else None,
                           default_internal=self.default_internal,
                           internal_transfer_minutes=self.policy.internal_transfer_minutes if self.inside else None,
                           pre_game_deadline=(self.game_start if self.inside else self.deadline).isoformat(),
                           internal_finish_at=self.internal_end.isoformat() if self.inside else None,
                           total_distance_m=sum(l["distance_m"] for l in legs),
                           total_travel_seconds=sum(l["seconds"] for l in legs))

    def stop_payload(self, entry, arrival):
        stop, place = entry["request"], entry["place"]
        departure = arrival + timedelta(minutes=stop.duration)
        reviews = evaluate_reviews(entry.get("review_verification") or {}, stop.reviews,
                                   arrival=arrival, departure=departure) if stop.reviews else None
        return {key: place.get(key) for key in ("placeId", "name", "lat", "lng", "address", "kind", "source",
                                               "collectedAt", "referenceMonth", "stadiumAffiliation",
                                               "category", "subcategory", "detail", "cuisine",
                                               "floor", "zone", "sourceUrl", "locationStatus")} | {
            "phase": stop.phase, "arrive_at": arrival.isoformat(),
            "depart_at": departure.isoformat(),
            "stay_minutes": stop.duration, "stay_is_default": stop.stay_minutes is None,
            "food_verification": entry.get("food_verification"),
            "review_verification": reviews,
            "search_radius_m": entry["radius_m"], "verification_status": "operations_unverified"}

    def result(self, status, **values):
        return {"version": 1, "status": status, "stops": [], "legs": [], "mode": self.mode,
                "arrival_deadline": self.deadline.isoformat(), "radius_cap_m": self.cap,
                "direction_calls": self.calls, "search_trace": self.trace[-160:],
                "ranking_policy": "bounded_preference_evidence_travel_v1",
                "retained_place_ids": [p['placeId'] for _, p in sorted(self.pinned_stops.items())],
                "reasons": sorted(self.reasons),
                "food_search": self.food_verifier.audit() if self.has_food_requirements and self.food_verifier else None,
                "review_search": self.review_verifier.audit() if self.has_review_requirements and self.review_verifier else None,
                **({"candidate_retrieval": self.candidate_source.audit()} if self.candidate_source else {}),
                **values}
