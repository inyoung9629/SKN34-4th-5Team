"""DB/일정 원천 기반의 결정론적 경기 선택. 팀의 홈구장을 추정하지 않는다."""
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import cached_property
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from baseball.models import Game, ScheduleDay, Stadium
from tving.relational import TEAM_MAP

KST = ZoneInfo("Asia/Seoul")
SCHEDULED = ("scheduled", "PREV", "READY")


def get_game_range_freshness(start, end):
    """Read the requested crawler window, not an unrelated complete month.

    This follows develop's DB-only schedule policy: no synchronous provider
    refresh, fabricated empty days, or modifications to collected fixtures.
    The marker/code consistency is checked again by _fresh_query below.
    """
    rows = list(ScheduleDay.objects.filter(date__range=(start, end)).values(
        "source_fetched_at", "last_synced_at"))
    return {"stale": len(rows) != (end-start).days+1 or any(
        not r["source_fetched_at"] or not r["last_synced_at"] for r in rows)}
# 경기 원천의 구장 약칭만 정규화한다. 사용자 발화의 지역→구장 추정에는 사용하지 않는다.
SOURCE_STADIUM_ALIASES = {
    "JAMSIL": ("잠실",), "GOCHEOK": ("고척",), "MUNHAK": ("문학", "인천"),
    "SUWON": ("수원",), "DAEJEON": ("대전",), "DAEGU": ("대구",),
    "GWANGJU": ("광주",), "SAJIK": ("사직",), "CHANGWON": ("창원",),
}


@dataclass
class GameSearch:
    games: list = field(default_factory=list)
    start: str = ""
    end: str = ""
    problem: str | None = None
    dual: bool = False
    tied: bool = False


def stadium_catalog():
    return list(Stadium.objects.order_by("stadium_code").values("stadium_code", "stadium_name_ko"))


def snapshot(game, team_code=None):
    reverse = {value: key for key, value in TEAM_MAP.items()}
    home = reverse.get(game.home_team.team_code, game.home_team.team_code)
    away = reverse.get(game.away_team.team_code, game.away_team.team_code)
    return {
        "id": game.id, "game_code": game.game_code,
        "date": game.game_date.isoformat(), "time": game.game_time.isoformat(),
        "starts_at": datetime.combine(game.game_date, game.game_time, KST).isoformat(),
        "home_team": home, "away_team": away,
        "home_name": game.home_team.team_name_ko, "away_name": game.away_team.team_name_ko,
        "stadium_id": game.stadium_id, "stadium_code": game.stadium.stadium_code,
        "stadium_name": game.stadium.stadium_name_ko, "status": game.status_code,
        "side": "home" if home == team_code else "away" if away == team_code else None,
        "source": game.source,
        "fetched_at": game.source_fetched_at.isoformat() if game.source_fetched_at else None,
    }


def matching(query, conditions):
    """전체 조건을 LIMIT 전에 적용한다. 프로필의 두 글자 코드와 DB 코드는 다르다."""
    for key in ("team_code", "opponent_code"):
        code = conditions.get(key)
        if code:
            codes = (code, TEAM_MAP[code])
            query = query.filter(Q(home_team__team_code__in=codes) | Q(away_team__team_code__in=codes))
    # 구장은 원천 구장명으로 정규화한 다음 필터링한다. DB FK가 과거 CSV 값일 수 있다.
    if conditions.get("game_id"):
        query = query.filter(pk=conditions["game_id"])
    if conditions.get("team_code") and conditions.get("home_away") in ("home", "away"):
        code = conditions["team_code"]
        query = query.filter(**{f"{conditions['home_away']}_team__team_code__in": (code, TEAM_MAP[code])})
    for key, lookup in (("game_time_min", "gte"), ("game_time_max", "lte")):
        if conditions.get(key):
            query = query.filter(**{f"game_time__{lookup}": time.fromisoformat(conditions[key])})
    return query


class GameRepository:
    def __init__(self, clock=None):
        self.clock = clock or timezone.now

    @cached_property
    def stadium_names(self):
        result = {}
        for stadium in Stadium.objects.all():
            for name in (stadium.stadium_code, stadium.stadium_name_ko,
                         *SOURCE_STADIUM_ALIASES.get(stadium.stadium_code, ())):
                key = name.replace(" ", "").casefold()
                if key in result and result[key] != stadium:
                    result[key] = None  # 충돌한 이름으로 임의 결정하지 않는다.
                else:
                    result[key] = stadium
        return result

    def actual_stadium(self, game):
        if game.source_stadium_name:
            # 원천명과 기존 FK가 다르면 원천명이 우선. 모르는 구장은 홈구장으로 대체하지 않는다.
            game.stadium = self.stadium_names.get(game.source_stadium_name.replace(" ", "").casefold())
        return game

    def _fresh_query(self, start, end):
        freshness = get_game_range_freshness(start, end)
        if freshness.get("stale"):
            return None, "stale"
        days = list(ScheduleDay.objects.filter(date__range=(start, end)))
        if len(days) != (end - start).days + 1:
            return None, "incomplete"
        codes = []
        for day in days:
            if (day.status not in ("ready", "empty") or not day.source_fetched_at
                    or len(day.game_codes) != day.game_count):
                return None, "incomplete"
            codes.extend(day.game_codes)
        # 원천에서 사라진 과거 CSV 경기/이월 경기가 잘못 재선택되지 않게 날짜별 활성 ID만 사용.
        query = Game.objects.filter(game_date__range=(start, end), source_external_code__in=codes)
        if query.count() != len(codes):
            return None, "incomplete"
        active_dates = {code: day.date for day in days for code in day.game_codes}
        if any(active_dates[code] != day for code, day in query.values_list("source_external_code", "game_date")):
            return None, "incomplete"
        return query.select_related("stadium", "home_team", "away_team"), None

    def search(self, conditions, now):
        now = now.astimezone(KST)
        start = max(date.fromisoformat(conditions.get("date_from", now.date().isoformat())), now.date())
        # 기본 검색 범위는 현재 시즌 연말까지. 미발표 차기 시즌을 임의로 만들지 않는다.
        end = date.fromisoformat(conditions.get("date_to", date(now.year, 12, 31).isoformat()))
        if conditions.get("game_id"):
            target = Game.objects.filter(pk=conditions["game_id"]).first()
            if not target:
                return GameSearch(problem="missing_game")
            if not conditions.get("date_from"):
                start, end = max(target.game_date, now.date()), target.game_date
        dual = bool(conditions.get("team_code") and not any(conditions.get(k) for k in
                    ("stadium_code", "opponent_code", "game_id", "single_game"))
                    and conditions.get("home_away") not in ("home", "away"))
        result = GameSearch(start=start.isoformat(), end=end.isoformat(), dual=dual)
        if end < start:
            result.problem = "past"
            return result
        if (end - start).days > 366:
            result.problem = "range"
            return result
        missing_sides = ["home", "away"] if dual else [None]
        cursor = start
        while cursor <= end and missing_sides:
            month_end = min(end, cursor.replace(day=monthrange(cursor.year, cursor.month)[1]))
            # The crawler may store a rolling window rather than a full month.
            # Only a contiguous known prefix can prove the nearest game. Never
            # skip a missing day to pick a later game, nor demand days AFTER an
            # already proven nearest game. One small date query per month.
            stored_days = set(ScheduleDay.objects.filter(date__range=(cursor, month_end))
                              .values_list("date", flat=True))
            gap = cursor
            while gap <= month_end and gap in stored_days:
                gap += timedelta(days=1)
            if gap == cursor:
                result.problem = "incomplete"
                return result
            checked_end = min(month_end, gap - timedelta(days=1))
            query, problem = self._fresh_query(cursor, checked_end)
            result.end = checked_end.isoformat()
            if problem:
                result.problem = problem
                return result
            # 원천 동기화가 길어져도 이미 시작한 경기를 선택하지 않는다.
            now = max(now, self.clock().astimezone(KST))
            query = matching(query, conditions).filter(
                Q(game_date__gt=now.date()) | Q(game_date=now.date(), game_time__gt=now.time().replace(tzinfo=None)),
                status_code__in=SCHEDULED,
            )
            for side in missing_sides[:]:
                side_query = query
                if side:
                    code = conditions["team_code"]
                    side_query = side_query.filter(**{f"{side}_team__team_code__in": (code, TEAM_MAP[code])})
                rows = []
                for row in side_query.order_by("game_date", "game_time", "id"):
                    self.actual_stadium(row)
                    requested_stadium = conditions.get("stadium_code")
                    if row.stadium_id and requested_stadium and row.stadium.stadium_code != requested_stadium:
                        continue
                    rows.append(row)
                    if len(rows) == 2:
                        break
                if not rows:
                    continue
                # 미확정 구장/시각이 있는 가장 가까운 경기를 건너뛰어 다음 경기를 확정하지 않는다.
                if not rows[0].stadium_id or not rows[0].home_team_id or not rows[0].away_team_id or rows[0].game_time == time(0):
                    result.problem = "incomplete"
                    return result
                result.games.append(snapshot(rows[0], conditions.get("team_code")))
                if len(rows) > 1 and (rows[0].game_date, rows[0].game_time) == (rows[1].game_date, rows[1].game_time):
                    if not rows[1].stadium_id or not rows[1].home_team_id or not rows[1].away_team_id:
                        result.problem = "incomplete"
                        return result
                    result.games.append(snapshot(rows[1], conditions.get("team_code")))
                    result.tied = True
                missing_sides.remove(side)
            if missing_sides and checked_end < month_end:
                result.problem = "incomplete"
                return result
            cursor = month_end + timedelta(days=1)
        # 표시 순서를 고정해 '1안/2안' 후속 선택이 흔들리지 않게 한다.
        if dual:
            result.games.sort(key=lambda game: (game["side"] != "home", game["starts_at"], game["id"]))
        return result

    def revalidate(self, anchor, conditions, now):
        """선택된 경기 ID를 재조회한다. 취소/종료/이동이면 다음 경기를 조용히 대체하지 않는다."""
        row = Game.objects.filter(pk=anchor["id"]).first()
        if not row:
            return None, "missing_game"
        query, problem = self._fresh_query(row.game_date, row.game_date)
        if problem:
            return None, problem
        row = query.filter(pk=anchor["id"]).first()
        if row:
            self.actual_stadium(row)
        if not row or not row.stadium_id or not row.home_team_id or not row.away_team_id or row.game_time == time(0):
            return None, "incomplete"
        current = snapshot(row, conditions.get("team_code"))
        now = max(now.astimezone(KST), self.clock().astimezone(KST))
        if row.status_code not in SCHEDULED or datetime.fromisoformat(current["starts_at"]) <= now.astimezone(KST):
            return current, "expired"
        if (not matching(query, conditions).filter(pk=row.pk).exists()
                or conditions.get("stadium_code", current["stadium_code"]) != current["stadium_code"]
                or conditions.get("date_from", current["date"]) > current["date"]
                or conditions.get("date_to", current["date"]) < current["date"]):
            return current, "condition_conflict"
        if any(current[k] != anchor[k] for k in ("starts_at", "stadium_code", "home_team", "away_team")):
            return current, "changed"
        return current, None
