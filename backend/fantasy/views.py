from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from accounts.models import PointTransaction, PointWallet
from baseball.models import Player
from fantasy.models import FantasySelection, FantasyWeek
from fantasy.serializers import PlayerPageSerializer, PlayerSerializer, SelectionCreateSerializer, SelectionSerializer, WeekSerializer
from fantasy.services.schedule import week_has_games
from fantasy.services.local_scheduler import run_local_jobs_if_needed
from fantasy.services.scoring import BATTER_STATS, PITCHER_STATS, player_fantasy_type, score_user_week
from fantasy.services.weeks import current_week, fantasy_today, next_week


def _confirmable(week):
    return fantasy_today() < week.week_start


class WeekView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(responses=WeekSerializer)
    def get(self, request, which):
        if which == "current":
            run_local_jobs_if_needed()
        return Response(WeekSerializer(current_week() if which == "current" else next_week()).data)


class PlayerListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    PAGE_SIZE = 8

    @extend_schema(responses=PlayerPageSerializer)
    def get(self, request):
        qs = Player.objects.select_related("team").order_by("name", "pk")
        if request.query_params.get("team"):
            qs = qs.filter(team__team_code=request.query_params["team"])
        if request.query_params.get("q"):
            qs = qs.filter(name__icontains=request.query_params["q"].strip())
        fantasy_type = request.query_params.get("fantasy_type", "").lower()
        if fantasy_type == "pitcher":
            qs = qs.filter(Q(positions__contains=["pitcher"]) | Q(positions__contains=["투수"]))
        elif fantasy_type == "batter":
            qs = qs.exclude(Q(positions__contains=["pitcher"]) | Q(positions__contains=["투수"]))
        page_values = request.query_params.getlist("page")
        if len(page_values) > 1:
            return Response({"detail": "page는 한 번만 지정할 수 있습니다."}, status=status.HTTP_400_BAD_REQUEST)
        page_value = page_values[0] if page_values else "1"
        if (
            not page_value.isascii()
            or not page_value.isdecimal()
            or len(page_value) > 10
            or int(page_value) < 1
        ):
            return Response({"detail": "page는 1 이상의 정수여야 합니다."}, status=status.HTTP_400_BAD_REQUEST)
        paginator = Paginator(qs, self.PAGE_SIZE)
        try:
            page = paginator.page(int(page_value))
        except (EmptyPage, PageNotAnInteger):
            return Response({"detail": "요청한 page 범위에 선수가 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "count": paginator.count,
            "next": page.next_page_number() if page.has_next() else None,
            "previous": page.previous_page_number() if page.has_previous() else None,
            "results": PlayerSerializer(page.object_list, many=True).data,
        })


class SelectionListCreateView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(responses=SelectionSerializer)
    def get(self, request):
        week_values = request.query_params.getlist("week")
        if len(week_values) > 1:
            return Response({"detail": "week은 한 번만 지정할 수 있습니다."}, status=status.HTTP_400_BAD_REQUEST)
        week_id = week_values[0] if week_values else None
        if week_id is not None and (
            not week_id.isascii()
            or not week_id.isdecimal()
            or len(week_id) > 19
            or int(week_id) < 1
            or int(week_id) > 9_223_372_036_854_775_807
        ):
            return Response({"detail": "week는 양의 정수여야 합니다."}, status=status.HTTP_400_BAD_REQUEST)
        qs = FantasySelection.objects.filter(user=request.user).select_related("player", "week")
        if week_id:
            qs = qs.filter(week_id=week_id)
        return Response(SelectionSerializer(qs, many=True).data)

    @transaction.atomic
    @extend_schema(request=SelectionCreateSerializer, responses=SelectionSerializer)
    def post(self, request):
        data = SelectionCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        player = data.validated_data["player"]
        fantasy_type = player_fantasy_type(player)
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        week = next_week()
        if not _confirmable(week):
            return Response({"detail": "선택 기간이 종료되었습니다."}, status=status.HTTP_400_BAD_REQUEST)
        if not week_has_games(week):
            return Response(
                {"detail": "해당 주차에는 예정된 경기가 없어 선수를 선택할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        selections = FantasySelection.objects.filter(
            week=week, user=request.user
        ).select_related("player")
        existing = next((selection for selection in selections if selection.player_id == player.pk), None)
        if existing:
            return Response(SelectionSerializer(existing).data, status=status.HTTP_200_OK)

        selections_to_replace = [
            selection.pk for selection in selections
            if selection.fantasy_type == fantasy_type
        ]
        if selections_to_replace:
            FantasySelection.objects.filter(pk__in=selections_to_replace).delete()

        stats = PITCHER_STATS if fantasy_type == "PITCHER" else BATTER_STATS
        selection = FantasySelection.objects.create(
            week=week,
            user=request.user,
            player=player,
            fantasy_type=fantasy_type,
            stat_weights={key: None for key in stats},
        )
        return Response(SelectionSerializer(selection).data, status=status.HTTP_201_CREATED)


class SelectionDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @transaction.atomic
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def delete(self, request, pk):
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        selection = FantasySelection.objects.select_for_update().filter(
            pk=pk, user=request.user
        ).select_related("week").first()
        if not selection:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not _confirmable(selection.week):
            return Response({"detail": "주차가 시작되어 수정할 수 없습니다."}, status=status.HTTP_400_BAD_REQUEST)
        selection.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentScoreView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        week = current_week()
        return Response({"week": WeekSerializer(week).data, "score": str(score_user_week(request.user, week))})


class PointWalletView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        wallet = PointWallet.objects.filter(user=request.user).first()
        transactions = PointTransaction.objects.filter(user=request.user).values(
            "amount", "transaction_type", "description", "created_at"
        )[:20]
        return Response({
            "balance": wallet.balance if wallet else 0,
            "transactions": list(transactions),
        })
