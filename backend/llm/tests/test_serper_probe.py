"""Offline-only Serper probe tests; fake credentials and mock HTTP transport."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

SPEC = importlib.util.spec_from_file_location("serper_probe", Path(__file__).with_name("probe_serper_50.py"))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class SerperProbeTests(unittest.TestCase):
    def call(self, payload=None, status=200, headers=None, raw=None):
        self.requests = []
        def handle(request):
            self.requests.append(request)
            if raw is not None:
                return httpx.Response(status, content=raw, headers=headers)
            return httpx.Response(status, json=payload, headers=headers)
        with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False) as client:
            return probe.search(client, "fake-test-key", "잠실 메뉴 후기")

    def test_one_call_fixed_endpoint_and_limits(self):
        row = self.call({"organic": [{"link": "https://example.com/a", "title": "<b>메뉴</b>", "snippet": "a &amp; b"}], "credits": 1})
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(str(request.url), probe.ENDPOINT)
        self.assertEqual(request.headers["X-API-KEY"], "fake-test-key")
        self.assertEqual(json.loads(request.content), {"q": "잠실 메뉴 후기", "gl": "kr", "hl": "ko", "num": 10, "page": 1})
        self.assertEqual(row["hits"][0]["title"], "메뉴")
        self.assertFalse(row["hits"][0]["body_read"])
        self.assertEqual(row["hits"][0]["evidence_status"], "discovery_only")
        self.assertEqual(row["credits_reported"], 1)
        self.assertNotIn("fake-test-key", str(row))

    def test_empty_is_not_error(self):
        self.assertEqual(self.call({"organic": [], "credits": 1})["status"], "empty")

    def test_429_not_retried(self):
        row = self.call({"message": "secret"}, status=429, headers={"retry-after": "10"})
        self.assertEqual(row["status"], "api_error")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(row["retry_after"], "10")
        self.assertNotIn("secret", str(row))

    def test_redirect_not_followed(self):
        row = self.call({}, status=302, headers={"location": "https://other.example/"})
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(row["http_status"], 302)

    def test_bad_json_sanitized(self):
        row = self.call(raw=b'bad secret api response')
        self.assertEqual(row["status"], "api_error")
        self.assertNotIn("secret", str(row))

    def test_oversized_body_stops(self):
        self.assertEqual(self.call(raw=b' ' * 1_000_001)["status"], "api_error")

    def test_private_and_duplicate_links_filtered(self):
        row = self.call({"organic": [{"link": u} for u in ("http://127.0.0.1/", "javascript:alert(1)", "https://example.com/a", "https://example.com/a#top")]})
        self.assertEqual(row["organic_count"], 1)
        self.assertIsNone(row["credits_reported"])

    def test_error_payload_not_empty_success(self):
        self.assertEqual(self.call({"message": "Invalid API key"})["status"], "api_error")

    def test_invalid_schema_is_error(self):
        for data in ({"organic": "bad"}, {"credits": True}, {"searchParameters": [1]}):
            self.assertEqual(self.call(data)["status"], "api_error")

    def test_fifty_cases_and_balanced_groups(self):
        prior = json.loads((probe.PRIOR / "manifest.json").read_text(encoding="utf-8"))
        cases = probe.build_cases(prior["candidates"])
        self.assertEqual(len(cases), 50)
        self.assertEqual(probe.Counter(c["kind"] for c in cases), {"cafe": 25, "food": 25})
        self.assertEqual(probe.Counter(c["group"] for c in cases), {"baseline": 30, "menu": 10, "review": 10})
        self.assertEqual([c["query"] for c in cases[:30]], [c["query"] for c in prior["candidates"]])
        manifest = {"cases": cases, "cases_hash": probe.digest(cases)}
        probe.validate(manifest)
        cases[0]["query"] += "changed"
        with self.assertRaises(ValueError):
            probe.validate(manifest)

    def manifest(self):
        prior = json.loads((probe.PRIOR / "manifest.json").read_text(encoding="utf-8"))
        cases = probe.build_cases(prior["candidates"])
        return {"cases": cases, "cases_hash": probe.digest(cases)}

    def test_51_requests_rejected_before_api(self):
        manifest = self.manifest()
        manifest["cases"].append({**manifest["cases"][0], "case_id": "P51"})
        manifest["cases_hash"] = probe.digest(manifest["cases"])
        with self.assertRaises(ValueError):
            probe.validate(manifest)

    def test_completed_job_cannot_spend_again(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"organic": [], "credits": 1})
        with tempfile.TemporaryDirectory() as temporary, \
             patch("dotenv.dotenv_values", return_value={"Serper_API_KEY": "fake-test-key"}), \
             patch.object(probe.httpx, "HTTPTransport", return_value=httpx.MockTransport(handler)), \
             patch.object(probe.time, "sleep"), patch.object(probe, "emit"), patch.object(probe, "summarize"):
            folder = Path(temporary)
            probe.run(folder, self.manifest())
            self.assertEqual(len(requests), 50)
            self.assertEqual(len(probe.read_rows(folder / "attempts.jsonl")), 50)
            with self.assertRaises(FileExistsError):
                probe.run(folder, self.manifest())
            self.assertEqual(len(requests), 50)

    def test_rate_limit_stops_remaining_49_without_retry(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(429)
        with tempfile.TemporaryDirectory() as temporary, \
             patch("dotenv.dotenv_values", return_value={"Serper_API_KEY": "fake-test-key"}), \
             patch.object(probe.httpx, "HTTPTransport", return_value=httpx.MockTransport(handler)), \
             patch.object(probe, "emit"), patch.object(probe, "summarize"):
            folder = Path(temporary)
            probe.run(folder, self.manifest())
            self.assertEqual(len(requests), 1)
            rows = probe.read_rows(folder / "results.jsonl")
            self.assertEqual(probe.Counter(r["status"] for r in rows), {"api_error": 1, "skipped": 49})


if __name__ == "__main__":
    unittest.main()
