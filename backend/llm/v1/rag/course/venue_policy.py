"""모든 코스 검색에 적용하는 구장 내부 장소 정책. 대화 취향/캐시와 독립적이다."""
import re
from contextlib import contextmanager
from contextvars import ContextVar

from travel.stadium_scope import classify_stadium_point, reviewed_zones
from travel.stadium_food import food_candidates, provider_shape, resolve_food_place, matching_menu_items, menu_text
from travel import stadium_signatures
from . import place_quality
from .signature_intent import generic_term, use_default

_PERMISSION = ContextVar("course_internal_venues", default=(None, frozenset()))
_EXTERNAL = ContextVar("course_external_visits", default=frozenset())
_MENU_REQUESTS = ContextVar("course_internal_menu_requests", default=())
_SIGNATURE_REQUESTS = ContextVar("course_signature_requests", default=())
_INSIDE = re.compile(r"(?:야구장|경기장|구장)\s*(?:내부|안쪽|내(?:에서|에|의|\s|$)|안(?:에서|에|의|\s|$)|에\s*있는|매점|식사|먹거리|간식|(?:대표|시그니처|시그니쳐)\s*(?:먹거리|메뉴|음식|간식)|에서(?![^.!?\n,]*(?:나와|나온|나가|나간|벗어나|출발))(?=[^.!?\n,]*(?:먹|식사|끼니|간식)))|입장\s*후.*(?:매점|카페|식당|편의점)")
_NEGATIVE = re.compile(r"말고|제외|빼(?:줘|고|주세요)|싫|아니라|추천하지|원하지|안\s*(?:가|갈|먹|마시|들르)")
_CATEGORY = {"FOOD_OUT": "FOOD", "FOOD_IN": "FOOD", "FD6": "FOOD", "BAR": "FOOD", "CE7": "CAFE",
             "CS2": "CONVENIENCE", "AD5": "STAY", "AT4": "SPOT"}


def persistent_conditions(conditions):
    """Internal venue permission is never a saved preference, including old checkpoints."""
    return [p for p in conditions if not _INSIDE.search(p.get("text", ""))]


def mentions_internal(question):
    """Decide whether a legacy stateless request also needs explicit intent parsing."""
    return bool(_INSIDE.search(question))


@contextmanager
def request_policy(question, code, requests=(), *, visits=None):
    allowed = set()
    external = set()
    menu_requests = []
    signature_requests = []
    if visits is not None:
        from .visit_requests import normalize
        normalized = normalize(question, visits)
        if normalized is not None:
            requests = [{"category": v["kind"], "expression": v["expression"],
                         "query": v["query"], "conditions": v["conditions"],
                         "signature_default": v.get("signature_default", False)}
                        for v in normalized if v["internal"]]
            external = {_CATEGORY.get(v["kind"], v["kind"]) for v in normalized if not v["internal"]}
    for item in requests or ():
        expression = item.get("expression", "").strip()
        if not expression or expression not in question or not _INSIDE.search(expression):
            continue
        # 모델이 부정 표현 직전까지만 인용해 허용으로 뒤집지 못하게 한다.
        start = question.index(expression)
        clause = re.split(r"[.!?\n,]", question[start:])[0]
        if _NEGATIVE.search(clause):
            continue
        category = _CATEGORY.get(item.get("category"), item.get("category"))
        allowed.add(category)
        menu_requests.append((category, expression))
        if use_default(expression, category, item.get("query"), item.get("conditions"), item.get("signature_default")):
            signature_requests.append(expression)
    token = _PERMISSION.set((code, frozenset(allowed)))
    external_token = _EXTERNAL.set(frozenset(external))
    menu_token = _MENU_REQUESTS.set(tuple(menu_requests))
    signature_token = _SIGNATURE_REQUESTS.set(tuple(signature_requests))
    try:
        yield
    finally:
        _SIGNATURE_REQUESTS.reset(signature_token)
        _MENU_REQUESTS.reset(menu_token)
        _EXTERNAL.reset(external_token)
        _PERMISSION.reset(token)


def _signature_only(kind):
    requests = [expression for category, expression in _MENU_REQUESTS.get() if category == kind]
    return kind in ("FOOD", "CAFE") and bool(requests) and all(expression in _SIGNATURE_REQUESTS.get() for expression in requests)


def signature_details(place, kind):
    if any(category == kind and expression in _SIGNATURE_REQUESTS.get() for category, expression in _MENU_REQUESTS.get()):
        return stadium_signatures.details(place)
    return None


def source_conditions(conditions):
    # The semantic parser marks this exact current-turn phrase as a generic meal.
    # Other conditions, especially saved exclusions and allergies, remain mandatory.
    return [text for text in conditions if text not in _SIGNATURE_REQUESTS.get()
            and not (_SIGNATURE_REQUESTS.get() and generic_term(text))]


def explain_missing_signature(result):
    code, _ = _PERMISSION.get()
    signature = stadium_signatures.SIGNATURES.get(code)
    if result and signature and any(_signature_only(kind) for kind in ("FOOD", "CAFE")) and not any(stadium_signatures.details(p) for p in food_candidates(code)):
        text = (f"이 구장의 기본 시그니처는 {signature['store']} {stadium_signatures.menu_label(signature)}예요. "
                "자리어때 수집 자료에서 매장과 해당 메뉴를 함께 확인하지 못해 코스에 넣지 못했어요.")
        result = {**result, "answer": text + "\n\n" + result.get("answer", "")}
    return result


def _menu_query_allowed(place, query, kind):
    # A source category is a display label; an explicit, photo-backed menu may
    # cross FOOD/CAFE categories. The current request must actually name it.
    return (kind in ("FOOD", "CAFE") and bool(matching_menu_items(place, query))
            and any(category == kind and menu_text(query) in menu_text(expression)
                    for category, expression in _MENU_REQUESTS.get()))


def filter_candidates(places, category=None, *, check_quality=True):
    """공급자 태그와 현재 구장 경계를 함께 검사한다. 오래된 RAG/캐시도 예외가 아니다.

    Kakao 원본과 정규화된 후보를 모두 받아 원본 형태를 보존한다. 실제 경기장
    목적지는 이 후보 목록과 별도로 삽입하므로 제외하지 않는다.
    """
    places = list(places)
    if not places:
        return []
    zones = reviewed_zones()  # 읽기 실패 시 차단을 건너뛰지 않고 오류를 전파한다.
    code, allowed = _PERMISSION.get()
    result, catalogues = [], {}
    for place in places:
        try:
            lat, lng = place.get("lat", place.get("y")), place.get("lng", place.get("x"))
            if isinstance(lat, bool) or isinstance(lng, bool):
                continue
            point = {"lat": float(lat), "lng": float(lng)}
            if not -90 <= point["lat"] <= 90 or not -180 <= point["lng"] <= 180:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        area = classify_stadium_point(point, zones)
        tagged = place.get("stadiumArea") or {}
        if area["scope"] in ("unknown", "excluded_complex") or tagged.get("scope") == "excluded_complex":
            continue
        internal = (area["scope"] == "internal" or tagged.get("scope") == "internal"
                    or place.get("scope") == "internal" or place.get("category") == "FOOD_IN")
        kind = category or place.get("category") or place.get("category_group_code")
        kind = _CATEGORY.get(kind, kind)
        canonical = resolve_food_place(place, catalogues) if internal else None
        if internal:
            if not canonical or canonical["stadium"] != code or kind not in allowed:
                continue
            menu_query = place.get("_internal_menu_query", "")
            signature = signature_details(canonical, kind)
            if _signature_only(kind) and not signature:
                continue
            cross_category = canonical["category"] != kind
            if cross_category and not signature and not _menu_query_allowed(canonical, menu_query, kind):
                continue
            # Never carry a cached menu, review or business status into collected facts.
            clean = {k: v for k, v in place.items() if k not in ("verifiedFacts", "conditionChecks", "lodgingCheck", "menuEvidence", "_signature_menu", "_signature_reason")}
            result.append({**clean, **canonical, "category": place.get("category", kind),
                           **(signature or {}),
                           **({"sourceCategory": canonical["category"]} if cross_category else {})})
        elif kind not in allowed or kind in _EXTERNAL.get():
            checked = (place_quality.filter_place(place, kind) if check_quality else
                       place if place_quality.candidate_allowed(place) else None)
            if checked is not None:
                result.append(checked)
    return result


def discover_candidates(invoke, args, category, *, diagnostics=None):
    """Fetch all required pages without changing the shared quality budget."""
    code, allowed = _PERMISSION.get()
    kind = _CATEGORY.get(category, category)
    if code and kind in allowed:
        # Empty/unavailable source data must never fall back to Kakao tenants.
        from .geo import haversine_m
        query = args.get("query") or ""
        places = []
        for place in food_candidates(code):
            if (haversine_m(args.get("latitude"), args.get("longitude"), place["lat"], place["lng"]) or 0) > args.get("radius", 2500):
                continue
            signature = signature_details(place, kind)
            if _signature_only(kind) and not signature:
                continue
            if signature:
                places.append({**place, **signature, "category": kind})
            elif _menu_query_allowed(place, query, kind):
                places.append({**place, "category": kind, "_internal_menu_query": query})
            elif place["category"] == kind:
                places.append(place)
        # Query conditions are verified against collected fields by the same chooser.
        if diagnostics is not None:
            diagnostics.update(source="MYSEATCHECK", warning="자리어때 수집 목록만 사용합니다. 핀은 구장 옆 표시 위치이며 상세 위치는 매장별 링크를 확인하세요. 현재 영업·메뉴는 미확인입니다.")
        return [provider_shape(p) for p in places]
    found = {}
    for page in range(1, 4):
        payload = invoke("course", "search_places", {**args, "page": page})
        if not isinstance(payload, dict):
            if diagnostics is not None:
                diagnostics["warning"] = str(payload)
            break
        if diagnostics is not None:
            diagnostics.update({k: v for k, v in payload.items() if k != "places"})
        for place in filter_candidates(payload.get("places", []), category, check_quality=False):
            key = place.get("id") or (place.get("place_name"), place.get("y"), place.get("x"))
            found.setdefault(key, place)
        if (not place_quality.active(category) and len(found) >= 15) or not payload.get("hasNextPage"):
            break
    return list(found.values())


def search_candidates(invoke, args, category, *, diagnostics=None, candidate_filter=None, quality_center=None, quality_reuse=True,
                      discovered=None):
    """Discovery may overlap; evidence checks and budget updates remain ordered."""
    places = discover_candidates(invoke, args, category, diagnostics=diagnostics) if discovered is None else discovered
    code, allowed = _PERMISSION.get()
    if code and _CATEGORY.get(category, category) in allowed:
        return places  # Collected stadium tenants never enter the external gate.
    found = {p.get("id") or (p.get("place_name"), p.get("y"), p.get("x")): p for p in places}
    # Reviewed rows are supplementary cached evidence, never a search allowlist.
    raw_count = len(found)
    for place in place_quality.search_candidates(args, category) or []:
        found.setdefault(place["id"], place)
    center = {"lat": args.get("latitude"), "lng": args.get("longitude")}
    located = all(center[k] is not None for k in ("lat", "lng"))
    # Provider distance fields and cached seed locations never bypass the current
    # search circle, excluded visits, or the caller's route constraints.
    candidates = [p for p in found.values() if
                  (not located or place_quality.distance_to(p, center) <= args.get("radius", 2500))
                  and (candidate_filter is None or candidate_filter(p))]
    result = place_quality.verify_candidates(candidates, category, args.get("query", ""),
        center=quality_center or (center if located else None), search_center=center if located else None,
        radius=args.get("radius", 2500), reuse=quality_reuse)
    if diagnostics is not None and place_quality.active(category):
        diagnostics.update(source="KAKAO_WITH_QUALITY_CHECK", rawCandidateCount=raw_count,
                           verifiedCandidateCount=len(result), warning=place_quality.NOTICE)
    return result
