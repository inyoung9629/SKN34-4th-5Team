from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from rest_framework.test import APITestCase


class AdminPermissionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.member = User.objects.create_user(username="member")
        cls.staff = User.objects.create_user(username="staff", is_staff=True)
        cls.master = User.objects.create_user(username="master", is_staff=True, is_superuser=True)
        cls.other_master = User.objects.create_user(username="other-master", is_staff=True, is_superuser=True)

    def change(self, target, payload):
        return self.client.patch(f"/api/v1/auth/admin/members/{target.pk}/role/", payload, format="json")

    def test_anonymous_and_member_cannot_list(self):
        self.assertEqual(self.client.get("/api/v1/auth/admin/members/").status_code, 401)
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get("/api/v1/auth/admin/members/").status_code, 403)

    def test_staff_can_search_but_not_grant(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get("/api/v1/auth/admin/members/?q=member")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertNotIn("password", response.data["results"][0])
        self.assertEqual(self.change(self.member, {"is_staff": True}).status_code, 403)

    def test_master_grant_revoke_and_audit(self):
        self.client.force_authenticate(self.master)
        self.assertEqual(self.change(self.member, {"is_staff": True}).status_code, 200)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_staff)
        self.assertEqual(self.change(self.member, {"is_staff": False}).status_code, 200)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_staff)
        self.assertEqual(LogEntry.objects.filter(user=self.master, object_id=str(self.member.pk)).count(), 2)

    def test_cannot_change_master_or_submit_superuser_flag(self):
        self.client.force_authenticate(self.master)
        for target in (self.master, self.other_master):
            self.assertEqual(self.change(target, {"is_staff": False}).status_code, 403)
        self.assertEqual(self.change(self.member, {"is_staff": True, "is_superuser": True}).status_code, 400)
        self.assertEqual(self.change(self.member, {"is_staff": "false"}).status_code, 400)
        self.assertEqual(self.change(self.member, []).status_code, 400)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_staff)
        self.assertFalse(self.member.is_superuser)

    def test_inactive_target_and_inactive_actor_denied(self):
        self.client.force_authenticate(self.master)
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])
        self.assertEqual(self.change(self.member, {"is_staff": True}).status_code, 400)
        self.master.is_active = False
        self.master.save(update_fields=["is_active"])
        self.assertEqual(self.client.get("/api/v1/auth/admin/members/").status_code, 403)
