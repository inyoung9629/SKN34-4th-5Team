"""2026 gate policy transcribed from the user's 2026-09-28 reference image.

Evidence labels are claims in the supplied table, not our independent official
verification. Unknown days/teams/seasons are not silently filled with defaults.
"""
from datetime import datetime, timedelta

from .itinerary_request import KST

REFERENCE = "사용자 제공 구장별 입장(게이트 개방) 시간 표 · 2026-09-28"
# Calendar facts, including 2026 additions. No live calendar/holiday API needed.
# https://www.kasi.re.kr/kor/post/newsMaterial/32031
# https://openminister.mpm.go.kr/mpm/comm/newsPress/newsPressRelease/?boardId=bbs_0000000000000029&cntId=4250&mode=view
HOLIDAYS_2026 = frozenset((
    "01-01", "02-16", "02-17", "02-18", "03-01", "03-02", "05-01", "05-05", "05-24", "05-25",
    "06-03", "06-06", "07-17", "08-15", "08-17", "09-24", "09-25", "09-26", "10-03", "10-05", "10-09", "12-25",
))


def gate_window(anchor, request):
    start = datetime.fromisoformat(anchor["starts_at"]).astimezone(KST)
    result = {"status": "unknown", "opens_at": None, "source": REFERENCE,
              "evidence": "unconfirmed", "warning": "해당 경기의 실제 입장 공지는 별도 확인이 필요합니다."}
    if request.gate_open_at:
        if request.gate_open_at.date() != start.date() or request.gate_open_at >= start:
            return {**result, "warning": "입장 시각은 해당 경기 당일의 경기 시작 전 시각이어야 합니다."}
        return {**result, "status": "user_supplied", "opens_at": request.gate_open_at.isoformat(),
                "evidence": "user_supplied", "source": "사용자가 명시한 해당 경기 입장 시각"}
    if start.year != 2026:
        return {**result, "warning": "첨부표는 2026 시즌 기준입니다. 다른 시즌에는 자동 적용하지 않습니다."}
    code, home = anchor["stadium_code"], anchor.get("home_team")
    day, holiday = start.weekday(), start.strftime("%m-%d") in HOLIDAYS_2026
    minutes, evidence = None, "blog_reference"
    if request.entry_membership == "hanwha_full" and (code != "DAEJEON" or home not in ("HH", "HANWHA")):
        return {**result, "warning": "한화 FULL 멤버십 입장 규칙을 이 구장·홈팀에 적용할 수 없습니다."}
    if request.entry_membership == "ssg_season" and (code != "MUNHAK" or home not in ("SK", "SSG")):
        return {**result, "warning": "SSG 시즌권 입장 규칙을 이 구장·홈팀에 적용할 수 없습니다."}
    if request.special_game and code != "SAJIK":
        return {**result, "warning": "특별 경기의 입장 시각은 첨부표로 확정할 수 없습니다. 경기별 공지가 필요합니다."}
    if code == "DAEJEON":
        evidence = "official_claim_in_user_table"
        if day >= 5 or holiday:
            minutes = 180 if request.entry_membership == "hanwha_full" else 150
        elif day in (1, 2, 3, 4):
            minutes = 120
    elif code in ("SUWON", "GOCHEOK", "GWANGJU"):
        minutes = 120
        if code != "GWANGJU":
            evidence = "official_claim_in_user_table"
    elif code == "MUNHAK":
        # The supplied SSG/LG/Samsung rows do not define weekday-holiday exceptions.
        if not holiday or day >= 4:
            minutes = 120 if day >= 4 else 90
            if request.entry_membership == "ssg_season":
                minutes += 60
    elif code == "JAMSIL" and home in ("LG", "OB", "DOOSAN"):
        evidence = "estimated_lg_rule_for_doosan" if home != "LG" else evidence
        if not holiday or day >= 4:
            minutes = 120 if day >= 4 else 90 if day in (1, 2, 3) else None
    elif code == "DAEGU":
        if not holiday or day >= 5:
            minutes = 120 if day >= 5 else 90 if day in (1, 2, 3, 4) else None
    elif code == "SAJIK":
        minutes = 180 if request.special_game else 120 if day >= 4 or holiday else 60
    if minutes is None:
        return {**result, "warning": "첨부표에서 해당 홈팀·요일·공휴일의 개방 시각을 확인할 수 없습니다. NC 오픈프랙티스의 3시간 전 입장을 일반 입장으로 사용하지 않습니다."}
    return {**result, "status": "reference", "evidence": evidence, "minutes_before": minutes,
            "opens_at": (start - timedelta(minutes=minutes)).isoformat()}
