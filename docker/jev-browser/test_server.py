"""Offline lifecycle checks: no Chrome or paid provider calls."""
import asyncio
import importlib.util
import os
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location("browser_server", os.path.join(os.path.dirname(__file__), "server.py"))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        server.lock = asyncio.Lock()

    async def test_busy_never_spawns(self):
        async with server.lock:
            with patch.object(server.asyncio, "create_subprocess_exec", new_callable=AsyncMock) as spawn:
                self.assertEqual((await server.jev_browse("https://example.com", "read"))["status"], "busy")
                spawn.assert_not_called()

    async def test_timeout_and_cancel_reap_before_unlock(self):
        for cancel in (False, True):
            process = AsyncMock()
            process.pid = 99999999
            async def read(size):
                await asyncio.Event().wait()
            process.stdout.read.side_effect = read
            with patch.object(server.asyncio, "create_subprocess_exec", return_value=process), patch.object(server.os, "killpg") as kill, patch.dict(os.environ, {"JEV_TIMEOUT_SECONDS": "0.01"}):
                def killed(*args):
                    process.stdout.read.side_effect = None
                    process.stdout.read.return_value = b""
                kill.side_effect = killed
                task = asyncio.create_task(server.jev_browse("https://example.com", "read"))
                if cancel:
                    await asyncio.sleep(0)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertEqual((await task)["status"], "timeout")
                kill.assert_called_once()
                process.wait.assert_awaited_once()
                self.assertFalse(server.lock.locked())

    async def test_body_actual_output_overflow_and_busy(self):
        import json
        async with server.lock:
            with patch.object(server.asyncio, "create_subprocess_exec", new_callable=AsyncMock) as spawn:
                self.assertEqual((await server.jev_read_body("https://example.com"))["status"], "busy")
                spawn.assert_not_called()
        for data in ({"status": "ok", "body": "BEGIN MIDDLE END", "source_kind": "rendered_dom_snapshot"},
                     {"status": "blocked"}, {"status": "partial"},
                     {"status": "ok", "body": "x" * (2 * 1024 * 1024 + 1)}):
            process = AsyncMock(pid=99999999, returncode=0)
            process.stdout.read.return_value = b""
            with patch.object(server.asyncio, "create_subprocess_exec", return_value=process), \
                    patch.object(server, "bounded_stdout", AsyncMock(return_value=json.dumps(data).encode())), \
                    patch.object(server.os, "killpg") as kill:
                result = await server.jev_read_body("https://example.com")
            self.assertEqual(result["status"], "overflow" if len(data.get("body", "")) > 2 * 1024 * 1024 else data["status"])
            kill.assert_called_once()
            self.assertFalse(server.lock.locked())

    async def test_body_cancellation_reaps_before_release(self):
        process = AsyncMock(pid=99999999)
        process.stdout.read.return_value = b""
        async def waiting(*args):
            await asyncio.Event().wait()
        with patch.object(server.asyncio, "create_subprocess_exec", return_value=process), \
                patch.object(server, "bounded_stdout", side_effect=waiting), patch.object(server.os, "killpg") as kill:
            task = asyncio.create_task(server.jev_read_body("https://example.com"))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        kill.assert_called_once()
        self.assertFalse(server.lock.locked())

    async def test_validation(self):
        for url in ("file:///etc/passwd", "http://user:password@example.com", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                await server.jev_browse(url, "read")


if __name__ == "__main__":
    unittest.main()
