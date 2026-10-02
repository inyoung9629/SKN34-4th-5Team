from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import generics, serializers
from rest_framework.permissions import IsAuthenticated

from .activity import active_member, activity_visible


class PublicMemberSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    nickname = serializers.SerializerMethodField()
    activityVisible = serializers.SerializerMethodField()

    def get_nickname(self, member) -> str:
        return member.nickname or member.username

    def get_activityVisible(self, member) -> bool:
        return activity_visible(member)


class PublicMemberView(generics.RetrieveAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = PublicMemberSerializer

    @extend_schema(responses={200: PublicMemberSerializer, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_object(self):
        return active_member(self.kwargs["member_id"])
