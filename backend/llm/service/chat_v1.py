"""V1 체인(astream_events 기반 RAG pipeline) 전용 실행 경로.

기존 llm.service.chat 의 v1 계약(공식 astream_events → project_event → SSE, run["answer"] 로
최종 답 수집)을 그대로 옮긴 것이다. 턴 저장(ChatThread.update)·취소(GeneratorExit)·에러 envelope 은
V2 와 같은 모양이지만 독립 파일 요구사항에 따라 여기 자체로 갖는다 (llm.service.chat_v2 참고).
"""
import asyncio
import logging
import uuid
from contextlib import closing

from langchain_core.messages import AIMessage, HumanMessage

from llm.enum import ChatRole, TurnStatus
from llm.serializer.message import project_event, project_history
from llm.service import chat_runs, usage
from llm.service.chat_thread import ChatThread

log = logging.getLogger(__name__)


def _model_history(messages, turns):
    """다음 모델 입력용 projection: 완료된 턴의 질문/최종 답변만."""
    return [
        HumanMessage(item["content"]) if item["role"] == ChatRole.USER else AIMessage(item["content"])
        for item in project_history(messages, turns) if item["status"] == TurnStatus.COMPLETED and not item.get("answer_deleted")
    ]


def _sync_events(agen, run):
    """async astream_events 를 WSGI 동기 이터레이터로 한 이벤트씩 넘긴다.

    요청 스레드에서 전용 event loop 를 이벤트 하나만큼씩 돌린다. 파이프라인은 asyncio.to_thread
    worker 에서 계속 돌기 때문에 provider 청크가 생성 완료 전에 바로 전달된다.
    닫힐 때(정상 종료·실패·클라이언트 연결 종료) run["cancelled"] 로 worker 를 멈추고
    worker 가 끝날 때까지 기다린 뒤 loop 를 닫는다.
    """
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                event = loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                return
            yield event
    finally:
        run["cancelled"] = True
        try:
            loop.run_until_complete(agen.aclose())
            # ponytail: 취소 플래그는 청크/도구 호출 사이에서만 확인된다. 진행 중인 도구 호출은
            # 자기 timeout 까지 기다린다. 더 빨라야 하면 도구 쪽에 취소 토큰을 넘긴다.
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


def _frames(chain, chain_input, run):
    """공식 이벤트 → 공개 (event, data). 기록되는 도구(대응표에 있는 run)만 내보낸다."""
    events = chain.astream_events(chain_input, version="v2")
    with closing(_sync_events(events, run)) as stream:
        for event in stream:
            if str(event.get("event", "")).startswith("on_tool_") and event.get("run_id") not in run["tool_call_ids"]:
                continue  # 저장되지 않는 도구(폴백 도메인 내부 등)는 조회 내역과 어긋나므로 내보내지 않는다
            frame = project_event(
                event, answer_run_id=run["answer_run_id"], tool_call_ids=run["tool_call_ids"],
            )
            if frame:
                yield frame


def _stream_turn(thread, prefix, turns, human, charge):
    """프레임: tool*/delta* → done | stopped | error (chat_runs.stream_turn)."""
    run = {
        "answer_run_id": uuid.uuid4(),
        "tool_call_ids": {},    # 도구 run_id(str) -> tool_call_id (v1 파이프라인이 채운다)
        "messages": [],         # 실제 AI(tool_calls)/ToolMessage 기록 (v1 파이프라인이 채운다)
        "answer": None,
        "cancelled": False,
    }

    def produce():
        from llm.v1.rag.pipeline import chat_chain
        chain_input = {"question": human.content, "chat_history": _model_history(prefix, turns), "run": run}
        return _frames(chat_chain(), chain_input, run)

    return chat_runs.stream_turn(thread, prefix, turns, human, produce, run,
                                 on_stop=lambda: run.update(cancelled=True), label="v1", charge=charge)


def _start(session, thread, begin):
    """예약(부족하면 InsufficientCredits) → 질문 저장 → 스트림. 저장 실패면 예약을 0 으로 푼다."""
    charge = usage.reserve(session)
    try:
        frames = _stream_turn(thread, *begin(), charge)
        next(frames)  # priming
    except BaseException:
        usage.settle(charge)
        raise
    return frames


def send_message(session, content, context=None):
    """V1 사용자 메시지를 저장하고 (event, data) 제너레이터를 돌려준다. context 는 v1 이 안 쓴다."""
    thread = ChatThread(session.id)
    return _start(session, thread, lambda: thread.ask(content))


def message_update(session, message_id, content, context=None):
    """V1: 해당 사용자 메시지 뒤를 지우고 같은 ID 로 질문을 바꾼 뒤 다시 답한다. context 는 안 쓴다."""
    thread = ChatThread(session.id)
    return _start(session, thread, lambda: thread.edit(message_id, content))
