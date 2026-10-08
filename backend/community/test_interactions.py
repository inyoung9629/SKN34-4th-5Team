from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework.test import APIClient, APITestCase

from .models import CommunityComment, CommunityPost, CommunityReport, CommunityVote


def make_post(source_id="interaction-post"):
    return CommunityPost.objects.create(
        source_id=source_id,
        post_number="999998",
        board="free",
        team_code="",
        author="작성자",
        title="상호작용 테스트",
        content="본문",
        category="잡담",
    )


class InteractionApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="owner", nickname="주인")
        cls.other = get_user_model().objects.create_user(username="other")
        cls.post = make_post()

    def authenticate(self, user=None):
        self.client.force_authenticate(user or self.user)

    def test_comments_are_public_but_writes_require_auth_and_owner(self):
        url = f"/api/v1/community/posts/{self.post.pk}/comments/"
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {"content": "댓글"}, format="json").status_code, 401)

        self.authenticate()
        created = self.client.post(url, {"content": "  첫 댓글  "}, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(
            set(created.data),
            {"id", "postId", "authorId", "author", "content", "createdAt", "updatedAt"},
        )
        self.assertEqual(created.data["content"], "첫 댓글")
        self.assertEqual(created.data["author"], "주인")
        self.post.refresh_from_db()
        self.assertEqual((self.post.comment_count, self.post.comments.count()), (1, 1))

        detail_url = f"/api/v1/community/comments/{created.data['id']}/"
        self.authenticate(self.other)
        self.assertEqual(self.client.patch(detail_url, {"content": "탈취"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(detail_url).status_code, 403)

        self.authenticate()
        updated = self.client.patch(detail_url, {"content": "수정 댓글"}, format="json")
        self.assertEqual((updated.status_code, updated.data["content"]), (200, "수정 댓글"))
        self.assertEqual(self.client.delete(detail_url).status_code, 204)
        self.post.refresh_from_db()
        self.assertEqual((self.post.comment_count, self.post.comments.count()), (0, 0))

    def test_comment_order_and_input_validation(self):
        first = CommunityComment.objects.create(post=self.post, author=self.user, content="먼저")
        second = CommunityComment.objects.create(post=self.post, author=self.other, content="나중")
        url = f"/api/v1/community/posts/{self.post.pk}/comments/"

        self.assertEqual([item["id"] for item in self.client.get(url).data], [first.id, second.id])
        self.assertEqual(
            [item["id"] for item in self.client.get(f"{url}?order=newest").data],
            [second.id, first.id],
        )
        self.assertEqual(self.client.get(f"{url}?order=invalid").status_code, 400)
        self.authenticate()
        self.assertEqual(self.client.post(url, {"content": "   "}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"content": "가" * 2001}, format="json").status_code, 400)

    def test_vote_is_a_final_idempotent_state_with_real_counts(self):
        url = f"/api/v1/community/posts/{self.post.pk}/vote/"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.authenticate()

        for desired, expected in (
            ("up", {"vote": "up", "recommendations": 1, "downvotes": 0}),
            ("up", {"vote": "up", "recommendations": 1, "downvotes": 0}),
            ("down", {"vote": "down", "recommendations": 0, "downvotes": 1}),
            (None, {"vote": None, "recommendations": 0, "downvotes": 0}),
        ):
            response = self.client.post(url, {"vote": desired}, format="json")
            self.assertEqual((response.status_code, response.data), (200, expected))

        self.assertFalse(CommunityVote.objects.filter(post=self.post, user=self.user).exists())
        self.post.refresh_from_db()
        self.assertEqual(self.post.recommendations, 0)
        self.assertEqual(self.client.post(url, {"vote": "sideways"}, format="json").status_code, 400)

    def test_vote_get_includes_other_users_in_counts(self):
        CommunityVote.objects.create(post=self.post, user=self.other, value="down")
        self.authenticate()
        response = self.client.get(f"/api/v1/community/posts/{self.post.pk}/vote/")
        self.assertEqual(response.data, {"vote": None, "recommendations": 0, "downvotes": 1})

    def test_reports_validate_and_deduplicate_without_public_reads(self):
        url = f"/api/v1/community/posts/{self.post.pk}/reports/"
        payload = {"reason": "spam", "detail": "반복 광고"}
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 401)
        self.authenticate()

        created = self.client.post(url, payload, format="json")
        duplicate = self.client.post(url, {"reason": "abuse", "detail": "다른 내용"}, format="json")
        self.assertEqual((created.status_code, created.data["created"]), (201, True))
        self.assertEqual((duplicate.status_code, duplicate.data), (200, {"id": created.data["id"], "created": False}))
        self.assertEqual(CommunityReport.objects.filter(post=self.post, reporter=self.user).count(), 1)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"reason": "unknown", "detail": ""}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"reason": "other", "detail": "가" * 51}, format="json").status_code, 400)


class ConcurrentVoteTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_vote_transition_keeps_one_row_and_one_count(self):
        user = get_user_model().objects.create_user(username="concurrent")
        post = make_post("concurrent-post")
        CommunityVote.objects.create(post=post, user=user, value="down")
        barrier = Barrier(2)

        def vote():
            close_old_connections()
            client = APIClient()
            client.force_authenticate(get_user_model().objects.get(pk=user.pk))
            barrier.wait()
            response = client.post(
                f"/api/v1/community/posts/{post.pk}/vote/", {"vote": "up"}, format="json"
            )
            close_old_connections()
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: vote(), range(2)))

        self.assertEqual(statuses, [200, 200])
        self.assertEqual(CommunityVote.objects.filter(post=post, user=user, value="up").count(), 1)
        post.refresh_from_db()
        self.assertEqual(post.recommendations, 1)
