from rest_framework import serializers

from .models import AdCreative, AdEvent, AdPlacement


class AdQuerySerializer(serializers.Serializer):
    placement = serializers.ChoiceField(choices=AdPlacement.choices)
    route_ids = serializers.ListField(child=serializers.CharField(max_length=80), max_length=3, required=False, default=list)


class AdPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdCreative
        fields = ("id", "advertiser", "title", "description", "image_url", "image_alt", "destination_url", "button_label", "context_label", "placement", "starts_at", "ends_at", "active")
        read_only_fields = fields


class AdSlotResponseSerializer(serializers.Serializer):
    ad = AdPublicSerializer(allow_null=True)
    token = serializers.CharField(allow_blank=True)
    exposure_id = serializers.UUIDField(allow_null=True)
    server_now = serializers.DateTimeField()
    valid_until = serializers.DateTimeField(allow_null=True)


class AdEventInputSerializer(serializers.Serializer):
    event_id = serializers.UUIDField()
    token = serializers.CharField(max_length=2048)
    kind = serializers.ChoiceField(choices=AdEvent.Kind.choices)


class AdEventResultSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    duplicate = serializers.BooleanField()


class AdAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdCreative
        fields = ("id", "advertiser", "title", "description", "image_url", "image_alt", "destination_url", "button_label", "context_label", "placement", "starts_at", "ends_at", "active", "is_test", "priority", "team_code", "place", "courses", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        def value(name):
            return attrs[name] if name in attrs else getattr(self.instance, name, None)

        if value("starts_at") and value("ends_at") and value("ends_at") <= value("starts_at"):
            raise serializers.ValidationError({"ends_at": "종료 일시는 시작 일시보다 뒤여야 합니다."})
        if value("placement") == AdPlacement.PARTNER and not value("place"):
            raise serializers.ValidationError({"place": "음식점·시설 광고에는 제휴 장소가 필요합니다."})
        return attrs
