"""기존 코스 엔진을 한 번 실행하는 전문 그래프. 모델·저장소는 추가하지 않는다."""
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from .common import V2AgentState
from ..middleware.dynamic_tools import DynamicToolMiddleware, request_args


class CourseBoundaryMiddleware(DynamicToolMiddleware):
    def __init__(self):
        # 메인과 같은 요청별 도구 선택을 인정하되 서비스 범위 판정은 유지한다.
        super().__init__(["plan_course"], {"day_plan": ("plan_course",)})


def build(model, tools_by_name):
    boundary = CourseBoundaryMiddleware()

    def prepare(state, config: RunnableConfig):
        _, question, _ = request_args(state)
        return {"messages": [AIMessage("", response_metadata={"parent_id": config.get("metadata", {}).get("parent_id")}, tool_calls=[{
            "name": "plan_course", "args": {"request": question}, "id": f"course_{uuid4().hex}",
        }])]}

    graph = StateGraph(V2AgentState)
    graph.add_node("prepare", prepare)
    graph.add_node("tools", ToolNode([tools_by_name["plan_course"]], wrap_tool_call=boundary.wrap_tool_call,
                                     handle_tool_errors=lambda exc: f"[조회 실패] plan_course: {type(exc).__name__}"))
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "tools")
    graph.add_edge("tools", END)
    return graph.compile()
