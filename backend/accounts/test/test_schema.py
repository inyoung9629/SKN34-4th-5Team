from django.test import SimpleTestCase
from django.urls import include, path
from drf_spectacular.generators import SchemaGenerator


class AuthSchemaTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = SchemaGenerator(patterns=[path("api/v1/auth/", include("accounts.urls"))]).get_schema(request=None, public=True)

    def test_auth_schema_matches_wire_contract(self):
        paths = self.schema["paths"]
        schemas = self.schema["components"]["schemas"]

        self.assertNotIn("content", paths["/api/v1/auth/signup/"]["post"]["responses"]["201"])
        self.assertEqual(schemas["Signup"]["properties"]["team_code"]["type"], "string")
        self.assertNotIn("team_code", schemas["Signup"]["required"])
        self.assertNotIn("content", paths["/api/v1/auth/password"]["post"]["responses"]["200"])
        logout = paths["/api/v1/auth/logout"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(logout, {"type": "object", "properties": {}, "additionalProperties": False})

        role = paths["/api/v1/auth/admin/members/{id}/role/"]["patch"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(role["required"], ["is_staff"])
        self.assertFalse(role["additionalProperties"])
        self.assertEqual(role["properties"], {"is_staff": {"type": "boolean"}})

        member_fields = {
            "id", "username", "email", "first_name", "birth_date", "gender", "is_staff", "is_superuser",
            "is_active", "nickname", "team_code", "avatar", "nickname_changed_at", "notifications", "visibility",
        }
        self.assertEqual(set(schemas["MemberUser"]["properties"]), member_fields)
        self.assertEqual(set(schemas["MemberUser"]["required"]), member_fields)
        self.assertEqual(set(schemas["AdminMember"]["properties"]), {"id", "username", "is_active", "is_staff", "is_superuser", "date_joined"})

        for component, fields in {
            "SignInRequest": ("password",),
            "Signup": ("password", "re_password"),
            "PasswordUpdateRequest": ("current_password", "new_password", "new_password_confirm", "uid", "token", "old_password", "password", "re_password"),
            "LogoutRequest": ("refresh",),
        }.items():
            for field in fields:
                self.assertTrue(schemas[component]["properties"][field]["writeOnly"])

        for endpoint in ("/api/v1/auth/user", "/api/v1/auth/email/request", "/api/v1/auth/email/verify"):
            method = "get" if endpoint.endswith("user") else "post"
            self.assertEqual(paths[endpoint][method]["security"], [{"jwtAuth": []}])
