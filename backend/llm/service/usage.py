"""채팅 후불 사용량. 1 credit = provider 입력+출력 1000 토큰.

양수 잔액이면 한 턴을 허용하고 지갑 행 잠금으로 동시 턴을 막는다. 예상 토큰은 예약하지 않는다.
모든 중첩 model run을 run_id당 한 번 계량하고 producer 종료 후 알려진 사용량 전부를 정산한다.
모르는 호출은 settled_unknown으로 남기며 예상 비용을 만들지 않는다. 다음 턴은 실제 잔액으로 판단한다.
기존 reserved 원장은 금액을 차감하지 않고 active guard로 유지한다(지난 달도 포함).
ponytail: 죽은 worker를 추측하여 자동 해제하지 않는다. 운영자가 종료 확인 후 settle_abandoned로 푼다.
"""
import logging
import threading
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

from llm.models import UsageCharge, UsageWallet

log = logging.getLogger(__name__)
TOKENS_PER_CREDIT = 1000
GUEST_PERIOD = "lifetime"
EXHAUSTED_CODE = "usage_exhausted"
EXHAUSTED_MESSAGE = "사용 가능한 크레딧을 모두 사용했어요."
BUSY_CODE = "usage_busy"
BUSY_MESSAGE = "진행 중인 답변이 끝난 뒤 다시 시도해 주세요."


def _conf(name, default):
    return getattr(settings, name, default)


class InsufficientCredits(Exception):
    """예약할 잔액이 없다. view 는 402 {code: usage_exhausted} 로 바꾼다."""


class WalletBusy(Exception):
    """같은 지갑의 provider 작업이 아직 끝나지 않았다."""


class UnsafeOutputLimit(Exception):
    """서버 출력 상한이 없는 호출. 잔액 부족과는 별개다."""


def _now():
    return timezone.now()


def period_for(member, now=None):
    if not member:
        return GUEST_PERIOD
    return (now or _now()).astimezone(ZoneInfo(_conf("USAGE_TIMEZONE", "Asia/Seoul"))).strftime("%Y-%m")


def _limit(member):
    return _conf("USAGE_MEMBER_MONTHLY_TOKENS", 999_999_000) if member else _conf("USAGE_GUEST_TOKENS", 500_000)


def _resets_at(member, now=None):
    if not member:
        return None
    local = (now or _now()).astimezone(ZoneInfo(_conf("USAGE_TIMEZONE", "Asia/Seoul")))
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return datetime(year, month, 1, tzinfo=local.tzinfo)


def _owner(session=None, user=None, guest=None):
    if session is not None:
        return ({"user_id": session.user_id} if session.user_id else {"guest": session.guest})
    return {"user_id": user.pk} if user is not None else {"guest": guest}


def _locked_wallet(owner):
    """지갑 행을 잠가 돌려준다(없으면 만든다). 달이 바뀌었으면 used 를 0 으로. transaction 안에서 부른다."""
    member = "user_id" in owner
    period = period_for(member)
    try:
        with transaction.atomic():
            UsageWallet.objects.get_or_create(**owner, defaults={"period": period})
    except IntegrityError:
        pass  # 동시 생성: 다른 요청이 먼저 만들었다
    wallet = UsageWallet.objects.select_for_update().get(**owner)
    period = period_for(member)
    if wallet.period != period:
        wallet.period, wallet.used_tokens = period, 0
        wallet.save(update_fields=["period", "used_tokens", "updated_at"])
    return wallet


def _apply(wallet, charge, tokens):
    charge.status = UsageCharge.SETTLED_UNKNOWN if charge.unknown_calls else UsageCharge.SETTLED
    charge.charged_tokens, charge.settled_at = tokens, _now()
    charge.save(update_fields=["status", "charged_tokens", "settled_at", "input_tokens", "output_tokens",
                               "calls", "unknown_calls"])
    if charge.period == wallet.period:  # 달이 넘어간 뒤 끝난 턴은 지난 달 몫이라 새 달 사용량에 넣지 않는다
        wallet.used_tokens += tokens
        wallet.save(update_fields=["used_tokens", "updated_at"])


def reserve(session):
    """양수 잔액으로 한 턴을 시작한다. provider 호출은 transaction 밖에서만."""
    owner = _owner(session)
    with transaction.atomic():
        wallet = _locked_wallet(owner)
        if wallet.charges.filter(status=UsageCharge.RESERVED).exists():
            raise WalletBusy
        available = _limit("user_id" in owner) - wallet.used_tokens
        if available <= 0:
            raise InsufficientCredits
        return UsageCharge.objects.create(
            wallet=wallet, session_id=session.id, period=wallet.period,
            reserved_tokens=0,
        )


def settle(charge, meter=None, *, unknown=False):
    """예약을 실제 토큰으로 정산하고 남은 예약을 푼다. 멱등(이미 정산됐으면 아무것도 안 한다)."""
    with transaction.atomic():
        wallet = UsageWallet.objects.select_for_update().get(pk=charge.wallet_id)
        charge = UsageCharge.objects.select_for_update().get(pk=charge.pk)
        if charge.status != UsageCharge.RESERVED:
            return charge
        if meter is not None:
            charge.input_tokens, charge.output_tokens, charge.calls, charge.unknown_calls = meter.totals()
        if unknown:
            charge.unknown_calls = max(1, charge.unknown_calls)
            charge.calls = max(charge.calls, charge.unknown_calls)
        _apply(wallet, charge, charge.input_tokens + charge.output_tokens)
        return charge


def settle_abandoned(charge):
    """운영자가 worker 종료 확인 후 해제. 기존 알려진 사용량을 보존하고 미확인으로 표시."""
    return settle(charge, unknown=True)


def balance(user=None, guest=None):
    """요청자 본인의 사용량 DTO. 비회원 쿠키가 없으면(아직 대화 전) 새 지갑 기준 값을 만들지 않고 계산만 한다."""
    member = user is not None
    used = reserved = unknown = 0
    active = False
    period = period_for(member)
    if member or guest is not None:
        with transaction.atomic():
            if UsageWallet.objects.filter(**_owner(user=user, guest=guest)).exists():
                wallet = _locked_wallet(_owner(user=user, guest=guest))
                used, period = wallet.used_tokens, wallet.period
                active = wallet.charges.filter(status=UsageCharge.RESERVED).exists()
                reserved = wallet.charges.filter(status=UsageCharge.RESERVED).aggregate(total=Sum("reserved_tokens"))["total"] or 0
                unknown = wallet.charges.filter(period=period).aggregate(total=Sum("unknown_calls"))["total"] or 0
    limit = _limit(member)
    remaining = max(0, limit - used)
    resets = _resets_at(member)
    return {
        "plan": "member" if member else "guest",
        "period": period,
        "timezone": _conf("USAGE_TIMEZONE", "Asia/Seoul") if member else None,
        "resets_at": resets.isoformat() if resets else None,
        "tokens_per_credit": TOKENS_PER_CREDIT,
        "limit_tokens": limit,
        "used_tokens": used,
        "reserved_tokens": reserved,
        "remaining_tokens": remaining,
        "remaining_credits": str((Decimal(remaining) / TOKENS_PER_CREDIT).quantize(Decimal("0.001"))),
        "active_turn": active,
        "unknown_calls": unknown,
        "accounting_state": "unknown" if unknown else "known",
        "can_send": remaining > 0 and not active,
    }


# ── 계량 ─────────────────────────────────────────────────────────────────────
class Meter(BaseCallbackHandler):
    """이 턴의 모든 provider 사용량. run_id 당 한 번, 예산 예측 없음."""
    raise_error = True  # 서버 출력 상한 유지

    def __init__(self):
        super().__init__()
        self.finished = threading.Event()  # 원본 스트림이 닫혀 더 이상 model 호출이 없다
        self._lock = threading.Lock()
        self._done = set()
        self.abandoned = False  # finish 가 포기함 → worker 가 끝날 때 정산
        self._in = self._out = self._calls = self._unknown = 0
        self._inflight = {}  # terminal callback 없는 시작된 호출도 미확인으로 보존

    def check(self, run_id=None):
        """이미 허용된 턴은 잔액 예측으로 중단하지 않는다. 시작된 run만 추적한다."""
        with self._lock:
            if run_id is not None and run_id not in self._done:
                self._inflight[run_id] = None

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
        params = kwargs.get("invocation_params") or {}
        limit = params.get("max_completion_tokens", params.get("max_tokens", params.get("max_output_tokens")))
        if not _valid(limit) or limit <= 0 or limit > _conf("USAGE_MAX_CALL_OUTPUT_TOKENS", 4000):
            raise UnsafeOutputLimit
        self.check(run_id=run_id)

    def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):
        raise UnsafeOutputLimit

    def on_llm_end(self, response, *, run_id, **kwargs):
        self.record(_usage(response), run_id)

    def on_llm_error(self, error, *, run_id, **kwargs):
        # 오류·중단(Stop=GeneratorExit)으로 끝난 호출: 부분 응답에 usage 가 있으면 그것, 없으면 미확인
        response = kwargs.get("response")
        self.record(_usage(response) if response is not None else None, run_id)

    def record(self, usage, run_id=None):
        """usage=(input, output), 일부/전부 None이면 미확인. 같은 run_id는 한 번만."""
        with self._lock:
            if run_id is not None:
                if run_id in self._done:
                    return
                self._done.add(run_id)
                self._inflight.pop(run_id, None)
            self._calls += 1
            incoming, outgoing = usage or (None, None)
            if not _valid(incoming, outgoing):
                self._unknown += 1
            self._in += incoming if _valid(incoming) else 0
            self._out += outgoing if _valid(outgoing) else 0

    def totals(self):
        with self._lock:
            return self._in, self._out, self._calls + len(self._inflight), self._unknown + len(self._inflight)


def _valid(*values):
    return all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values)


def _usage(response):
    """각 batch의 provider usage 합계(후보 답변마다 중복하지 않음). 부분 usage도 보존."""
    inputs, outputs = [], []
    for generations in response.generations or ():
        if not generations:
            continue
        meta = getattr(getattr(generations[0], "message", None), "usage_metadata", None) or {}
        inputs.append(meta.get("input_tokens"))
        outputs.append(meta.get("output_tokens"))
    if inputs and _valid(*inputs, *outputs):
        return sum(inputs), sum(outputs)
    fallback = (response.llm_output or {}).get("token_usage") or {}
    if _valid(fallback.get("prompt_tokens"), fallback.get("completion_tokens")):
        return fallback["prompt_tokens"], fallback["completion_tokens"]
    if any(_valid(v) for v in [*inputs, *outputs]):
        return (sum(v for v in inputs if _valid(v)) if _valid(*inputs) else None,
                sum(v for v in outputs if _valid(v)) if _valid(*outputs) else None)
    if any(_valid(fallback.get(key)) for key in ("prompt_tokens", "completion_tokens")):
        return fallback.get("prompt_tokens"), fallback.get("completion_tokens")
    return None


_meter = ContextVar("chat_usage_meter", default=None)
register_configure_hook(_meter, inheritable=True)


def record_external(input_tokens, output_tokens):
    """LangChain llm 콜백을 안 내는 호출(JEV 분류기)의 usage. 둘 중 하나라도 None 이면 모름으로 센다."""
    meter = _meter.get()
    if meter is None:
        return
    meter.record((input_tokens, output_tokens))


def metered(produce, meter, charge):
    """produce() 를 Meter 를 켠 채로 돌린다(chat_runs.Run.pump 의 worker 스레드). 원본이 닫혀 provider 작업이 멈추면 meter.finished.

    요청 스레드(finish)가 기다리다 포기했으면 여기서 정산한다."""
    token = _meter.set(meter)
    inner = None
    try:
        inner = produce()
        yield from inner
    finally:
        try:
            if inner is not None:
                inner.close()  # v1 은 여기서 파이프라인 스레드가 끝날 때까지 기다린다
        finally:
            _meter.reset(token)
            with meter._lock:
                meter.finished.set()
                late = meter.abandoned
            if late:
                try:
                    settle(charge, meter)
                except Exception:
                    log.exception("usage late settle failed; reservation %s left locked", charge.pk)


def finish(charge, meter):
    """요청 스레드에서 정산한다. provider 작업(worker)이 멈출 때까지 기다린 뒤에만 예약을 푼다.

    끊김·중단 뒤에도 worker 는 다음 청크/timeout 까지 돌 수 있다. USAGE_SETTLE_WAIT_SECONDS 안에 안 끝나면
    worker(metered) 가 끝날 때 실제 사용량으로 정산한다.
    """
    meter.finished.wait(_conf("USAGE_SETTLE_WAIT_SECONDS", 60))
    with meter._lock:
        if not meter.finished.is_set():
            meter.abandoned = True
            log.warning("usage reservation %s handed to worker: provider work still running", charge.pk)
            return
    try:
        settle(charge, meter)
    except Exception:
        log.exception("usage settle failed; reservation %s left locked", charge.pk)
