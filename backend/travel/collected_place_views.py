from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer

from .collected_places import CatalogueQueryError, CatalogueUnavailable, catalogue


class CollectedPlaceListView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="collected_places_list", parameters=[OpenApiParameter("stadium", str, required=True)], responses={200: inline_serializer(name="CollectedPlaceCatalogue", fields={
        "status": serializers.CharField(), "snapshotId": serializers.CharField(), "stadium": serializers.CharField(),
        "radiusM": serializers.IntegerField(), "count": serializers.IntegerField(), "warning": serializers.CharField(),
        "places": serializers.ListField(child=serializers.DictField()), "lodging": serializers.DictField(),
    })}, auth=[])
    def get(self, request):
        if set(request.query_params) != {"stadium"} or len(request.query_params.getlist("stadium")) != 1:
            return Response({"error": "구장 코드 하나를 지정해 주세요."}, status=400)
        try:
            payload = catalogue(request.query_params["stadium"])
        except CatalogueQueryError as error:
            return Response({"error": str(error)}, status=400)
        except CatalogueUnavailable:
            return Response({"error": CatalogueUnavailable.message}, status=503)
        return Response(payload, headers={"Cache-Control": "private, max-age=300"})
