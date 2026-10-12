from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CommunityComment, CommunityPost, CommunityReport, CommunityVote
from accounts.point_policy import COMMENT, grant_definition
from accounts.point_service import grant_points
from .serializers import (
    CommunityCommentSerializer,
    CommunityCommentWriteSerializer,
    CommunityReportResultSerializer,
    CommunityReportWriteSerializer,
    CommunityVoteStateSerializer,
    CommunityVoteWriteSerializer,
)


def _vote_counts(post):
    return post.votes.aggregate(
        recommendations=Count("id", filter=Q(value="up")),
        downvotes=Count("id", filter=Q(value="down")),
    )


def _vote_response(post, user):
    counts = _vote_counts(post)
    return {
        "vote": post.votes.filter(user=user).values_list("value", flat=True).first(),
        **counts,
    }


class CommentListCreateView(APIView):
    def get_permissions(self):
        return [AllowAny()] if self.request.method == "GET" else [IsAuthenticated()]

    @extend_schema(
        parameters=[OpenApiParameter("order", OpenApiTypes.STR, enum=("oldest", "newest"))],
        responses={200: CommunityCommentSerializer(many=True), 400: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT}, auth=[],
    )
    def get(self, request, source_id):
        post = get_object_or_404(CommunityPost, source_id=source_id)
        order = request.query_params.get("order", "oldest")
        if order not in {"oldest", "newest"}:
            raise serializers.ValidationError({"order": "oldest 또는 newest를 입력해 주세요."})
        ordering = ("created_at", "id") if order == "oldest" else ("-created_at", "-id")
        comments = post.comments.select_related("author").order_by(*ordering)
        return Response(CommunityCommentSerializer(comments, many=True).data)

    @extend_schema(
        request=CommunityCommentWriteSerializer,
        responses={201: CommunityCommentSerializer, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    )
    @transaction.atomic
    def post(self, request, source_id):
        post = get_object_or_404(CommunityPost.objects.select_for_update(), source_id=source_id)
        serializer = CommunityCommentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = CommunityComment.objects.create(
            post=post, author=request.user, **serializer.validated_data
        )
        grant_points(user=request.user, **grant_definition(COMMENT, f"{comment.pk}"))
        CommunityPost.objects.filter(pk=post.pk).update(comment_count=post.comments.count())
        return Response(CommunityCommentSerializer(comment).data, status=status.HTTP_201_CREATED)


class CommentDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    @staticmethod
    def _locked_owned_comment(comment_id, user):
        post_id = get_object_or_404(
            CommunityComment.objects.only("post_id"), pk=comment_id
        ).post_id
        post = get_object_or_404(CommunityPost.objects.select_for_update(), pk=post_id)
        comment = get_object_or_404(
            CommunityComment.objects.select_for_update().select_related("author"),
            pk=comment_id,
            post=post,
        )
        if comment.author_id != user.id:
            raise PermissionDenied("본인 댓글만 수정하거나 삭제할 수 있습니다.")
        return post, comment

    @extend_schema(
        request=CommunityCommentWriteSerializer,
        responses={200: CommunityCommentSerializer, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    )
    @transaction.atomic
    def patch(self, request, comment_id):
        _, comment = self._locked_owned_comment(comment_id, request.user)
        serializer = CommunityCommentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment.content = serializer.validated_data["content"]
        comment.save(update_fields=("content", "updated_at"))
        return Response(CommunityCommentSerializer(comment).data)

    @extend_schema(responses={204: OpenApiResponse(description="본문 없음"), 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT})
    @transaction.atomic
    def delete(self, request, comment_id):
        post, comment = self._locked_owned_comment(comment_id, request.user)
        comment.delete()
        CommunityPost.objects.filter(pk=post.pk).update(comment_count=post.comments.count())
        return Response(status=status.HTTP_204_NO_CONTENT)


class VoteView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(responses={200: CommunityVoteStateSerializer, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT})
    def get(self, request, source_id):
        post = get_object_or_404(CommunityPost, source_id=source_id)
        return Response(CommunityVoteStateSerializer(_vote_response(post, request.user)).data)

    @extend_schema(
        request=CommunityVoteWriteSerializer,
        responses={200: CommunityVoteStateSerializer, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    )
    @transaction.atomic
    def post(self, request, source_id):
        post = get_object_or_404(CommunityPost.objects.select_for_update(), source_id=source_id)
        serializer = CommunityVoteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        desired = serializer.validated_data["vote"]
        current = CommunityVote.objects.filter(post=post, user=request.user).first()

        if desired is None:
            if current:
                current.delete()
        elif current:
            if current.value != desired:
                current.value = desired
                current.save(update_fields=("value",))
        else:
            CommunityVote.objects.create(post=post, user=request.user, value=desired)

        counts = _vote_counts(post)
        CommunityPost.objects.filter(pk=post.pk).update(recommendations=counts["recommendations"])
        return Response(CommunityVoteStateSerializer({"vote": desired, **counts}).data)


class ReportCreateView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        request=CommunityReportWriteSerializer,
        responses={200: CommunityReportResultSerializer, 201: CommunityReportResultSerializer, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    )
    def post(self, request, source_id):
        post = get_object_or_404(CommunityPost, source_id=source_id)
        serializer = CommunityReportWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report, created = CommunityReport.objects.get_or_create(
            post=post,
            reporter=request.user,
            defaults=serializer.validated_data,
        )
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        data = CommunityReportResultSerializer({"id": report.id, "created": created}).data
        return Response(data, status=response_status)
