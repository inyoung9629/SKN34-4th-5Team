from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.auth_service import AuthService


@override_settings(
    MAILERS={
        "default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}
    }
)
class JWTPasswordRegressionTest(APITestCase):
    password = "InitialSafe9!"
    changed_password = "ChangedSafe8!"
    reset_password = "RecoveredSafe7!"
    protected_url = "/api/v1/chat/sessions/"

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="token-user", email="token@example.test", password=cls.password
        )
        cls.other = User.objects.create_user(
            username="other-user", password=cls.password
        )

    def login(self, user=None, password=None):
        response = self.client.post(
            reverse("login"),
            {
                "username": (user or self.user).username,
                "password": password or self.password,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def authenticate(self, access):
        return self.client.get(
            self.protected_url, HTTP_AUTHORIZATION=f"Bearer {access}"
        )

    def refresh(self, token):
        return self.client.post(
            reverse("token_refresh"), {"refresh": token}, format="json"
        )

    def reset_link(self):
        response = self.client.post(
            reverse("password_request"), {"email": self.user.email}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        link = mail.outbox[0].body.split(": http", 1)[1].strip()
        params = urlparse("http" + link).fragment
        query = parse_qs(params)
        return query["uid"][0], query["token"][0]

    def test_reset_email_uses_configured_origin_and_dynamic_credentials(self):
        for origin in ("http://127.0.0.1:80", "https://public.example.test"):
            with self.subTest(origin=origin), override_settings(
                AUTH_FRONTEND_ORIGIN=origin
            ):
                mail.outbox.clear()
                AuthService.send_reset_email(self.user.email)
                link = mail.outbox[0].body.split(": http", 1)[1].strip()
                query = parse_qs(urlparse("http" + link).fragment)
                uid, token = query["uid"][0], query["token"][0]

                self.assertIn(
                    f"{origin}/login#uid={uid}&token={token}", mail.outbox[0].body
                )
                self.assertEqual(uid, urlsafe_base64_encode(force_bytes(self.user.pk)))
                self.assertTrue(default_token_generator.check_token(self.user, token))

    def change(self, fields, access=None, uid=None, token=None):
        url = reverse("password_reset")
        if uid is not None:
            fields = {**fields, "uid": uid}
        if token is not None:
            fields = {**fields, "token": token}
        return self.client.post(
            url,
            fields,
            format="json",
            **({"HTTP_AUTHORIZATION": f"Bearer {access}"} if access else {}),
        )

    def assert_pair_valid(self, pair):
        self.assertEqual(self.authenticate(pair["access"]).status_code, status.HTTP_200_OK)
        self.assertEqual(self.refresh(pair["refresh"]).status_code, status.HTTP_200_OK)

    def assert_pair_revoked(self, pair):
        checks = (
            ("access", lambda: self.authenticate(pair["access"])),
            ("refresh", lambda: self.refresh(pair["refresh"])),
        )
        for token_type, request in checks:
            with self.subTest(token_type=token_type):
                self.assertEqual(request().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_password_change_revokes_two_sessions_only(self):
        sessions = (self.login(), self.login())
        other = self.login(self.other)

        response = self.change(
            {
                "current_password": self.password,
                "new_password": self.changed_password,
                "new_password_confirm": self.changed_password,
            },
            access=sessions[0]["access"],
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for pair in sessions:
            self.assert_pair_revoked(pair)
        self.assert_pair_valid(other)
        self.assertEqual(
            self.client.post(
                reverse("login"),
                {"username": self.user.username, "password": self.password},
                format="json",
            ).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.assert_pair_valid(self.login(password=self.changed_password))

    def test_reset_link_revokes_two_sessions_and_cannot_be_replayed(self):
        sessions = (self.login(), self.login())
        other = self.login(self.other)
        uid, token = self.reset_link()
        fields = {
            "new_password": self.reset_password,
            "new_password_confirm": self.reset_password,
        }

        response = self.change(fields, uid=uid, token=token)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.change(fields, uid=uid, token=token).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        for pair in sessions:
            self.assert_pair_revoked(pair)
        self.assert_pair_valid(other)
        self.assertEqual(
            self.client.post(
                reverse("login"),
                {"username": self.user.username, "password": self.password},
                format="json",
            ).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.assert_pair_valid(self.login(password=self.reset_password))

    def test_rejected_authenticated_changes_preserve_password_and_tokens(self):
        cases = (
            {
                "current_password": "WrongSafe9!",
                "new_password": self.changed_password,
                "new_password_confirm": self.changed_password,
            },
            {
                "current_password": self.password,
                "new_password": "weak",
                "new_password_confirm": "weak",
            },
            {
                "current_password": self.password,
                "new_password": self.changed_password,
                "new_password_confirm": "MismatchSafe6!",
            },
        )
        for fields in cases:
            with self.subTest(fields=fields):
                pair = self.login()
                self.assertEqual(
                    self.change(fields, access=pair["access"]).status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assert_pair_valid(pair)
                self.user.refresh_from_db()
                self.assertTrue(self.user.check_password(self.password))

    def test_password_change_accepts_canonical_and_legacy_fields(self):
        pair = self.login()
        self.assertEqual(
            self.change(
                {
                    "current_password": self.password,
                    "new_password": self.changed_password,
                    "new_password_confirm": self.changed_password,
                },
                access=pair["access"],
            ).status_code,
            status.HTTP_200_OK,
        )
        pair = self.login(password=self.changed_password)
        self.assertEqual(
            self.change(
                {
                    "old_password": self.changed_password,
                    "password": self.reset_password,
                    "re_password": self.reset_password,
                },
                access=pair["access"],
            ).status_code,
            status.HTTP_200_OK,
        )
        self.assert_pair_valid(self.login(password=self.reset_password))

    def test_invalid_reset_links_preserve_password_and_tokens(self):
        pair = self.login()
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        fields = {"password": self.reset_password, "re_password": self.reset_password}
        cases = ((None, token), (uid, None), ("invalid-uid", token), (uid, "invalid-token"))

        for bad_uid, bad_token in cases:
            with self.subTest(uid=bad_uid, token=bad_token):
                self.assertEqual(
                    self.change(fields, uid=bad_uid, token=bad_token).status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assert_pair_valid(pair)
                self.user.refresh_from_db()
                self.assertTrue(self.user.check_password(self.password))

    def test_expired_reset_link_is_rejected(self):
        pair = self.login()
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        with patch("django.contrib.auth.tokens.PasswordResetTokenGenerator._now", return_value=datetime.now() + timedelta(days=4)):
            response = self.change({"password": self.reset_password, "re_password": self.reset_password}, uid=uid, token=token)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_pair_valid(pair)

    def test_refresh_rejects_malformed_expired_wrong_type_and_missing_hash(self):
        malformed = "not-a-jwt"
        expired = RefreshToken.for_user(self.user)
        expired.set_exp(from_time=timezone.now() - timedelta(days=2))
        access = str(RefreshToken.for_user(self.user).access_token)
        missing_hash = RefreshToken.for_user(self.user)
        missing_hash.payload.pop("hash_password", None)

        for token in (malformed, str(expired), access, str(missing_hash)):
            with self.subTest(token=token):
                self.assertEqual(
                    self.refresh(token).status_code, status.HTTP_401_UNAUTHORIZED
                )

    def test_tokens_for_inactive_or_deleted_users_are_rejected(self):
        cases = ("inactive", "deleted")
        for state in cases:
            with self.subTest(state=state):
                user = get_user_model().objects.create_user(
                    username=f"{state}-user", password=self.password
                )
                pair = self.login(user)
                if state == "inactive":
                    user.is_active = False
                    user.save(update_fields=["is_active"])
                else:
                    user.delete()
                self.assert_pair_revoked(pair)
