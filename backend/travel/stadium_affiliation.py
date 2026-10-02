"""Public-list review hints, not ticket-access or course-eligibility decisions.

Stadium branch name AND stadium address must match. A shared complex address is
not a shop coordinate. Daejeon is omitted because old/new parks share an address.
"""
import re

RULES = {
    "JAMSIL": (("잠실야구장",), ("올림픽로25",)),
    "GOCHEOK": (("고척스카이돔",), ("경인로430",)),
    "MUNHAK": (("문학야구장", "랜더스필드"), ("매소홀로618",)),
    "SUWON": (("위즈파크",), ("경수대로893",)),
    "DAEGU": (("라이온즈파크",), ("야구전설로1", "달구벌대로2950")),
    "GWANGJU": (("챔피언스필드",), ("서림로10",)),
    "SAJIK": (("사직야구장",), ("사직로45",)),
    "CHANGWON": (("NC파크", "엔씨파크"), ("삼호로63",)),
}


def stadium_affiliation_hint(code, row):
    if row.get("source") != "SBIZ" or row.get("kind") not in {"restaurant", "bar", "cafe", "convenience_store"}:
        return None
    names, addresses = RULES.get(code, ((), ()))
    name = re.sub(r"\s+", "", row["name"]).upper()
    address = re.sub(r"\s+", "", row["address"])
    if not any(token.upper() in name for token in names) or not any(address.endswith(street) for street in addresses):
        return None
    return {"stadium": code, "status": "candidate", "scope": "unknown",
            "label": "구장 소속 후보 · 내외부 미확인",
            "basis": "구장 지점명과 구장 주소 일치. 자리어때 매장 레코드와 개별 대조 전",
            "coordinateStatus": "shop_position_unverified"}
