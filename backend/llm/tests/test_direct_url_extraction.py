import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from llm.service import attachments
from llm.service.chat_runs import Stopped


class NativeOnlySourceTests(SimpleTestCase):
    def test_url_and_image_refuse_before_network_storage_or_cached_body(self):
        for kind in ("url", "image"):
            for cached in ("", "legacy cached article"):
                row = SimpleNamespace(kind=kind, source_url="https://example.com/", extracted_text=cached)
                with patch("socket.getaddrinfo", side_effect=AssertionError("no DNS")), \
                        patch("socket.create_connection", side_effect=AssertionError("no HTTP")), \
                        patch.object(attachments, "read_file") as read, \
                        patch.object(attachments.ChatAttachment.objects, "filter") as cache:
                    with self.assertRaisesRegex(ValueError, "only TXT/MD"):
                        attachments.source_text(row)
                read.assert_not_called()
                cache.assert_not_called()
                self.assertEqual(row.extracted_text, cached)
        for name in ("_public_target", "public_url", "_fetch_url", "_extract_url"):
            self.assertFalse(hasattr(attachments, name))

    def test_text_reads_full_utf8_and_checks_cancellation(self):
        text = "시작 " + "잠실 좌석 " * 10000 + " 끝"
        raw = text.encode()
        row = SimpleNamespace(kind="text", object_key="offline", size=len(raw))
        with patch.object(attachments.image_storage, "get_object", return_value=io.BytesIO(raw)):
            self.assertEqual(attachments.source_text(row), text)
        with patch("llm.service.chat_runs.check_cancelled", side_effect=Stopped()), \
                patch.object(attachments.image_storage, "get_object") as storage:
            with self.assertRaises(Stopped):
                attachments.source_text(row)
        storage.assert_not_called()

    def test_legacy_url_middleware_never_fetches_or_changes_system(self):
        from langchain_core.messages import HumanMessage, SystemMessage
        from llm.v2.middleware.attachment_context import AttachmentContextMiddleware
        row = SimpleNamespace(kind="url", source_url="https://article.example/", name="source",
                              extracted_text="legacy cached body", id="x", session_id="s")
        human = HumanMessage(row.source_url, id="q", additional_kwargs={"attachment_ids": ["x"]})
        with patch.object(attachments, "source_text", side_effect=AssertionError("URL disabled")) as source, \
                patch.object(attachments.ChatAttachment.objects, "filter") as rows:
            rows.return_value.__iter__.return_value = iter([row])
            update = AttachmentContextMiddleware().before_agent({"decision": {"allowed": True},
                         "attachment_session_id": "s", "messages": [human]}, None)
        source.assert_not_called()
        request = MagicMock(state=update, system_message=SystemMessage("system"), messages=[human])
        AttachmentContextMiddleware().wrap_model_call(request, lambda value: value)
        self.assertNotIn("system_message", request.override.call_args.kwargs)
        self.assertIn(human.content, request.override.call_args.kwargs["messages"][0].text)
