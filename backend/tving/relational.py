from datetime import date as Date, datetime, time as Time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime

from baseball.data_loader import stable_id
from baseball.models import (
    Game, Player, PlayerCareerRecord, PlayerSeasonRecord, ScheduleDay,
    Stadium, StandingHistory, Team, TeamProfile, TeamRoster, TeamSeasonRecord,
    TeamTopPlayer,
)


KST = ZoneInfo("Asia/Seoul")
TEAM_MAP = {"SS": "SAMSUNG", "KT": "KT", "LG": "LG", "HT": "KIA", "OB": "DOOSAN", "NC": "NC", "HH": "HANWHA", "LT": "LOTTE", "SK": "SSG", "WO": "KIWOOM"}
RAW_STATUS = {"scheduled": "PREV", "live": "NOW", "final": "END", "cancelled": "CANCEL", "postponed": "CANCEL", "suspended": "SUSPENDED", "unknown": "UNKNOWN"}
NORMAL_STATUS = {"PREV": "scheduled", "READY": "scheduled", "NOW": "live", "END": "final", "CANCEL": "cancelled", "SUSPENDED": "suspended"}
STADIUM_CODES = {"잠실": "JAMSIL", "고척": "GOCHEOK", "문학": "MUNHAK", "인천": "MUNHAK",
                 "수원": "SUWON", "대전": "DAEJEON", "대구": "DAEGU", "광주": "GWANGJU",
                 "사직": "SAJIK", "창원": "CHANGWON"}


class RelationalDataError(ValueError):
    pass


def stadium_for(name):
    """Resolve only a provider venue name, never infer a venue from the home team."""
    normalized = "".join((name or "").split()).casefold()
    if not normalized:
        return None
    code = STADIUM_CODES.get(normalized)
    matches = [stadium for stadium in Stadium.objects.all()
               if stadium.stadium_code == code
               or "".join(stadium.stadium_name_ko.split()).casefold() == normalized]
    return matches[0] if len(matches) == 1 else None


def team_for(code):
    mapped = TEAM_MAP.get(code)
    if not mapped:
        return None
    try:
        return Team.objects.get(team_code=mapped)
    except Team.DoesNotExist:
        raise RelationalDataError(f"baseball.Team mapping missing for {code}") from None


def _due(value, now):
    return value is None or now - value >= timedelta(seconds=settings.EXTERNAL_DATA_SYNC_INTERVAL_SECONDS)


def _provided(values):
    """Null/blank optional text is absent; zero, False, and empty collections are explicit."""
    return {field: value for field, value in values.items() if value is not None and value != ""}


def _upsert(model, lookup, defaults, now):
    row = model.objects.select_for_update().filter(**lookup).first()
    if row and not _due(row.last_synced_at, now):
        return row, False
    values = {**defaults, "source_fetched_at": now, "last_synced_at": now}
    if row:
        values = _provided(values)
        for field, value in values.items():
            setattr(row, field, value)
        row.save(update_fields=(*values, "updated_at"))
        return row, True
    try:
        with transaction.atomic():
            return model.objects.create(**lookup, **values), True
    except IntegrityError:
        row = model.objects.select_for_update().get(**lookup)
        if not _due(row.last_synced_at, now):
            return row, False
        values = _provided(values)
        for field, value in values.items():
            setattr(row, field, value)
        row.save(update_fields=(*values, "updated_at"))
        return row, True


def _ensure_player(code, team, name, now, image_url=None):
    try:
        with transaction.atomic():
            return Player.objects.create(external_code=code, team=team, name=name, image_url=image_url, identity_source_fetched_at=now, identity_last_synced_at=now)
    except IntegrityError:
        player = Player.objects.select_for_update().get(external_code=code)
        if _due(player.identity_last_synced_at, now):
            player.team, player.name = team, name
            if image_url is not None: player.image_url = image_url
            player.identity_source_fetched_at = player.identity_last_synced_at = now
            player.save(update_fields=("team", "name", "image_url", "identity_source_fetched_at", "identity_last_synced_at", "updated_at"))
        return player


def _promote_team(team, now, name):
    team = Team.objects.select_for_update().get(pk=team.pk)
    if team.source != "tving" or _due(team.last_synced_at, now):
        team.team_name_ko, team.source, team.source_fetched_at, team.last_synced_at = name, "tving", now, now
        team.save(update_fields=("team_name_ko", "source", "source_fetched_at", "last_synced_at", "updated_at"))
    return team


def _game_values(game, now, raw=False):
    away, home = game["away"], game["home"]
    day = Date.fromisoformat(game.get("date") or game["startsAt"][:10])
    game_time = Time.fromisoformat(game["time"])
    status = NORMAL_STATUS.get(game["status"], game["status"]) if raw else game["status"]
    return day, game_time, team_for(away["code"]), team_for(home["code"]), {
        "game_date": day, "game_time": game_time, "home_score": home.get("score"), "away_score": away.get("score"),
        "status_code": status, "collected_at": now, "source": "tving",
        "source_external_code": game["id"], "source_stadium_name": game["stadium"],
        "stadium": stadium_for(game["stadium"]),
        "source_status_label": game["statusLabel"] if "statusLabel" in game else game["status"], "source_fetched_at": now, "last_synced_at": now,
        "source_home_code": home["code"], "source_home_name": home["name"], "source_away_code": away["code"], "source_away_name": away["name"],
        "home_starting_pitcher": home.get("startingPitcher"), "away_starting_pitcher": away.get("startingPitcher"),
    }


def _new_game_id(external_code):
    candidate = stable_id(Game, f"tving:{external_code}")
    for _ in range(1000):
        if not Game.objects.filter(pk=candidate).exists():
            return candidate
        candidate = candidate % 2_000_000_000 + 1
    raise RelationalDataError("unable to allocate collision-free game id")


def _reserve_exact_games(games, now, raw=False):
    reserved = {}
    used = set()
    for game in games:
        day, game_time, away_team, home_team, _ = _game_values(game, now, raw)
        matches = Game.objects.select_for_update().filter(game_date=day, game_time=game_time, away_team=away_team, home_team=home_team, source_external_code__isnull=True)
        if matches.count() > 1:
            raise RelationalDataError("ambiguous exact cross-source game identity")
        row = matches.first()
        if row:
            if row.pk in used:
                raise RelationalDataError("one CSV game matches multiple TVING games")
            reserved[game["id"]] = row.pk
            used.add(row.pk)
    return reserved, used


def _upsert_game(game, now, raw=False, reserved_pk=None, reserved_else=frozenset()):
    day, game_time, away_team, home_team, values = _game_values(game, now, raw)
    row = Game.objects.select_for_update().filter(source_external_code=game["id"]).first()
    if row and (row.away_team_id != (away_team.pk if away_team else None) or row.home_team_id != (home_team.pk if home_team else None)):
        raise RelationalDataError("existing TVING game identity changed teams")
    if not row and reserved_pk:
        row = Game.objects.select_for_update().get(pk=reserved_pk, source_external_code__isnull=True)
    if not row:
        candidates = Game.objects.select_for_update().filter(game_date=day, away_team=away_team, home_team=home_team, source_external_code__isnull=True).exclude(pk__in=reserved_else)
        exact = candidates.filter(game_time=game_time)
        if exact.count() == 1:
            row = exact.first()
        elif exact.count() > 1:
            raise RelationalDataError("ambiguous exact cross-source game identity")
        elif candidates.count() == 1:
            row = candidates.first()
        elif candidates.count() > 1:
            raise RelationalDataError("ambiguous cross-source game identity")
    if row and row.source == "tving" and not _due(row.last_synced_at, now):
        return row, False
    if row:
        values = _provided(values)
        for field, value in values.items(): setattr(row, field, value)
        row.save(update_fields=(*values, "updated_at"))
        return row, True
    row = Game(id=_new_game_id(game["id"]), game_code=f"tving:{game['id']}", home_team=home_team, away_team=away_team, postseason_stage=None, game_type="UNKNOWN", **values)
    try:
        with transaction.atomic(): row.save(force_insert=True)
    except IntegrityError:
        return _upsert_game(game, now, raw)
    return row, True


def _game_json(row, raw=False):
    away_code = row.source_away_code or next(key for key, value in TEAM_MAP.items() if row.away_team and value == row.away_team.team_code)
    home_code = row.source_home_code or next(key for key, value in TEAM_MAP.items() if row.home_team and value == row.home_team.team_code)
    status = RAW_STATUS.get(row.status_code, "UNKNOWN") if raw else row.status_code
    starts_at = datetime.combine(row.game_date, row.game_time, KST).isoformat()
    return {
        "id": row.source_external_code or row.game_code, "date": row.game_date.isoformat(), "startsAt": starts_at,
        "time": row.game_time.strftime("%H:%M"), "stadium": row.source_stadium_name or (row.stadium.stadium_name_ko if row.stadium else "미정"), "status": status,
        **({} if raw else {"statusLabel": row.source_status_label or status}),
        "away": {"code": away_code, "name": row.source_away_name or row.away_team.team_name_ko.split()[0], "score": row.away_score, **({} if raw else {"startingPitcher": row.away_starting_pitcher})},
        "home": {"code": home_code, "name": row.source_home_name or row.home_team.team_name_ko.split()[0], "score": row.home_score, **({} if raw else {"startingPitcher": row.home_starting_pitcher})},
    }


def persist_daily(data, now):
    day = Date.fromisoformat(data["date"])
    with transaction.atomic():
        reserved, reserved_pks = _reserve_exact_games(data["games"], now)
        _upsert(ScheduleDay, {"date": day}, {"status": "ready" if data["games"] else "empty", "game_count": len(data["games"]), "game_codes": [game["id"] for game in data["games"]]}, now)
        for game in data["games"]:
            _upsert_game(game, now, reserved_pk=reserved.get(game["id"]), reserved_else=reserved_pks - {reserved.get(game["id"])})
        for standing in data["standings"]:
            team = team_for(standing["teamCode"])
            row = StandingHistory.objects.select_for_update().filter(team=team, snapshot_date=day).first()
            if not row:
                row = StandingHistory(id=stable_id(StandingHistory, f"{day}:{team.team_code}"), team=team, snapshot_date=day)
            if row.source != "tving" or _due(row.last_synced_at, now):
                values = {"rank": standing["rank"], "played": standing["played"], "wins": standing["wins"], "draws": standing["draws"], "losses": standing["losses"], "win_rate": standing["winRate"], "games_behind": Decimal(standing["gamesBehind"]), "winning_streak": standing["streak"], "batting_average": standing["battingAverage"], "era": standing["era"], "last_ten": standing["lastTen"], "collected_at": now, "source": "tving", "source_fetched_at": now, "last_synced_at": now}
                values = _provided(values)
                for field, value in values.items(): setattr(row, field, value)
                row.save(force_insert=row._state.adding, update_fields=None if row._state.adding else (*values, "updated_at"))
        for kind, rows in (("pitcher", data["individualRankings"]["pitchers"]), ("hitter", data["individualRankings"]["hitters"])):
            for ranking in rows:
                player = _ensure_player(ranking["playerCode"], team_for(ranking["teamCode"]), ranking["player"], now)
                metrics = {key: value for key, value in ranking.items() if key not in {"rank", "playerCode", "player", "teamCode", "team"}}
                _upsert(PlayerSeasonRecord, {"player": player, "season": day.year, "record_kind": kind, "record_key": "ranking"}, {
                    "rank": ranking["rank"], "title": "", "value": "", "rank_label": None, "is_first_rank": False, "metrics": metrics, "graphs": [],
                }, now)
    return daily_sync_time(day)


def persist_month(data, now):
    with transaction.atomic():
        reserved, reserved_pks = _reserve_exact_games(data["games"], now)
        for day in data["days"]:
            codes = [game["id"] for game in data["games"] if game["date"] == day["date"]]
            _upsert(ScheduleDay, {"date": Date.fromisoformat(day["date"])}, {"status": day["status"], "game_count": day["gameCount"], "game_codes": codes}, now)
        for game in data["games"]:
            _upsert_game(game, now, reserved_pk=reserved.get(game["id"]), reserved_else=reserved_pks - {reserved.get(game["id"])})
    return month_sync_time(data["month"])


def persist_team(data, now):
    team = team_for(data["code"])
    season = now.astimezone(KST).year
    with transaction.atomic():
        team = _promote_team(team, now, data["teamName"])
        roster_codes = {position: [athlete["code"] for athlete in athletes] for position, athletes in data["rosters"].items()}
        top_keys = {kind: [[group["title"], athlete["code"]] for group in groups for athlete in group["athletes"]] for kind, groups in data["rankings"].items()}
        _upsert(TeamProfile, {"team": team}, {"external_code": data["code"], "short_name": data["shortName"], "image_url": data["teamImageUrl"], "background_image_url": data["backgroundImage"], "season_title": data["seasonTitle"], "roster_codes": roster_codes, "top_keys": top_keys}, now)
        for category, records in (("main", data["mainRecords"]), ("box", data["boxRecords"])):
            for record in records:
                _upsert(TeamSeasonRecord, {"team": team, "season": season, "category": category, "title": record["title"]}, {"value": record["value"]}, now)
        reserved, reserved_pks = _reserve_exact_games(data["schedule"], now, raw=True)
        for game in data["schedule"]:
            _upsert_game(game, now, raw=True, reserved_pk=reserved.get(game["id"]), reserved_else=reserved_pks - {reserved.get(game["id"])})
        for position, athletes in data["rosters"].items():
            for athlete in athletes:
                player = _ensure_player(athlete["code"], team, athlete["name"], now, athlete["imageUrl"])
                _upsert(TeamRoster, {"team": team, "player": player}, {"position": position, "back_number": athlete["backNumber"]}, now)
        for athlete_type, groups in data["rankings"].items():
            for group in groups:
                for athlete in group["athletes"]:
                    player = _ensure_player(athlete["code"], team, athlete["name"], now, athlete["imageUrl"])
                    _upsert(TeamTopPlayer, {"team": team, "player": player, "athlete_type": athlete_type, "category": group["title"]}, {"rank": athlete["rank"], "value": athlete["value"], "image_url": athlete["imageUrl"]}, now)
    return team_sync_time(team)


def persist_athlete(data, now):
    profile = data["profile"]
    team = team_for(profile["team"]["code"])
    season = now.astimezone(KST).year
    with transaction.atomic():
        player = _ensure_player(profile["code"], team, profile["name"], now, profile["imageUrl"])
        if _due(player.profile_last_synced_at, now):
            values = {"team": team, "name": profile["name"], "image_url": profile["imageUrl"], "positions": profile["positions"], "back_number": profile["backNumber"], "join_date": profile["joinDate"], "birth_date": profile["birthDate"], "body": profile["body"], "education": profile["education"], "draft_order": profile["draftOrder"], "team_color": profile["team"]["color"], "team_logo_url": profile["team"]["logoUrl"], "profile_source_fetched_at": now, "profile_last_synced_at": now}
            values = _provided(values)
            for field, value in values.items(): setattr(player, field, value)
            player.save(update_fields=(*values, "updated_at"))
        for record in data["seasonRecords"]:
            _upsert(PlayerSeasonRecord, {"player": player, "season": season, "record_kind": "detail", "record_key": record["title"]}, {"rank": None, "title": record["title"], "value": record["value"], "rank_label": record["rank"], "is_first_rank": record["isFirstRank"], "metrics": {}, "graphs": record["graphs"]}, now)
        columns = data["careerColumns"]
        for position, row in enumerate(data["careerRows"]):
            _upsert(PlayerCareerRecord, {"player": player, "position": position}, {"season_label": row.get("season", ""), "title": data["careerTitle"], "columns": columns, "metrics": row}, now)
        if _due(player.detail_last_synced_at, now):
            values = {"season_title": data["seasonTitle"], "career_title": data["careerTitle"], "detail_record_keys": [record["title"] for record in data["seasonRecords"]], "career_positions": list(range(len(data["careerRows"]))), "detail_source_fetched_at": now, "detail_last_synced_at": now}
            for field, value in values.items(): setattr(player, field, value)
            player.save(update_fields=(*values, "updated_at"))
    return athlete_sync_time(player)


def daily_sync_time(day):
    values = list(ScheduleDay.objects.filter(date=day).values_list("source_fetched_at", "last_synced_at"))
    values += list(Game.objects.filter(game_date=day, source="tving").values_list("source_fetched_at", "last_synced_at"))
    values += list(StandingHistory.objects.filter(snapshot_date=day, source="tving").values_list("source_fetched_at", "last_synced_at"))
    values += list(PlayerSeasonRecord.objects.filter(season=day.year, record_kind__in=("pitcher", "hitter")).values_list("source_fetched_at", "last_synced_at"))
    return min((synced for fetched, synced in values if fetched and synced), default=None) if values and all(fetched and synced for fetched, synced in values) else None


def month_sync_time(month):
    year, number = int(month[:4]), int(month[5:])
    values = list(ScheduleDay.objects.filter(date__year=year, date__month=number).values_list("source_fetched_at", "last_synced_at"))
    return min((synced for fetched, synced in values if fetched and synced), default=None) if values and all(fetched and synced for fetched, synced in values) else None


def team_sync_time(team):
    profile = TeamProfile.objects.filter(team=team).first()
    if not profile:
        return None
    values = [(profile.source_fetched_at, profile.last_synced_at)]
    values += [
        (row.source_fetched_at, row.last_synced_at)
        for row in TeamRoster.objects.filter(team=team)
        if row.player_id in profile.roster_codes.get(row.position, [])
    ]
    active_top = {tuple(value) for group in profile.top_keys.values() for value in group}
    values += [
        (row.source_fetched_at, row.last_synced_at)
        for row in TeamTopPlayer.objects.filter(team=team)
        if (row.category, row.player_id) in active_top
    ]
    return min((synced for fetched, synced in values if fetched and synced), default=None) if values and all(fetched and synced for fetched, synced in values) else None


def athlete_sync_time(player):
    values = [(player.profile_source_fetched_at, player.profile_last_synced_at), (player.detail_source_fetched_at, player.detail_last_synced_at)]
    values += list(player.season_records.filter(record_kind="detail", record_key__in=player.detail_record_keys).values_list("source_fetched_at", "last_synced_at"))
    values += list(player.career_records.filter(position__in=player.career_positions).values_list("source_fetched_at", "last_synced_at"))
    return min((synced for fetched, synced in values if fetched and synced), default=None) if all(fetched and synced for fetched, synced in values) else None


def read_daily(day):
    date = Date.fromisoformat(day)
    marker = ScheduleDay.objects.filter(date=date).first()
    standings = list(StandingHistory.objects.filter(snapshot_date=date, source="tving").select_related("team").order_by("rank"))
    pitchers = list(PlayerSeasonRecord.objects.filter(season=date.year, record_kind="pitcher").select_related("player__team").order_by("rank"))
    hitters = list(PlayerSeasonRecord.objects.filter(season=date.year, record_kind="hitter").select_related("player__team").order_by("rank"))
    games = list(Game.objects.filter(game_date=date, source="tving", source_external_code__in=marker.game_codes if marker else []).select_related("home_team", "away_team", "stadium").order_by("game_time", "source_external_code"))
    if not marker or len(standings) != 10 or not pitchers or not hitters or len(games) != marker.game_count:
        return None
    def standing(row):
        code = next(key for key, value in TEAM_MAP.items() if value == row.team.team_code)
        return {"rank": row.rank, "teamCode": code, "team": row.team.team_name_ko.split()[0], "played": row.played, "wins": row.wins, "draws": row.draws, "losses": row.losses, "winRate": row.win_rate, "gamesBehind": str(row.games_behind), "streak": row.winning_streak, "battingAverage": row.batting_average, "era": row.era, "lastTen": row.last_ten}
    def ranking(row):
        code = next(key for key, value in TEAM_MAP.items() if value == row.player.team.team_code)
        return {"rank": row.rank, "playerCode": row.player_id, "player": row.player.name, "teamCode": code, "team": row.player.team.team_name_ko.split()[0], **row.metrics}
    return {"date": day, "games": [_game_json(game) for game in games], "standings": [standing(row) for row in standings], "individualRankings": {"pitchers": [ranking(row) for row in pitchers], "hitters": [ranking(row) for row in hitters]}, "sourceUpdatedAt": None, "mode": "fixed-interval"}


def read_next_game_day(day):
    """수집된 일정 중 다음 예정 경기일을 찾는다. 취소 경기와 달력에서 빠진 경기는 제외한다."""
    markers = ScheduleDay.objects.filter(date__gt=day, status="ready", game_count__gt=0).order_by("date")
    for marker in markers.iterator():
        rows = list(Game.objects.filter(
            game_date=marker.date, source="tving", source_external_code__in=marker.game_codes,
        ).select_related("home_team", "away_team", "stadium").order_by("game_time", "source_external_code"))
        if len(rows) != marker.game_count:
            raise RelationalDataError("incomplete next game day")
        games = [row for row in rows if row.status_code == "scheduled"]
        if games:
            synced = [marker.source_fetched_at, marker.last_synced_at]
            synced += [value for row in games for value in (row.source_fetched_at, row.last_synced_at)]
            if any(value is None for value in synced):
                raise RelationalDataError("missing next game sync time")
            return [_game_json(row) for row in games], min(synced)
    return [], None


def read_month(month, today):
    year, number = int(month[:4]), int(month[5:])
    import calendar
    expected = calendar.monthrange(year, number)[1]
    days = list(ScheduleDay.objects.filter(date__year=year, date__month=number).order_by("date"))
    if len(days) != expected:
        return None
    active_codes = [code for day in days for code in day.game_codes]
    games = list(Game.objects.filter(game_date__year=year, game_date__month=number, source="tving", source_external_code__in=active_codes).select_related("home_team", "away_team", "stadium").order_by("game_date", "game_time", "source_external_code"))
    if len(games) != sum(day.game_count for day in days):
        return None
    return {"year": year, "month": month, "today": today, "games": [_game_json(game) for game in games], "days": [{"date": day.date.isoformat(), "status": day.status, "gameCount": day.game_count} for day in days], "loading": False}


def read_team(code):
    team = team_for(code)
    profile = TeamProfile.objects.filter(team=team).first()
    if not profile:
        return None
    season = profile.source_fetched_at.astimezone(KST).year
    records = list(TeamSeasonRecord.objects.filter(team=team, season=season).order_by("id"))
    rosters = {position: [] for position in ("pitcher", "infielder", "outfielder", "catcher")}
    for row in TeamRoster.objects.filter(team=team).select_related("player").order_by("id"):
        if row.player_id in profile.roster_codes.get(row.position, []):
            rosters[row.position].append({"code": row.player_id, "name": row.player.name, "imageUrl": row.player.image_url, "backNumber": row.back_number})
    rankings = {kind: [] for kind in ("pitcher", "hitter")}
    top_rows = TeamTopPlayer.objects.filter(team=team).select_related("player").order_by("athlete_type", "category", "rank")
    for kind in rankings:
        categories = []
        active = {tuple(value) for value in profile.top_keys.get(kind, [])}
        for name in dict.fromkeys(row.category for row in top_rows if row.athlete_type == kind and (row.category, row.player_id) in active):
            categories.append({"title": name, "athletes": [{"rank": row.rank, "name": row.player.name, "code": row.player_id, "value": row.value, "imageUrl": row.image_url or row.player.image_url} for row in top_rows if row.athlete_type == kind and row.category == name and (row.category, row.player_id) in active]})
        rankings[kind] = categories
    if any(
        {row.player_id for row in TeamRoster.objects.filter(team=team, position=position) if row.player_id in profile.roster_codes.get(position, [])}
        != set(profile.roster_codes.get(position, []))
        for position in rosters
    ) or any(
        {(row.category, row.player_id) for row in top_rows if row.athlete_type == kind and (row.category, row.player_id) in {tuple(value) for value in profile.top_keys.get(kind, [])}}
        != {tuple(value) for value in profile.top_keys.get(kind, [])}
        for kind in rankings
    ):
        return None
    profile_images = dict(TeamProfile.objects.values_list("team_id", "image_url"))
    shortcuts = [{"code": external, "name": target.team_name_ko, "imageUrl": profile_images.get(target.pk)} for external, internal in TEAM_MAP.items() if internal != team.team_code for target in Team.objects.filter(team_code=internal)]
    games = Game.objects.filter(Q(away_team=team) | Q(home_team=team), source="tving").select_related("home_team", "away_team", "stadium").order_by("game_date", "game_time")[:30]
    return {"code": code, "teamName": team.team_name_ko, "shortName": profile.short_name, "teamImageUrl": profile.image_url, "backgroundImage": profile.background_image_url, "seasonTitle": profile.season_title, "mainRecords": [{"title": row.title, "value": row.value} for row in records if row.category == "main"], "boxRecords": [{"title": row.title, "value": row.value} for row in records if row.category == "box"], "schedule": [{key: value for key, value in _game_json(game, raw=True).items() if key != "date"} for game in games], "rankings": rankings, "rosters": rosters, "shortcuts": shortcuts}


def read_athlete(code, *, player=None):
    player = player or Player.objects.filter(external_code=code).select_related("team").first()
    if not player or not player.profile_last_synced_at or not player.detail_last_synced_at:
        return None
    team_code = next(key for key, value in TEAM_MAP.items() if value == player.team.team_code)
    season_records = sorted((row for row in player.season_records.all() if row.record_kind == "detail" and row.record_key in player.detail_record_keys), key=lambda row: row.pk)
    career = sorted((row for row in player.career_records.all() if row.position in player.career_positions), key=lambda row: row.position)
    if len(season_records) != len(player.detail_record_keys) or len(career) != len(player.career_positions):
        return None
    return {"profile": {"code": code, "name": player.name, "imageUrl": player.image_url, "positions": player.positions, "backNumber": player.back_number, "joinDate": player.join_date, "birthDate": player.birth_date, "body": player.body, "education": player.education, "draftOrder": player.draft_order, "team": {"name": player.team.team_name_ko, "code": team_code, "color": player.team_color, "logoUrl": player.team_logo_url}}, "seasonTitle": player.season_title or "시즌 기록", "seasonRecords": [{"title": row.title, "value": row.value, "rank": row.rank_label, "isFirstRank": row.is_first_rank, "graphs": row.graphs} for row in season_records], "careerTitle": player.career_title or (career[0].title if career else "통산 기록"), "careerColumns": career[0].columns if career else [], "careerRows": [row.metrics for row in career]}
