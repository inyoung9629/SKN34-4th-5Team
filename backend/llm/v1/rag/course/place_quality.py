"""Discover first, then verify external food/cafes in the two demo stadiums.

Kakao Local has no rating/review fields. Missing evidence is pending, not zero
stars. Only branch-matched, dated research can approve a candidate. Stadium
tenants are handled separately by venue_policy before this gate is called.
"""
import hashlib
import json
import logging
import math
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache.backends.filebased import FileBasedCache
from llm.v1.progress import ProgressCancelled, ProgressStorageError
from .geo import haversine_m

_SCOPE = ContextVar("course_quality_stadium", default=None)
_USED = ContextVar("course_quality_checked", default=False)
_RESEARCH = ContextVar("course_quality_research", default=None)
_FRESH = ContextVar("course_quality_fresh", default=True)
log = logging.getLogger(__name__)
STADIUMS = {"GWANGJU", "JAMSIL"}
FOOD_CATEGORIES = {"FOOD", "FOOD_OUT", "FD6", "BAR", "CAFE", "CE7"}
# An ordinary izakaya's private room is not an entertainment business.
ENTERTAINMENT = re.compile(r"룸\s*[살싸]롱|유흥\s*주점|단란\s*주점|노래\s*주점|가라오케")
BLOCKED_IDS = {"18774818"}  # 밤비: user's explicit exclusion, not an industry allegation.
NOTICE = "카카오 검색 후보에서 평점·후기와 실내 상태를 확인한 매장을 사용해요. 구장 내부 먹거리는 평점 검사에서 제외해요."
RESEARCH_VERSION = "shared-pages-v2-review-wraps"
GENERIC_QUERIES = {"", "카페", "커피", "식당", "음식점", "맛집", "식사", "술집", "먹거리", "간식"}
LOCAL_REUSE_M = 800
LOCAL_DISTANCE_SLACK_M = 250
LOCAL_CHOICES = 3


@contextmanager
def research_policy(enabled):
    """Broad seed lookups may reuse evidence; actual visit searches investigate."""
    token = _FRESH.set(enabled and _FRESH.get())
    try:
        yield
    finally:
        _FRESH.reset(token)


def reusable_conditions(conditions):
    if not _FRESH.get():
        return False
    if not conditions or not active():
        return True
    from .evidence_memory import requirements
    return all(r.attribute == "catalog" and r.intent != "exclude" and _text(r.term) in GENERIC_QUERIES
               for r in requirements(conditions))


def distance_to(place, center):
    if center is None:
        return float("inf")
    try:
        coords = [float(v) for v in (center.get("lat"), center.get("lng"),
                  place.get("lat", place.get("y")), place.get("lng", place.get("x")))]
        if not all(math.isfinite(v) for v in coords) or not all(-90 <= coords[i] <= 90 and -180 <= coords[i + 1] <= 180 for i in (0, 2)):
            return float("inf")
        return haversine_m(*coords)
    except (TypeError, ValueError, OverflowError):
        return float("inf")


def _nearby_choices(places, category, query, center, radius):
    """Evidence reuse is limited to the closest local alternatives, never a rank boost."""
    if center is None or _text(query) not in GENERIC_QUERIES:
        return False
    from . import availability
    def usable(p):
        known = resolve(p)
        detail = str(known["detail"] if known else p.get("detail") or p.get("category_name") or "")
        bar = bool(re.search(r"술집|주점|호프|이자카야", detail))
        want_bar = category == "BAR" or _text(query) == "술집"
        return (candidate_allowed(p) and (category in ("CAFE", "CE7") or bar == want_bar)
                and not availability.check(p, availability.visit_date()))
    candidates = [p for p in places if usable(p)]
    nearest = min((distance_to(p, center) for p in candidates), default=float("inf"))
    limit = min(radius, LOCAL_REUSE_M, nearest + LOCAL_DISTANCE_SLACK_M)
    local = [p for p in candidates if distance_to(p, center) <= limit]
    brand = lambda p: str(p.get("name") or p.get("place_name") or "").split()[0]
    needed = min(LOCAL_CHOICES, len({brand(p) for p in local if p.get("name") or p.get("place_name")}))
    confirmed = {brand(p) for p in local if filter_place(p, category) is not None}
    return needed > 0 and len(confirmed) >= needed


@contextmanager
def request_scope(code):
    token = _SCOPE.set(code)
    used_token = _USED.set(False)
    research_token = _RESEARCH.set({"reader": None, "tried": set(), "searched": set(), "checked": set(), "approved": set(), "deadline": None})
    try:
        yield
    finally:
        if reader := _RESEARCH.get().get("reader"):
            reader.close()
        _RESEARCH.reset(research_token)
        _USED.reset(used_token)
        _SCOPE.reset(token)


def active(category=None):
    return _SCOPE.get() in STADIUMS and (category is None or category in FOOD_CATEGORIES)


def missing_notice():
    if active() and _USED.get():
        state = _RESEARCH.get() or {}
        counts = f"검색·보유 후보 {len(state.get('searched', []))}곳 중 평점·실내 근거를 확인한 곳은 {len(state.get('approved', []))}곳이에요. "
        return counts + "확인 자료가 부족한 곳은 보류했으며, 실제 매장이 없거나 평점이 0점이라는 뜻은 아니에요."
    return ""


@lru_cache(maxsize=1)
def catalogue():
    path = Path(__file__).with_name("reviewed_places.json")
    rows = json.loads(path.read_text(encoding="utf-8"))["places"]
    return {row["placeId"]: row for row in rows}


@lru_cache(maxsize=1)
def verdict_cache():
    # Reusable across Django workers/restarts. Only bounded verdicts and source
    # links are cached; page/review bodies and images are never saved here.
    return FileBasedCache(str(settings.BASE_DIR / ".cache" / "place-quality"), {"TIMEOUT": 7 * 86400, "OPTIONS": {"MAX_ENTRIES": 3000}})


def cache_key(place):
    def coordinate(key, raw):
        try:
            return round(float(place.get(key, place.get(raw))), 6)
        except (TypeError, ValueError, OverflowError):
            return None
    identity = [_id(place), _text(place.get("name") or place.get("place_name")),
                coordinate("lat", "y"), coordinate("lng", "x")]
    return "quality-v4:" + hashlib.sha256(json.dumps(identity, ensure_ascii=False, default=str).encode()).hexdigest()


def canonical(place):
    try:
        pid = _id(place)
        lat, lng = float(place.get("lat", place.get("y"))), float(place.get("lng", place.get("x")))
        name = str(place.get("name") or place.get("place_name") or "")
        address = str(place.get("address") or place.get("road_address_name") or "")
        detail = str(place.get("detail") or place.get("category_name") or "")
        if not pid or not name or not address or not -90 <= lat <= 90 or not -180 <= lng <= 180:
            return None
        return {"placeId": pid, "stadium": _SCOPE.get(), "name": name, "address": address, "lat": lat, "lng": lng,
                "detail": detail, "kind": "CAFE" if "카페" in detail or place.get("category_group_code") == "CE7" else
                "BAR" if re.search(r"술집|주점|호프|이자카야", detail) else "FOOD"}
    except (ValueError, TypeError, OverflowError):
        return None


def _text(value):
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _id(place):
    value = str(place.get("placeId") or place.get("id") or place.get("kakao_place_id") or "")
    if value.isdecimal():
        return value
    for key in ("placeUrl", "place_url", "sourceUrl"):
        url = urlsplit(str(place.get(key) or ""))
        if url.hostname == "place.map.kakao.com" and url.path.strip("/").isdecimal():
            return url.path.strip("/")
    return ""


def approved(row):
    try:
        age = (date.today() - date.fromisoformat(row["checkedAt"])).days
        return (row["status"] == "approved" and 0 <= age <= 180
                and 0 < row["rating"] <= 5 and row["ratingCount"] >= 2
                and row["reviewUrl"].startswith("https://www.diningcode.com/profile.php?rid=")
                and row["interior"]["status"] == "acceptable"
                and bool(row["interior"]["sourceUrl"]) and bool(row["interior"]["note"]))
    except (KeyError, TypeError, ValueError):
        return False


def resolve(place):
    identifier = _id(place)
    if not identifier:
        return None
    row = catalogue().get(identifier)
    if not row or not approved(row):
        row = verdict_cache().get(cache_key(place), {}).get("row")
    if not row or not approved(row):
        return None
    if _text(place.get("name") or place.get("place_name")) not in {_text(row["name"]), *map(_text, row.get("aliases", []))}:
        return None
    try:
        distance = haversine_m(float(place.get("lat", place.get("y"))), float(place.get("lng", place.get("x"))), row["lat"], row["lng"])
        return row if distance is not None and distance <= 80 else None
    except (TypeError, ValueError, OverflowError):
        return None


def filter_place(place, category):
    detail = str(place.get("detail") or place.get("category_name") or place.get("category_detail") or "")
    if _id(place) in BLOCKED_IDS or ENTERTAINMENT.search(detail):
        return None
    if not active(category):
        return place
    _USED.set(True)
    row = resolve(place)
    if not row or row["stadium"] != _SCOPE.get():
        return None
    expected = "CAFE" if category in ("CAFE", "CE7") else "FOOD"
    if ("CAFE" if row["kind"] == "CAFE" else "FOOD") != expected:
        return None
    # Reattach canonical research after raw-provider/RAG adapters; caller tags
    # and an LLM-generated "술집" suffix cannot change the researched kind.
    return {**place, "detail": row["detail"], "category_name": row["detail"]}


def candidate_allowed(place):
    detail = str(place.get("detail") or place.get("category_name") or "")
    return _id(place) not in BLOCKED_IDS and not ENTERTAINMENT.search(detail)


def verify_candidates(places, category, query="", *, center=None, search_center=None, radius=2500, reuse=True):
    """A bounded fresh investigation, after provider search and scope filtering."""
    places = list(places)
    if not active(category):
        return places
    _USED.set(True)
    state = _RESEARCH.get()
    if state is None:
        return [p for p in places if filter_place(p, category)]
    state["searched"].update(_id(p) for p in places if _id(p))
    def checked():
        rows = [row for p in places if (row := filter_place(p, category)) is not None]
        state["approved"].update(_id(p) for p in rows if _id(p))
        return sorted(rows, key=lambda p: distance_to(p, center)) if center else rows
    def enough():
        return reuse and _nearby_choices(places, category, query, center, radius)
    if not _FRESH.get() or enough():
        return checked()
    missing = []
    for p in places:
        if not candidate_allowed(p):
            continue
        if resolve(p):
            state["approved"].add(_id(p))
            continue
        item = canonical(p)
        key = cache_key(p)
        cached = verdict_cache().get(key)
        # Old empty verdicts may come from consumed pages or wrapped review
        # quotes. Keep proven approvals/rejections, but retry those misses once.
        cooling = cached is not None and (cached.get("row") or cached.get("researchVersion") == RESEARCH_VERSION)
        if item and key not in state["tried"] and not cooling:
            missing.append((p, item, key))
    # Match explicit menu/category hints first; do not spend the budget on a
    # nearby bar merely because Kakao returned it for a sushi keyword.
    from .grounding import query_variants, canonical_menu_term
    terms = [_text(term) for term in query_variants(query) if term]
    def priority(triple, focus):
        text = _text(triple[1]["name"] + triple[1]["detail"])
        direct = any(term in text for term in terms)
        # A retrieval hint only: Chinese restaurants still need actual menu proof.
        cuisine = canonical_menu_term(query) == "짜장면" and "중식" in text and "양꼬치" not in text
        distance = distance_to(triple[0], focus) if focus else float(triple[0].get("distance") or 0)
        return (not direct, not cuisine, distance)
    missing.sort(key=lambda triple: priority(triple, center))
    if search_center and center and _text(query) not in GENERIC_QUERIES:
        # A pre-game visit has two useful areas: the departure point and the
        # stadium being searched. A dense departure area must not consume every
        # investigation before a restaurant beside the stadium is even read.
        # Interleave fresh candidates only; cached approval never gets a boost.
        around_search = sorted(missing, key=lambda triple: priority(triple, search_center))
        interleaved, seen = [], set()
        for pair in zip(missing, around_search):
            for triple in pair:
                if triple[2] not in seen:
                    interleaved.append(triple)
                    seen.add(triple[2])
        missing = interleaved
    from travel.public_page_reader import PublicReader
    from . import quality_research
    pending, profiles = [], []
    def flush():
        checks = {}
        if profiles:
            try:
                checks = quality_research.interiors(profiles)
            except (ProgressCancelled, ProgressStorageError):
                raise
            except Exception as exc:
                log.warning("place interior check failed: %s", type(exc).__name__)
        by_id = {p["place"]["placeId"]: p for p in profiles}
        for item, key in pending:
            proof, interior = by_id.get(item["placeId"]), checks.get(item["placeId"])
            row = None
            if proof and interior:
                row = {**item, "status": "approved" if interior["status"] == "acceptable" else "rejected",
                       "rating": proof["rating"], "ratingCount": proof["ratingCount"], "ratingSource": "다이닝코드",
                       "reviewUrl": proof["url"], "checkedAt": date.today().isoformat(), "interior": interior}
            verdict_cache().set(key, {"row": row, "researchVersion": RESEARCH_VERSION}, 7 * 86400 if row and approved(row) else 3600)
            if row and approved(row):
                state["approved"].add(item["placeId"])
        pending.clear()
        profiles.clear()

    local_search = reuse and center is not None and _text(query) in GENERIC_QUERIES
    for original, item, key in missing[:10 if query else 6]:
        if len(state["tried"]) >= 24:
            break
        if state["deadline"] is None:
            state["deadline"] = time.monotonic() + 100
            state["reader"] = quality_research.ProfileReader(PublicReader(deadline=state["deadline"], max_requests=75, gap=.25))
        if time.monotonic() + 12 >= state["deadline"]:
            break
        state["tried"].add(key)
        state["checked"].add(item["placeId"])
        try:
            urls = quality_research.discover(item, state["reader"])
            found = next((row for url in urls[:6] if (row := quality_research.profile(item, url, state["reader"]))), None)
            pending.append((item, key))
            if found:
                profiles.append(found)
        except (ProgressCancelled, ProgressStorageError):
            raise
        except Exception as exc:
            log.warning("place quality read failed: %s", type(exc).__name__)
        if local_search and len(pending) >= LOCAL_CHOICES:
            flush()
            if enough():
                break
    flush()
    return checked()


def search_candidates(args, category):
    """Supplement live search with reviewed seeds, never replace discovery."""
    if not active(category):
        return None
    _USED.set(True)
    kind = "CAFE" if category in ("CAFE", "CE7") else "FOOD"
    query = _text(args.get("query"))
    want_bar = category == "BAR" or any(word in query for word in ("술집", "이자카야", "호프", "맥주", "주점", "하이볼"))
    rows = []
    for row in catalogue().values():
        if row["stadium"] != _SCOPE.get() or not approved(row) or row["placeId"] in BLOCKED_IDS:
            continue
        if ("CAFE" if row["kind"] == "CAFE" else "FOOD") != kind or want_bar and row["kind"] != "BAR":
            continue
        if query and not want_bar and query not in ("식당", "음식점", "맛집", "식사", "카페", "커피"):
            terms = [row["name"], row["detail"], *row.get("aliases", []), *row.get("searchTerms", [])]
            if not any(query in _text(term) or _text(term) in query for term in terms if term):
                continue
        distance = haversine_m(args.get("latitude"), args.get("longitude"), row["lat"], row["lng"])
        if distance is None or distance > args.get("radius", 2500):
            continue
        rows.append({"id": row["placeId"], "place_name": row["name"], "y": str(row["lat"]), "x": str(row["lng"]),
                     "category_group_code": "CE7" if row["kind"] == "CAFE" else "FD6", "category_name": row["detail"],
                     "road_address_name": row["address"], "place_url": f"https://place.map.kakao.com/{row['placeId']}",
                     "distance": distance})
    return sorted(rows, key=lambda row: row["distance"])


def annotation(place):
    row = resolve(place)
    if not row:
        return ""
    return (f"[평점·후기]({row['reviewUrl']}) 다이닝코드 {row['rating']:g}/5 · 평가 {row['ratingCount']}명"
            f" (확인 {row['checkedAt']})")
