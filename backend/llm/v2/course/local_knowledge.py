"""Local-only evidence policy until approved stored observations are connected.

The current RAG has place basics and research outcomes, not reusable menu/review
facts. Neither a catalogue label nor a historical `pass` proves a requirement.
This module never opens a URL, reads a provider key or calls a model/search API.
"""
from types import SimpleNamespace

from .food_requirements import condition_label
from .review_requirements import REVIEW_LABELS


def missing_required_evidence(request):
    missing = []
    for index, stop in enumerate(request.stops):
        if stop.phase == "inside":
            continue  # Existing internal-facility rules own this branch.
        if stop.food:
            missing.append({"stop_index": index, "type": "food", "conditions": [
                " 또는 ".join(condition_label(term) for term in group.any_of)
                for group in stop.food.all_of]})
        if stop.reviews:
            labels = [REVIEW_LABELS[condition.aspect] for condition in stop.reviews.all_of
                      if condition.priority == "required"]
            if labels:
                missing.append({"stop_index": index, "type": "review", "conditions": labels})
    return missing


class UnverifiedLocalReviews:
    """Preserve optional preferences as unknown without buying a search.

    Required preferences are stopped by the planner's local-only evidence gate.
    A future approved-observation reader should replace this explicit abstention,
    not promote the research collection to facts or pretend it was searched live.
    """
    policy = SimpleNamespace(wall_seconds=0)

    def verify(self, place, requirements):
        return {"reason": "stored_review_evidence_missing", "identity_verified": False,
                "observations": [], "sources": [], "method": "local_knowledge_unverified"}

    def audit(self):
        return {"lookups": 0, "completed_tool_calls": 0, "max_lookups": 0,
                "max_tool_calls_per_lookup": 0, "wall_seconds": 0,
                "mode": "local_only", "trace": []}
