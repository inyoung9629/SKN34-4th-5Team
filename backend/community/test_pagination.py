from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APITestCase

from .models import CommunityPost


class CommunityPostPaginationTests(APITestCase):
    def test_legacy_request_still_returns_a_list(self):
        response = self.client.get("/api/v1/community/posts/")

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 352)

    def test_page_boundaries_use_default_page_size(self):
        first = self.client.get("/api/v1/community/posts/?page=1")
        second = self.client.get("/api/v1/community/posts/?page=2")
        last = self.client.get("/api/v1/community/posts/?page=18")
        missing = self.client.get("/api/v1/community/posts/?page=19")

        self.assertEqual((first.status_code, first.data["count"], len(first.data["results"])), (200, 352, 20))
        self.assertEqual((last.status_code, last.data["count"], len(last.data["results"])), (200, 352, 12))
        self.assertEqual(urlsplit(first.data["next"]).path, "/api/v1/community/posts/")
        self.assertEqual(parse_qs(urlsplit(first.data["next"]).query)["page"], ["2"])
        self.assertTrue(
            {post["id"] for post in first.data["results"]}.isdisjoint(
                post["id"] for post in second.data["results"]
            )
        )
        self.assertIsNone(last.data["next"])
        self.assertEqual(missing.status_code, 404)

    def test_invalid_pagination_and_search_values_return_400(self):
        for query in (
            "page=0", "page=-1", "page=01", "page=+1", "page=1.0",
            "page=2147483648", "page_size=0", "page_size=101", "search_field=content",
            f"q={'x' * 201}",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/api/v1/community/posts/?{query}").status_code, 400)

        self.assertEqual(self.client.get(f"/api/v1/community/posts/?q={'x' * 200}").status_code, 200)

    def test_filtered_count_is_computed_before_pagination(self):
        response = self.client.get("/api/v1/community/posts/?board=teams&team=lt&page=1&page_size=7")

        self.assertEqual((response.status_code, response.data["count"], len(response.data["results"])), (200, 30, 7))
        self.assertTrue(all(post["teamCode"] == "LT" for post in response.data["results"]))
        next_link = urlsplit(response.data["next"])
        self.assertEqual(next_link.path, "/api/v1/community/posts/")
        self.assertEqual(parse_qs(next_link.query), {"board": ["teams"], "team": ["lt"], "page": ["2"], "page_size": ["7"]})

        page_size_only = self.client.get("/api/v1/community/posts/?page_size=7")
        empty = self.client.get("/api/v1/community/posts/?q=존재하지않는검색어&page=1")
        self.assertEqual((page_size_only.status_code, len(page_size_only.data["results"])), (200, 7))
        self.assertEqual((empty.status_code, empty.data["count"], empty.data["results"]), (200, 0, []))

    def test_search_is_trimmed_and_applied_before_pagination(self):
        CommunityPost.objects.create(
            source_id="pagination-search",
            post_number="999999",
            board="free",
            team_code="",
            author="검색 작성자",
            title="고유한 검색 제목",
            content="고유한 검색 본문 표식",
            category="잡담",
        )

        response = self.client.get("/api/v1/community/posts/?q=%20본문%20표식%20&page=1&page_size=1")
        author = self.client.get("/api/v1/community/posts/?q=검색%20작성자&search_field=author&page=1")
        title_only = self.client.get("/api/v1/community/posts/?q=검색%20작성자&search_field=title&page=1")

        self.assertEqual((response.status_code, response.data["count"]), (200, 1))
        self.assertEqual(response.data["results"][0]["id"], "pagination-search")
        self.assertEqual((author.status_code, author.data["count"]), (200, 1))
        self.assertEqual((title_only.status_code, title_only.data["count"]), (200, 0))


class CommunityPostPaginationSchemaTests(SimpleTestCase):
    def test_schema_describes_legacy_and_paginated_responses(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema["paths"]["/api/v1/community/posts/"]["get"]
        parameters = {parameter["name"]: parameter["schema"] for parameter in operation["parameters"]}
        response = operation["responses"]["200"]["content"]["application/json"]["schema"]

        self.assertEqual(parameters["page_size"]["maximum"], 100)
        self.assertEqual(parameters["q"]["maxLength"], 200)
        self.assertEqual(set(parameters["search_field"]["enum"]), {"all", "title", "author"})
        self.assertEqual(response["$ref"], "#/components/schemas/CommunityPostListResponse")
        self.assertEqual(len(schema["components"]["schemas"]["CommunityPostListResponse"]["oneOf"]), 2)
