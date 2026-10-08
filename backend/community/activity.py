from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import generics, serializers
from rest_framework.permissions import IsAuthenticated

from accounts.activity import activity_author
from .models import CommunityComment
from .pagination import CommunityPostPagination, CommunityPostQuery


class CommunityMemberCommentSerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    postId = serializers.CharField(source="post.source_id", read_only=True)
    postTitle = serializers.CharField(source="post.title", read_only=True)
    board = serializers.ChoiceField(source="post.board", choices=("free", "teams"), read_only=True)
    teamCode = serializers.CharField(source="post.team_code", read_only=True, allow_blank=True)

    class Meta:
        model = CommunityComment
        fields = ("id", "content", "createdAt", "postId", "postTitle", "board", "teamCode")
        read_only_fields = fields


class CommunityMemberCommentListView(generics.ListAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = CommunityMemberCommentSerializer
    pagination_class = CommunityPostPagination

    @extend_schema(
        parameters=[
            OpenApiParameter("author_id", {"type": "integer", "minimum": 1}, required=True),
            OpenApiParameter("page", {"type": "integer", "minimum": 1, "maximum": 2_147_483_647}),
            OpenApiParameter("page_size", {"type": "integer", "minimum": 1, "maximum": 100}),
        ],
        responses={200: CommunityMemberCommentSerializer(many=True), 400: OpenApiTypes.OBJECT,
                   401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        member = activity_author(self.request)
        CommunityPostQuery.from_params(self.request.query_params)
        return CommunityComment.objects.filter(
            author=member, post__is_hidden=False,
        ).select_related("post").order_by("-created_at", "-id")
