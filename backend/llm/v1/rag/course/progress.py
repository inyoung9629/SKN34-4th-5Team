"""사용자가 알려 준 방문 진행 상황. 완료 구간은 고정하고 남은 시간만 순방향 계산한다."""
import re
from copy import deepcopy

from . import timeline


class ProgressError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 검증 메시지."""


def clean(value):
    if not isinstance(value, dict) or not value or set(value) - {"startMinute", "gameEndMinute"}:
        return None
    if any(isinstance(n, bool) or not isinstance(n, int) or not 0 <= n < 2880 for n in value.values()):
        return None
    return dict(value)


def minute(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:익일 )?(?:[01]\d|2[0-3]):[0-5]\d", value):
        return None
    return timeline.to_min(value.removeprefix("익일 ")) + (1440 if value.startswith("익일 ") else 0)


def completed_count(places):
    count = 0
    while count < len(places) and places[count].get("completed") is True:
        count += 1
    if any(p.get("completed") for p in places[count:]):
        raise ProgressError("완료한 장소 앞에 미방문 장소가 있어요. 앞선 방문지의 완료 여부를 먼저 알려 주세요.")
    return count


def protect(before, after):
    count = completed_count(before)
    if after[:count] != before[:count]:
        raise ProgressError("이미 방문을 완료한 장소와 순서는 유지해요. 남은 장소만 수정해 주세요. 완료 표시를 잘못했다면 직전 수정 취소를 요청해 주세요.")


def apply(places, current, tl, plan=None, inbound=0):
    """baseline 시간표에 진행 상황을 적용한다. 날짜 기준 분 단위로 자정 이후도 보존한다."""
    plan = plan or {}
    op = plan.get("operation")
    state = deepcopy(current.get("progress") or {})
    old = {p["visitId"]: p for p in current["places"]}
    rows = [{**r, "time": minute(r["time"]), "until": minute(r["until"])} for r in tl["rows"]]
    count = completed_count(places)
    g = next((i for i, p in enumerate(places) if p["category"] == "STADIUM"), None)
    start, end = minute(tl["gameStart"]), minute(tl["gameEnd"])
    end = state.get("gameEndMinute", end)
    notice = ""

    # 완료 구간 및 그 출발 기준은 다음 카페 교체/순서 변경에서도 움직이지 않는다.
    for i in range(count):
        p = places[i]
        previous = old.get(p["visitId"], p)
        rows[i]["time"] = minute(previous.get("time")) if minute(previous.get("time")) is not None else rows[i]["time"]
        rows[i]["until"] = minute(previous.get("until")) if minute(previous.get("until")) is not None else rows[i]["time"] + rows[i]["stayMin"]
        if i == g and minute(previous.get("until")) is None:
            rows[i]["until"] = end

    if op == "complete":
        ids = plan.get("targets", [])
        selected = [i for i, p in enumerate(places) if p["visitId"] in ids]
        if not selected or len(selected) != len(set(ids)):
            raise ProgressError("방문을 마친 장소 이름이나 번호를 알려 주세요.")
        last = max(selected)
        if any(i not in selected for i in range(count, last + 1)):
            raise ProgressError("앞선 장소의 방문 여부가 아직 확인되지 않았어요. 이미 방문한 장소를 함께 알려 주시면 남은 일정만 계산할게요.")
        if last < count:
            raise ProgressError("해당 장소는 이미 방문 완료로 표시되어 있어요.")
        # 완료 직전 시간표를 기준으로 기록한다. 이전에 적용한 지연을 다시 역산하지 않는다.
        for i in range(count, last + 1):
            previous = old.get(places[i]["visitId"], {})
            arrival = minute(previous.get("time"))
            if arrival is not None:
                rows[i]["time"] = arrival
                rows[i]["until"] = minute(previous.get("until")) or (end if i == g else arrival + rows[i]["stayMin"])
            places[i]["completed"] = True
        actual = minute(plan.get("at_time"))
        if actual is not None:
            if actual < rows[last]["time"]:
                raise ProgressError("완료 시각이 기존 도착 시각보다 빨라요. 실제 도착·완료 시각을 다시 확인해 주세요.")
            rows[last]["until"] = actual
        else:
            notice = "완료 시각을 따로 말씀하지 않아 기존 시간표의 방문 종료 시각부터 계산했어요. 실제 출발 시각이 다르면 알려 주세요."
        count = last + 1
        state["startMinute"] = rows[last]["until"]
        if last == g:
            state["gameEndMinute"] = end = rows[last]["until"]

    if op in ("delay", "game_delay"):
        delta, actual = plan.get("delay_minutes", 0), minute(plan.get("at_time"))
        if actual is None and (isinstance(delta, bool) or not isinstance(delta, int) or not 1 <= delta <= 720):
            raise ProgressError("얼마나 늦어졌는지 분 단위로, 또는 새 출발/경기 종료 시각을 알려 주세요. 확인되면 남은 일정만 조정할게요.")
        if op == "game_delay":
            if g is None or places[g].get("completed"):
                raise ProgressError("진행 중인 경기의 종료 시각을 바꾸는 요청이에요. 경기 관람을 이미 마쳤다면 남은 일정의 출발 시각을 알려 주세요.")
            end = actual if actual is not None else end + delta
            if end < start:
                raise ProgressError("경기 종료 시각은 경기 시작 이후여야 해요. 자정을 넘었다면 '익일'을 함께 알려 주세요.")
            state["gameEndMinute"] = end
        else:
            if count == len(places):
                raise ProgressError("모든 방문을 완료해 조정할 남은 일정이 없어요.")
            previous = old.get(places[count]["visitId"], {})
            base = state.get("startMinute", rows[count - 1]["until"] if count else minute(previous.get("time")))
            base = rows[count]["time"] if base is None else base
            state["startMinute"] = (actual + (inbound if count == 0 else 0)) if actual is not None else base + delta

    if state and clean(state) is None:
        raise ProgressError("이틀 범위를 넘는 시간 변경이에요. 방문 날짜와 시각을 다시 알려 주세요.")
    cursor = state.get("startMinute")
    if count and cursor is None:
        cursor = rows[count - 1]["until"]
    for i in range(count, len(rows)):
        row = rows[i]
        if i == g:
            arrival_target = tl.get("gameArrivalMinute", start - timeline.ENTER_BEFORE_MIN)
            row["time"] = max(arrival_target, cursor + (rows[i - 1]["legMin"] if i else 0)) if cursor is not None else row["time"]
            row["until"] = max(end, row["time"])
            cursor = row["until"]
        elif g is not None and i > g:
            row["time"] = cursor + (rows[i - 1]["legMin"] if i else 0)
            row["until"] = row["time"] + row["stayMin"]
            cursor = row["until"]
        elif cursor is not None:
            row["time"] = cursor + (rows[i - 1]["legMin"] if i else 0)
            row["until"] = row["time"] + row["stayMin"]
            cursor = row["until"]
        elif op == "game_delay":
            previous = old.get(places[i]["visitId"], {})
            if minute(previous.get("time")) is not None:
                row["time"] = minute(previous["time"])
                row["until"] = minute(previous.get("until")) or row["time"] + row["stayMin"]
    warning = ""
    if g is not None and not places[g].get("completed"):
        # 먼저 끝내기 어려워지는 방문을 명시한다. 장소를 임의로 삭제하지 않는다.
        first = next((i for i in range(count, g) if rows[i]["until"] + sum(r["legMin"] for r in rows[i:g]) > start), None)
        if first is not None:
            warning = f"변경된 시간 기준으로 {first + 1}번째 {places[first]['name']}부터는 경기 시작 전에 방문을 마치기 어려워요. 장소는 유지했으니 체류시간을 줄이거나 해당 장소를 빼 달라고 요청해 주세요."
        elif rows[g]["time"] > start:
            warning = "변경된 출발 시각으로는 경기 시작 뒤에 구장에 도착할 것으로 예상돼요."
    if any(r["time"] < 0 or r["until"] >= 2880 for r in rows):
        raise ProgressError("변경한 일정이 이틀 범위를 벗어나요. 방문 날짜와 시각을 다시 알려 주세요.")
    tl = {**tl, "rows": [{**r, "time": timeline.to_hhmm(r["time"]), "until": timeline.to_hhmm(r["until"])} for r in rows],
          "gameEnd": timeline.to_hhmm(end), "startTime": timeline.to_hhmm(rows[0]["time"]),
          "endTime": timeline.to_hhmm(rows[-1]["until"]), "totalMin": rows[-1]["until"] - rows[0]["time"]}
    return tl, state, warning, notice
