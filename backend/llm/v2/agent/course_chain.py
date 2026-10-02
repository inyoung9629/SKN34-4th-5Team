"""course 체인: 경기 전후 코스 / 하루 일정 / 이동 동선 / 장소 조합."""
from langchain_core.runnables import RunnableLambda
from llm.v2.course.itinerary_request import RULES
from llm.v2.course.itinerary_service import generate_itinerary

# The model extracts constraints only; it cannot bypass server search/validation
# using free-form tool calls, RAG places, or an unvalidated final answer.
CATEGORIES = ()
TOOLS = ()
itinerary_chain = RunnableLambda(generate_itinerary)


def _run(inputs, config):
    from llm.v2.course.policy import resolve_course
    runtime = inputs.get("course_runtime") or {"state": None, "profile_team": None}
    decision = resolve_course(
        inputs["question"], runtime.get("state"), runtime.get("profile_team"),
        inputs.get("context"), inputs.get("chat_history") or [],
    )
    runtime["next_state"] = decision.state
    yield decision.message
    if decision.anchor:
        yield from itinerary_chain.stream({**inputs, "course_anchor": decision.anchor,
            "course_preferences": decision.state["preferences"], "course_state": decision.state,
            "course_previous_state": runtime.get("state") if not decision.state.get("itinerary_reset") else None,
            "course_historical": decision.historical,
            "course_notice": decision.message}, config)


course_chain = RunnableLambda(_run)
