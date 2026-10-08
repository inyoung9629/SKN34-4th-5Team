"""대화 체크포인트에만 보관하는 코스 조건. 공개 응답에는 표시용 목록만 투영한다."""
from copy import deepcopy

from langchain_core.messages import HumanMessage, ToolMessage


def empty():
    return {"conditions": [], "rejected": [], "locked": []}


def core(value):
    value = value or {}
    return {key: deepcopy(value.get(key, [])) for key in ("conditions", "rejected", "locked")}


def restore(messages, turns):
    """실패·취소·삭제된 답변은 기억하지 않는다. 질문 수정 시 잘린 prefix만 읽는다."""
    from .conversation_scope import scoped_messages
    result, completed = {}, False
    for message in scoped_messages(messages, turns):
        if isinstance(message, HumanMessage):
            turn = turns.get(message.id, {})
            completed = turn.get("status") == "completed" and not turn.get("answer_deleted")
        elif completed:
            from llm.v2.agent.course_output import course_artifacts
            for artifact in course_artifacts([message]):
                if not isinstance(artifact.get("course_memory"), dict):
                    continue
                previous = result.get("current")
                result = deepcopy(artifact["course_memory"])
                course = artifact.get("course")
                if isinstance(course, dict) and course.get("places") and result.get("current") and "writerState" not in result["current"]:
                    from .writer_state import for_result
                    same = previous if course.get("edit") and (previous or {}).get("stadiumCode") == course.get("stadiumCode") else None
                    result["current"]["writerState"] = for_result(course, same, result.get("origin"))
    return result


def identity(place):
    return {k: place[k] for k in ("placeId", "name", "lat", "lng") if k in place}


def same(a, b):
    if a.get("placeId") and b.get("placeId") and a["placeId"] == b["placeId"]:
        return True
    if any(str(p.get("placeId", "")).startswith("stadium-facility:") for p in (a, b)):
        # 수집 매장의 핀은 구장 옆 표시 좌표다. 다른 블럭의 동명 매장을 거리로 합치지 않는다.
        return False
    return a.get("name") == b.get("name") and all(abs(a.get(k, 0) - b.get(k, 1)) < .0005 for k in ("lat", "lng"))


def remember_place(items, place):
    if not any(same(p, place) for p in items):
        items.append(identity(place))
    return items[-100:]


def public(value):
    if not isinstance(value, dict):
        return None
    return {"conditions": [p["text"][:160] for p in value.get("conditions", [])[:20]],
            "lockedPlaces": [p["name"][:255] for p in value.get("locked", [])[:12]],
            "rejectedPlaces": [p["name"][:255] for p in value.get("rejected", [])[-100:]]}


def fingerprint(current, origin=None):
    # 시간표·지도 번호는 재계산되므로 비교에서 제외한다. 수동 변경이 있으면 undo를 막는다.
    return {"stadiumCode": current.get("stadiumCode"), "origin": {k: origin[k] for k in ("lat", "lng")} if origin else None,
            "travelMode": current.get("travelMode", "walk"), "legModes": current.get("legModes", {}),
            "progress": current.get("progress", {}),
            "places": [{**{k: p[k] for k in ("name", "lat", "lng", "category")}, **{k: p[k] for k in ("stayOverride", "completed") if k in p}}
                       for p in current.get("places", [])]}


def snapshot(result):
    places = deepcopy(result["places"])
    for i, place in enumerate(places):
        place.setdefault("visitId", f"course:{i}:{place.get('placeId', place['name'])}")
        place["label"] = str(i + 1)
    return {"places": places, "stadiumCode": result.get("stadiumCode"),
            **({"writerState": deepcopy(result["writerState"])} if result.get("writerState") else {}),
            "travelMode": result.get("travel", {}).get("mode", "walk"), "legModes": result.get("legModes", {}),
            **({"game": result["game"]} if result.get("game") else {}),
            **({"progress": deepcopy(result["progress"])} if result.get("progress") else {})}


def attach(result, value, current=None, origin=None, before=None, undo=True):
    state = deepcopy(value)
    if result.get("places"):
        from .writer_state import for_result
        result["writerState"] = for_result(result, current, origin)
        state["current"] = snapshot(result)
        state["origin"] = result.get("origin", origin)
        if undo and current:
            state["undo"] = {"before": deepcopy(current), "origin": origin, "memory": core(before),
                             "after": fingerprint(state["current"], state["origin"])}
    result["courseMemory"] = state
    return result


def relevant(value, category):
    return [p["text"] for p in value.get("conditions", []) if p["scope"] in ("ALL", category)]


def planning_text(value):
    return "\n".join(p["text"] for p in value.get("conditions", []))


def planning_slots(question, value):
    from . import slots
    result = slots.parse(f"{planning_text(value)}\n{question}")
    # 취향 문구를 활동 목록에 이어 붙이면 식사·카페가 중복 방문으로 해석된다.
    # 방문 구성은 현재 질문을 우선하고, 미지정일 때만 저장된 방문 구성을 사용한다.
    activities = slots.parse(question)
    if activities["itinerary"] is None:
        itinerary = next((p["text"] for p in reversed(value.get("conditions", [])) if p["scope"] == "ITINERARY"), None)
        if itinerary:
            activities = slots.parse(itinerary)
    for key in ("itinerary", "after_kinds", "scope", "extra_phases", "extras"):
        result[key] = activities[key]
    return result
