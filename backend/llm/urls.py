from django.urls import include, path, re_path

from llm.views.message import ChatMessageView, ChatUsageView
from llm.views.feedback import ChatFeedbackView, AdminFeedbackListView, AdminFeedbackDetailView
from llm.views.sesstion import ChatRoomDetailView, ChatRoomListView

"""v1/v2 공통 채팅 URL. config/urls.py 는 plain path("api/", include("llm.urls")) 로 여기를
그대로 include 한다. 버전 분기는 이 파일 안의 re_path(r"^(?P<version>v1|v2)/chat/", ...) 가
캡처해서 kwargs["version"] 으로 view 에 전달한다. 실제 분기는 view 가 그 값을 service.chat 에
그대로 넘긴다 (llm/service/chat.py 의 send_message/message_update).
"""

_session_patterns = [
    path("usage/", ChatUsageView.as_view(), name="chat-usage"),
    path("sessions/<uuid:session_id>/feedback/", ChatFeedbackView.as_view(), name="chat-feedback"),
    path("admin/feedback/", AdminFeedbackListView.as_view(), name="admin-chat-feedback"),
    path("admin/feedback/<int:pk>/", AdminFeedbackDetailView.as_view(), name="admin-chat-feedback-detail"),
    path("sessions/", ChatRoomListView.as_view(), name="chat-session-list-create"),
    path("sessions/<uuid:session_id>/", ChatRoomDetailView.as_view(), name="chat-session-detail"),
    path(
        "sessions/<uuid:session_id>/messages/",
        ChatMessageView.as_view(),
        name="chat-session-messages",
    ),
]

from llm.views.attachments import ChatAttachmentView, ChatAttachmentDetailView, ToolGroupsView

urlpatterns = [
    path("v2/chat/tool-groups/", ToolGroupsView.as_view()),
    path("v2/chat/sessions/<uuid:session_id>/attachments/", ChatAttachmentView.as_view()),
    path("v2/chat/sessions/<uuid:session_id>/attachments/<uuid:attachment_id>/", ChatAttachmentDetailView.as_view()),
    re_path(r"^(?P<version>v1|v2)/chat/", include(_session_patterns)),
]
