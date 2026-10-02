"""V2 채팅 턴: 메인 Agent 그래프(checkpointer 없음)의 공개 답변 청크·도구 진행을 SSE 로 흘리고 대화 state 에 저장한다."""
import logging
from contextlib import closing

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from llm.enum import ChatRole, PublicChatEvent, PublicToolStatus, TurnStatus
from llm.serializer.message import _public_tool, project_history, tool_summary, tool_title
from llm.service import chat_runs, usage
from llm.service.chat_thread import ChatThread

log = logging.getLogger(__name__)


def _model_history(messages, turns):
    """다음 모델 입력용 projection: 완료된 턴의 질문/최종 답변만."""
    return [
        HumanMessage(item["content"]) if item["role"] == ChatRole.USER else AIMessage(item["content"])
        for item in project_history(messages, turns) if item["status"] == TurnStatus.COMPLETED
    ]


def _frames(graph_input, run):
    """메인 Agent 그래프를 checkpointer 없이 subgraphs=True 로 돌려 공개 (event, data) 만 흘린다. 대화 state 쓰기는 finish 만 한다.

    - delta: 메인 model 텍스트 청크는 모두(도구 호출과 같은 호출의 머리말 포함) 바로 흘린다. 하위 Agent(ask_* 안쪽,
      namespace "tools:<task>") model 텍스트는 parent_id = 그 하위 Agent 를 부른 ask_* tool_call_id 로 흘린다.
      parent_id 는 sub_agents._delegate 가 하위 실행 config metadata 에 싣고, namespace 첫 칸별로 기억한다.
    - 저장 답변 run["answer"] = 메인(ns=()) update 의 도구 호출 없는 AI 답. 마지막 도구 호출 뒤 흘린 텍스트가 없으면
      (JEV 거절·비스트리밍 답) 그 답을 한 번에 흘린다.
    - tool: 메인·하위 Agent 의 실제 도구 호출 running → ToolMessage 로 completed/failed. 인자·결과는 싣지 않는다.
      메인 호출/결과 원본(하위 대화는 ask_* 결과 artifact)만 run["messages"] 에 모아 턴에 저장한다.
    """
    from llm.v2.agent.chain import get_graph
    stream = get_graph().stream(graph_input, stream_mode=["messages", "updates", "custom"], subgraphs=True)
    parents, titles, summaries = {}, {}, {}  # namespace 첫 칸 → parent tool_call_id / tool_call_id → 하위 Agent title
    streamed = False  # 마지막 메인 도구 호출 뒤 메인 텍스트를 흘렸는지
    with closing(stream):
        for ns, mode, data in stream:
            parent = parents.get(ns[0]) if ns else None
            if mode == "custom":
                if not ns and isinstance(data, dict) and isinstance(data.get("course_delta"), str):
                    streamed = True
                    yield PublicChatEvent.DELTA.value, {"text": data["course_delta"]}
                continue
            if mode == "messages":
                chunk, meta = data
                if ns and meta.get("parent_id"):
                    parents[ns[0]] = parent = meta["parent_id"]
                if meta.get("langgraph_node") != "model" or not isinstance(chunk, AIMessage) or not chunk.text:
                    continue
                if ns and not parent:
                    continue  # 출처를 모르는 하위 텍스트는 메인 답에 섞지 않는다
                if not ns:
                    streamed = True
                yield PublicChatEvent.DELTA.value, {"text": chunk.text, **({"parent_id": parent} if ns else {})}
                continue
            for update in data.values():
                if not ns and isinstance(update, dict) and update.get("course_state") is not None:
                    run["course_state"] = update["course_state"]
                for message in (update or {}).get("messages") or [] if isinstance(update, dict) else ():
                    if not ns and isinstance(message, AIMessage) and not message.tool_calls:
                        run["answer"] = str(message.text)
                        if not streamed:
                            yield PublicChatEvent.DELTA.value, {"text": run["answer"]}  # 청크 없이 끝난 답: 한 번에
                    elif isinstance(message, AIMessage) and message.tool_calls:
                        if not ns:
                            run["messages"].append(message)
                            streamed = False
                        for call in message.tool_calls:
                            titles[call["id"]] = tool_title(call["name"], call.get("args"))
                            summaries[call["id"]] = tool_summary(call["name"], call.get("args"))
                            yield PublicChatEvent.TOOL.value, _public_tool(
                                call["id"], call["name"], PublicToolStatus.RUNNING.value, parent, titles[call["id"]],
                                summaries[call["id"]])
                    elif isinstance(message, ToolMessage):
                        if not ns:
                            run["messages"].append(message)
                        status = PublicToolStatus.FAILED if message.status == "error" else PublicToolStatus.COMPLETED
                        yield PublicChatEvent.TOOL.value, _public_tool(
                            message.tool_call_id, message.name, status.value, parent, titles.get(message.tool_call_id),
                            summaries.get(message.tool_call_id))


def _stream_turn(thread, prefix, turns, human, context, charge, profile_team=None):
    """프레임: tool*/delta* → done | stopped | error (chat_runs.stream_turn). 저장 답변 = 마지막 도구 없는 model 호출의 답."""
    run = {"answer": "", "messages": []}
    graph_input = {"messages": [*_model_history(prefix, turns), human]}
    from llm.v2.course.runtime import previous_course_state
    graph_input["course_runtime"] = {"state": previous_course_state(prefix, turns), "profile_team": profile_team}
    if context:
        graph_input["context"] = context
    return chat_runs.stream_turn(thread, prefix, turns, human, lambda: _frames(graph_input, run), run,
                                 label="v2", charge=charge)


def _start(session, thread, begin, context):
    """예약(부족하면 InsufficientCredits) → 질문 저장 → 스트림. 저장 실패면 예약을 0 으로 푼다."""
    charge = usage.reserve(session)
    try:
        from django.contrib.auth import get_user_model
        profile_team = (get_user_model().objects.filter(pk=session.user_id).values_list("team_code", flat=True).first()
                        if session.user_id else None)
        frames = _stream_turn(thread, *begin(), context, charge, profile_team)
        next(frames)  # priming
    except BaseException:
        usage.settle(charge)
        raise
    return frames


def send_message(session, content, context=None):
    """V2 사용자 메시지를 저장하고 (event, data) 튜플을 흘려보내는 제너레이터를 돌려준다."""
    thread = ChatThread(session.id)
    return _start(session, thread, lambda: thread.ask(content), context)


def message_update(session, message_id, content, context=None):
    """V2: 해당 사용자 메시지 뒤를 지우고 같은 ID 로 질문을 바꾼 뒤 다시 답한다."""
    thread = ChatThread(session.id)
    return _start(session, thread, lambda: thread.edit(message_id, content), context)
