from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.admin_views import StaffOnly
from .models import AdCreative
from .serializers import AdAdminSerializer, AdEventInputSerializer, AdEventResultSerializer, AdQuerySerializer, AdSlotResponseSerializer
from .services import make_delivery, record_event, select_ad


class AdSlotView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "ad_delivery"

    @extend_schema(parameters=[AdQuerySerializer], responses=AdSlotResponseSerializer)
    def get(self, request):
        query = AdQuerySerializer(data={"placement": request.query_params.get("placement"), "route_ids": request.query_params.getlist("route_ids")})
        query.is_valid(raise_exception=True)
        delivery = make_delivery(select_ad(query.validated_data["placement"], query.validated_data["route_ids"]))
        response = Response(AdSlotResponseSerializer(delivery).data)
        response["Cache-Control"] = "no-store"
        return response


class AdEventView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "ad_event"

    @extend_schema(request=AdEventInputSerializer, responses=AdEventResultSerializer)
    def post(self, request):
        serializer = AdEventInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(record_event(**serializer.validated_data))


class AdAdminPagination(PageNumberPagination):
    page_size = 20


class AdAdminListCreateView(generics.ListCreateAPIView):
    permission_classes = (StaffOnly,)
    serializer_class = AdAdminSerializer
    pagination_class = AdAdminPagination
    queryset = AdCreative.objects.prefetch_related("courses").all()


class AdAdminDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = (StaffOnly,)
    serializer_class = AdAdminSerializer
    queryset = AdCreative.objects.prefetch_related("courses").all()
    http_method_names = ["get", "patch", "head", "options"]
