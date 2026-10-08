"""느린 검색 중 연결 유지와 실제 작업 정체 제한을 구분한다."""
import threading
import time
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from llm.service.chat_runs import Run, Stopped
from llm.views.sse import event_stream_response


class StreamKeepaliveTest(SimpleTestCase):
    @override_settings(CHAT_STREAM_IDLE_TIMEOUT_SECONDS=2)
    def test_silent_work_stays_connected_then_delivers_real_result(self):
        gate, closed = threading.Event(), threading.Event()

        def work():
            try:
                gate.wait(3)
                yield "delta", {"text": "조사 완료"}
                yield "done", {"message_id": "2"}
            finally:
                closed.set()

        with patch("llm.service.chat_runs._HEARTBEAT_SECONDS", 0.02):
            response = event_stream_response(Run("slow-search").pump(work()))
            stream = iter(response.streaming_content)
            try:
                self.assertEqual(next(stream), b": keep-alive\n\n")
                self.assertEqual(next(stream), b": keep-alive\n\n")
                gate.set()
                rest = b"".join(stream).decode()
                self.assertIn('event: delta\ndata: {"text": "조사 완료"}', rest)
                self.assertEqual(rest.count("event: done"), 1)
                self.assertNotIn("heartbeat", rest)
            finally:
                gate.set()
                response.close()
        self.assertTrue(closed.wait(1))

    @override_settings(CHAT_STREAM_IDLE_TIMEOUT_SECONDS=0.12)
    def test_keepalives_do_not_extend_real_idle_deadline(self):
        gate, closed = threading.Event(), threading.Event()

        def work():
            try:
                gate.wait(2)
                yield "delta", {"text": "too late"}
            finally:
                closed.set()

        stream = Run("stalled").pump(work())
        started, heartbeats = time.monotonic(), []
        try:
            with patch("llm.service.chat_runs._HEARTBEAT_SECONDS", 0.02):
                with self.assertRaises(TimeoutError):
                    while True:
                        heartbeats.append(next(stream))
            self.assertTrue(heartbeats)
            self.assertTrue(all(frame == ("heartbeat", {}) for frame in heartbeats))
            self.assertLess(time.monotonic() - started, 1)
        finally:
            stream.close()
            gate.set()
        self.assertTrue(closed.wait(1))

    @override_settings(CHAT_STREAM_IDLE_TIMEOUT_SECONDS=2)
    def test_cancel_during_keepalive_wait_does_not_send_a_late_answer(self):
        run, gate = Run("cancelled"), threading.Event()

        def work():
            gate.wait(2)
            yield "delta", {"text": "late answer"}

        stream = run.pump(work())
        try:
            with patch("llm.service.chat_runs._HEARTBEAT_SECONDS", 0.02):
                self.assertEqual(next(stream), ("heartbeat", {}))
                run.cancel()
                with self.assertRaises(Stopped):
                    next(stream)
        finally:
            gate.set()
            stream.close()
