import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase
from django.urls import Resolver404, resolve, reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from community.models import CommunityImage

from .models import Course, CourseReaction, CourseStop, CourseView
from .views import CourseWriteThrottle


def course_data(**changes):
    data = {
        "title": "잠실 직관 코스",
        "stadium": "잠실야구장",
        "content": "경기 전 산책",
        "contentFormat": "html",
        "duration": "반나절",
        "tags": ["첫 직관"],
        "startLat": 37.51,
        "startLng": 127.07,
        "stops": [
            {"position": 0, "name": "카페", "lat": 37.5, "lng": 127.1, "category": "카페", "placeId": "p1"},
            {"position": 1, "name": "야구장", "lat": 37.51, "lng": 127.07, "category": "경기 관람", "isMapPoint": True},
        ],
    }
    data.update(changes)
    return data


class CourseApiTests(TestCase):
    def test_travel_modes_default_to_walk_and_survive_create_edit_read(self):
        created = self.create_course()
        self.assertEqual(created.data["travelMode"], "walk")
        self.assertEqual(created.data["legModes"], {})
        key = "37.500000,127.100000>37.510000,127.070000"
        url = f"/api/v1/courses/{created.data['id']}/"
        response = self.client.patch(url, {"travelMode": "walk", "legModes": {key: "transit"}},
                                     format="json", HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.get(url).data["legModes"], {key: "transit"})
        for value in ({key: "flight"}, {"bad": "car"}, {"99.000000,127.100000>37.510000,127.070000": "walk"}):
            response = self.client.patch(url, {"legModes": value}, format="json", HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"])
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(url).data["legModes"], {key: "transit"})

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.member = get_user_model().objects.create_user(username="course-test-member", password="test-pass")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.member).access_token}")

    def test_guests_can_read_but_cannot_create_edit_or_delete_courses(self):
        created = self.create_course()
        guest = APIClient()
        url = f"/api/v1/courses/{created.data['id']}/"
        self.assertEqual(guest.get("/api/v1/courses/").status_code, 200)
        self.assertEqual(guest.get(url).status_code, 200)
        self.assertEqual(guest.post("/api/v1/courses/", course_data(), format="json").status_code, 401)
        self.assertEqual(guest.patch(url, {"title": "변경"}, format="json", HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"]).status_code, 401)
        self.assertEqual(guest.delete(url, HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"]).status_code, 401)
        self.assertEqual(Course.objects.get(pk=created.data["id"]).title, course_data()["title"])

    def create_course(self):
        response = self.client.post("/api/v1/courses/", course_data(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def test_nested_round_trip_ordering_and_token_omission(self):
        created = self.create_course()
        token = created.data["editToken"]
        course = Course.objects.get(pk=created.data["id"])
        self.assertTrue(check_password(token, course.edit_token_hash))
        self.assertEqual(list(course.stops.values_list("position", flat=True)), [0, 1])

        detail = self.client.get(f"/api/v1/courses/{course.pk}/")
        listing = self.client.get("/api/v1/courses/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["stops"], course_data()["stops"])
        self.assertNotIn("editToken", detail.data)
        self.assertNotIn("editToken", listing.data[0])
        self.assertNotIn("edit_token_hash", detail.data)
        self.assertRegex(detail.data["routeNumber"], r"^\d{6}$")

        reversed_stops = list(reversed(course_data()["stops"]))
        reordered = self.client.post("/api/v1/courses/", course_data(stops=reversed_stops), format="json")
        self.assertEqual(reordered.status_code, 201, reordered.data)
        self.assertEqual([stop["position"] for stop in reordered.data["stops"]], [0, 1])

    def test_patch_and_delete_require_the_edit_token(self):
        created = self.create_course()
        url = f"/api/v1/courses/{created.data['id']}/"
        self.assertEqual(self.client.patch(url, {"title": "변경"}, format="json").status_code, 403)
        self.assertEqual(self.client.patch(url, {"title": "변경"}, format="json", HTTP_X_COURSE_EDIT_TOKEN="wrong").status_code, 403)
        updated = self.client.patch(url, {"title": "변경"}, format="json", HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"])
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["title"], "변경")
        self.assertNotIn("editToken", updated.data)
        self.assertEqual(self.client.delete(url, HTTP_X_COURSE_EDIT_TOKEN="wrong").status_code, 403)
        deleted = self.client.delete(url, HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"])
        self.assertEqual((deleted.status_code, deleted.content), (204, b""))
        self.assertFalse(Course.objects.filter(pk=created.data["id"]).exists())

    def test_reaction_is_member_owned_idempotent_and_view_is_token_deduplicated(self):
        created = self.create_course()
        course = Course.objects.get(pk=created.data["id"])
        reaction_url = f"/api/v1/courses/{course.pk}/reaction/"
        view_url = f"/api/v1/courses/{course.pk}/view/"
        self.assertEqual(APIClient().get(reaction_url).status_code, 401)

        user = get_user_model().objects.create_user(username="course-fan")
        self.client.force_authenticate(user)
        first = self.client.post(reaction_url, {"liked": True}, format="json")
        repeated = self.client.post(reaction_url, {"liked": True}, format="json")
        self.assertEqual((first.status_code, first.data), (200, {"liked": True, "likes": 1}))
        self.assertEqual(repeated.data, first.data)
        self.assertEqual(CourseReaction.objects.filter(course=course, user=user).count(), 1)
        removed = self.client.post(reaction_url, {"liked": False}, format="json")
        repeated_remove = self.client.post(reaction_url, {"liked": False}, format="json")
        self.assertEqual((removed.data, repeated_remove.data), ({"liked": False, "likes": 0}, {"liked": False, "likes": 0}))

        self.client.force_authenticate(user=None)
        self.client.credentials()
        token = str(uuid.uuid4())
        invalid = self.client.post(view_url, HTTP_X_COURSE_VIEW_TOKEN="not-a-uuid")
        first_view = self.client.post(view_url, HTTP_X_COURSE_VIEW_TOKEN=token)
        repeated_view = self.client.post(view_url, HTTP_X_COURSE_VIEW_TOKEN=token)
        second_view = self.client.post(view_url, HTTP_X_COURSE_VIEW_TOKEN=str(uuid.uuid4()))
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual((first_view.data["views"], repeated_view.data["views"], second_view.data["views"]), (1, 1, 2))
        self.assertEqual(CourseView.objects.filter(course=course).count(), 2)
        self.assertFalse(CourseView.objects.filter(actor_digest=token).exists())
        self.assertNotIn("Set-Cookie", first_view.headers)

    def test_course_writes_require_same_origin_without_trusting_forwarded_headers(self):
        for host in ("localhost:43123", "127.0.0.1:43124"):
            with self.subTest(host=host):
                response = self.client.post(
                    "/api/v1/courses/", course_data(), format="json",
                    HTTP_HOST=host, HTTP_ORIGIN=f"http://{host}",
                )
                self.assertEqual(response.status_code, 201, response.data)

        created = self.create_course()
        url = f"/api/v1/courses/{created.data['id']}/"
        forwarded = {
            "HTTP_ORIGIN": "https://evil.example",
            "HTTP_X_FORWARDED_HOST": "evil.example",
            "HTTP_X_FORWARDED_PROTO": "https",
        }
        self.assertEqual(self.client.post("/api/v1/courses/", course_data(), format="json", **forwarded).status_code, 403)
        self.assertEqual(self.client.patch(url, {"title": "거부"}, format="json", **forwarded).status_code, 403)
        self.assertEqual(self.client.delete(url, **forwarded).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/courses/", course_data(), format="json", HTTP_ORIGIN="null").status_code, 403)
        self.assertEqual(self.client.post(
            "/api/v1/courses/", course_data(), format="json",
            HTTP_ORIGIN="http://testserver", HTTP_SEC_FETCH_SITE="cross-site",
        ).status_code, 403)

    def test_course_write_body_is_bounded_json(self):
        self.assertEqual(self.client.generic(
            "POST", "/api/v1/courses/", b"{}", content_type="text/plain", HTTP_ORIGIN="http://testserver",
        ).status_code, 415)
        self.assertEqual(self.client.generic(
            "POST", "/api/v1/courses/", b"x" * 256001, content_type="application/json", HTTP_ORIGIN="http://testserver",
        ).status_code, 413)

    def test_story_format_round_trips_and_images_require_jwt_owner(self):
        owner = get_user_model().objects.create_user(username="route-image-owner", password="test-pass")
        other = get_user_model().objects.create_user(username="route-image-other", password="test-pass")
        image = CommunityImage.objects.create(
            owner=owner, object_key="community/test-course-image.jpg", content_type="image/jpeg",
            size=8, width=1, height=1,
        )
        run = {"text": "경기 전 카페", "font": "serif", "size": 20, "color": "#246bf3",
               "bold": True, "italic": False, "underline": False}
        document = {"version": 1, "blocks": [
            {"type": "paragraph", "align": "center", "runs": [run]},
            {"type": "image", "id": str(image.pk)},
        ]}
        payload = course_data(content="경기 전 카페\n[이미지]", contentFormat="", contentDoc=document)
        self.assertEqual(self.client.post("/api/v1/courses/", payload, format="json").status_code, 400)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(other).access_token}")
        self.assertEqual(self.client.post("/api/v1/courses/", payload, format="json").status_code, 400)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(owner).access_token}")
        created = self.client.post("/api/v1/courses/", payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        image.refresh_from_db()
        self.assertEqual(str(image.course_id), created.data["id"])
        detail = self.client.get(f"/api/v1/courses/{created.data['id']}/")
        self.assertEqual(detail.data["contentDoc"], document)
        changed = self.client.patch(
            f"/api/v1/courses/{created.data['id']}/",
            {"content": "경기 전 카페", "contentDoc": {"version": 1, "blocks": [document["blocks"][0]]}},
            format="json", HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"],
        )
        self.assertEqual(changed.status_code, 200, changed.data)
        image.refresh_from_db()
        self.assertIsNone(image.course_id)

    def test_styled_story_requires_jwt_even_without_images(self):
        document = {"version": 1, "blocks": [{"type": "paragraph", "align": "right", "runs": [
            {"text": "직관 준비", "font": "sans", "size": 16, "color": "#26354b",
             "bold": False, "italic": True, "underline": False},
        ]}]}
        payload = course_data(content="직관 준비", contentFormat="", contentDoc=document)
        self.assertEqual(APIClient().post("/api/v1/courses/", payload, format="json").status_code, 401)
        created = self.client.post("/api/v1/courses/", payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["contentDoc"], document)
        mismatch = self.client.post("/api/v1/courses/", course_data(content="다른 글", contentFormat="", contentDoc=document), format="json")
        self.assertEqual(mismatch.status_code, 400)

    def test_validation_rejects_invalid_course_shapes(self):
        invalid = (
            course_data(title=""),
            course_data(title="x" * 81),
            course_data(content="x" * 12001),
            course_data(startLng=None),
            course_data(startLat=91),
            course_data(stops=[]),
            course_data(stops=course_data()["stops"] * 7),
            course_data(stops=[{**course_data()["stops"][0], "lat": 91}]),
            course_data(stops=[{**course_data()["stops"][0], "lng": 181}]),
            course_data(stops=[{**course_data()["stops"][0], "position": 1}]),
            course_data(stops=[course_data()["stops"][0], {**course_data()["stops"][1], "position": 0}]),
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/v1/courses/", payload, format="json").status_code, 400)
        self.assertEqual(Course.objects.filter(is_sample=False).count(), 0)

    def test_non_finite_coordinates_never_persist(self):
        invalid = tuple(
            payload
            for value in ("NaN", "Infinity", "-Infinity")
            for payload in (course_data(**{field: value}) for field in ("startLat", "startLng"))
        ) + tuple(
            course_data(stops=[{**course_data()["stops"][0], field: value}])
            for value in ("NaN", "Infinity", "-Infinity")
            for field in ("lat", "lng")
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/v1/courses/", payload, format="json").status_code, 400)
        self.assertEqual(Course.objects.filter(is_sample=False).count(), 0)
        self.assertEqual(CourseStop.objects.filter(course__is_sample=False).count(), 0)

        created = self.create_course()
        course = Course.objects.get(pk=created.data["id"])
        old_stops = list(course.stops.values("position", "name", "lat", "lng"))
        response = self.client.patch(
            f"/api/v1/courses/{course.pk}/",
            {"title": "바뀌면 안 됨", "stops": [{**course_data()["stops"][0], "lng": "NaN"}]},
            format="json",
            HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"],
        )
        self.assertEqual(response.status_code, 400)
        course.refresh_from_db()
        self.assertEqual(course.title, course_data()["title"])
        self.assertEqual(list(course.stops.values("position", "name", "lat", "lng")), old_stops)

    def test_patch_replacement_requires_every_stop_field_before_mutation(self):
        created = self.create_course()
        course = Course.objects.get(pk=created.data["id"])
        url = f"/api/v1/courses/{course.pk}/"
        old_stops = list(course.stops.values())

        for field in ("position", "name", "category", "lat", "lng"):
            stop = dict(course_data()["stops"][0])
            stop.pop(field)
            with self.subTest(field=field):
                response = self.client.patch(
                    url,
                    {"title": "바뀌면 안 됨", "stops": [stop]},
                    format="json",
                    HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"],
                )
                self.assertEqual(response.status_code, 400, response.data)
                course.refresh_from_db()
                self.assertEqual(course.title, course_data()["title"])
                self.assertEqual(list(course.stops.values()), old_stops)

        replacement = [{"position": 0, "name": "새 장소", "lat": 35.0, "lng": 128.0, "category": "식사"}]
        response = self.client.patch(
            url,
            {"stops": replacement},
            format="json",
            HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"],
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["stops"], replacement)

    def test_failed_stop_replacement_rolls_back_course_and_stops(self):
        created = self.create_course()
        course = Course.objects.get(pk=created.data["id"])
        old_stops = list(course.stops.values("position", "name"))
        replacement = [{"position": 0, "name": "새 장소", "lat": 35.0, "lng": 128.0, "category": "식사"}]
        with patch.object(CourseStop.objects, "bulk_create", side_effect=RuntimeError("db failure")):
            with self.assertRaises(RuntimeError):
                self.client.patch(
                    f"/api/v1/courses/{course.pk}/",
                    {"title": "저장되면 안 됨", "stops": replacement},
                    format="json",
                    HTTP_X_COURSE_EDIT_TOKEN=created.data["editToken"],
                )
        course.refresh_from_db()
        self.assertEqual(course.title, course_data()["title"])
        self.assertEqual(list(course.stops.values("position", "name")), old_stops)

    def test_anonymous_write_throttle_uses_the_last_trusted_proxy_address(self):
        created = self.create_course()
        url = f"/api/v1/courses/{created.data['id']}/"
        cases = (
            (lambda address: self.client.post("/api/v1/courses/", course_data(title=""), format="json", HTTP_X_FORWARDED_FOR=address), 400),
            (lambda address: self.client.patch(url, {"title": "변경"}, format="json", HTTP_X_FORWARDED_FOR=address), 403),
            (lambda address: self.client.delete(url, HTTP_X_FORWARDED_FOR=address), 403),
        )
        with patch.object(CourseWriteThrottle, "THROTTLE_RATES", {"course_write": "1/min"}):
            for index, (request, expected) in enumerate(cases, start=1):
                first = f"192.0.2.{index}"
                second = f"198.51.100.{index}"
                self.assertEqual(request(first).status_code, expected)
                self.assertEqual(request(first).status_code, 429)
                self.assertEqual(request(second).status_code, expected)
                self.assertEqual(request(f"203.0.113.99, {first}").status_code, 429)

    def test_course_routes_match_the_nginx_stripped_api_prefix(self):
        created = self.create_course()
        self.assertEqual(reverse("course-list"), "/api/v1/courses/")
        self.assertEqual(reverse("course-detail", kwargs={"pk": created.data["id"]}), f"/api/v1/courses/{created.data['id']}/")
        self.assertEqual(resolve("/api/v1/courses/").url_name, "course-list")
        self.assertEqual(resolve(f"/api/v1/courses/{created.data['id']}/").url_name, "course-detail")
        with self.assertRaises(Resolver404):
            resolve("/courses/")


class CourseSampleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.samples = json.loads((Path(__file__).parent / "seed_data" / "course_samples_v1.json").read_text(encoding="utf-8"))

    def test_seed_matches_the_immutable_course_snapshot(self):
        self.assertEqual(Course.objects.filter(is_sample=True).count(), 19)
        self.assertEqual(CourseStop.objects.filter(course__is_sample=True).count(), 58)
        self.assertEqual(sum(len(sample["tags"]) for sample in self.samples), 43)
        for sample in self.samples:
            with self.subTest(sample=sample["id"]):
                course = Course.objects.get(source_id=sample["id"])
                self.assertEqual(course.pk, uuid.uuid5(uuid.NAMESPACE_URL, f"kbo-trip/course/{sample['id']}"))
                self.assertEqual(
                    (course.title, course.stadium, course.description, course.content, course.content_format, course.duration, course.cover, course.tags, course.author, course.likes, course.views),
                    (sample["title"], sample["stadium"], sample["description"], sample["content"], sample.get("contentFormat", ""), sample["duration"], sample["cover"], sample["tags"], sample["author"], sample["likes"], sample.get("views", 0)),
                )
                self.assertEqual(course.created_at, datetime.fromisoformat(sample["createdAt"]))
                self.assertEqual(course.updated_at, course.created_at)
                self.assertTrue(course.edit_token_hash.startswith("!"))
                self.assertFalse(check_password("any-token", course.edit_token_hash))
                self.assertEqual(
                    list(course.stops.values("position", "name", "lat", "lng", "category", "place_id", "visit_id", "address", "tour_content_id", "is_map_point", "is_drawn_point")),
                    [
                        {
                            "position": position,
                            "name": stop["name"],
                            "lat": stop["lat"],
                            "lng": stop["lng"],
                            "category": stop["category"],
                            "place_id": stop.get("placeId"),
                            "visit_id": stop.get("visitId"),
                            "address": stop.get("address"),
                            "tour_content_id": stop.get("tourContentId"),
                            "is_map_point": stop.get("isMapPoint"),
                            "is_drawn_point": stop.get("isDrawnPoint"),
                        }
                        for position, stop in enumerate(sample["stops"])
                    ],
                )

    def test_samples_are_read_only_and_can_be_cloned_as_ordinary_courses(self):
        sample = Course.objects.get(source_id="fan-sajik-date")
        self.assertEqual(APIClient().patch(f"/api/v1/courses/{sample.pk}/", {"title": "변경"}, format="json", HTTP_X_COURSE_EDIT_TOKEN="wrong").status_code, 401)
        payload = course_data(
            title=sample.title,
            stadium=sample.stadium,
            content=sample.content,
            contentFormat=sample.content_format,
            duration=sample.duration,
            tags=sample.tags,
            stops=[
                {
                    "position": stop.position,
                    "name": stop.name,
                    "lat": stop.lat,
                    "lng": stop.lng,
                    "category": stop.category,
                    "placeId": stop.place_id,
                    "isDrawnPoint": stop.is_drawn_point,
                }
                for stop in sample.stops.all()
            ],
            isSample=True,
            sampleId="client-controlled",
            description="client-controlled",
            cover="/client-controlled.jpg",
            likes=999,
            views=999,
        )
        payload.pop("startLat")
        payload.pop("startLng")
        member = get_user_model().objects.create_user(username="sample-clone-member", password="test-pass")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(member).access_token}")
        response = client.post("/api/v1/courses/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        clone = Course.objects.get(pk=response.data["id"])
        self.assertFalse(clone.is_sample)
        self.assertIsNone(clone.source_id)
        self.assertEqual((clone.description, clone.cover, clone.likes, clone.views), ("", "", 0, 0))
        self.assertEqual(clone.stops.count(), 3)
        self.assertNotIn("sampleId", response.data)
        self.assertFalse(response.data["isSample"])

    def test_list_exposes_each_sample_once_with_legacy_id_metadata(self):
        response = APIClient().get("/api/v1/courses/")
        self.assertEqual(response.status_code, 200)
        samples = [course for course in response.data if course["isSample"]]
        self.assertEqual(len(samples), 19)
        self.assertEqual(len({course["sampleId"] for course in samples}), 19)
        copied = next(course for course in samples if course["sampleId"] == "fan-sajik-date")
        self.assertEqual([stop["name"] for stop in copied["stops"]], ["사직야구장", "산책 후보 지점", "마무리 지점"])


class CourseConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_parallel_create_assigns_distinct_consecutive_route_numbers(self):
        barrier = Barrier(2)

        def create(index):
            close_old_connections()
            barrier.wait(2)
            course = Course.objects.create(
                title=f"동시 코스 {index}", stadium="잠실", duration="반나절", tags=[], edit_token_hash="hash",
            )
            close_old_connections()
            return course.route_number

        with ThreadPoolExecutor(max_workers=2) as pool:
            numbers = sorted(pool.map(create, range(2)))
        self.assertEqual(int(numbers[1]) - int(numbers[0]), 1)

    def test_parallel_same_member_like_is_idempotent(self):
        course = Course.objects.create(title="반응 코스", stadium="잠실", duration="반나절", tags=[], edit_token_hash="hash")
        user = get_user_model().objects.create_user(username="parallel-course-fan")
        barrier = Barrier(2)

        def like(_index):
            close_old_connections()
            client = APIClient()
            client.force_authenticate(get_user_model().objects.get(pk=user.pk))
            barrier.wait(2)
            response = client.post(f"/api/v1/courses/{course.pk}/reaction/", {"liked": True}, format="json")
            close_old_connections()
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(like, range(2)))
        course.refresh_from_db()
        self.assertEqual(statuses, [200, 200])
        self.assertEqual((course.likes, CourseReaction.objects.filter(course=course, user=user).count()), (1, 1))


class CourseSampleMigrationTests(TransactionTestCase):
    def test_reverse_noop_and_reapply_preserve_custom_rows_and_do_not_duplicate_samples(self):
        latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        custom = Course.objects.create(
            title="사용자 코스", stadium="잠실야구장", duration="반나절", tags=[], author="익명", edit_token_hash="custom-hash"
        )
        CourseStop.objects.create(course=custom, position=0, name="사용자 장소", lat=37.5, lng=127.1, category="카페")
        MigrationExecutor(connection).migrate([("travel", "0003_course_sample_fields")])
        MigrationExecutor(connection).migrate([
            ("travel", "0006_merge_course_engagement_content_doc"),
            ("community", "0005_communitypostimage_course"),
        ])
        historical = MigrationExecutor(connection).loader.project_state([
            ("travel", "0006_merge_course_engagement_content_doc"),
            ("community", "0005_communitypostimage_course"),
        ]).apps
        HistoricalCourse = historical.get_model("travel", "Course")
        HistoricalStop = historical.get_model("travel", "CourseStop")
        custom = HistoricalCourse.objects.get(pk=custom.pk)
        self.assertEqual(custom.edit_token_hash, "custom-hash")
        self.assertEqual(list(custom.stops.values_list("name", flat=True)), ["사용자 장소"])
        self.assertEqual(HistoricalCourse.objects.filter(is_sample=True).count(), 19)
        self.assertEqual(HistoricalStop.objects.filter(course__is_sample=True).count(), 58)
        MigrationExecutor(connection).migrate(latest)
