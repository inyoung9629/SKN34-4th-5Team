from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TransactionTestCase
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import CommunityDraft, CommunityImage, CommunityPost


class CommunityDraftPublishTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="publisher", nickname="게시자")
        cls.other = get_user_model().objects.create_user(username="other-publisher")

    def make_draft(self, **changes):
        values = {
            "owner": self.owner,
            "board": "free",
            "team_code": "",
            "category": "잡담",
            "title": "게시할 글",
            "content": "게시할 본문",
        }
        values.update(changes)
        return CommunityDraft.objects.create(**values)

    def publish(self, draft, key="publish-1", user=None):
        self.client.force_authenticate(user or self.owner)
        return self.client.post(
            f"/api/v1/community/drafts/{draft.pk}/publish/",
            {"revision": draft.revision},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def test_jwt_publish_moves_images_and_same_retry_returns_same_post(self):
        draft = self.make_draft()
        image = CommunityImage.objects.create(
            owner=self.owner,
            object_key="community/publish.webp",
            content_type="image/webp",
            size=123,
            width=10,
            height=20,
            draft=draft,
        )
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        created = self.client.post(
            f"/api/v1/community/drafts/{draft.pk}/publish/",
            {"revision": draft.revision},
            format="json",
            HTTP_IDEMPOTENCY_KEY="publish-jwt",
        )
        retried = self.client.post(
            f"/api/v1/community/drafts/{draft.pk}/publish/",
            {"revision": draft.revision},
            format="json",
            HTTP_IDEMPOTENCY_KEY="publish-jwt",
        )

        self.assertEqual((created.status_code, retried.status_code), (201, 200))
        self.assertEqual(created.data["id"], retried.data["id"])
        self.assertEqual(created.data["images"], [{
            "id": str(image.pk),
            "contentType": "image/webp",
            "size": 123,
            "width": 10,
            "height": 20,
        }])
        draft.refresh_from_db()
        image.refresh_from_db()
        self.assertEqual((draft.published_post_id, image.post_id, image.draft_id), (created.data["id"], created.data["id"], None))
        self.assertEqual((draft.title, draft.content), ("게시할 글", "게시할 본문"))
        self.assertEqual(CommunityPost.objects.filter(owner=self.owner).count(), 1)
        self.assertEqual(self.client.get(f"/api/v1/community/drafts/{draft.pk}/").status_code, 404)
        self.assertNotIn(str(draft.pk), [item["id"] for item in self.client.get("/api/v1/community/drafts/").data["results"]])

    def test_draft_patch_attaches_only_owned_available_images_with_revision(self):
        draft = self.make_draft()
        other_draft = self.make_draft(title="다른 초안")
        available = CommunityImage.objects.create(
            owner=self.owner,
            object_key="community/available.webp",
            content_type="image/webp",
            size=10,
            width=2,
            height=3,
        )
        unavailable = CommunityImage.objects.create(
            owner=self.owner,
            object_key="community/unavailable.webp",
            content_type="image/webp",
            size=10,
            width=2,
            height=3,
            draft=other_draft,
        )
        self.client.force_authenticate(self.owner)

        attached = self.client.patch(
            f"/api/v1/community/drafts/{draft.pk}/",
            {"revision": 1, "imageIds": [str(available.pk)]},
            format="json",
        )
        stale = self.client.patch(
            f"/api/v1/community/drafts/{draft.pk}/",
            {"revision": 1, "imageIds": []},
            format="json",
        )
        stolen = self.client.patch(
            f"/api/v1/community/drafts/{draft.pk}/",
            {"revision": 2, "imageIds": [str(unavailable.pk)]},
            format="json",
        )

        self.assertEqual((attached.status_code, attached.data["revision"]), (200, 2))
        self.assertEqual(attached.data["imageIds"], [str(available.pk)])
        self.assertEqual((stale.status_code, stolen.status_code), (409, 400))
        draft.refresh_from_db()
        available.refresh_from_db()
        self.assertEqual((draft.revision, available.draft_id), (2, draft.pk))

    def test_publish_rejects_missing_data_wrong_owner_and_changed_retry(self):
        incomplete = self.make_draft(title="", content="")
        self.assertEqual(self.publish(incomplete).status_code, 400)
        incomplete.refresh_from_db()
        self.assertIsNone(incomplete.published_post_id)

        draft = self.make_draft()
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.post(f"/api/v1/community/drafts/{draft.pk}/publish/").status_code, 401)
        self.assertEqual(self.publish(draft, user=self.other).status_code, 404)
        self.assertEqual(self.publish(draft, key="first-key").status_code, 201)
        self.assertEqual(self.publish(draft, key="changed-key").status_code, 409)
        stale_publish = self.client.post(
            f"/api/v1/community/drafts/{draft.pk}/publish/",
            {"revision": draft.revision + 1},
            format="json",
            HTTP_IDEMPOTENCY_KEY="first-key",
        )
        self.assertEqual((stale_publish.status_code, set(stale_publish.data)), (409, {"revision"}))
        self.assertEqual(
            self.client.post(
                f"/api/v1/community/drafts/{draft.pk}/publish/",
                {"revision": draft.revision, "imageUrl": "https://attacker.invalid/image"},
                format="json",
                HTTP_IDEMPOTENCY_KEY="first-key",
            ).status_code,
            400,
        )

        draft.title = "게시 후 변조"
        draft.save(update_fields=("title",))
        self.assertEqual(self.publish(draft, key="first-key").status_code, 409)
        self.assertEqual(CommunityPost.objects.filter(owner=self.owner).count(), 1)

    def test_failure_rolls_back_post_image_and_draft(self):
        draft = self.make_draft()
        image = CommunityImage.objects.create(
            owner=self.owner,
            object_key="community/rollback.webp",
            content_type="image/webp",
            size=123,
            width=10,
            height=20,
            draft=draft,
        )
        with patch("community.publishing.CommunityDraft.save", side_effect=IntegrityError):
            response = self.publish(draft, key="rollback")

        self.assertEqual(response.status_code, 409)
        draft.refresh_from_db()
        image.refresh_from_db()
        self.assertIsNone(draft.published_post_id)
        self.assertEqual((image.draft_id, image.post_id), (draft.pk, None))
        self.assertFalse(CommunityPost.objects.filter(owner=self.owner).exists())

    def test_image_database_constraint_forbids_two_attachment_targets(self):
        draft = self.make_draft()
        post = CommunityPost.objects.create(
            source_id="double-target-post",
            post_number="999996",
            board="free",
            team_code="",
            owner=self.owner,
            author="게시자",
            title="게시글",
            content="본문",
            category="잡담",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            CommunityImage.objects.create(
                owner=self.owner,
                object_key="community/double-target.webp",
                content_type="image/webp",
                size=10,
                width=2,
                height=3,
                draft=draft,
                post=post,
            )

    def test_deleting_post_removes_consumed_draft_and_prevents_republish(self):
        draft = self.make_draft()
        created = self.publish(draft, key="delete-published")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.delete(f"/api/v1/community/posts/{created.data['id']}/").status_code, 204)
        self.assertFalse(CommunityDraft.objects.filter(pk=draft.pk).exists())
        self.assertEqual(self.publish(draft, key="delete-published").status_code, 404)


class CommunityDraftPublishConcurrencyTests(TransactionTestCase):
    def test_concurrent_same_key_publishes_once(self):
        user = get_user_model().objects.create_user(username="concurrent-publisher")
        draft = CommunityDraft.objects.create(
            owner=user,
            board="free",
            category="잡담",
            title="동시 게시",
            content="한 번만 게시",
        )
        barrier = Barrier(2)

        def submit():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user)
                barrier.wait()
                response = client.post(
                    f"/api/v1/community/drafts/{draft.pk}/publish/",
                    {"revision": draft.revision},
                    format="json",
                    HTTP_IDEMPOTENCY_KEY="concurrent-publish",
                )
                return response.status_code, response.data["id"]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))

        self.assertEqual(sorted(code for code, _ in results), [200, 201])
        self.assertEqual(len({post_id for _, post_id in results}), 1)
        self.assertEqual(CommunityPost.objects.filter(owner=user).count(), 1)
