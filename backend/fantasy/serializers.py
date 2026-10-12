from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from baseball.models import Player
from fantasy.models import FantasySelection, FantasyWeek
from fantasy.services.schedule import week_has_games
from fantasy.services.scoring import player_fantasy_type


class WeekSerializer(serializers.ModelSerializer):
    has_games = serializers.SerializerMethodField()

    @extend_schema_field(serializers.BooleanField)
    def get_has_games(self, obj):
        return week_has_games(obj)

    class Meta:
        model = FantasyWeek
        fields = ("id", "week_start", "week_end", "settlement_date", "status", "has_games")


class PlayerSerializer(serializers.ModelSerializer):
    team = serializers.CharField(source="team.team_code", read_only=True)
    team_name = serializers.CharField(source="team.team_name_ko", read_only=True)
    fantasy_type = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField)
    def get_fantasy_type(self, obj):
        return player_fantasy_type(obj)

    class Meta:
        model = Player
        fields = ("external_code", "name", "team", "team_name", "positions", "fantasy_type", "image_url")


class SelectionSerializer(serializers.ModelSerializer):
    player = PlayerSerializer(read_only=True)
    fantasy_type = serializers.CharField(read_only=True)
    weights = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DictField)
    def get_weights(self, obj):
        return obj.stat_weights if obj.is_confirmed else {key: None for key in obj.stat_weights}

    class Meta:
        model = FantasySelection
        fields = ("id", "week", "player", "fantasy_type", "is_confirmed", "weights", "selected_at")


class PlayerPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.IntegerField(allow_null=True)
    previous = serializers.IntegerField(allow_null=True)
    results = PlayerSerializer(many=True)


class SelectionCreateSerializer(serializers.Serializer):
    player = serializers.PrimaryKeyRelatedField(queryset=Player.objects.all())
