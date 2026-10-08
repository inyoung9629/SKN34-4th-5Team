from unittest.mock import patch
from django.test import TestCase
from django.db import IntegrityError, transaction
from rest_framework.test import APIRequestFactory
from .models import Course, CourseStop, Place
from .serializers import CourseStopSerializer
from .place_service import search_live_lodging, PlaceValidationError, PlaceUpstreamError
from .place_views import LodgingSearchView, PlaceSearchThrottle


class KakaoLodgingTests(TestCase):
    query = {"method":"category", "category":"AD5", "lat":37.512, "lng":127.072, "radius":2500, "page":1, "size":15, "sort":"distance"}
    document = {"id":"123", "place_name":"테스트 호텔", "x":"127.080", "y":"37.510", "category_group_code":"AD5", "category_group_name":"숙박", "category_name":"숙박 > 호텔", "road_address_name":"서울 테스트로 1", "address_name":"서울 테스트동", "phone":"", "place_url":"https://place.map.kakao.com/123"}

    def test_live_search_never_writes_provider_content(self):
        with patch("travel.place_service._request_kakao", return_value={"meta":{"is_end":True}, "documents":[self.document]}), self.assertNumQueries(0):
            result = search_live_lodging(self.query)
        self.assertEqual(result["places"][0]["id"], "123")
        self.assertFalse(result["hasNextPage"])
        self.assertEqual(Place.objects.count(), 0)

    def test_lodging_endpoint_is_no_store(self):
        with patch("travel.place_service._request_kakao", return_value={"meta":{"is_end":True}, "documents":[self.document]}), patch.dict(PlaceSearchThrottle.THROTTLE_RATES, {"place_search":"100/min"}):
            response = LodgingSearchView.as_view()(APIRequestFactory().post("/api/v1/places/lodging/", self.query, format="json"))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_arbitrary_queries_and_wrong_provider_response_rejected(self):
        for query in ({**self.query, "category":"FD6"}, {**self.query, "method":"keyword", "keyword":"맛집"}):
            with self.assertRaises(PlaceValidationError):
                search_live_lodging(query)
        for document in ({**self.document, "category_group_code":"FD6"}, {**self.document, "id":"abc"}):
            with patch("travel.place_service._request_kakao", return_value={"meta":{"is_end":True}, "documents":[document]}), self.assertRaises(PlaceUpstreamError):
                search_live_lodging(self.query)

    def test_reference_strips_name_address_and_coordinates(self):
        serializer = CourseStopSerializer(data={"position":0, "name":"provider name", "category":"호텔", "address":"provider address", "placeId":"kakao-lodging:hotel:123", "lat":None, "lng":None})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["name"], "선택한 숙소")
        self.assertNotIn("address", serializer.validated_data)
        for identity in ("kakao-lodging:wrong:123", "kakao-lodging:hotel:abc"):
            invalid = CourseStopSerializer(data={"position":0,"name":"x","category":"숙박","placeId":identity,"lat":None,"lng":None})
            self.assertFalse(invalid.is_valid())
        course = Course.objects.create(title="test", stadium="test", duration="test", route_number="900014", edit_token_hash="test")
        CourseStop.objects.create(course=course, position=0, name="선택한 숙소", category="숙박", place_id="kakao-lodging:hotel:123", lat=None, lng=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseStop.objects.create(course=course, position=1, name="x", category="숙박", place_id="kakao-lodging:hotel:456", lat=37, lng=127)
