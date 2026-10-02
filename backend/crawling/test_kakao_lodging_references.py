import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import collect_kakao_lodging_references as c


CENTER = {"lat": 37.5121854, "lng": 127.0718513}


def document(ident="123", **overrides):
    return {"id": ident, "place_url": "http://place.map.kakao.com/" + ident,
            "place_name": "DO_NOT_SAVE_NAME", "address_name": "DO_NOT_SAVE_ADDRESS",
            "x": str(CENTER["lng"]), "y": str(CENTER["lat"]),
            "phone": "DO_NOT_SAVE_PHONE", "category_group_code": "AD5",
            "category_name": "숙박 > 호텔", "review": "DO_NOT_SAVE_REVIEW", **overrides}


def payload(rows=None, is_end=True):
    return {"meta": {"is_end": is_end, "total_count": 999, "pageable_count": 45},
            "documents": [document()] if rows is None else rows}


class FakeClient:
    def __init__(self, fail_on=None):
        self.requests_attempted = 0
        self.fail_on = fail_on

    def __call__(self, center, keyword, page):
        self.requests_attempted += 1
        if self.requests_attempted == self.fail_on:
            raise c.CollectionError("KAKAO_HTTP_429")
        return payload()


class KakaoLodgingReferenceTests(unittest.TestCase):
    def test_nine_reviewed_venues(self):
        centers = c.stadium_centers()
        self.assertEqual(tuple(centers), c.STADIUM_CODES)
        self.assertEqual(centers["JAMSIL"], CENTER)
        self.assertEqual(c.MAX_REQUESTS, 135)

    def test_reference_uses_https_and_strips_every_extra_field(self):
        rows, end = c.page_references(payload(), CENTER, "")
        self.assertTrue(end)
        self.assertEqual(rows, [{"place_id": "123", "place_url": "https://place.map.kakao.com/123"}])

    def test_untrusted_url_and_id_rejected(self):
        for ident, url in [("abc", "https://place.map.kakao.com/abc"),
                           ("１２３", "https://place.map.kakao.com/１２３"),
                           ("123", "https://place.map.kakao.com/456"),
                           ("123", "https://place.map.kakao.com.evil.test/123"),
                           ("123", "https://place.map.kakao.com@evil.test/123"),
                           ("123", "https://place.map.kakao.com:443/123"),
                           ("123", "https://place.map.kakao.com/123?key=SECRET"),
                           ("123", "https://place.map.kakao.com/123#SECRET"),
                           ("123", "https://place.map.kakao.com/12\n3"),
                           ("123", "file:///123"), ("123", None)]:
            with self.subTest(url=url), self.assertRaises(c.CollectionError):
                c.canonical_reference(ident, url)

    def test_non_lodging_and_invalid_responses_fail_closed(self):
        bad_pages = [None, {}, payload(is_end="true"), payload([], False),
                     payload([document()] * 16), payload([document(category_group_code="FD6")]),
                     payload([document(y="NaN")]), payload([document(x="181")]),
                     payload([document(x=None)])]
        for page in bad_pages:
            with self.subTest(page=page), self.assertRaises(c.CollectionError):
                c.page_references(page, CENTER, "")

    def test_radius_and_keyword_filters_only_apply_in_memory(self):
        rows = [document("1"), document("2", y="38.0"),
                document("3", category_name="숙박 > 모텔,여관")]
        self.assertEqual(len(c.page_references(payload(rows), CENTER, "")[0]), 2)
        self.assertEqual([r["place_id"] for r in c.page_references(payload(rows), CENTER, "호텔")[0]], ["1"])
        for keyword in ("모텔", "여관", "여인숙"):
            self.assertEqual([r["place_id"] for r in c.page_references(payload(rows), CENTER, keyword)[0]], ["3"])

    def test_page_limits_and_deduplication(self):
        calls = []
        def search(center, keyword, page):
            calls.append((keyword, page))
            return payload(is_end=False)
        refs, capped = c.collect_stadium(CENTER, search)
        self.assertTrue(capped)
        self.assertEqual(len(calls), 15)
        self.assertEqual(len(refs), 1)
        self.assertEqual(max(page for _, page in calls), 3)
        self.assertEqual({keyword for keyword, _ in calls}, set(c.KEYWORDS))
        client = FakeClient()
        self.assertFalse(c.collect_stadium(CENTER, client)[1])
        self.assertEqual(client.requests_attempted, 5)

    def test_persistence_rewhitelists_deduplicates_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refs.jsonl"
            row = {"place_id": "123", "place_url": "https://place.map.kakao.com/123", "name": "SECRET_NAME"}
            self.assertEqual(len(c.write_references(path, [row, row])), 1)
            self.assertNotIn("SECRET_NAME", path.read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                c.write_references(path, [row])

    def test_complete_snapshot_and_no_provider_content_or_metadata_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "snapshot"
            client = FakeClient()
            result = c.save_snapshot(output, c.stadium_centers(), client, progress=lambda _: None)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(result["stadiums"]), 9)
            self.assertEqual(result["api_requests_attempted"], 45)
            self.assertEqual(result["unique_reference_count"], 1)
            self.assertEqual((output / "urls.txt").read_text(encoding="utf-8"), "https://place.map.kakao.com/123\n")
            for path in output.iterdir():
                text = path.read_text(encoding="utf-8")
                for banned in ("DO_NOT_SAVE", "total_count", "pageable_count", "category_name", "place_name", "phone", "review"):
                    self.assertNotIn(banned, text)
                if path.suffix == ".jsonl":
                    for line in text.splitlines():
                        self.assertEqual(set(json.loads(line)), {"place_id", "place_url"})

    def test_failure_preserves_only_completed_stadiums_and_never_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "snapshot"
            client = FakeClient(fail_on=6)
            with self.assertRaisesRegex(c.CollectionError, "KAKAO_HTTP_429"):
                c.save_snapshot(output, c.stadium_centers(), client, progress=lambda _: None)
            result = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "failed")
            self.assertEqual(list(result["stadiums"]), ["JAMSIL"])
            self.assertFalse((output / "GOCHEOK.jsonl").exists())
            self.assertEqual(client.requests_attempted, 6)
            self.assertEqual(result["unique_reference_count"], 1)

    def test_http_network_json_and_size_errors_do_not_leak_keys(self):
        client = c.KakaoClient("SECRET")
        for error, code in [(HTTPError("https://example.test/?key=SECRET", 429, "SECRET", {}, io.BytesIO(b"SECRET")), "KAKAO_HTTP_429"),
                            (URLError("SECRET"), "KAKAO_NETWORK_ERROR")]:
            with patch.object(client._opener, "open", side_effect=error), patch.object(c.time, "sleep"):
                with self.assertRaises(c.CollectionError) as raised:
                    client(CENTER, "", 1)
                self.assertEqual(str(raised.exception), code)
        for raw, code in [(b"SECRET", "KAKAO_INVALID_JSON"), (b"x" * (c.MAX_RESPONSE_BYTES + 1), "RESPONSE_TOO_LARGE")]:
            with patch.object(client._opener, "open", return_value=io.BytesIO(raw)), patch.object(c.time, "sleep"):
                with self.assertRaises(c.CollectionError) as raised:
                    client(CENTER, "", 1)
                self.assertEqual(str(raised.exception), code)

    def test_client_fixed_endpoint_and_bounded_request_parameters(self):
        client = c.KakaoClient("SECRET")
        with patch.object(client._opener, "open", return_value=io.BytesIO(json.dumps(payload()).encode())) as opener:
            client(CENTER, "호텔", 1)
            request = opener.call_args.args[0]
            self.assertTrue(request.full_url.startswith(c.ENDPOINT + "keyword.json?"))
            self.assertNotIn("SECRET", request.full_url)
            self.assertIn("category_group_code=AD5", request.full_url)
            self.assertIn("radius=2500", request.full_url)
            self.assertEqual(request.get_header("Authorization"), "KakaoAK SECRET")
        self.assertIsNone(c.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))
        with patch.object(client._opener, "open") as opener:
            for keyword, page in [("맛집", 1), ("", 4)]:
                with self.assertRaises(c.CollectionError):
                    client(CENTER, keyword, page)
            client.requests_attempted = c.MAX_REQUESTS
            with self.assertRaisesRegex(c.CollectionError, "BUDGET"):
                client(CENTER, "", 1)
            opener.assert_not_called()

    def test_dry_run_never_reads_key_or_calls_network(self):
        with patch.object(c.sys, "argv", ["collector"]), patch.object(c, "load_key") as key, \
                patch.object(c, "KakaoClient") as client, patch("builtins.print"):
            self.assertEqual(c.main(), 0)
            key.assert_not_called()
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
