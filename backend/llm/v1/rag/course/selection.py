"""Keep manual anchors when adding relative visits or connecting a game itinerary."""
import re
from copy import deepcopy
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

_ORDER = re.compile(
    r"(?:가기|방문하기|들르기|먹기|마시기|이용하기)\s*전|(?<!오)전(?:에|(?=\s|$))|이전|앞(?:뒤|에|으로)|"
    r"(?<!오)후(?:에|(?=\s|$))|이후|뒤(?:에|로)|(?:난|한|온|은|간|른)\s*다음|"
    r"(?:다녀온|들른|먹은|마신|방문한|이용한)\s*(?:뒤|후)|(?:먹고|마시고|들르고)\s*나서"
)
_PLAN = re.compile(r"코스|루트|일정|동선|추가|넣어|넣고|들르|들러|추천|짜\s*줘|짜줄|만들")
_ACTIVITY = re.compile(r"카페|커피|식당|식사|밥|간식|술집|산책|공원|놀거리|관광|숙소|호텔|편의점|쇼핑")
_RESET = re.compile(r"선택.{0,12}(?:무시|취소|해제)|처음부터|전체.{0,8}새로")
_COURSE = re.compile(r"코스|루트|동선|짜|짤|만들|추가|넣|연결|이어|맞춰|맞추|바꿔|바꾸|조정|수정|추천|보러|관람")
_NO_GAME = re.compile(
    r"(?:경기|직관)(?:는|를|은|을)?\s*(?:안\s*(?:봐|볼|보|할|해)|빼|제외|말고|없이)|"
    r"(?:구장|야구장)(?:에는|에|은|는|을)?\s*(?:안\s*(?:가|갈|들어|들르|넣)|가지\s*않|빼|제외|말고|없이)"
)


def game_course_request(question, current):
    """An explicit date/game itinerary can extend a manual route without a stadium."""
    if not current or not (current.get("selectedPlace") or current.get("places")):
        return False
    from ..club.router import detect_stadium
    requested_code = detect_stadium(question)
    if requested_code and requested_code != current.get("stadiumCode"):
        return False
    if any(p["category"] == "STADIUM" for p in current["places"]):
        return False
    if _RESET.search(question) or _NO_GAME.search(question) or not _COURSE.search(question):
        return False
    from .venue_policy import mentions_internal
    if (re.search(r"경기|직관|(?:구장|야구장)(?:으로|에)?\s*(?:가|갈|방문|들르|입장|관람|코스|일정)", question)
            or mentions_internal(question)):
        return True
    from ..club.structured import date_in, DateRequestError
    try:
        return bool(date_in(question, datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()))
    except DateRequestError:
        return False  # The entry point returns the invalid-date explanation.


def connect_game_context(question, current, code):
    """Prepare a private working copy. Only a successful edit may publish it."""
    if not code or current.get("stadiumCode") != code or not game_course_request(question, current):
        return current, False
    from .agent import stadium_anchor, valid_origin
    from .progress import ProgressError
    working = deepcopy(current)
    selected = working.get("selectedPlace")
    if selected and not any(p["visitId"] == selected["visitId"] for p in working["places"]):
        working["places"].append(deepcopy(selected))
    stadium = next((p for p in working["places"] if p["category"] == "STADIUM"), None)
    if stadium:
        visit_id = stadium["visitId"]
    else:
        if len(working["places"]) >= 12:
            raise ProgressError("구장 방문을 연결하려면 코스에 한 자리 이상이 필요해요. 최대 12곳까지 담을 수 있어요.")
        stadium = stadium_anchor(code)
        if not stadium or valid_origin(stadium) is None:
            raise ProgressError("구장 위치를 확인하지 못해 경기 관람 일정을 연결하지 못했어요.")
        visit_id = f"course:stadium:{uuid4().hex}"
        working["places"].append({**stadium, "visitId": visit_id, "label": str(len(working["places"]) + 1), "phase": "GAME"})
    # A manual route's stale date must not replace the current request or nearest game.
    working.pop("game", None)
    working["gameConnection"] = {"visitId": visit_id, "selectedVisitId": selected["visitId"] if selected else None}
    return working, True


def selected_relative_request(question, current):
    """Preserve explicit anchor requests; the model still resolves actions and names."""
    from ..club.router import detect_stadium
    requested_code = detect_stadium(question)
    if requested_code and requested_code != (current or {}).get("stadiumCode"):
        return False
    if game_course_request(question, current):
        return True
    selected = (current or {}).get("selectedPlace")
    if not selected or _RESET.search(question) or not _ORDER.search(question):
        return False
    if not (_PLAN.search(question) or _ACTIVITY.search(question)):
        return False
    # A bare '경기 전에 카페 코스' still refers to the game, not an unrelated pin.
    refers_selected = ((selected.get("name") and selected["name"] in question)
                       or re.search(r"여기|이곳|거기|그곳|선택한|찍은|그\s*(?:가게|매장|장소)", question))
    if (selected.get("category") != "STADIUM" and not refers_selected
            and re.search(r"(?:경기|구장).{0,12}(?:전|후|가기|다녀|끝)", question)):
        return False
    return True
