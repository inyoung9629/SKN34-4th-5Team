from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from accounts.serializers import TEAM_CODES


class SignupTeamTests(APITestCase):
    def payload(self, username="signupteam"):
        return {
            "username": username, "password": "RiverStone742!Q", "re_password": "RiverStone742!Q",
            "email": "fan@example.test", "first_name": "야구팬", "birth_date": "2000-01-01", "gender": "F",
        }

    def test_all_teams_are_saved_at_signup(self):
        for team_code in sorted(TEAM_CODES):
            with self.subTest(team_code=team_code):
                payload = {**self.payload(f"fan{team_code}"), "team_code": team_code}
                response = self.client.post("/api/v1/auth/signup/", payload, format="json")
                self.assertEqual(response.status_code, 201, response.data)
                user = get_user_model().objects.get(username=payload["username"])
                self.assertEqual(user.team_code, team_code)

    def test_missing_and_empty_team_remain_optional(self):
        for index, extra in enumerate(({}, {"team_code": ""})):
            payload = {**self.payload(f"optional{index}"), **extra}
            self.assertEqual(self.client.post("/api/v1/auth/signup/", payload, format="json").status_code, 201)
            self.assertEqual(get_user_model().objects.get(username=payload["username"]).team_code, "")

    def test_invalid_team_does_not_create_user(self):
        for value in ("XX", "lg", "잠실야구장", None, [], {}, 1):
            with self.subTest(value=value):
                response = self.client.post("/api/v1/auth/signup/", {**self.payload(), "team_code": value}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("team_code", response.data)
                self.assertFalse(get_user_model().objects.filter(username="signupteam").exists())

    def test_selected_team_is_returned_after_login_and_can_be_changed(self):
        payload = {**self.payload(), "team_code": "LG"}
        self.assertEqual(self.client.post("/api/v1/auth/signup/", payload, format="json").status_code, 201)
        login = self.client.post("/api/v1/auth/signin", {"username": payload["username"], "password": payload["password"]}, format="json")
        self.assertEqual(login.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        self.assertEqual(self.client.get("/api/v1/auth/user").data["team_code"], "LG")
        changed = self.client.patch("/api/v1/auth/user", {"team_code": "OB"}, format="json")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.data["team_code"], "OB")

    def test_profile_update_keeps_same_team_validation(self):
        user = get_user_model().objects.create_user(username="existingfan", team_code="LG")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        self.assertEqual(self.client.patch("/api/v1/auth/user", {"team_code": "XX"}, format="json").status_code, 400)
        user.refresh_from_db()
        self.assertEqual(user.team_code, "LG")
        self.assertEqual(self.client.patch("/api/v1/auth/user", {"team_code": ""}, format="json").status_code, 200)
