"""진행 중인 채팅 스트림(run)의 프로세스 로컬 취소 + 공개 이벤트 idle timeout. 취소/종료 판정의 유일한 주인.

- 스트림 하나 = Run 하나. 세션별 집합에 객체 그대로 등록하고 끝나면 그 객체만 뺀다(identity-safe).
- 편집(PUT/DELETE)·세션 삭제는 commit 전에 등록돼 있던 Run 객체들을 고정하고(snapshot), commit 성공 뒤 그 객체만 취소한다.
  그래서 뒤늦은 취소가 같은 세션에 새로 시작한 스트림(ABA)을 건드리지 않는다.
- 취소된 스트림은 다음 이벤트 경계에서 Stopped 를 받아 턴을 cancelled 로 저장하고 stopped 를 한 번 보낸다.
ponytail: 레지스트리는 프로세스 로컬이다. 다른 worker 프로세스의 스트림은 취소되지 않고, 최종 쓰기가
ChatThread.update 의 revision fence/세션 행 잠금에 막혀 error 로 끝난다(정확성은 fence 가 보장).
여러 프로세스에서 즉시 취소가 필요하면 pub/sub(Redis 등)로 cancel 을 전파한다.
"""
import queue
import threading
import time
from contextvars import ContextVar

_cancelled = ContextVar("chat_run_cancelled", default=lambda: False)


def check_cancelled():
    if _cancelled.get()():
        raise Stopped

from django.conf import settings
from django.db import connections

_lock = threading.Lock()
_runs = {}  # session_id(str) -> set[Run]
MAX_PENDING = 64  # worker 가 소비자보다 앞서 쌓을 수 있는 이벤트 수(백프레셔)
_POLL = 0.1  # 막힌 put 이 취소/종료를 확인하는 간격
_HEARTBEAT_SECONDS = 10


class Stopped(Exception):
    """취소된 run: 종료 이벤트는 stopped."""


class Run:
    def __init__(self, session_id):
        self.session_id = str(session_id)
        self.cancelled = False
        self._inbox = queue.Queue(MAX_PENDING)

    def cancel(self):
        self.cancelled = True
        try:
            self._inbox.put_nowait(("wake", None))
        except queue.Full:
            pass  # 꽉 찼으면 소비자는 바로 get 하고 cancelled 를 본다

    def pump(self, frames, on_stop=None):
        """공개 (event, data) 제너레이터를 worker 스레드에서 돌려, 이벤트마다 CHAT_STREAM_IDLE_TIMEOUT_SECONDS 안에 받는다.

        실제 이벤트가 오면 timeout 이 다시 시작된다. 대기 중 heartbeat는 전송 연결만 유지하고 제한을 연장하지 않는다.
        취소면 Stopped, 정체면 TimeoutError, 원본 예외는 그대로 올린다.
        끝나거나 닫히면 on_stop(원본 쪽 취소 플래그) 을 부르고 worker 에 멈추라고 알린다.
        큐는 MAX_PENDING 으로 묶여 있고, 막힌 put 은 취소·소비자 종료 때 풀려 원본을 닫는다(협조적 중단).
        ponytail: 원본이 next() 안에서 막혀 있으면 worker 스레드는 다음 청크(또는 원본 timeout)까지 남는다.
        진짜 즉시 중단이 필요하면 async 그래프 반복으로 바꿔 task.cancel() 한다.
        """
        stop = threading.Event()

        def put(msg):
            while not (stop.is_set() or self.cancelled):
                try:
                    self._inbox.put(msg, timeout=_POLL)
                    return True
                except queue.Full:
                    pass
            return False

        def work():
            token = _cancelled.set(lambda: self.cancelled or stop.is_set())
            try:
                for item in frames:
                    if not put(("item", item)):
                        break
                else:
                    put(("end", None))
            except BaseException as exc:  # noqa: BLE001 - 소비자 스레드에서 다시 올린다
                put(("error", exc))
            finally:
                frames.close()
                _cancelled.reset(token)
                connections.close_all()  # 이 스레드가 연 DB 연결(도구 등)

        threading.Thread(target=work, daemon=True, name=f"chat-run-{self.session_id}").start()
        deadline = time.monotonic() + settings.CHAT_STREAM_IDLE_TIMEOUT_SECONDS
        try:
            while True:
                if self.cancelled:
                    raise Stopped
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    kind, value = self._inbox.get(timeout=min(_HEARTBEAT_SECONDS, remaining))
                except queue.Empty:
                    if self.cancelled:
                        raise Stopped
                    if time.monotonic() >= deadline:
                        raise TimeoutError("chat stream idle timeout") from None
                    yield "heartbeat", {}  # HTTP 계층에서 SSE 주석으로 직렬화; 대화/진행 기록에는 저장하지 않는다.
                    continue
                if self.cancelled:  # get 과 kind 처리 사이에 이긴 취소가 producer terminal 을 덮는다
                    raise Stopped
                if kind == "end":
                    break
                if kind == "error":
                    raise value
                if kind == "item" and not self.cancelled:
                    deadline = time.monotonic() + settings.CHAT_STREAM_IDLE_TIMEOUT_SECONDS
                    yield value
            if self.cancelled:
                raise Stopped
        finally:
            stop.set()
            if on_stop:
                on_stop()


def register(session_id):
    run = Run(session_id)
    with _lock:
        _runs.setdefault(run.session_id, set()).add(run)
    return run


def unregister(run):
    with _lock:
        runs = _runs.get(run.session_id)
        if runs is not None:
            runs.discard(run)
            if not runs:
                del _runs[run.session_id]


def snapshot(session_id):
    """지금 등록된 run 객체들. 나중에 cancel(snapshot) 해도 이후 시작된 run 은 건드리지 않는다."""
    with _lock:
        return list(_runs.get(str(session_id), ()))


def cancel_after_commit(session_id):
    """현재 run 들을 commit 뒤 취소하도록 예약한다(롤백되면 취소 안 함). autocommit 이면 바로 취소."""
    from django.db import transaction
    victims = snapshot(session_id)
    if victims:
        transaction.on_commit(lambda: [run.cancel() for run in victims], robust=True)


def stream_turn(thread, prefix, turns, human, produce, run, on_stop=None, label="chat", charge=None):
    """V1/V2 공통 턴 스트림. (event, data) 제너레이터이며 호출자가 첫 yield(None, priming)까지 미리 돌린다.

    produce(): 공개 (event, data) 제너레이터. run: {"answer", "messages"} 를 produce 가 채운다.
    종료 이벤트는 정확히 하나: done(최종 저장 성공) | stopped(편집/삭제로 취소) | error(실패·정체·stale).
    클라이언트 연결 종료(close)는 이벤트 없이 턴을 cancelled 로 저장한다.
    """
    import logging
    import uuid

    from langchain_core.messages import AIMessage

    from llm.enum import PublicChatEvent, TurnStatus
    from llm.serializer.message import error_payload, wire_done
    from llm.service.attachments import AttachmentProcessingLimit

    log = logging.getLogger(__name__)

    def finish(final, status):
        added = [*run["messages"], *([final] if final else [])]
        turn = {"status": status.value, "answer_id": final.id if final else None}
        if not thread.update(added, {human.id: turn}):
            return None
        return [*prefix, human, *added], {**turns, human.id: turn}

    def cancel_save():
        try:
            finish(None, TurnStatus.CANCELLED)
        except Exception:
            log.exception("%s chat cancel save failed", label)

    meter, owner_started = None, [False]
    if charge is not None:  # 토큰 계량: produce 를 worker 안에서 Meter 로 감싸고 끝나면 여기(요청 스레드)서 정산
        from llm.service import usage
        meter, source = usage.Meter(), produce
        produce = lambda: usage.metered(source, meter, charge)  # noqa: E731
    owner = register(thread.thread_id)
    try:
        final, failure = None, error_payload()
        try:
            yield None  # priming: 소비 전에 close() 돼도 아래 GeneratorExit 경로가 돈다
            owner_started[0] = True
            yield from owner.pump(produce(), on_stop)
            answer = run["answer"]
            if not isinstance(answer, str) or not answer:
                raise ValueError("agent returned no answer")
            final = AIMessage(answer, id=str(uuid.uuid4()),
                              response_metadata={**({"chain_version": label} if label in ("v1", "v2") else {}),
                                                 **({"course_history_reset": True} if run.get("course_history_reset") else {})})
            if owner.cancelled:  # 최종 저장(소유권) 전에 이긴 취소는 stopped
                raise Stopped
        except GeneratorExit:
            cancel_save()  # 질문·도구 내역은 두고 턴만 cancelled. 부분 답변은 저장 안 함.
            raise
        except Stopped:
            cancel_save()  # 대개 revision fence/세션 삭제에 막혀 쓰지 않는다. 그래도 같은 경로로 둔다.
            yield PublicChatEvent.STOPPED.value, {}
            return
        except AttachmentProcessingLimit as error:
            failure = {"detail": error.detail}
        except TimeoutError:
            log.warning("%s chat stream idle timeout", label)
        except Exception:
            log.exception("%s chat generation failed", label)

        done = None
        try:
            saved = finish(final, TurnStatus.COMPLETED if final else TurnStatus.FAILED)
            if owner.cancelled:  # DB lock 대기 중 edit/delete 가 revision 과 취소 소유권을 먼저 얻은 경우
                raise Stopped
            if final is not None and saved is not None:
                done = wire_done(*saved, thread.wire)
        except Stopped:
            cancel_save()
            yield PublicChatEvent.STOPPED.value, {}
            return
        except Exception:
            # ponytail: 최종 저장이 실패하면 턴은 pending 으로 남는다(질문은 이미 저장됨). 다음 요청은 정상 진행.
            log.exception("%s chat save failed", label)
        if done:
            yield PublicChatEvent.DONE.value, done
        else:
            yield PublicChatEvent.ERROR.value, failure
    finally:
        unregister(owner)
        if charge is not None:
            if not meter.finished.is_set() and not owner_started[0]:
                meter.finished.set()  # produce 가 시작도 안 됨(priming 전에 닫힘): model 호출 없음
            usage.finish(charge, meter)
