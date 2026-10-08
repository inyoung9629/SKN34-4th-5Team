from drf_spectacular.utils import extend_schema_field
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from rest_framework import serializers

from . import models


validate_http_url = URLValidator(schemes=("http", "https"))


RESOURCE_FIELDS = {
    "teams": ("id", "team_code", "team_name_ko"),
    "stadiums": ("id", "stadium_code", "stadium_name_ko", "address", "longitude", "latitude", "geocode_source", "facility_manager", "game_operator", "phone_general", "phone_facility", "phone_ticket", "image_url", "image_source_url", "image_credit", "image_credit_url", "image_license_url", "collected_at"),
    "home-contexts": ("id", "season", "team_id", "stadium_id"),
    "postseason-stages": ("id", "stage_code", "stage_name", "start_date", "end_date", "matchup_description", "status_tag", "collected_at"),
    "games": ("id", "game_code", "home_team_id", "away_team_id", "stadium_id", "postseason_stage_id", "game_date", "game_time", "home_score", "away_score", "status_code", "game_type", "collected_at"),
    "standing-histories": ("id", "team_id", "snapshot_date", "rank", "wins", "losses", "draws", "games_behind", "collected_at"),
    "seat-zones": ("id", "home_context_id", "zone_code", "zone_name_ko", "level", "side", "seat_type", "group_size", "accessible"),
    "ticket-prices": ("id", "seat_zone_id", "price_tier", "day_type", "customer_type", "group_size", "price_krw", "valid_from", "valid_to", "discount_condition", "collected_at"),
    "ticket-policies": ("id", "policy_code", "team_id", "game_id", "policy_type", "subtype", "open_at", "max_tickets", "channel_no", "booking_channel", "channel_condition", "collected_at"),
    "seat-maps": ("id", "home_context_id", "map_title", "page_url"),
    "seat-map-assets": ("id", "seat_map_id", "asset_no", "asset_url", "asset_role", "source_url"),
    "seat-scopes": ("id", "home_context_id", "scope_code", "scope_name"),
    "seat-views": ("id", "seat_scope_id", "view_characteristic", "roof_coverage", "evidence_scope"),
    "food-stores": ("id", "record_code", "stadium_id", "store_facility", "location_qty", "collected_at"),
    "food-store-locations": ("id", "food_store_id", "location_no", "floor", "zone_location"),
    "food-store-menus": ("id", "food_store_id", "menu_category_official"),
    "transports": ("id", "stadium_id", "access_code", "mode", "title", "details", "parking_spaces", "reservation_required", "collected_at"),
    "stadium-contents": ("id", "record_code", "stadium_id", "content_type", "name", "floor", "location", "official_description", "operating_condition", "collected_at"),
    "facilities": ("id", "record_code", "stadium_id", "facility_type", "floor", "side", "nearby_section", "gate", "gender", "indoor_outdoor", "location_detail", "collected_at"),
}

RESOURCE_MODELS = dict(zip(RESOURCE_FIELDS, (
    models.Team, models.Stadium, models.HomeContext, models.PostseasonStage,
    models.Game, models.StandingHistory, models.SeatZone, models.TicketPrice,
    models.TicketPolicy, models.SeatMap, models.SeatMapAsset, models.SeatScope,
    models.SeatView, models.FoodStore, models.FoodStoreLocation,
    models.FoodStoreMenu, models.Transport, models.StadiumContent, models.Facility,
), strict=True))


class BaseballSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        data = {**({} if self.instance is None else {
            field.name: getattr(self.instance, field.name)
            for field in self.Meta.model._meta.fields
        }), **attrs}
        for name in ("home_score", "away_score", "wins", "losses", "draws", "rank", "group_size", "price_krw", "max_tickets", "channel_no", "asset_no", "location_no", "parking_spaces"):
            if data.get(name) is not None and data[name] < 0:
                raise serializers.ValidationError({name: "0 이상의 값을 입력해 주세요."})
        if data.get("start_date") and data.get("end_date") and data["start_date"] > data["end_date"]:
            raise serializers.ValidationError({"end_date": "시작일 이후여야 합니다."})
        if data.get("valid_from") and data.get("valid_to") and data["valid_from"] > data["valid_to"]:
            raise serializers.ValidationError({"valid_to": "시작일 이후여야 합니다."})
        if data.get("home_team") and data.get("away_team") and data["home_team"] == data["away_team"]:
            raise serializers.ValidationError({"away_team_id": "홈팀과 원정팀은 달라야 합니다."})
        for name in ("page_url", "asset_url"):
            if data.get(name):
                try:
                    validate_http_url(data[name])
                except DjangoValidationError as error:
                    raise serializers.ValidationError({name: "올바른 http 또는 https 주소를 입력해 주세요."}) from error
        return attrs

    def validate_id(self, value):
        if self.instance and value != self.instance.pk:
            raise serializers.ValidationError("ID는 변경할 수 없습니다.")
        if value < 1:
            raise serializers.ValidationError("1 이상의 ID를 입력해 주세요.")
        return value

    def validate_longitude(self, value):
        if not -180 <= value <= 180:
            raise serializers.ValidationError("경도 범위를 확인해 주세요.")
        return value

    def validate_latitude(self, value):
        if not -90 <= value <= 90:
            raise serializers.ValidationError("위도 범위를 확인해 주세요.")
        return value


def serializer_for(resource, model):
    attrs = {}
    for field in model._meta.fields:
        if field.is_relation:
            attrs[f"{field.name}_id"] = serializers.PrimaryKeyRelatedField(
                source=field.name, queryset=field.remote_field.model.objects.all(),
                required=not field.null, allow_null=field.null,
            )
    meta = type("Meta", (), {"model": model, "fields": RESOURCE_FIELDS[resource]})
    return type(f"{model.__name__}Serializer", (BaseballSerializer,), {**attrs, "Meta": meta})


RESOURCE_SERIALIZERS = {resource: serializer_for(resource, model) for resource, model in RESOURCE_MODELS.items()}


def detail_serializer_for(model, serializer):
    meta = type("Meta", (serializer.Meta,), {"fields": (*serializer.Meta.fields, "_etag")})
    return type(
        f"{model.__name__}DetailSerializer",
        (serializer,),
        {"_etag": serializers.CharField(read_only=True), "Meta": meta},
    )


RESOURCE_DETAIL_SERIALIZERS = {
    resource: detail_serializer_for(model, RESOURCE_SERIALIZERS[resource])
    for resource, model in RESOURCE_MODELS.items()
}


class BaseballErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
    references = serializers.DictField(child=serializers.IntegerField(), required=False)


class PublicTeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Team
        fields = ("id", "team_code", "team_name_ko")


class PublicHomeTeamSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="team.team_code", read_only=True)
    name = serializers.CharField(source="team.team_name_ko", read_only=True)

    class Meta:
        model = models.HomeContext
        fields = ("id", "team_id", "code", "name", "season")


class ParkingMapSerializer(serializers.Serializer):
    imageUrl = serializers.CharField()
    sourceUrl = serializers.URLField(required=False)
    credit = serializers.CharField(required=False)
    title = serializers.CharField(required=False)
    summary = serializers.CharField(required=False)
    kind = serializers.ChoiceField(choices=("entrance", "preferred-area", "nearby-alternatives", "access-gates"), required=False)
    visualNotes = serializers.ListField(child=serializers.CharField(), required=False)
    capturedAt = serializers.DateField(required=False)
    width = serializers.IntegerField(required=False)
    height = serializers.IntegerField(required=False)


class SeatingMapSerializer(serializers.Serializer):
    imageUrl = serializers.CharField()
    sourceUrl = serializers.URLField(required=False)
    title = serializers.CharField()
    home_context_id = serializers.IntegerField()
    season = serializers.IntegerField()
    team_code = serializers.CharField()


class PublicStadiumSerializer(serializers.ModelSerializer):
    home_teams = PublicHomeTeamSerializer(source="home_contexts", many=True, read_only=True)
    parkingMap = serializers.SerializerMethodField()
    seatingMap = serializers.SerializerMethodField()

    @extend_schema_field(ParkingMapSerializer(allow_null=True))
    def get_parkingMap(self, stadium):
        from .stadium_guides import parking_map
        return parking_map(stadium)

    @extend_schema_field(SeatingMapSerializer(allow_null=True))
    def get_seatingMap(self, stadium):
        from .stadium_guides import seating_map
        return seating_map(stadium)

    class Meta:
        model = models.Stadium
        fields = ("id", "stadium_code", "stadium_name_ko", "address", "longitude", "latitude", "geocode_source", "facility_manager", "game_operator", "phone_general", "phone_facility", "phone_ticket", "image_url", "image_source_url", "image_credit", "image_credit_url", "image_license_url", "collected_at", "home_teams", "parkingMap", "seatingMap")

class PublicTicketPriceSerializer(serializers.ModelSerializer):
    seat_zone_code = serializers.CharField(source="seat_zone.zone_code", read_only=True)
    seat_zone_name = serializers.CharField(source="seat_zone.zone_name_ko", read_only=True)

    class Meta:
        model = models.TicketPrice
        fields = (*RESOURCE_FIELDS["ticket-prices"], "seat_zone_code", "seat_zone_name")


class PublicFoodStoreSerializer(serializers.ModelSerializer):
    locations = RESOURCE_SERIALIZERS["food-store-locations"](many=True, read_only=True)
    menus = RESOURCE_SERIALIZERS["food-store-menus"](many=True, read_only=True)

    class Meta:
        model = models.FoodStore
        fields = (*RESOURCE_FIELDS["food-stores"], "locations", "menus")


class PublicSeatMapSerializer(serializers.ModelSerializer):
    assets = RESOURCE_SERIALIZERS["seat-map-assets"](many=True, read_only=True)

    class Meta:
        model = models.SeatMap
        fields = (*RESOURCE_FIELDS["seat-maps"], "assets")


class PublicSeatScopeSerializer(serializers.ModelSerializer):
    seat_views = RESOURCE_SERIALIZERS["seat-views"](many=True, read_only=True)

    class Meta:
        model = models.SeatScope
        fields = (*RESOURCE_FIELDS["seat-scopes"], "seat_views")
