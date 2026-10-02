"""guest_id 쿠키가 UUID 형식이 아닐 때의 회귀 테스트.

수정 전에는 llm/service/ownership.py 가 쿠키 문자열을 ChatSession.guest(UUIDField) 에 그대로
filter 해서 ValidationError(500) 가 났고, POST /sessions/ 는 깨진 쿠키를 그대로 소유자로 저장하려
했다. 이제 깨진 쿠키는 "쿠키 없음" 과 동일하게 취급되고, 생성 시에는 새 UUID 를 발급해 쿠키를 교체한다.

실제 LLM 호출 없음 (세션 목록/생성/수정/삭제, 메시지 목록만 사용).
"""
import uuid

from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from llm.models import ChatSession
from llm.service.ownership import GUEST_COOKIE_NAME, get_owned_session, parse_guest_id

User = get_user_model()

INVALID_COOKIES = ("not-a-uuid", "12345", "aaaaaaaa-aaaa-aaaa-aaaa", "' OR 1=1 --")
OTHER_GUEST = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _client_with_cookie(value):
    client = APIClient()
    client.cookies[GUEST_COOKIE_NAME] = value
    return client


class ParseGuestIdTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_valid_uuid_cookie_parses(self):
        request = self.factory.get("/")
        request.COOKIES[GUEST_COOKIE_NAME] = OTHER_GUEST
        self.assertEqual(parse_guest_id(request), uuid.UUID(OTHER_GUEST))

    def test_missing_and_invalid_cookies_are_none(self):
        request = self.factory.get("/")
        self.assertIsNone(parse_guest_id(request))
        for raw in INVALID_COOKIES:
            with self.subTest(cookie=raw):
                request.COOKIES[GUEST_COOKIE_NAME] = raw
                self.assertIsNone(parse_guest_id(request))

    def test_get_owned_session_with_invalid_cookie_is_404_not_500(self):
        session = ChatSession.objects.create(guest=OTHER_GUEST)
        request = self.factory.get("/")
        request.user = type("Anon", (), {"is_authenticated": False})()
        request.COOKIES[GUEST_COOKIE_NAME] = "not-a-uuid"
        with self.assertRaises(Http404):
            get_owned_session(request, session.id)


class InvalidGuestCookieViewTest(TestCase):
    def setUp(self):
        self.other_session = ChatSession.objects.create(guest=OTHER_GUEST, title="남의 방")
        self.member = User.objects.create_user(username="guest-regress-member", password="pw12345!")
        self.member_session = ChatSession.objects.create(user=self.member, title="회원 방")

    def test_invalid_cookie_lists_nothing_like_no_identity(self):
        anon = APIClient().get("/api/v2/chat/sessions/")
        self.assertEqual(anon.status_code, 200)
        for raw in INVALID_COOKIES:
            with self.subTest(cookie=raw):
                response = _client_with_cookie(raw).get("/api/v2/chat/sessions/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), anon.json())
                self.assertEqual(response.json(), [])

    def test_invalid_cookie_cannot_touch_other_sessions(self):
        client = _client_with_cookie("not-a-uuid")
        for target in (self.other_session, self.member_session):
            with self.subTest(target=target.title):
                self.assertEqual(
                    client.patch(f"/api/v2/chat/sessions/{target.id}/", {"title": "탈취"}, format="json").status_code,
                    404,
                )
                self.assertEqual(client.get(f"/api/v2/chat/sessions/{target.id}/messages/").status_code, 404)
                self.assertEqual(client.delete(f"/api/v2/chat/sessions/{target.id}/").status_code, 404)
        self.assertEqual(ChatSession.objects.count(), 2)
        self.assertEqual(ChatSession.objects.get(id=self.other_session.id).title, "남의 방")

    def test_invalid_cookie_on_create_gets_fresh_uuid_and_cookie_replaced(self):
        response = _client_with_cookie("not-a-uuid").post(
            "/api/v2/chat/sessions/", {"title": "새 비회원 방"}, format="json",
        )
        self.assertEqual(response.status_code, 201)
        new_cookie = response.cookies[GUEST_COOKIE_NAME].value
        self.assertNotEqual(new_cookie, "not-a-uuid")
        issued = uuid.UUID(new_cookie)  # 유효한 UUID 여야 한다
        created = ChatSession.objects.get(id=response.json()["id"])
        self.assertIsNone(created.user)
        self.assertEqual(created.guest, issued)
        # 새 쿠키로는 자기 방만 보인다
        listed = _client_with_cookie(new_cookie).get("/api/v2/chat/sessions/").json()
        self.assertEqual([row["id"] for row in listed], [str(created.id)])

    def test_valid_cookie_on_create_keeps_same_guest(self):
        response = _client_with_cookie(OTHER_GUEST).post(
            "/api/v2/chat/sessions/", {"title": "두 번째 방"}, format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.cookies[GUEST_COOKIE_NAME].value, OTHER_GUEST)
        self.assertEqual(ChatSession.objects.get(id=response.json()["id"]).guest, uuid.UUID(OTHER_GUEST))

    def test_member_with_invalid_guest_cookie_still_uses_auth(self):
        client = _client_with_cookie("not-a-uuid")
        client.force_authenticate(self.member)
        listed = client.get("/api/v2/chat/sessions/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([row["id"] for row in listed.json()], [str(self.member_session.id)])
        created = client.post("/api/v2/chat/sessions/", {"title": "회원 새 방"}, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertNotIn(GUEST_COOKIE_NAME, created.cookies)
        self.assertEqual(ChatSession.objects.get(id=created.json()["id"]).user, self.member)
