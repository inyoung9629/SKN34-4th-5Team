"""Explicit public UI tool; never render model HTML or navigate to a model URL."""
from langchain_core.tools import tool
from pydantic import BaseModel, Field


class PlanningQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=160)
    choices: list[str] = Field(min_length=2, max_length=4)


def public_ui(value):
    if not isinstance(value, dict) or not isinstance(value.get("offer_writer"), bool):
        return None
    questions = value.get("questions")
    if not isinstance(questions, list) or len(questions) > 4:
        return None
    cleaned = []
    for item in questions:
        if not isinstance(item, dict):
            return None
        question, choices = item.get("question"), item.get("choices")
        if not isinstance(question, str) or not question.strip() or len(question) > 160:
            return None
        if not isinstance(choices, list) or not 2 <= len(choices) <= 4:
            return None
        if any(not isinstance(c, str) or not c.strip() or len(c) > 80 for c in choices) or len(set(choices)) != len(choices):
            return None
        cleaned.append({"question": question, "choices": list(choices)})
    return {"offer_writer": value["offer_writer"], "questions": cleaned}


@tool(response_format="content_and_artifact")
def present_planning_questions(offer_writer: bool, questions: list[PlanningQuestion]):
    """Ask 1-4 essential questions only when the stadium is unknown or the user requests choices. With a known stadium, do not require a date, game or time: call plan_course first. Never ask known information."""
    payload = public_ui({"offer_writer": offer_writer, "questions": [q.model_dump() for q in questions]})
    if payload is None:
        raise ValueError("Invalid public planning questions")
    return "계획 조건을 선택하거나 채팅으로 답할 수 있어요.", payload
