from decimal import Decimal, ROUND_DOWN

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import PointTransaction, PointWallet
from accounts.point_service import grant_points
from fantasy.models import (
    FantasyBattingGameStat,
    FantasyPitchingGameStat,
    FantasyPlayerScore,
    FantasySettlement,
    FantasyUserResult,
    FantasyWeek,
)
from fantasy.services.schedule import scheduled_games
from fantasy.services.scoring import score_selection

TEST_SETTLEMENT_SOURCE_TYPE = "fantasy.FantasyTestSettlement"


class FantasySettlementNotReady(Exception):
    pass


class FantasyTestSettlementConflict(Exception):
    pass


class FantasyTestSettlementNotFound(Exception):
    pass


def _point_amount(final_score):
    return int(
        (max(final_score, Decimal("0")) * settings.FANTASY_POINT_PAYOUT_RATE).quantize(
            Decimal("1"), rounding=ROUND_DOWN
        )
    )


def validate_week_stats_complete(week):
    games = list(scheduled_games(week).select_related("home_team", "away_team").order_by("game_date", "game_time", "id"))
    if not games:
        raise FantasySettlementNotReady("해당 주차의 경기 일정이 없어 결산할 수 없습니다.")
    for game in games:
        if not game.home_team_id or not game.away_team_id:
            raise FantasySettlementNotReady(f"{game.game_date} {game.game_time} 경기의 양 팀 일정 정보가 없습니다.")
        expected_teams = {game.home_team_id, game.away_team_id}
        for stat_model, kind in (
            (FantasyBattingGameStat, "타자"),
            (FantasyPitchingGameStat, "투수"),
        ):
            recorded_teams = set(
                stat_model.objects.filter(game=game).values_list("team_id", flat=True).distinct()
            )
            if recorded_teams != expected_teams:
                raise FantasySettlementNotReady(
                    f"{game.game_date} {game.game_time} 경기의 {kind} 기록이 양 팀 모두 입력되지 않았습니다."
                )


def validate_week_ready(week):
    validate_week_stats_complete(week)
    if week.stats_finalized_at is None:
        raise FantasySettlementNotReady("주차 경기 기록 완료 확인 후 결산할 수 없습니다.")
    if week.selections.filter(is_confirmed=False).exists():
        raise FantasySettlementNotReady("확정되지 않은 선수 배율이 남아 있어 결산할 수 없습니다.")


@transaction.atomic
def settle_week(week_id):
    week = FantasyWeek.objects.select_for_update().get(pk=week_id)
    if week.status == FantasyWeek.SETTLED:
        return week.settlement
    validate_week_ready(week)
    return _create_settlement(week, source_type="fantasy.FantasySettlement")


@transaction.atomic
def settle_week_for_testing(week_id):
    week = FantasyWeek.objects.select_for_update().get(pk=week_id)
    has_existing_settlement = FantasySettlement.objects.filter(week=week).exists()
    if week.status == FantasyWeek.SETTLED or has_existing_settlement:
        raise FantasyTestSettlementConflict("이번 주차는 이미 결산되어 있습니다.")
    if not week.selections.exists():
        raise FantasySettlementNotReady("이번 주차에 결산할 선수 선택이 없습니다.")
    return _create_settlement(week, source_type=TEST_SETTLEMENT_SOURCE_TYPE)


def _create_settlement(week, *, source_type):
    now = timezone.now()
    settlement = FantasySettlement.objects.create(week=week, settled_at=now)
    user_ids = week.selections.values_list("user_id", flat=True).distinct()
    for user_id in user_ids:
        selections = list(week.selections.filter(user_id=user_id).select_related("player"))
        scores = [(selection, score_selection(selection)) for selection in selections]
        final_score = sum((score for _, score in scores), Decimal("0"))
        payout = _point_amount(final_score)
        result = FantasyUserResult.objects.create(settlement=settlement, user_id=user_id, final_score=final_score, point_amount=payout)
        FantasyPlayerScore.objects.bulk_create([FantasyPlayerScore(user_result=result, player=selection.player, score=score) for selection, score in scores])
        grant_points(
            user_id=user_id,
            amount=payout,
            source_type=source_type,
            source_key=f"{settlement.pk}:{user_id}",
            description=f"환상야구 {week.week_start} 주차 결산 지급",
        )
    week.status = FantasyWeek.SETTLED
    week.settled_at = now
    week.save(update_fields=("status", "settled_at", "updated_at"))
    return settlement


@transaction.atomic
def cancel_test_settlement(week_id):
    week = FantasyWeek.objects.select_for_update().get(pk=week_id)
    settlement = FantasySettlement.objects.filter(week=week).first()
    if settlement is None:
        raise FantasyTestSettlementNotFound("취소할 테스트 결산이 없습니다.")

    results = list(settlement.results.select_related("user").all())
    if not results or not PointTransaction.objects.filter(
        source_type=TEST_SETTLEMENT_SOURCE_TYPE,
        source_key=f"{settlement.pk}:{results[0].user_id}",
        user_id=results[0].user_id,
    ).exists():
        raise FantasyTestSettlementConflict("테스트 지급이 아닌 결산은 이 버튼으로 취소할 수 없습니다.")

    reversed_total = 0
    for result in results:
        source_key = f"{settlement.pk}:{result.user_id}"
        original = PointTransaction.objects.filter(
            user_id=result.user_id,
            source_type=TEST_SETTLEMENT_SOURCE_TYPE,
            source_key=source_key,
        ).first()
        if original is None or original.amount != result.point_amount:
            raise RuntimeError("테스트 결산 포인트 원장을 확인할 수 없습니다.")

        wallet = PointWallet.objects.select_for_update().get(user_id=result.user_id)
        reversal_key = f"{settlement.pk}:{result.user_id}"
        PointTransaction.objects.create(
            user_id=result.user_id,
            wallet=wallet,
            amount=-result.point_amount,
            transaction_type="FANTASY_SETTLEMENT_REVERSAL",
            source_type="fantasy.FantasyTestSettlementCancellation",
            source_key=reversal_key,
            description=f"환상야구 {week.week_start} 주차 테스트 결산 취소",
        )
        wallet.balance -= result.point_amount
        wallet.save(update_fields=("balance", "updated_at"))
        reversed_total += result.point_amount

    settlement.delete()
    week.status = FantasyWeek.OPEN
    week.settled_at = None
    week.save(update_fields=("status", "settled_at", "updated_at"))
    return len(results), reversed_total
