"""운영 관리자용 게시글·신고 관리 API. 모두 운영 관리자(is_staff) 이상만 쓸 수 있다."""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.admin_views import StaffOnly

from .models import CommunityPost, CommunityReport


class AdminPages(PageNumberPagination):
    page_size = 20


class AdminPostSerializer(serializers.ModelSerializer):
    report_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = CommunityPost
        fields = ("source_id", "post_number", "board", "team_code", "category", "title", "author", "created_at", "views", "comment_count", "is_sample", "is_hidden", "report_count")
        read_only_fields = fields


class AdminPostOwnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ("id", "username", "nickname", "is_staff", "is_superuser")
        read_only_fields = fields


SANCTIONS = ("none", "7d", "30d", "permanent")


class AdminReportPostSerializer(serializers.ModelSerializer):
    owner = AdminPostOwnerSerializer(read_only=True, allow_null=True)

    class Meta:
        model = CommunityPost
        fields = ("source_id", "post_number", "board", "team_code", "title", "author", "is_hidden", "owner")
        read_only_fields = fields


class AdminReportSerializer(serializers.ModelSerializer):
    post = AdminReportPostSerializer(read_only=True)
    reporter = serializers.CharField(source="reporter.username", read_only=True)

    class Meta:
        model = CommunityReport
        fields = ("id", "post", "reporter", "reason", "detail", "created_at", "status", "handled_at")
        read_only_fields = fields


class AdminReportActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=("hold", "unhold", "hide", "delete"))
    sanction = serializers.ChoiceField(choices=SANCTIONS, required=False, default="none")


class AdminReportActionResultSerializer(serializers.Serializer):
    action = serializers.CharField()
    sanction = serializers.CharField()
    status = serializers.CharField(allow_null=True)


QUERY = OpenApiParameter("q", {"type": "string", "maxLength": 200})
PAGE = OpenApiParameter("page", {"type": "integer", "minimum": 1})


@extend_schema(parameters=[QUERY, PAGE])
class AdminPostList(generics.ListAPIView):
    """게시글 목록 (제목·작성자·글 번호 검색). 신고 수를 함께 준다."""
    permission_classes = (StaffOnly,)
    serializer_class = AdminPostSerializer
    pagination_class = AdminPages

    def get_queryset(self):
        posts = CommunityPost.objects.annotate(report_count=Count("reports", distinct=True)).order_by("-post_number")
        query = self.request.query_params.get("q", "").strip()[:200]
        if query:
            posts = posts.filter(Q(title__icontains=query) | Q(author__icontains=query) | Q(post_number=query))
        return posts


class AdminPostDetail(generics.DestroyAPIView):
    """게시글 삭제 (댓글·추천·신고도 함께 삭제된다)."""
    permission_classes = (StaffOnly,)
    serializer_class = AdminPostSerializer   # API 문서 생성용 (삭제 응답은 본문 없음)
    queryset = CommunityPost.objects.all()
    lookup_field = "source_id"
    lookup_url_kwarg = "source_id"


@extend_schema(parameters=[PAGE])
class AdminReportList(generics.ListAPIView):
    """신고 목록. 처리 대기 중인 신고가 먼저, 그 안에서는 최근 신고 순."""
    permission_classes = (StaffOnly,)
    serializer_class = AdminReportSerializer
    pagination_class = AdminPages

    def get_queryset(self):
        return CommunityReport.objects.select_related("post__owner", "reporter").annotate(
            waiting=Case(When(status="pending", then=Value(0)), default=Value(1), output_field=IntegerField()),
        ).order_by("waiting", "-created_at", "-pk")


class AdminReportAction(APIView):
    """신고 처리.
    hold: 보류(글 유지) / unhold: 보류 취소(처리 대기로 복귀) /
    hide: 글 숨김(같은 글의 신고도 숨김 처리) /
    delete: 글 삭제(신고도 함께 삭제). 화면에서 고른 처분(sanction)은 기록용으로만 받고 계정에는 적용하지 않는다.
    """
    permission_classes = (StaffOnly,)

    @extend_schema(request=AdminReportActionSerializer, responses=AdminReportActionResultSerializer)
    def post(self, request, report_id):
        serializer = AdminReportActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]
        sanction = serializer.validated_data["sanction"]
        if action != "delete" and sanction != "none":
            raise ValidationError({"sanction": "계정 처분은 글을 삭제할 때만 정할 수 있습니다."})
        now = timezone.now()
        with transaction.atomic():
            # 작성자가 없을 수 있는 글과의 외부 조인에는 FOR UPDATE를 걸 수 없어서, 신고와 글을 각각 잠근다
            report = get_object_or_404(CommunityReport.objects.select_for_update(), pk=report_id)
            post = CommunityPost.objects.select_for_update().get(pk=report.post_id)
            status = None
            if action in ("hold", "unhold") and (report.status == "hidden" or post.is_hidden):
                raise ValidationError({"action": "숨김 처리된 게시글의 신고는 보류 상태를 변경할 수 없습니다."})
            if action == "hold":
                report.status, report.handled_at, report.handled_by = "held", now, request.user
                report.save(update_fields=["status", "handled_at", "handled_by"])
                status = "held"
            elif action == "unhold":
                report.status, report.handled_at, report.handled_by = "pending", None, None
                report.save(update_fields=["status", "handled_at", "handled_by"])
                status = "pending"
            elif action == "hide":
                post.is_hidden = True
                post.save(update_fields=["is_hidden"])
                post.reports.exclude(status="hidden").update(status="hidden", handled_at=now, handled_by=request.user)
                status = "hidden"
            else:
                post.delete()
        return Response(AdminReportActionResultSerializer({
            "action": action,
            "sanction": sanction,
            "status": status,
        }).data)
