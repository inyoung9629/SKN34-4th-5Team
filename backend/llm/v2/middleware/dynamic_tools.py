"""미들웨어 2: 동적 도구 할당 + 실행 직전 allowlist. 요청 state 만 읽고 캐시된 agent/도구 배열은 바꾸지 않는다."""
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# llm.tools.assistant 에서 옮겨온 도구. 요청 상태(구장 hint·질문·대화·출발지)를 ContextVar(request_state)로 읽는다.
MIGRATED_TOOLS = frozenset({"get_ticket_policy", "search_nearby_places", "plan_course"})


def request_args(state):
    """V2 state → request_state(hint, question, history). 도구 호출마다 새 상태라 동시 요청·캐시 agent 와 안 섞인다."""
    from llm.tools.assistant import to_stadium_code
    turns = [m for m in state.get("messages") or [] if isinstance(m, HumanMessage)
             or (isinstance(m, AIMessage) and not m.tool_calls and m.text)]
    last = next((i for i in range(len(turns) - 1, -1, -1) if isinstance(turns[i], HumanMessage)), None)
    question = turns[last].text if last is not None else ""
    history = [{"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": m.text}
               for m in turns[:last or 0][-20:]]
    stadium = (state.get("context") or {}).get("stadium")
    return (to_stadium_code(stadium) if stadium else None), question, history


# JEV capability → 기존 llm/tools 실제 도구 이름 (선행 도구 포함). 키는 jev_guidelines.CAPABILITIES 와 같다.
CAPABILITY_TOOLS = {
    "web_research": ("ask_web_research",),
    "schedule": ("get_games",),
    "standings": ("get_standings",),
    "players": ("search_players",),
    "baseball_stats": ("get_baseball_schema", "execute_baseball_select"),
    "rules": ("search_kbo_documents",),
    "stadium_info": (
        "get_stadiums", "get_stadium", "get_seat_zones", "get_seat_views", "get_seat_maps", "get_ticket_prices",
        "get_ticket_policies", "get_ticket_policy", "get_food_stores", "get_facilities", "get_stadium_contents", "get_transport", "search_kbo_documents",
    ),
    "carry_in": ("get_stadium", "search_kbo_documents"),
    "parking_transport": ("get_stadium", "get_transport", "search_kbo_documents"),
    "community": ("search_community_posts", "get_prediction_games", "get_games"),
    "nearby_places": ("get_stadium", "search_places", "search_documents_tool", "ask_travel_research"),
    "tourism": ("get_stadium", "search_tourism", "search_nearby_places", "search_documents_tool"),
    "directions": ("get_stadium", "get_directions"),
    "courses": ("search_courses", "get_course"),
    "weather": ("get_games", "get_stadium", "get_weather"),
    "day_plan": ("ask_baseball", "ask_travel_research", "ask_place_data", "get_directions", "ask_course"),
}


class DynamicToolMiddleware(AgentMiddleware):
    def __init__(self, role_tools, capability_tools=None):
        super().__init__()
        self.role_tools = frozenset(role_tools)
        self.capability_tools = capability_tools  # None 이면 역할 고정 도구 묶음 그대로

    def allowed(self, state) -> frozenset:
        if self.capability_tools is None:
            return self.role_tools
        decision = state.get("decision") or {}
        if decision.get("allowed") is not True:
            return frozenset()
        capabilities = set(decision.get("capabilities") or ()) | set(state.get("tool_group_ids") or ())
        return self.role_tools & {n for c in capabilities for n in self.capability_tools.get(c, ())}

    def wrap_model_call(self, request, handler):
        allowed = self.allowed(request.state)
        if self.course_called(request.state):
            allowed = allowed - {"ask_course", "ask_travel_research", "search_documents_tool"}
        tools = [t for t in request.tools if getattr(t, "name", None) in allowed]
        return handler(request.override(tools=tools))

    @staticmethod
    def course_called(state):
        for message in reversed(state.get("messages") or []):
            if isinstance(message, HumanMessage):
                break
            # limiter가 실행 전에 넣은 중복 거절은 실제 전문 위임 결과가 아니다.
            if isinstance(message, ToolMessage) and message.name == "ask_course" and (message.status != "error" or isinstance(message.artifact, list)):
                return True
        return False

    def wrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        if name not in self.allowed(request.state) or (self.course_called(request.state) and name in {"ask_course", "ask_travel_research", "search_documents_tool"}):
            return ToolMessage(
                content=f"허용되지 않은 도구입니다: {name}", tool_call_id=request.tool_call["id"], name=name, status="error",
            )
        if name in MIGRATED_TOOLS:
            from llm.tools.assistant import request_state
            from llm.v2.agent.course_output import public_course
            context = request.state.get("context") or {}
            with request_state(*request_args(request.state), origin=context.get("origin"), route_path=context.get("routePath"), current_course=context.get("currentCourse")) as state:
                state["course_memory"] = request.state.get("course_memory", {})
                state["course_request"] = (request.state.get("decision") or {}).get("course_request")
                result = handler(request)
                if name == "plan_course" and isinstance(result, ToolMessage) and result.status != "error":
                    course = public_course(state.get("course"))
                    artifact = {"course": course} if course else {}
                    outcome = state.get("course") or {}
                    if outcome.get("courseHistoryReset"):
                        artifact["course_history_reset"] = True
                    if outcome.get("evidenceHandled"):
                        artifact["course_evidence_handled"] = True
                    if outcome.get("route", "").startswith("course:edit"):
                        artifact["course_edit_handled"] = True
                    if "courseMemory" in outcome:
                        artifact["course_memory"] = outcome["courseMemory"]
                    if artifact:
                        result = result.model_copy(update={"artifact": artifact})
                return result
        return handler(request)
