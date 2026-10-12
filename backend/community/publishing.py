from django.db import DataError, IntegrityError, transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .draft_schemas import DraftPublishInput
from .drafts import validated
from .models import CommunityDraft, CommunityImage, CommunityPost
from .serializers import CommunityPostSerializer
from .views import post_queryset, same_submission
from accounts.point_policy import POST, grant_definition
from accounts.point_service import grant_points


def draft_post_data(draft):
    return {
        "board": draft.board,
        "teamCode": draft.team_code,
        "category": draft.category,
        "title": draft.title,
        "content": draft.content,
    }


class DraftPublishSchema(serializers.Serializer):
    revision = serializers.IntegerField(min_value=1)


@extend_schema(
    request=DraftPublishSchema,
    parameters=[OpenApiParameter("Idempotency-Key", OpenApiTypes.STR, OpenApiParameter.HEADER, required=True)],
    responses={
        200: CommunityPostSerializer,
        201: CommunityPostSerializer,
        400: OpenApiTypes.OBJECT,
        401: OpenApiTypes.OBJECT,
        404: OpenApiTypes.OBJECT,
        409: OpenApiTypes.OBJECT,
    },
)
class CommunityDraftPublishView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, draft_id):
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key or len(key) > 128:
            raise ValidationError({"idempotencyKey": "1~128자의 Idempotency-Key가 필요합니다."})
        expected_revision = validated(DraftPublishInput, request.data).revision

        try:
            with transaction.atomic():
                draft = get_object_or_404(
                    CommunityDraft.objects.select_for_update(),
                    pk=draft_id,
                    owner=request.user,
                )
                data = draft_post_data(draft)
                serializer = CommunityPostSerializer(data=data)

                if draft.published_post_id:
                    post = CommunityPost.objects.select_for_update().get(pk=draft.published_post_id)
                    if (
                        post.idempotency_key != key
                        or not serializer.is_valid()
                        or not same_submission(post, serializer.validated_data)
                    ):
                        return Response(
                            {"idempotencyKey": "게시된 초안은 같은 키와 내용으로만 다시 요청할 수 있습니다."},
                            status=status.HTTP_409_CONFLICT,
                        )
                    if draft.revision != expected_revision:
                        return Response(
                            {"revision": "임시저장본이 다른 요청에서 수정되었습니다."},
                            status=status.HTTP_409_CONFLICT,
                        )
                    return Response(CommunityPostSerializer(post_queryset().get(pk=post.pk)).data)

                if draft.revision != expected_revision:
                    return Response(
                        {"revision": "임시저장본이 다른 요청에서 수정되었습니다."},
                        status=status.HTTP_409_CONFLICT,
                    )
                serializer.is_valid(raise_exception=True)
                values = serializer.validated_data

                if CommunityPost.objects.filter(owner=request.user, idempotency_key=key).exists():
                    return Response(
                        {"idempotencyKey": "다른 게시 작업에서 이미 사용한 키입니다."},
                        status=status.HTTP_409_CONFLICT,
                    )

                images = list(CommunityImage.objects.select_for_update(of=("self",)).filter(draft=draft))
                if len(images) > 10 or any(image.owner_id != request.user.id for image in images):
                    raise ValidationError({"images": "초안에는 본인 이미지 최대 10개만 첨부할 수 있습니다."})

                post = serializer.save(
                    owner=request.user,
                    author=request.user.nickname or request.user.username,
                    idempotency_key=key,
                    is_sample=False,
                )
                grant_points(user=request.user, **grant_definition(POST, f"{post.source_id}"))
                if images:
                    CommunityImage.objects.filter(pk__in=(image.pk for image in images)).update(draft=None, post=post)
                draft.published_post = post
                draft.save(update_fields=("published_post", "updated_at"))
        except IntegrityError:
            return Response(
                {"idempotencyKey": "다른 게시 작업에서 이미 사용한 키입니다."},
                status=status.HTTP_409_CONFLICT,
            )
        except DataError as exc:
            raise ValidationError({"postNumber": "게시글 번호를 더 발급할 수 없습니다."}) from exc

        return Response(
            CommunityPostSerializer(post_queryset().get(pk=post.pk)).data,
            status=status.HTTP_201_CREATED,
        )
