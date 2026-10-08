import hashlib
import json
from datetime import date

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.urls import path
from rest_framework import generics, status, viewsets
from rest_framework.exceptions import MethodNotAllowed, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view

from . import models
from .permissions import ActiveStaffOnly
from .serializers import BaseballErrorSerializer, PublicFoodStoreSerializer, PublicSeatMapSerializer, PublicSeatScopeSerializer, PublicStadiumSerializer, PublicTeamSerializer, PublicTicketPriceSerializer, RESOURCE_DETAIL_SERIALIZERS, RESOURCE_MODELS, RESOURCE_SERIALIZERS


class BaseballPages(PageNumberPagination):
    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 100


def instance_etag(serializer):
    body = json.dumps(serializer.data, sort_keys=True, ensure_ascii=False, default=str).encode()
    return '"' + hashlib.sha256(body).hexdigest() + '"'


def positive_int_param(params, name):
    value = params.get(name)
    if value is None:
        return None
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 2_147_483_647:
        raise ValidationError({name: "1 이상의 정수를 입력해 주세요."})
    return int(value)


def date_param(params, name):
    value = params.get(name)
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError as error:
        raise ValidationError({name: "YYYY-MM-DD 날짜를 입력해 주세요."}) from error


def safe_error(response, exc):
    if isinstance(response.data, dict) and "code" not in response.data:
        fields = {key: value if isinstance(value, list) else [value] for key, value in response.data.items() if key != "detail"}
        detail = response.data.get("detail")
        message = str(detail) if detail else str(next(iter(fields.values()))[0]) if fields else "요청을 처리하지 못했습니다."
        response.data = {"code": getattr(exc, "default_code", "error"), "message": message, "field_errors": fields}
    return response


class BaseballPublicMixin:
    def handle_exception(self, exc):
        return safe_error(super().handle_exception(exc), exc)


class BaseballManageViewSet(viewsets.ModelViewSet):
    permission_classes = (ActiveStaffOnly,)
    pagination_class = BaseballPages
    http_method_names = ("get", "post", "patch", "delete", "head", "options")
    resource = ""

    def handle_exception(self, exc):
        return safe_error(super().handle_exception(exc), exc)

    def get_queryset(self):
        queryset = RESOURCE_MODELS[self.resource].objects.order_by("pk")
        relations = [field.name for field in queryset.model._meta.fields if field.is_relation]
        if relations:
            queryset = queryset.select_related(*relations)
        query = self.request.query_params.get("q", "").strip()[:150]
        if query:
            condition = Q()
            for field in queryset.model._meta.fields:
                if field.get_internal_type() in {"TextField", "CharField"}:
                    condition |= Q(**{f"{field.name}__icontains": query})
            if query.isascii() and query.isdigit():
                condition |= Q(pk=int(query))
            queryset = queryset.filter(condition)
        return queryset

    def get_serializer_class(self):
        return RESOURCE_SERIALIZERS[self.resource]

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        response = Response(serializer.data)
        etag = instance_etag(serializer)
        response.data["_etag"] = etag
        response["ETag"] = etag
        return response

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                serializer.save()
        except IntegrityError:
            return Response({"code": "conflict", "message": "같은 ID 또는 고유 값이 이미 존재합니다.", "field_errors": {}}, status=status.HTTP_409_CONFLICT)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=self.get_success_headers(serializer.data))

    def update(self, request, *args, **kwargs):
        if request.method == "PUT":
            raise MethodNotAllowed("PUT")
        with transaction.atomic():
            try:
                instance = RESOURCE_MODELS[self.resource].objects.select_for_update().get(pk=kwargs["pk"])
            except RESOURCE_MODELS[self.resource].DoesNotExist as error:
                raise Http404 from error
            expected = request.headers.get("If-Match") or request.data.get("_etag")
            if not expected:
                raise ValidationError({"if_match": "수정 전에 최신 상세 정보를 불러와 주세요."})
            if expected != instance_etag(self.get_serializer(instance)):
                return Response({"code": "stale_write", "message": "다른 관리자가 먼저 변경했습니다.", "field_errors": {}}, status=status.HTTP_412_PRECONDITION_FAILED)
            data = {key: value for key, value in request.data.items() if key != "_etag"}
            serializer = self.get_serializer(instance, data=data, partial=True)
            serializer.is_valid(raise_exception=True)
            try:
                with transaction.atomic():
                    serializer.save()
            except IntegrityError:
                return Response({"code": "conflict", "message": "같은 고유 값이 이미 존재합니다.", "field_errors": {}}, status=status.HTTP_409_CONFLICT)
        response = Response(serializer.data)
        etag = instance_etag(serializer)
        response.data["_etag"] = etag
        response["ETag"] = etag
        return response

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            try:
                instance = RESOURCE_MODELS[self.resource].objects.select_for_update().get(pk=kwargs["pk"])
            except RESOURCE_MODELS[self.resource].DoesNotExist as error:
                raise Http404 from error
            expected = request.headers.get("If-Match") or request.data.get("_etag")
            if not expected:
                raise ValidationError({"if_match": "삭제 전에 최신 상세 정보를 불러와 주세요."})
            if expected != instance_etag(self.get_serializer(instance)):
                return Response({"code": "stale_write", "message": "다른 관리자가 먼저 변경했습니다.", "field_errors": {}}, status=status.HTTP_412_PRECONDITION_FAILED)
            try:
                instance.delete()
            except ProtectedError as error:
                protected = {}
                for item in error.protected_objects:
                    name = item._meta.verbose_name
                    protected[name] = protected.get(name, 0) + 1
                return Response({"code": "protected", "message": "참조 중인 데이터가 있어 삭제할 수 없습니다.", "field_errors": {}, "references": protected}, status=status.HTTP_409_CONFLICT)
        return Response(status=status.HTTP_204_NO_CONTENT)


def viewset_for(resource):
    serializer = RESOURCE_SERIALIZERS[resource]
    detail_serializer = RESOURCE_DETAIL_SERIALIZERS[resource]
    return extend_schema_view(
        list=extend_schema(responses={200: serializer}),
        retrieve=extend_schema(responses={200: detail_serializer, 404: BaseballErrorSerializer}),
        create=extend_schema(
            request=serializer,
            responses={201: serializer, 400: BaseballErrorSerializer, 409: BaseballErrorSerializer},
        ),
        partial_update=extend_schema(
            request=serializer,
            responses={
                200: detail_serializer,
                400: BaseballErrorSerializer,
                404: BaseballErrorSerializer,
                409: BaseballErrorSerializer,
                412: BaseballErrorSerializer,
            },
        ),
        destroy=extend_schema(
            responses={
                204: None,
                400: BaseballErrorSerializer,
                404: BaseballErrorSerializer,
                409: BaseballErrorSerializer,
                412: BaseballErrorSerializer,
            }
        ),
    )(type(f"{RESOURCE_MODELS[resource].__name__}ViewSet", (BaseballManageViewSet,), {"resource": resource}))


RESOURCE_VIEWSETS = {resource: viewset_for(resource) for resource in RESOURCE_MODELS}


class PublicTeamList(BaseballPublicMixin, generics.ListAPIView):
    permission_classes = (AllowAny,)
    serializer_class = PublicTeamSerializer
    queryset = models.Team.objects.order_by("team_code")
    pagination_class = None


class PublicStadiumList(BaseballPublicMixin, generics.ListAPIView):
    permission_classes = (AllowAny,)
    serializer_class = PublicStadiumSerializer
    pagination_class = BaseballPages

    def get_queryset(self):
        queryset = models.Stadium.objects.prefetch_related("home_contexts__team", "home_contexts__seat_maps__assets").order_by("stadium_code")
        query = self.request.query_params.get("q", "").strip()[:150]
        return queryset.filter(Q(stadium_name_ko__icontains=query) | Q(address__icontains=query) | Q(home_contexts__team__team_name_ko__icontains=query)).distinct() if query else queryset


class PublicStadiumDetail(BaseballPublicMixin, generics.RetrieveAPIView):
    permission_classes = (AllowAny,)
    serializer_class = PublicStadiumSerializer
    lookup_field = "stadium_code"
    lookup_url_kwarg = "code"
    queryset = models.Stadium.objects.prefetch_related("home_contexts__team", "home_contexts__seat_maps__assets")


class StadiumChildren(BaseballPublicMixin, generics.ListAPIView):
    permission_classes = (AllowAny,)
    pagination_class = BaseballPages
    relation = ""

    def get_serializer_class(self):
        if self.relation == "food-stores":
            return PublicFoodStoreSerializer
        if self.relation == "seat-maps":
            return PublicSeatMapSerializer
        if self.relation == "seat-scopes":
            return PublicSeatScopeSerializer
        if self.relation == "ticket-prices":
            return PublicTicketPriceSerializer
        return RESOURCE_SERIALIZERS[self.relation]

    def get_queryset(self):
        model = RESOURCE_MODELS[self.relation]
        if self.relation in {"transports", "food-stores", "stadium-contents", "facilities"}:
            key = "stadium__stadium_code"
        elif self.relation == "ticket-prices":
            key = "seat_zone__home_context__stadium__stadium_code"
        elif self.relation == "seat-views":
            key = "seat_scope__home_context__stadium__stadium_code"
        else:
            key = "home_context__stadium__stadium_code"
        queryset = model.objects.filter(**{key: self.kwargs["code"]}).order_by("pk")
        context_resources = {"seat-zones", "ticket-prices", "seat-maps", "seat-scopes", "seat-views"}
        for parameter in (() if self.relation not in context_resources else ("home_context", "team", "season")):
            value = positive_int_param(self.request.query_params, parameter)
            if not value:
                continue
            lookup = {"home_context": "home_context_id", "team": "home_context__team_id", "season": "home_context__season"}[parameter]
            if self.relation == "ticket-prices":
                lookup = f"seat_zone__{lookup}"
            elif self.relation == "seat-views":
                lookup = f"seat_scope__{lookup}"
            queryset = queryset.filter(**{lookup: value})
        relations = [field.name for field in model._meta.fields if field.is_relation]
        if self.relation == "food-stores":
            queryset = queryset.prefetch_related("locations", "menus")
        elif self.relation == "seat-maps":
            queryset = queryset.prefetch_related("assets")
        elif self.relation == "seat-scopes":
            queryset = queryset.prefetch_related("seat_views")
        return queryset.select_related(*relations) if relations else queryset


public_urlpatterns = [path("teams/", PublicTeamList.as_view()), path("stadiums/", PublicStadiumList.as_view()), path("stadiums/<str:code>/", PublicStadiumDetail.as_view())]
for suffix, resource in (("seat-zones", "seat-zones"), ("ticket-prices", "ticket-prices"), ("seat-maps", "seat-maps"), ("seat-scopes", "seat-scopes"), ("seat-views", "seat-views"), ("food-stores", "food-stores"), ("transports", "transports"), ("facilities", "facilities"), ("contents", "stadium-contents")):
    serializer = {
        "food-stores": PublicFoodStoreSerializer,
        "seat-maps": PublicSeatMapSerializer,
        "seat-scopes": PublicSeatScopeSerializer,
        "ticket-prices": PublicTicketPriceSerializer,
    }.get(resource, RESOURCE_SERIALIZERS[resource])
    view = extend_schema_view(list=extend_schema(responses={200: serializer}))(
        type(f"Public{RESOURCE_MODELS[resource].__name__}List", (StadiumChildren,), {"relation": resource})
    )
    public_urlpatterns.append(path(f"stadiums/<str:code>/{suffix}/", view.as_view()))


class PublicResourceList(BaseballPublicMixin, generics.ListAPIView):
    permission_classes = (AllowAny,)
    pagination_class = BaseballPages
    resource = ""

    def get_serializer_class(self):
        return PublicTicketPriceSerializer if self.resource == "ticket-prices" else RESOURCE_SERIALIZERS[self.resource]

    def get_queryset(self):
        model = RESOURCE_MODELS[self.resource]
        queryset = model.objects.order_by("pk")
        params = self.request.query_params
        if self.resource == "games":
            start, end = date_param(params, "date_from"), date_param(params, "date_to")
            if start and end:
                if end < start:
                    raise ValidationError({"date_to": "시작일 이후 날짜를 입력해 주세요."})
                if (end - start).days > 366:
                    raise ValidationError({"date_to": "조회 기간은 366일 이하여야 합니다."})
            if start:
                queryset = queryset.filter(game_date__gte=start)
            if end:
                queryset = queryset.filter(game_date__lte=end)
            team, stadium = positive_int_param(params, "team"), positive_int_param(params, "stadium")
            if team:
                queryset = queryset.filter(Q(home_team_id=team) | Q(away_team_id=team))
            if stadium:
                queryset = queryset.filter(stadium_id=stadium)
            if params.get("status"):
                queryset = queryset.filter(status_code=params["status"][:50])
        elif self.resource == "standing-histories":
            snapshot = date_param(params, "snapshot_date")
            if not snapshot:
                snapshot = queryset.values("snapshot_date").annotate(team_count=Count("team_id", distinct=True)).filter(team_count=models.Team.objects.count()).order_by("-snapshot_date").values_list("snapshot_date", flat=True).first()
            queryset = queryset.filter(snapshot_date=snapshot) if snapshot else queryset.none()
        elif self.resource == "postseason-stages":
            start, end = date_param(params, "date_from"), date_param(params, "date_to")
            if start and end and end < start:
                raise ValidationError({"date_to": "시작일 이후 날짜를 입력해 주세요."})
            if start:
                queryset = queryset.filter(end_date__gte=start)
            if end:
                queryset = queryset.filter(start_date__lte=end)
            if params.get("status"):
                queryset = queryset.filter(status_tag=params["status"][:50])
        elif self.resource == "ticket-prices":
            seat_zone, context = positive_int_param(params, "seat_zone"), positive_int_param(params, "home_context")
            if seat_zone:
                queryset = queryset.filter(seat_zone_id=seat_zone)
            if context:
                queryset = queryset.filter(seat_zone__home_context_id=context)
        elif self.resource == "ticket-policies":
            team, game = positive_int_param(params, "team"), positive_int_param(params, "game")
            if team:
                queryset = queryset.filter(team_id=team)
            if game:
                queryset = queryset.filter(game_id=game)
        relations = [field.name for field in model._meta.fields if field.is_relation]
        return queryset.select_related(*relations) if relations else queryset


for suffix, resource in (("games", "games"), ("standings", "standing-histories"), ("postseason-stages", "postseason-stages"), ("ticket-prices", "ticket-prices"), ("ticket-policies", "ticket-policies")):
    serializer = PublicTicketPriceSerializer if resource == "ticket-prices" else RESOURCE_SERIALIZERS[resource]
    view = extend_schema_view(list=extend_schema(responses={200: serializer}))(
        type(f"Public{RESOURCE_MODELS[resource].__name__}List", (PublicResourceList,), {"resource": resource})
    )
    public_urlpatterns.append(path(f"{suffix}/", view.as_view()))
