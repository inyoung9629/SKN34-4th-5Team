"""경기 전 코스를 삭제하지 않고 방문 가능한 순서를 계산한다. 외부·LLM 호출 없음."""
import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
_CLOCK = re.compile(r"(?P<period>오전|오후|아침|저녁|밤|새벽|낮)?\s*(?P<hour>\d{1,2})"
                    r"(?:시(?:\s*(?P<minute>\d{1,2})분|\s*(?P<half>반))?|:(?P<colon>\d{2}))")
_START = re.compile(r"\s*(?:에|쯤에?|경에?)?\s*(?:(?:첫\s*(?:장소|방문지)|구장|근처|역|코스|일정)"
                    r"(?:에|에서|을|를)?\s*)?(도착|출발|시작|식사|먹)")


def start_minute(question):
    """'오후 1시 도착', '13:30부터' 등 방문 시작 표현만 읽고 경기 시작 시각은 제외한다."""
    found = None
    for match in _CLOCK.finditer(question or ""):
        before, after = question[max(0, match.start() - 18):match.start()], question[match.end():]
        action = _START.match(after)
        if not action and not re.match(r"\s*부터", after):
            continue
        if re.search(r"경기(?:는|가|\s*시작(?:은|이)?)?\s*$", before) and (not action or action[1] == "시작"):
            continue
        if (re.search(r"(?:야구장|경기장|구장)(?:에는|에|엔|까지)?\s*$", before)
                or re.match(r"\s*(?:에|까지|에는|쯤)?\s*(?:야구장|경기장|구장)(?:에는|에|엔|까지)?\s*(?:도착|입장)", after)):
            continue  # Stadium arrival is the end of the pre-game course, not its start.
        hour = int(match["hour"])
        minute = 30 if match["half"] else int(match["minute"] or match["colon"] or 0)
        period = match["period"]
        if minute > 59 or hour > 23 or (period and not 1 <= hour <= 12):
            continue
        if period:
            hour %= 12
            if period in {"오후", "저녁", "밤", "낮"}:
                hour += 12
        found = hour * 60 + minute
    return found


def time_warning(course, lookup, tl, game, question, now, to_stadium_minutes):
    """첫 방문지부터 출발하는 낙관적 예상. 접두 코스를 방문하고 곧장 구장에 가도 늦는 첫 장소를 알린다.

    다음 경기 날짜를 미루거나 코스를 줄이지 않는다. 아직 방문 시작 시각을 모르는 미래 일정은
    역산한 권장 시간표를 사용한다. 기본 입장 권장 시각이 아니라 실제 경기 시작과 비교한다.
    """
    if not game:
        return ""
    g = next((i for i, step in enumerate(course) if step["phase"] == "GAME"), 0)
    if not g:
        return ""
    now = now.astimezone(KST)
    try:
        kickoff = datetime.fromisoformat(f"{game['date']}T{game['time']}").replace(tzinfo=KST)
    except (KeyError, TypeError, ValueError):
        return ""
    minute = start_minute(question)
    if minute is None and kickoff.date() != now.date():
        return ""
    start = kickoff.replace(hour=minute // 60, minute=minute % 60) if minute is not None else now
    if kickoff.date() == now.date():
        start = max(start, now)
    # 초 단위의 지연을 이전 분으로 버려 방문 가능하다고 잘못 판단하지 않는다.
    if start.second or start.microsecond:
        start = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    rows = tl["rows"]
    elapsed, first_late = 0, None
    for i in range(g):
        elapsed += rows[i]["stayMin"]
        direct = to_stadium_minutes[i]
        arrival = start + timedelta(minutes=elapsed + direct)
        if arrival > kickoff and first_late is None:
            first_late = i
        elapsed += rows[i]["legMin"]
    arrival = start + timedelta(minutes=elapsed)
    if first_late is None or arrival <= kickoff:
        return ""
    name = lookup[course[first_late]["key"]].get("name", "방문지")
    delay = math.ceil((arrival - kickoff).total_seconds() / 60)
    arrival_time = arrival.strftime("%H:%M") if arrival.date() == kickoff.date() else arrival.strftime("%m월 %d일 %H:%M")
    return (
        f"{start:%Y-%m-%d %H:%M}에 첫 방문지에서 시작한다고 계산하면, "
        f"경기 전 코스 중 방문 순서 {first_late + 1}번째 장소({name})부터는 경기 전에 방문하기 어려워요. "
        f"경기 전 장소를 모두 들르면 구장 도착은 {arrival_time} 예상으로, "
        f"{game['time']} 경기 시작보다 약 {delay}분 늦어요. "
        "코스와 지도 핀은 그대로 두었으니 해당 장소를 건너뛰거나 경기 후로 옮겨 주세요. "
        "표시된 시간표는 경기 시작에 맞춰 역산한 권장 시각이에요. "
        "이동·체류 시간은 예상이며 첫 장소까지의 이동과 대기 시간은 제외했어요."
    )
