"""미들웨어 2: 동적 도구 할당 + 실행 직전 allowlist. 요청 state 만 읽고 캐시된 agent/도구 배열은 바꾸지 않는다."""
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# llm.tools.assistant 에서 옮겨온 도구. 요청 상태(구장 hint·질문·대화)를 ContextVar(request_state)로 읽는다.
MIGRATED_TOOLS = frozenset({"get_ticket_policy", "search_nearby_places", "plan_course"})


def request_args(state):
    """V2 state → request_state(hint, question, history). 도구 호출마다 새 상태라 동시 요청·캐시 agent 와 안 섞인다."""
    from llm.tools.assistant import to_stadium_code
    turns = [m for m in state.get("messages") or [] if isinstance(m, HumanMessage)
             or (isinstance(m, AIMessage) and not m.tool_calls and m.text)]
    last = next((i for i in range(len(turns) - 1, -1, -1) if isinstance(turns[i], HumanMessage)), None)
    question = turns[last].text if last is not None else ""
    history = [{"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": m.text}
               for m in turns[:last or 0]]
    stadium = (state.get("context") or {}).get("stadium")
    return (to_stadium_code(stadium) if stadium else None), question, history


# JEV capability → 기존 llm/tools 실제 도구 이름 (선행 도구 포함). 키는 jev_guidelines.CAPABILITIES 와 같다.
CAPABILITY_TOOLS = {
    "schedule": ("get_games",),
    "standings": ("get_standings",),
    "players": ("search_players",),
    "baseball_stats": ("get_baseball_schema", "execute_baseball_select"),
    "rules": ("search_kbo_documents",),
    "stadium_info": (
        "get_stadiums", "get_stadium", "get_seat_zones", "get_seat_views", "get_seat_maps", "get_ticket_prices",
        "get_ticket_policies", "get_ticket_policy", "get_food_stores", "get_facilities", "get_stadium_contents", "get_transport", "search_kbo_documents",
    ),
    "parking_transport": ("get_stadium", "get_transport", "search_kbo_documents"),
    "community": ("search_community_posts", "get_prediction_games", "get_games"),
    "nearby_places": ("get_stadium", "search_place_knowledge", "search_places", "search_documents_tool"),
    "tourism": ("get_stadium", "search_place_knowledge", "search_tourism", "search_nearby_places", "search_documents_tool"),
    "directions": ("get_stadium", "get_directions"),
    "courses": ("search_courses", "get_course"),
    "weather": ("get_games", "get_stadium", "get_weather"),
    "day_plan": ("ask_baseball", "ask_travel_research", "ask_place_data", "get_directions", "plan_course"),
}


class DynamicToolMiddleware(AgentMiddleware):
    def __init__(self, role_tools, capability_tools=None):
        super().__init__()
        self.role_tools = frozenset(role_tools)
        self.capability_tools = capability_tools  # None 이면 역할 고정 도구 묶음 그대로

    def allowed(self, state) -> frozenset:
        if self.capability_tools is None:
            return self.role_tools
        capabilities = (state.get("decision") or {}).get("capabilities") or ()
        return self.role_tools & {n for c in capabilities for n in self.capability_tools.get(c, ())}

    def wrap_model_call(self, request, handler):
        allowed = self.allowed(request.state)
        return handler(request.override(tools=[t for t in request.tools if getattr(t, "name", None) in allowed]))

    def wrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        if name not in self.allowed(request.state):
            return ToolMessage(
                content=f"허용되지 않은 도구입니다: {name}", tool_call_id=request.tool_call["id"], name=name, status="error",
            )
        if name in MIGRATED_TOOLS:
            # ponytail: sources/course 는 호출 단위 상태에만 남고 버려진다. V2 스트림/저장에 소비처가 없어서, 생기면 artifact 로.
            from llm.tools.assistant import request_state
            with request_state(*request_args(request.state)):
                return handler(request)
        return handler(request)
