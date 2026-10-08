import json
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TransactionTestCase
from langchain_core.messages import AIMessage, HumanMessage
from rest_framework.test import APIClient

from llm.models import ChatAttachment, ChatAttachmentDeletion, ChatSession
from llm.serializer.message import done_payload, project_history, wire_history
from llm.service import attachments
from llm.service.chat_v2 import _model_history


class AttachmentRepairTests(TransactionTestCase):
    def setUp(self):
        self.guest = uuid.uuid4()
        self.session = ChatSession.objects.create(guest=self.guest)
        self.client = APIClient()
        self.client.cookies["guest_id"] = str(self.guest)
        self.path = f"/api/v2/chat/sessions/{self.session.id}/attachments/"

    def upload(self):
        return self.client.post(self.path, {"file": SimpleUploadedFile("x.txt", b"hello", content_type="text/plain")}, format="multipart")

    def test_non_object_and_invalid_url_payloads_are_400(self):
        for data in (123, "url", ["url"], [["url"]], None, {}, {"url": 123}, {"url": []}, {"url": "https://example.com", "extra": True}):
            with self.subTest(data=data):
                response = self.client.post(self.path, data=json.dumps(data), content_type="application/json")
                self.assertEqual(response.status_code, 400)
        anonymous = APIClient()
        self.assertEqual(anonymous.post(self.path, {"url": "https://example.com/"}, format="json").status_code, 404)
        self.assertEqual(anonymous.post(self.path, {"url": "https://example.com/"}, format="json", HTTP_AUTHORIZATION="Bearer invalid-test-token").status_code, 401)
        self.client.cookies["guest_id"] = str(uuid.uuid4())
        self.assertEqual(self.client.post(self.path, {"url": "https://example.com/"}, format="json").status_code, 404)

    def test_failed_insert_and_failed_compensation_reserve_outside_rollback_then_drain(self):
        with patch.object(attachments.image_storage, "put_object") as put, \
                patch.object(ChatAttachment.objects, "create", side_effect=IntegrityError("injected metadata failure")), \
                patch.object(attachments.image_storage, "delete_object", side_effect=attachments.image_storage.StorageUnavailable("injected storage failure")):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        key = put.call_args.args[0]
        self.assertEqual(ChatAttachment.objects.count(), 0)
        self.assertEqual(list(ChatAttachmentDeletion.objects.values_list("object_key", flat=True)), [key])
        with patch.object(attachments.image_storage, "delete_object", side_effect=attachments.image_storage.StorageUnavailable()):
            attachments.purge_deleted_attachments()
        self.assertTrue(ChatAttachmentDeletion.objects.filter(pk=key).exists())
        with patch.object(attachments.image_storage, "delete_object") as delete:
            attachments.purge_deleted_attachments()
        delete.assert_called_once_with(key)
        self.assertEqual(ChatAttachmentDeletion.objects.count(), 0)

    def test_successful_compensation_preserves_original_database_error_without_outbox(self):
        with patch.object(attachments.image_storage, "put_object") as put, \
                patch.object(ChatAttachment.objects, "create", side_effect=IntegrityError("injected metadata failure")), \
                patch.object(attachments.image_storage, "delete_object") as delete:
            with self.assertRaisesRegex(IntegrityError, "injected metadata failure"):
                self.upload()
        delete.assert_called_once_with(put.call_args.args[0])
        self.assertEqual(ChatAttachment.objects.count(), 0)
        self.assertEqual(ChatAttachmentDeletion.objects.count(), 0)

    def test_outer_commit_failure_compensates_and_keeps_retry(self):
        from django.db import connection
        original_commit = connection.commit
        commits = []

        def fail_first_commit():
            commits.append(True)
            if len(commits) == 1:
                raise IntegrityError("injected commit failure")
            return original_commit()

        with patch.object(attachments.image_storage, "put_object") as put, \
                patch.object(connection, "commit", side_effect=fail_first_commit), \
                patch.object(attachments.image_storage, "delete_object", side_effect=attachments.image_storage.StorageUnavailable()):
            # Reserve uses autocommit after atomic has rolled back, not the failed commit method.
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(ChatAttachment.objects.count(), 0)
        self.assertTrue(ChatAttachmentDeletion.objects.filter(pk=put.call_args.args[0]).exists())

    def test_seven_turn_projection_is_pure_and_http_batch_is_ordered_and_scoped(self):
        foreign = ChatSession.objects.create(guest=uuid.uuid4())
        foreign_row = ChatAttachment.objects.create(session=foreign, kind="text", name="private.txt")
        messages, turns, wire = [], {}, {}
        for i in range(7):
            rows = [ChatAttachment.objects.create(session=self.session, kind="text", name=f"{i}-{j}.txt") for j in range(2)]
            ids = [str(rows[1].id), str(uuid.uuid4()), str(foreign_row.id), str(rows[0].id)]
            messages.extend([HumanMessage(f"q{i}", id=f"h{i}", additional_kwargs={"attachment_ids": ids, "tool_group_ids": ["weather"]}), AIMessage(f"a{i}", id=f"a{i}")])
            turns[f"h{i}"] = {"status": "completed", "answer_id": f"a{i}"}
            wire.update({f"{role}{i}": {"id": i * 2 + j + 1, "created_at": "now", "updated_at": "now"} for j, role in enumerate(("h", "a"))})
        with self.assertNumQueries(0):
            projected = project_history(messages, turns)
            model = _model_history(messages, turns)
            done = done_payload(messages, turns)
        self.assertEqual(len(projected), 14)
        self.assertNotIn("attachments", projected[0])
        self.assertEqual(projected[0]["tool_group_ids"], ["weather"])
        self.assertEqual(done["message_id"], "a6")
        self.assertTrue(all(m.type in {"human", "ai"} for m in model))
        self.assertEqual(model[-2].additional_kwargs["attachment_ids"], messages[-2].additional_kwargs["attachment_ids"])
        with self.assertNumQueries(1):
            history = wire_history(messages, turns, wire, session_id=self.session.id)
        for i, item in enumerate(history[::2]):
            self.assertEqual([row["name"] for row in item["attachments"]], [f"{i}-1.txt", f"{i}-0.txt"])
            self.assertEqual(item["tool_group_ids"], ["weather"])
        with self.assertNumQueries(0):
            empty = wire_history(messages, turns, wire)
        self.assertNotIn("attachments", empty[0])
        with patch("llm.service.chat.ChatThread") as thread:
            thread.return_value.state.return_value = (messages, turns)
            thread.return_value.wire = wire
            # Actual HTTP facade: ownership + attachment batch + feedback, independent of seven turns.
            with self.assertNumQueries(3):
                response = self.client.get(f"/api/v2/chat/sessions/{self.session.id}/messages/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["name"] for row in response.data[0]["attachments"]], ["0-1.txt", "0-0.txt"])
