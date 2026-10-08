"""Use explicit business information on pages read in this request. Unknown stays eligible.

No extra web/model call, raw-page persistence, or inference from search snippets.
Only a page matched to the same shop and full address can supply a constraint.
"""
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

_STATE = ContextVar("course_availability", default=None)
KST = ZoneInfo("Asia/Seoul")
ALL_DAYS = list(range(7))
DAY_NAMES = "월화수목금토일"
CLOCK = r"(?<!\d)(?:[01]?\d|2[0-4]):[0-5]\d(?!\d)"
RANGE = re.compile(rf"(?P<start>{CLOCK})\s*(?:~|～|〜|–|—|-|부터)\s*(?P<end>{CLOCK})")
HEADING = re.compile(r"영업\s*시간|운영\s*시간|이용\s*시간|정기\s*휴무|휴무일|영업\s*상태")
END = re.compile(r"^(?:메뉴\s*(?:정보|안내|목록)|방문자\s*(?:리뷰|평가)|블로그\s*리뷰|이용\s*후기|근처\s*맛집|주변\s*추천|비슷한\s*맛집)(?:\s|$)")
UNCLEAR = re.compile(r"변동|유동|문의|미정|확인\s*필요|공휴일|명절|격주|매월|첫째|둘째|셋째|넷째|다섯째|[1-5](?:번째|째|주차)")


@contextmanager
def session():
    if _STATE.get() is not None:
        yield
        return
    token = _STATE.set({})
    try:
        yield
    finally:
        _STATE.reset(token)


def key(place):
    # Findings use place_id; lodging details use id; course candidates use placeId.
    identifier = place.get("placeId") or place.get("place_id") or place.get("id")
    return ("id", str(identifier)) if identifier else ("address", place.get("name"), place.get("address"))


def minute(value):
    h, m = map(int, value.split(":"))
    return h * 60 + m if h < 24 or (h == 24 and m == 0) else None


def days(text):
    if re.search(r"매일|연중무휴", text):
        return ALL_DAYS
    if "평일" in text:
        return list(range(5))
    if "주말" in text:
        return [5, 6]
    span = re.search(r"([월화수목금토일])(?:요일)?\s*[~～〜–-]\s*([월화수목금토일])(?:요일)?", text)
    if span:
        first, last = map(DAY_NAMES.index, span.groups())
        return [(first + i) % 7 for i in range((last - first) % 7 + 1)]
    labels = re.findall(r"(?:^|[\s,·/([]|매주)([월화수목금토일])(?:요일)?(?=$|[\s,·/):\]]|정기|휴무|은|는)", text)
    return sorted({DAY_NAMES.index(label) for label in labels}) or None


def parse(body, observed_on=None):
    """Recognise explicit hours sections; ambiguous schedules are left unknown."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines() if line.strip()]
    rules, closed, active, in_section, count = [], False, None, False, 0
    past_details, pending_holiday, uncertain_context = False, False, False
    observed_on = observed_on or datetime.now(KST).date()
    active_dates = None
    unbound_dates = False
    facility_section = False
    for line in lines:
        if END.search(line):
            in_section, active = False, None
            if re.search(r"리뷰|후기|평가|근처|주변|비슷한", line):
                past_details = True
        if past_details:
            continue
        # A hotel's pool/breakfast/check-out or a shop's parking hours are not
        # the operating hours of the venue itself.
        if re.search(r"수영장|사우나|조식|대실|객실|체크\s*인|체크\s*아웃|입실|퇴실|주차장", line):
            facility_section, in_section = True, False
            continue
        if facility_section:
            if re.match(r"^(?:매장\s*)?영업\s*시간", line):
                facility_section = False
            else:
                continue
        short_dates = re.findall(r"(?<!\d)(\d{1,2})월\s*(\d{1,2})일(?:\s*\([월화수목금토일]\))?", line)
        # A flattened row of date tabs loses the association with each timetable.
        # Only a single date can scope subsequent text. The HTML reader supplies
        # separately paired date/time rows when the source structure is intact.
        date_label = re.sub(r"\d{1,2}월\s*\d{1,2}일(?:\s*\([월화수목금토일]\))?", "", line)
        if short_dates and not date_label.strip(" ,·"):
            candidates = []
            for month, day_number in short_dates:
                possible = []
                for year in (observed_on.year - 1, observed_on.year, observed_on.year + 1):
                    try:
                        possible.append(date(year, int(month), int(day_number)))
                    except ValueError:
                        pass
                if possible:
                    candidates.append(min(possible, key=lambda d: abs((d - observed_on).days)).isoformat())
            active_dates = candidates if len(short_dates) == len(candidates) == 1 else None
            unbound_dates = active_dates is None
            active, pending_holiday, uncertain_context = None, False, False
            in_section, count = True, 0
            continue
        if re.fullmatch(r"오늘(?:\([월화수목금토일]\))?", line):
            active_dates = [observed_on.isoformat()]
            active, pending_holiday, unbound_dates = None, False, False
            continue
        # A present-tense platform status, not a review about a former business.
        if re.fullmatch(r"(?:(?:영업|운영)\s*상태\s*[:：]?\s*)?(?:폐업|영구\s*폐업|영구\s*영업\s*종료)(?:\s*안내|했습니다|한\s*장소입니다)?[.!]?", line):
            if not count or in_section:
                closed = True
        if (HEADING.search(line) or re.fullmatch(r"24\s*시간\s*(?:영업|운영)", line)
                or (days(line) is not None and re.search(r"휴무|휴업", line))):
            in_section, count = True, 0
            uncertain_context = False
            pending_holiday = bool(re.fullmatch(r"(?:정기\s*)?휴무일?\s*[:：]?", line))
        if not in_section:
            continue
        count += 1
        if count > 35:
            in_section = False
            continue
        if UNCLEAR.search(line):
            active, pending_holiday = None, False
            uncertain_context = True
            continue
        full_date = re.search(r"(20\d\d)[./년-]\s*(\d{1,2})[./월-]\s*(\d{1,2})(?:일)?", line)
        dated = None
        if full_date:
            try:
                dated = date(*map(int, full_date.groups())).isoformat()
            except ValueError:
                continue
            active_dates, active, unbound_dates = [dated], None, False
        explicit = days(line)
        if explicit is not None:
            active = explicit
            if not dated:
                active_dates = None
            unbound_dates = False
            uncertain_context = False
        if uncertain_context or unbound_dates:
            continue
        applicable = {"days": active if active is not None else ALL_DAYS, "date": dated, "dates": active_dates,
                      "specific": explicit is not None or active is not None, "evidence": line[:180]}
        # '오늘 휴무/영업 종료' cannot establish a future closure; no invented date.
        if re.search(r"오늘|현재|지금|잠시|임시", line) and not dated:
            continue
        if (re.search(r"휴무|휴업", line) or pending_holiday) and (explicit is not None or dated or active_dates or (active is not None and line == "휴무")):
            if not re.search(r"없|아님|아니|무휴", line):
                rules.append({**applicable, "kind": "closed"})
            pending_holiday = False
            continue
        if re.search(r"24\s*시간", line):
            rules.append({**applicable, "kind": "hours", "start": 0, "end": 1440})
            continue
        def clock_text(match):
            period, hour, mins = match.groups()
            hour, mins = int(hour), int(mins or 0)
            if period:
                hour = hour % 12 + (12 if period == "오후" else 0)
            return f"{hour:02d}:{mins:02d}"
        normalized = re.sub(r"(?:(오전|오후)\s*)?(\d{1,2})시(?:\s*(\d{1,2})분)?(?=\s|[~～〜–-]|부터|까지|$)", clock_text, line)
        normalized = re.sub(r"(오전|오후)\s*(\d{1,2}):(\d{2})", clock_text, normalized)
        ranges = list(RANGE.finditer(normalized))
        if ranges:
            kind = "break" if re.search(r"브레이크|휴게|준비\s*시간|break", line, re.I) else "hours"
            for match in ranges:
                start, end = minute(match["start"]), minute(match["end"])
                if start is None or end is None or start == end or start == 1440:
                    continue
                rules.append({**applicable, "kind": kind, "start": start, "end": end + (1440 if end < start else 0)})
        order = re.search(rf"(?:라스트\s*오더|마지막\s*주문|주문\s*마감)\s*[:：]?\s*({CLOCK})", normalized)
        reverse_order = re.search(rf"({CLOCK})\s*(?:라스트\s*오더|마지막\s*주문|주문\s*마감)", normalized)
        if clock := order or reverse_order:
            if (value := minute(clock[1])) is not None:
                rules.append({**applicable, "kind": "last_order", "start": value})
    unique = {}
    for rule in rules:
        identity = (rule["kind"], rule.get("start"), rule.get("end"), rule.get("date"),
                    tuple(rule.get("dates") or []), tuple(rule["days"]))
        unique.setdefault(identity, rule)
    return {"closed": closed, "rules": list(unique.values())}


def observe(place, page, url=""):
    state = _STATE.get()
    if state is None or not page.get("body_read"):
        return
    from .grounding import address_in_body, title_matches
    identity = SimpleNamespace(name=place.get("name", ""), address=place.get("address", ""))
    body = page.get("body_text", "")
    title = page.get("title", "")
    closed_title = bool(re.search(r"[([]\s*(?:폐업|영구\s*폐업)\s*[)\]]", title))
    normalized = {**page, "title": re.sub(r"[([]\s*(?:폐업|영구\s*폐업)\s*[)\]]", "", title).strip()}
    if urlsplit(page.get("url") or url).hostname == "nol.yanolja.com":
        normalized["title"] = re.split(r"\s(?:호텔/리조트|모텔|펜션|게스트하우스|리조트)\s+예약", normalized["title"])[0]
    if not identity.address or not title_matches(identity, normalized) or not address_in_body(identity.address, body):
        return
    info = parse(page.get("availability_text", body))
    for dated_hours in page.get("dated_hours", []):
        info["rules"].extend(parse(dated_hours)["rules"])
    info["closed"] = info["closed"] or closed_title
    if info["closed"] or info["rules"]:
        info["url"] = page.get("url") or url
        state.setdefault(key(place), {})[info["url"]] = info


def records(place):
    return list((_STATE.get() or {}).get(key(place), {}).values())


def set_date(value):
    if _STATE.get() is not None:
        _STATE.get()["visit_date"] = value


def visit_date():
    return (_STATE.get() or {}).get("visit_date")


def _applies(rule, day):
    if rule.get("date"):
        return rule["date"] == day.isoformat()
    if rule.get("dates"):
        return day.isoformat() in rule["dates"]
    return day.weekday() in rule["days"]


def _rules(info, day, kind):
    found = [r for r in info["rules"] if r["kind"] == kind and _applies(r, day)]
    # A dated exception takes precedence over the weekly table for that kind.
    dated = [r for r in found if r.get("date") or r.get("dates")]
    specific = [r for r in found if r.get("specific")]
    return dated or specific or found


def _verdict(info, day, start, duration):
    if info["closed"]:
        return "폐업", True
    if day is None:
        return "", False
    holiday = _rules(info, day, "closed")
    hours = _rules(info, day, "hours")
    special_opening = (any(r.get("date") or r.get("dates") for r in hours)
                       and not any(r.get("date") or r.get("dates") for r in holiday))
    if start is None:
        if holiday and not special_opening:
            return "휴무일", True
        return "", False
    windows = [(r["start"], r["end"]) for r in hours]
    previous = day - timedelta(days=1)
    carry = []
    if not _rules(info, previous, "closed"):
        carry = [(r["start"] - 1440, r["end"] - 1440) for r in _rules(info, previous, "hours") if r["end"] > 1440]
    windows += carry
    following = day + timedelta(days=1)
    if not _rules(info, following, "closed"):
        windows += [(r["start"] + 1440, r["end"] + 1440) for r in _rules(info, following, "hours")]
    merged = []
    for a, b in sorted(windows):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    windows = merged
    finish = start + duration
    if holiday and not special_opening and not any(a <= start < b and finish <= b for a, b in carry):
        return "휴무일", True
    if (hours or any(a <= start < b for a, b in carry)) and not any(a <= start < b and finish <= b for a, b in windows):
        return "예정 방문·체류 시간이 영업시간 밖", True
    breaks = [(r["start"], r["end"]) for r in _rules(info, day, "break")]
    breaks += [(r["start"] - 1440, r["end"] - 1440) for r in _rules(info, previous, "break") if r["end"] > 1440]
    if any(start < b and max(finish, start + 1) > a for a, b in breaks):
        return "브레이크타임", True
    # After midnight, last-order clocks belong to the same overnight window.
    for offset in (0, -1):
        source_day = day + timedelta(days=offset)
        source_hours = _rules(info, source_day, "hours")
        for rule in _rules(info, source_day, "last_order"):
            limit = rule["start"]
            overnight = any(r["start"] > limit and r["end"] > 1440 for r in source_hours)
            if overnight:
                limit += 1440
            if offset and not (overnight and any(a <= start < b for a, b in carry)):
                continue
            if start >= limit + offset * 1440:
                return "주문 마감 이후", True
    return "", bool(hours or any(a <= start < b for a, b in carry))


def check(place, visit_date=None, row=None):
    """Return an exclusion reason only for a supported closure; unknown returns ''."""
    if place.get("completed") or place.get("category") == "STADIUM":
        return ""
    try:
        day = date.fromisoformat(visit_date) if visit_date else None
    except (TypeError, ValueError):
        day = None
    start, duration = None, 0
    if row and day:
        match = re.fullmatch(r"(?:(익일)\s+)?(\d{2}:\d{2})", str(row.get("time", "")))
        if match:
            start = minute(match[2])
            day += timedelta(days=row.get("dayOffset", 1 if match[1] else 0))
            duration = max(0, int(row.get("stayMin") or 0))
    verdicts = [_verdict(info, day, start, duration) for info in records(place)]
    # Conflicting explicit sources are uncertain, so do not silently discard.
    if any(not reason and known for reason, known in verdicts):
        return ""
    return next((reason for reason, _ in verdicts if reason), "")


def repair(places, calculate, alternatives, visit_date, *, max_attempts=4):
    """Re-evaluate the whole timetable after each replacement; unknown never drops out.

    calculate(rows) -> {'tl': {'rows': [...]}, ...}; alternatives receives only
    the blocked slot and must keep its category/user conditions/radius policy.
    """
    places = [dict(p) for p in places]
    rejected, changes, attempts = [], [], {}
    while places:
        calculated = calculate(places)
        rows = calculated["tl"]["rows"]
        blocked = next(((i, check(p, visit_date, row)) for i, (p, row) in enumerate(zip(places, rows))
                        if check(p, visit_date, row)), None)
        if blocked is None:
            return places, calculated, changes
        i, why = blocked
        old = places[i]
        slot = old.get("key") or old.get("visitId") or str(i)
        rejected.append(old)
        attempts[slot] = attempts.get(slot, 0) + 1
        options = alternatives(old, i, places, rejected) if attempts[slot] <= max_attempts else []
        options = [p for p in options if not any(key(p) == key(r) for r in rejected)
                   and not check(p, visit_date)]
        if options:
            new = {**options[0], "phase": old.get("phase", "BEFORE")}
            if "key" in old:
                new["key"] = old["key"]
            if "visitId" in old:
                new["visitId"] = "availability:" + str(uuid4())
            if "stayOverride" in old:
                new["stayOverride"] = old["stayOverride"]
            places[i] = new
            changes.append(f"{old['name']}은 {why}으로 제외하고 {new['name']}을 다음 후보로 확인했어요.")
        else:
            places.pop(i)
            changes.append(f"{old['name']}은 {why}으로 제외했어요. 같은 조건의 대체 후보를 찾지 못해 해당 방문은 담지 않았어요.")
    return places, {"tl": {"rows": []}}, changes


def notices(changes):
    return "\n".join(changes)
