"""메시지별 스냅샷으로 복구 가능한 코스 상태. 대화방/사용자 전체 기록을 섞지 않는다."""
from copy import deepcopy

from .conditions import ANCHOR_FIELDS, CONDITION_FIELDS

STATE_VERSION = 1


def empty_state():
    return {"version": STATE_VERSION, "conditions": {}, "sources": {}, "cleared": [],
            "preferences": {}, "profile_allowed": True, "selected_game": None,
            "candidates": [], "pending": None, "screen_context": {}}


def normalize_state(value):
    state = empty_state()
    if isinstance(value, dict) and value.get("version") == STATE_VERSION:
        state.update(deepcopy(value))
    return state


def merge_patch(previous, patch, source="user"):
    state = normalize_state(previous)
    conditions, sources = state["conditions"], state["sources"]
    cleared = set(state["cleared"])
    changed = False
    clear_fields = set(patch.clear_fields)
    if clear_fields & {"date_from", "date_to"}:
        clear_fields.update(("date_from", "date_to"))
    for key in clear_fields:
        if key == "preferences":
            state["preferences"] = {}
            state.pop("itinerary_request", None)
            state.pop("itinerary_result", None)
            state.pop("itinerary_baseline", None)
            state.pop("itinerary_origin", None)
            state["itinerary_reset"] = True
            continue
        changed = changed or key in ANCHOR_FIELDS
        conditions.pop(key, None)
        sources.pop(key, None)
        cleared.add(key)
    updates = {k: v for k, v in patch.model_dump(mode="json").items()
               if k in CONDITION_FIELDS and v is not None}
    # 프로필 추정은 사용자가 구장/팀/경기를 직접 고르면 숨은 필터가 되지 않는다.
    if any(k in updates for k in ("team_code", "stadium_code", "game_id")) and sources.get("team_code") == "profile":
        conditions.pop("team_code", None)
        sources.pop("team_code", None)
        changed = True
    if patch.action == "next_game":
        for key in ("date_from", "date_to", "game_id"):
            conditions.pop(key, None)
            sources.pop(key, None)
        # ID로만 골랐던 경기라면, '다음 경기'는 그 경기의 실제 구장을 이어받는다.
        anchor = state["selected_game"]
        if anchor and not (conditions.get("team_code") or conditions.get("stadium_code")):
            conditions["stadium_code"] = anchor["stadium_code"]
            sources["stadium_code"] = "previous_game"
        changed = True
    for key, value in updates.items():
        changed = changed or conditions.get(key) != value
        conditions[key], sources[key] = value, source
        cleared.discard(key)
    if patch.profile is not None:
        state["profile_allowed"] = patch.profile == "allow"
        if patch.profile == "deny" and sources.get("team_code") == "profile":
            conditions.pop("team_code", None)
            sources.pop("team_code", None)
            changed = True
        if patch.profile == "allow":
            cleared.discard("team_code")
    for key, value in patch.preferences.items():
        if value is None:
            state["preferences"].pop(key, None)
        else:
            state["preferences"][key] = value
    if changed:
        if "game_id" not in updates:
            conditions.pop("game_id", None)
            sources.pop("game_id", None)
        state.update(selected_game=None, candidates=[], pending=None)
    if changed or patch.preferences:
        state.pop("itinerary_result", None)
    state["cleared"] = sorted(cleared)
    return state, changed


def add_profile(state, team_code, explicitly_requested=False):
    conditions = state["conditions"]
    if (not team_code or not state["profile_allowed"] or "team_code" in state["cleared"]
            or conditions.get("team_code") or state["selected_game"]):
        return
    if set(state["cleared"]) & {"stadium_code", "game_id"} and not explicitly_requested:
        return
    if (conditions.get("stadium_code") or conditions.get("game_id")) and not explicitly_requested:
        return
    conditions["team_code"] = team_code
    state["sources"]["team_code"] = "profile"
