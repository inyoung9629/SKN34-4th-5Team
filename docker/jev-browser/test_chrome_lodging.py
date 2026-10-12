"""Deterministic Chrome tool lifecycle; no browser or paid APIs."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location("chrome_browser_server", Path(__file__).with_name("server.py"))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class ChromeToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        server.lock = asyncio.Lock()
        server.warm_task = None

    async def test_foreground_preempts_warmer_and_waits_for_process_cleanup(self):
        started = asyncio.Event()
        process = AsyncMock(pid=99999999, returncode=0)
        process.stdout.read.return_value = b""
        spawned = []
        async def spawn(*args, **kwargs):
            spawned.append(args[2])
            return process
        async def output(*args):
            if len(spawned) == 1:
                started.set()
                await asyncio.Event().wait()
            self.assertFalse(Path(spawned[0]).exists(), "Warmer profile must be removed before user Chrome starts")
            return b'{"status":"read"}'
        with patch.object(server.asyncio, "create_subprocess_exec", side_effect=spawn), \
                patch.object(server, "bounded_stdout", side_effect=output), patch.object(server.os, "killpg") as kill:
            background = asyncio.create_task(server.chrome_lodging("호텔", background=True))
            await asyncio.wait_for(started.wait(), 2)
            foreground = await server.chrome_lodging("사용자 호텔")
            self.assertEqual(foreground["status"], "read")
            self.assertEqual((await background)["status"], "busy")
        self.assertEqual(kill.call_count, 2)
        self.assertFalse(server.lock.locked())
        self.assertIsNone(server.warm_task)

    async def test_warmer_never_cancels_foreground_and_only_one_warmer_runs(self):
        started = asyncio.Event()
        process = AsyncMock(pid=99999999, returncode=0)
        process.stdout.read.return_value = b""
        async def output(*args):
            started.set()
            await asyncio.Event().wait()
        with patch.object(server.asyncio, "create_subprocess_exec", return_value=process) as spawn, \
                patch.object(server, "bounded_stdout", side_effect=output), patch.object(server.os, "killpg"):
            foreground = asyncio.create_task(server.chrome_lodging("호텔"))
            await started.wait()
            self.assertEqual((await server.chrome_lodging("사전 갱신", background=True))["status"], "busy")
            self.assertFalse(foreground.done())
            foreground.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await foreground
            spawn.assert_called_once()

    async def test_busy_and_invalid_input_do_not_spawn(self):
        with patch.object(server.asyncio, "create_subprocess_exec", new_callable=AsyncMock) as spawn:
            async with server.lock:
                self.assertEqual((await server.chrome_lodging("호텔"))["status"], "busy")
            for name, seconds in (("", 45), ("x"*256, 45), ("호텔", 0), ("호텔", 46), ("호텔", float("nan"))):
                self.assertEqual((await server.chrome_lodging(name, seconds))["status"], "error")
            spawn.assert_not_called()

    async def test_only_chrome_script_runs_without_provider_credentials(self):
        process = AsyncMock(pid=99999999, returncode=0)
        process.stdout.read.return_value = b""
        result = {"status": "read", "body_text": "actual text"}
        with patch.object(server.asyncio, "create_subprocess_exec", return_value=process) as spawn, \
                patch.object(server, "bounded_stdout", AsyncMock(return_value=json.dumps(result).encode())), \
                patch.object(server.os, "killpg") as kill, \
                patch.dict(os.environ, {"OPENAI_API_KEY": "unused-test", "OPENROUTER_API_KEY": "unused-test"}):
            self.assertEqual(await server.chrome_lodging("호텔", region="서울 송파구"), result)
        self.assertEqual(spawn.call_args.args[:2], ("node", "/opt/service/chrome-lodging.mjs"))
        self.assertEqual(spawn.call_args.args[-1], "서울 송파구")
        self.assertNotIn("OPENAI_API_KEY", spawn.call_args.kwargs["env"])
        self.assertNotIn("OPENROUTER_API_KEY", spawn.call_args.kwargs["env"])
        self.assertFalse(Path(spawn.call_args.args[2]).exists())
        kill.assert_called_once()
        self.assertFalse(server.lock.locked())

    async def test_cancel_timeout_and_overflow_reap_before_unlock(self):
        for outcome in ("cancel", "timeout", "overflow"):
            process = AsyncMock(pid=99999999, returncode=0)
            process.stdout.read.return_value = b""
            async def output(*args):
                if outcome == "timeout": raise TimeoutError
                if outcome == "overflow": raise OverflowError
                await asyncio.Event().wait()
            with patch.object(server.asyncio, "create_subprocess_exec", return_value=process), \
                    patch.object(server, "bounded_stdout", side_effect=output), patch.object(server.os, "killpg") as kill:
                task = asyncio.create_task(server.chrome_lodging("호텔"))
                if outcome == "cancel":
                    await asyncio.sleep(0)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
                else:
                    self.assertEqual((await task)["status"], outcome)
            kill.assert_called_once()
            process.wait.assert_awaited_once()
            self.assertFalse(server.lock.locked())


if __name__ == "__main__":
    unittest.main()
