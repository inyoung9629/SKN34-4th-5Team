import json
import os
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import TEAM_CODES


MAX_RESPONSE_BYTES = 2_000_000
MAX_SOURCE_AGE = timedelta(minutes=75)
SOURCE_GRACE = timedelta(minutes=2)
STATUSES = {"scheduled", "live", "final", "cancelled", "postponed", "suspended", "unknown"}
LOCKED_STATUSES = STATUSES - {"scheduled"}
VOID_STATUSES = {"cancelled", "postponed"}


class PredictionSourceError(Exception):
    pass


def _fail(message):
    raise PredictionSourceError(message)


def _text(value, field, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        _fail(f"{field} 값이 올바르지 않습니다.")
    result = value.strip()
    if any(ord(character) < 32 for character in result) or "<" in result or ">" in result:
        _fail(f"{field} 값이 올바르지 않습니다.")
    return result


def _timestamp(value, field):
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or timezone.is_naive(parsed):
        _fail(f"{field} 시각이 올바르지 않습니다.")
    return parsed


def _score(value, field, required=False):
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 999:
        _fail(f"{field} 점수가 올바르지 않습니다.")
    return value


def _team(value, field, score_required):
    if not isinstance(value, dict):
        _fail(f"{field} 팀 정보가 올바르지 않습니다.")
    code = _text(value.get("code"), f"{field} 팀 코드", 2)
    if code not in TEAM_CODES:
        _fail(f"{field} 팀 코드가 올바르지 않습니다.")
    return {
        "code": code,
        "name": _text(value.get("name"), f"{field} 팀 이름", 20),
        "score": _score(value.get("score"), field, score_required),
    }


def _source_url():
    value = os.getenv("PREDICTION_SOURCE_URL", "http://127.0.0.1:8000/api/v1/tving/daily/").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        _fail("경기 원천 URL 설정이 올바르지 않습니다.")
    return value


def fetch_prediction_snapshot(now=None, opener=None):
    now = now or timezone.now()
    if opener is None and not os.getenv("PREDICTION_SOURCE_URL", "").strip():
        from tving.service import refresh_daily
        today = now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
        payload = {"data": refresh_daily(today), "error": None}
    else:
        opener = opener or urlopen
        payload = _fetch_prediction_payload(opener)

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or payload.get("error") is not None:
        _fail("경기 원천 응답이 비어 있습니다.")
    today = now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    if data.get("date") != today or not isinstance(data.get("games"), list) or len(data["games"]) > 20:
        _fail("오늘 경기 스냅샷이 아닙니다.")
    fetched_at = _timestamp(data.get("fetchedAt"), "수집")
    next_check_at = _timestamp(data.get("nextCheckAt"), "다음 확인")
    if data.get("stale") is not False or fetched_at > now + SOURCE_GRACE or now - fetched_at > MAX_SOURCE_AGE or now > next_check_at + SOURCE_GRACE:
        _fail("경기 원천 갱신이 지연되었습니다.")

    games = []
    seen = set()
    for value in data["games"]:
        if not isinstance(value, dict):
            _fail("경기 정보가 올바르지 않습니다.")
        source_id = _text(value.get("id"), "경기 ID", 64)
        if source_id in seen or value.get("date") != today:
            _fail("경기 ID가 중복되었거나 날짜가 다릅니다.")
        seen.add(source_id)
        status = value.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            _fail("경기 상태를 확인할 수 없습니다.")
        starts_at = _timestamp(value.get("startsAt"), "경기 시작") if value.get("startsAt") is not None else None
        if starts_at and starts_at.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat() != today:
            _fail("경기 시작 날짜가 다릅니다.")
        score_required = status == "final"
        away = _team(value.get("away"), "원정", score_required)
        home = _team(value.get("home"), "홈", score_required)
        if away["code"] == home["code"]:
            _fail("홈팀과 원정팀이 같습니다.")
        result = ""
        if status == "final":
            result = "draw" if away["score"] == home["score"] else "away" if away["score"] > home["score"] else "home"
        games.append({
            "source_id": source_id,
            "game_date": today,
            "starts_at": starts_at,
            "stadium": _text(value.get("stadium"), "구장", 80),
            "away_team_code": away["code"],
            "away_team_name": away["name"],
            "away_score": away["score"],
            "home_team_code": home["code"],
            "home_team_name": home["name"],
            "home_score": home["score"],
            "status": status,
            "result": result,
            "source_fetched_at": fetched_at,
        })
    return games


def _fetch_prediction_payload(opener):
    source_url = _source_url()
    request = Request(source_url, headers={"Accept": "application/json"})
    try:
        with opener(request, timeout=15) as response:
            if response.status != 200:
                _fail(f"경기 원천 요청이 실패했습니다. (HTTP {response.status})")
            if response.geturl() != source_url:
                _fail("경기 원천이 다른 주소로 이동했습니다.")
            if "application/json" not in response.headers.get("Content-Type", ""):
                _fail("경기 원천이 JSON을 반환하지 않았습니다.")
            size = response.headers.get("Content-Length")
            if size and (not size.isdigit() or int(size) > MAX_RESPONSE_BYTES):
                _fail("경기 원천 응답이 너무 큽니다.")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        _fail(f"경기 원천에 연결하지 못했습니다: {type(error).__name__}")
    if len(body) > MAX_RESPONSE_BYTES:
        _fail("경기 원천 응답이 너무 큽니다.")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("경기 원천 JSON을 읽지 못했습니다.")

    return payload


def sync_prediction_games(now=None, opener=None):
    from .models import PredictionGame

    now = now or timezone.now()
    games = fetch_prediction_snapshot(now, opener)
    with transaction.atomic():
        source_ids = {values["source_id"] for values in games}
        existing_open_ids = set(PredictionGame.objects.select_for_update().filter(
            game_date=now.astimezone(ZoneInfo("Asia/Seoul")).date(),
        ).exclude(status__in=("final", "cancelled", "postponed")).values_list("source_id", flat=True))
        if existing_open_ids - source_ids:
            _fail("기존 예정 경기가 원천 응답에서 누락되었습니다.")
        for values in games:
            source_id = values.pop("source_id")
            incoming_status = values["status"]
            should_lock = values["starts_at"] is None or values["starts_at"] <= now or incoming_status in LOCKED_STATUSES
            defaults = {**values, "locked_at": now if should_lock else None, "voided_at": now if incoming_status in VOID_STATUSES else None}
            game, created = PredictionGame.objects.select_for_update().get_or_create(source_id=source_id, defaults=defaults)
            if created:
                continue
            if game.game_date != now.astimezone(ZoneInfo("Asia/Seoul")).date() or game.home_team_code != values["home_team_code"] or game.away_team_code != values["away_team_code"]:
                _fail("기존 경기 ID의 날짜 또는 대진이 변경되었습니다.")
            if game and (game.result or game.voided_at):
                values.update(
                    status=game.status,
                    result=game.result,
                    away_score=game.away_score,
                    home_score=game.home_score,
                )
            values["locked_at"] = game.locked_at if game and game.locked_at else now if should_lock else None
            values["voided_at"] = game.voided_at if game and game.voided_at else now if incoming_status in VOID_STATUSES else None
            for field, value in values.items():
                setattr(game, field, value)
            game.save()
    return len(games)
