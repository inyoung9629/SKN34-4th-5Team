"""Return the verified course text only for an unambiguous course-only turn."""
from datetime import date
import re
from uuid import uuid4

from django.utils import timezone
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from llm.service.chat_runs import check_cancelled


# Conservative backstop to the semantic course_only decision. A false negative
# merely keeps the existing model; a false positive could omit a user's question.
EXTRA_REQUEST = re.compile(
    r"https?://|왜|이유|근거|설명|비교|분석|번역|영어로|일본어로|중국어로|표로|표\s*형식|json|"
    r"티켓|예매|순위|선수|반입|환불|규정|규칙|날씨|기온|강수|가격|예산|저렴|가성비|예약|빈방|얼마|"
    r"주차\s*(?:요금|비용)", re.I)
EXTRA_CAPABILITIES = frozenset({"weather", "standings", "players", "baseball_stats", "rules", "community", "courses", "carry_in", "highlight"})


def course_text(outcome):
    """Keep every warning/link and deterministic game provenance/date notes."""
    text = outcome.get("answer")
    if not isinstance(text, str) or not text.strip() or outcome.get("courseOnly") is not True:
        return None
    from llm.v1.rag.course.game_metadata import annotate
    text = annotate(text, outcome.get("game"))
    try:
        day = date.fromisoformat((outcome.get("game") or {}).get("date", ""))
    except (ValueError, TypeError):
        day = None
    if day and day < timezone.localdate():
        text = f"요청하신 {day.isoformat()}은 이미 지난 날짜예요. 당시 경기 일정 기준으로 구성한 코스예요.\n\n{text}"
    return text


class CourseAnswerMiddleware(AgentMiddleware):
    @hook_config(can_jump_to=["tools", "end"])
    def before_model(self, state, runtime):
        decision = state.get("decision") or {}
        from llm.v1.rag.course import recommendation_policy
        editing = recommendation_policy.enabled() and decision.get("course_request") == "EDIT"
        if decision.get("allowed") is not True or not (decision.get("course_request") == "NEW" or editing):
            return None
        if set(decision.get("capabilities", [])) & EXTRA_CAPABILITIES:
            return None
        if any(state.get(key) for key in ("attachment_sources", "attachment_web_call_id", "attachment_source_incomplete")):
            return None
        # Attachment middleware also rebuilds ordinary text messages. Only actual
        # attachments/multimodal content require the existing answer model.
        if any(m.additional_kwargs.get("attachment_ids") or not isinstance(m.content, str)
               for m in (state.get("attachment_messages") or {}).values()):
            return None
        messages = state.get("messages") or []
        last = next((i for i in range(len(messages)-1, -1, -1) if isinstance(messages[i], HumanMessage)), None)
        if last is None:
            return None
        human = messages[last]
        if human.additional_kwargs.get("attachment_ids") or not isinstance(human.content, str) or EXTRA_REQUEST.search(human.content):
            return None
        current = messages[last+1:]
        context = state.get("context") or {}
        if not current and context.get("intent") == "route" and "day_plan" in decision.get("capabilities", []):
            from llm.tools.assistant import to_stadium_code
            if to_stadium_code(context.get("stadium")):
                # The guard classified NEW/EDIT; the course tool interprets
                # the complete original request. Skip only the model that would
                # choose ask_course again, preserving its normal tool guards,
                # cancellation, usage accounting and structured result.
                check_cancelled()
                if editing and not context.get("currentCourse"):
                    return None
                return {"messages": [AIMessage("요청한 순서와 조건에 맞춰 코스를 확인할게요.", tool_calls=[{
                    "name": "ask_course", "id": uuid4().hex, "type": "tool_call",
                    "args": {"task": human.content, "summary": "요청한 코스 수정" if editing else "요청한 코스 생성"}}])], "jump_to": "tools"}
        calls = [c for m in current if isinstance(m, AIMessage) for c in m.tool_calls]
        results = [m for m in current if isinstance(m, ToolMessage)]
        if len(calls) != 1 or calls[0]["name"] != "ask_course" or len(results) != 1:
            return None
        result = results[0]
        if result.name != "ask_course" or result.status == "error" or result.tool_call_id != calls[0]["id"]:
            return None
        from llm.v2.agent.course_output import course_artifacts, public_course
        artifacts = list(course_artifacts([result]))
        if len(artifacts) != 1:
            return None
        artifact = artifacts[0]
        if editing and artifact.get("course_edit_handled") is not True:
            return None
        text = artifact.get("course_direct_answer")
        if not public_course(artifact.get("course")) or not isinstance(text, str) or not text.strip():
            return None
        check_cancelled()
        return {"messages": [AIMessage(text, response_metadata={"course_direct": True})], "jump_to": "end"}
