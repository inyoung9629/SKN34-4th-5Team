from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import CommunityDraft


def authenticated_client(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


class CommunityDraftApiTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="draft-owner")
        self.other = get_user_model().objects.create_user(username="draft-other")
        self.client = authenticated_client(self.owner)

    def create_draft(self, **changes):
        payload = {
            "board": "free",
            "teamCode": "",
            "category": "잡담",
            "title": "임시 제목",
            "content": "임시 본문",
        }
        payload.update(changes)
        return self.client.post("/api/v1/community/drafts/", payload, format="json")

    def test_authenticated_crud_and_server_owned_owner(self):
        self.assertEqual(APIClient().get("/api/v1/community/drafts/").status_code, 401)
        created = self.create_draft()
        self.assertEqual((created.status_code, created.data["revision"]), (201, 1))
        self.assertNotIn("owner", created.data)
        draft_id = created.data["id"]

        detail = self.client.get(f"/api/v1/community/drafts/{draft_id}/")
        updated = self.client.patch(
            f"/api/v1/community/drafts/{draft_id}/",
            {"revision": 1, "title": "수정 제목"},
            format="json",
        )
        self.assertEqual((detail.status_code, updated.status_code, updated.data["revision"], updated.data["title"]), (200, 200, 2, "수정 제목"))
        self.assertEqual(CommunityDraft.objects.get(pk=draft_id).owner, self.owner)
        self.assertEqual(self.client.delete(f"/api/v1/community/drafts/{draft_id}/").status_code, 204)
        self.assertEqual(self.client.get(f"/api/v1/community/drafts/{draft_id}/").status_code, 404)

    def test_cross_user_access_is_owner_scoped_404(self):
        draft_id = self.create_draft().data["id"]
        other = authenticated_client(self.other)
        self.assertEqual(other.get("/api/v1/community/drafts/").data["count"], 0)
        for method, data in ((other.get, None), (other.patch, {"revision": 1}), (other.delete, None)):
            response = method(f"/api/v1/community/drafts/{draft_id}/", data, format="json") if data is not None else method(f"/api/v1/community/drafts/{draft_id}/")
            self.assertEqual(response.status_code, 404)

    def test_incomplete_drafts_and_existing_board_rules(self):
        for payload in (
            {"board": "free", "teamCode": "", "category": "", "title": "", "content": ""},
            {"board": "teams", "teamCode": "lg", "category": "", "title": "", "content": ""},
        ):
            response = self.client.post("/api/v1/community/drafts/", payload, format="json")
            self.assertEqual(response.status_code, 201)
        self.assertEqual(CommunityDraft.objects.get(board="teams").team_code, "LG")

        whitespace = self.create_draft(title="  작성 중  ", content="첫 줄  \n둘째 줄\n")
        saved = CommunityDraft.objects.get(pk=whitespace.data["id"])
        self.assertEqual((saved.title, saved.content), ("  작성 중  ", "첫 줄  \n둘째 줄\n"))

        invalid = (
            {"board": "free", "teamCode": "LG"},
            {"board": "teams", "teamCode": ""},
            {"board": "other"},
            {"board": "free", "category": "응원"},
            {"board": "teams", "teamCode": "XX"},
            {"board": "free", "title": "x" * 201},
            {"board": "free", "content": "x" * 20001},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/v1/community/drafts/", payload, format="json").status_code, 400)

    def test_unknown_fields_and_stale_revision_are_rejected(self):
        self.assertEqual(self.create_draft(owner=self.other.id).status_code, 400)
        created = self.create_draft()
        url = f"/api/v1/community/drafts/{created.data['id']}/"
        self.assertEqual(self.client.patch(url, {"revision": 1, "owner": self.other.id}, format="json").status_code, 400)
        for revision in (0, True, 1.5, "1"):
            with self.subTest(revision=revision):
                self.assertEqual(self.client.patch(url, {"revision": revision, "title": "bad"}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(url, {"revision": 1, "title": "first"}, format="json").status_code, 200)
        stale = self.client.patch(url, {"revision": 1, "title": "stale"}, format="json")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(CommunityDraft.objects.get(pk=created.data["id"]).title, "first")

    def test_list_page_size_is_bounded(self):
        CommunityDraft.objects.bulk_create(
            [CommunityDraft(owner=self.owner, board="free") for _ in range(101)]
        )
        response = self.client.get("/api/v1/community/drafts/?page_size=1000")
        self.assertEqual((response.status_code, response.data["count"], len(response.data["results"])), (200, 101, 100))


class CommunityDraftConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature("supports_transactions")
    def test_concurrent_same_revision_has_one_winner(self):
        if connection.vendor != "postgresql":
            self.skipTest("실제 동시 UPDATE 검증은 PostgreSQL에서 실행합니다.")
        user = get_user_model().objects.create_user(username="draft-concurrent")
        draft = CommunityDraft.objects.create(owner=user, board="free")
        barrier = Barrier(2)

        def update(title):
            close_old_connections()
            try:
                client = authenticated_client(user)
                barrier.wait()
                response = client.patch(
                    f"/api/v1/community/drafts/{draft.id}/",
                    {"revision": 1, "title": title},
                    format="json",
                )
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ("first", "second")))

        self.assertEqual(sorted(results), [200, 409])
        draft.refresh_from_db()
        self.assertEqual(draft.revision, 2)
