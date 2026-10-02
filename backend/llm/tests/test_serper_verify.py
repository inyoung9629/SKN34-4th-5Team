"""Offline evidence/reader/budget guards. Never uses a real key or network."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import probe_public_page as reader
import probe_serper_verify as probe


PLACE = {"name": "테스트카페", "address": "서울특별시 송파구 테스트로 12", "requested_menu": "카페라떼", "goal": "menu"}
TEXT = "테스트카페 서울 송파구 테스트로 12 메뉴 카페라떼 2026.09.01 매장이 조용해요 테이블이 깨끗해요 " + "안내 " * 150


def source(**changes):
    return probe.SourceFinding(source_id="one", identity="match", name_quote="테스트카페",
        address_quote="테스트로 12", kind="menu_listing", menu_state="pass", menu_quote="카페라떼",
        current_menu=True, menu_date=None, menu_date_quote="", observations=[], **changes)


def page(sid="one", url="https://example.com/a", **changes):
    return {"source_id": sid, "url": url, "body_read": True, "body_text": TEXT,
            "visible_text_complete": True, **changes}


def observation(**changes):
    return probe.Observation(**{"aspect": "quietness", "polarity": "positive", "kind": "customer_review",
        "quote": "매장이 조용해요", "published_on": "2026-09-01", "date_quote": "2026.09.01",
        "promotion": "not_disclosed", "context": "general", **changes})


class EvidenceTests(unittest.TestCase):
    def assess(self, f=None, pages=None, p=None):
        return probe.assess(p or PLACE, probe.Extraction(sources=[f or source()], note=""), pages or [page()])

    def test_body_grounded_menu_pass(self):
        self.assertEqual(self.assess()["status"], "pass")

    def test_snippet_only_cannot_pass(self):
        self.assertEqual(self.assess(pages=[page(body_read=False)])["status"], "unknown")

    def test_invented_or_other_branch_address_rejected(self):
        f = source().model_copy(update={"address_quote": "테스트로 13"})
        self.assertEqual(self.assess(f)["reason"], "identity_unverified")

    def test_invented_menu_quote_rejected(self):
        f = source().model_copy(update={"menu_quote": "디카페인라떼"})
        self.assertEqual(self.assess(f)["reason"], "menu_missing")

    def test_review_cannot_prove_menu(self):
        f = source().model_copy(update={"kind": "review"})
        self.assertEqual(self.assess(f)["status"], "unknown")

    def test_missing_menu_is_not_explicit_non_sale(self):
        quote = "메뉴정보 인기 메뉴 보기 싱글레귤러"
        f = source().model_copy(update={"menu_state": "fail", "menu_quote": quote})
        verdict = self.assess(f, [page(body_text=TEXT+quote)])
        self.assertEqual(verdict["status"], "unknown")
        self.assertEqual(verdict["guard_rejections"]["menu_non_sale_not_explicit"], 1)

    def test_explicit_non_sale_and_ambiguous_negations(self):
        quote = "카페라떼는 현재 판매하지 않습니다"
        f = source().model_copy(update={"menu_state": "fail", "menu_quote": quote})
        self.assertEqual(self.assess(f, [page(body_text=TEXT+quote)])["status"], "fail")
        for quote in ("카페라떼 5,000원", "메뉴에 카페라떼가 보이지 않음",
                      "카페라떼 판매 중단 아닙니다", "카페라떼 5,000원. 케이크는 미판매",
                      "카페라떼는 판매 종료되었습니다만 다시 판매합니다"):
            self.assertFalse(probe.explicit_menu_non_sale("카페라떼", quote))

    def test_old_menu_rejected(self):
        f = source().model_copy(update={"menu_date": "2025-01-01", "menu_date_quote": "2025.01.01"})
        self.assertEqual(self.assess(f, [page(body_text=TEXT+"2025.01.01")])["status"], "unknown")

    def test_two_independent_recent_reviews_required(self):
        p = {**PLACE, "goal": "quietness"}
        f = source().model_copy(update={"observations": [observation()]})
        self.assertEqual(self.assess(f, p=p)["status"], "unknown")
        second = f.model_copy(update={"source_id": "two"})
        verdict = probe.assess(p, probe.Extraction(sources=[f, second], note=""), [page(), page("two", "https://example.com/b")])
        self.assertEqual(verdict["status"], "pass")
        verdict = probe.assess(p, probe.Extraction(sources=[f, second], note=""), [page(), page("two", "https://example.com/a")])
        self.assertEqual(verdict["status"], "unknown")

    def test_unknown_dates_ad_platform_summary_and_excerpt_rejected(self):
        p = {**PLACE, "goal": "quietness"}
        for change in ({"published_on": None}, {"promotion": "disclosed"}, {"kind": "platform_summary"}, {"context": "limited"}, {"date_quote": "2026.10.01"}):
            f = source().model_copy(update={"observations": [observation(polarity="negative", **change)]})
            self.assertEqual(self.assess(f, p=p)["status"], "unknown")
        f = source().model_copy(update={"observations": [observation(polarity="negative")]})
        self.assertEqual(self.assess(f, [page(visible_text_complete=False)], p)["status"], "unknown")

    def test_no_web_tools_and_no_retry_in_judge(self):
        response = Mock()
        response.status = "completed"
        response.model = probe.MODEL
        response.output_text = probe.Extraction(sources=[], note="").model_dump_json()
        response.model_dump.return_value = {"usage": {"input_tokens": 1000, "output_tokens": 100}, "output": []}
        client = Mock()
        client.responses.create.return_value = response
        row = probe.luna_judge(client, PLACE, [page()])
        kwargs = client.responses.create.call_args.kwargs
        self.assertTrue(row["ok"])
        self.assertEqual(kwargs["tools"], [])
        self.assertEqual(kwargs["tool_choice"], "none")
        self.assertEqual(kwargs["model"], "gpt-5.6-luna")
        self.assertAlmostEqual(row["cost_usd"], .00032)
        client.responses.create.side_effect = RuntimeError("secret credential")
        row = probe.luna_judge(client, PLACE, [page()])
        self.assertNotIn("secret", str(row))
        self.assertEqual(client.responses.create.call_count, 2)

    def test_canonical_naver_mobile_same_article(self):
        self.assertEqual(probe.canonical("https://m.blog.naver.com/a/123?viewType=pc"),
                         probe.canonical("https://blog.naver.com/PostView.nhn?blogId=a&logNo=123&redirect=Dlog"))

    def test_review_payload_omits_unrequested_menu(self):
        client = Mock()
        client.responses.create.side_effect = RuntimeError("offline only")
        probe.luna_judge(client, {**PLACE, "goal": "quietness"}, [page()])
        payload = json.loads(client.responses.create.call_args.kwargs["input"])
        self.assertIsNone(payload["target"]["requested_menu"])
        self.assertIn("ONLY target.goal", client.responses.create.call_args.kwargs["instructions"])


class ReaderTests(unittest.TestCase):
    def make(self, robots="User-agent: *\nAllow: /", status=200, body=None):
        self.calls = []
        def handle(request):
            self.calls.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text=robots)
            return httpx.Response(status, text=body or ("<html><body>"+TEXT+"</body></html>"), headers={"content-type": "text/html; charset=utf-8"})
        return reader.PublicReader(httpx.Client(transport=httpx.MockTransport(handle)), dns_check=lambda host: True, gap=0)

    def test_private_and_unknown_hosts_denied(self):
        for url in ("http://127.0.0.1/", "https://evil.example/", "https://user:pass@polle.com/a", "https://polle.com.evil.example/a"):
            self.assertFalse(reader.allowed_url(url))

    def test_robots_disallow_no_page_get(self):
        r = self.make("User-agent: *\nDisallow: /")
        self.assertEqual(r.read("https://polle.com/a", ["메뉴"])["status"], "robots_disallowed")
        self.assertEqual(len(self.calls), 1)

    def test_bad_robots_page_is_not_allow(self):
        r = self.make("<html>Login required</html>")
        self.assertEqual(r.read("https://polle.com/a", ["메뉴"])["status"], "robots_unavailable")

    def test_403_disables_host(self):
        r = self.make(status=403)
        self.assertEqual(r.read("https://polle.com/a", ["메뉴"])["status"], "http_403")
        self.assertEqual(r.read("https://polle.com/b", ["메뉴"])["status"], "host_blocked")
        self.assertEqual(len(self.calls), 2)

    def test_no_script_text_no_persistent_body_record(self):
        r = self.make(body="<script>fake menu</script><style>fake review</style><p>"+TEXT+"</p>")
        row = r.read("https://polle.com/a", ["메뉴"])
        self.assertTrue(row["body_read"])
        self.assertNotIn("fake", row["body_text"])
        self.assertNotIn("body_text", probe.clean_page_record(row))

    def test_dns_private_no_http(self):
        r = self.make()
        r.dns_check = lambda host: False
        self.assertFalse(r.read("https://polle.com/a", ["메뉴"])["body_read"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
