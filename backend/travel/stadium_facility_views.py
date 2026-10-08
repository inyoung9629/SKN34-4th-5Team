from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .collected_places import CatalogueQueryError, CatalogueUnavailable
from .stadium_facilities import facility_catalogue


class StadiumFacilityListView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="stadium_facilities_list", parameters=[OpenApiParameter("stadium", str, required=True)], responses={200: inline_serializer(name="StadiumFacilityCatalogue", fields={
        "stadium": serializers.CharField(), "count": serializers.IntegerField(), "pinCount": serializers.IntegerField(),
        "checkedAt": serializers.CharField(), "warning": serializers.CharField(), "review": serializers.DictField(),
        "records": serializers.ListField(child=serializers.DictField()),
    })}, auth=[])
    def get(self, request):
        if set(request.query_params) != {"stadium"} or len(request.query_params.getlist("stadium")) != 1:
            return Response({"error": "구장 코드 하나를 지정해 주세요."}, status=400)
        try:
            return Response(facility_catalogue(request.query_params["stadium"]), headers={"Cache-Control": "private, max-age=300"})
        except CatalogueQueryError as error:
            return Response({"error": str(error)}, status=400)
        except CatalogueUnavailable:
            return Response({"error": "구장 시설 자료를 읽지 못했습니다. 일반 장소로 대체하지 않습니다."}, status=503)
