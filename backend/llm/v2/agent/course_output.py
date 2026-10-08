"""지도에 필요한 코스 필드만 공개한다. 도구 원본·검색 자료·내부 상태는 포함하지 않는다."""
import math
import re
from urllib.parse import urlsplit, urlunsplit

PHASES = {"BEFORE", "GAME", "AFTER"}
CATEGORIES = {"FOOD", "CAFE", "SPOT", "STADIUM", "STAY", "WALK", "INDOOR", "CONVENIENCE"}


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ""


def _source_url(value):
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() or ord(c) < 32 for c in value):
        return ""
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            return ""
        return urlunsplit(url._replace(scheme="https")) if url.hostname == "place.map.kakao.com" else value
    except ValueError:
        return ""


def course_artifacts(messages):
    """성공한 코스 결과만 읽는다. 기존 최상위와 전문 도구 내부 기록 모두 호환한다."""
    from langchain_core.messages import ToolMessage
    from llm.serializer.message import _artifact_messages
    for message in messages:
        if not isinstance(message, ToolMessage) or message.status == "error":
            continue
        if message.name == "plan_course" and isinstance(message.artifact, dict):
            yield message.artifact
        elif message.name == "ask_course":
            yield from course_artifacts(_artifact_messages(message.artifact))


def public_course(value):
    if not isinstance(value, dict) or not isinstance(value.get("places"), list):
        return None
    places = []
    for raw in value["places"][:12]:
        if not isinstance(raw, dict):
            continue
        name = _text(raw.get("name"), 255)
        lat, lng = raw.get("lat"), raw.get("lng")
        if not name or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (lat, lng)):
            continue
        phase, category = _text(raw.get("phase"), 20), _text(raw.get("category"), 20)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180) or phase not in PHASES or category not in CATEGORIES:
            continue
        place = {"name": name, "lat": lat, "lng": lng, "phase": phase, "category": category}
        if url := _source_url(raw.get("placeUrl")):
            place["placeUrl"] = url
        for key, limit in (("visitId", 255), ("placeId", 255), ("address", 500), ("reason", 120), ("time", 16), ("until", 16)):
            if text := _text(raw.get(key), limit):
                place[key] = text
        if raw.get("completed") is True:
            place["completed"] = True
        stay = raw.get("stayMin")
        if isinstance(stay, int) and not isinstance(stay, bool) and 0 <= stay <= 1440:
            place["stayMin"] = stay
        override = raw.get("stayOverride")
        if isinstance(override, int) and not isinstance(override, bool) and 1 <= override <= 720:
            place["stayOverride"] = override
        places.append(place)
    if not places or (not value.get("edit") and not any(p["category"] != "STADIUM" for p in places)):
        return None
    result = {"places": places}
    from llm.v1.rag.course.progress import clean
    if state := clean(value.get("progress")):
        result["progress"] = state
    if value.get("edit") is True:
        result["edit"] = True
    if "legModes" in value:
        from llm.v1.rag.course.edit_context import leg_modes
        if leg_modes(value["legModes"]):
            result["legModes"] = dict(value["legModes"])
    game = value.get("game")
    if (isinstance(game, dict) and isinstance(game.get("date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", game["date"])
            and isinstance(game.get("time"), str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", game["time"])):
        result["game"] = {"date": game["date"], "time": game["time"]}
    for key in ("origin", "entryPoint"):
        point = value.get(key)
        if isinstance(point, dict):
            lat, lng = point.get("lat"), point.get("lng")
            if all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in (lat, lng)) and -90 <= lat <= 90 and -180 <= lng <= 180:
                result[key] = {"lat": lat, "lng": lng}
                if name := _text(point.get("name"), 100):
                    result[key]["name"] = name
    if notice := _text(value.get("approachNotice"), 1000):
        result["approachNotice"] = notice
    if warning := _text(value.get("timeWarning"), 1000):
        result["timeWarning"] = warning
    if code := _text(value.get("stadiumCode"), 40):
        result["stadiumCode"] = code
    travel = value.get("travel")
    if isinstance(travel, dict):
        cleaned = {}
        if _text(travel.get("mode"), 20) in {"walk", "car", "transit"}:
            cleaned["mode"] = travel["mode"]
        for key, limit in (("label", 20), ("summary", 160)):
            if text := _text(travel.get(key), limit):
                cleaned[key] = text
        if isinstance(travel.get("lines"), list):
            cleaned["lines"] = [text for line in travel["lines"][:3] if (text := _text(line, 160))]
        result["travel"] = cleaned
    from llm.v1.rag.course.writer_state import clean as clean_writer
    if writer := clean_writer(value.get("writerState")):
        result["writerState"] = writer
    payload = value.get("coursePayload")
    if isinstance(payload, dict):
        result["coursePayload"] = {key: text for key, limit in (("title", 80), ("content", 12000))
                                   if (text := _text(payload.get(key), limit))}
    return result
