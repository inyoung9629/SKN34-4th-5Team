import math

from rest_framework import serializers

from .models import Place


class StrictCharField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, int):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictFloatField(serializers.FloatField):
    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            self.fail("invalid")
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            raise serializers.ValidationError("유한한 숫자를 입력해 주세요.")
        return value


class StrictURLField(serializers.URLField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class PlaceSearchSerializer(serializers.Serializer):
    method = StrictCharField()
    keyword = StrictCharField(required=False, max_length=100)
    category = StrictCharField(required=False)
    lat = StrictFloatField(min_value=-90, max_value=90)
    lng = StrictFloatField(min_value=-180, max_value=180)
    radius = StrictIntegerField(required=False, min_value=1, max_value=20000)
    page = StrictIntegerField(min_value=1, max_value=3)
    size = StrictIntegerField(min_value=1, max_value=15)
    sort = StrictCharField()

    def validate(self, attrs):
        categories = {"FD6", "CE7", "AT4", "CT1", "CS2", "AD5"}
        method = attrs["method"]
        category = attrs.get("category", "").strip()
        keyword = attrs.get("keyword", "").strip()
        if method not in {"keyword", "category"} or attrs["sort"] not in {"accuracy", "distance"}:
            raise serializers.ValidationError("장소 검색 조건을 확인해 주세요.")
        if method == "keyword" and not keyword:
            raise serializers.ValidationError({"keyword": "검색어를 입력해 주세요."})
        if method == "category" and category not in categories:
            raise serializers.ValidationError({"category": "지원하는 카테고리를 입력해 주세요."})
        if category and category not in categories:
            raise serializers.ValidationError({"category": "지원하는 카테고리를 입력해 주세요."})
        attrs["keyword"] = keyword
        attrs["category"] = category
        return attrs


class PlaceWriteSerializer(serializers.ModelSerializer):
    kakao_place_id = StrictCharField(required=False, allow_null=True, max_length=100)
    name = StrictCharField(max_length=255)
    address = StrictCharField(required=False, allow_blank=True, max_length=500)
    road_address = StrictCharField(required=False, allow_blank=True, max_length=500)
    category_group_code = StrictCharField(required=False, allow_blank=True, max_length=20)
    category_group_name = StrictCharField(required=False, allow_blank=True, max_length=100)
    category_name = StrictCharField(required=False, allow_blank=True, max_length=255)
    phone = StrictCharField(required=False, allow_blank=True, max_length=50)
    lat = StrictFloatField(min_value=-90, max_value=90)
    lng = StrictFloatField(min_value=-180, max_value=180)
    url = StrictURLField(required=False, allow_blank=True, max_length=500)

    class Meta:
        model = Place
        fields = (
            "kakao_place_id", "name", "address", "road_address", "category_group_code",
            "category_group_name", "category_name", "phone", "lat", "lng", "url",
        )


class PlacePatchSerializer(serializers.Serializer):
    name = StrictCharField(required=False, max_length=255)
    address = StrictCharField(required=False, allow_blank=True, max_length=500)
    road_address = StrictCharField(required=False, allow_blank=True, max_length=500)
    category_group_code = StrictCharField(required=False, allow_blank=True, max_length=20)
    category_group_name = StrictCharField(required=False, allow_blank=True, max_length=100)
    category_name = StrictCharField(required=False, allow_blank=True, max_length=255)
    phone = StrictCharField(required=False, allow_blank=True, max_length=50)
    lat = StrictFloatField(required=False, min_value=-90, max_value=90)
    lng = StrictFloatField(required=False, min_value=-180, max_value=180)
    url = StrictURLField(required=False, allow_blank=True, max_length=500)


class PlaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Place
        fields = (
            "id", "kakao_place_id", "name", "address", "road_address", "category_group_code",
            "category_group_name", "category_name", "phone", "lat", "lng", "url",
            "created_at", "updated_at", "last_synced_at",
        )


class KakaoPlaceSerializer(serializers.Serializer):
    id = serializers.CharField()
    place_name = serializers.CharField()
    place_url = serializers.URLField(required=False, allow_blank=True)
    road_address_name = serializers.CharField()
    address_name = serializers.CharField()
    category_group_name = serializers.CharField()
    category_group_code = serializers.CharField()
    category_name = serializers.CharField()
    phone = serializers.CharField()
    x = serializers.CharField()
    y = serializers.CharField()
    stadiumArea = serializers.DictField(
        child=serializers.CharField(allow_null=True), required=False, read_only=True,
        help_text="scope: internal(초록 내부) 또는 external. stadium: 해당 구장 코드 또는 null. 빨강 전용 구역은 반환하지 않습니다.",
    )


class PlaceSearchResponseSerializer(serializers.Serializer):
    places = KakaoPlaceSerializer(many=True)
    hasNextPage = serializers.BooleanField()
    totalCount = serializers.IntegerField(min_value=0, required=False, help_text="필터 적용 전 카카오 검색 결과 수")
    pageableCount = serializers.IntegerField(min_value=0, max_value=45, required=False, help_text="해당 검색에서 카카오가 반환할 수 있는 결과 수")
    syncedAt = serializers.DateTimeField(help_text="카카오 공급자 응답을 성공적으로 조회한 시각이며 모든 저장 행의 동기화 시각을 뜻하지 않습니다.")


class PlaceListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = PlaceSerializer(many=True)


class PlaceErrorSerializer(serializers.Serializer):
    error = serializers.CharField()
