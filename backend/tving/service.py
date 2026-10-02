import json
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from baseball.models import (
    Game, Player, PlayerSeasonRecord, ProviderSnapshot, StandingHistory,
    TeamProfile, TeamRoster, TeamTopPlayer,
)
from .parsers import (
    ATHLETE_TYPES, POSITIONS, TEAM_CODES, TvingValidationError, compact_date,
    parse_athlete_detail, parse_calendar, parse_rankings, parse_schedule,
    parse_standings, parse_team_detail,
)
from .relational import (
    RelationalDataError, TEAM_MAP, athlete_sync_time, daily_sync_time, month_sync_time,
    persist_athlete, persist_daily, persist_month, persist_team, read_athlete,
    read_daily, read_month, read_team, team_for, team_sync_time,
)


BASE_URL = "https://gw.tving.com/bff/sports/v2"
SOURCE = {"name": "TVING", "url": "https://www.tving.com/sports/kbo"}
MAX_RESPONSE_BYTES = 4_000_000


class TvingError(Exception):
    code = "tving_error"


class TvingUpstreamError(TvingError):
    code = "upstream_unavailable"


class TvingInputError(TvingError):
    code = "invalid_input"


class TvingAuthorizationError(TvingError):
    code = "forbidden"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _provider_json(path, params, opener=None):
    if path not in {
        "/kbo/schedule", "/kbo/schedule/day", "/kbo/history/team",
        "/kbo/history/athlete/ranking", "/team", "/kbo/history/athlete/top5",
        "/roaster/item", "/athlete",
    }:
        raise TvingInputError("허용되지 않은 TVING 리소스입니다.")
    url = f"{BASE_URL}{path}?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json", "Origin": "https://www.tving.com", "Referer": "https://www.tving.com/"})
    try:
        opener = opener or build_opener(_NoRedirect).open
        with opener(request, timeout=15) as response:
            if response.status != 200 or response.geturl() != url:
                raise TvingUpstreamError("TVING 응답을 확인하지 못했습니다.")
            if "application/json" not in response.headers.get("Content-Type", "").lower():
                raise TvingUpstreamError("TVING이 JSON 응답을 반환하지 않았습니다.")
            length = response.headers.get("Content-Length")
            if length and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                raise TvingUpstreamError("TVING 응답 크기 제한을 초과했습니다.")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
        raise TvingUpstreamError(f"TVING 연결 실패 ({type(error).__name__})") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise TvingUpstreamError("TVING 응답 크기 제한을 초과했습니다.")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TvingUpstreamError("TVING JSON을 읽지 못했습니다.") from None


def _validate_identity(kind, key):
    try:
        if kind == ProviderSnapshot.DAILY:
            compact_date(key)
        elif kind == ProviderSnapshot.MONTH:
            compact_date(f"{key}-01")
        elif kind == ProviderSnapshot.TEAM:
            if key not in TEAM_CODES:
                raise TvingInputError("존재하지 않는 KBO 구단입니다.")
        elif kind == ProviderSnapshot.ATHLETE:
            if not isinstance(key, str) or not key.isdigit() or not 4 <= len(key) <= 12:
                raise TvingInputError("선수 코드가 올바르지 않습니다.")
        else:
            raise TvingInputError("리소스 종류가 올바르지 않습니다.")
    except TvingValidationError:
        raise TvingInputError("리소스 키가 올바르지 않습니다.") from None


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def read_snapshot(kind, key):
    _validate_identity(kind, key)
    return ProviderSnapshot.objects.filter(resource_kind=kind, resource_key=key).first()


def search_snapshots(*, kind=None, key=None, team=None, player=None, date=None, month=None, page=1, page_size=50):
    if kind is not None and kind not in dict(ProviderSnapshot.KINDS):
        raise TvingInputError("리소스 종류가 올바르지 않습니다.")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 10_000 or isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise TvingInputError("페이지 범위가 올바르지 않습니다.")
    for value, label, maximum in ((key, "key", 16), (player, "player", 80)):
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(char) < 32 for char in value)):
            raise TvingInputError(f"{label} 검색 값이 올바르지 않습니다.")
    if team is not None and (not isinstance(team, str) or team.upper() not in TEAM_CODES):
        raise TvingInputError("team 검색 값이 올바르지 않습니다.")
    if date is not None: _validate_identity(ProviderSnapshot.DAILY, date)
    if month is not None: _validate_identity(ProviderSnapshot.MONTH, month)
    query = ProviderSnapshot.objects.all().order_by("resource_kind", "resource_key")
    if kind: query = query.filter(resource_kind=kind)
    if key: query = query.filter(resource_key=key)
    if team: query = query.filter(payload__icontains=team.upper())
    if player: query = query.filter(payload__icontains=player)
    if date: query = query.filter(payload__icontains=date)
    if month: query = query.filter(payload__icontains=month)
    start = (page - 1) * page_size
    return list(query[start:start + page_size]), query.count()


def _require_actor(actor):
    if actor is None or not getattr(actor, "is_authenticated", False) or not getattr(actor, "is_staff", False):
        raise TvingAuthorizationError("관리자 권한이 필요합니다.")


def create_snapshot(*, kind, key, payload, source_fetched_at, actor):
    _require_actor(actor)
    _validate_identity(kind, key)
    _validate_normalized(kind, key, payload)
    if not isinstance(source_fetched_at, datetime) or timezone.is_naive(source_fetched_at):
        raise TvingInputError("원천 조회 시각은 timezone-aware 값이어야 합니다.")
    return ProviderSnapshot.objects.create(resource_kind=kind, resource_key=key, payload=payload, source_fetched_at=source_fetched_at, last_synced_at=None)


def update_snapshot(snapshot, *, payload, source_fetched_at, actor):
    _require_actor(actor)
    _validate_normalized(snapshot.resource_kind, snapshot.resource_key, payload)
    if not isinstance(source_fetched_at, datetime) or timezone.is_naive(source_fetched_at):
        raise TvingInputError("원천 조회 시각은 timezone-aware 값이어야 합니다.")
    with transaction.atomic():
        locked = ProviderSnapshot.objects.select_for_update().get(pk=snapshot.pk)
        locked.payload, locked.source_fetched_at, locked.last_synced_at = payload, source_fetched_at, None
        locked.save(update_fields=("payload", "source_fetched_at", "last_synced_at", "updated_at"))
    return locked


def delete_snapshot(snapshot, *, actor):
    _require_actor(actor)
    snapshot.delete()


def _validate_normalized(kind, key, payload):
    try:
        encoded_size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode())
    except (TypeError, ValueError):
        encoded_size = MAX_RESPONSE_BYTES + 1
    if not isinstance(payload, dict) or encoded_size > MAX_RESPONSE_BYTES:
        raise TvingInputError("payload가 올바른 JSON 객체가 아닙니다.")
    try:
        _safe_json(payload)
        if kind == ProviderSnapshot.DAILY:
            if payload.get("date") != key or len(payload.get("standings", [])) != 10 or not payload.get("individualRankings", {}).get("pitchers") or not payload.get("individualRankings", {}).get("hitters"):
                raise ValueError
            games = payload.get("games")
            if not isinstance(games, list) or len(games) > 20 or any(not _valid_game(game, key) for game in games) or len({game.get("id") for game in games}) != len(games):
                raise ValueError
            if any(not isinstance(row, dict) or row.get("teamCode") not in TEAM_CODES or isinstance(row.get("rank"), bool) or not isinstance(row.get("rank"), int) for row in payload["standings"]):
                raise ValueError
            for values in payload["individualRankings"].values():
                if any(not isinstance(row, dict) or not isinstance(row.get("playerCode"), str) or row.get("teamCode") not in TEAM_CODES or isinstance(row.get("rank"), bool) or not isinstance(row.get("rank"), int) for row in values):
                    raise ValueError
        elif kind == ProviderSnapshot.MONTH:
            if payload.get("month") != key or not isinstance(payload.get("days"), list) or not isinstance(payload.get("games"), list):
                raise ValueError
            if any(day.get("status") not in {"ready", "empty", "pending", "error"} or not isinstance(day.get("gameCount"), int) for day in payload["days"]):
                raise ValueError
            if any(not _valid_game(game, game.get("date")) or not str(game.get("date", "")).startswith(f"{key}-") for game in payload["games"]):
                raise ValueError
        elif kind == ProviderSnapshot.TEAM:
            if payload.get("code") != key or not all(isinstance(payload.get(field), str) for field in ("teamName", "shortName", "seasonTitle")) or set(payload.get("rosters", {})) != set(POSITIONS) or set(payload.get("rankings", {})) != set(ATHLETE_TYPES):
                raise ValueError
            if any(not isinstance(athlete, dict) or not isinstance(athlete.get("code"), str) or not isinstance(athlete.get("name"), str) for roster in payload["rosters"].values() for athlete in roster):
                raise ValueError
            if not _valid_image(payload.get("teamImageUrl")) or not _valid_image(payload.get("backgroundImage")):
                raise ValueError
        elif kind == ProviderSnapshot.ATHLETE:
            profile = payload.get("profile", {})
            if profile.get("code") != key or profile.get("team", {}).get("code") not in TEAM_CODES or not isinstance(profile.get("name"), str) or not isinstance(profile.get("positions"), list):
                raise ValueError
            if not all(field in payload and isinstance(payload[field], expected) for field, expected in (("seasonTitle", str), ("seasonRecords", list), ("careerTitle", str), ("careerColumns", list), ("careerRows", list))):
                raise ValueError
            if not _valid_image(profile.get("imageUrl")) or not _valid_image(profile.get("team", {}).get("logoUrl")):
                raise ValueError
        else:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise TvingInputError("정규화된 payload 계약이 올바르지 않습니다.") from None


def _safe_json(value):
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError
        return
    if isinstance(value, str):
        if len(value) > 4_000 or any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError
        return
    if isinstance(value, list):
        if len(value) > 2_000: raise ValueError
        for item in value: _safe_json(item)
        return
    if isinstance(value, dict):
        if len(value) > 200: raise ValueError
        for item_key, item in value.items():
            if not isinstance(item_key, str) or len(item_key) > 120: raise ValueError
            _safe_json(item)
        return
    raise ValueError


def _valid_game(game, day):
    if not isinstance(game, dict) or game.get("date") != day or game.get("status") not in {"scheduled", "live", "final", "cancelled", "postponed", "suspended", "unknown"}:
        return False
    if not isinstance(game.get("id"), str) or not game["id"] or not isinstance(game.get("stadium"), str):
        return False
    for side in ("away", "home"):
        team = game.get(side)
        if not isinstance(team, dict) or team.get("code") not in TEAM_CODES | {"WE", "EA"} or not isinstance(team.get("name"), str):
            return False
        score = team.get("score")
        if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 999):
            return False
    return game["away"]["code"] != game["home"]["code"]


def _valid_image(value):
    if value is None:
        return True
    from urllib.parse import urlsplit
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and parsed.hostname == "image.tving.com" and not parsed.username and not parsed.password


def _persist_if_due(kind, key, payload, fetched_at):
    interval = timedelta(seconds=settings.EXTERNAL_DATA_SYNC_INTERVAL_SECONDS)
    with transaction.atomic():
        snapshot = ProviderSnapshot.objects.select_for_update().filter(resource_kind=kind, resource_key=key).first()
        if snapshot and snapshot.last_synced_at is not None and fetched_at - snapshot.last_synced_at < interval:
            return snapshot, False
        if snapshot:
            snapshot.payload, snapshot.source_fetched_at, snapshot.last_synced_at = payload, fetched_at, fetched_at
            snapshot.save(update_fields=("payload", "source_fetched_at", "last_synced_at", "updated_at"))
            return snapshot, True
        try:
            with transaction.atomic():
                snapshot = ProviderSnapshot.objects.create(resource_kind=kind, resource_key=key, payload=payload, source_fetched_at=fetched_at, last_synced_at=fetched_at)
            return snapshot, True
        except IntegrityError:
            snapshot = ProviderSnapshot.objects.select_for_update().get(resource_kind=kind, resource_key=key)
            if snapshot.last_synced_at is not None and fetched_at - snapshot.last_synced_at < interval:
                return snapshot, False
            snapshot.payload, snapshot.source_fetched_at, snapshot.last_synced_at = payload, fetched_at, fetched_at
            snapshot.save(update_fields=("payload", "source_fetched_at", "last_synced_at", "updated_at"))
            return snapshot, True


def _envelope(data, fetched_at, last_synced_at, *, stale=False, warning=None, source_url=None):
    return {
        **data,
        "fetchedAt": _iso(fetched_at),
        "providerFetchedAt": _iso(fetched_at),
        "updatedAt": _iso(last_synced_at),
        "lastSyncedAt": _iso(last_synced_at),
        "source": {**SOURCE, **({"url": source_url} if source_url else {})},
        "stale": stale,
        "warning": warning,
    }


def _fresh_or_fallback(fetcher, persister, reader, sync_time, *, daily=False, source_url=None):
    # 화면 요청에서는 TVING API 호출과 DB 저장을 하지 않고 DB 값만 반환한다.
    previous = reader()
    last_synced_at = sync_time() if previous is not None else None
    # DB에 저장된 값이 없으면 크론 수집 후 다시 조회한다.
    if previous is None:
        raise TvingUpstreamError("데이터 호출 오류!")
    if daily:
        previous["nextCheckAt"] = _iso(last_synced_at + timedelta(seconds=settings.EXTERNAL_DATA_SYNC_INTERVAL_SECONDS))
    return _envelope(previous, last_synced_at, last_synced_at, source_url=source_url)

def refresh_daily(day, provider=None):
    provider = provider or _provider_json
    _validate_identity(ProviderSnapshot.DAILY, day)
    compact = compact_date(day)
    year = day[:4]
    def fetch(previous):
        with ThreadPoolExecutor(max_workers=4) as pool:
            schedule_job = pool.submit(provider, "/kbo/schedule", {"date": compact})
            standings_job = pool.submit(provider, "/kbo/history/team", {"yearSeason": year, "gameSeason": "0"})
            pitcher_job = pool.submit(provider, "/kbo/history/athlete/ranking", {"yearSeason": year, "gameSeason": "regular", "athleteType": "pitcher", "pitcherRankOrder": "earnedRunAverage", "screenCode": "CSSD0100", "osCode": "CSOD0900"})
            hitter_job = pool.submit(provider, "/kbo/history/athlete/ranking", {"yearSeason": year, "gameSeason": "regular", "athleteType": "hitter", "hitterRankOrder": "battingAverage", "screenCode": "CSSD0100", "osCode": "CSOD0900"})
            schedule_payload = schedule_job.result()
            schedule_band = next((item for item in schedule_payload.get("data", {}).get("bands", []) if isinstance(item, dict) and item.get("bandType") == "SPORTS_SCHEDULE"), {})
            month_payload = provider("/kbo/schedule/day", {"date": compact[:6]}) if str(schedule_band.get("focusDate")) != compact or (schedule_band.get("items") == [] and "calendar" not in schedule_band) else None
            games = parse_schedule(schedule_payload, day, month_payload)
            data = {"date": day, "games": games, "standings": parse_standings(standings_job.result(), day), "individualRankings": {"pitchers": parse_rankings(pitcher_job.result(), "pitcher"), "hitters": parse_rankings(hitter_job.result(), "hitter")}, "sourceUpdatedAt": None, "mode": "fixed-interval"}
        return data
    parsed_day = datetime.fromisoformat(day).date()
    return _fresh_or_fallback(fetch, persist_daily, lambda: read_daily(day), lambda: daily_sync_time(parsed_day), daily=True)


def refresh_month(month, provider=None):
    provider = provider or _provider_json
    _validate_identity(ProviderSnapshot.MONTH, month)
    def fetch(previous):
        calendar_payload = provider("/kbo/schedule/day", {"date": month.replace("-", "")})
        calendar = parse_calendar(calendar_payload, month)
        if previous:
            old_calendar = {int(day["date"][-2:]) for day in previous.get("days", []) if day.get("status") == "ready"}
            if not old_calendar.issubset(set(calendar)):
                raise TvingValidationError("저장된 경기일이 원천 달력에서 누락되었습니다")
        games_by_day = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            jobs = {pool.submit(provider, "/kbo/schedule", {"date": f"{month.replace('-', '')}{day:02d}"}): day for day in calendar}
            for job in as_completed(jobs):
                day = jobs[job]
                date = f"{month}-{day:02d}"
                games_by_day[day] = parse_schedule(job.result(), date, calendar_payload)
        import calendar as month_calendar
        days = []
        games = []
        for day in range(1, month_calendar.monthrange(int(month[:4]), int(month[5:]))[1] + 1):
            date = f"{month}-{day:02d}"
            day_games = games_by_day.get(day, [])
            games.extend(day_games)
            days.append({"date": date, "status": "ready" if day_games else "empty", "gameCount": len(day_games)})
        return {"year": int(month[:4]), "month": month, "today": datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(), "games": sorted(games, key=lambda game: (game["date"], game["time"], game["id"])), "days": days, "loading": False}
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    return _fresh_or_fallback(fetch, persist_month, lambda: read_month(month, today), lambda: month_sync_time(month), source_url="https://www.tving.com/sports/kbo/schedule")


def refresh_team(code, provider=None):
    provider = provider or _provider_json
    if not isinstance(code, str):
        raise TvingInputError("존재하지 않는 KBO 구단입니다.")
    code = code.upper()
    _validate_identity(ProviderSnapshot.TEAM, code)
    def fetch(_previous):
        with ThreadPoolExecutor(max_workers=7) as pool:
            main = pool.submit(provider, "/team", {"code": code, "sportsType": "kbo"})
            rankings = {kind: pool.submit(provider, "/kbo/history/athlete/top5", {"teamCode": code, "athleteType": kind}) for kind in ATHLETE_TYPES}
            rosters = {position: pool.submit(provider, "/roaster/item", {"sportsType": "kbo", "code": code, "position": position}) for position in POSITIONS}
            return parse_team_detail(code, main.result(), {key: job.result() for key, job in rankings.items()}, {key: job.result() for key, job in rosters.items()})
    team = team_for(code)
    result = _fresh_or_fallback(fetch, persist_team, lambda: read_team(code), lambda: team_sync_time(team), source_url=f"https://www.tving.com/sports/kbo/team/{code}")
    result.update(collecting=False, progress=details_status())
    return result


def refresh_athlete(code, provider=None):
    provider = provider or _provider_json
    _validate_identity(ProviderSnapshot.ATHLETE, code)
    sync_time = lambda: athlete_sync_time(Player.objects.get(external_code=code)) if Player.objects.filter(external_code=code).exists() else None
    result = _fresh_or_fallback(lambda _previous: parse_athlete_detail(code, provider("/athlete", {"code": code, "sportsType": "kbo"})), persist_athlete, lambda: read_athlete(code), sync_time, source_url=f"https://www.tving.com/sports/kbo/athlete/{code}")
    result.update(collecting=False, progress=details_status())
    return result


def get_game_range_freshness(start_date, end_date, provider=None):
    """요청 기간의 월별 일정이 DB에 있는지 확인해 stale/warning만 돌려준다.

    TVING 호출·DB 저장은 하지 않는다(#29부터 갱신은 crawling/ 크롤러 담당).
    """
    month = start_date.replace(day=1)
    end_month = end_date.replace(day=1)
    stale = False
    warnings = []
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    while month <= end_month:
        if month.year <= today.year:
            try:
                result = refresh_month(month.strftime("%Y-%m"), provider)
                stale = stale or result["stale"]
                if result["warning"]:
                    warnings.append(result["warning"])
            except TvingError:
                stale = True
                warnings.append("최신 일정을 확인하지 못해 저장된 자료만 조회합니다.")
        else:
            stale = True
            warnings.append("TVING이 아직 제공하지 않는 미래 일정은 저장된 자료만 조회합니다.")
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {"stale": stale, "warning": warnings[0] if warnings else None}


def get_standings_freshness(snapshot_date=None, provider=None):
    """오늘 순위가 DB에 있는지 확인해 stale/warning만 돌려준다.

    TVING 호출·DB 저장은 하지 않는다(#29부터 갱신은 crawling/ 크롤러 담당).
    과거·미래 날짜는 저장분만 조회한다는 경고를 준다.
    """
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    latest = StandingHistory.objects.order_by("-snapshot_date").values_list("snapshot_date", flat=True).first()
    if snapshot_date is not None and snapshot_date != today:
        return {"stale": True, "warning": "과거 날짜 순위는 현재 시즌 응답으로 덮어쓰지 않고 저장된 자료만 조회합니다."}
    if snapshot_date is None and latest is not None and latest > today:
        return {"stale": True, "warning": "미래 날짜 순위는 저장된 자료만 조회합니다."}
    try:
        result = refresh_daily(today.isoformat(), provider)
        return {"stale": result["stale"], "warning": result["warning"]}
    except TvingError:
        return {"stale": True, "warning": "최신 순위를 확인하지 못해 저장된 자료만 조회합니다."}


def details_status():
    teams = TeamProfile.objects.count()
    athletes = Player.objects.filter(profile_last_synced_at__isnull=False).count()
    return {"state": "partial" if teams or athletes else "idle", "generation": None, "startedAt": None, "completedAt": None, "teamTotal": 10, "teamDone": teams, "athleteTotal": athletes, "athleteDone": athletes, "failures": [], "strategy": "on-demand"}


def player_entity(player):
    code = next(key for key, value in TEAM_MAP.items() if value == player.team.team_code)
    return {"externalCode": player.external_code, "teamCode": code, "name": player.name, "imageUrl": player.image_url, "positions": player.positions, "backNumber": player.back_number, "profileLastSyncedAt": _iso(player.profile_last_synced_at)}


def search_entities(*, kind, team=None, player=None, date=None, month=None, page=1, page_size=50):
    if kind not in {"game", "standing", "team", "player", "roster", "player-season", "team-top"}:
        raise TvingInputError("entity kind가 올바르지 않습니다.")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 10_000 or isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise TvingInputError("페이지 범위가 올바르지 않습니다.")
    if team is not None and (not isinstance(team, str) or team.upper() not in TEAM_CODES):
        raise TvingInputError("구단 코드가 올바르지 않습니다.")
    mapped_team = team_for(team.upper()) if team else None
    if player is not None and (not isinstance(player, str) or not player.strip() or len(player) > 40 or any(ord(char) < 32 for char in player)):
        raise TvingInputError("선수 코드가 올바르지 않습니다.")
    if date: _validate_identity(ProviderSnapshot.DAILY, date)
    if month: _validate_identity(ProviderSnapshot.MONTH, month)
    if kind == "game":
        query = Game.objects.filter(source="tving").select_related("home_team", "away_team").order_by("game_date", "game_time", "pk")
        if mapped_team: query = query.filter(Q(home_team=mapped_team) | Q(away_team=mapped_team))
        if date: query = query.filter(game_date=date)
        if month: query = query.filter(game_date__year=int(month[:4]), game_date__month=int(month[5:]))
        serializer = lambda row: {"id": row.pk, "gameCode": row.game_code, "tvingCode": row.source_external_code, "date": row.game_date, "time": row.game_time, "homeTeam": row.home_team.team_code if row.home_team else row.source_home_code, "awayTeam": row.away_team.team_code if row.away_team else row.source_away_code, "status": row.status_code, "lastSyncedAt": row.last_synced_at}
    elif kind == "standing":
        query = StandingHistory.objects.filter(source="tving").select_related("team").order_by("-snapshot_date", "rank")
        if mapped_team: query = query.filter(team=mapped_team)
        if date: query = query.filter(snapshot_date=date)
        if month: query = query.filter(snapshot_date__year=int(month[:4]), snapshot_date__month=int(month[5:]))
        serializer = lambda row: {"id": row.pk, "teamCode": row.team.team_code, "date": row.snapshot_date, "rank": row.rank, "wins": row.wins, "draws": row.draws, "losses": row.losses, "lastSyncedAt": row.last_synced_at}
    elif kind == "team":
        query = TeamProfile.objects.select_related("team").order_by("external_code")
        if mapped_team: query = query.filter(team=mapped_team)
        serializer = lambda row: {"id": row.pk, "teamCode": row.external_code, "name": row.team.team_name_ko, "seasonTitle": row.season_title, "lastSyncedAt": row.last_synced_at}
    elif kind == "player":
        query = Player.objects.select_related("team").order_by("external_code")
        if mapped_team: query = query.filter(team=mapped_team)
        if player: query = query.filter(external_code=player)
        serializer = player_entity
    elif kind == "roster":
        query = TeamRoster.objects.select_related("team", "player").order_by("team_id", "position", "player_id")
        if mapped_team: query = query.filter(team=mapped_team)
        if player: query = query.filter(player_id=player)
        serializer = lambda row: {"id": row.pk, "teamCode": row.team.team_code, "playerCode": row.player_id, "position": row.position, "lastSyncedAt": row.last_synced_at}
    elif kind == "player-season":
        query = PlayerSeasonRecord.objects.select_related("player__team").order_by("-season", "record_kind", "rank", "player_id")
        if mapped_team: query = query.filter(player__team=mapped_team)
        if player: query = query.filter(player_id=player)
        serializer = lambda row: {"id": row.pk, "playerCode": row.player_id, "season": row.season, "kind": row.record_kind, "rank": row.rank, "metrics": row.metrics, "lastSyncedAt": row.last_synced_at}
    else:
        query = TeamTopPlayer.objects.select_related("team", "player").order_by("team_id", "athlete_type", "category", "rank")
        if mapped_team: query = query.filter(team=mapped_team)
        if player: query = query.filter(player_id=player)
        serializer = lambda row: {"id": row.pk, "teamCode": row.team.team_code, "playerCode": row.player_id, "athleteType": row.athlete_type, "category": row.category, "rank": row.rank, "lastSyncedAt": row.last_synced_at}
    count = query.count(); start = (page - 1) * page_size
    return [serializer(row) for row in query[start:start + page_size]], count


def create_player(*, external_code, team_code, name, actor):
    _require_actor(actor)
    _validate_identity(ProviderSnapshot.ATHLETE, external_code)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise TvingInputError("선수 이름이 올바르지 않습니다.")
    try:
        return Player.objects.create(external_code=external_code, team=team_for(team_code.upper()), name=name.strip())
    except IntegrityError:
        raise TvingInputError("이미 존재하는 선수 코드입니다.") from None


def update_player(player, *, team_code, name, actor):
    _require_actor(actor)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise TvingInputError("선수 이름이 올바르지 않습니다.")
    with transaction.atomic():
        locked = Player.objects.select_for_update().get(pk=player.pk)
        locked.team, locked.name = team_for(team_code.upper()), name.strip()
        locked.save(update_fields=("team", "name", "updated_at"))
    return locked


def delete_player(player, *, actor):
    _require_actor(actor)
    player.delete()
