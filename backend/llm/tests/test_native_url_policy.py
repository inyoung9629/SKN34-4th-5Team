from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from langchain_core.messages import HumanMessage, SystemMessage

from llm.v2.middleware.dynamic_tools import DynamicToolMiddleware


class NativeUrlPolicyTests(SimpleTestCase):
    def request(self, text):
        human = HumanMessage(text)
        return SimpleNamespace(
            state={"decision": {"allowed": True}, "messages": [human]},
            messages=[human], tools=[], system_message=SystemMessage("system"), override=Mock(),
        )

    def test_incidental_url_spans_do_not_supply_reading_intent(self):
        middleware = DynamicToolMiddleware([], {})
        for text in ("홈페이지 https://example.com/check", "홈페이지 https://example.com/readme",
                     "https://example.com/check 홈페이지", "https://example.com/readme 홈페이지"):
            with self.subTest(text=text):
                request, handler = self.request(text), Mock()
                middleware.wrap_model_call(request, handler)
                request.override.assert_called_once_with(tools=[])
                handler.assert_called_once()

    def test_url_only_and_surrounding_explicit_read_still_force_native_search(self):
        middleware = DynamicToolMiddleware([], {})
        for text in ("https://example.com/readme", "https://example.com./check",
                     "read https://example.com/page", "https://example.com/page 확인해줘",
                     "read https://8.8.8.8/a", "read https://[2606:4700:4700::1111]/a",
                     "read https://0x7f.example.com/a", "read https://0177.example.com/a", "read https://bücher.de/a"):
            with self.subTest(text=text):
                request, handler = self.request(text), Mock()
                middleware.wrap_model_call(request, handler)
                self.assertEqual(request.override.call_args.kwargs["tools"], [{"type": "web_search"}])
                self.assertEqual(request.override.call_args.kwargs["tool_choice"], {"type": "web_search"})
                handler.assert_called_once()

    def test_legacy_ipv4_rejects_raw_and_restored_references_without_network(self):
        middleware = DynamicToolMiddleware([], {})
        with patch("socket.getaddrinfo", side_effect=AssertionError("no DNS")), \
                patch("llm.service.attachments.source_text", side_effect=AssertionError("no source")):
            for host in ("0x7f.0.0.1", "0x7f.0x0.0x0.0x1", "0X7F000001", "0177.0.0.1",
                         "127.1", "127.0.1", "2130706433", "0x7f.1.", "0x0a.0.0.1",
                         "0xa9.0xfe.0xa9.0xfe", "0x08080808"):
                for restored in (False, True):
                    with self.subTest(host=host, restored=restored):
                        url = f"http://{host}/a"
                        request, handler = self.request("잠실 첨부" if restored else f"read {url}"), Mock()
                        if restored:
                            request.messages = [HumanMessage(content=[{"type": "text", "text":
                                f"참고 URL (본문을 읽은 자료가 아님): {url}"}])]
                        with self.assertRaisesRegex(ValueError, "public HTTP"):
                            middleware.wrap_model_call(request, handler)
                        request.override.assert_not_called()
                        handler.assert_not_called()

    def test_root_dot_private_hosts_reject_before_model_search(self):
        middleware = DynamicToolMiddleware([], {})
        for host in ("localhost.", "127.0.0.1.", "10.0.0.1.", "192.168.1.1.", "169.254.169.254.",
                     "service.local.", "service.localhost.", "service.internal.", "intranet."):
            with self.subTest(host=host):
                request, handler = self.request(f"read https://{host}/page"), Mock()
                with self.assertRaisesRegex(ValueError, "public HTTP"):
                    middleware.wrap_model_call(request, handler)
                request.override.assert_not_called()
                handler.assert_not_called()
