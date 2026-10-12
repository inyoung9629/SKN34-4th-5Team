import re
import unicodedata
from datetime import date as Date


TEAM_NAMES = {"SS": "삼성", "KT": "KT", "LG": "LG", "HT": "KIA", "OB": "두산", "NC": "NC", "HH": "한화", "LT": "롯데", "SK": "SSG", "WO": "키움"}
SCHEDULE_TEAM_NAMES = {**TEAM_NAMES, "WE": "나눔", "EA": "드림"}
TEAM_CODES = frozenset(TEAM_NAMES)
POSITIONS = ("pitcher", "infielder", "outfielder", "catcher")
ATHLETE_TYPES = ("pitcher", "hitter")
STATUS = {
    "PREV": ("scheduled", "경기 예정"), "READY": ("scheduled", "경기 준비"),
    "NOW": ("live", "경기 중"), "END": ("final", "경기 종료"),
    "CANCEL": ("cancelled", "경기 취소"), "SUSPENDED": ("suspended", "경기 중단"),
}


class TvingValidationError(ValueError):
    pass


def fail(message):
    raise TvingValidationError(f"TVING KBO 응답 확인 실패: {message}")


def obj(value, field):
    if not isinstance(value, dict):
        fail(field)
    return value


def array(value, field, maximum=1000):
    if not isinstance(value, list) or len(value) > maximum:
        fail(field)
    return value


def text(value, field, maximum=120, empty=False):
    if not isinstance(value, str):
        fail(field)
    result = value.strip()
    if (not empty and not result) or len(result) > maximum or "<" in result or ">" in result or any(unicodedata.category(char) in {"Cc", "Cf"} for char in result):
        fail(field)
    return result


def optional_text(value, field, maximum=500):
    return "" if value in (None, "") else text(value, field, maximum, True)


def integer(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r"\d+", str(value)):
        fail(field)
    result = int(value)
    if result > 9_007_199_254_740_991:
        fail(field)
    return result


def numeric(value, field, maximum=None, signed=False):
    result = text(value, field, 24)
    pattern = r"-?\d+(?:\.\d+)?" if signed else r"\d+(?:\.\d+)?"
    if not re.fullmatch(pattern, result) or (maximum is not None and float(result) > maximum):
        fail(field)
    return result


def image_url(value, field):
    if value in (None, ""):
        return None
    from urllib.parse import urlsplit
    result = text(value, field, 1000)
    parsed = urlsplit(result)
    if parsed.scheme != "https" or parsed.hostname != "image.tving.com" or parsed.username or parsed.password:
        fail(field)
    return result


def success_data(payload):
    response = obj(payload, "응답 형식")
    if response.get("code") != "0000":
        fail("요청이 정상 처리되지 않았습니다")
    return obj(response.get("data"), "data")


def band_from_data(data, kind, optional=False):
    matches = [obj(item, "band") for item in array(data.get("bands"), "bands", 30) if obj(item, "band").get("bandType") == kind]
    if len(matches) > 1 or (not optional and len(matches) != 1):
        fail(f"{kind} 누락 또는 중복")
    return matches[0] if matches else None


def band(payload, kind):
    return band_from_data(success_data(payload), kind)


def compact_date(value):
    try:
        parsed = Date.fromisoformat(value)
    except (TypeError, ValueError):
        fail("요청 날짜")
    if parsed.isoformat() != value:
        fail("요청 날짜")
    return value.replace("-", "")


def parse_calendar(payload, month):
    compact_date(f"{month}-01")
    values = array(success_data(payload).get("calendar"), "월별 경기일", 31)
    days = [integer(value, "월별 경기일") for value in values]
    import calendar
    limit = calendar.monthrange(int(month[:4]), int(month[5:]))[1]
    if len(set(days)) != len(days) or any(day < 1 or day > limit for day in days):
        fail("월별 경기일 형식")
    return sorted(days)


def _game_team(value, show_score):
    item = obj(value, "경기 팀")
    name = text(item.get("name"), "팀 이름", 30)
    code = item.get("code") if isinstance(item.get("code"), str) else next((key for key, label in SCHEDULE_TEAM_NAMES.items() if label == name), None)
    if SCHEDULE_TEAM_NAMES.get(code) != name:
        fail("알 수 없는 팀")
    pitcher = item.get("pitcherName")
    try:
        pitcher = text(pitcher, "선발 투수", 60) if isinstance(pitcher, str) and pitcher.strip() and pitcher.strip().upper() not in {"-", "—", "미정", "미발표", "TBD", "N/A"} else None
    except TvingValidationError:
        pitcher = None
    return {"code": code, "name": name, "score": integer(item.get("score"), "경기 점수") if show_score else None, "startingPitcher": pitcher}


def parse_schedule(payload, requested_date, month_payload=None):
    requested = compact_date(requested_date)
    schedule = band(payload, "SPORTS_SCHEDULE")
    focused = str(integer(schedule.get("focusDate"), "일정 기준 날짜"))
    items = array(schedule.get("items"), "경기 목록", 20)
    calendar_payload = month_payload
    if schedule.get("calendar") is not None:
        calendar_payload = {"code": "0000", "data": {"calendar": schedule["calendar"]}}
    calendar_days = parse_calendar(calendar_payload, requested_date[:7]) if calendar_payload is not None else None
    if focused != requested:
        if calendar_days is None or int(requested[-2:]) in calendar_days:
            fail("요청 날짜와 일정 기준 날짜가 다릅니다")
        return []
    if not items:
        if calendar_days is None or int(requested[-2:]) in calendar_days:
            fail("빈 경기 목록의 월별 경기일 확인이 필요합니다")
        return []
    games = []
    for raw in items:
        item = obj(raw, "경기")
        game_id = text(item.get("code"), "경기 고유번호", 60)
        raw_time = str(integer(item.get("dateTime"), "경기 시각"))
        if not re.fullmatch(r"\d{12}", raw_time) or raw_time[:8] != requested or int(raw_time[8:10]) > 23 or int(raw_time[10:]) > 59:
            fail("유효하지 않은 경기 시각")
        raw_status = text(item.get("status"), "경기 상태", 30)
        if raw_status not in STATUS:
            fail("알 수 없는 경기 상태")
        status, label = STATUS[raw_status]
        show_score = status in {"live", "final", "suspended"}
        away, home = _game_team(item.get("away"), show_score), _game_team(item.get("home"), show_score)
        if away["code"] == home["code"]:
            fail("동일한 홈/원정 팀")
        time = f"{raw_time[8:10]}:{raw_time[10:]}"
        games.append({"id": game_id, "date": requested_date, "startsAt": f"{requested_date}T{time}:00+09:00", "time": time, "stadium": text(item.get("stadium"), "구장", 60), "away": away, "home": home, "status": status, "statusLabel": label})
    if len({game["id"] for game in games}) != len(games):
        fail("중복된 경기 고유번호")
    return sorted(games, key=lambda game: (game["time"], game["id"]))


def parse_standings(payload, requested_date):
    ranking = band(payload, "SPORTS_TEAM_RANKING_CALENDAR")
    year, season = obj(ranking.get("focusYearSeason"), "순위 시즌"), obj(ranking.get("focusGameSeason"), "순위 리그")
    if str(year.get("code")) != requested_date[:4] or str(season.get("code")) != "0":
        fail("요청한 정규리그 시즌과 다릅니다")
    items = array(ranking.get("items"), "정규리그 순위", 10)
    if len(items) != 10:
        fail("정규리그 10팀 순위가 완전하지 않습니다")
    rows = []
    for raw in items:
        item = obj(raw, "팀 순위")
        code, name = text(item.get("code"), "순위 팀 코드", 2), text(item.get("name"), "순위 팀 이름", 20)
        played, wins, draws, losses, rank = (integer(item.get(key), label) for key, label in (("games", "경기 수"), ("wins", "승"), ("draws", "무"), ("losses", "패"), ("rank", "순위")))
        if TEAM_NAMES.get(code) != name or not 1 <= rank <= 10 or played != wins + draws + losses:
            fail("순위 집계 불일치")
        rows.append({"rank": rank, "teamCode": code, "team": name, "played": played, "wins": wins, "draws": draws, "losses": losses, "winRate": numeric(item.get("winningPercentage"), "승률", 1), "gamesBehind": numeric(item.get("gamesBehind"), "게임차"), "streak": text(item.get("winningStreak"), "연속", 20), "battingAverage": numeric(item.get("battingAverage"), "타율", 1), "era": numeric(item.get("earnedRunAverage"), "평균자책"), "lastTen": text(item.get("recentGames"), "최근 10경기", 30)})
    if len({row["teamCode"] for row in rows}) != 10:
        fail("순위에 중복된 팀이 있습니다")
    return sorted(rows, key=lambda row: row["rank"])


PITCHER_FIELDS = {"earnedRunAverage": "earnedRunAverage", "fip": "fip", "whip": "whip", "war": "war", "qualityStarts": "qs", "games": "games", "wins": "wins", "losses": "losses", "saves": "save", "holds": "hold", "strikeouts": "strikeOut", "hitsAllowed": "hit", "homeRunsAllowed": "homeRun", "walks": "baseOnBalls", "hitByPitch": "hitByPitch", "wildPitches": "wildPitch", "runsAllowed": "run", "winningPercentage": "winningPercentage"}
HITTER_FIELDS = {"battingAverage": "battingAverage", "ops": "ops", "wrcPlus": "wrcPlus", "war": "war", "games": "games", "atBats": "atBat", "hits": "hit", "doubles": "doubles", "triples": "triples", "homeRuns": "homeRun", "runsBattedIn": "runBattedIn", "runs": "run", "stolenBases": "stolenBase", "walks": "baseOnBalls", "strikeouts": "strikeOut", "doublePlays": "doublePlay", "onBasePercentage": "onBasePercentage", "sluggingPercentage": "sluggingPercentage"}


def parse_rankings(payload, athlete_type):
    items = array(success_data(payload).get("items"), f"{athlete_type} 개인 순위", 500)
    if not items:
        fail("개인 순위 목록")
    fields = PITCHER_FIELDS if athlete_type == "pitcher" else HITTER_FIELDS
    rows = []
    for raw in items:
        item = obj(raw, "개인 순위")
        team_name = text(item.get("teamName"), "선수 소속팀", 20)
        team_code = next((code for code, name in TEAM_NAMES.items() if name == team_name), None)
        rank = integer(item.get("rank"), "개인 순위")
        if not team_code or not 1 <= rank <= 500:
            fail("선수 순위 정보")
        row = {"rank": rank, "playerCode": text(item.get("code"), "선수 코드", 40), "player": text(item.get("name"), "선수 이름", 60), "teamCode": team_code, "team": team_name}
        for target, source in fields.items():
            row[target] = numeric(item.get(source), target, signed=True)
        if athlete_type == "pitcher":
            innings = text(item.get("inning"), "이닝", 24)
            if not re.fullmatch(r"\d+(?: [12]/3)?", innings):
                fail("이닝")
            row["innings"] = innings
        rows.append(row)
    if len({row["playerCode"] for row in rows}) != len(rows):
        fail("중복된 선수 코드")
    return sorted(rows, key=lambda row: row["rank"])


def _records(value, field):
    return [{"title": text(obj(item, field).get("title"), field, 40), "value": text(obj(item, field).get("value"), field, 60)} for item in array(value, field, 30)]


def _ranking_groups(payload):
    groups = []
    for raw in array(success_data(payload).get("items"), "팀 내 순위", 30):
        item = obj(raw, "팀 내 순위")
        athletes = []
        for raw_athlete in array(item.get("athletes"), "팀 내 순위 선수", 10):
            athlete = obj(raw_athlete, "팀 내 순위 선수")
            code, name = optional_text(athlete.get("code"), "선수 코드", 40), optional_text(athlete.get("name"), "선수 이름", 80)
            if code and name:
                rank = integer(athlete.get("rank"), "팀 내 선수 순위")
                if not 1 <= rank <= 10:
                    fail("팀 내 선수 순위")
                athletes.append({"rank": rank, "code": code, "name": name, "value": optional_text(athlete.get("value"), "팀 내 선수 기록", 40), "imageUrl": image_url(athlete.get("imageUrl"), "선수 사진")})
        groups.append({"title": text(item.get("title"), "팀 내 순위 이름", 40), "athletes": athletes})
    return groups


def _roster_items_response(payload):
    athletes = [{"code": text(obj(raw, "선수단 선수").get("code"), "선수 코드", 40), "name": text(obj(raw, "선수단 선수").get("name"), "선수 이름", 80), "imageUrl": image_url(obj(raw, "선수단 선수").get("imageUrl"), "선수 사진"), "backNumber": optional_text(obj(raw, "선수단 선수").get("backNumber"), "등번호", 20)} for raw in array(success_data(payload).get("items"), "선수단", 100)]
    if len({item["code"] for item in athletes}) != len(athletes):
        fail("선수단 중복 선수")
    return athletes


def _roaster_band_response(payload):
    """Parse the /roaster response band without shadowing the item contract."""
    data = success_data(payload)
    bands = array(data.get("bands"), "bands", 30)
    roster_bands = [
        item for item in bands
        if isinstance(item, dict)
        and (item.get("bandType") == "KBO_TEAM_ROASTER" or item.get("bandName") == "KBO_TEAM_ROASTER")
    ]
    if len(roster_bands) != 1:
        fail("KBO_TEAM_ROASTER band")
    items = array(roster_bands[0].get("items"), "roster items", 100)
    athletes = [{
        "code": text((raw.get("player") if isinstance(raw.get("player"), dict) else raw).get("code"), "player code", 40),
        "name": text((raw.get("player") if isinstance(raw.get("player"), dict) else raw).get("name"), "player name", 80),
        "imageUrl": image_url((raw.get("player") if isinstance(raw.get("player"), dict) else raw).get("imageUrl"), "player image"),
        "backNumber": optional_text((raw.get("player") if isinstance(raw.get("player"), dict) else raw).get("backNumber"), "back number", 20),
    } for raw in items]
    if len({item["code"] for item in athletes}) != len(athletes):
        fail("duplicate player code")
    return athletes


# 기존 roster crawler가 사용하는 내부 이름을 유지합니다.
_roster = _roaster_band_response


def parse_team_detail(code, payload, rankings, rosters):
    if code not in TEAM_CODES:
        fail("구단 코드")
    data = success_data(payload)
    season_band, schedule_band, shortcut_band = (band_from_data(data, kind) for kind in ("KBO_TEAM_SEASON_RECORD", "SPORTS_TEAM_SCHEDULE", "SPORTS_TEAM_SHORTCUT"))
    season_items = array(season_band.get("items"), "시즌 기록", 2)
    if len(season_items) != 1:
        fail("시즌 기록")
    season = obj(season_items[0], "시즌 기록")
    parsed_rosters = {
        position: _roster_items_response(rosters[position])
        for position in POSITIONS
    }
    all_codes = [athlete["code"] for values in parsed_rosters.values() for athlete in values]
    if len(set(all_codes)) != len(all_codes):
        fail("포지션 간 중복 선수")
    games = []
    for raw in array(schedule_band.get("items"), "팀 경기 일정", 30):
        item = obj(raw, "팀 경기")
        value = str(integer(item.get("dateTime"), "경기 시각"))
        if not re.fullmatch(r"\d{12}", value) or int(value[8:10]) > 23 or int(value[10:]) > 59:
            fail("경기 시각 형식")
        def team(raw_team):
            team_item = obj(raw_team, "경기 팀")
            score = team_item.get("score")
            return {"code": text(team_item.get("code"), "경기 팀 코드", 3), "name": text(team_item.get("name"), "경기 팀 이름", 30), "score": integer(score, "점수") if score is not None else None}
        away, home = team(item.get("away")), team(item.get("home"))
        status = text(item.get("status"), "경기 상태", 30)
        if status not in {"NOW", "END", "SUSPENDED"}:
            away["score"] = home["score"] = None
        day, time = f"{value[:4]}-{value[4:6]}-{value[6:8]}", f"{value[8:10]}:{value[10:]}"
        games.append({"id": text(item.get("code"), "경기 코드", 60), "startsAt": f"{day}T{time}:00+09:00", "time": time, "stadium": text(item.get("stadium"), "경기장", 50), "status": status, "away": away, "home": home})
    shortcuts = []
    for raw in array(shortcut_band.get("items"), "다른 구단", 10):
        item = obj(raw, "다른 구단")
        shortcut_code = text(item.get("code"), "다른 구단 코드", 2).upper()
        if shortcut_code not in TEAM_CODES:
            fail("다른 구단 코드")
        shortcuts.append({"code": shortcut_code, "name": text(item.get("name"), "다른 구단 이름", 30), "imageUrl": image_url(item.get("imageUrl"), "구단 로고")})
    return {"code": code, "teamName": text(season_band.get("teamName"), "구단 이름", 50), "shortName": text(season_band.get("teamShortName"), "구단 짧은 이름", 30), "teamImageUrl": image_url(data.get("teamImageUrl"), "구단 로고"), "backgroundImage": image_url(data.get("backgroundImage"), "구단 배경"), "seasonTitle": text(season_band.get("bandName"), "시즌 제목", 80), "mainRecords": _records(season.get("mainRecords"), "주요 기록"), "boxRecords": _records(season.get("boxRecords"), "세부 기록"), "schedule": games, "rankings": {kind: _ranking_groups(rankings[kind]) for kind in ATHLETE_TYPES}, "rosters": parsed_rosters, "shortcuts": shortcuts}


def parse_athlete_detail(code, payload):
    data = success_data(payload)
    profile_band = band_from_data(data, "KBO_ATHLETE_PROFILE")
    season_band = band_from_data(data, "KBO_ATHLETE_SEASON_RECORD", True)
    career_band = band_from_data(data, "KBO_ATHLETE_WHOLE_RECORD", True)
    profiles = array(profile_band.get("items"), "선수 프로필", 2)
    if len(profiles) != 1:
        fail("선수 프로필")
    item = obj(profiles[0], "선수 프로필")
    if text(item.get("code"), "선수 코드", 40) != code:
        fail("요청 선수와 프로필이 다릅니다")
    team = obj(item.get("team"), "선수 소속팀")
    team_code = text(team.get("code"), "소속팀 코드", 2).upper()
    if team_code not in TEAM_CODES:
        fail("소속팀 코드")
    profile = {"code": code, "name": text(item.get("name"), "선수 이름", 80), "imageUrl": image_url(item.get("imageUrl"), "선수 사진"), "positions": [text(value, "선수 포지션", 40) for value in array(item.get("positions"), "선수 포지션", 8)], "backNumber": optional_text(item.get("backNumber"), "등번호", 20), "joinDate": optional_text(item.get("joinDate"), "입단일", 40), "birthDate": optional_text(item.get("birthDate"), "생년월일", 40), "body": [text(value, "신체", 30) for value in array(item.get("body") or [], "신체", 5)], "education": optional_text(item.get("education"), "경력", 200), "draftOrder": optional_text(item.get("draftOrder"), "지명 순위", 100), "team": {"name": text(team.get("name"), "소속팀 이름", 50), "code": team_code, "color": optional_text(team.get("color"), "소속팀 색상", 20), "logoUrl": image_url(team.get("logoUrl"), "소속팀 로고")}}
    season_records = []
    if season_band:
        for raw in array(season_band.get("items"), "시즌 기록", 30):
            record = obj(raw, "시즌 기록")
            label = obj(record.get("label"), "기록 표시") if record.get("label") is not None else {}
            graphs = []
            for raw_group in array(record.get("graphRecords") or [], "그래프 기록", 10):
                group = obj(raw_group, "그래프 기록")
                graph_type = text(group.get("type"), "그래프 유형", 30)
                if graph_type not in {"line_filled", "line", "dot", "bar"}:
                    fail("그래프 유형")
                for raw_graph in array(group.get("graphs"), "그래프", 10):
                    graph = obj(raw_graph, "그래프")
                    graphs.append({"type": graph_type, "color": optional_text(graph.get("color"), "그래프 색상", 20), "points": [{"x": text(obj(point, "그래프 점").get("xvalue"), "그래프 X값", 60), "y": text(obj(point, "그래프 점").get("yvalue"), "그래프 Y값", 60), "description": None if obj(point, "그래프 점").get("description") in (None, "") else text(obj(point, "그래프 점").get("description"), "그래프 설명", 100)} for point in array(graph.get("points"), "그래프 점", 100)]})
            rank = record.get("rank")
            season_records.append({"title": text(record.get("title"), "시즌 기록 이름", 60), "value": text(record.get("value"), "시즌 기록 값", 60), "rank": None if rank in (None, "") else text(rank, "시즌 기록 순위", 40), "isFirstRank": label.get("isFirstRank") is True, "graphs": graphs})
    columns, rows = [], []
    if career_band:
        columns = [{"name": text(obj(raw, "통산 기록 열").get("name"), "통산 기록 열 이름", 40), "key": text(obj(raw, "통산 기록 열").get("variableName"), "통산 기록 열 키", 60)} for raw in array(career_band.get("column"), "통산 기록 열", 60)]
        if not columns or columns[0]["key"] != "season" or len({column["key"] for column in columns}) != len(columns):
            fail("통산 기록 열")
        rows = [{column["key"]: optional_text(obj(raw, "통산 기록 행").get(column["key"]), f"통산 기록 {column['name']}", 60) for column in columns} for raw in array(career_band.get("items"), "통산 기록 행", 100)]
    return {"profile": profile, "seasonTitle": text(season_band.get("bandName"), "시즌 기록 제목", 80) if season_band else "시즌 기록", "seasonRecords": season_records, "careerTitle": text(career_band.get("bandName"), "통산 기록 제목", 80) if career_band else "통산 기록", "careerColumns": columns, "careerRows": rows}
