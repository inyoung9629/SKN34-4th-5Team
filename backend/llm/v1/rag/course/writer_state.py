"""코스 편집기의 메타데이터. 누락된 출발지와 명시적으로 지운 출발지를 구분한다."""
import math
from copy import deepcopy


def clean(value):
    if not isinstance(value, dict) or set(value) != {"title", "origin", "completed"}:
        return None
    if not isinstance(value["title"], str) or len(value["title"]) > 80 or not isinstance(value["completed"], bool):
        return None
    origin = value["origin"]
    if origin is not None:
        if not isinstance(origin, dict):
            return None
        for key, bound in (("lat", 90), ("lng", 180)):
            number = origin.get(key)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or abs(number) > bound:
                return None
        if "name" in origin and (not isinstance(origin["name"], str) or len(origin["name"]) > 100):
            return None
        origin = {key: origin[key] for key in ("lat", "lng", "name") if key in origin}
    return {"title": value["title"], "origin": deepcopy(origin), "completed": value["completed"]}


def for_result(result, current=None, origin=None):
    previous = clean(result.get("writerState")) or clean((current or {}).get("writerState"))
    title = previous["title"] if previous else (result.get("coursePayload") or {}).get("title", "")
    return {"title": title[:80], "origin": deepcopy(result.get("origin", origin)),
            "completed": previous["completed"] if previous else True}
