from datetime import datetime, timezone as datetime_timezone
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import CustomUser, PointTransaction, PointWallet
from accounts.point_policy import SIGNUP, SIGNUP_SOURCE_KEY, grant_definition
from accounts.point_service import grant_points
from community.models import CommunityPost


class PointIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = CustomUser.objects.create_user(username="testuser1", password="password123!")
        self.user2 = CustomUser.objects.create_user(username="testuser2", password="password123!")

    def test_signup_grants_points(self):
        response = self.client.post("/api/v1/auth/signup/", {
            "username": "newuser",
            "password": "Password123!",
            "re_password": "Password123!",
            "nickname": "newuser",
            "email": "newuser@example.com",
            "birth_date": "2000-01-01",
            "first_name": "홍길동",
            "gender": "M",
            "team_code": ""
        }, format="json")
        if response.status_code != 201:
            print(response.data)
        self.assertEqual(response.status_code, 201)
        new_user = CustomUser.objects.get(username="newuser")
        wallet = PointWallet.objects.get(user=new_user)
        self.assertEqual(wallet.balance, 100)
        signup_entry = PointTransaction.objects.get(user=new_user)
        self.assertEqual(
            (signup_entry.source_type, signup_entry.source_key),
            (SIGNUP[0], SIGNUP_SOURCE_KEY),
        )

    def test_login_grants_daily_points(self):
        self.assertEqual(PointWallet.objects.filter(user=self.user).count(), 0)
        
        response = self.client.post("/api/v1/auth/signin", {
            "username": "testuser1",
            "password": "password123!"
        })
        self.assertEqual(response.status_code, 200)
        
        wallet = PointWallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, 10)
        
        # Second login same day should not grant more points
        self.client.post("/api/v1/auth/signin", {
            "username": "testuser1",
            "password": "password123!"
        })
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 10)

    def test_normalized_logins_grant_one_daily_reward(self):
        for username in ("testuser1", " testuser1 ", "\ttestuser1\n"):
            with self.subTest(username=username):
                response = self.client.post("/api/v1/auth/signin", {
                    "username": username,
                    "password": "password123!",
                }, format="json")
                self.assertEqual(response.status_code, 200)
                for token_class, key in ((AccessToken, "access"), (RefreshToken, "refresh")):
                    self.assertEqual(token_class(response.data[key])["user_id"], str(self.user.pk))
                member = self.client.get(
                    "/api/v1/auth/user", HTTP_AUTHORIZATION=f"Bearer {response.data['access']}"
                )
                self.assertEqual(member.status_code, 200)
                self.assertEqual(member.data["id"], self.user.pk)
                refreshed = self.client.post("/api/v1/auth/token/refresh/", {
                    "refresh": response.data["refresh"],
                }, format="json")
                self.assertEqual(refreshed.status_code, 200)
                self.assertEqual(PointWallet.objects.get(user=self.user).balance, 10)
                entry = PointTransaction.objects.get(user=self.user)
                self.assertEqual(entry.source_type, "daily_login")
                self.assertEqual(entry.source_key, f"{timezone.localdate().isoformat()}:{self.user.pk}")

    def test_rejected_logins_do_not_grant_points(self):
        self.user2.is_active = False
        self.user2.save(update_fields=["is_active"])
        for username, password in (
            ("testuser1", "wrong-password"),
            ("missing-user", "password123!"),
            ("testuser2", "password123!"),
        ):
            with self.subTest(username=username):
                response = self.client.post("/api/v1/auth/signin", {
                    "username": username,
                    "password": password,
                }, format="json")
                self.assertEqual(response.status_code, 401)
                self.assertFalse(PointWallet.objects.exists())
                self.assertFalse(PointTransaction.objects.exists())

    def test_login_points_reset_on_korean_calendar_day_boundary(self):
        seoul = ZoneInfo("Asia/Seoul")
        first_day = timezone.localdate(datetime(2025, 1, 1, 14, 59, tzinfo=datetime_timezone.utc), seoul)
        next_day = timezone.localdate(datetime(2025, 1, 1, 15, 1, tzinfo=datetime_timezone.utc), seoul)
        with timezone.override("Asia/Seoul"), patch(
            "accounts.serializers.timezone.localdate",
            side_effect=(first_day, next_day),
        ):
            first = self.client.post("/api/v1/auth/signin", {
                "username": "testuser1",
                "password": "password123!"
            })
            second = self.client.post("/api/v1/auth/signin", {
                "username": "testuser1",
                "password": "password123!"
            })

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(PointWallet.objects.get(user=self.user).balance, 20)
        self.assertEqual(
            PointTransaction.objects.filter(user=self.user, source_type="daily_login").count(),
            2,
        )

    def test_post_creation_grants_points(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/v1/community/posts/", {
            "title": "Test Post",
            "content": "Test Content",
            "board": "free",
            "category": "잡담",
            "teamCode": "",
            "images": []
        }, format="json", HTTP_IDEMPOTENCY_KEY="test-key-1")
        if response.status_code != 201:
            print("Post error:", response.data)
        self.assertEqual(response.status_code, 201)
        
        wallet = PointWallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, 1)

        retry = self.client.post("/api/v1/community/posts/", {
            "title": "Test Post",
            "content": "Test Content",
            "board": "free",
            "category": "잡담",
            "teamCode": "",
            "images": []
        }, format="json", HTTP_IDEMPOTENCY_KEY="test-key-1")
        self.assertEqual(retry.status_code, 200)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 1)
        self.assertEqual(PointTransaction.objects.filter(user=self.user, source_type="post").count(), 1)

        edited = self.client.patch(
            f"/api/v1/community/posts/{response.data['id']}/",
            {"title": "Edited Post"},
            format="json",
        )
        self.assertEqual(edited.status_code, 200)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 1)

    def test_post_read_grants_points(self):
        post = CommunityPost.objects.create(
            owner=self.user2,
            author="testuser2",
            title="Read me",
            content="Content",
            board="free",
            category="잡담",
            team_code="",
            source_id="read-1"
        )
        anonymous = APIClient().get(f"/api/v1/community/posts/{post.pk}/")
        self.assertEqual(anonymous.status_code, 200)
        self.assertFalse(PointWallet.objects.filter(user=self.user).exists())

        self.client.force_authenticate(user=self.user)
        
        # First read by testuser1
        response = self.client.get(f"/api/v1/community/posts/{post.pk}/")
        self.assertEqual(response.status_code, 200)
        wallet = PointWallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, 1)
        
        # Second read by testuser1
        self.client.get(f"/api/v1/community/posts/{post.pk}/")
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 1)  # Balance should not increase
        
        # Read by owner (testuser2)
        self.client.force_authenticate(user=self.user2)
        self.client.get(f"/api/v1/community/posts/{post.pk}/")
        wallet2 = PointWallet.objects.filter(user=self.user2).first()
        self.assertIsNone(wallet2)  # Owner gets no point for their own post

    def test_comment_creation_grants_points(self):
        post = CommunityPost.objects.create(
            owner=self.user2,
            author="testuser2",
            title="Comment me",
            content="Content",
            board="free",
            category="잡담",
            team_code="",
            source_id="comment-1"
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.post(f"/api/v1/community/posts/{post.pk}/comments/", {
            "content": "Test comment"
        })
        self.assertEqual(response.status_code, 201)
        wallet = PointWallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, 1)
        edited = self.client.patch(
            f"/api/v1/community/comments/{response.data['id']}/",
            {"content": "Edited comment"},
            format="json",
        )
        self.assertEqual(edited.status_code, 200)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 1)

    def test_saving_and_editing_a_draft_does_not_grant_points(self):
        self.client.force_authenticate(user=self.user)
        created = self.client.post("/api/v1/community/drafts/", {
            "board": "free",
            "teamCode": "",
            "category": "잡담",
            "title": "Draft",
            "content": "Draft content",
        }, format="json")
        self.assertEqual(created.status_code, 201)
        updated = self.client.patch(
            f"/api/v1/community/drafts/{created.data['id']}/",
            {"revision": created.data["revision"], "title": "Updated draft"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertFalse(PointWallet.objects.filter(user=self.user).exists())

    def test_backfill_signup_points_command(self):
        out = StringIO()
        call_command("backfill_signup_points", dry_run=True, stdout=out)
        self.assertIn("대상 회원:", out.getvalue())
        self.assertIn(f"{self.user.pk}: {self.user.username}", out.getvalue())
        
        call_command("backfill_signup_points")
        self.assertEqual(PointWallet.objects.get(user=self.user).balance, 100)
        self.assertEqual(PointWallet.objects.get(user=self.user2).balance, 100)
        
        # Calling again should be idempotent
        call_command("backfill_signup_points")
        self.assertEqual(PointWallet.objects.get(user=self.user).balance, 100)

    def test_backfill_dry_run_excludes_signup_awarded_users_and_preserves_balance(self):
        wallet, _, created = grant_points(
            user=self.user,
            **grant_definition(SIGNUP, SIGNUP_SOURCE_KEY),
        )
        self.assertTrue(created)
        wallet.balance += 37
        wallet.save(update_fields=("balance", "updated_at"))

        out = StringIO()
        call_command("backfill_signup_points", dry_run=True, stdout=out)

        self.assertNotIn(f"{self.user.pk}: {self.user.username}", out.getvalue())
        self.assertIn(f"{self.user2.pk}: {self.user2.username}", out.getvalue())
        self.assertEqual(PointWallet.objects.get(user=self.user).balance, 137)
        self.assertFalse(PointWallet.objects.filter(user=self.user2).exists())

        call_command("backfill_signup_points")
        self.assertEqual(PointWallet.objects.get(user=self.user).balance, 137)
        self.assertEqual(PointWallet.objects.get(user=self.user2).balance, 100)
        call_command("backfill_signup_points")
        self.assertEqual(PointWallet.objects.get(user=self.user2).balance, 100)

    def test_point_wallet_api(self):
        wallet = PointWallet.objects.create(user=self.user, balance=500)
        PointTransaction.objects.create(
            user=self.user,
            wallet=wallet,
            amount=10,
            transaction_type=PointTransaction.ACTIVITY_EARNED,
            source_type="test",
            source_key="api-entry",
            description="테스트 지급",
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/v1/fantasy/points/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["balance"], 500)
        self.assertEqual(response.data["transactions"][0]["amount"], 10)
        self.assertEqual(response.data["transactions"][0]["description"], "테스트 지급")
