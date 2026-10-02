"""Offline tests: mock only the local SearXNG HTTP response, no API credentials."""
import dataclasses
import unittest

import httpx

from llm.v2.course.place_search import SearXNGSearch, place_query


def result(url="https://example.com/menu", **changes):
    return {"url": url, "title": "<b>돈카츠</b> 메뉴", "content": "등심 &amp; 안심",
            "engines": ["duckduckgo"], **changes}


class PlaceSearchTests(unittest.TestCase):
    def provider(self, payload=None, *, status=200, headers=None, **options):
        self.requests = []

        def handle(request):
            self.requests.append(request)
            return httpx.Response(status, json=payload, headers=headers)

        return SearXNGSearch(transport=httpx.MockTransport(handle), **options)

    def test_korean_json_one_page_one_call_with_no_paid_fallback(self):
        provider = self.provider({"results": [result()]})
        packet = provider.search("잠실 돈까스 메뉴")
        self.assertEqual(packet.status, "ok")
        self.assertEqual(len(self.requests), 1)
        params = self.requests[0].url.params
        self.assertEqual(params["q"], "잠실 돈까스 메뉴")
        self.assertEqual(params["pageno"], "1")
        self.assertEqual(params["language"], "ko-KR")
        self.assertEqual(params["engines"], "duckduckgo,brave")
        self.assertEqual(params["format"], "json")
        self.assertEqual(packet.paid_search_calls, 0)
        self.assertEqual(packet.model_calls, 0)

    def test_search_text_is_never_full_body_or_verified_evidence(self):
        packet = self.provider({"results": [result(body_read=True)]}).search("식당")
        hit = packet.hits[0]
        self.assertFalse(hit.body_read)
        self.assertEqual(hit.evidence_status, "discovery_only")
        self.assertEqual(hit.title, "돈카츠 메뉴")
        self.assertEqual(hit.snippet, "등심 & 안심")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            hit.body_read = True

    def test_duplicate_fragment_urls_and_result_cap(self):
        packet = self.provider({"results": [result(), result("https://example.com/menu#top"),
                                          result("https://example.com/other")]}).search("식당")
        self.assertEqual(len(packet.hits), 2)
        packet = self.provider({"results": [result(), result("https://example.com/other")]},
                               max_results=1).search("식당")
        self.assertEqual(len(packet.hits), 1)

    def test_non_public_or_credential_links_are_discarded_without_fetching(self):
        urls = ["file:///C:/test", "http://127.0.0.1/", "http://169.254.169.254/",
                "https://localhost./", "http://192.168.1.1/", "http://[::1]/",
                "https://secret@example.com/", "http://internal/", "https://example.com:9000/",
                "http://2130706433/", "javascript:alert(1)", "https://bad.local/", "https://x.com/\n"]
        provider = self.provider({"results": [result(url) for url in urls] + [result()]})
        packet = provider.search("식당")
        self.assertEqual([hit.url for hit in packet.hits], ["https://example.com/menu"])
        self.assertEqual(len(self.requests), 1)

    def test_partial_result_keeps_engine_failure_visible(self):
        packet = self.provider({"results": [result()], "unresponsive_engines": [["brave", "timeout"]]}).search("식당")
        self.assertEqual(packet.status, "partial")
        self.assertEqual(packet.unavailable_engines, ("brave",))
        self.assertEqual(packet.engine_errors, (("brave", "timeout"),))

    def test_empty_is_different_from_upstream_failure(self):
        packet = self.provider({"results": []}).search("식당")
        self.assertEqual(packet.status, "empty")
        packet = self.provider({"results": [], "unresponsive_engines": [["duckduckgo", "blocked"]]}).search("식당")
        self.assertEqual(packet.status, "unavailable")
        self.assertEqual(packet.error, "upstream_search_unavailable")

    def test_http_error_never_retries_or_redirects(self):
        for status in (302, 403, 429, 500):
            with self.subTest(status=status):
                provider = self.provider({}, status=status, headers={"location": "https://example.com/paid"})
                packet = provider.search("식당")
                self.assertEqual(packet.status, "unavailable")
                self.assertEqual(packet.error, f"search_http_{status}")
                self.assertEqual(len(self.requests), 1)

    def test_timeout_and_connection_failure_are_not_no_matches(self):
        for error, reason in ((httpx.ReadTimeout, "search_timeout"),
                              (httpx.ConnectError, "search_connection_failed")):
            requests = []

            def fail(request):
                requests.append(request)
                raise error("test")

            packet = SearXNGSearch(transport=httpx.MockTransport(fail)).search("식당")
            self.assertEqual(packet.error, reason)
            self.assertEqual(packet.status, "unavailable")
            self.assertEqual(len(requests), 1)

    def test_invalid_payloads_and_html_are_rejected(self):
        for payload in ([], {}, {"results": "bad"}, {"results": [], "unresponsive_engines": 3}):
            self.assertEqual(self.provider(payload).search("식당").error, "invalid_search_response")
        packet = self.provider({}, headers={"content-type": "text/html"}).search("식당")
        self.assertEqual(packet.error, "json_disabled_or_invalid_response")
        provider = SearXNGSearch(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=b"{broken", headers={"content-type": "application/json"})))
        self.assertEqual(provider.search("식당").error, "invalid_search_response")

    def test_response_size_is_bounded(self):
        packet = self.provider({"results": [], "large": "x" * 1_000_001}).search("식당")
        self.assertEqual(packet.error, "search_response_too_large")

    def test_missing_or_unknown_engine_is_not_accepted(self):
        packet = self.provider({"results": [result(engines=["paidapi"]),
                                           result(engines="duckduckgo"), None]}).search("식당")
        self.assertEqual(packet.hits, ())

    def test_only_explicit_local_endpoint_is_allowed(self):
        for url in ("https://public.example:8888", "http://127.0.0.1:8888/path", "http://127.0.0.1",
                    "http://key@localhost:8888", "http://localhost:8888/?q=x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                SearXNGSearch(url)

    def test_limits_and_engine_selection(self):
        for values in ({"engines": ["braveapi"]}, {"engines": []}, {"timeout": 0},
                       {"timeout": 31}, {"max_results": 0}, {"max_results": 21}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                SearXNGSearch(**values)

    def test_place_query_only_uses_public_business_fields(self):
        candidate = {"name": "식당", "address": "서울 주소", "user_profile": "PRIVATE"}
        self.assertEqual(place_query(candidate, ["돈카츠", "메뉴"]), "식당 서울 주소 돈카츠 메뉴")
        self.assertNotIn("!", place_query(candidate, ["!google"]))
        with self.assertRaises(ValueError):
            place_query({"name": "식당"})
        with self.assertRaises(ValueError):
            place_query(candidate, ["a", "b", "c", "d"])

    def test_query_overrides_and_empty_input_are_rejected_before_io(self):
        provider = self.provider({"results": []})
        for query in ("", " ", "x" * 401, "!google 식당", ":en 식당", "식당\n메뉴"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                provider.search(query)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
