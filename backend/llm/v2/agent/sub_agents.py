"""하위 Agent 를 메인 Agent 의 ask_* 도구로 감싼다. 결과 content 는 최종 답(문자열), artifact 는 하위 대화 전체."""
from typing import Annotated

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage

from . import baseball_sub_agent, place_sub_agent, travel_sub_agent
from .common import final_text

SPECIALISTS = {
    "ask_baseball": (baseball_sub_agent, "야구 전문 에이전트: 경기 일정·시각, 구장 확인, 구장 안 정보, 야구 커뮤니티를 조회해 돌려준다."),
    "ask_travel_research": (travel_sub_agent, "주변 후보 조사 에이전트: 구장 주변 맛집·카페·관광·실내활동 후보를 찾아 돌려준다."),
    "ask_place_data": (place_sub_agent, "장소 확인 에이전트: 기존 공개 코스와 특정 장소가 실제 검색되는지 확인해 돌려준다."),
}


def _delegate(name, description, agent):
    @tool(name, description=description, response_format="content_and_artifact")
    def ask(task: str, runtime: ToolRuntime, summary: Annotated[str, "사용자 화면에 공개되는 40자 이내 짧은 작업명(예: 두산 다음 경기 확인). 비밀·개인정보·식별자·긴 지시 금지"] = ""):
        """task: 하위 Agent 에게 줄 실제 작업 지시.
        summary: 사용자 화면에 보일 40자 이내 짧은 작업명(예: 두산 다음 경기 확인). 비밀·개인정보·긴 지시 금지. 수행 입력에는 쓰이지 않는다."""
        # 하위 Agent 에는 작업 문자열과 선택 context 만 넘긴다. 승인(decision)은 작업 문자열이 아닌 내부 state 로 전달.
        messages = [HumanMessage(task)]
        try:
            for update in agent.stream({"messages": messages, "context": runtime.state.get("context"),
                                        "decision": runtime.state.get("decision")}, stream_mode="updates",
                                       # 스트리밍 청크 metadata 로 이 하위 Agent 를 부른 tool_call_id 를 알린다 (SSE parent_id)
                                       config={"metadata": {"parent_id": runtime.tool_call_id}}):
                for node in update.values():
                    messages.extend((node or {}).get("messages") or [] if isinstance(node, dict) else ())
        except Exception as exc:  # 한도 초과/조회 실패를 결과 없음과 구분해 메인 Agent 에 알린다
            return f"[조회 실패] {name}: {type(exc).__name__}", messages
        return final_text({"messages": messages}) or f"[조회 실패] {name}: 답변 없음", messages
    return ask


def build(model, tools_by_name):
    return [_delegate(name, desc, module.build(model, tools_by_name)) for name, (module, desc) in SPECIALISTS.items()]
