"""Offline tracing privacy and fail-open checks; no provider/network calls."""
import asyncio
from contextlib import contextmanager
import json
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_server import server


class TracingTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_and_missing_key(self):
        for env in ({"JEV_LANGSMITH_TRACING": "false", "LANGSMITH_API_KEY": "KEY_SENTINEL"},
                    {"JEV_LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": ""}):
            server.lock = asyncio.Lock()
            with patch.dict(os.environ, env), patch.object(server, "Client") as client:
                async with server.lock:
                    self.assertEqual((await server.jev_read_body("https://example.com"))["status"], "busy")
                client.assert_not_called()

    async def test_allowlist_lifecycle_and_sdk_failures(self):
        records = []
        @contextmanager
        def traced(name, **kwargs):
            row = {"name": name, "inputs": kwargs["inputs"]}
            records.append(row)
            run = Mock()
            run.end.side_effect = lambda **fields: row.update(outputs=fields["outputs"])
            yield run
        @contextmanager
        def enabled(**kwargs):
            yield
        env = {"JEV_LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "KEY_SENTINEL"}
        url = "https://example.com/PATH_SENTINEL?QUERY_SENTINEL#FRAGMENT_SENTINEL"
        with patch.dict(os.environ, env), patch.object(server, "Client", Mock()), \
                patch.object(server, "tracing_client", None), patch.object(server, "trace", traced, create=True), \
                patch.object(server, "tracing_context", enabled, create=True):
            for tool in (server.jev_browse, server.jev_read_body):
                server.lock = asyncio.Lock()
                args = (url, "GOAL_SENTINEL") if tool is server.jev_browse else (url,)
                async with server.lock:
                    self.assertEqual((await tool(*args))["status"], "busy")
                self.assertEqual(records[-1]["outputs"]["status"], "busy")
                for outcome in ("ok", "blocked", "timeout", "overflow", "cancelled", "error"):
                    process = AsyncMock(pid=99999999, returncode=0)
                    process.stdout.read.return_value = b""
                    body = {"status": "ok", "body": "BODY_SENTINEL", "source_url": url}
                    data = {"status": "done", "goal": "GOAL_SENTINEL"} if tool is server.jev_browse else body
                    if outcome == "blocked": data["status"] = "blocked"
                    failure = {"timeout": TimeoutError(), "overflow": OverflowError(),
                               "cancelled": asyncio.CancelledError(), "error": RuntimeError("SECRET_SENTINEL")}.get(outcome)
                    with patch.object(server.asyncio, "create_subprocess_exec", return_value=process), \
                            patch.object(server.os, "killpg"), \
                            patch.object(server, "bounded_stdout", AsyncMock(side_effect=failure, return_value=json.dumps(data).encode())):
                        if outcome in {"cancelled", "error"}:
                            with self.assertRaises(type(failure)): await tool(*args)
                        else:
                            await tool(*args)
                    self.assertEqual(records[-1]["outputs"]["status"], outcome)
                    self.assertFalse(server.lock.locked())
            await server.jev_read_body("https://USER_SENTINEL:PASS_SENTINEL@example.com")
            self.assertEqual(records[-1]["outputs"]["status"], "blocked")
            for row in records:
                self.assertEqual(set(row["inputs"]), {"source_domain"})
                self.assertEqual(set(row["outputs"]), {"status", "source_domain", "body_bytes", "duration_ms"})
                self.assertEqual(row["outputs"]["source_domain"], "example.com")
                self.assertNotIn("SENTINEL", json.dumps(row))
            self.assertTrue(any(row["outputs"]["body_bytes"] == len("BODY_SENTINEL") for row in records))
            for failing in ("Client", "trace"):
                with patch.object(server, "tracing_client", None), patch.object(server, failing, side_effect=RuntimeError("KEY_SENTINEL")):
                    self.assertEqual((await server.jev_read_body("file:///blocked"))["status"], "blocked")
            @contextmanager
            def end_failure(*args, **kwargs):
                yield Mock(end=Mock(side_effect=RuntimeError("KEY_SENTINEL")))
            with patch.object(server, "trace", end_failure):
                self.assertEqual((await server.jev_read_body("file:///blocked"))["status"], "blocked")
            @contextmanager
            def exit_failure(*args, **kwargs):
                yield Mock()
                raise RuntimeError("KEY_SENTINEL")
            with patch.object(server, "trace", exit_failure):
                self.assertEqual((await server.jev_read_body("file:///blocked"))["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
