from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase

from .models import CommunityComment, CommunityPost, CommunityReport


class CommunityAdminApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.member = users.create_user(username="member", nickname="회원", password="pass-1234!")
        cls.reporter = users.create_user(username="reporter", nickname="신고자")
        cls.staff = users.create_user(username="staff", nickname="운영", is_staff=True)
        cls.post = CommunityPost.objects.create(post_number="000001", board="free", author="회원", title="관리 대상 글", content="본문", category="잡담", owner=cls.member)
        cls.report = CommunityReport.objects.create(post=cls.post, reporter=cls.reporter, reason="spam", detail="광고")

    def act(self, **body):
        self.client.force_authenticate(self.staff)
        return self.client.post(f"/api/v1/community/admin/reports/{self.report.id}/action/", body, format="json")

    def test_members_cannot_use_admin_endpoints(self):
        for url in ("/api/v1/community/admin/posts/", "/api/v1/community/admin/reports/"):
            self.assertEqual(self.client.get(url).status_code, 401)
            self.client.force_authenticate(self.member)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.client.force_authenticate(None)
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.delete(f"/api/v1/community/admin/posts/{self.post.source_id}/").status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/community/admin/reports/{self.report.id}/action/", {"action": "delete"}, format="json").status_code, 403)
        self.assertTrue(CommunityPost.objects.filter(pk=self.post.pk).exists())

    def test_staff_lists_and_searches_posts_with_report_counts(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get("/api/v1/community/admin/posts/", {"q": "관리 대상"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual((row["source_id"], row["title"], row["report_count"], row["is_hidden"]), (self.post.source_id, "관리 대상 글", 1, False))

    def test_staff_lists_reports_with_post_owner_and_status(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get("/api/v1/community/admin/reports/")
        self.assertEqual(response.status_code, 200)
        # 마이그레이션이 넣은 샘플 신고도 함께 있으므로 이 테스트의 신고를 id 로 찾는다
        row = next(item for item in response.data["results"] if item["id"] == self.report.id)
        self.assertEqual((row["reporter"], row["status"], row["reason"], row["detail"]), ("reporter", "pending", "spam", "광고"))
        self.assertEqual(row["post"]["source_id"], self.post.source_id)
        self.assertEqual((row["post"]["owner"]["username"], row["post"]["owner"]["nickname"]), ("member", "회원"))

    def test_hold_keeps_post_visible(self):
        response = self.act(action="hold")
        self.assertEqual(response.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual((self.report.status, self.report.handled_by), ("held", self.staff))
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"/api/v1/community/posts/{self.post.source_id}/").status_code, 200)

    def test_post_hide_matches_report_policy_and_preserves_records(self):
        comment = CommunityComment.objects.create(post=self.post, author=self.member, content="댓글")
        self.client.force_authenticate(self.staff)
        url = f"/api/v1/community/admin/posts/{self.post.pk}/"
        self.assertEqual(self.client.patch(url, {"action": "hide"}, format="json").status_code, 200)
        self.report.refresh_from_db()
        handled_at = self.report.handled_at
        self.assertEqual((self.report.status, self.report.handled_by), ("hidden", self.staff))
        self.assertTrue(CommunityComment.objects.filter(pk=comment.pk).exists())
        self.assertEqual(self.client.patch(url, {"action": "hide"}, format="json").status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.handled_at, handled_at)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"/api/v1/community/posts/{self.post.pk}/").status_code, 404)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(f"/api/v1/community/posts/{self.post.pk}/").status_code, 200)

    def test_post_without_reports_can_be_hidden(self):
        post = CommunityPost.objects.create(board="free", author="회원", title="신고 없음", content="본문", category="잡담")
        self.client.force_authenticate(self.staff)
        response = self.client.patch(f"/api/v1/community/admin/posts/{post.pk}/", {"action": "hide"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_hidden"])
        self.assertEqual(response.data["report_count"], 0)

    def test_post_hide_permissions_and_input(self):
        url = f"/api/v1/community/admin/posts/{self.post.pk}/"
        self.assertEqual(self.client.patch(url, {"action": "hide"}, format="json").status_code, 401)
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.patch(url, {"action": "hide"}, format="json").status_code, 403)
        self.client.force_authenticate(self.staff)
        for body in ({}, {"action": "unhide"}, {"action": "hide", "title": "변경"}):
            self.assertEqual(self.client.patch(url, body, format="json").status_code, 400)
        self.assertEqual(self.client.patch("/api/v1/community/admin/posts/missing/", {"action": "hide"}, format="json").status_code, 404)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_hidden)

    def test_hide_removes_post_from_public_views(self):
        self.assertEqual(self.act(action="hide").status_code, 200)
        self.post.refresh_from_db()
        self.report.refresh_from_db()
        self.assertTrue(self.post.is_hidden)
        self.assertEqual(self.report.status, "hidden")
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"/api/v1/community/posts/{self.post.source_id}/").status_code, 404)
        listed = self.client.get("/api/v1/community/posts/", {"q": "관리 대상"}).data
        rows = listed["results"] if isinstance(listed, dict) else listed
        self.assertFalse(any(row["id"] == self.post.source_id for row in rows))
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(f"/api/v1/community/posts/{self.post.source_id}/").status_code, 200)

    def test_unhold_returns_report_to_pending_without_changing_post(self):
        self.assertEqual(self.act(action="hold").status_code, 200)
        response = self.act(action="unhold")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "pending")
        self.report.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual(self.report.status, "pending")
        self.assertIsNone(self.report.handled_at)
        self.assertIsNone(self.report.handled_by)
        self.assertFalse(self.post.is_hidden)
        self.assertEqual(self.act(action="hold").data["status"], "held")

    def test_repeated_unhold_is_idempotent(self):
        self.act(action="hold")
        self.assertEqual(self.act(action="unhold").data["status"], "pending")
        self.assertEqual(self.act(action="unhold").data["status"], "pending")

    def test_hidden_report_cannot_be_held_or_unheld(self):
        self.act(action="hide")
        for action in ("hold", "unhold"):
            self.assertEqual(self.act(action=action).status_code, 400)
        self.post.refresh_from_db()
        self.report.refresh_from_db()
        self.assertTrue(self.post.is_hidden)
        self.assertEqual(self.report.status, "hidden")

    def test_unhold_requires_staff_and_rejects_sanction(self):
        self.assertEqual(self.act(action="unhold", sanction="7d").status_code, 400)
        self.client.force_authenticate(self.member)
        url = f"/api/v1/community/admin/reports/{self.report.id}/action/"
        self.assertEqual(self.client.post(url, {"action": "unhold"}, format="json").status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(url, {"action": "unhold"}, format="json").status_code, 401)

    def test_sanction_only_with_delete(self):
        self.assertEqual(self.act(action="hold", sanction="7d").status_code, 400)

    def test_delete_without_sanction_removes_post_comments_and_reports(self):
        CommunityComment.objects.create(post=self.post, author=self.member, content="댓글")
        response = self.act(action="delete", sanction="none")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CommunityPost.objects.filter(pk=self.post.pk).exists())
        self.assertFalse(CommunityReport.objects.filter(pk=self.report.pk).exists())
        self.assertFalse(CommunityComment.objects.exists())

    def test_delete_with_any_sanction_only_deletes_the_post(self):
        for sanction in ("7d", "30d", "permanent"):
            post = CommunityPost.objects.create(board="free", author="회원", title=f"삭제 {sanction}", content="본문", category="잡담", owner=self.member)
            report = CommunityReport.objects.create(post=post, reporter=self.reporter, reason="abuse")
            self.client.force_authenticate(self.staff)
            response = self.client.post(f"/api/v1/community/admin/reports/{report.id}/action/", {"action": "delete", "sanction": sanction}, format="json")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(CommunityPost.objects.filter(pk=post.pk).exists())
        # 계정에는 아무 제재도 없어서 그대로 로그인할 수 있다
        signin = APIClient().post("/api/v1/auth/signin", {"username": "member", "password": "pass-1234!"}, format="json")
        self.assertEqual(signin.status_code, 200)

    def test_ownerless_and_admin_posts_can_be_deleted(self):
        self.post.owner = None
        self.post.save(update_fields=["owner"])
        self.assertEqual(self.act(action="delete", sanction="7d").status_code, 200)
        self.assertFalse(CommunityPost.objects.filter(pk=self.post.pk).exists())
