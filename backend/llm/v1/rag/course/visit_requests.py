"""새 코스의 방문별 검색 조건. 같은 업종이어도 메뉴와 내부 허용을 공유하지 않는다."""
import re
from copy import deepcopy

from . import slots, venue_policy
from .signature_intent import use_default


DESSERT = re.compile(r"츄러스|추러스|아이스크림|빙수|와플|크로플|도넛|디저트")


def normalize(question, visits):
    if visits is None:
        return None
    result = []
    for raw in visits:
        if not isinstance(raw, dict):
            return None
        expression = raw.get("expression", "").strip()
        if (not expression or expression not in question or raw.get("kind") not in slots.ACTIVITY_LABEL
                or raw.get("phase") not in ("BEFORE", "AFTER")):
            return None
        if (raw["kind"] == "SPOT" and re.search(r"구장|경기장|야구장", expression)
                and not re.search(r"투어|박물관|기념관|관광", expression)):
            continue
        kind = raw["kind"]
        # 수집 목록의 디저트 분류와 맞춘다. '먹는다'만으로 식사로 바꾸지 않는다.
        if kind == "FOOD" and DESSERT.search(expression):
            kind = "CAFE"
        internal = venue_policy.mentions_internal(expression) and not venue_policy._NEGATIVE.search(expression)
        phase = raw["phase"]
        if internal and kind in ("FOOD", "CAFE") and not re.search(r"전\s*(?:과|/|·)?\s*후", question):
            # Stadium snacks connect to kickoff by default. Preserve an explicit
            # after-game scope, including a marker before this visit expression.
            prefix = question[:question.index(expression) + len(expression)]
            before = max((m.start() for m in slots.BEFORE_ONLY.finditer(prefix)), default=-1)
            after = max((m.start() for m in slots.AFTER_ONLY.finditer(prefix)), default=-1)
            phase = "AFTER" if after > before else "BEFORE"
        signature_default = internal and use_default(expression, kind, raw.get("query"), raw.get("conditions"), raw.get("signature_default"))
        conditions = [] if signature_default else list(raw.get("conditions") or [])
        if not internal and re.search(r"구장\s*(?:밖|외부)", expression):
            # The full expression and visit policy already require an external
            # venue. A duplicate scope token is not a separate review feature.
            # Keep modifiers, exclusions and any other location constraints.
            conditions = [c for c in conditions if not re.fullmatch(r"구장\s*(?:밖|외부)", c.strip())]
        if kind in ("FOOD", "CAFE"):
            conditions = list(dict.fromkeys([*conditions, expression]))
        result.append({**raw, "phase": phase, "id": f"{phase}:{len(result)}", "kind": kind,
                       "expression": expression, "conditions": conditions,
                       "query": "" if signature_default else raw.get("query") or "",
                       "signature_default": signature_default, "internal": internal})
    return result


def at(sl, phase, index):
    visits = [v for v in sl.get("requested_visits", []) if v["phase"] == phase
              and not (v["kind"] == "BAR" and any(w in sl.get("ban", [])
                       for w in ("술집", "호프", "포장마차", "이자카야")))]
    return visits[index] if index < len(visits) else None


def matches(place, visit):
    return visit is None or visit["id"] in place.get("_requested_visit_ids", [])


def local_slots(sl, visit):
    if visit is None:
        return sl
    return {**sl, "prefs": slots.preferences(visit["expression"]),
            "visit_query": visit.get("query", ""),
            "cheap_cafe": bool(re.search(r"저가|저렴|싼|가성비", visit["expression"]))}


class Selector:
    def __init__(self, visits, state, code):
        self.visits, self.state, self.code = visits, state or {}, code
        self.cache = {}

    def policy(self, visit):
        requests = [{"category": visit["kind"], "expression": visit["expression"],
                     "query": visit.get("query", ""), "conditions": visit.get("conditions", []),
                     "signature_default": visit.get("signature_default", False)}] if visit["internal"] else []
        return venue_policy.request_policy(visit["expression"], self.code, requests)

    def conditions(self, visit):
        # 요청 방문의 업종 조건은 그 방문에 저장된다. 전체 공통 조건만 합친다.
        return list(dict.fromkeys([p["text"] for p in self.state.get("conditions", []) if p["scope"] == "ALL"]
                                  + visit["conditions"]))

    def for_visit(self, items, visit, *, center=None):
        from . import agent
        from .evidence_memory import local_candidates
        state = deepcopy(self.state)
        category = agent.CAT_LABEL.get(agent.STEP_CATEGORY[visit["kind"]], visit["kind"])
        state["conditions"] = [{"scope": category, "text": text} for text in self.conditions(visit)]
        with self.policy(visit):
            candidates = [p for p in venue_policy.filter_candidates(items) if agent._step_matches(visit["kind"], p)]
            with local_candidates(center):
                accepted = agent.remembered_candidates(candidates, state, self.cache)
        return [{**p, "_requested_visit_ids": list(dict.fromkeys([*p.get("_requested_visit_ids", []), visit["id"]]))}
                for p in accepted]

    def __call__(self, items):
        from .route_ranking import identity
        result = {}
        for visit in self.visits:
            for p in self.for_visit(items, visit):
                key = identity(p)
                old = result.get(key, {})
                result[key] = {**p, "_requested_visit_ids": list(dict.fromkeys(
                    [*old.get("_requested_visit_ids", []), *p["_requested_visit_ids"]]))}
        return list(result.values())

    def search(self, anchor, *, origin=None):
        from . import agent, editing, place_quality
        found, searches = [], []
        for visit in self.visits:
            if visit["kind"] not in ("FOOD", "CAFE", "BAR"):
                continue
            query = visit.get("query", "")
            if not query and not visit["internal"]:
                from .evidence_memory import requirements
                terms = [r.term for r in requirements(self.conditions(visit))
                         if r.attribute in ("menu", "cuisine") and r.intent == "required"] if self.conditions(visit) else []
                query = terms[0] if terms else "술집" if visit["kind"] == "BAR" else ""
            searches.append((visit, query))
        # Investigate explicit menus/brands before generic visits spend the shared
        # lookup budget. This changes search order, never the requested itinerary.
        generic = place_quality.GENERIC_QUERIES
        external = [(v, q) for v, q in searches if not v["internal"]]
        explicit = [(v, q) for v, q in external if q.strip() not in generic]
        # With an origin, the first visit is searched again at its actual route
        # position. Defer this one investigation instead of researching the
        # stadium-wide seed and then the origin separately. Multiple explicit
        # visits retain their existing menu-first budget ordering.
        deferred = (external[0][0]["id"] if origin and external
                    and external[0][0]["phase"] == "BEFORE" and len(explicit) == 1
                    and explicit[0][0]["id"] == external[0][0]["id"] else None)
        searches.sort(key=lambda item: (item[0]["internal"], item[1].strip() in generic))
        for visit, query in searches:
            # Generic visits are researched at their actual preceding stop below.
            # A stadium-wide seed search must not exhaust the budget first.
            with self.policy(visit), place_quality.research_policy(
                    not origin or query.strip() not in generic and visit["id"] != deferred):
                category = "FOOD" if visit["kind"] in ("FOOD", "BAR") else visit["kind"]
                focus = origin if origin and visit["phase"] == "BEFORE" else anchor
                rows = editing.candidates({**focus, "category": category},
                                          anchor, {"query": query, "conditions": self.conditions(visit)}, [])
            for p in rows:
                # 검색어 적중만으로 일반 음식점의 실제 업종을 술집으로 바꾸지 않는다.
                if visit["kind"] == "BAR" and not any(w in p.get("detail", "") for w in agent.timeline.BAR_WORDS):
                    continue
                found.append({**p, "category": agent.STEP_CATEGORY[visit["kind"]], "dist": .25,
                              "distance": round(agent.geo._dist(p, anchor)),
                              "doc_id": p.get("placeUrl") or f"kakao:{p['placeId']}",
                              "_menu_queries": [query] if query else []})
        return found
