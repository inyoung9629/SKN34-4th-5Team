"""Compact presentation only; Pydantic remains the authoritative validator."""
import json


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def schema_json(model):
    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in ("title", "default")}
        return [strip(v) for v in node] if isinstance(node, list) else node
    return compact_json(strip(model.model_json_schema()))


def condition_state(state):
    # Evidence, search traces, route geometry and rendered answers are NOT input
    # to the game-condition extractor. Never copy another room's state here.
    keys = ("conditions", "preferences", "sources", "cleared", "profile_allowed",
            "selected_game", "candidates", "pending", "screen_context")
    return {k: state[k] for k in keys if k in (state or {})}
