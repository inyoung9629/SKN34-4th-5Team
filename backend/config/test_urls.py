from django.test import SimpleTestCase
from django.urls import Resolver404
from django.urls import resolve

from baseball.views import RESOURCE_VIEWSETS
from community.views import CommunityPostListCreateView
from llm.views.message import ChatMessageView
from llm.views.sesstion import ChatRoomListView
from travel.views import CourseListCreateView


class RootUrlResolutionTests(SimpleTestCase):
    def test_feature_routes_resolve_to_expected_views(self):
        # 이 저장소의 다른 앱 (baseball/community/travel) 라우팅은 LLM URL 통합과 무관하게
        # 그대로 유지된다는 걸 같이 확인한다 (기존 테스트, 변경 없음).
        cases = (
            ("/api/v1/courses/", CourseListCreateView),
            ("/api/v1/community/posts/", CommunityPostListCreateView),
            ("/api/v1/baseball/manage/teams/", RESOURCE_VIEWSETS["teams"]),
        )

        for path, view_class in cases:
            with self.subTest(path=path):
                func = resolve(path).func
                self.assertIs(getattr(func, "view_class", getattr(func, "cls", None)), view_class)

    def test_chat_routes_resolve_for_both_api_versions(self):
        """api/v1/chat/ 와 api/v2/chat/ 가 config/urls.py 의 plain path("api/", include("llm.urls"))
        를 공유하고, llm/urls.py 안의 re_path(r"^(?P<version>v1|v2)/chat/", ...) 가 캡처한 version
        kwarg 가 view 에 도달하는지 확인한다."""
        cases = (
            ("/api/v1/chat/sessions/", ChatRoomListView, "v1"),
            ("/api/v2/chat/sessions/", ChatRoomListView, "v2"),
            ("/api/v1/chat/sessions/11111111-1111-1111-1111-111111111111/messages/", ChatMessageView, "v1"),
            ("/api/v2/chat/sessions/11111111-1111-1111-1111-111111111111/messages/", ChatMessageView, "v2"),
        )

        for path, view_class, expected_version in cases:
            with self.subTest(path=path):
                match = resolve(path)
                func = match.func
                self.assertIs(getattr(func, "view_class", getattr(func, "cls", None)), view_class)
                self.assertEqual(match.kwargs.get("version"), expected_version)

    def test_unknown_api_version_prefix_does_not_resolve(self):
        """api/v1, api/v2 이외의 버전 접두어는 애초에 라우팅되지 않는다
        (llm/urls.py 의 re_path 정규식은 v1/v2 만 허용한다)."""
        with self.assertRaises(Resolver404):
            resolve("/api/v3/chat/sessions/")
