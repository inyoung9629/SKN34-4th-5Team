import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db import transaction

KST = ZoneInfo("Asia/Seoul")
TEAM_CODES = {"LG": "LG", "한화": "HH", "SSG": "SK", "삼성": "SS", "NC": "NC", "KT": "KT", "롯데": "LT", "KIA": "HT", "두산": "OB", "키움": "WO"}


def _setup():
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()


def _events(html):
    marker = r'initialEvents\":['
    start = html.find(marker)
    if start < 0:
        raise ValueError("예매 이벤트를 찾지 못했습니다.")
    start += len(marker) - 1
    depth = 0
    quoted = escaped = False
    for index in range(start, len(html)):
        char = html[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted:
            depth += char == "["
            depth -= char == "]"
            if depth == 0:
                return json.loads(html[start:index + 1].replace(r'\"', '"'))
    raise ValueError("예매 이벤트 종료 위치를 찾지 못했습니다.")


def _policy_content(event):
    title = event.get("title", "")
    description = event.get("description", "")
    try:
        date = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(KST)
        date_text = f"{date.month}월 {date.day}일 {('월','화','수','목','금','토','일')[date.weekday()]}요일"
        title = re.sub(r"(오전|오후)\s*\d+시", f"{date_text} \\g<0>", title, count=1)
        description = re.sub(r"(오픈:\s*)(오전|오후)\s*\d+시", f"\\g<1>{date_text} \\g<2>", description, count=1)
    except (KeyError, ValueError):
        pass
    return re.sub(r"\s+", " ", f"{title} {description}").strip()


def _parse_policy(content, team_code):
    """크롤링 원문과 기존 구조화 CSV 양쪽 형식을 허용한다."""
    from preprocessing.parse_ticket_policy import make_new_id, parse_row

    parsed = parse_row(content)
    if parsed.get("parse_status") == "OK":
        return parsed, make_new_id(parsed, team_code, content)
    policy_type = re.search(r"^\[(선예매|일반)\]", content)
    booking = re.search(r"예매처:\s*(.*)$", content)
    maximum = re.search(r"(?:최대|시즌)\s*(\d+)매", content)
    name = re.search(r"^\[[^]]+\]\s*(.*?)\s+\d{1,2}월\s*\d{1,2}일", content)
    parsed = {
        "parse_status": "OK" if policy_type else "FAILED",
        "policy_type": policy_type.group(1) if policy_type else "",
        "policy_name": name.group(1).strip() if name else "",
        "policy_subtype": "",
        "max_tickets": maximum.group(1) if maximum else "",
        "booking_channel_and_condition": booking.group(1).strip() if booking else "",
    }
    key = "|".join((team_code, parsed["policy_type"], parsed["policy_name"], parsed["max_tickets"], content))
    return parsed, f"ticket_policy_{hashlib.md5(key.encode()).hexdigest()[:16]}"


def _collect_policies():
    import requests
    from preprocessing.team_stadium_map import normalize_team
    now = datetime.now(KST)
    response = requests.get(f"https://yagu.today/calendar/{now.year}/{now.month}", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    response.raise_for_status()
    rows = []
    for event in _events(response.text):
        if event.get("category") != "ticket":
            continue
        # TVING 약어(team_for용)와 내부 표준 코드(ID·벡터 metadata용)를 같은 팀 기준으로 함께 만든다.
        name = event.get("homeTeam") if event.get("homeTeam") in TEAM_CODES else event.get("awayTeam")
        tving_code, team_code = TEAM_CODES.get(name), normalize_team(name)
        if not tving_code or not team_code:
            continue
        try:
            event_date = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(KST)
        except (KeyError, ValueError):
            continue
        if event_date < now:
            continue
        content = _policy_content(event)
        parsed, policy_id = _parse_policy(content, team_code)
        rows.append({"id": policy_id, "team_code": team_code, "tving_code": tving_code, "content": content, "parsed": parsed})
    return rows


def _load_prices(now):
    from baseball.data_loader import stable_id
    from baseball.models import HomeContext, SeatZone, TicketPrice
    path = Path("/data/preprocessed/구장티켓가격.csv")
    if not path.exists():
        path = Path(__file__).resolve().parents[3] / "data/preprocessed/구장티켓가격.csv"
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            context = HomeContext.objects.filter(season=int(row["season"]), team__team_code=row["team_code"], stadium__stadium_code=row["stadium_code"]).first()
            zone = SeatZone.objects.filter(home_context=context, zone_code=row["zone_code"]).first() if context else None
            if not zone:
                continue
            key = "|".join(row.get(name, "") for name in ("season", "team_code", "stadium_code", "zone_code", "price_tier", "day_type", "customer_type", "group_size", "price_krw", "valid_from", "valid_to", "discount_condition"))
            values = {"seat_zone": zone, "price_tier": row["price_tier"], "day_type": row["day_type"], "customer_type": row["customer_type"], "group_size": int(row["group_size"]) if row["group_size"].isdigit() else None, "price_krw": int(row["price_krw"]), "valid_from": row["valid_from"] or None, "valid_to": row["valid_to"] or None, "discount_condition": row["discount_condition"], "collected_at": now}
            lookup = {field: values[field] for field in ("seat_zone", "price_tier", "day_type", "customer_type", "group_size", "price_krw", "valid_from", "valid_to", "discount_condition")}
            item = TicketPrice.objects.filter(**lookup).order_by("id").first()
            if item is None:
                item = TicketPrice.objects.create(id=stable_id(TicketPrice, key), **values)
            else:
                for field, value in values.items(): setattr(item, field, value)
                item.save(update_fields=tuple(values))
            count += 1
    return count


def _deduplicate_ticket_rows():
    from baseball.models import TicketPolicy, TicketPrice
    removed_prices = 0
    price_fields = ("seat_zone_id", "price_tier", "day_type", "customer_type", "group_size", "price_krw", "valid_from", "valid_to", "discount_condition")
    seen = {}
    for item in TicketPrice.objects.order_by("id"):
        key = tuple(getattr(item, field) for field in price_fields)
        if key in seen:
            item.delete()
            removed_prices += 1
        else:
            seen[key] = item.id
    removed_policies = 0
    seen = {}
    for item in TicketPolicy.objects.exclude(channel_condition="").order_by("id"):
        key = (item.channel_condition, item.team_id, item.policy_type, item.subtype, item.max_tickets, item.channel_no)
        if key in seen:
            item.delete()
            removed_policies += 1
        else:
            seen[key] = item.id
    # PostgreSQL vectors are preserved as migration input; Qdrant upserts use stable IDs.
    return removed_prices, removed_policies, 0


def collect_tickets():
    _setup()
    from baseball.models import TicketPolicy
    from preprocessing.team_stadium_map import stadium_code_of
    from tving.relational import team_for
    from .cron_tving import _vector_upsert
    now = datetime.now(KST)
    policies = _collect_policies()
    with transaction.atomic():
        removed_prices, removed_policies, removed_vectors = _deduplicate_ticket_rows()
        for item in TicketPolicy.objects.filter(policy_type=""):
            parsed, _ = _parse_policy(item.channel_condition, "")
            if parsed.get("parse_status") != "OK":
                continue
            item.policy_type = parsed.get("policy_type", "")
            item.subtype = parsed.get("policy_subtype", "")
            item.max_tickets = int(parsed["max_tickets"]) if parsed.get("max_tickets", "").isdigit() else None
            item.booking_channel = parsed.get("booking_channel_and_condition", "")
            item.save(update_fields=("policy_type", "subtype", "max_tickets", "booking_channel"))
        for row in policies:
            team = team_for(row["tving_code"])
            if not team:
                continue
            parsed = row["parsed"]
            max_tickets = parsed.get("max_tickets") or None
            values = {"policy_code": row["id"], "team": team, "game": None, "policy_type": parsed.get("policy_type", ""), "subtype": parsed.get("policy_subtype", ""), "open_at": None, "max_tickets": int(max_tickets) if max_tickets and max_tickets.isdigit() else None, "channel_no": 1, "booking_channel": parsed.get("booking_channel_and_condition", ""), "channel_condition": row["content"], "collected_at": now}
            item = TicketPolicy.objects.filter(policy_code=row["id"], channel_no=1).first()
            if not item:
                item = TicketPolicy.objects.filter(channel_condition=row["content"], channel_no=1).first()
            if item:
                for field, value in values.items(): setattr(item, field, value)
                item.save(update_fields=tuple(values))
            else:
                TicketPolicy.objects.create(id=abs(hash(row["id"])) % 2_000_000_000, **values)
        prices = _load_prices(now)
    docs = []
    for row in policies:
        stadium = stadium_code_of(row["team_code"]) or "UNKNOWN"
        docs.append({
            "doc_id": f"TICKET_POLICY_{stadium}_{row['id']}",
            "content": row["content"],
            "metadata": {"id": row["id"], "category": "TICKET_POLICY", "team_code": row["team_code"], "stadium_code": stadium},
        })
    return len(policies), prices, {"removed_prices": removed_prices, "removed_policies": removed_policies, "removed_vectors": removed_vectors, **_vector_upsert(docs)}
