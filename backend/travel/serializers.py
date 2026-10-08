import math
import re

from django.db import transaction
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from community.models import CommunityImage
from community.serializers import validate_content_doc

from .models import Course, CourseStop


class FiniteFloatField(serializers.FloatField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            raise serializers.ValidationError("유한한 숫자를 입력해 주세요.")
        return value


class CourseStopSerializer(serializers.ModelSerializer):
    placeId = serializers.CharField(source="place_id", required=False, allow_blank=True, allow_null=True)
    visitId = serializers.CharField(source="visit_id", required=False, allow_blank=True, allow_null=True)
    tourContentId = serializers.CharField(source="tour_content_id", required=False, allow_blank=True, allow_null=True)
    isMapPoint = serializers.BooleanField(source="is_map_point", required=False, allow_null=True)
    isDrawnPoint = serializers.BooleanField(source="is_drawn_point", required=False, allow_null=True)
    lat = FiniteFloatField(min_value=-90, max_value=90, allow_null=True)
    lng = FiniteFloatField(min_value=-180, max_value=180, allow_null=True)

    class Meta:
        model = CourseStop
        fields = ("position", "name", "lat", "lng", "category", "placeId", "visitId", "address", "tourContentId", "isMapPoint", "isDrawnPoint")

    def validate(self, attrs):
        place_id = attrs.get("place_id") or ""
        if place_id.startswith(("google-ui-kit:", "kakao-lodging:")):
            if not re.fullmatch(r"google-ui-kit:[A-Za-z0-9_-]{1,220}|kakao-lodging:(?:all|hotel|motel|inn):[0-9]{1,100}", place_id):
                raise serializers.ValidationError("숙소 장소 ID를 확인해 주세요.")
            if attrs.get("lat") is not None or attrs.get("lng") is not None:
                raise serializers.ValidationError("숙소는 좌표를 저장하지 않고 ID만 저장합니다.")
            # Both providers' lodging content is live-only, including drafts.
            return {"position": attrs["position"], "place_id": place_id,
                    "visit_id": attrs.get("visit_id"), "name": "선택한 숙소",
                    "category": "숙박", "lat": None, "lng": None}
        if attrs.get("lat") is None or attrs.get("lng") is None:
            raise serializers.ValidationError("일반 장소의 위도와 경도를 입력해 주세요.")
        return attrs

    def to_representation(self, instance):
        return {key: value for key, value in super().to_representation(instance).items() if value is not None or key in ("lat", "lng")}


class CourseSerializer(serializers.ModelSerializer):
    travelMode = serializers.ChoiceField(source="travel_mode", choices=("walk", "car", "transit"), required=False)
    legModes = serializers.DictField(source="leg_modes", child=serializers.ChoiceField(choices=("walk", "car", "transit")), required=False)
    sampleId = serializers.CharField(source="source_id", read_only=True)
    isSample = serializers.BooleanField(source="is_sample", read_only=True)
    routeNumber = serializers.CharField(source="route_number", read_only=True)
    content = serializers.CharField(required=False, allow_blank=True, max_length=12000)
    contentDoc = serializers.JSONField(source="content_doc", required=False, allow_null=True)
    contentFormat = serializers.ChoiceField(source="content_format", choices=("", "html"), required=False, allow_blank=True)
    startLat = FiniteFloatField(source="start_lat", min_value=-90, max_value=90, required=False, allow_null=True)
    startLng = FiniteFloatField(source="start_lng", min_value=-180, max_value=180, required=False, allow_null=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    stops = CourseStopSerializer(many=True, required=False)

    class Meta:
        model = Course
        fields = ("id", "sampleId", "routeNumber", "title", "stadium", "description", "content", "contentDoc", "contentFormat", "duration", "cover", "tags", "startLat", "startLng", "travelMode", "legModes", "author", "likes", "views", "isSample", "createdAt", "updatedAt", "stops")
        read_only_fields = ("id", "sampleId", "routeNumber", "description", "cover", "author", "likes", "views", "isSample", "createdAt", "updatedAt")

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("코스 이름을 입력해 주세요.")
        return value

    def validate_legModes(self, value):
        if len(value) > 144:
            raise serializers.ValidationError("구간 이동수단 설정이 너무 많아요.")
        for key in value:
            if not re.fullmatch(r"-?\d{1,3}\.\d{6},-?\d{1,3}\.\d{6}>-?\d{1,3}\.\d{6},-?\d{1,3}\.\d{6}", key):
                raise serializers.ValidationError("이동 구간 좌표를 확인해 주세요.")
            for point in key.split(">"):
                lat, lng = map(float, point.split(","))
                if not -90 <= lat <= 90 or not -180 <= lng <= 180:
                    raise serializers.ValidationError("이동 구간 좌표를 확인해 주세요.")
        return value

    def validate_tags(self, value):
        if not isinstance(value, list) or not all(isinstance(tag, str) for tag in value):
            raise serializers.ValidationError("태그는 문자열 목록이어야 합니다.")
        return value

    def validate_stops(self, value):
        if self.partial:
            serializer = CourseStopSerializer(data=self.initial_data["stops"], many=True)
            serializer.is_valid(raise_exception=True)
            return serializer.validated_data
        return value

    def validate(self, attrs):
        if "content_doc" in attrs and attrs["content_doc"] is not None:
            text, image_ids = validate_content_doc(attrs["content_doc"], self.context["request"].user,
                                                   course=True, max_chars=12000)
            if attrs.get("content", getattr(self.instance, "content", "")) != text:
                raise serializers.ValidationError({"contentDoc": "본문 내용과 서식이 일치하지 않아요."})
            self._image_ids = image_ids
        elif "content" in attrs and self.instance and self.instance.content_doc is not None:
            attrs["content_doc"] = None
        lat = attrs.get("start_lat", getattr(self.instance, "start_lat", None))
        lng = attrs.get("start_lng", getattr(self.instance, "start_lng", None))
        if (lat is None) != (lng is None):
            raise serializers.ValidationError("출발 좌표는 위도와 경도를 함께 입력해 주세요.")
        if "stops" in attrs:
            stops = attrs["stops"]
            if not 1 <= len(stops) <= 12:
                raise serializers.ValidationError({"stops": "방문 장소는 1개 이상 12개 이하이어야 합니다."})
            positions = [stop["position"] for stop in stops]
            if sorted(positions) != list(range(len(stops))):
                raise serializers.ValidationError({"stops": "position은 0부터 중복 없이 이어져야 합니다."})
        elif self.instance is None:
            raise serializers.ValidationError({"stops": "방문 장소를 입력해 주세요."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        stops = validated_data.pop("stops")
        course = Course.objects.create(**validated_data)
        CourseStop.objects.bulk_create(CourseStop(course=course, **stop) for stop in stops)
        if getattr(self, "_image_ids", None):
            CommunityImage.objects.filter(id__in=self._image_ids, owner=self.context["request"].user).update(course=course)
        return course

    @transaction.atomic
    def update(self, instance, validated_data):
        stops = validated_data.pop("stops", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if stops is not None:
            instance.stops.all().delete()
            CourseStop.objects.bulk_create(CourseStop(course=instance, **stop) for stop in stops)
        if hasattr(self, "_image_ids"):
            CommunityImage.objects.filter(course=instance).exclude(id__in=self._image_ids).update(course=None)
            if self._image_ids:
                CommunityImage.objects.filter(id__in=self._image_ids, owner=self.context["request"].user).update(course=instance)
        elif validated_data.get("content_doc", "not-updated") is None:
            CommunityImage.objects.filter(course=instance).update(course=None)
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data["sampleId"] is None:
            data.pop("sampleId")
        if not data["contentFormat"]:
            data.pop("contentFormat")
        if data["startLat"] is None:
            data.pop("startLat")
            data.pop("startLng")
        return data


@extend_schema_serializer(component_name="CourseStopWrite")
class CourseStopWriteSerializer(serializers.Serializer):
    position = serializers.IntegerField(min_value=0)
    name = serializers.CharField(max_length=255)
    lat = FiniteFloatField(min_value=-90, max_value=90, allow_null=True)
    lng = FiniteFloatField(min_value=-180, max_value=180, allow_null=True)
    category = serializers.CharField(max_length=120)
    placeId = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    visitId = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    address = serializers.CharField(max_length=500, required=False, allow_blank=True, allow_null=True)
    tourContentId = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    isMapPoint = serializers.BooleanField(required=False, allow_null=True)
    isDrawnPoint = serializers.BooleanField(required=False, allow_null=True)


@extend_schema_serializer(component_name="CourseStop")
class CourseStopResponseSerializer(CourseStopWriteSerializer):
    placeId = serializers.CharField(max_length=255, required=False)
    visitId = serializers.CharField(max_length=255, required=False)
    address = serializers.CharField(max_length=500, required=False)
    tourContentId = serializers.CharField(max_length=255, required=False)
    isMapPoint = serializers.BooleanField(required=False)
    isDrawnPoint = serializers.BooleanField(required=False)


@extend_schema_serializer(component_name="CourseCreateRequest")
class CourseCreateRequestSerializer(serializers.Serializer):
    travelMode = serializers.ChoiceField(choices=("walk", "car", "transit"), required=False)
    legModes = serializers.DictField(child=serializers.ChoiceField(choices=("walk", "car", "transit")), required=False)
    title = serializers.CharField(max_length=80)
    stadium = serializers.CharField(max_length=120)
    content = serializers.CharField(max_length=12000, required=False, allow_blank=True)
    contentDoc = serializers.JSONField(required=False, allow_null=True)
    contentFormat = serializers.ChoiceField(choices=("", "html"), required=False, allow_blank=True)
    duration = serializers.CharField(max_length=80)
    tags = serializers.ListField(child=serializers.CharField())
    startLat = FiniteFloatField(min_value=-90, max_value=90, required=False, allow_null=True)
    startLng = FiniteFloatField(min_value=-180, max_value=180, required=False, allow_null=True)
    stops = CourseStopWriteSerializer(many=True)


@extend_schema_serializer(component_name="CoursePatchRequest")
class CoursePatchRequestSerializer(CourseCreateRequestSerializer):
    title = serializers.CharField(max_length=80, required=False)
    stadium = serializers.CharField(max_length=120, required=False)
    duration = serializers.CharField(max_length=80, required=False)
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    stops = CourseStopWriteSerializer(many=True, required=False)


@extend_schema_serializer(component_name="Course")
class CourseResponseSerializer(serializers.Serializer):
    travelMode = serializers.ChoiceField(choices=("walk", "car", "transit"), required=False)
    legModes = serializers.DictField(child=serializers.ChoiceField(choices=("walk", "car", "transit")), required=False)
    id = serializers.UUIDField()
    sampleId = serializers.CharField(required=False)
    routeNumber = serializers.RegexField(r"^\d{6}$")
    title = serializers.CharField()
    stadium = serializers.CharField()
    description = serializers.CharField()
    content = serializers.CharField()
    contentDoc = serializers.JSONField(required=False, allow_null=True)
    contentFormat = serializers.ChoiceField(choices=("html",), required=False)
    duration = serializers.CharField()
    cover = serializers.CharField()
    tags = serializers.ListField(child=serializers.CharField())
    startLat = serializers.FloatField(required=False)
    startLng = serializers.FloatField(required=False)
    author = serializers.CharField()
    likes = serializers.IntegerField(min_value=0)
    views = serializers.IntegerField(min_value=0)
    isSample = serializers.BooleanField()
    createdAt = serializers.DateTimeField()
    updatedAt = serializers.DateTimeField()
    stops = CourseStopResponseSerializer(many=True)


@extend_schema_serializer(component_name="CourseCreateResult")
class CourseCreateResultSerializer(CourseResponseSerializer):
    editToken = serializers.CharField()


@extend_schema_serializer(component_name="CourseReaction")
class CourseReactionSerializer(serializers.Serializer):
    liked = serializers.BooleanField()
    likes = serializers.IntegerField(min_value=0)


@extend_schema_serializer(component_name="CourseReactionRequest")
class CourseReactionRequestSerializer(serializers.Serializer):
    liked = serializers.BooleanField()


@extend_schema_serializer(component_name="CourseViewResult")
class CourseViewResultSerializer(serializers.Serializer):
    views = serializers.IntegerField(min_value=0)


class StrictObjectSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("객체를 입력해 주세요.")
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({name: "지원하지 않는 필드입니다." for name in sorted(unknown)})
        return super().to_internal_value(data)


class StrictFiniteFloatField(FiniteFloatField):
    def to_internal_value(self, data):
        if not isinstance(data, (int, float)) or isinstance(data, bool):
            raise serializers.ValidationError("숫자를 입력해 주세요.")
        return super().to_internal_value(data)


class TravelPointSerializer(StrictObjectSerializer):
    lat = StrictFiniteFloatField(min_value=-90, max_value=90)
    lng = StrictFiniteFloatField(min_value=-180, max_value=180)


class DirectionsRequestSerializer(StrictObjectSerializer):
    action = serializers.ChoiceField(choices=("directions",), required=False)
    mode = serializers.ChoiceField(choices=("walk", "car", "transit"))
    points = serializers.ListField(child=TravelPointSerializer(), min_length=2, max_length=13)


class DirectionsLegSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("ok", "error"))
    distance = serializers.IntegerField(allow_null=True)
    seconds = serializers.IntegerField(allow_null=True)
    paths = serializers.ListField(child=serializers.ListField(child=TravelPointSerializer()))
    instructions = serializers.ListField(child=serializers.CharField())
    stale = serializers.BooleanField(required=False)
    warning = serializers.CharField(required=False, allow_null=True)
    error = serializers.CharField(required=False)


class DirectionsResponseSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=("walk", "car", "transit"))
    legs = DirectionsLegSerializer(many=True)
    distance = serializers.IntegerField(allow_null=True)
    seconds = serializers.IntegerField(allow_null=True)


class DirectionsErrorSerializer(serializers.Serializer):
    error = serializers.CharField()
