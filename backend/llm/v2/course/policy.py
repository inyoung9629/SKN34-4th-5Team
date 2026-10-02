"""코스 진입점: 추출 → 조건 병합 → 재질문/경기 후보/고정 경기. UI와 독립적이다."""
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from .conditions import CONDITION_FIELDS, ConditionPatch, extract_conditions
from .games import GameRepository, KST, TEAM_MAP, stadium_catalog
from .state import add_profile, merge_patch, normalize_state


@dataclass
class CourseDecision:
    state: dict
    message: str
    anchor: dict | None = None
    historical: bool = False


def describe(game):
    side = {"home": "홈", "away": "원정"}.get(game.get("side"), "경기")
    return (f"{side} · {game['date']} {game['time'][:5]}(한국 시간) · "
            f"{game['away_name']} vs {game['home_name']} · {game['stadium_name']} "
            f"(경기 ID {game['id']})")


def _stop(state, message, pending):
    state["pending"] = pending
    state["pending_question"] = message
    return CourseDecision(state, message)


def _proposals(state, games, prefix=""):
    state.update(selected_game=None, candidates=games, pending="choice")
    lines = [f"{i}안: {describe(game)}" for i, game in enumerate(games, 1)]
    return CourseDecision(state, prefix + "\n".join(lines) + "\n어느 경기 기준으로 코스를 짤까요?")


def _confirmed(state, game, prefix=""):
    state.update(selected_game=game, candidates=[], pending=None)
    state.pop("pending_question", None)
    return CourseDecision(state, prefix + f"기준 경기: {describe(game)}\n\n", game)


def _problem(state, problem, start="", end=""):
    if problem in ("stale", "incomplete"):
        return _stop(state, "최신 경기 일정 또는 확정된 구장·시각을 확인하지 못했어요. "
                     "경기가 없다는 뜻은 아니며, 조건은 유지했어요. 잠시 후 다시 요청해 주세요.", "schedule_unavailable")
    if problem == "past":
        return _stop(state, "요청한 날짜에 아직 시작하지 않은 경기가 없어요. 날짜를 바꿀까요?", "date")
    if problem == "range":
        return _stop(state, "경기 조회 기간을 최대 366일 이내로 알려 주세요.", "date")
    if problem == "missing_game":
        return _stop(state, "지정한 경기 ID를 일정 데이터에서 찾지 못했어요. 팀·구장·날짜로 다시 알려 주세요.", "target")
    if problem == "condition_conflict":
        return _stop(state, "경기 정보가 바뀌어 저장한 팀·구장·날짜·시각 조건과 더 이상 맞지 않아요. "
                     "기존 조건을 유지할까요, 바뀐 일정에 맞게 조건을 수정할까요?", "changed")
    return _stop(state, f"{start}~{end} 범위에서 요청 조건에 맞는 시작 전 경기를 찾지 못했어요. "
                 "조건을 임의로 바꾸지 않았어요. 날짜·팀·구장 중 어떤 조건을 바꿀까요?", "no_match")


def _screen_patch(state, context, catalog):
    """빠진/빈 선택은 유지. 같은 화면 값이 매 요청마다 대화의 새 조건을 덮지 않게 한다."""
    context = context or {}
    screen = {k: v for k, v in context.items() if k in ("stadium", "course", "origin") and v}
    previous = state["screen_context"]
    values = {}
    if screen.get("course") and screen["course"] != previous.get("course"):
        previous_course = previous.get("course") or {}
        values.update({key: value for key, value in screen["course"].items() if previous_course.get(key) != value})
        if {"date_from", "date_to"} & values.keys():
            values.update({key: value for key, value in screen["course"].items() if key in ("date_from", "date_to")})
    if screen.get("stadium") and screen["stadium"] != previous.get("stadium") and not values.get("stadium_code"):
        name = screen["stadium"].replace(" ", "").lower()
        matches = [s for s in catalog if name in (s["stadium_code"].lower(), s["stadium_name_ko"].replace(" ", "").lower())]
        if len(matches) != 1:
            values["clarification"] = "선택한 구장을 정확히 확인하지 못했어요. 구장 이름을 채팅에 적어 주세요."
        else:
            values["stadium_code"] = matches[0]["stadium_code"]
    if screen.get("origin") and screen["origin"] != previous.get("origin"):
        origin = screen["origin"]
        values.setdefault("preferences", {})["origin"] = f"위도 {origin['lat']}, 경도 {origin['lng']}"
    result, changed = merge_patch(state, ConditionPatch.model_validate(values), source="selection")
    result["screen_context"].update(screen)
    return result, changed, values.get("clarification")


def resolve_course(question, previous=None, profile_team=None, context=None, history=(), *,
                   now=None, extractor=None, repository=None, catalog=None):
    fixed_now = now
    now = (now or timezone.now()).astimezone(KST)
    repository = repository or GameRepository(clock=(lambda: fixed_now) if fixed_now else None)
    extractor = extractor or extract_conditions
    catalog = catalog if catalog is not None else stadium_catalog()
    state = normalize_state(previous)
    try:
        state, _, screen_question = _screen_patch(state, context, catalog)
        patch = extractor(question, state, catalog, now, history)
        state, _ = merge_patch(state, patch)
        # 누적 상태에서도 날짜/시각의 상호 충돌을 검증한다.
        ConditionPatch.model_validate(state["conditions"])
    except ValueError:
        return _stop(normalize_state(previous), "요청 조건을 정확히 해석하지 못했어요. "
                     "팀·구장·날짜 중 바꾸려는 조건을 조금 더 구체적으로 알려 주세요.", "conditions")
    if patch.clarification or (screen_question and not patch.stadium_code and not patch.team_code):
        return _stop(state, patch.clarification or screen_question, "conditions")
    if (state["pending"] == "conditions" and not patch.preferences and not patch.clear_fields
            and patch.profile is None and not any(getattr(patch, k) is not None for k in CONDITION_FIELDS)):
        return _stop(state, state.get("pending_question", "바꾸려는 조건을 구체적으로 알려 주세요."), "conditions")
    if state["conditions"].get("stadium_code") not in {s["stadium_code"] for s in catalog} | {None}:
        return _stop(state, "구장 이름을 정확히 확인하지 못했어요. 어느 구장인가요?", "target")

    if patch.profile == "allow" and not (patch.team_code or patch.stadium_code or patch.game_id):
        # '내 응원팀으로 바꿔'는 명시적 변경이다. 확정된 방은 평소 프로필 변경에 영향받지 않는다.
        if profile_team not in TEAM_MAP:
            return _stop(state, "마이페이지에 등록된 응원팀이 없어요. 어느 팀의 경기를 볼까요?", "target")
        state, _ = merge_patch(state, ConditionPatch(team_code=profile_team), source="profile")
    add_profile(state, profile_team if profile_team in TEAM_MAP else None,
                explicitly_requested=patch.profile == "allow")
    conditions = state["conditions"]
    if not any(conditions.get(key) for key in ("team_code", "stadium_code", "game_id")) and not state["selected_game"]:
        return _stop(state, "어느 팀의 경기 또는 어느 구장에서 직관할까요? "
                     "팀이나 구장 중 하나만 알려 주세요. 다른 요청 조건은 기억해 둘게요.", "target")
    if conditions.get("home_away") in ("home", "away") and not conditions.get("team_code"):
        return _stop(state, "홈·원정은 어느 팀 기준인가요? 팀을 알려 주세요.", "target")

    anchor = state["selected_game"]
    if patch.action in ("recall", "edit_past") and anchor:
        if patch.action == "recall":
            return CourseDecision(state, "이 채팅방에 저장된 기준 경기예요. 현재 일정으로 재추천한 것은 아니에요.\n" + describe(anchor))
        # '방금 코스 수정' is often classified edit_past even for tomorrow's
        # game. Never let that model label bypass future schedule validation.
        if datetime.fromisoformat(anchor['starts_at']) <= now:
            return CourseDecision(state, "이전 기록의 코스를 수정할게요. 새 직관 일정 추천이 아니에요.\n"
                                  + f"기준 경기: {describe(anchor)}\n\n", anchor, historical=True)
    if anchor:
        current, problem = repository.revalidate(anchor, conditions, now)
        if problem in ("stale", "incomplete", "condition_conflict"):
            return _problem(state, problem)
        if problem == "changed":
            return _proposals(state, [current], "선택한 경기의 시각·구장·대진이 변경됐어요. 변경된 경기로 진행할까요?\n")
        if problem:
            return _stop(state, "이전 기록의 기준 경기가 이미 시작·종료됐거나 취소/삭제되어 그대로 새 일정을 짤 수 없어요. "
                         "이전 코스를 수정할까요, 같은 조건의 다음 경기로 새로 짤까요?", "expired")
        return _confirmed(state, current, "이 채팅방에서 선택한 경기를 유지할게요.\n")

    candidates = state["candidates"]
    choice = patch.choice
    # game_id가 후보 선택으로 들어오면 merge가 후보를 비운다. 기존 후보와 대조해 검증한다.
    if (patch.game_id and not patch.clear_fields and not context
            and not any(getattr(patch, k) is not None for k in CONDITION_FIELDS if k != "game_id")):
        candidates = normalize_state(previous)["candidates"]
        candidates = [g for g in candidates if g["id"] == patch.game_id]
        choice = "single" if candidates else choice
    if candidates:
        chosen = []
        if choice in ("home", "away"):
            chosen = [game for game in candidates if game.get("side") == choice]
        elif choice in ("first", "second"):
            index = 0 if choice == "first" else 1
            chosen = candidates[index:index + 1]
        elif choice == "single" or patch.action == "confirm":
            chosen = candidates if len(candidates) == 1 else []
        if len(chosen) != 1:
            return _proposals(state, candidates, "아직 기준 경기를 선택하지 않았어요.\n")
        current, problem = repository.revalidate(chosen[0], conditions, now)
        if problem == "changed":
            return _proposals(state, [current], "후보 경기의 일정이 변경됐어요. 다시 확인해 주세요.\n")
        if problem in ("stale", "incomplete", "condition_conflict"):
            return _problem(state, problem)
        if problem:
            state["candidates"] = []
            return _stop(state, "선택한 후보 경기는 더 이상 시작 전 경기가 아니에요. 다음 경기를 찾아볼까요?", "expired")
        return _confirmed(state, current)

    result = repository.search(conditions, now)
    state["search_range"] = {"from": result.start, "to": result.end}
    if result.problem:
        return _problem(state, result.problem, result.start, result.end)
    if not result.games:
        return _problem(state, "no_match", result.start, result.end)
    prefix = "마이페이지 응원팀을 기준으로 찾았어요.\n" if state["sources"].get("team_code") == "profile" else ""
    if state["sources"].get("stadium_code") == "previous_game":
        prefix += "이전 경기의 구장을 기준으로 다음 경기를 찾았어요.\n"
    prefix += f"조회 범위: {result.start}~{result.end}.\n"
    if result.dual and len(result.games) == 1:
        prefix += "이 범위에는 홈·원정 중 한쪽 후보만 있어요. 날짜 범위를 넓히지는 않았어요.\n"
    if result.tied:
        prefix += "같은 시각에 여러 경기가 있어 경기 ID로 구분해 주세요.\n"
    if result.dual or result.tied or state["sources"].get("team_code") == "profile":
        return _proposals(state, result.games, prefix)
    return _confirmed(state, result.games[0], prefix)
