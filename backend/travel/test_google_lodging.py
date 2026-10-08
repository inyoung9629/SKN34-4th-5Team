from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework.test import APIRequestFactory, force_authenticate
from .models import Course, CourseStop
from .serializers import CourseSerializer, CourseStopSerializer
from .views import CourseListCreateView, CourseDetailView, CourseWriteThrottle


class GoogleLodgingTests(TestCase):
    def stop(self, **changes):
        return {"position": 0, "name": "Google name must not persist", "category": "호텔",
                "address": "Google address", "placeId": "google-ui-kit:fixture_1",
                "lat": None, "lng": None, **changes}

    def test_reference_only_roundtrip_and_patch(self):
        data = {"title": "테스트", "stadium": "인천", "duration": "반나절", "tags": [], "stops": [self.stop()]}
        serializer = CourseSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with patch("travel.models.next_route_number", return_value="900001"):
            # Explicit number avoids PostgreSQL's sequence default in isolated SQLite tests.
            course = serializer.save(route_number="900001", edit_token_hash="test")
        stop = course.stops.get()
        self.assertIsNone(stop.lat)
        self.assertIsNone(stop.lng)
        self.assertIsNone(stop.address)
        self.assertEqual(stop.name, "선택한 숙소")
        self.assertEqual(stop.category, "숙박")
        self.assertEqual(CourseSerializer(course).data["stops"][0]["lat"], None)
        patcher = CourseSerializer(course, data={"stops": [self.stop(placeId="google-ui-kit:fixture_2")]}, partial=True)
        self.assertTrue(patcher.is_valid(), patcher.errors)
        patcher.save()
        self.assertEqual(course.stops.get().place_id, "google-ui-kit:fixture_2")

    def test_google_coordinates_and_invalid_ids_rejected(self):
        for changes in ({"lat": 37, "lng": 127}, {"lat": 37}, {"placeId": "google-ui-kit:"}, {"placeId": "google-ui-kit:a/b"}):
            serializer = CourseStopSerializer(data=self.stop(**changes))
            self.assertFalse(serializer.is_valid())

    def test_ordinary_places_still_require_coordinates(self):
        serializer = CourseStopSerializer(data=self.stop(placeId="collected:SBIZ:1"))
        self.assertFalse(serializer.is_valid())
        serializer = CourseStopSerializer(data=self.stop(placeId="collected:SBIZ:1", lat=37, lng=127))
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_api_create_and_public_read_reference(self):
        user = get_user_model().objects.create_user(username="lodging-test", password="test-only")
        factory = APIRequestFactory()
        request = factory.post("/api/v1/courses/", {"title": "숙박 참조 테스트", "stadium": "인천", "duration": "반나절", "tags": [], "stops": [self.stop()]}, format="json")
        force_authenticate(request, user=user)
        with patch.object(Course._meta.get_field("route_number"), "_get_default", lambda: "900002"), patch.dict(CourseWriteThrottle.THROTTLE_RATES, {"course_write": "100/min"}):
            created = CourseListCreateView.as_view()(request)
        self.assertEqual(created.status_code, 201, created.data)
        response = CourseDetailView.as_view()(factory.get("/api/v1/courses/test/"), pk=created.data["id"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stops"][0]["placeId"], "google-ui-kit:fixture_1")
        self.assertIsNone(response.data["stops"][0]["lat"])

    def test_database_rejects_coordinate_content_on_reference(self):
        course = Course.objects.create(title="test", stadium="test", duration="test", route_number="900003", edit_token_hash="test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseStop.objects.create(course=course, position=0, name="test", category="숙박", place_id="google-ui-kit:test", lat=37, lng=127)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseStop.objects.create(course=course, position=0, name="test", category="카페", lat=None, lng=None)
