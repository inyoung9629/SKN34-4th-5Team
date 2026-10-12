"""Offline boundary tests; these do not establish provider or retrieval quality."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("experiment", Path(__file__).parents[1] / "highlight_multimodal.py")
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)


class MultimodalTests(unittest.TestCase):
    def test_vtt_alignment_and_stable_segments(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "real.vtt"
            path.write_text("WEBVTT\n\n00:00:02.000 --> 00:00:04.000\n<b>타구</b>\n\n00:00:26.000 --> 00:00:28.000\n홈런\n")
            cues = mm.transcript(path, 30)
            self.assertEqual(cues[0]["text"], "타구")
            rows = mm.segments("SlvDBmr4Bd8", cues, 30, 2)
            self.assertEqual(rows, mm.segments("SlvDBmr4Bd8", cues, 30, 2))
            self.assertEqual(rows[0]["start"], 0)
            self.assertEqual(rows[0]["text"], "타구")
            with self.assertRaises(ValueError):
                mm.transcript(path, 20)
        for bad in ("01:60:00.000", "-1", "00:00:60.000"):
            with self.assertRaises(ValueError):
                mm.timestamp(bad)

    def test_vector_validation(self):
        for bad in (None, [1], [0] * mm.DIM, [float("nan")] * mm.DIM, [True] * mm.DIM, ["1"] * mm.DIM):
            with self.assertRaises(ValueError):
                mm.vector(bad)
        self.assertEqual(len(mm.vector([1] * mm.DIM)), mm.DIM)

    def test_provider_response_shape(self):
        g = mm.Google("test-only-not-a-key")
        for bad in ([], {}, {"embedding": []}, {"embedding": {"values": [1]}}):
            g.call = lambda *args, response=bad: response
            with self.assertRaises(ValueError):
                g.embed({"text": "test"})
        verifier = mm.Google("test-only-not-a-key")
        verifier.call = lambda *args: {"choices": []}
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "clip.mp4"
            clip.write_bytes(b"offline-test")
            with self.assertRaises(ValueError):
                verifier.verify("test", {"id": "x", "start": 0, "end": 24}, clip, clip)

    def test_provider_endpoint_serialization(self):
        import base64
        import io
        from unittest.mock import Mock, patch
        output = {"candidate_id": "x", "match": True, "local_seconds": 2, "evidence": "visible hit"}
        with tempfile.TemporaryDirectory() as folder:
            video, audio = Path(folder) / "clip.mp4", Path(folder) / "clip.wav"
            video.write_bytes(b"actual-video-bytes")
            audio.write_bytes(b"separate-audio-bytes")
            with patch.object(mm.urllib.request, "urlopen", side_effect=[
                    io.BytesIO(json.dumps({"embedding": {"values": [1.] * mm.DIM}}).encode()),
                    io.BytesIO(b'{"totalTokens": 9000}'),
                    io.BytesIO(json.dumps({"modelVersion": "gemini-3.8-flash", "candidates": [{"content": {"parts": [{"text": json.dumps(output)}]}}]}).encode())]) as call:
                mm.Google("google-test").embed(mm.media(video, "video/mp4"))
                google = mm.Google("google-test", quota=Mock())
                self.assertEqual(google.verify("hit", {"id": "x", "start": 120, "end": 144}, video, audio), output)
                self.assertEqual(google.last_model_version, "gemini-3.8-flash")
            google.quota.acquire.assert_called_once_with("gemini-3.8-flash", 9000)
            embed, count, verify = [c.args[0] for c in call.call_args_list]
            self.assertTrue(count.full_url.endswith(":countTokens"))
            self.assertEqual(json.loads(count.data)["generateContentRequest"]["contents"], json.loads(verify.data)["contents"])
            self.assertEqual(embed.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:embedContent")
            self.assertIsNone(embed.get_header("Authorization"))
            self.assertEqual(verify.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent")
            self.assertEqual(verify.get_header("X-goog-api-key"), "google-test")
            self.assertIsNone(verify.get_header("Authorization"))
            body = json.loads(verify.data)
            content = body["contents"][0]["parts"]
            self.assertEqual(content[1]["inlineData"], {"mimeType": "video/mp4", "data": base64.b64encode(video.read_bytes()).decode()})
            self.assertEqual(content[2]["inlineData"], {"mimeType": "audio/wav", "data": base64.b64encode(audio.read_bytes()).decode()})
            self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
            self.assertFalse(body["generationConfig"]["responseJsonSchema"]["additionalProperties"])
            self.assertEqual(body["generationConfig"]["responseJsonSchema"]["required"], list(output))

    def test_verifier_provider_failures_are_not_absence(self):
        import io
        from unittest.mock import patch
        verifier = mm.Google("google-test")
        for error in (mm.urllib.error.HTTPError("https://generativelanguage.googleapis.com", 429, "unavailable", {},
                          io.BytesIO(b'{"error":{"status":"RESOURCE_EXHAUSTED","message":"secret","details":[{"reason":"RATE_LIMIT_EXCEEDED"}]}}')),
                      mm.urllib.error.HTTPError("https://generativelanguage.googleapis.com", 503, "busy", {}, None),
                      mm.urllib.error.URLError("unavailable"), TimeoutError()):
            with patch.object(mm.urllib.request, "urlopen", side_effect=error) as call, patch.object(mm.time, "sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "Google generateContent") as caught:
                    verifier.call(verifier.model, "generateContent", {})
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(call.call_count, 1)
                sleep.assert_not_called()
        with patch.dict(mm.os.environ, {"GOOGLE_API_KEY": "google-only"}, clear=True):
            self.assertEqual(mm.Google().model, "gemini-3.8-flash")
        with patch.dict(mm.os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "GOOGLE_API_KEY missing"):
                mm.Google()

    def test_three_candidates_reject_first_match_second(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        specialist = object.__new__(mm.Specialist)
        specialist.google = Mock()
        specialist.google.embed.return_value = [1.] * mm.DIM
        specialist.verifier = Mock()
        specialist.root = Path("/offline")
        specialist.rows = {sid: {"id": sid, "video_id": "SlvDBmr4Bd8", "start": start, "end": start + 24}
                           for sid, start in (("a", 100), ("b", 646.079), ("c", 800))}
        specialist.db = Mock()
        specialist.db.query_points.return_value.points = [SimpleNamespace(id=sid) for sid in specialist.rows]
        specialist.sql = Mock()
        specialist.sql.execute.return_value = []
        specialist.verifier.verify.side_effect = lambda query, row, video, audio: {
            "candidate_id": row["id"], "match": row["id"] == "b", "local_seconds": 0,
            "evidence": "7:1 becomes 9:1 on a hit" if row["id"] == "b" else "wrong score"}
        result = specialist.ask("KT 안타")
        self.assertEqual(result["retrieval_only"], ["a", "b", "c"])
        self.assertEqual(specialist.verifier.verify.call_count, 3)
        self.assertEqual(result["matches"][0]["playback_seconds"], 646.079)
        self.assertTrue(result["complete"])
        specialist.google.verify.assert_not_called()
        specialist.verifier.verify.side_effect = RuntimeError("Google generateContent HTTP 503")
        failed = specialist.ask("KT 안타")
        self.assertFalse(failed["complete"])
        self.assertIsNone(failed["no_match"])

    def offline_specialist(self, verifier, count=5):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from threading import get_ident
        specialist = object.__new__(mm.Specialist)
        specialist.root = Path("/offline")
        specialist.rows = {sid: {"id": sid, "video_id": "SlvDBmr4Bd8", "start": i * 24, "end": (i + 1) * 24}
                           for i, sid in enumerate("abcde"[:count])}
        caller = get_ident()
        def retrieval(value):
            self.assertEqual(get_ident(), caller)
            return value
        specialist.google = Mock()
        specialist.google.embed.side_effect = lambda part: retrieval([1.] * mm.DIM)
        specialist.db = Mock()
        specialist.db.query_points.side_effect = lambda *a, **k: retrieval(SimpleNamespace(points=[SimpleNamespace(id=sid) for sid in specialist.rows]))
        specialist.sql = Mock()
        specialist.sql.execute.side_effect = lambda *a: retrieval([])
        specialist.verifier = verifier
        return specialist

    def test_parallel_cap_all_checks_and_original_order(self):
        from threading import Barrier, Event, Lock, get_ident
        from unittest.mock import Mock
        gate, last, lock = Barrier(3), Event(), Lock()
        active, peak, completed = 0, 0, []
        caller = get_ident()
        def verify(query, row, video, audio):
            nonlocal active, peak
            self.assertNotEqual(get_ident(), caller)
            sid = row["id"]
            with lock:
                active += 1
                peak = max(peak, active)
            if sid in "abc":
                gate.wait(timeout=5)
            if sid == "a":
                self.assertTrue(last.wait(timeout=5))
            with lock:
                completed.append(sid)
                active -= 1
            if sid == "c":
                last.set()
            return {"candidate_id": sid, "match": True, "local_seconds": 1, "evidence": "visible hit"}
        specialist = self.offline_specialist(Mock(verify=verify))
        result = specialist.ask("hit", 5)
        self.assertEqual(peak, 3)
        self.assertLess(completed.index("c"), completed.index("a"))
        self.assertCountEqual(completed, list("abcde"))
        self.assertEqual([d["candidate_id"] for d in result["decisions"]], list("abcde"))
        self.assertEqual([m["candidate_id"] for m in result["matches"]], list("abcde"))
        self.assertTrue(result["complete"])
        self.assertFalse(result["no_match"])
        self.assertGreater(result["verification_latency"], 0)

    def test_parallel_mixed_failures_are_incomplete_not_no_match(self):
        from unittest.mock import Mock
        def verify(query, row, video, audio):
            sid = row["id"]
            if sid == "c":
                raise RuntimeError("Google generateContent HTTP 503")
            return {"candidate_id": "wrong" if sid == "b" else sid, "match": False,
                    "local_seconds": 0, "evidence": "absent"}
        specialist = self.offline_specialist(Mock(verify=verify), 3)
        result = specialist.ask("hit")
        self.assertEqual([d["candidate_id"] for d in result["decisions"]], list("abc"))
        self.assertIn("rejected", result["decisions"][1])
        self.assertIn("provider_error", result["decisions"][2])
        self.assertEqual(result["matches"], [])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["no_match"])

    def test_model_version_is_invocation_local_during_parallel_validation(self):
        from threading import Barrier
        from unittest.mock import patch
        from unittest.mock import Mock
        google = mm.Google("test-only", quota=Mock())
        gate = Barrier(3)
        def call(model, operation, body):
            if operation == "countTokens":
                return {"totalTokens": 9000}
            prompt = body["contents"][0]["parts"][0]["text"]
            sid = prompt.split("Candidate ID: ")[1].split("\n")[0]
            output = {"candidate_id": sid, "match": False, "local_seconds": 0, "evidence": "absent"}
            return {"modelVersion": "version-" + sid, "candidates": [{"content": {"parts": [{"text": json.dumps(output)}]}}]}
        google.call = call
        original_decision = mm.decision
        def validate(output, row):
            # Force all three responses to be parsed before their versions are read.
            gate.wait(timeout=5)
            return original_decision(output, row)
        specialist = self.offline_specialist(google, 3)
        with patch.object(mm, "media", return_value={}), patch.object(mm, "decision", side_effect=validate):
            result = specialist.ask("hit")
        self.assertEqual([d["model_version"] for d in result["decisions"]], ["version-a", "version-b", "version-c"])
        self.assertIsNone(google.last_model_version)
        self.assertTrue(result["complete"])
        self.assertTrue(result["no_match"])

    def test_quota_refill_tpm_daily_and_bounded_wait(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = [datetime(2026, 10, 12, 23, 59, 0, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()]
        elapsed = [0.]
        def sleep(seconds):
            now[0] += seconds
            elapsed[0] += seconds
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "quota.sqlite3"
            quota = mm.VerificationQuota(path, rpm=3, tpm=100, rpd=4, max_wait=20, clock=lambda: now[0], sleep=sleep, monotonic=lambda: elapsed[0])
            for _ in range(3):
                self.assertEqual(quota.acquire("m", 0), 0)
            self.assertEqual(quota.acquire("m", 0), 20)
            # Persisted across instances/restarts, daily usage never refills per minute.
            reopened = mm.VerificationQuota(path, rpm=3, tpm=100, rpd=4, max_wait=20, clock=lambda: now[0], sleep=sleep, monotonic=lambda: elapsed[0])
            with self.assertRaisesRegex(RuntimeError, "daily quota"):
                reopened.acquire("m", 0)
            now[0] += 40  # Midnight Pacific, including daylight-saving timezone.
            self.assertEqual(reopened.acquire("m", 100), 0)
            with self.assertRaisesRegex(RuntimeError, "bounded wait"):
                reopened.acquire("m", 100)
            with self.assertRaisesRegex(RuntimeError, "token capacity"):
                reopened.acquire("m", 101)
            now[0] += 60
            self.assertEqual(reopened.acquire("m", 100), 0)

    def test_quota_busy_wall_clock_rollback_keeps_bounded_wait(self):
        from contextlib import closing
        now, elapsed = [1791800000.], [0.]
        def sleep(seconds):
            now[0] += seconds - (.5 if elapsed[0] == 0 else 0)
            elapsed[0] += seconds
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "quota.sqlite3"
            quota = mm.VerificationQuota(path, max_wait=.1, clock=lambda: now[0],
                                         sleep=sleep, monotonic=lambda: elapsed[0])
            with closing(mm.sqlite3.connect(path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                with self.assertRaisesRegex(RuntimeError, "bounded wait"):
                    quota.acquire("m", 0)
                self.assertAlmostEqual(elapsed[0], .1)
                self.assertLess(now[0], 1791800000.)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM quota").fetchone()[0], 0)

    def test_quota_atomic_parallel_admission_and_no_partial_spend(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "quota.sqlite3"
            quota = mm.VerificationQuota(path, rpm=900, tpm=100, rpd=100, max_wait=0, clock=lambda: 1791800000)
            def admit(_):
                try:
                    quota.acquire("m", 30)
                    return True
                except RuntimeError:
                    return False
            with ThreadPoolExecutor(max_workers=6) as pool:
                accepted = list(pool.map(admit, range(6)))
            self.assertLessEqual(sum(accepted), 3)
            # Fill any requests rejected solely due to simultaneous SQLite locking.
            while sum(accepted) < 3:
                self.assertTrue(admit(0))
                accepted.append(True)
            with self.assertRaisesRegex(RuntimeError, "bounded wait"):
                quota.acquire("m", 1)
            with mm.sqlite3.connect(path) as db:
                requests, tokens, daily = db.execute("SELECT requests, tokens, daily FROM quota").fetchone()
            self.assertEqual((requests, tokens, daily), (0, 10, 3))

    def test_count_failure_and_failed_generation_contracts(self):
        from unittest.mock import Mock, patch
        google = mm.Google("test-only", quota=Mock())
        specialist = self.offline_specialist(google, 1)
        with patch.object(mm, "media", return_value={}):
            for bad in ({}, {"totalTokens": True}, {"totalTokens": -1}):
                google.call = Mock(return_value=bad)
                result = specialist.ask("hit")
                self.assertFalse(result["complete"])
                self.assertIsNone(result["no_match"])
                self.assertIn("rejected", result["decisions"][0])
                google.quota.acquire.assert_not_called()
            google.call = Mock(side_effect=[{"totalTokens": 9000}, RuntimeError("Google generateContent HTTP 503")])
            result = specialist.ask("hit")
            google.quota.acquire.assert_called_once_with(google.model, 9000)
            self.assertIn("provider_error", result["decisions"][0])
            self.assertIsNone(result["no_match"])
            self.assertIn("quota_wait", result["decisions"][0])
            self.assertIn("provider_latency", result["decisions"][0])
            google.quota.acquire.side_effect = RuntimeError("Verification quota bounded wait exhausted")
            google.call = Mock(return_value={"totalTokens": 9000})
            result = specialist.ask("hit")
            self.assertEqual(google.call.call_count, 1)
            self.assertFalse(result["complete"])
            self.assertIsNone(result["no_match"])

    def test_fusion_overlap_unknown_duplicate(self):
        rows = {"a": {"video_id": "x", "start": 0, "end": 20}, "b": {"video_id": "x", "start": 10, "end": 30},
                "c": {"video_id": "y", "start": 0, "end": 20}}
        self.assertEqual(mm.fusion([["a", "a", "b", "c", "unknown"], ["c", "a"]], rows), ["a", "c"])
        self.assertEqual(mm.fusion([], rows), [])

    def test_candidate_boundaries_and_no_match(self):
        row = {"id": "a", "video_id": "SlvDBmr4Bd8", "start": 120, "end": 144}
        good = {"candidate_id": "a", "match": True, "local_seconds": 3, "evidence": "visible bat contact"}
        self.assertEqual(mm.decision(good, row)["playback_seconds"], 123)
        self.assertIsNone(mm.decision({**good, "match": False}, row))
        for patch in ({"candidate_id": "../evil"}, {"local_seconds": -1}, {"local_seconds": 24},
                      {"local_seconds": float("inf")}, {"local_seconds": True}, {"match": "true"}, {"evidence": ""}):
            with self.assertRaises(ValueError):
                mm.decision({**good, **patch}, row)

    def test_specialist_no_match_false_candidate_and_bad_output(self):
        class FakeGoogle:
            def embed(self, part):
                return [1.0] * mm.DIM
            def verify(self, query, row, video, audio):
                return {"candidate_id": "wrong", "match": True, "local_seconds": 1, "evidence": "fabricated"}
        from qdrant_client import models
        with tempfile.TemporaryDirectory() as folder:
            s = mm.Specialist(folder, FakeGoogle(), FakeGoogle())
            try:
                s.db.create_collection(mm.COLLECTION, vectors_config={m: models.VectorParams(size=mm.DIM, distance=models.Distance.COSINE) for m in mm.MODALITIES})
                sid = "12345678-1234-1234-1234-123456789012"
                s.rows[sid] = {"id": sid, "video_id": "SlvDBmr4Bd8", "start": 0, "end": 24}
                s.db.upsert(mm.COLLECTION, [models.PointStruct(id=sid, vector={m: [1.] * mm.DIM for m in mm.MODALITIES})])
                result = s.ask('" OR unknown --', 1)
                self.assertFalse(result["complete"])
                self.assertIsNone(result["no_match"])
                self.assertEqual(result["matches"], [])
                self.assertEqual(result["decisions"][0]["rejected"], "malformed provider output")
                s.verifier.verify = lambda query, row, video, audio: {
                    "candidate_id": row["id"], "match": False, "local_seconds": 0,
                    "evidence": "Requested action is absent from this clip"}
                semantic_no_match = s.ask("test", 1)
                self.assertTrue(semantic_no_match["complete"])
                self.assertTrue(semantic_no_match["no_match"])
                self.assertEqual(semantic_no_match["matches"], [])
                def unavailable(*args):
                    raise RuntimeError("Google generateContent HTTP 503")
                s.verifier.verify = unavailable
                failed = s.ask("test", 1)
                self.assertFalse(failed["complete"])
                self.assertIsNone(failed["no_match"])
                self.assertIn("provider_error", failed["decisions"][0])
                with self.assertRaises(ValueError):
                    s.ask("test", 500)
                (Path(folder) / "segments.json").write_text(json.dumps(s.rows))
            finally:
                s.close()
            reopened = mm.Specialist(folder, FakeGoogle(), FakeGoogle())
            try:
                saved = reopened.db.retrieve(mm.COLLECTION, [sid], with_vectors=True)[0]
                self.assertEqual(set(saved.vector), set(mm.MODALITIES))
                self.assertEqual(reopened.rows[sid]["start"], 0)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
