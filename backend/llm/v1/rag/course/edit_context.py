"""화면의 현재 코스: 변경 요청에서 참조할 작은 스냅샷. 외부 입력은 모두 다시 검증한다."""
import re
from datetime import date

from llm.v2.agent.course_output import public_course

MODES = {"walk", "car", "transit"}
LEG_KEY = re.compile(r"-?\d{1,3}\.\d{6},-?\d{1,3}\.\d{6}>-?\d{1,3}\.\d{6},-?\d{1,3}\.\d{6}")


def leg_modes(value):
    return (isinstance(value, dict) and len(value) <= 144
            and all(isinstance(k, str) and LEG_KEY.fullmatch(k) and isinstance(v, str) and v in MODES for k, v in value.items()))


def validate(value):
    if not isinstance(value, dict) or not isinstance(value.get("places"), list) or len(value["places"]) > 12:
        raise ValueError("현재 코스에는 최대 12개 장소를 담을 수 있습니다.")
    if not value["places"] and "writerState" not in value:
        raise ValueError("빈 코스에는 현재 작성 상태가 필요합니다.")
    # 출발지만 찍은 화면/명시적으로 비운 화면을 과거 추천 코스로 대체하지 않는다.
    cleaned = public_course({**value, "edit": True}) if value["places"] else {"places": []}
    if not cleaned or len(cleaned["places"]) != len(value["places"]):
        raise ValueError("현재 코스의 장소와 좌표를 확인해 주세요.")
    if (not isinstance(value.get("stadiumCode"), str) or not 1 <= len(value["stadiumCode"]) <= 40
            or not isinstance(value.get("travelMode"), str) or value["travelMode"] not in MODES
            or not leg_modes(value.get("legModes", {}))):
        raise ValueError("현재 코스의 구장과 이동수단을 확인해 주세요.")
    seen = set()
    for raw, place in zip(value["places"], cleaned["places"]):
        for key, limit in (("visitId", 255), ("label", 20)):
            if not isinstance(raw.get(key), str) or not 1 <= len(raw[key]) <= limit:
                raise ValueError("현재 코스의 장소 번호를 확인해 주세요.")
            place[key] = raw[key]
        if place["visitId"] in seen:
            raise ValueError("현재 코스의 장소 번호가 중복되었습니다.")
        seen.add(place["visitId"])
    result = {"places": cleaned["places"], "stadiumCode": value["stadiumCode"],
              "travelMode": value["travelMode"], "legModes": dict(value.get("legModes", {}))}
    if "selectedPlace" in value:
        raw = value["selectedPlace"]
        selected = public_course({"places": [raw], "edit": True})
        if not isinstance(raw, dict) or not selected or any(not isinstance(raw.get(k), str) or not 1 <= len(raw[k]) <= limit
                               for k, limit in (("visitId", 255), ("label", 20))):
            raise ValueError("선택한 장소를 확인해 주세요.")
        result["selectedPlace"] = next((p.copy() for p in result["places"] if p["visitId"] == raw["visitId"]),
                                       {**selected["places"][0], "visitId": raw["visitId"], "label": raw["label"]})
    if "writerState" in value:
        from .writer_state import clean
        writer = clean(value["writerState"])
        if writer is None:
            raise ValueError("코스 제목·출발지·완성 상태를 확인해 주세요.")
        result["writerState"] = writer
    if "progress" in value:
        from .progress import clean
        state = clean(value["progress"])
        if state is None:
            raise ValueError("코스의 진행 시각을 확인해 주세요.")
        result["progress"] = state
    if game := value.get("game"):
        if not isinstance(game, dict) or not isinstance(game.get("date"), str) or not isinstance(game.get("time"), str):
            raise ValueError("경기 날짜와 시각을 확인해 주세요.")
        date.fromisoformat(game["date"])
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", game["time"]):
            raise ValueError("경기 시각을 확인해 주세요.")
        result["game"] = {"date": game["date"], "time": game["time"]}
    return result
