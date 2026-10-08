from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from .models import CommunityComment, CommunityPost


class MemberActivityTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="activity-owner", nickname="야구팬")
        self.viewer = get_user_model().objects.create_user(username="activity-viewer")
        self.post = CommunityPost.objects.create(
            source_id="activity-free", post_number="900001", owner=self.owner,
            board="free", team_code="", author="야구팬", title="활동 제목", content="본문", category="잡담",
        )
        self.comment = CommunityComment.objects.create(post=self.post, author=self.owner, content="활동 댓글")
        self.authenticate(self.viewer)

    def authenticate(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")

    def public_url(self, member_id=None):
        return f"/api/v1/auth/users/{member_id or self.owner.pk}/public/"

    def activity_urls(self, member_id=None):
        return [f"/api/v1/community/{kind}/?author_id={member_id or self.owner.pk}" for kind in ("posts", "comments")]

    def publish(self):
        self.owner.visibility = {"posts": True}
        self.owner.save(update_fields=["visibility"])

    def test_public_summary_is_minimal_and_defaults_to_private(self):
        response = self.client.get(self.public_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"id": self.owner.pk, "nickname": "야구팬", "activityVisible": False})
        self.owner.nickname = ""
        self.owner.save(update_fields=["nickname"])
        self.assertEqual(self.client.get(self.public_url()).data["nickname"], self.owner.username)

    def test_anonymous_requires_jwt(self):
        self.client.credentials()
        for url in [self.public_url(), *self.activity_urls()]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 401)

    def test_invalid_jwt_is_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        for url in [self.public_url(), *self.activity_urls()]:
            self.assertEqual(self.client.get(url).status_code, 401)

    def test_private_activity_denied_even_when_summary_is_accessible(self):
        for visibility in ({}, {"posts": False}, {"posts": "true"}):
            self.owner.visibility = visibility
            self.owner.save(update_fields=["visibility"])
            for url in self.activity_urls():
                with self.subTest(visibility=visibility, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_owner_can_read_private_activity(self):
        self.authenticate(self.owner)
        self.assertFalse(self.client.get(self.public_url()).data["activityVisible"])
        for url in self.activity_urls():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["count"], 1)

    def test_public_activity_and_comment_contract(self):
        self.publish()
        self.assertTrue(self.client.get(self.public_url()).data["activityVisible"])
        posts, comments = [self.client.get(url) for url in self.activity_urls()]
        self.assertEqual(posts.data["results"][0]["id"], self.post.pk)
        item = comments.data["results"][0]
        self.assertEqual(set(item), {"id", "content", "createdAt", "postId", "postTitle", "board", "teamCode"})
        self.assertEqual((item["id"], item["postId"], item["postTitle"], item["board"], item["teamCode"]),
                         (self.comment.pk, self.post.pk, self.post.title, "free", ""))

    def test_existing_visibility_patch_controls_activity(self):
        self.authenticate(self.owner)
        response = self.client.patch("/api/v1/auth/user", {
            "visibility": {"courses": False, "posts": True, "likes": False},
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.authenticate(self.viewer)
        for url in self.activity_urls():
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_missing_and_inactive_targets_return_404(self):
        for url in [self.public_url(999999), *self.activity_urls(999999)]:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        for url in [self.public_url(), *self.activity_urls()]:
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_invalid_author_and_missing_comment_author(self):
        for kind in ("posts", "comments"):
            for author_id in ("", "0", "-1", "01", "+1", "1.0", "abc", "１", "1e2"):
                with self.subTest(kind=kind, author_id=author_id):
                    response = self.client.get(f"/api/v1/community/{kind}/", {"author_id": author_id})
                    self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/v1/community/comments/").status_code, 400)

    def test_hidden_posts_and_their_comments_excluded_even_for_owner(self):
        self.post.is_hidden = True
        self.post.save(update_fields=["is_hidden"])
        self.publish()
        for user in (self.owner, self.viewer):
            self.authenticate(user)
            for url in self.activity_urls():
                self.assertEqual(self.client.get(url).data["results"], [])

    def test_empty_member_still_has_profile_and_empty_pages(self):
        self.viewer.visibility = {"posts": True}
        self.viewer.save(update_fields=["visibility"])
        self.authenticate(self.owner)
        self.assertEqual(self.client.get(self.public_url(self.viewer.pk)).status_code, 200)
        for url in self.activity_urls(self.viewer.pk):
            self.assertEqual(self.client.get(url).data, {"count": 0, "next": None, "previous": None, "results": []})

    def test_author_filter_combines_with_existing_filters(self):
        self.publish()
        team_post = CommunityPost.objects.create(
            source_id="activity-team", post_number="900002", owner=self.owner,
            board="teams", team_code="LG", author="야구팬", title="팀 검색", content="내용", category="잡담",
        )
        CommunityPost.objects.create(
            source_id="activity-other", post_number="900003", owner=self.viewer,
            board="teams", team_code="LG", author="다른팬", title="팀 검색", content="내용", category="잡담",
        )
        response = self.client.get("/api/v1/community/posts/", {
            "author_id": self.owner.pk, "board": "teams", "team": "LG", "q": "팀 검색", "search_field": "title",
        })
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], team_post.pk)

    def test_pagination_latest_order_and_isolation(self):
        self.publish()
        for number in range(2, 23):
            post = CommunityPost.objects.create(
                source_id=f"activity-{number}", post_number=str(900000 + number), owner=self.owner,
                board="free", team_code="", author="야구팬", title=f"제목 {number}", content="본문", category="잡담",
            )
            CommunityComment.objects.create(post=post, author=self.owner, content=f"댓글 {number}")
        CommunityComment.objects.create(post=self.post, author=self.viewer, content="다른 회원 댓글")
        for url in self.activity_urls():
            first = self.client.get(url)
            self.assertEqual((first.data["count"], len(first.data["results"])), (22, 20))
            self.assertIn(f"author_id={self.owner.pk}", first.data["next"])
            second = self.client.get(first.data["next"])
            self.assertEqual(len(second.data["results"]), 2)
            self.assertIsNone(second.data["next"])
            self.assertTrue({item["id"] for item in first.data["results"]}.isdisjoint(item["id"] for item in second.data["results"]))
            self.assertEqual(self.client.get(url + "&page=3").status_code, 404)
        posts = self.client.get(self.activity_urls()[0]).data["results"]
        self.assertEqual(posts[0]["id"], "activity-22")
        comments = self.client.get(self.activity_urls()[1]).data["results"]
        self.assertEqual(comments[0]["content"], "댓글 22")

    def test_invalid_pagination(self):
        self.publish()
        for url in self.activity_urls():
            for query in ("page=0", "page=2147483648", "page_size=101", "page_size=-1"):
                self.assertEqual(self.client.get(url + "&" + query).status_code, 400)

    def test_unfiltered_posts_remain_public(self):
        self.client.credentials()
        response = self.client.get("/api/v1/community/posts/")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)


class MemberActivitySchemaTests(SimpleTestCase):
    def test_v1_contract_and_public_fields(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        paths = schema["paths"]
        self.assertFalse(any("/api/v2/community/members" in path for path in paths))
        public = paths["/api/v1/auth/users/{member_id}/public/"]["get"]
        self.assertEqual(public["security"], [{"jwtAuth": []}])
        self.assertEqual(set(schema["components"]["schemas"]["PublicMember"]["properties"]), {"id", "nickname", "activityVisible"})
        for path in ("/api/v1/community/posts/", "/api/v1/community/comments/"):
            operation = paths[path]["get"]
            self.assertIn("author_id", {parameter["name"] for parameter in operation["parameters"]})
            self.assertIn("403", operation["responses"])
