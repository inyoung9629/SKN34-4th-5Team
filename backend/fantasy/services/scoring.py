from decimal import Decimal

from django.db.models import Sum

from fantasy.models import FantasyBattingGameStat, FantasyPitchingGameStat
from fantasy.services.schedule import NON_SCORING_GAME_STATUSES

BASE_SCORE = {
    "at_bats": Decimal("1"), "hits": Decimal("4"), "rbi": Decimal("5"), "runs": Decimal("5"),
    "saves": Decimal("30"), "batters_faced": Decimal("1"), "strikeouts": Decimal("3"), "pitch_count": Decimal("0.1"),
}
WEIGHT_MIN, WEIGHT_MAX, NEUTRAL_WEIGHT = 1, 9, 5
BATTER_STATS = ("at_bats", "hits", "rbi", "runs")
PITCHER_STATS = ("saves", "batters_faced", "strikeouts", "pitch_count")
PITCHER_POSITION_NAMES = {"pitcher", "투수"}


def player_fantasy_type(player):
    positions = {str(value).lower() for value in (player.positions or [])}
    return "PITCHER" if positions & PITCHER_POSITION_NAMES else "BATTER"


def _score_values(selection):
    week = selection.week
    filters = {
        "game__game_date__gte": week.week_start,
        "game__game_date__lte": week.week_end,
        "player": selection.player,
    }
    if selection.fantasy_type == "PITCHER":
        queryset = FantasyPitchingGameStat.objects.filter(**filters)
        rows = _exclude_non_scoring_games(queryset).aggregate(
            saves=Sum("saves"),
            batters_faced=Sum("batters_faced"),
            strikeouts=Sum("strikeouts"),
            pitch_count=Sum("pitch_count"),
        )
        return rows
    queryset = FantasyBattingGameStat.objects.filter(**filters)
    return _exclude_non_scoring_games(queryset).aggregate(**{key: Sum(key) for key in BATTER_STATS})


def _exclude_non_scoring_games(queryset):
    for status in NON_SCORING_GAME_STATUSES:
        queryset = queryset.exclude(game__status_code__iexact=status)
    return queryset


def score_selection(selection):
    if not selection.is_confirmed:
        return Decimal("0")
    values = _score_values(selection)
    stats = PITCHER_STATS if selection.fantasy_type == "PITCHER" else BATTER_STATS
    return sum(
        Decimal(values.get(stat) or 0) * BASE_SCORE[stat] * Decimal(selection.stat_weights[stat]) / NEUTRAL_WEIGHT
        for stat in stats
    )


def score_user_week(user, week):
    return sum((score_selection(selection) for selection in week.selections.filter(user=user)), Decimal("0"))
