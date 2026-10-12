from django.db import transaction

from accounts.models import CustomUser, PointTransaction, PointWallet


@transaction.atomic
def grant_points(*, user=None, user_id=None, amount, source_type, source_key, description=""):
    """멱등적으로 포인트를 지급하고 잔액과 원장을 함께 갱신한다."""
    if amount < 0:
        raise ValueError("지급 포인트는 음수일 수 없습니다.")
    if user is None:
        user = CustomUser.objects.get(pk=user_id)
    wallet, _ = PointWallet.objects.select_for_update().get_or_create(user=user)
    existing = PointTransaction.objects.filter(user=user, source_type=source_type, source_key=source_key).first()
    if existing:
        return wallet, existing, False
    wallet.balance += amount
    wallet.save(update_fields=("balance", "updated_at"))
    entry = PointTransaction.objects.create(
        user=user, wallet=wallet, amount=amount,
        transaction_type=PointTransaction.FANTASY_SETTLEMENT if source_type.startswith("fantasy.") else PointTransaction.ACTIVITY_EARNED,
        source_type=source_type, source_key=source_key, description=description,
    )
    return wallet, entry, True
