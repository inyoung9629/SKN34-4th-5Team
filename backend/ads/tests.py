import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APITestCase

from travel.models import Course, Place
from .models import AdCreative, AdEvent, AdPlacement
from .services import TOKEN_SALT, select_ad


class AdApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.ad = AdCreative.objects.create(advertiser="테스트 광고주", title="구단 광고", image_url="https://example.com/banner.png", destination_url="https://example.com/event", placement=AdPlacement.CLUB, starts_at=self.now - timedelta(hours=1), ends_at=self.now + timedelta(hours=1), active=True)

    def delivery(self, **query):
        response = self.client.get("/api/v1/ads/slots/", {"placement": AdPlacement.CLUB, **query})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        return response.data

    def event(self, token, kind="impression", event_id=None):
        return self.client.post("/api/v1/ads/events/", {"event_id": str(event_id or uuid.uuid4()), "token": token, "kind": kind}, format="json")

    def test_public_delivery_minimal_fields(self):
        result = self.delivery()
        self.assertEqual(result["ad"]["id"], self.ad.pk)
        self.assertTrue(result["token"])
        self.assertNotIn("courses", result["ad"])
        self.assertNotIn("is_test", result["ad"])

    def test_inactive_expired_future_and_test_ads_are_hidden(self):
        for changes in [{"active": False}, {"ends_at": self.now - timedelta(minutes=1)}, {"starts_at": self.now + timedelta(minutes=10)}, {"is_test": True}]:
            with self.subTest(changes=changes):
                AdCreative.objects.filter(pk=self.ad.pk).update(active=True, is_test=False, starts_at=self.now - timedelta(hours=1), ends_at=self.now + timedelta(hours=1))
                AdCreative.objects.filter(pk=self.ad.pk).update(**changes)
                self.assertIsNone(self.delivery()["ad"])

    def test_exact_start_included_end_excluded(self):
        with patch("ads.services.timezone.now", return_value=self.ad.starts_at):
            self.assertEqual(select_ad(AdPlacement.CLUB, []).pk, self.ad.pk)
        with patch("ads.services.timezone.now", return_value=self.ad.ends_at):
            self.assertIsNone(select_ad(AdPlacement.CLUB, []))

    def test_database_enforces_period(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdCreative.objects.filter(pk=self.ad.pk).update(ends_at=self.ad.starts_at)

    def test_query_validation(self):
        for query in [{"placement": "unknown"}, {}, {"placement": AdPlacement.CLUB, "route_ids": ["a"] * 4}, {"placement": AdPlacement.CLUB, "route_ids": ["x" * 81]}]:
            self.assertEqual(self.client.get("/api/v1/ads/slots/", query).status_code, 400)

    def test_duplicate_events_are_counted_once(self):
        token = self.delivery()["token"]
        event_id = uuid.uuid4()
        first = self.event(token, event_id=event_id)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.data["duplicate"])
        self.assertTrue(self.event(token, event_id=event_id).data["duplicate"])
        self.assertTrue(self.event(token).data["duplicate"])
        self.assertEqual(AdEvent.objects.count(), 1)

    def test_click_can_arrive_before_impression(self):
        token = self.delivery()["token"]
        self.assertEqual(self.event(token, "click").status_code, 200)
        self.assertEqual(self.event(token).status_code, 200)
        self.assertEqual(AdEvent.objects.count(), 2)

    def test_event_id_cannot_be_reused_across_exposures(self):
        token_a, token_b = self.delivery()["token"], self.delivery()["token"]
        event_id = uuid.uuid4()
        self.assertEqual(self.event(token_a, event_id=event_id).status_code, 200)
        self.assertEqual(self.event(token_b, event_id=event_id).status_code, 400)
        self.assertEqual(self.event(token_a, "click", event_id).status_code, 400)

    def test_tampered_expired_and_malformed_tokens(self):
        token = self.delivery()["token"]
        self.assertEqual(self.event(token + "changed").status_code, 400)
        payload = signing.loads(token, salt=TOKEN_SALT)
        payload["expires"] = self.now.timestamp() - 1
        self.assertEqual(self.event(signing.dumps(payload, salt=TOKEN_SALT)).status_code, 400)
        for value in [None, [], {}, {"expires": float("nan")}]:
            self.assertEqual(self.event(signing.dumps(value, salt=TOKEN_SALT)).status_code, 400)

    def test_disabled_or_test_ad_rejects_events(self):
        token = self.delivery()["token"]
        AdCreative.objects.filter(pk=self.ad.pk).update(active=False)
        self.assertEqual(self.event(token).status_code, 400)
        AdCreative.objects.filter(pk=self.ad.pk).update(active=True, is_test=True)
        self.assertEqual(self.event(token).status_code, 400)

    def test_admin_jwt_permissions(self):
        url = "/api/v1/ads/admin/creatives/"
        self.assertEqual(self.client.get(url).status_code, 401)
        user = get_user_model().objects.create_user(username="ad-viewer", password="test-only-password")
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(url).status_code, 403)
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get(url).status_code, 200)
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_create_update_validation_and_pagination(self):
        staff = get_user_model().objects.create_user(username="ad-staff", is_staff=True)
        self.client.force_authenticate(staff)
        url = "/api/v1/ads/admin/creatives/"
        payload = {"advertiser": "업체", "title": "새 광고", "image_url": "https://example.com/a.png", "destination_url": "https://example.com/ad", "placement": AdPlacement.CLUB, "starts_at": self.ad.starts_at.isoformat(), "ends_at": self.ad.ends_at.isoformat()}
        result = self.client.post(url, payload, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        detail = f"{url}{result.data['id']}/"
        self.assertEqual(self.client.patch(detail, {"active": True}, format="json").status_code, 200)
        for changes in [{"ends_at": self.ad.starts_at.isoformat()}, {"destination_url": "javascript:alert(1)"}, {"image_url": "http://example.com/a.png"}, {"destination_url": "https://user:password@example.com"}, {"placement": AdPlacement.PARTNER}]:
            self.assertEqual(self.client.patch(detail, changes, format="json").status_code, 400)
        for index in range(20):
            AdCreative.objects.create(**{**payload, "title": f"광고 {index}"})
        page = self.client.get(url).data
        self.assertEqual(page["count"], 22)
        self.assertEqual(len(page["results"]), 20)
        self.assertIsNotNone(page["next"])
        self.assertEqual(self.client.delete(detail).status_code, 405)

    def test_partner_matches_only_registered_course_uuid_or_source(self):
        course = Course.objects.create(source_id="sample-ad-course", route_number="999991", title="광고 테스트", stadium="잠실", duration="1시간", edit_token_hash="test")
        place = Place.objects.create(name="테스트 식당", lat=37.5, lng=127.0)
        self.ad.placement, self.ad.place = AdPlacement.PARTNER, place
        self.ad.save()
        self.ad.courses.add(course)
        for route_id in [str(course.pk), course.source_id]:
            self.assertEqual(self.delivery(placement=AdPlacement.PARTNER, route_ids=[route_id])["ad"]["id"], self.ad.pk)
        self.assertIsNone(self.delivery(placement=AdPlacement.PARTNER, route_ids=["unknown"])["ad"])
        self.assertIsNone(self.delivery(placement=AdPlacement.PARTNER)["ad"])

    def test_rate_limit(self):
        with patch("rest_framework.throttling.ScopedRateThrottle.get_rate", return_value="1/minute"):
            self.assertEqual(self.client.get("/api/v1/ads/slots/", {"placement": AdPlacement.CLUB}).status_code, 200)
            self.assertEqual(self.client.get("/api/v1/ads/slots/", {"placement": AdPlacement.CLUB}).status_code, 429)
