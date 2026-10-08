"""회원/비회원 ChatSession 소유권 조회 헬퍼.

세션 목록·상세·수정·삭제, 메시지 목록·조회·발송·수정·삭제가 전부 같은 규칙을
한 곳에서 쓰도록 모아둔다: 로그인 사용자는 request.user, 비회원은 guest_id
쿠키로만 자신의 세션에 접근할 수 있다. 남의 세션/쿠키 없는 비회원은 404.
guest_id 쿠키는 UUID 문자열이어야 하며, 형식이 깨진 값은 "쿠키 없음"과 똑같이 취급한다
(UUIDField 에 그대로 filter 하면 ValidationError 500 이 나므로 여기서 한 번만 검증한다).
"""
import uuid

from django.http import Http404

from llm.models import ChatSession

GUEST_COOKIE_NAME = "guest_id"


def parse_guest_id(request):
    """guest_id 쿠키를 UUID 로 돌려준다. 없거나 UUID 형식이 아니면 None."""
    raw = request.COOKIES.get(GUEST_COOKIE_NAME)
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def owned_session_queryset(request):
    """요청자(회원 또는 guest 쿠키)가 소유한 ChatSession만 돌려준다."""
    if request.user.is_authenticated:
        return ChatSession.objects.filter(user=request.user)

    guest_id = parse_guest_id(request)
    if guest_id is None:
        return ChatSession.objects.none()
    return ChatSession.objects.filter(guest=guest_id)


def get_owned_session(request, session_id):
    """소유하지 않은/존재하지 않는 session_id는 전부 404로 통일한다."""
    session = owned_session_queryset(request).filter(id=session_id).first()
    if session is None:
        raise Http404("session not found")
    return session
