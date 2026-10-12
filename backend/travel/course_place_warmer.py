"""Bounded public-place preparation. Never changes course ranking or conditions.

Queue files contain branch identity and supported menu terms only, not user
messages, origins, review bodies or images. They share the backend cache volume.
"""
import hashlib
import json
import math
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from llm.service import chat_runs

BACKGROUND = ContextVar("course_place_background", default=False)
MAX_QUEUE = 512
RETENTION = 7 * 86400


def enabled():
    return getattr(settings, "COURSE_PLACE_WARM_ENABLED", False)


def root():
    return Path(getattr(settings, "COURSE_PLACE_WARM_DIR", settings.BASE_DIR / ".cache" / "course-place-warm"))


def read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix("." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def foreground():
    """A crashed request's marker expires; normal requests remove it on exit."""
    marker = None
    if enabled() and not BACKGROUND.get():
        try:
            marker = root() / "active" / (uuid4().hex + ".json")
            write(marker, {"started": time.time()})
        except OSError:
            marker = None  # Preparation must not break foreground generation.
    try:
        yield
    finally:
        if marker:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass


def foreground_active():
    for path in (root() / "active").glob("*.json"):
        if time.time() - read(path).get("started", 0) < 600:
            return True
        path.unlink(missing_ok=True)
    return False


def remember(places, code, query=""):
    """Best-effort demand hints, throttled per branch; no recommendation boost."""
    from llm.v1.rag.course import place_quality as quality
    from llm.v1.rag.course.grounding import canonical_menu_term
    from llm.v1.rag.course.catalog_conditions import MENUS
    if not enabled() or code not in quality.STADIUMS:
        return
    menu = canonical_menu_term(query)
    menus = [menu] if menu in MENUS.values() else []
    try:
        for place in list(places)[:12]:
            row = quality.canonical(place)
            if not row or not quality.candidate_allowed(place) or not row["placeId"].isdecimal():
                continue
            row["stadium"] = code
            key = hashlib.sha256((code + quality.cache_key(place)).encode()).hexdigest()
            path = root() / "queue" / (key + ".json")
            old = read(path)
            merged = list(dict.fromkeys([*old.get("menus", []), *menus]))
            if time.time() - old.get("seen", 0) < 3600 and merged == old.get("menus", []):
                continue
            write(path, {**old, "place": row, "menus": merged, "seen": time.time()})
    except (OSError, ValueError, TypeError):
        pass


def seeds(codes, count):
    """Rotate directions and categories, rather than repeatedly warming one shop."""
    from baseball.stadium_locations import reviewed_venue
    from llm.v1.rag.course import agent, venue_policy, place_quality as quality
    state_path = root() / "seeds.json"
    state = read(state_path)
    jobs = [(code, direction, query) for direction in range(5)
            for query in ("카페", "술집", "자장면", "돈까스", "식당") for code in codes]
    if not jobs:
        return
    for _ in range(min(count, len(jobs))):
        chat_runs.check_cancelled()
        index = state.get("cursor", 0) % len(jobs)
        code, direction, query = jobs[index]
        anchor = reviewed_venue(code)
        point = dict(anchor)
        if direction:
            angle = (direction - 1) * math.pi / 2
            point["lat"] += math.cos(angle) * 1200 / 111320
            point["lng"] += math.sin(angle) * 1200 / (111320 * math.cos(math.radians(point["lat"])))
        with quality.request_scope(code), venue_policy.request_policy("", code):
            places = venue_policy.discover_candidates(agent.invoke_domain_tool,
                {"method": "keyword", "query": query, "category": "CE7" if query == "카페" else "FD6",
                 "latitude": point["lat"], "longitude": point["lng"], "radius": 1400, "limit": 15, "sort": "distance"},
                "CAFE" if query == "카페" else "FOOD")
            places = [p for p in places if quality.distance_to(p, anchor) <= 2500]
            remember(places, code, query)
        state["cursor"] = index + 1
        write(state_path, state)


def pending(codes):
    now, rows = time.time(), []
    for path in (root() / "queue").glob("*.json"):
        row = read(path)
        if now - row.get("seen", 0) > RETENTION:
            path.unlink(missing_ok=True)
            continue
        rows.append((path, row))
    # Enforce a bounded queue, retaining recent demand and bootstrap coverage.
    rows.sort(key=lambda item: item[1].get("seen", 0), reverse=True)
    for path, _ in rows[MAX_QUEUE:]:
        path.unlink(missing_ok=True)
    rows = rows[:MAX_QUEUE]
    # Interleave stadiums and kinds so one dense restaurant area cannot starve cafes/bars.
    groups = {}
    for item in sorted(rows, key=lambda item: item[1].get("attempted", 0)):
        p = item[1]["place"]
        if p["stadium"] not in codes:
            continue
        groups.setdefault((p["stadium"], p["kind"]), []).append(item)
    while groups:
        for key in list(groups):
            yield groups[key].pop(0)
            if not groups[key]:
                del groups[key]


def warm(codes, **options):
    if not enabled():
        return {"status": "disabled", "items": []}
    lock = root() / "worker.json"
    owner = uuid4().hex
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock.open("x", encoding="utf-8") as stream:
            json.dump({"owner": owner, "started": time.time()}, stream)
    except FileExistsError:
        # A stale lock is only removed; the next cycle acquires it atomically.
        if time.time() - read(lock).get("started", time.time()) > 720:
            lock.unlink(missing_ok=True)
        return {"status": "deferred", "items": []}
    try:
        return _warm(codes, **options)
    finally:
        if read(lock).get("owner") == owner:
            lock.unlink(missing_ok=True)


def _warm(codes, *, limit=6, max_seconds=120, seed_count=2, refresh_days=3):
    from llm.v1.rag.course import place_quality as quality, venue_policy, evidence_memory as evidence
    from datetime import date
    if not enabled():
        return {"status": "disabled", "items": []}
    if foreground_active():
        return {"status": "deferred", "items": []}
    started = time.monotonic()
    result = {"status": "ok", "items": [], "fresh": 0, "menu_storage_enabled": bool(settings.PLACE_EVIDENCE_STORAGE_POLICIES)}
    inherited = chat_runs._cancelled.get()
    token = chat_runs._cancelled.set(lambda: inherited() or time.monotonic() - started >= max_seconds or foreground_active())
    background = BACKGROUND.set(True)
    try:
        seeds(codes, seed_count)
        for path, task in pending(codes):
            chat_runs.check_cancelled()
            if len(result["items"]) >= limit:
                break
            if time.time() - task.get("attempted", 0) < 3600:
                continue
            p = task["place"]
            with quality.request_scope(p["stadium"]), venue_policy.request_policy("", p["stadium"]):
                # Recheck stadium boundaries/source restrictions even for queued rows.
                if not venue_policy.filter_candidates([p], p["kind"], check_quality=False):
                    path.unlink(missing_ok=True)
                    continue
                existing = quality.resolve(p)
                reviewed = quality.resolve(p, include_rejected=True)
                age = (date.today() - date.fromisoformat(reviewed["checkedAt"])).days if reviewed else None
                if reviewed and age < refresh_days and (not existing or not (task.get("menus") and settings.PLACE_EVIDENCE_STORAGE_POLICIES)):
                    result["fresh"] += 1
                    continue
                if not reviewed or age >= refresh_days:
                    quality.verify_candidates([p], p["kind"], center=p, reuse=False, refresh=bool(reviewed))
                checked = quality.resolve(p)
                stored = 0
                # Use the existing reviewed URL policies. Never grant ourselves
                # permission to persist menu observations, or store page bodies.
                if checked and task.get("menus") and settings.PLACE_EVIDENCE_STORAGE_POLICIES:
                    candidate = {**p, "category": "CAFE" if p["kind"] == "CAFE" else "FOOD_OUT"}
                    with evidence.request_budget():
                        evidence.ensure_place(candidate, evidence.timezone.now())
                        for menu in task["menus"]:
                            chat_runs.check_cancelled()
                            req = evidence.requirements([menu])
                            if all(evidence.verdict(evidence.read(candidate, r), r) == "match" for r in req):
                                continue
                            # Read only the already discovered exact branch profile.
                            with quality.local_probe():
                                facts, opened, _ = evidence.search([candidate], req)
                            now = evidence.timezone.now()
                            stored += evidence.store(candidate, evidence.checked_rows(candidate, req, facts, opened, now), now)
                result["items"].append({"id": p["placeId"], "stadium": p["stadium"],
                                        "quality": bool(checked), "menu_observations": stored})
                task["attempted"] = time.time()
                write(path, task)
    except chat_runs.Stopped:
        if inherited():
            raise
        result["status"] = "deferred" if foreground_active() else "budget_exhausted"
    finally:
        BACKGROUND.reset(background)
        chat_runs._cancelled.reset(token)
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result
