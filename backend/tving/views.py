import os
from datetime import datetime
from zoneinfo import ZoneInfo

from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

from baseball.models import Player, ProviderSnapshot
from .serializers import (
    AthleteResponseSerializer, DailyResponseSerializer, ErrorResponseSerializer,
    EntityPageResponseSerializer, MonthResponseSerializer, PlayerEntityResponseSerializer,
    PlayerMutationSerializer, SnapshotMutationSerializer, SnapshotPageResponseSerializer,
    SnapshotResponseSerializer, StatusResponseSerializer, TeamResponseSerializer,
)
from .service import (
    TvingAuthorizationError, TvingError, TvingInputError, create_snapshot,
    create_player, delete_player, delete_snapshot, details_status, player_entity,
    refresh_athlete, refresh_daily, refresh_month, refresh_team, search_entities,
    search_snapshots, update_player, update_snapshot,
)


def _run_local_crawler_if_needed():
    # 로컬 프로필에서만 화면 요청 기반의 일일 크롤링을 허용한다.
    profiles = {
        profile.strip()
        for profile in os.getenv("COMPOSE_PROFILES", "").split(",")
        if profile.strip()
    }
    if "local" not in profiles:
        return

    from crawling.local_scheduler import run_if_needed_today

    run_if_needed_today()


def _error(error):
    status = 400 if isinstance(error, TvingInputError) else 403 if isinstance(error, TvingAuthorizationError) else 503
    return Response({"data": None, "error": "요청 값을 확인해 주세요." if status == 400 else "TVING 정보를 불러오지 못했습니다.", "code": error.code}, status=status, headers={"Cache-Control": "no-store"})


def _unavailable():
    return Response({"data": None, "error": "TVING 정보를 불러오지 못했습니다.", "code": "service_unavailable"}, status=503, headers={"Cache-Control": "no-store"})


def _snapshot_data(snapshot):
    return {
        "id": snapshot.pk, "resourceKind": snapshot.resource_kind, "resourceKey": snapshot.resource_key,
        "payload": snapshot.payload, "sourceFetchedAt": snapshot.source_fetched_at,
        "lastSyncedAt": snapshot.last_synced_at, "createdAt": snapshot.created_at, "updatedAt": snapshot.updated_at,
    }


class RefreshView(APIView):
    permission_classes = [AllowAny]
    refresh = None
    argument = None

    def get(self, request, value=None):
        argument = value or request.query_params.get(self.argument)
        if self.argument == "date" and not argument:
            argument = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        if self.argument == "month" and not argument:
            today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
            argument = today[:7] if today.startswith("2026-") else "2026-12"
        try:
            # 일정·순위 데이터 요청 전에 로컬 크롤러를 하루 한 번만 실행한다.
            if self.argument in {"date", "month"}:
                _run_local_crawler_if_needed()
            return Response({"data": self.refresh(argument), "error": None}, headers={"Cache-Control": "no-store"})
        except TvingError as error:
            return _error(error)
        except Exception:
            return _unavailable()


class DailyView(RefreshView):
    refresh, argument = staticmethod(refresh_daily), "date"

    @extend_schema(parameters=[OpenApiParameter("date", OpenApiTypes.DATE, required=False)], responses={200: DailyResponseSerializer, 400: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def get(self, request, value=None):
        return super().get(request, value)


class MonthView(RefreshView):
    refresh, argument = staticmethod(refresh_month), "month"

    @extend_schema(parameters=[OpenApiParameter("month", str, required=False)], responses={200: MonthResponseSerializer, 400: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def get(self, request, value=None):
        return super().get(request, value)


class TeamView(RefreshView):
    refresh = staticmethod(refresh_team)

    @extend_schema(responses={200: TeamResponseSerializer, 400: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def get(self, request, value=None):
        return super().get(request, value)


class AthleteView(RefreshView):
    refresh = staticmethod(refresh_athlete)

    @extend_schema(responses={200: AthleteResponseSerializer, 400: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def get(self, request, value=None):
        return super().get(request, value)


class DetailsStatusView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses={200: StatusResponseSerializer, 503: ErrorResponseSerializer})
    def get(self, request):
        try:
            return Response({"data": details_status(), "error": None}, headers={"Cache-Control": "no-store"})
        except Exception:
            return _unavailable()


class SnapshotListView(APIView):
    def get_permissions(self):
        return [AllowAny()] if self.request.method == "GET" else [IsAdminUser()]

    @extend_schema(operation_id="tving_snapshots_list", parameters=[
        OpenApiParameter("kind", str), OpenApiParameter("key", str), OpenApiParameter("team", str),
        OpenApiParameter("player", str), OpenApiParameter("date", OpenApiTypes.DATE), OpenApiParameter("month", str),
        OpenApiParameter("page", int), OpenApiParameter("page_size", int),
    ], responses={200: SnapshotPageResponseSerializer, 400: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def get(self, request):
        try:
            page, page_size = int(request.query_params.get("page", 1)), int(request.query_params.get("page_size", 50))
            snapshots, count = search_snapshots(kind=request.query_params.get("kind"), key=request.query_params.get("key"), team=request.query_params.get("team"), player=request.query_params.get("player"), date=request.query_params.get("date"), month=request.query_params.get("month"), page=page, page_size=page_size)
            return Response({"data": {"count": count, "page": page, "pageSize": page_size, "results": [_snapshot_data(item) for item in snapshots]}, "error": None})
        except (ValueError, TvingError) as error:
            return _error(error if isinstance(error, TvingError) else TvingInputError("페이지 값이 올바르지 않습니다."))
        except Exception:
            return _unavailable()

    @extend_schema(request=SnapshotMutationSerializer, responses={201: SnapshotResponseSerializer, 400: ErrorResponseSerializer, 401: ErrorResponseSerializer, 403: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def post(self, request):
        serializer = SnapshotMutationSerializer(data=request.data)
        if not serializer.is_valid():
            return _error(TvingInputError("원천 조회 시각이 올바르지 않습니다."))
        try:
            values = serializer.validated_data
            snapshot = create_snapshot(kind=values.get("resourceKind"), key=values.get("resourceKey"), payload=values["payload"], source_fetched_at=values["sourceFetchedAt"], actor=request.user)
            return Response({"data": _snapshot_data(snapshot), "error": None}, status=201)
        except TvingError as error:
            return _error(error)
        except Exception:
            return _unavailable()


class SnapshotDetailView(APIView):
    def get_permissions(self):
        return [AllowAny()] if self.request.method == "GET" else [IsAdminUser()]

    def _get(self, pk):
        return ProviderSnapshot.objects.filter(pk=pk).first()

    @extend_schema(operation_id="tving_snapshots_retrieve", responses={200: SnapshotResponseSerializer, 404: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def get(self, request, pk):
        try:
            snapshot = self._get(pk)
            if not snapshot:
                return Response({"data": None, "error": "스냅샷을 찾지 못했습니다."}, status=404)
            return Response({"data": _snapshot_data(snapshot), "error": None})
        except Exception:
            return _unavailable()

    @extend_schema(request=SnapshotMutationSerializer, responses={200: SnapshotResponseSerializer, 400: ErrorResponseSerializer, 401: ErrorResponseSerializer, 403: ErrorResponseSerializer, 404: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def patch(self, request, pk):
        serializer = SnapshotMutationSerializer(data=request.data)
        if not serializer.is_valid():
            return _error(TvingInputError("원천 조회 시각이 올바르지 않습니다."))
        try:
            snapshot = self._get(pk)
            if not snapshot:
                return Response({"data": None, "error": "스냅샷을 찾지 못했습니다."}, status=404)
            values = serializer.validated_data
            return Response({"data": _snapshot_data(update_snapshot(snapshot, payload=values["payload"], source_fetched_at=values["sourceFetchedAt"], actor=request.user)), "error": None})
        except TvingError as error:
            return _error(error)
        except Exception:
            return _unavailable()

    @extend_schema(responses={204: None, 401: ErrorResponseSerializer, 403: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def delete(self, request, pk):
        try:
            snapshot = self._get(pk)
            if not snapshot:
                return Response(status=204)
            delete_snapshot(snapshot, actor=request.user)
            return Response(status=204)
        except TvingError as error:
            return _error(error)
        except Exception:
            return _unavailable()


class EntityListView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(parameters=[OpenApiParameter("kind", str, required=True), OpenApiParameter("team", str), OpenApiParameter("player", str), OpenApiParameter("date", OpenApiTypes.DATE), OpenApiParameter("month", str), OpenApiParameter("page", int), OpenApiParameter("page_size", int)], responses={200: EntityPageResponseSerializer, 400: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def get(self, request):
        try:
            rows, count = search_entities(kind=request.query_params.get("kind"), team=request.query_params.get("team"), player=request.query_params.get("player"), date=request.query_params.get("date"), month=request.query_params.get("month"), page=int(request.query_params.get("page", 1)), page_size=int(request.query_params.get("page_size", 50)))
            return Response({"data": {"count": count, "page": int(request.query_params.get("page", 1)), "pageSize": int(request.query_params.get("page_size", 50)), "results": rows}, "error": None})
        except (ValueError, TvingError) as error:
            return _error(error if isinstance(error, TvingError) else TvingInputError("페이지 값이 올바르지 않습니다."))
        except Exception:
            return _unavailable()


class PlayerEntityListView(APIView):
    permission_classes = [IsAdminUser]

    @extend_schema(request=PlayerMutationSerializer, responses={201: PlayerEntityResponseSerializer, 400: ErrorResponseSerializer, 401: ErrorResponseSerializer, 403: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def post(self, request):
        serializer = PlayerMutationSerializer(data=request.data)
        if not serializer.is_valid() or "externalCode" not in serializer.validated_data:
            return _error(TvingInputError("선수 입력이 올바르지 않습니다."))
        try:
            values = serializer.validated_data
            player = create_player(external_code=values["externalCode"], team_code=values["teamCode"], name=values["name"], actor=request.user)
            return Response({"data": player_entity(player), "error": None}, status=201)
        except TvingError as error:
            return _error(error)
        except Exception:
            return _unavailable()


class PlayerEntityDetailView(APIView):
    def get_permissions(self):
        return [AllowAny()] if self.request.method == "GET" else [IsAdminUser()]

    def _get(self, code):
        return Player.objects.filter(pk=code).select_related("team").first()

    @extend_schema(responses={200: PlayerEntityResponseSerializer, 404: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def get(self, request, code):
        try:
            player = self._get(code)
            return Response({"data": player_entity(player), "error": None}) if player else Response({"data": None, "error": "선수를 찾지 못했습니다."}, status=404)
        except Exception:
            return _unavailable()

    @extend_schema(request=PlayerMutationSerializer, responses={200: PlayerEntityResponseSerializer, 400: ErrorResponseSerializer, 401: ErrorResponseSerializer, 403: ErrorResponseSerializer, 404: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def patch(self, request, code):
        serializer = PlayerMutationSerializer(data=request.data)
        if not serializer.is_valid():
            return _error(TvingInputError("선수 입력이 올바르지 않습니다."))
        try:
            player = self._get(code)
            if not player: return Response({"data": None, "error": "선수를 찾지 못했습니다."}, status=404)
            values = serializer.validated_data
            return Response({"data": player_entity(update_player(player, team_code=values["teamCode"], name=values["name"], actor=request.user)), "error": None})
        except TvingError as error:
            return _error(error)
        except Exception:
            return _unavailable()

    @extend_schema(responses={204: None, 401: ErrorResponseSerializer, 403: ErrorResponseSerializer, 503: ErrorResponseSerializer})
    def delete(self, request, code):
        try:
            player = self._get(code)
            if player: delete_player(player, actor=request.user)
            return Response(status=204)
        except TvingError as error:
            return _error(error)
        except Exception:
            return _unavailable()
