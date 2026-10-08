from rest_framework import serializers
from rest_framework.generics import GenericAPIView, ListAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response

from llm.models import AnswerFeedback
from llm.serializer.feedback import AdminFeedbackSerializer, FeedbackInputSerializer, REASONS
from llm.service.feedback import set_feedback


class ChatFeedbackView(GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = FeedbackInputSerializer

    def put(self, request, session_id, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response({"feedback": set_feedback(request, session_id, serializer.validated_data)})


class SuperuserOnly(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and getattr(request.user, "is_superuser", False) is True


class FeedbackPagination(PageNumberPagination):
    page_size = 20


class AdminFeedbackListView(ListAPIView):
    permission_classes = [SuperuserOnly]
    serializer_class = AdminFeedbackSerializer
    pagination_class = FeedbackPagination

    def get_queryset(self):
        rows = AnswerFeedback.objects.all()
        for key, choices in (("rating", ("up", "down")), ("reason", REASONS)):
            value = self.request.query_params.get(key, "")
            if value:
                if value not in choices:
                    raise serializers.ValidationError({key: "올바른 필터를 선택해 주세요."})
                rows = rows.filter(**{key: value})
        return rows


class AdminFeedbackDetailView(RetrieveAPIView):
    permission_classes = [SuperuserOnly]
    serializer_class = AdminFeedbackSerializer
    queryset = AnswerFeedback.objects.all()
