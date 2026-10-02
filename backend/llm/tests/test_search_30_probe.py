"""Offline guards for the opt-in comparison; never execute provider calls."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location("search_probe", Path(__file__).with_name("probe_search_30.py"))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def response(actions, *, status="completed"):
    data = {"output": [{"type": "web_search_call", **a} for a in actions],
            "usage": {"input_tokens": 10000, "output_tokens": 100,
                      "input_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 1000}}}
    return SimpleNamespace(model_dump=lambda **kwargs: data, status=status, id="fake-test", model=probe.MODEL)


def search(*urls, status="completed"):
    return {"status": status, "action": {"type": "search", "queries": ["식당 메뉴 후기"],
                                         "sources": [{"url": url} for url in urls]}}


class SearchProbeTests(unittest.TestCase):
    def test_one_search_fee_not_number_of_urls(self):
        row = probe.luna_metadata(response([search("https://example.com/a", "https://example.com/b")]))
        self.assertEqual(row["estimated_search_cost_usd"], .01)
        self.assertEqual(row["observed_url_count"], 2)
        self.assertAlmostEqual(row["estimated_model_cost_usd"], .00199)
        self.assertEqual(row["status"], "ok")
        self.assertFalse(row["body_read"])

    def test_page_open_does_not_add_search_fee(self):
        row = probe.luna_metadata(response([search("https://example.com"),
            {"status": "completed", "action": {"type": "open_page", "url": "https://example.com"}}]))
        self.assertEqual(row["estimated_search_cost_usd"], .01)
        self.assertTrue(row["unexpected_actions"])

    def test_no_result_not_api_error(self):
        self.assertEqual(probe.luna_metadata(response([search()]))["status"], "empty")

    def test_incomplete_tool_is_error_even_if_message_completed(self):
        row = probe.luna_metadata(response([search("https://example.com", status="searching")]))
        self.assertEqual(row["status"], "tool_error")
        self.assertEqual(row["observed_url_count"], 0)

    def test_search_success_separate_from_final_response_status(self):
        row = probe.luna_metadata(response([search("https://example.com")], status="incomplete"))
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["response_status"], "incomplete")

    def test_missing_search_and_private_urls(self):
        self.assertEqual(probe.luna_metadata(response([]))["status"], "tool_error")
        row = probe.luna_metadata(response([search("http://127.0.0.1:8000")]))
        self.assertEqual(row["status"], "empty")

    def test_paid_call_parameters_are_bounded(self):
        client = Mock()
        client.responses.create.return_value = response([search()])
        probe.luna_search(client, "식당 메뉴 후기")
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(kwargs["max_tool_calls"], 1)
        self.assertEqual(kwargs["max_output_tokens"], 768)
        self.assertFalse(kwargs["store"])
        self.assertEqual(client.responses.create.call_count, 1)

    def test_api_error_is_sanitized_and_not_retried(self):
        client = Mock()
        client.responses.create.side_effect = RuntimeError("secret must not escape")
        row = probe.luna_search(client, "식당 메뉴 후기")
        self.assertEqual(row["status"], "api_error")
        self.assertIsNone(row["estimated_cost_usd"])
        self.assertNotIn("secret", str(row))
        self.assertEqual(client.responses.create.call_count, 1)

    def test_blocked_engine_removed_not_retried(self):
        self.assertEqual(probe.blocked_engines([
            ("duckduckgo", "CAPTCHA"), ("brave", "Suspended: too many requests")]),
            {"duckduckgo", "brave"})
        self.assertEqual(probe.blocked_engines([("brave", "timeout")]), set())


if __name__ == "__main__":
    unittest.main()
