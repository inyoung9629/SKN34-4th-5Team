import importlib
import json
import tempfile
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.test.utils import override_settings
from PIL import Image
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import CommunityImage, CommunityPost, CommunityReport, CommunityVote, TEAM_CODES


SEED_FILE = Path(__file__).parent / "migrations/data/community_posts_v1.json"


class CommunityPostApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="owner", nickname="글쓴이")
        cls.other = get_user_model().objects.create_user(username="other", nickname="다른이")

    def create_post(self, key="create-1", **changes):
        payload = {"board": "free", "teamCode": "", "category": "잡담", "title": "새 글", "content": "새 본문"}
        payload.update(changes)
        self.client.force_authenticate(self.owner)
        return self.client.post("/api/v1/community/posts/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key)

    def test_public_list_and_filters_return_seeded_dto(self):
        response = self.client.get("/api/v1/community/posts/")
        self.assertEqual(response.status_code, 200)
        # 예시 350개 + 이전된 글(0006) + 로컬 샘플 글(0010)
        self.assertEqual(len(response.data), 352)
        self.assertEqual(response.data[0]["id"], "e25bc43f62554ec2adc6fbd683fc397b")
        self.assertEqual(response.data[0]["author"], "신그는날두인가")
        self.assertFalse(response.data[0]["isSample"])
        self.assertEqual(response.data[1]["id"], "153c4936cba745d3a3e89f07891885c4")
        self.assertEqual(response.data[1]["author"], "진성칰갈")
        self.assertEqual(response.data[-1], {
            "id": "lg-sample-1", "sourceId": "lg-sample-1", "postNumber": "001001", "board": "teams",
            "teamCode": "LG", "authorId": None, "author": "예시 작성자", "title": "잠실 외야에서 보면 타구 판단 좀 되나요?",
            "content": "늘 내야에서만 보다가 이번엔 외야로 가볼까 합니다.\n\n공이 뜨면 홈런인지 평범한 플라이인지 구분이 잘 되는지 궁금해요. 중계로 볼 때랑 느낌이 많이 다를 것 같아서요. LG 응원하면서 수비 위치도 같이 보고 싶습니다.", "contentDoc": None,
            "category": "좌석·예매", "createdAt": None, "views": 0, "recommendations": 0, "downvotes": 0,
            "commentCount": 0, "isSample": True, "images": [],
            "images": [],
        })
        numbers = [post["postNumber"] for post in response.data]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
        self.assertEqual(len(self.client.get("/api/v1/community/posts/?board=free").data), 50)
        self.assertEqual(len(self.client.get("/api/v1/community/posts/?board=teams&team=lt").data), 30)

    def test_new_posts_lead_the_first_page_in_free_and_team_boards(self):
        free = self.create_post(key="new-free", title="최신 자유 글")
        team = self.create_post(key="new-team", board="teams", teamCode="LG", title="최신 팀 글")
        self.assertEqual((free.status_code, team.status_code), (201, 201))
        self.client.force_authenticate(user=None)

        for path, expected_id in (
            ("/api/v1/community/posts/?board=free", free.data["id"]),
            ("/api/v1/community/posts/?board=teams&team=LG", team.data["id"]),
        ):
            with self.subTest(path=path):
                posts = self.client.get(path).data
                self.assertGreater(len(posts), 20)
                self.assertEqual(posts[0]["id"], expected_id)
                self.assertEqual(
                    [post["postNumber"] for post in posts],
                    sorted((post["postNumber"] for post in posts), reverse=True),
                )

    def test_invalid_filters_and_unauthenticated_writes_are_rejected(self):
        for query in ("?board=other", "?team=XX", "?board=free&team=LG"):
            self.assertEqual(self.client.get(f"/api/v1/community/posts/{query}").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/community/posts/?mine=1").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/community/posts/", {}, format="json").status_code, 401)

    def test_jwt_authenticated_write(self):
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.post(
            "/api/v1/community/posts/",
            {"board": "teams", "teamCode": "lg", "category": "응원", "title": "JWT 글", "content": "인증 본문"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="jwt-create",
        )
        self.assertEqual((response.status_code, response.data["teamCode"], response.data["authorId"]), (201, "LG", self.owner.id))

    def test_create_is_server_owned_idempotent_and_uses_common_sequence(self):
        response = self.create_post(author="위조", postNumber="999999", views=999)
        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.data["authorId"], response.data["author"], response.data["views"]), (self.owner.id, "글쓴이", 0))
        self.assertRegex(response.data["id"], r"^[0-9a-f]{32}$")
        self.assertRegex(response.data["postNumber"], r"^[0-9]{6}$")

        retried = self.create_post(author="다른 위조", postNumber="000001")
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.data["id"], response.data["id"])
        self.assertEqual(CommunityPost.objects.filter(owner=self.owner).count(), 1)

        conflict = self.create_post(title="충돌", key="create-1")
        self.assertEqual(conflict.status_code, 409)
        first_number = int(response.data["postNumber"])
        self.client.delete(f"/api/v1/community/posts/{response.data['id']}/")
        second = self.create_post(key="create-2")
        self.assertEqual(int(second.data["postNumber"]), first_number + 1)

    def test_validation_rejects_invalid_team_category_and_bounded_text(self):
        for changes in (
            {"board": "free", "teamCode": "LG"},
            {"board": "teams", "teamCode": ""},
            {"board": "teams", "teamCode": "XX"},
            {"board": "free", "category": "응원"},
            {"title": " "},
            {"content": " "},
            {"content": "x" * 20001},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.create_post(key=f"invalid-{len(str(changes))}", **changes).status_code, 400)

        for category in ("소식·정보", "이적·신인"):
            response = self.create_post(key=f"category-{category}", board="teams", teamCode="LG", category=category)
            self.assertEqual((response.status_code, response.data["category"]), (201, category))

    def test_detail_counts_views_and_only_owner_can_mutate(self):
        created = self.create_post()
        url = f"/api/v1/community/posts/{created.data['id']}/"
        self.client.force_authenticate(user=None)
        first = self.client.get(url)
        second = self.client.get(url)
        self.assertEqual((first.status_code, first.data["views"], second.data["views"]), (200, 1, 2))
        self.assertEqual(self.client.patch(url, {"title": "익명 탈취"}, format="json").status_code, 401)

        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(url, {"title": "탈취"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(url).status_code, 403)
        self.client.force_authenticate(self.owner)
        updated = self.client.patch(url, {"title": "수정됨"}, format="json")
        self.assertEqual((updated.status_code, updated.data["title"]), (200, "수정됨"))
        self.assertEqual(self.client.delete(url).status_code, 204)

    def test_legacy_post_is_readable_but_not_mutable_and_mine_is_scoped(self):
        created = self.create_post()
        self.client.force_authenticate(self.owner)
        mine = self.client.get("/api/v1/community/posts/?mine=1")
        self.assertEqual([post["id"] for post in mine.data], [created.data["id"]])
        legacy_url = "/api/v1/community/posts/lg-sample-1/"
        self.assertEqual(self.client.get(legacy_url).status_code, 200)
        self.assertEqual(self.client.patch(legacy_url, {"title": "탈취"}, format="json").status_code, 403)


class CommunityRichPostTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="rich-owner")
        self.other = get_user_model().objects.create_user(username="rich-other")
        self.media = tempfile.TemporaryDirectory()
        self.settings = override_settings(MEDIA_ROOT=self.media.name)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.addCleanup(self.media.cleanup)

    @staticmethod
    def image_file():
        buffer = BytesIO()
        Image.new("RGB", (20, 20), "red").save(buffer, format="JPEG")
        return SimpleUploadedFile("photo.jpg", buffer.getvalue(), content_type="image/jpeg")

    @staticmethod
    def document(image_id):
        return {"version": 1, "blocks": [
            {"type": "paragraph", "align": "center", "runs": [{"text": "직관 사진", "font": "serif", "size": 24, "color": "#246bf3", "bold": True, "italic": False, "underline": True}]},
            {"type": "image", "id": image_id},
        ]}

    def test_jwt_upload_and_rich_post_round_trip(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.owner).access_token}")
        uploaded = self.client.post("/api/v1/community/images/", {"image": self.image_file()}, format="multipart")
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual((uploaded.data["width"], uploaded.data["height"]), (20, 20))
        image_id = uploaded.data["id"]
        doc = self.document(image_id)
        created = self.client.post("/api/v1/community/posts/", {"board": "free", "teamCode": "", "category": "잡담", "title": "서식 글", "content": "직관 사진\n[이미지]", "contentDoc": doc}, format="json", HTTP_IDEMPOTENCY_KEY="rich-1")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["contentDoc"], doc)
        retried = self.client.post("/api/v1/community/posts/", {"board": "free", "teamCode": "", "category": "잡담", "title": "서식 글", "content": "직관 사진\n[이미지]", "contentDoc": doc}, format="json", HTTP_IDEMPOTENCY_KEY="rich-1")
        self.assertEqual((retried.status_code, retried.data["id"]), (200, created.data["id"]))
        self.assertEqual(str(CommunityImage.objects.get(pk=image_id).post_id), created.data["id"])
        self.client.credentials()
        fetched = self.client.get(f"/api/v1/community/posts/{created.data['id']}/")
        image = self.client.get(uploaded.data["url"].removeprefix("/api"))
        self.assertEqual((fetched.status_code, image.status_code, image["Content-Type"]), (200, 200, "image/jpeg"))
        self.assertEqual(fetched.data["contentDoc"], doc)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.owner).access_token}")
        edited = self.client.patch(f"/api/v1/community/posts/{created.data['id']}/", {"title": "수정된 서식 글", "content": "직관 사진\n[이미지]", "contentDoc": doc}, format="json")
        self.assertEqual((edited.status_code, edited.data["title"], edited.data["contentDoc"]), (200, "수정된 서식 글", doc))
        self.assertEqual(str(CommunityImage.objects.get(pk=image_id).post_id), created.data["id"])

    def test_spoofed_files_and_foreign_images_are_rejected(self):
        self.assertEqual(self.client.post("/api/v1/community/images/", {"image": self.image_file()}, format="multipart").status_code, 401)
        self.client.force_authenticate(self.owner)
        bad = SimpleUploadedFile("fake.jpg", b"<script>alert(1)</script>", content_type="image/jpeg")
        self.assertEqual(self.client.post("/api/v1/community/images/", {"image": bad}, format="multipart").status_code, 400)
        uploaded = self.client.post("/api/v1/community/images/", {"image": self.image_file()}, format="multipart")
        self.assertEqual(uploaded.status_code, 201)
        self.client.force_authenticate(self.other)
        payload = {"board": "free", "teamCode": "", "category": "잡담", "title": "타인 이미지", "content": "직관 사진\n[이미지]", "contentDoc": self.document(uploaded.data["id"])}
        self.assertEqual(self.client.post("/api/v1/community/posts/", payload, format="json", HTTP_IDEMPOTENCY_KEY="foreign-1").status_code, 400)
        payload["contentDoc"]["blocks"][0]["runs"][0]["font"] = "<script>"
        self.assertEqual(self.client.post("/api/v1/community/posts/", payload, format="json", HTTP_IDEMPOTENCY_KEY="xss-1").status_code, 400)


class CommunityInteractionSchemaTests(APITestCase):
    def test_vote_and_report_are_unique_per_actor_and_post(self):
        user = get_user_model().objects.create_user(username="schema-user")
        post = CommunityPost.objects.get(pk="lg-sample-1")
        CommunityVote.objects.create(post=post, user=user, value="up")
        CommunityReport.objects.create(post=post, reporter=user, reason="spam")
        for model, values in (
            (CommunityVote, {"post": post, "user": user, "value": "down"}),
            (CommunityReport, {"post": post, "reporter": user, "reason": "other"}),
        ):
            with self.subTest(model=model.__name__), self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(**values)


class CommunityPostConcurrencyTests(TransactionTestCase):
    def test_concurrent_same_key_creates_one_post(self):
        user = get_user_model().objects.create_user(username="concurrent")
        payload = {"board": "free", "teamCode": "", "category": "잡담", "title": "동시 글", "content": "한 번만 저장"}
        barrier = Barrier(2)

        def submit():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user)
                barrier.wait()
                response = client.post("/api/v1/community/posts/", payload, format="json", HTTP_IDEMPOTENCY_KEY="same-key")
                return response.status_code, response.data["id"]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))

        self.assertEqual(sorted(code for code, _ in results), [200, 201])
        self.assertEqual(len({source_id for _, source_id in results}), 1)
        self.assertEqual(CommunityPost.objects.filter(owner=user, idempotency_key="same-key").count(), 1)


class CommunitySeedMigrationTests(TransactionTestCase):
    reset_sequences = True

    def test_seed_is_complete_insert_only_and_historical(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("community", "0001_initial")])
        historical_apps = executor.loader.project_state([("community", "0001_initial")]).apps
        HistoricalPost = historical_apps.get_model("community", "CommunityPost")
        HistoricalPost.objects.all().delete()
        HistoricalPost.objects.create(
            source_id="custom-post", post_number="999999", board="free", team_code="", author="사용자",
            title="보존할 글", content="사용자 본문", category="잡담",
        )

        migration = importlib.import_module("community.migrations.0002_seed_community_posts")
        with connection.schema_editor() as schema_editor:
            migration.seed_community_posts(historical_apps, schema_editor)
        HistoricalPost.objects.filter(source_id="free-sample-1").update(title="수정된 샘플")
        with connection.schema_editor() as schema_editor:
            migration.seed_community_posts(historical_apps, schema_editor)

        self.assertEqual(HistoricalPost.objects.count(), 351)
        self.assertEqual(HistoricalPost.objects.filter(board="free", is_sample=True).count(), 50)
        self.assertEqual(HistoricalPost.objects.filter(board="teams", is_sample=True).count(), 300)
        self.assertEqual(set(HistoricalPost.objects.filter(board="teams").values_list("team_code", flat=True)), set(TEAM_CODES))
        self.assertEqual(HistoricalPost.objects.get(source_id="free-sample-1").title, "수정된 샘플")
        self.assertEqual(HistoricalPost.objects.get(source_id="custom-post").content, "사용자 본문")

        executor = MigrationExecutor(connection)
        executor.migrate([("community", "0003_community_post_api")])
        self.assertEqual(CommunityPost.objects.count(), 351)
        executor = MigrationExecutor(connection)
        executor.migrate([("community", "0001_initial")])
        self.assertEqual(CommunityPost.objects.count(), 351)
        executor = MigrationExecutor(connection)
        executor.migrate([("community", "0003_community_post_api")])
        self.assertEqual(CommunityPost.objects.count(), 351)

    def test_versioned_json_matches_all_exported_objects(self):
        records = json.loads(SEED_FILE.read_text())
        self.assertEqual((len(records), sum(post["board"] == "free" for post in records)), (350, 50))
        self.assertEqual(len({post["sourceId"] for post in records}), 350)
        self.assertEqual(len({post["postNumber"] for post in records}), 350)
        self.assertEqual(len({(post["title"], post["content"]) for post in records}), 350)
        self.assertEqual(set(records[0]), {"sourceId", "board", "postNumber", "author", "createdAt", "views", "recommendations", "commentCount", "teamCode", "category", "title", "content", "isSample"})
        self.assertEqual({post["teamCode"] for post in records if post["board"] == "teams"}, set(TEAM_CODES))
        self.assertTrue(all(post["createdAt"] is None and post["views"] == post["recommendations"] == post["commentCount"] == 0 and post["isSample"] for post in records))
