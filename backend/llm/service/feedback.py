from django.db import transaction
from django.http import Http404
from langchain_core.messages import AIMessage, HumanMessage

from llm.models import AnswerFeedback
from llm.serializer.feedback import public_feedback
from llm.serializer.message import project_history
from llm.service.chat_thread import ChatThread
from llm.service.ownership import owned_session_queryset


def set_feedback(request, session_id, data):
    # Same session-row fence as checkpoint writes/edit/delete: validate the latest answer under this lock.
    with transaction.atomic():
        session = owned_session_queryset(request).select_for_update().filter(id=session_id).first()
        if session is None:
            raise Http404("session not found")
        thread = ChatThread(session.id)
        messages, turns = thread.state()
        target = data["message_id"]
        answer_id = next((key for key, entry in thread.wire.items() if entry["id"] == target), None) if isinstance(target, int) else target
        items = project_history(messages, turns)
        item = next((item for item in items if item["id"] == answer_id and item["role"] == "assistant" and item["status"] == "completed"), None)
        if item is None:
            raise Http404("completed answer not found")
        human_id = next((key for key, turn in turns.items() if turn.get("answer_id") == answer_id), None)
        human = next((m for m in messages if isinstance(m, HumanMessage) and m.id == human_id), None)
        answer = next((m for m in messages if isinstance(m, AIMessage) and m.id == answer_id), None)
        if human is None or answer is None or answer.tool_calls:
            raise Http404("completed answer not found")
        rows = AnswerFeedback.objects.filter(session=session, answer_id=answer_id)
        if data["rating"] is None:
            rows.delete()
            return None
        # Only persist metadata that actually exists on the stored final answer / turn.
        metadata = {}
        if answer.response_metadata.get("chain_version") in ("v1", "v2"):
            metadata["version"] = answer.response_metadata["chain_version"]
        for key in ("model_name", "model", "run_id"):
            value = answer.response_metadata.get(key)
            if isinstance(value, str) and value:
                metadata[key] = value
        changes = {key: data[key] for key in ("rating", "reason", "comment")}
        row, created = AnswerFeedback.objects.get_or_create(session=session, answer_id=answer_id, defaults={
            **changes, "message_id": thread.wire[answer_id]["id"],
            "question": str(human.text), "answer": item["content"], "metadata": metadata,
        })
        if not created:
            for key, value in changes.items():
                setattr(row, key, value)
            row.save(update_fields=[*changes, "updated_at"])
        return public_feedback(row)
