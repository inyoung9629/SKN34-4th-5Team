"""Complete, unambiguous replacement commands; unsupported text uses the LLM.

This only removes redundant interpretation. Candidate discovery, source checks,
locked/completed visits, atomic application and timeline rebuilding stay shared.
"""
import re

from llm.place_choices import compact, selected_place
from . import catalog_conditions as catalog

CATEGORIES = {"식당": "FOOD", "음식점": "FOOD", "식사": "FOOD", "카페": "CAFE",
              "커피": "CAFE", "산책": "WALK", "공원": "WALK", "숙박": "STAY", "숙소": "STAY"}


def _alternatives(values):
    return "|".join(re.escape(v) for v in sorted(values, key=len, reverse=True))


KINDS = _alternatives(CATEGORIES)
DESTINATIONS = _alternatives({**catalog.TERMS, **CATEGORIES})
REPLACE = re.compile(
    r"(?:(?P<source>" + KINDS + r")(?:은|는|을|를|만|말고|대신))?"
    r"(?P<destination>" + DESTINATIONS + r")(?P<kind>가게|매장|식당|카페)?(?:으로|로)(?:만)?"
    r"(?:일정)?(?:바꿔(?:줘|주세요)|수정해(?:줘|주세요)|변경해(?:줘|주세요))", re.I)
PREFERENCE = re.compile(
    r"(?:난|나는|저는)?(?:카페|커피)(?:는|은)?(?:무조건|항상)"
    r"(?P<brand>" + _alternatives(catalog.BRANDS) + r")(?:로|에)?(?:가야해(?:요)?|갈거야|가고싶어(?:요)?)", re.I)


def _category(place):
    value = place.get("category")
    return "FOOD" if value in {"FOOD_IN", "FOOD_OUT"} else value


CHOICE_QUESTION = re.compile(
    r"(?P<destination>" + DESTINATIONS + r")(?:가게|매장|식당|카페)?(?:으로|로)바꿀"
    r"(?:" + KINDS + r"|곳|장소)(?:을|를)?골라(?:줘|주세요)[.!?]?", re.I)


def interpret(question, current, saved=None, history=None):
    if plan := _interpret(question, current, saved):
        return plan
    if (saved or {}).get("pending_selection") or any(t.get("planning_questions") for t in (history or [])[-1:]):
        return _pending_choice(question, current, saved or {}, history or [])
    # A choice is a continuation of the immediately preceding request, never a
    # replacement for its conditions. Unknown clauses still need semantics.
    turns = history or []
    last = next((i for i in range(len(turns) - 1, -1, -1) if turns[i].get('role') == 'user'), None)
    if last is None:
        return None
    assistants = [str(t.get('content', '')) for t in turns[last + 1:] if t.get('role') == 'assistant']
    if not assistants:
        return None
    prefix, separator, choice = question.partition(':')
    prompts = [prefix] if separator else [line.partition(':')[0] for line in assistants[-1].splitlines()]
    prompt = next((p for p in prompts if CHOICE_QUESTION.fullmatch(compact(p))), None)
    if not prompt or compact(prompt) not in compact(assistants[-1]):
        return None
    target = selected_place(choice if separator else question, current.get('places', []))
    if target is None:
        return None
    plan = _interpret(str(turns[last].get('content', '')), current, saved, target=target)
    if not plan or plan['operation'] != 'replace':
        return None
    destination = CHOICE_QUESTION.fullmatch(compact(prompt))['destination']
    canonical = next(k for k in {**catalog.TERMS, **CATEGORIES} if k.casefold() == destination.casefold())
    # Do not bind a stale/different question to an otherwise valid command.
    if canonical in catalog.TERMS:
        if plan['query'] != catalog.TERMS[canonical]:
            return None
    elif (plan['category'] or _category(target)) != CATEGORIES[canonical]:
        return None
    return plan


def _snapshot(current):
    from .memory import fingerprint
    return {**fingerprint(current), "game": {k: (current.get("game") or {}).get(k) for k in ("date", "time")},
            "visits": [{k: p.get(k) for k in ("visitId", "placeId", "label", "phase")} for p in current.get("places", [])]}


def pending_selection(question, current, saved):
    """Only a fully parsed single replacement can await a visit selection."""
    targets = []
    for place in current.get("places", []):
        plan = _interpret(question, current, saved, target=place)
        if plan and plan["operation"] == "replace":
            targets.append(place["visitId"])
    if len(targets) < 2:
        return None
    return {"request": question, "snapshot": _snapshot(current), "targets": targets}


def _pending_choice(question, current, saved, history):
    pending = saved.get("pending_selection")
    if not isinstance(pending, dict) or pending.get("snapshot") != _snapshot(current):
        return None
    last = next((i for i in range(len(history)-1, -1, -1) if history[i].get("role") == "user"), None)
    if last is None or history[last].get("content") != pending.get("request"):
        return None
    assistants = [t for t in history[last+1:] if t.get("role") == "assistant"]
    questions = assistants[-1].get("planning_questions", []) if assistants else []
    if len(questions) != 1:
        return None
    row = questions[0]
    places = current.get("places", [])
    offered = [selected_place(c, places) for c in row.get("choices", [])]
    if len(offered) < 2 or any(p is None or p.get("visitId") not in pending["targets"] for p in offered):
        return None
    prefix, separator, answer = question.partition(":")
    if separator and compact(prefix) != compact(row.get("question", "")):
        return None
    target = selected_place(answer if separator else question, places)
    if target is None or target["visitId"] not in {p["visitId"] for p in offered}:
        return None
    # Reconsume the complete original request and current saved preferences.
    # Extra free-text conditions or changed maps use the semantic interpreter.
    plan = _interpret(pending["request"], current, saved, target=target)
    return plan if plan and plan["operation"] == "replace" else None


def _interpret(question, current, saved=None, *, target=None):
    from .editing import EditAction, EditPlan

    # Consume every clause; no keyword-only guesses or loss of trailing facts.
    clauses = [re.sub(r"\s+", "", s) for s in re.split(r"[.!?\n]+|\s+그리고\s+", question) if s.strip()]
    if not clauses or len(clauses) > 6:
        return None
    actions, preferences, rejected_category = [], [], None
    for clause in clauses:
        if preference := PREFERENCE.fullmatch(clause):
            brand = next(v for k, v in catalog.BRANDS.items() if k.casefold() == preference['brand'].casefold())
            preferences.append({"scope": "CAFE", "text": brand + " 브랜드"})
            continue
        if complaint := re.fullmatch(r"(?P<kind>" + KINDS + r")이(?:좀)?별로(?:다|야)", clause):
            if rejected_category is not None or actions:
                return None
            rejected_category = CATEGORIES[complaint['kind']]
            continue
        match = REPLACE.fullmatch(clause)
        if not match:
            return None
        raw = next(k for k in {**catalog.TERMS, **CATEGORIES} if k.casefold() == match['destination'].casefold())
        if match['kind'] and not (
            raw in catalog.BRANDS and match['kind'] in {'가게', '매장', '카페'}
            or (raw in catalog.MENUS or raw in catalog.CUISINES) and match['kind'] in {'가게', '매장', '식당'}
        ):
            return None
        term = catalog.TERMS.get(raw)
        category = ("CAFE" if raw in catalog.BRANDS else "FOOD" if raw in catalog.CUISINES or raw in catalog.MENUS
                    else CATEGORIES.get(raw))
        source = CATEGORIES[match['source']] if match['source'] else category
        targets = [p for p in current.get('places', []) if _category(p) == source]
        if target is not None:
            targets = [p for p in targets if p is target]
        if len(targets) != 1 or not targets[0].get('visitId'):
            return None
        if rejected_category and rejected_category != source:
            return None
        if any(targets[0]['visitId'] in a['targets'] for a in actions):
            return None  # A second edit to the same visit can depend on the first.
        conditions = [term + " 브랜드" if raw in catalog.BRANDS else term] if term else []
        action = EditAction(operation='replace', targets=[targets[0]['visitId']], reference='', position='after',
                            query=term if raw in catalog.BRANDS or raw in catalog.CUISINES or raw in catalog.MENUS else '',
                            category=category if category != source else '', conditions=conditions,
                            clarification='', preferences=[], forget_conditions=[]).model_dump()
        actions.append(action)
    if not actions:
        return None
    # Conflicting declarations and previously saved brand rules need semantics.
    for preference in preferences:
        brand = catalog.parse([preference['text']])[0]
        cafe_actions = [a for a in actions if a['category'] == 'CAFE' or any(
            p['visitId'] in a['targets'] and _category(p) == 'CAFE' and not a['category'] for p in current['places'])]
        if not cafe_actions or any(brand not in (catalog.parse(a['conditions']) or []) for a in cafe_actions):
            return None
    for action in actions:
        category = action['category'] or next(_category(p) for p in current['places'] if p['visitId'] in action['targets'])
        wanted = set(catalog.parse(action['conditions']) or []) & set(catalog.BRANDS.values())
        if wanted:
            for condition in (saved or {}).get('conditions', []):
                if condition.get('scope') not in {category, 'ALL'}:
                    continue
                terms = catalog.parse([condition.get('text', '')])
                if terms is None or (set(terms) & set(catalog.BRANDS.values())) - wanted:
                    return None
    primary = actions[0] if len(actions) == 1 else {**actions[0], 'operation': 'batch', 'targets': [], 'query': '', 'category': '', 'conditions': []}
    result = EditPlan(**{**primary, 'preferences': preferences}, course_only=True,
                      actions=actions if len(actions) > 1 else []).model_dump()
    result['_literal_edit'] = True  # Server-created proof: the entire text was consumed.
    return result
