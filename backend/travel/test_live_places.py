from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory

from .models import Place
from .place_service import PlaceUpstreamError, PlaceValidationError, search_live_places
from .place_views import PlaceLiveSearchView


class LivePlacesTests(TestCase):
    query = {"method": "category", "category": "FD6", "lat": 37.512, "lng": 127.072,
             "radius": 2500, "page": 1, "size": 15, "sort": "distance"}
    document = {"id": "123", "place_name": "가상 식당", "x": "127.080", "y": "37.510",
                "category_group_code": "FD6", "category_name": "음식점 > 일식",
                "place_url": "https://place.map.kakao.com/123"}

    def payload(self, documents=None):
        return {"meta": {"is_end": False}, "documents": documents if documents is not None else [self.document]}

    def test_lookup_and_repeated_lookup_do_not_read_or_write_database(self):
        with patch("travel.place_service._request_kakao", return_value=self.payload()) as upstream:
            with self.assertNumQueries(0):
                for _ in range(2):
                    result = search_live_places(self.query)
            self.assertEqual(upstream.call_count, 2)
        self.assertEqual(result["places"][0]["place_name"], "가상 식당")
        self.assertTrue(result["hasNextPage"])
        self.assertEqual(Place.objects.count(), 0)

    def test_food_cafe_and_unrestricted_keyword(self):
        for category in ("FD6", "CE7", ""):
            query = {**self.query, "category": category}
            if not category:
                query.pop("category")
                query.update(method="keyword", keyword="공원")
            with patch("travel.place_service._request_kakao", return_value=self.payload([{**self.document, "category_group_code": category}])):
                self.assertEqual(len(search_live_places(query)["places"]), 1)

    def test_invalid_paging_categories_and_extra_fields_do_not_call_provider(self):
        for fields in ({"page": 4}, {"size": 16}, {"category": "HP8"}, {"url": "https://example.com"}):
            with patch("travel.place_service._request_kakao") as upstream, self.assertRaises(PlaceValidationError):
                search_live_places({**self.query, **fields})
            upstream.assert_not_called()

    def test_bad_provider_results_are_rejected_without_writes(self):
        for documents in (
            [self.document, self.document], [{**self.document, "id": "not-numeric"}],
            [{**self.document, "id": "１２３"}], [{**self.document, "category_group_code": "AD5"}],
            [{**self.document, "y": "NaN"}], [{**self.document, "place_url": "https://example.com/123"}],
        ):
            with patch("travel.place_service._request_kakao", return_value=self.payload(documents)), self.assertNumQueries(0), self.assertRaises(PlaceUpstreamError):
                search_live_places(self.query)

    def test_endpoint_is_read_only_and_no_store(self):
        with patch("travel.place_service._request_kakao", return_value=self.payload()), patch.object(PlaceLiveSearchView, "throttle_classes", ()):
            response = PlaceLiveSearchView.as_view()(APIRequestFactory().post("/api/v1/places/live/", self.query, format="json"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(Place.objects.count(), 0)
