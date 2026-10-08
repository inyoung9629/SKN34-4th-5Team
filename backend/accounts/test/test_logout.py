from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


class JWTLogoutTest(APITestCase):
    password = "LogoutForest935!K"

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="logout-user", email="logout@example.test", password=cls.password
        )
        cls.other = User.objects.create_user(username="other-user", password=cls.password)

    def login(self, user=None):
        response = self.client.post(
            reverse("login"),
            {"username": (user or self.user).username, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def get_user(self, access):
        return self.client.get(reverse("auth_user"), HTTP_AUTHORIZATION=f"Bearer {access}")

    def logout(self, refresh, **headers):
        return self.client.post(
            reverse("auth_logout"), {"refresh": refresh}, format="json", **headers
        )

    def refresh(self, refresh):
        return self.client.post(
            reverse("token_refresh"), {"refresh": refresh}, format="json"
        )

    def test_logout_blocks_refresh_but_preserves_access_until_expiry(self):
        self.assertEqual(reverse("auth_logout"), "/api/v1/auth/logout")
        self.assertEqual(reverse("auth_user"), "/api/v1/auth/user")
        pair = self.login()
        self.assertEqual(self.get_user(pair["access"]).json(), {
            "id": self.user.pk, "username": self.user.username, "email": self.user.email,
            "first_name": "", "birth_date": None, "gender": None, "is_staff": False,
            "is_superuser": False, "is_active": True, "nickname": "", "team_code": "",
            "avatar": "", "nickname_changed_at": None,
            "notifications": {"comments": True, "courses": True, "announcements": True},
            "visibility": {"courses": False, "posts": False, "likes": False},
        })

        response = self.logout(pair["refresh"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

        refresh = RefreshToken(pair["refresh"], verify=False)
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists())
        self.assertEqual(self.refresh(pair["refresh"]).status_code, 401)
        self.assertEqual(self.get_user(pair["access"]).status_code, 200)
        access = AccessToken(pair["access"])
        self.assertEqual(access["exp"] - access["iat"], 300)
        with patch("jwt.api_jwt.datetime") as clock:
            clock.now.return_value = datetime.fromtimestamp(access["exp"] + 1, tz=UTC)
            self.assertEqual(self.get_user(pair["access"]).status_code, 401)

    def test_other_logins_and_users_are_not_revoked(self):
        pair = self.login()
        survivors = (self.login(), self.login(self.other))
        self.assertEqual(self.logout(pair["refresh"]).status_code, 200)
        self.assertEqual(self.refresh(pair["refresh"]).status_code, 401)
        for survivor in survivors:
            with self.subTest(user_id=AccessToken(survivor["access"])["user_id"]):
                self.assertEqual(self.get_user(survivor["access"]).status_code, 200)
                self.assertEqual(self.refresh(survivor["refresh"]).status_code, 200)

    def test_logout_ignores_expired_or_malformed_access_header(self):
        pair = self.login()
        expired = AccessToken(pair["access"])
        expired.set_exp(from_time=timezone.now() - timedelta(days=1))
        for header in (f"Bearer {expired}", "Bearer not-a-jwt"):
            with self.subTest(header_type="expired" if header != "Bearer not-a-jwt" else "malformed"):
                refresh = self.login()["refresh"]
                self.assertEqual(self.logout(refresh, HTTP_AUTHORIZATION=header).status_code, 200)
                self.assertEqual(self.refresh(refresh).status_code, 401)

    def test_logout_rejects_missing_blank_and_invalid_body_types(self):
        bodies = ({}, {"refresh": ""}, {"refresh": "  "}, {"refresh": None},
                  {"refresh": []}, {"refresh": {}}, {"refresh": False}, [], "invalid")
        for body in bodies:
            with self.subTest(body=body):
                response = self.client.post(reverse("auth_logout"), body, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIsInstance(response.json(), dict)
        self.assertEqual(self.client.post(
            reverse("auth_logout"), '{"refresh":', content_type="application/json"
        ).status_code, 400)

    def test_logout_rejects_invalid_expired_access_and_replayed_tokens(self):
        pair = self.login()
        expired = RefreshToken(pair["refresh"])
        expired.set_exp(from_time=timezone.now() - timedelta(days=2))
        parts = pair["refresh"].split(".")
        parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        for label, token in (
            ("malformed", "not-a-jwt"), ("forged", ".".join(parts)),
            ("expired", str(expired)), ("access", pair["access"]),
        ):
            with self.subTest(token_type=label):
                response = self.logout(token)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["code"], "token_not_valid")
        self.assertEqual(self.refresh(pair["refresh"]).status_code, 200)
        self.assertEqual(self.logout(pair["refresh"]).status_code, 200)
        self.assertEqual(self.logout(pair["refresh"]).status_code, 401)

    def test_get_user_rejects_missing_and_invalid_access(self):
        response = self.client.get(reverse("auth_user"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "인증이 필요합니다."})
        pair = self.login()
        for token in ("not-a-jwt", pair["refresh"]):
            with self.subTest(token_type="refresh" if token == pair["refresh"] else "malformed"):
                response = self.get_user(token)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["code"], "token_not_valid")

    def test_routes_enforce_http_methods_and_no_trailing_slash(self):
        self.assertEqual(self.client.get(reverse("auth_logout")).status_code, 405)
        self.assertEqual(self.client.post(reverse("auth_user"), {}, format="json").status_code, 405)
        self.assertEqual(self.client.post("/api/v1/auth/logout/", {}, format="json").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/auth/user/").status_code, 404)
