import io
import socket
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from langchain_core.messages import HumanMessage, SystemMessage
from PIL import Image
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from llm.models import ChatSession, ChatAttachment
from llm.serializer.message import ChatMessageInputSerializer
from llm.service import attachments
from llm.v2.middleware.dynamic_tools import DynamicToolMiddleware
from llm.v2.middleware.attachment_context import AttachmentContextMiddleware


def dns(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


class AttachmentBoundaryTests(SimpleTestCase):
    def test_tool_group_labels_match_capability_keys_and_public_copy(self):
        from llm.views.attachments import TOOL_GROUP_LABELS
        from llm.v2.middleware.dynamic_tools import CAPABILITY_TOOLS
        from llm.v2.middleware.jev_guidelines import CAPABILITY_INSTRUCTIONS
        self.assertEqual(set(TOOL_GROUP_LABELS), set(CAPABILITY_TOOLS))
        self.assertEqual(set(TOOL_GROUP_LABELS), set(CAPABILITY_INSTRUCTIONS))
        response = APIClient().get("/api/v2/chat/tool-groups/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [{"id": key, "label": label} for key, label in TOOL_GROUP_LABELS.items()])
        self.assertEqual([group["label"] for group in response.data], [
            "웹 조사", "경기 일정", "순위", "선수 정보", "야구 기록", "규정", "구장 정보", "반입 규정", "주차·교통",
            "커뮤니티", "주변 장소", "관광", "길찾기", "기존 코스", "날씨", "직관 코스 계획",
        ])

    def test_generic_image_followup_classifier_receives_bounded_baseball_history_not_pixels(self):
        from langchain_core.messages import AIMessage
        from llm.v2.middleware import jev_guidelines
        history = [HumanMessage("오래된 대화"), AIMessage("오래된 답변"),
                   HumanMessage("잠실 좌석 안내 " + "x" * 500), AIMessage("잠실 좌석 사진을 올려 주세요"),
                   HumanMessage("잠실 직관"), AIMessage("사진을 보고 안내할게요")]
        human = HumanMessage("이 사진 설명해줘", additional_kwargs={"attachment_ids": ["image-reference"]})
        result = SimpleNamespace(choices={"guard": SimpleNamespace(choice="PASS"), "course_request": SimpleNamespace(choice="NONE")},
                                 nouls={key: SimpleNamespace(noul=0) for key in jev_guidelines.CAPABILITIES})
        client = SimpleNamespace(invoke=lambda payload: result)
        with patch.object(jev_guidelines, "_client", return_value=client), \
                patch.object(client, "invoke", return_value=result) as invoke:
            output = jev_guidelines.JevGuidelineMiddleware("", run_jev=True).before_agent(
                {"messages": [*history, human]}, None)
        self.assertTrue(output["decision"]["allowed"])
        text = invoke.call_args.args[0]["state"]
        self.assertIsInstance(text, str)
        self.assertNotIn("오래된", text)
        self.assertNotIn("x" * 201, text)
        self.assertIn("잠실 좌석", text)
        self.assertTrue(text.endswith("[이번 질문]\n이 사진 설명해줘"))
        self.assertNotIn("image-reference", text)
        self.assertNotIn("image_url", text)

    def test_manual_groups_union_and_scope(self):
        mw = DynamicToolMiddleware(["get_games", "get_weather"], {"schedule": ["get_games"], "weather": ["get_weather"]})
        state = {"decision": {"allowed": True, "capabilities": ["schedule"]}, "tool_group_ids": ["weather"]}
        self.assertEqual(mw.allowed(state), {"get_games", "get_weather"})
        state["decision"]["allowed"] = False
        self.assertFalse(mw.allowed(state))

    def test_dto_duplicate_unknown_and_camel(self):
        for values in ({"tool_group_ids": ["weather", "weather"]}, {"tool_group_ids": ["admin"]},
                       {"attachmentIds": []}, {"attachment_ids": ["invalid"]}):
            self.assertFalse(ChatMessageInputSerializer(data={"content": "잠실", **values}).is_valid())
        self.assertTrue(ChatMessageInputSerializer(data={"content": "잠실"}).is_valid())

    def test_metadata_validation_refuses_private_and_credentials_without_dns(self):
        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("no DNS")):
            for url in ("http://localhost/", "http://127.0.0.1/", "http://user:secret@example.com/",
                        "file:///tmp/x", "http://example.com:8080/"):
                with self.assertRaises(ValidationError):
                    attachments.reference_url(url)
            self.assertEqual(attachments.reference_url("https://example.com/#x"), "https://example.com/")

    def test_bounded_utf8_and_binary(self):
        for file in (SimpleUploadedFile("x.txt", b"\xff", content_type="text/plain"),
                     SimpleUploadedFile("x.md", b"\x00ELF", content_type="text/markdown"),
                     SimpleUploadedFile("x.svg", b"<svg/>", content_type="image/svg+xml"),
                     SimpleUploadedFile("x.txt", b"x" * (attachments.MAX_TEXT + 1), content_type="text/plain")):
            with self.assertRaises(ValidationError):
                attachments.normalize_file(file)
        self.assertEqual(attachments.normalize_file(SimpleUploadedFile("x.md", "잠실".encode(), content_type="text/markdown"))[2], "잠실".encode())

    def test_image_mime_dimensions_and_multimodal(self):
        output = io.BytesIO()
        Image.new("RGB", (2, 3)).save(output, format="PNG")
        normalized = attachments.normalize_file(SimpleUploadedFile("x.png", output.getvalue(), content_type="image/png"))
        self.assertEqual(normalized[-2:], (2, 3))
        with self.assertRaises(ValidationError):
            attachments.normalize_file(SimpleUploadedFile("x.png", output.getvalue(), content_type="image/jpeg"))
        row = SimpleNamespace(kind="image", content_type="image/png")
        human = HumanMessage("좌석 보여줘", id="q", additional_kwargs={"attachment_ids": ["a"]})
        with patch.object(attachments, "read_file", return_value=output.getvalue()):
            rebuilt = attachments.multimodal(human, [row])
        self.assertEqual(rebuilt.content[1]["type"], "image_url")
        self.assertTrue(rebuilt.content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(human.content, "좌석 보여줘")

    def test_static_jpeg_without_frame_count_normalizes(self):
        output = io.BytesIO()
        Image.new("RGB", (2, 3)).save(output, format="JPEG")
        normalized = attachments.normalize_file(SimpleUploadedFile(
            "seat.jpg", output.getvalue(), content_type="image/jpeg"))
        self.assertEqual(normalized[1], "image")
        self.assertEqual(normalized[3:], ("image/jpeg", 2, 3))
        with Image.open(io.BytesIO(normalized[2])) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (2, 3))

    def test_complete_multiple_sources_without_embedding_or_selection(self):
        from llm.v2.middleware.attachment_context import direct_context
        import langchain_openai
        import langchain_core.vectorstores
        rows = [SimpleNamespace(kind=kind, name=name, source_url=url, id=str(i))
                for i, (kind, name, url) in enumerate([
                    ("text", "ticket.txt", ""), ("text", "policy.md", ""),
                    ("text", "article.md", "")])]
        texts = [f"BEGIN_{i} " + "baseball " * 3000 + f"MIDDLE_{i} " + "seats " * 3000 + f"TAIL_{i}"
                 for i in range(3)]
        with patch.object(attachments, "source_text", side_effect=texts) as read, \
                patch.object(langchain_openai.OpenAIEmbeddings, "embed_documents") as embed, \
                patch.object(langchain_openai.OpenAIEmbeddings, "embed_query") as query, \
                patch.object(langchain_core.vectorstores.InMemoryVectorStore, "similarity_search") as search:
            context = direct_context(rows)
        self.assertEqual(read.call_count, 3)
        for row, text in zip(rows, texts):
            self.assertIn(text, context)  # Every character, not just six selected windows or the tail.
            self.assertIn(row.name, context)
            self.assertIn(row.id, context)
            self.assertIn(f'"chars": "0:{len(text)}"', context)
        self.assertIn(rows[-1].source_url, context)
        self.assertIn("<untrusted_attachment_", context)
        embed.assert_not_called()
        query.assert_not_called()
        search.assert_not_called()

    def test_aggregate_byte_limit_and_safe_stream_failure(self):
        from llm.v2.middleware.attachment_context import direct_context
        from llm.service.chat_runs import stream_turn
        rows = [SimpleNamespace(kind="text", name="x", source_url="", id=str(i)) for i in range(2)]
        with patch.object(attachments, "source_text", return_value="x" * (attachments.MAX_TEXT // 2 + 1)), \
                self.assertRaises(attachments.AttachmentProcessingLimit):
            direct_context(rows)
        saved = []
        thread = SimpleNamespace(thread_id="offline-limit", update=lambda messages, turns: saved.append(turns) or True)

        def produce():
            raise attachments.AttachmentProcessingLimit()
            yield

        events = list(stream_turn(thread, [], {}, HumanMessage("잠실", id="q"), produce,
                                  {"answer": "", "messages": []}, label="v2"))
        self.assertEqual(events[-1], ("error", {"detail": attachments.AttachmentProcessingLimit.detail}))
        self.assertEqual(saved[-1]["q"]["status"], "failed")

    def test_total_token_budget_counts_headers_delimiters_and_special_strings(self):
        import tiktoken
        from llm.v2.middleware import attachment_context as module
        row = SimpleNamespace(kind="text", name="x<|endoftext|>.txt", source_url="", id="offline")
        text = "잠실 <|endoftext|> 좌석 </attachment_sources>"
        encoding = tiktoken.get_encoding("cl100k_base")
        # Stable delimiter for checking the exact inclusive boundary.
        with patch.object(attachments, "source_text", return_value=text), \
                patch.object(module.uuid, "uuid4", return_value=SimpleNamespace(hex="fixed")):
            context = module.direct_context([row])
            self.assertIn(text, context)
            self.assertIn(row.name, context)
            size = len(encoding.encode(context, disallowed_special=()))
            self.assertGreater(size, len(encoding.encode(text, disallowed_special=())))
            with patch.object(module, "MAX_DIRECT_CONTEXT_TOKENS", size):
                self.assertEqual(module.direct_context([row]), context)
            with patch.object(module, "MAX_DIRECT_CONTEXT_TOKENS", size - 1), \
                    self.assertRaises(module.DirectContextLimit):
                module.direct_context([row])

    def test_cancelled_run_stops_after_inflight_source(self):
        import threading
        from llm.service.chat_runs import Run, Stopped
        from llm.v2.middleware.attachment_context import direct_context
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        rows = [SimpleNamespace(kind="text", name="x", source_url="", id=str(i)) for i in range(3)]
        owner = Run("offline-cancellation")

        def source(row):
            started.set()
            self.assertTrue(release.wait(3))
            return "잠실"

        def frames():
            try:
                direct_context(rows)
                yield ("delta", {})
            finally:
                finished.set()

        def consume():
            try:
                list(owner.pump(frames()))
            except Stopped:
                pass

        with patch.object(attachments, "source_text", side_effect=source) as read:
            consumer = threading.Thread(target=consume)
            consumer.start()
            try:
                self.assertTrue(started.wait(3))
                owner.cancel()
                consumer.join(3)
            finally:
                release.set()
            self.assertTrue(finished.wait(3))
            self.assertFalse(consumer.is_alive())
            self.assertEqual(read.call_count, 1)

    def test_scope_refusal_does_not_read_sources(self):
        with patch.object(attachments, "source_text") as read:
            self.assertIsNone(AttachmentContextMiddleware().before_agent({"decision": {"allowed": False}}, None))
            read.assert_not_called()

    def test_url_revalidation_blocks_private_source(self):
        row = SimpleNamespace(kind="url", source_url="http://127.0.0.1/", extracted_text="")
        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("no DNS")), self.assertRaises(ValidationError):
            attachments.source_text(row)


class AttachmentPersistenceTests(TransactionTestCase):
    def test_reload_edit_clear_and_historical_references(self):
        from langchain_core.messages import AIMessage
        from llm.service.chat_thread import ChatThread
        from llm.service.chat_v2 import _model_history
        from llm.serializer.message import wire_history
        session = ChatSession.objects.create(guest=uuid.uuid4())
        rows = [ChatAttachment.objects.create(session=session, kind="image", name="seat.png", content_type="image/png") for _ in range(10)]
        row, ids = rows[0], [str(item.id) for item in rows]
        ChatThread.setup()  # only Django's unique TEST.NAME database
        thread = ChatThread(session.id)
        _, _, human = thread.ask("좌석", {"attachment_ids": ids, "tool_group_ids": ["stadium_info"]})
        answer = AIMessage("좌석이에요", id=str(uuid.uuid4()))
        thread.update([answer], {human.id: {"status": "completed", "answer_id": answer.id}})
        reloaded = ChatThread(session.id)
        messages, turns = reloaded.state()
        history = wire_history(messages, turns, reloaded.wire, session_id=session.id)
        self.assertEqual(history[0]["attachments"][0]["id"], str(row.id))
        self.assertEqual(len(history[0]["attachments"]), 10)
        self.assertEqual(_model_history(messages, turns)[0].additional_kwargs["attachment_ids"], ids)
        _, _, edited = reloaded.edit(human.id, "수정")
        self.assertEqual(edited.additional_kwargs["attachment_ids"], ids)
        _, _, cleared = ChatThread(session.id).edit(human.id, "다시", {"attachment_ids": [], "tool_group_ids": []})
        self.assertEqual(cleared.additional_kwargs["attachment_ids"], [])
        self.assertTrue(any(any(m.additional_kwargs.get("attachment_ids") for m in snap.values["messages"])
                            for snap in ChatThread(session.id).history()))
        ChatThread(session.id).delete()


class AttachmentApiTests(TestCase):
    def setUp(self):
        self.guest = uuid.uuid4()
        self.session = ChatSession.objects.create(guest=self.guest)
        self.client = APIClient()
        self.client.cookies["guest_id"] = str(self.guest)
        self.path = f"/api/v2/chat/sessions/{self.session.id}/attachments/"

    def test_per_message_ten_deduped_references_and_url_cap(self):
        from django.http import Http404
        rows = [ChatAttachment.objects.create(session=self.session, kind="image", name="seat.png") for _ in range(11)]
        ids = [str(row.id) for row in rows]
        for count in (10, 11):
            dto = ChatMessageInputSerializer(data={"content": "잠실", "attachment_ids": ids[:count]})
            self.assertEqual(dto.is_valid(), count == 10)
        dto = ChatMessageInputSerializer(data={"content": "잠실", "attachment_ids": ids[:10] + ids[:10]})
        self.assertTrue(dto.is_valid(), dto.errors)
        self.assertEqual(dto.validated_data["attachment_ids"], ids[:10])
        self.assertEqual(len(attachments.resolve(self.session.id, ids[:10] + ids[:10])), 10)
        with self.assertRaises(ValidationError):
            attachments.resolve(self.session.id, ids)
        urls = [ChatAttachment.objects.create(session=self.session, kind="url", name="url") for _ in range(4)]
        mixed = ids[:7] + [str(row.id) for row in urls[:3]]
        self.assertEqual(len(attachments.resolve(self.session.id, mixed)), 10)
        with self.assertRaises(ValidationError):
            attachments.resolve(self.session.id, ids[:6] + [str(row.id) for row in urls])
        foreign = ChatAttachment.objects.create(session=ChatSession.objects.create(guest=uuid.uuid4()), kind="text", name="foreign")
        with self.assertRaises(Http404):
            attachments.resolve(self.session.id, ids[:9] + [str(foreign.id)])
        path = f"/api/v2/chat/sessions/{self.session.id}/messages/"
        with patch("llm.views.message.chat_v2.send_message", return_value=iter(())):
            for selected, status in ((ids[:10], 200), (mixed, 200), (ids, 400)):
                response = self.client.post(path, {"content": "잠실", "attachment_ids": selected}, format="json")
                self.assertEqual(response.status_code, status)

    def test_upload_is_not_a_session_lifetime_count_limit(self):
        ChatAttachment.objects.bulk_create([ChatAttachment(session=self.session, kind="text", name="old") for _ in range(40)])
        with patch.object(attachments.image_storage, "put_object"):
            response = self.client.post(self.path, {"file": SimpleUploadedFile("new.txt", b"hello", content_type="text/plain")}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)

    def test_upload_guest_private_and_foreign_access(self):
        with patch.object(attachments.image_storage, "put_object"):
            response = self.client.post(self.path, {"file": SimpleUploadedFile("x.md", b"hello", content_type="text/markdown")}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("object_key", response.data)
        with patch.object(attachments.image_storage, "get_object", return_value=io.BytesIO(b"hello")):
            response2 = self.client.get(response.data["url"])
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2["Cache-Control"], "private, no-store")
        self.client.cookies["guest_id"] = str(uuid.uuid4())
        self.assertEqual(self.client.get(response.data["url"]).status_code, 404)
        self.assertEqual(self.client.get(self.path).status_code, 404)

    def test_real_graph_rebuilds_images_and_manual_tools_without_mutating_checkpoint(self):
        from langchain_core.messages import AIMessage
        from llm.v2.tests.test_chain import ScriptedModel, fake_tools
        from llm.v2.agent.chain import build_graph
        row = ChatAttachment.objects.create(session=self.session, kind="image", name="seat.png", content_type="image/png")
        human = HumanMessage("잠실 좌석", id="q", additional_kwargs={"attachment_ids": [str(row.id)]})
        calls = []
        graph = build_graph(ScriptedModel(script=[AIMessage("좌석이에요")], calls=calls), fake_tools([]))
        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), \
                patch.object(attachments, "read_file", return_value=b"image-bytes"):
            graph.invoke({"messages": [human], "attachment_session_id": str(self.session.id), "tool_group_ids": ["weather"]})
        self.assertIn("get_weather", calls[0]["tools"])
        submitted = next(m for m in calls[0]["messages"] if isinstance(m, HumanMessage))
        self.assertEqual(submitted.content[1]["type"], "image_url")
        self.assertEqual(human.content, "잠실 좌석")

    def test_repeated_image_references_only_restore_latest_once_in_real_graph(self):
        from langchain_core.messages import AIMessage
        from llm.v2.tests.test_chain import ScriptedModel, fake_tools
        from llm.v2.agent.chain import build_graph
        rows = [ChatAttachment.objects.create(session=self.session, kind="image", name="x.png", content_type="image/png")
                for _ in range(12)]
        ids = [str(row.id) for row in rows]
        humans = [HumanMessage("잠실 좌석", id="old", additional_kwargs={"attachment_ids": ids[:10]}),
                  HumanMessage("이 사진 좌석 다시 설명해줘", id="new", additional_kwargs={"attachment_ids": ids[-10:] + [ids[-1]]}),
                  HumanMessage("첨부 좌석 다시 설명해줘", id="followup")]
        calls = []
        graph = build_graph(ScriptedModel(script=[AIMessage("좌석")], calls=calls), fake_tools([]))
        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}) as classify, \
                patch.object(attachments, "read_file", return_value=b"offline-fake-image") as read:
            graph.invoke({"messages": humans, "attachment_session_id": str(self.session.id)})
        submitted = [m for m in calls[0]["messages"] if isinstance(m, HumanMessage)]
        self.assertIsInstance(classify.call_args.args[0], str)  # scope is text/history only, not unseen image content
        self.assertEqual(sum(sum(b.get("type") == "image_url" for b in m.content) for m in submitted
                             if isinstance(m.content, list)), 10)
        self.assertEqual(submitted[0].content, humans[0].content)
        self.assertEqual(read.call_count, 10)
        self.assertTrue(all(isinstance(m.content, str) for m in humans))

    def test_direct_context_followup_reaches_model_without_checkpoint_content(self):
        from langchain_core.messages import AIMessage
        from llm.v2.tests.test_chain import ScriptedModel, fake_tools
        from llm.v2.agent.chain import build_graph
        rows = [ChatAttachment.objects.create(session=self.session, kind=kind, name=name, source_url=url)
                for kind, name, url in [("text", "ticket.md", ""),
                                         ("text", "article.md", "")]]
        texts = ["BEGIN " + "baseball " * 4000 + "MIDDLE " + "seats " * 4000 + "TAIL", "Complete URL article"]
        history = [HumanMessage("잠실 첨부", id="old", additional_kwargs={"attachment_ids": [str(r.id) for r in rows]}),
                   HumanMessage("첨부 전체를 다시 설명해줘", id="new")]
        calls = []
        graph = build_graph(ScriptedModel(script=[AIMessage("안내")], calls=calls), fake_tools([]))
        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), \
                patch.object(attachments, "source_text", side_effect=texts):
            graph.invoke({"messages": history, "attachment_session_id": str(self.session.id)})
        submitted = next(m for m in calls[0]["messages"] if isinstance(m, HumanMessage) and m.id == "old")
        for text in texts:
            self.assertIn(text, submitted.text)
            self.assertNotIn(text, calls[0]["system"])
        self.assertEqual(history[0].content, "잠실 첨부")
        self.assertEqual(history[1].content, "첨부 전체를 다시 설명해줘")

    def test_rolling_window_and_budget_evict_messages_with_sources(self):
        from langchain_core.messages import AIMessage, ToolMessage
        from llm.v2.tests.test_chain import ScriptedModel, fake_tools
        from llm.v2.agent.chain import build_graph
        from llm.v2.middleware.attachment_context import recent_messages
        rows = [ChatAttachment.objects.create(session=self.session, kind="text", name=f"{i}.txt") for i in range(5)]
        messages = []
        for i, row in enumerate(rows):
            messages.extend([HumanMessage(f"잠실 {i}", id=f"q{i}", additional_kwargs={"attachment_ids": [str(row.id)]}),
                             AIMessage("answer", id=f"a{i}")])
        messages.append(HumanMessage("잠실 followup", id="followup"))
        calls, reads = [], []
        graph = build_graph(ScriptedModel(script=[AIMessage("ok")], calls=calls), fake_tools([]))
        def source(row):
            reads.append(row.name)
            if row.name in {"0.txt", "1.txt"}:
                raise AssertionError("evicted source fetched")
            return "seat " * 11000
        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), \
                patch.object(attachments, "source_text", side_effect=source):
            graph.invoke({"messages": messages, "attachment_session_id": str(self.session.id)})
        self.assertEqual(reads, ["4.txt", "3.txt"])
        submitted = [m for m in calls[0]["messages"] if isinstance(m, HumanMessage)]
        self.assertEqual([m.id for m in submitted], ["q4", "followup"])
        self.assertIn("seat " * 11000, submitted[0].text)
        self.assertEqual(len(messages), 11)  # Model trimming never mutates history.
        tools = [HumanMessage("old", id="old"), AIMessage("", tool_calls=[{"id": "t", "name": "x", "args": {}}]),
                 ToolMessage("result", tool_call_id="t"), AIMessage("done")]
        for i in range(3):
            tools.extend([HumanMessage("recent", id=f"r{i}"), AIMessage("reply")])
        self.assertEqual(recent_messages(tools), tools)
        tools.append(HumanMessage("new", id="new"))
        self.assertEqual(recent_messages(tools)[0].id, "r0")

    @patch("llm.views.message.GuestChatThrottle.allow_request", return_value=True)
    def test_url_metadata_registration_validates_and_dedupes_without_fetch(self, throttle):
        with patch("socket.getaddrinfo", side_effect=AssertionError("no DNS")), \
                patch.object(attachments, "source_text", side_effect=AssertionError("no source")):
            first = self.client.post(self.path, {"url": "https://EXAMPLE.com.:443/a?q=1#part"}, format="json")
            duplicate = self.client.post(self.path, {"url": "https://example.com/a?q=1#other"}, format="json")
            other = self.client.post(self.path, {"url": "https://example.com/a?q=2"}, format="json")
            self.assertEqual(first.status_code, 201)
            self.assertEqual(first.data["url"], "https://example.com/a?q=1")
            self.assertEqual(first.data["id"], duplicate.data["id"])
            self.assertNotEqual(first.data["id"], other.data["id"])
            row = ChatAttachment.objects.get(pk=first.data["id"])
            self.assertEqual((row.object_key, row.extracted_text, row.size), ("", "", 0))
            for url in ("http://localhost./", "http://127.0.0.1./", "http://10.0.0.1/", "http://169.254.169.254/",
                        "http://service.internal./", "http://example.com:8080/", "http://user:pw@example.com/", "http://2130706433/",
                        "http://0x7f.0.0.1/a", "http://0x7f.0x0.0x0.0x1/a", "http://0X7F000001/a",
                        "http://0177.0.0.1/a", "http://127.1/a", "http://127.0.1/a", "http://0x7f.1./a",
                        "http://0x0a.0.0.1/a", "http://0xa9.0xfe.0xa9.0xfe/a", "http://0x08080808/a"):
                with self.subTest(url=url):
                    self.assertEqual(self.client.post(self.path, {"url": url}, format="json").status_code, 400)
            self.assertEqual(self.session.attachments.count(), 2)
            for url in ("https://8.8.8.8/a", "https://[2606:4700:4700::1111]/a",
                        "https://0x7f.example.com/a", "https://0177.example.com/a", "https://bücher.de/a"):
                with self.subTest(url=url):
                    response = self.client.post(self.path, {"url": url}, format="json")
                    self.assertEqual(response.status_code, 201)
                    row = ChatAttachment.objects.get(pk=response.data["id"])
                    self.assertEqual((row.object_key, row.extracted_text, row.size), ("", "", 0))

    def test_url_create_metadata_and_existing_url_only_retry_model_input(self):
        from langchain_core.messages import AIMessage
        from llm.v2.tests.test_chain import ScriptedModel, fake_tools
        from llm.v2.agent.chain import build_graph
        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("no DNS")):
            response = self.client.post(self.path, {"url": "https://example.com"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["url"], "https://example.com/")
        row = ChatAttachment.objects.create(session=self.session, kind="url", name="old Naver", source_url="https://example.com", extracted_text="cached untouched")
        human = HumanMessage(row.source_url, id="retry", additional_kwargs={"attachment_ids": [str(row.id)]})
        calls = []
        graph = build_graph(ScriptedModel(script=[AIMessage("ok")], calls=calls), fake_tools([]))
        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), \
                patch("llm.v2.agent.browser_research.web_body", side_effect=AssertionError("cached URL must not refetch")):
            graph.invoke({"messages": [human], "attachment_session_id": str(self.session.id)})
        self.assertIn(row.source_url, next(m for m in calls[0]["messages"] if isinstance(m, HumanMessage)).text)
        self.assertNotIn("web_search", calls[0]["tools"])
        self.assertIn("cached untouched", next(m for m in calls[0]["messages"] if isinstance(m, HumanMessage)).text)
        row.refresh_from_db()
        self.assertEqual(row.extracted_text, "cached untouched")

    def test_scope_and_foreign_session_refuse_before_source_or_model(self):
        from langchain_core.messages import AIMessage
        from llm.v2.tests.test_chain import ScriptedModel, fake_tools
        from llm.v2.agent.chain import build_graph
        foreign = ChatSession.objects.create(guest=uuid.uuid4())
        row = ChatAttachment.objects.create(session=foreign, kind="text", name="foreign.txt")
        human = HumanMessage("잠실", id="q", additional_kwargs={"attachment_ids": [str(row.id)]})
        for allowed in (False, True):
            calls = []
            graph = build_graph(ScriptedModel(script=[AIMessage("must not run")], calls=calls), fake_tools([]))
            with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": allowed, "capabilities": []}), \
                    patch.object(attachments, "source_text") as read:
                if allowed:
                    with self.assertRaisesRegex(ValueError, "missing conversation attachment"):
                        graph.invoke({"messages": [human], "attachment_session_id": str(self.session.id)})
                else:
                    graph.invoke({"messages": [human], "attachment_session_id": str(self.session.id)})
            read.assert_not_called()
            self.assertEqual(calls, [])

    def test_member_ownership_and_url_cache(self):
        from django.contrib.auth import get_user_model
        member = get_user_model().objects.create_user(username="attachment-test", email="attachment-test@example.com", password="offline-test")
        owned = ChatSession.objects.create(user=member)
        row = ChatAttachment.objects.create(session=owned, kind="url", name="source", source_url="https://example.com/")
        self.assertEqual(self.client.get(f"/api/v2/chat/sessions/{owned.id}/attachments/").status_code, 404)
        self.client.force_authenticate(member)
        self.assertEqual(self.client.get(f"/api/v2/chat/sessions/{owned.id}/attachments/").status_code, 200)
        with patch("llm.v2.agent.browser_research.web_body", return_value={"status": "blocked"}), self.assertRaises(ValueError):
            attachments.source_text(row)
        row.refresh_from_db()
        self.assertEqual(row.extracted_text, "")

    def test_foreign_reference_rejected_and_history_delete_conflict(self):
        foreign = ChatSession.objects.create(guest=uuid.uuid4())
        row = ChatAttachment.objects.create(session=foreign, kind="text", name="x")
        response = self.client.post(f"/api/v2/chat/sessions/{self.session.id}/messages/",
                                    {"content": "잠실", "attachment_ids": [str(row.id)]}, format="json")
        self.assertEqual(response.status_code, 404)
        row.session = self.session
        row.save()
        message = HumanMessage("잠실", additional_kwargs={"attachment_ids": [str(row.id)]})
        with patch("llm.views.attachments.ChatThread.history", return_value=[SimpleNamespace(values={"messages": [message]})]):
            self.assertEqual(self.client.delete(self.path + f"{row.id}/").status_code, 409)


class DirectContextStreamTests(TransactionTestCase):
    def test_over_token_budget_fails_before_model_and_persists_failed_turn(self):
        from langchain_core.messages import AIMessage
        from llm.v2.tests.test_chain import ScriptedModel, fake_tools
        from llm.v2.agent.chain import build_graph
        from llm.v2.middleware.attachment_context import DirectContextLimit
        from llm.service.chat_runs import stream_turn
        session = ChatSession.objects.create(guest=uuid.uuid4())
        rows = [ChatAttachment.objects.create(session=session, kind="text", name=f"{i}.txt") for i in range(2)]
        human = HumanMessage("잠실 첨부", id="q", additional_kwargs={"attachment_ids": [str(r.id) for r in rows]})
        calls, saved = [], []
        graph = build_graph(ScriptedModel(script=[AIMessage("must not run")], calls=calls), fake_tools([]))
        thread = SimpleNamespace(thread_id="offline-token-limit", update=lambda messages, turns: saved.append(turns) or True)

        def produce():
            graph.invoke({"messages": [human], "attachment_session_id": str(session.id)})
            yield ("delta", {})

        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), \
                patch.object(attachments, "source_text", return_value="seat " * 11000):
            events = list(stream_turn(thread, [], {}, human, produce, {"answer": "", "messages": []}, label="v2"))
        self.assertEqual(calls, [])
        self.assertEqual(events[-1], ("error", {"detail": DirectContextLimit.detail}))
        self.assertEqual(saved[-1]["q"]["status"], "failed")

