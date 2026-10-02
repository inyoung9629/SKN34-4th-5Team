"""채팅 토큰 사용량: 지갑 예약 → 모든 model 호출 계량 → 실제 토큰 정산. 1 credit = 1000 토큰(입력+출력 같은 비율).

- 주인은 항상 ChatSession 에서 나온다(회원 user / 비회원 guest UUID). 클라이언트가 주인·잔액·비용을 보내지 않는다.
- 회원: USAGE_TIMEZONE 달마다 USAGE_MEMBER_MONTHLY_TOKENS. 비회원: USAGE_GUEST_TOKENS 한 번, 충전 없음.
- 예약: 세션 질문 저장(ask/edit) 전에 지갑 행 잠금 안에서 min(남은 양, USAGE_TURN_RESERVE_TOKENS) 을 잡는다(동시 초과 방지).
  provider 호출은 이 transaction 밖에서만 일어난다.
- 계량: register_configure_hook 의 ContextVar 로 이 턴 안의 모든 LangChain model run(v1 중첩, v2 하위 Agent, 재시도 run)에
  Meter 가 붙는다. run_id 로 한 번만 센다. JEV 분류기는 classify() 가 usage를 직접 넘기고,
  코스 Luna 본문 분석은 metered_external로 입력/스키마/출력 예산 예약 후 usage를 정산한다.
- 정산: 알려진 토큰. usage 를 모르는 호출(응답에 usage 없음, 오류·중단(Stop) 으로 끝나 usage 가 안 온 호출 포함)이 있으면
  예약 전체를 청구한다(무료로 새지 않게).
- 호출 전 검사(preflight): 이미 쓴 양 + 이 호출 입력(설치된 tiktoken 으로 메시지·system·도구 schema 를 셈) + 출력 상한
  (ChatOpenAI max_tokens == USAGE_MAX_CALL_OUTPUT_TOKENS) 이 예약을 넘으면 시작 전에 막는다(UsageExhausted).
  토큰을 셀 수 없는 모델(tokenizer 없음, 상한 없음, chat 이 아닌 LLM)은 fail-closed 로 막는다.
  단, gpt-6-luna의 tiktoken 매핑이 없을 때는 UTF-8 바이트+서식 여유로 보수적으로 예약한다(정확한 토큰 수 아님).
- JEV(TypeSafe SDK) 는 출력 상한·토큰 계산 API 가 없어 호출 전에는 누적 사용만 본다. 응답 usage 가 없으면 모름 → 예약 전체.
- 정산은 provider 작업(worker)이 끝난 뒤에만. 요청 스레드가 USAGE_SETTLE_WAIT_SECONDS 안에 못 보면 worker 가 끝날 때 정산한다.
ponytail: 프로세스가 죽어 정산 못 한 예약은 자동 만료하지 않는다(아직 쓰는 중인지 알 수 없음) → 잔액에서 잠긴 채 남는다.
  운영자가 확인 후 settle_abandoned(charge) 로 예약 전체를 청구해 푼다. 스케줄러는 없다.
"""
import json
import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from uuid import uuid4

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


def _conf(name, default):
    return getattr(settings, name, default)


class InsufficientCredits(Exception):
    """예약할 잔액이 없다. view 는 402 {code: usage_exhausted} 로 바꾼다."""


class UsageExhausted(Exception):
    """이 턴 예약을 다 써서 다음 model 호출을 시작하지 않는다."""


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


def _reserved(wallet):
    return wallet.charges.filter(status=UsageCharge.RESERVED, period=wallet.period).aggregate(
        total=Sum("reserved_tokens"))["total"] or 0


def _apply(wallet, charge, tokens):
    charge.status, charge.charged_tokens, charge.settled_at = UsageCharge.SETTLED, tokens, _now()
    charge.save(update_fields=["status", "charged_tokens", "settled_at", "input_tokens", "output_tokens",
                               "calls", "unknown_calls"])
    if charge.period == wallet.period:  # 달이 넘어간 뒤 끝난 턴은 지난 달 몫이라 새 달 사용량에 넣지 않는다
        wallet.used_tokens += tokens
        wallet.save(update_fields=["used_tokens", "updated_at"])


def reserve(session):
    """세션 주인의 지갑에서 이번 턴 예산을 잡는다. 부족하면 InsufficientCredits."""
    owner = _owner(session)
    with transaction.atomic():
        wallet = _locked_wallet(owner)
        available = _limit("user_id" in owner) - wallet.used_tokens - _reserved(wallet)
        if available < _conf("USAGE_MIN_START_TOKENS", TOKENS_PER_CREDIT):
            raise InsufficientCredits
        return UsageCharge.objects.create(
            wallet=wallet, session_id=session.id, period=wallet.period,
            reserved_tokens=min(available, _conf("USAGE_TURN_RESERVE_TOKENS", 20_000)),
        )


def settle(charge, meter=None):
    """예약을 실제 토큰으로 정산하고 남은 예약을 푼다. 멱등(이미 정산됐으면 아무것도 안 한다)."""
    with transaction.atomic():
        wallet = UsageWallet.objects.select_for_update().get(pk=charge.wallet_id)
        charge = UsageCharge.objects.select_for_update().get(pk=charge.pk)
        if charge.status != UsageCharge.RESERVED:
            return charge
        tokens = 0
        if meter is not None:
            charge.input_tokens, charge.output_tokens, charge.calls, charge.unknown_calls = meter.totals()
            tokens = charge.input_tokens + charge.output_tokens
            if charge.unknown_calls:
                tokens = max(tokens, charge.reserved_tokens)
        _apply(wallet, charge, tokens)
        return charge


def settle_abandoned(charge):
    """크래시로 남은 예약을 운영자가 확인 후 푼다: 사용량을 모르므로 예약 전체를 청구한다. 멱등."""
    meter = Meter(charge.reserved_tokens)
    meter.record(None)
    return settle(charge, meter)


def balance(user=None, guest=None):
    """요청자 본인의 사용량 DTO. 비회원 쿠키가 없으면(아직 대화 전) 새 지갑 기준 값을 만들지 않고 계산만 한다."""
    member = user is not None
    used = reserved = 0
    period = period_for(member)
    if member or guest is not None:
        with transaction.atomic():
            if UsageWallet.objects.filter(**_owner(user=user, guest=guest)).exists():
                wallet = _locked_wallet(_owner(user=user, guest=guest))
                used, reserved, period = wallet.used_tokens, _reserved(wallet), wallet.period
    limit = _limit(member)
    remaining = max(0, limit - used - reserved)
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
        "can_send": remaining >= _conf("USAGE_MIN_START_TOKENS", TOKENS_PER_CREDIT),
    }


# ── 계량 ─────────────────────────────────────────────────────────────────────
class Meter(BaseCallbackHandler):
    """이 턴의 모든 model run 토큰. run_id 당 한 번. 예약량에 닿으면 새 model run 을 막는다."""
    raise_error = True  # on_*_start 의 UsageExhausted 가 호출을 실제로 막게 한다

    def __init__(self, budget):
        super().__init__()
        self.budget = budget
        self.exhausted = False
        self.finished = threading.Event()  # 원본 스트림이 닫혀 더 이상 model 호출이 없다
        self._lock = threading.Lock()
        self._done = set()
        self.abandoned = False  # finish 가 포기함 → worker 가 끝날 때 정산
        self._in = self._out = self._calls = self._unknown = 0
        self._inflight = {}  # run_id → 시작 시 잡은 최대 비용. 병렬 호출이 같은 잔액을 두 번 쓰지 않게 한다

    def check(self, next_call=0, run_id=None):
        """쓴 양 + 진행 중 호출 예약 + next_call 이 예약을 넘으면 막는다. run_id 가 있으면 next_call 을 잡아 둔다."""
        with self._lock:
            # usage 를 모르는 호출이 하나라도 끝났으면 이 턴은 예약 전체로 정산된다: 더 허용하면 공짜가 된다
            if self._unknown or self._in + self._out + sum(self._inflight.values()) + max(next_call, 1) > self.budget:
                log.warning("chat_token_budget_block budget=%d used=%d inflight=%d next=%d unknown=%d",
                            self.budget, self._in+self._out, sum(self._inflight.values()),
                            max(next_call, 1), self._unknown)
                self.exhausted = True
                raise UsageExhausted
            if run_id is not None and run_id not in self._done:
                self._inflight[run_id] = max(next_call, 1)

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
        self.check(_call_cost(messages, kwargs.get("invocation_params") or {}), run_id)

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._refuse()  # chat 이 아닌 LLM 은 쓰지 않는다: 셀 수 없으면 막는다

    def _refuse(self):
        with self._lock:
            self.exhausted = True
        raise UsageExhausted

    def on_llm_end(self, response, *, run_id, **kwargs):
        self.record(_usage(response), run_id)

    def on_llm_error(self, error, *, run_id, **kwargs):
        # 오류·중단(Stop=GeneratorExit)으로 끝난 호출: 부분 응답에 usage 가 있으면 그것, 없으면 모름(예약 전체)
        response = kwargs.get("response")
        self.record(_usage(response) if response is not None else None, run_id)

    def record(self, usage, run_id=None):
        """usage=(input, output) 또는 None(모름). 같은 run_id 는 한 번만."""
        with self._lock:
            if run_id is not None:
                if run_id in self._done:
                    return
                self._done.add(run_id)
                self._inflight.pop(run_id, None)
            self._calls += 1
            if usage is None:
                self._unknown += 1
            else:
                self._in += usage[0]
                self._out += usage[1]

    def totals(self):
        with self._lock:
            return self._in, self._out, self._calls, self._unknown


def _call_cost(messages, params):
    """이번 호출 최대 토큰 = 입력(메시지·도구 schema, tiktoken) + 출력 상한. 셀 수 없으면 UsageExhausted(fail-closed)."""
    import tiktoken
    cap = _conf("USAGE_MAX_CALL_OUTPUT_TOKENS", 4000)
    limit = params.get("max_completion_tokens", params.get("max_tokens", params.get("max_output_tokens")))
    if not _valid(limit) or limit > cap:
        raise UsageExhausted
    try:
        encoding = tiktoken.encoding_for_model(params.get("model") or params.get("model_name") or "")
    except KeyError:
        # tiktoken has not published a GPT-6 mapping yet. Do not pretend that a
        # different model's tokenizer is exact. For this explicitly supported
        # text model reserve UTF-8 bytes + framing headroom; settle actual usage.
        if (params.get("model") or params.get("model_name")) != "gpt-6-luna":
            raise UsageExhausted from None
        encoding = None
    def count(value):
        text = json.dumps(value, ensure_ascii=False, default=str)
        return len(encoding.encode(text, disallowed_special=())) if encoding else len(text.encode("utf-8"))
    tokens = count(params.get("tools") or []) + (1024 if encoding is None else 0)
    for batch in messages:
        for message in batch:  # ponytail: 메시지당 +8 는 role/구분자 여유. 실제 서식보다 크게 잡는다.
            body = [message.content, getattr(message, "tool_calls", None) or []]
            tokens += (64 if encoding is None else 8) + count(body)
    return tokens + limit


def _valid(*values):
    return all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values)


def _usage(response):
    """LLMResult → (input, output) 또는 None. AIMessage.usage_metadata 우선, 없으면 llm_output.token_usage."""
    for generations in response.generations or ():
        for generation in generations:
            meta = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
            if _valid(meta.get("input_tokens"), meta.get("output_tokens")):
                return meta["input_tokens"], meta["output_tokens"]
    token_usage = (response.llm_output or {}).get("token_usage") or {}
    if _valid(token_usage.get("prompt_tokens"), token_usage.get("completion_tokens")):
        return token_usage["prompt_tokens"], token_usage["completion_tokens"]
    return None


_meter = ContextVar("chat_usage_meter", default=None)
register_configure_hook(_meter, inheritable=True)


@contextmanager
def metered_external(messages, params):
    """Reserve a direct text-model call and settle once, even on errors/abort.

    The caller supplies all input/schema and the output cap before network I/O,
    then reports response token usage. No response usage means unknown cost.
    """
    meter = _meter.get()
    if meter is None:
        yield lambda input_tokens, output_tokens: None
        return
    run_id = uuid4()
    meter.check(_call_cost(messages, params), run_id)
    actual = None
    def report(input_tokens, output_tokens):
        nonlocal actual
        actual = (input_tokens, output_tokens) if _valid(input_tokens, output_tokens) else None
    try:
        yield report
    finally:
        meter.record(actual, run_id)


def record_external(input_tokens, output_tokens):
    """LangChain llm 콜백을 안 내는 호출(JEV 분류기)의 usage. 둘 중 하나라도 None 이면 모름으로 센다."""
    meter = _meter.get()
    if meter is None:
        return
    if _valid(input_tokens, output_tokens):
        meter.record((input_tokens, output_tokens))
    else:
        meter.record(None)


def check_external():
    """JEV 호출 전 예약 확인(LangChain start 콜백이 없으므로 직접).

    TypeSafe SDK 는 출력 상한·토큰 계산 API 가 없어 이번 호출 크기는 셀 수 없다: 누적 사용이 예약에 닿았는지만 본다."""
    if (meter := _meter.get()) is not None:
        meter.check()


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
