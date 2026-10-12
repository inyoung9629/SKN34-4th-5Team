"""Bounded prefetch of public lodging pages, with foreground browser priority."""
import os
import pickle
import time

from llm.service.chat_runs import check_cancelled
from llm.v1.rag.nearby import kakao
from . import chrome_lodging as chrome


def candidates(stadiums, per_stadium=2, *, deadline=float("inf")):
    """Demand first; bootstrap each configured stadium with nearby hotels.

    Distance/category only selects warm-up work, never verifies a condition or
    changes the course's candidate ordering.
    """
    try:
        recent = chrome._page_cache().get("lodging:warm:recent", {})
        recent = recent.values() if isinstance(recent, dict) else []
        active = [r for r in recent if isinstance(r, dict) and r.get("last_used", 0) > time.time() - 7 * 24 * 3600]
        known = [r["candidate"] for r in sorted(active, key=lambda r: (r.get("hits", 0), r["last_used"]), reverse=True)]
    except (OSError, ValueError, TypeError, KeyError, EOFError, pickle.UnpicklingError):
        known = []
    seen = set()
    for code in [None, *stadiums]:
        check_cancelled()
        if time.monotonic() + 1 >= deadline:
            return
        if code is not None:
            nearby = kakao.nearby(code, "stay")
            nearby = sorted(nearby, key=lambda p: ("호텔" not in [s.strip() for s in p.get("detail", "").split(">")], p.get("distance", 0)))
            known = [{"id": str(p.get("placeId") or ""), "name": p.get("name", ""), "address": p.get("address", "")}
                     for p in nearby[:per_stadium]]
        for item in known:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and 0 < len(item[k]) <= 255 for k in ("id", "name", "address")):
                continue
            key = chrome._cache_key(item)
            if key not in seen:
                seen.add(key)
                yield item


def warm(stadiums, *, limit=6, max_seconds=180, refresh_before=1800, per_stadium=2):
    """Refresh near expiry; never cache a failure or extend an old snapshot."""
    if os.getenv("LODGING_CHROME_TRANSPORT", "local").lower() != "mcp":
        return {"status": "disabled", "reason": "foreground_priority_requires_mcp", "items": []}
    started = time.monotonic()
    deadline = started + max_seconds
    result = {"status": "ok", "items": [], "cached": 0}
    for candidate in candidates(stadiums, per_stadium, deadline=deadline):
        check_cancelled()
        if len(result["items"]) >= limit or time.monotonic() + 1 >= deadline:
            break
        page = chrome.cached(candidate)
        if page and page["cache_remaining_seconds"] > refresh_before:
            result["cached"] += 1
            continue
        page = chrome.collect(candidate, deadline=deadline, refresh_before=refresh_before, background=True)
        result["items"].append({"id": candidate["id"], "status": page["status"], "cache_hit": page.get("cache_hit", False)})
        if page["status"] == "busy":
            result["status"] = "deferred"
            break
        if len(result["items"]) >= limit:
            break
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result
