"""Exact, positive catalog requests. Anything outside this grammar uses the LLM.

These checks establish category/brand or a labeled specialty hint, never verified
menu availability, reviews or opening hours.
"""
import re

CUISINES = {
    "한식": "한식", "한식집": "한식", "한식당": "한식", "한국음식": "한식",
    "중식": "중식", "중식집": "중식", "중식당": "중식", "중국집": "중식", "중국요리": "중식",
    "일식": "일식", "일식집": "일식", "일식당": "일식", "일본음식": "일식",
    "양식": "양식", "양식집": "양식", "양식당": "양식",
}
BRANDS = {"메가MGC커피": "메가MGC커피", "메가커피": "메가MGC커피", "컴포즈커피": "컴포즈커피"}
MENUS = {"자장면": "짜장면", "짜장면": "짜장면", "돈카츠": "돈까스", "돈까스": "돈까스"}
GENERIC = {"카페": "카페", "커피": "카페", "식당": "식당", "음식점": "식당", "식사": "식당"}
TERMS = {**CUISINES, **BRANDS, **GENERIC, **MENUS}
COURSE_INSTRUCTIONS = frozenset({"모든이동은도보", "모든이동은도보로", "모두도보로이동",
    "도보로이동", "도보이동", "전체도보이동", "각장소는한곳씩", "각장소한곳씩",
    "방문지별한곳씩", "각방문지한곳씩"})


def course_scope(text):
    value = re.sub(r"\s+", "", text.strip().rstrip(".!"))
    if value in COURSE_INSTRUCTIONS:
        return "TRAVEL" if "도보" in value else "ITINERARY"
    if value in {"지도에찍은출발지에서출발", "지도에지정한출발지에서출발", "지정한출발지에서출발"}:
        return "TRAVEL"
    if re.fullmatch(r"\d{4}년\d{1,2}월\d{1,2}일(?:경기)?코스", value):
        return "ITINERARY"
    return None


PREFIX = r"(?:(?:경기|야구|직관)(?:전|후)(?:에)?)?(?:구장(?:밖|외부)(?:의)?)?"
EATING = r"먹(?:고(?:싶어(?:요)?)?|기|은(?:뒤|후|다음)(?:에)?)"
ACTION = (r"(?:(?:에서|에|으로|로|을|를)?(?P<action>식사(?:하고|하기|한뒤)?|갔다가|가기|들르기|들러|들렀다가|방문|방문하기|커피한잔|"
          + EATING + r"))?")
PATTERN = re.compile(PREFIX + r"(?P<term>" + "|".join(sorted(TERMS, key=len, reverse=True))
                     + r")(?P<kind>식당|음식점|카페|브랜드|매장|메뉴)?" + ACTION, re.I)


def parse(conditions):
    result = []
    for text in conditions:
        # No substring/keyword classification: exclusions, alternatives, menus,
        # modifiers and unknown wording must survive through the semantic path.
        normalized = re.sub(r"\s+", "", text.strip().rstrip(".!"))
        if course_scope(text):
            # Routing and requested visits apply these instructions; they are
            # not venue properties. Distance/accessibility limits do not match.
            continue
        match = PATTERN.fullmatch(normalized)
        if not match:
            return None
        raw = next(k for k in TERMS if k.casefold() == match["term"].casefold())
        term, kind = TERMS[raw], match["kind"]
        action = match["action"] or ""
        # An activity outside the proven category is a service/menu constraint.
        if (action == "커피한잔" and raw not in BRANDS and term != "카페"
                or action.startswith(("식사", "먹")) and (raw in BRANDS or term == "카페")):
            return None
        if kind and ((raw in CUISINES and kind not in ("식당", "음식점"))
                     or (raw in BRANDS and kind not in ("카페", "브랜드", "매장"))
                     or (raw in MENUS and kind not in ("식당", "음식점", "메뉴"))
                     or raw in GENERIC):
            return None
        if term not in result:
            result.append(term)
    return result


def matches(place, term):
    detail = str(place.get("detail") or place.get("category_name") or "")
    parts = [re.sub(r"\s+", "", p) for p in detail.split(">")]
    if term in set(CUISINES.values()):
        return "음식점" in parts and term in parts
    if term == "카페":
        return "카페" in parts or "커피전문점" in parts
    if term == "식당":
        return "음식점" in parts and not any(p in parts for p in ("카페", "커피전문점", "술집"))
    name = str(place.get("name") or place.get("place_name") or "").strip()
    brand = r"메가(?:MGC)?커피" if term == "메가MGC커피" else re.escape(term)
    # Require the actual brand boundary and cafe classification; a similarly
    # named restaurant or a brand mentioned in an address is not a match.
    return matches(place, "카페") and bool(re.fullmatch(brand + r"(?:\s+\S+점)?", name, re.I))


def simple_edit_matches(question, terms):
    """Only a complete, known replacement sentence can skip raw-text selection.

    An interpreted 'cafe' summary must not erase an unknown brand, menu modifier
    or another venue condition in the original request.
    """
    text = re.sub(r"[\s.!]+", "", question)
    target = "|".join(sorted(TERMS, key=len, reverse=True))
    source_menu = "|".join(MENUS)
    match = re.fullmatch(
        r"(?:식당이(?:좀)?별로(?:다|야))?"
        r"(?:(?:경기|야구|직관)(?:전|후))?"
        + r"(?:(?:(?:" + source_menu + r")?식당|카페|음식점)(?:만|은|는|을|를)?)?"
        + r"(?P<term>" + target + r")(?:가게|식당|음식점|카페|매장)?(?:으로|로)(?:만)?"
        + r"(?:바꿔(?:줘|주세요)|수정해(?:줘|주세요)|변경해(?:줘|주세요))", text)
    return bool(match and TERMS[match["term"]] in terms)


def choose(places, plan, question, *, verified=False):
    """None = unsupported; [] = an exact request with no catalog match."""
    conditions = plan.get("conditions") or []
    if question != "; ".join(conditions):
        return None  # Never let a lossy edit summary override the user's words.
    terms = parse([*conditions, *([plan["query"]] if plan.get("query") else [])])
    if terms is None or not terms and not conditions:
        return None
    if not terms:
        return [{**p, "reason": "요청한 코스의 방문지 후보예요."} for p in places]
    menus = [term for term in terms if term in MENUS.values()]
    if menus and not verified:
        from . import recommendation_policy
        if recommendation_policy.balanced():
            def hinted(p, term):
                if term not in menus:
                    return matches(p, term)
                if p.get("category") not in {"FOOD", "FOOD_OUT", "FOOD_IN"}:
                    return False
                name = re.sub(r"\s+", "", str(p.get("name", "")))
                detail = re.sub(r"\s+", "", str(p.get("detail", "")))
                # A specific specialty/name is useful recommendation evidence.
                # Generic Chinese/Japanese cuisine is insufficient for a dish.
                if term == "돈까스":
                    return bool(re.search(r"돈까스|돈카츠|돈가스|돈카쓰", name + detail))
                return bool(re.search(r"(?:짜장|자장)(?:면)?", name)
                            and not re.search(r"(?:짜장|자장)(?:밥|소스|떡볶이|라면|범벅|김밥|버거|피자)", name))
            candidates = [p for p in places if all(hinted(p, term) for term in terms)]
            if candidates:
                return [{**p, "catalogMenuHint": menus,
                         "reason": "·".join(menus) + " 관련 상호·전문 업종에 맞는 후보예요. 현재 메뉴는 매장 링크에서 확인해 주세요."}
                        for p in candidates]
        return None  # Menu names are never evidence of actual menu availability.
    def satisfies(place, term):
        if term not in menus:
            return matches(place, term)
        return any(c.get("term") == term and c.get("attribute") == "menu"
                   and c.get("intent") == "required" and c.get("status") == "match"
                   for c in place.get("conditionChecks", []))
    from .evidence_memory import reason
    return [{**p, "reason": "이름·업종에서 요청한 분류를 확인했어요."}
            if not menus else {**p, "reason": reason(p)}
            for p in places if all(satisfies(p, term) for term in terms)]
