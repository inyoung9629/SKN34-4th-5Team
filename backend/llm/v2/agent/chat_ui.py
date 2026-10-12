"""Explicit public UI tool; never render model HTML or navigate to a model URL."""
import re
from urllib.parse import urlsplit

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator


class PlanningQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=160)
    choices: list[str] = Field(max_length=10, description="Empty for free text; otherwise 2-10 distinct short choices.")

    @field_validator("choices")
    @classmethod
    def validate_choices(cls, choices):
        if len(choices) == 1 or any(not c.strip() or len(c) > 80 for c in choices) or len(set(choices)) != len(choices):
            raise ValueError("Use empty choices for free text or 2-10 distinct nonempty choices")
        return choices


def public_ui(value):
    from .course_output import _source_url

    if not isinstance(value, dict) or not isinstance(value.get("offer_writer"), bool):
        return None
    questions = value.get("questions")
    if not isinstance(questions, list) or len(questions) > 4 or (not questions and not value["offer_writer"]):
        return None
    cleaned = []
    for item in questions:
        if not isinstance(item, dict):
            return None
        question, choices = item.get("question"), item.get("choices")
        if not isinstance(question, str) or not question.strip() or len(question) > 160:
            return None
        if not isinstance(choices, list) or len(choices) == 1 or len(choices) > 10:
            return None
        if any(not isinstance(c, str) or not c.strip() or len(c) > 80 for c in choices) or len(set(choices)) != len(choices):
            return None
        row = {"question": question, "choices": list(choices)}
        links = []
        seen = set()
        for raw in item.get("choice_links", []) if isinstance(item.get("choice_links"), list) else []:
            if not isinstance(raw, dict) or raw.get("choice") not in choices or raw["choice"] in seen:
                continue
            url = _source_url(raw.get("url"))
            if not url:
                continue
            label = "매장 상세 위치" if urlsplit(url).hostname == "myseatcheck.com" else "장소 정보"
            links.append({"choice": raw["choice"], "url": url, "label": label})
            seen.add(raw["choice"])
        if links:
            row["choice_links"] = links
        cleaned.append(row)
    return {"offer_writer": value["offer_writer"], "questions": cleaned}


def with_place_links(payload, state):
    """Attach existing course sources on the server; no model URLs or lookup."""
    from llm.place_choices import selected_place
    current = (state.get("context") or {}).get("currentCourse")
    if not isinstance(current, dict):
        current = (state.get("course_memory") or {}).get("current") or {}
    places = current.get("places") or []
    payload = public_ui(payload)
    if payload is None:
        return None
    for question in payload["questions"]:
        question["choice_links"] = [
            {"choice": choice, "url": place.get("placeUrl")}
            for choice in question["choices"]
            if (place := selected_place(choice, places)) is not None
        ]
    return public_ui(payload)


@tool(response_format="content_and_artifact")
def present_planning_questions(offer_writer: bool, questions: list[PlanningQuestion]):
    """Use for essential supervisor questions only; answer directly when context/results suffice and reuse prior candidates/results without repeated lookup or questions. Before asking for an enumerable team/stadium/player/place, obtain verified candidates via exposed direct tools or a fixed specialist's narrow lookup (ask_baseball for teams/stadiums); main asks after the result, never invent options. Put 2-10 verified candidates in choices, not question prose; existing free input stays available. Show all candidates when at most 10; otherwise disclose a partial list. Empty choices only for genuinely open-ended input or disclosed unavailable facts/failed lookup. Ask 1-4 essential unknown questions. Unspecified schedule can query the whole schedule without requiring team/date. Non-course: offer_writer=False. Course: first condition questions may offer the writer, follow-ups False; a writer-only offer may have no questions. With a known course stadium, call ask_course first without requiring date/game/time."""
    payload = public_ui({"offer_writer": offer_writer, "questions": [q.model_dump() for q in questions]})
    if payload is None:
        raise ValueError("Invalid public planning questions")
    return "계획 조건을 선택하거나 채팅으로 답할 수 있어요.", payload


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_VIDEOS = 5


def public_videos(value):
    """select_highlight_scenes artifact → 화면 영상 카드. 서버가 DB 에서 만든 videoId·시작 시각만 형식 검사해 통과시킨다."""
    if not isinstance(value, dict) or not isinstance(value.get("videos"), list):
        return []
    videos = []
    for raw in value["videos"][:MAX_VIDEOS]:
        if not isinstance(raw, dict):
            continue
        video_id, start = raw.get("videoId"), raw.get("start")
        if not isinstance(video_id, str) or not VIDEO_ID.match(video_id):
            continue
        if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 6 * 3600:
            continue
        label = raw.get("label") if isinstance(raw.get("label"), str) else ""
        game_date = raw.get("gameDate") if isinstance(raw.get("gameDate"), str) else None
        videos.append({"videoId": video_id, "start": start, "label": label.strip()[:120],
                       **({"gameDate": game_date[:10]} if game_date else {})})
    return videos


def highlight_videos(messages):
    """한 턴의 메시지(메인 + ask_highlight 하위 대화)에서 마지막으로 선택된 하이라이트 영상 카드를 꺼낸다."""
    from langchain_core.messages import ToolMessage
    from llm.serializer.message import _artifact_messages
    found = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.status == "error":
            continue
        if message.name == "select_highlight_scenes":
            found = public_videos(message.artifact) or found
        elif message.name == "ask_highlight":
            found = highlight_videos(_artifact_messages(message.artifact)) or found
    return found

