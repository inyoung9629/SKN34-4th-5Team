from django.db import DatabaseError
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from baseball.permissions import ActiveStaffOnly

from .place_service import (
    PlaceAuthorizationError,
    PlaceConfigurationError,
    PlaceConflictError,
    PlaceNotFoundError,
    PlaceRateLimitError,
    PlaceUpstreamError,
    PlaceValidationError,
    create_place,
    delete_place,
    get_place,
    list_places,
    search_and_sync_places,
    search_live_lodging,
    search_live_places,
    update_place,
)
from .place_serializers import PlaceErrorSerializer, PlaceListResponseSerializer, PlacePatchSerializer, PlaceSearchResponseSerializer, PlaceSearchSerializer, PlaceSerializer, PlaceWriteSerializer


class PlacePayloadTooLarge(APIException):
    status_code = 413


class PlaceConflict(APIException):
    status_code = 409


class PlaceUpstreamUnavailable(APIException):
    status_code = 502


class PlaceRateLimited(APIException):
    status_code = 429


class PlaceConfigurationUnavailable(APIException):
    status_code = 503


def _service_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except PlaceValidationError as error:
        raise ValidationError(error.message) from error
    except PlaceAuthorizationError as error:
        raise PermissionDenied(error.message) from error
    except PlaceNotFoundError as error:
        raise NotFound(error.message) from error
    except PlaceConflictError as error:
        raise PlaceConflict(error.message) from error
    except PlaceRateLimitError as error:
        raise PlaceRateLimited(error.message) from error
    except PlaceUpstreamError as error:
        raise PlaceUpstreamUnavailable(error.message) from error
    except PlaceConfigurationError as error:
        raise PlaceConfigurationUnavailable(error.message) from error


class PlaceApiMixin:
    parser_classes = (JSONParser,)

    def initial(self, request, *args, **kwargs):
        if request.method in {"POST", "PATCH"}:
            length = request.META.get("CONTENT_LENGTH", "")
            if (length and not length.isdecimal()) or int(length or 0) > 12000 or len(request.body) > 12000:
                raise PlacePayloadTooLarge
        return super().initial(request, *args, **kwargs)

    def handle_exception(self, exc):
        if isinstance(exc, DatabaseError):
            return Response({"error": "장소 저장소를 사용할 수 없습니다."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response = super().handle_exception(exc)
        messages = {
            400: "입력값을 확인해 주세요.",
            401: "로그인이 필요합니다.",
            403: "관리자 권한이 필요합니다.",
            404: "장소를 찾을 수 없습니다.",
            409: "같은 카카오 장소 ID가 이미 존재합니다.",
            413: "요청 내용이 너무 큽니다.",
            415: "JSON 요청만 사용할 수 있습니다.",
            429: "장소 검색 요청이 많아요. 잠시 후 다시 시도해 주세요.",
            502: "일부 장소를 불러오지 못했어요.",
            503: "지도 데이터 연결 설정이 필요해요. 서버의 카카오 REST API 키를 확인해 주세요.",
        }
        response.data = {"error": messages.get(response.status_code, "요청을 처리하지 못했습니다.")}
        return response


class PlaceSearchThrottle(SimpleRateThrottle):
    scope = "place_search"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class PlaceSearchView(PlaceApiMixin, APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (PlaceSearchThrottle,)

    @extend_schema(operation_id="places_search", request=PlaceSearchSerializer, responses={200: PlaceSearchResponseSerializer, 400: PlaceErrorSerializer, 413: PlaceErrorSerializer, 415: PlaceErrorSerializer, 429: PlaceErrorSerializer, 502: PlaceErrorSerializer, 503: PlaceErrorSerializer}, auth=[])
    def post(self, request):
        return Response(_service_call(search_and_sync_places, request.data))


class PlaceLiveSearchView(PlaceApiMixin, APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (PlaceSearchThrottle,)

    @extend_schema(operation_id="places_live_search", request=PlaceSearchSerializer, responses={200: PlaceSearchResponseSerializer, 400: PlaceErrorSerializer, 413: PlaceErrorSerializer, 415: PlaceErrorSerializer, 429: PlaceErrorSerializer, 502: PlaceErrorSerializer, 503: PlaceErrorSerializer}, auth=[], description="카카오 주변 장소를 실시간 조회합니다. 응답을 장소 DB에 저장하지 않습니다.")
    def post(self, request):
        response = Response(_service_call(search_live_places, request.data))
        response["Cache-Control"] = "no-store"
        return response


class LodgingSearchView(PlaceApiMixin, APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (PlaceSearchThrottle,)

    @extend_schema(operation_id="lodging_search", request=PlaceSearchSerializer, responses={200: PlaceSearchResponseSerializer, 400: PlaceErrorSerializer, 413: PlaceErrorSerializer, 415: PlaceErrorSerializer, 429: PlaceErrorSerializer, 502: PlaceErrorSerializer, 503: PlaceErrorSerializer}, auth=[], description="카카오 AD5 숙박만 실시간 검색합니다. 응답을 장소 DB에 저장하지 않습니다.")
    def post(self, request):
        response = Response(_service_call(search_live_lodging, request.data))
        response["Cache-Control"] = "no-store"
        return response


class PlaceListCreateView(PlaceApiMixin, APIView):
    def get_permissions(self):
        return [AllowAny()] if self.request.method == "GET" else [ActiveStaffOnly()]

    @extend_schema(
        operation_id="places_list",
        parameters=[
            OpenApiParameter("name", OpenApiTypes.STR), OpenApiParameter("category", OpenApiTypes.STR),
            OpenApiParameter("kakao_place_id", OpenApiTypes.STR), OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses={200: PlaceListResponseSerializer, 400: PlaceErrorSerializer, 503: PlaceErrorSerializer}, auth=[],
    )
    def get(self, request):
        allowed = {"name", "category", "kakao_place_id", "page", "page_size"}
        if set(request.query_params) - allowed:
            raise ValidationError
        try:
            page = int(request.query_params.get("page", "1"))
            page_size = int(request.query_params.get("page_size", "20"))
        except ValueError as error:
            raise ValidationError from error
        filters = {key: request.query_params[key] for key in ("name", "category", "kakao_place_id") if key in request.query_params}
        return Response(_service_call(list_places, filters, page=page, page_size=page_size))

    @extend_schema(operation_id="places_create", request=PlaceWriteSerializer, responses={201: PlaceSerializer, 400: PlaceErrorSerializer, 401: PlaceErrorSerializer, 403: PlaceErrorSerializer, 409: PlaceErrorSerializer, 413: PlaceErrorSerializer, 415: PlaceErrorSerializer, 503: PlaceErrorSerializer})
    def post(self, request):
        data = _service_call(create_place, request.data, actor=request.user)
        return Response(data, status=status.HTTP_201_CREATED)


class PlaceDetailView(PlaceApiMixin, APIView):
    def get_permissions(self):
        return [AllowAny()] if self.request.method == "GET" else [ActiveStaffOnly()]

    @extend_schema(operation_id="places_retrieve", responses={200: PlaceSerializer, 400: PlaceErrorSerializer, 404: PlaceErrorSerializer, 503: PlaceErrorSerializer}, auth=[])
    def get(self, request, pk):
        return Response(_service_call(get_place, pk))

    @extend_schema(operation_id="places_partial_update", request=PlacePatchSerializer, responses={200: PlaceSerializer, 400: PlaceErrorSerializer, 401: PlaceErrorSerializer, 403: PlaceErrorSerializer, 404: PlaceErrorSerializer, 409: PlaceErrorSerializer, 413: PlaceErrorSerializer, 415: PlaceErrorSerializer, 503: PlaceErrorSerializer})
    def patch(self, request, pk):
        return Response(_service_call(update_place, pk, request.data, actor=request.user))

    @extend_schema(operation_id="places_destroy", responses={204: OpenApiResponse(description="본문 없음"), 401: PlaceErrorSerializer, 403: PlaceErrorSerializer, 404: PlaceErrorSerializer, 503: PlaceErrorSerializer})
    def delete(self, request, pk):
        _service_call(delete_place, pk, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
