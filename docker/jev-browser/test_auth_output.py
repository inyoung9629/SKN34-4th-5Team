"""Real HTTP MCP authentication and real subprocess overflow cleanup; no paid APIs."""
import asyncio
import importlib.util
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch

import httpx
import uvicorn
from datetime import timedelta
from langchain_mcp_adapters.client import MultiServerMCPClient

spec = importlib.util.spec_from_file_location("browser_server", Path(__file__).with_name("server.py"))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
spawn = asyncio.create_subprocess_exec


async def main():
    with patch.dict(os.environ, {"JEV_MCP_TOKEN": ""}):
        try:
            server.authenticated_app()
            raise AssertionError("missing server token accepted")
        except RuntimeError:
            pass
    # Fixed non-secret test value, never a runtime credential.
    with patch.dict(os.environ, {"JEV_MCP_TOKEN": "fixture-only"}):
        app = server.authenticated_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    http_server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    serving = asyncio.create_task(http_server.serve(sockets=[sock]))
    try:
        while not http_server.started:
            if serving.done():
                await serving
            await asyncio.sleep(.01)
        url = f"http://127.0.0.1:{port}/mcp"
        async with httpx.AsyncClient() as client:
            for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic fixture-only"}):
                response = await client.post(url, headers=headers, json={})
                assert response.status_code == 401
                assert response.headers["www-authenticate"] == "Bearer"
        client = MultiServerMCPClient({"browser": {"transport": "streamable_http", "url": url,
            "headers": {"Authorization": "Bearer fixture-only"}, "timeout": timedelta(seconds=10)}})
        tools = await client.get_tools()
        browser = next(t for t in tools if t.name == "jev_browse")
        async with server.lock:
            result = await browser.ainvoke({"url": "https://example.com", "goal": "read"})
            assert "busy" in str(result)
        from llm.v2.agent.browser_research import browse
        with patch.dict(os.environ, {"JEV_MCP_TOKEN": "fixture-only", "JEV_MCP_URL": url}):
            async with server.lock:
                assert "busy" in str(await browse("https://example.com", "read"))
        print("PASS real MCP auth: missing/wrong/non-Bearer denied, valid list/invoke and backend headers, absent config fails closed")
        with tempfile.TemporaryDirectory(prefix="overflow-receipt-") as root:
            receipt = Path(root) / "receipt"
            pid_receipt = Path(root) / "pid"
            async def fixture(*args, **kwargs):
                code = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(os.environ['JEV_PROFILE']); pathlib.Path(sys.argv[2]).write_text(str(os.getpid())); pathlib.Path(os.environ['TMPDIR'],'download').write_bytes(b'sentinel'); sys.stdout.buffer.write(b'x' * (2*1024*1024)); sys.stdout.flush(); time.sleep(60)"
                return await spawn(sys.executable, "-c", code, str(receipt), str(pid_receipt), **kwargs)
            with patch.object(server.asyncio, "create_subprocess_exec", fixture):
                result = await browser.ainvoke({"url": "https://example.com", "goal": "overflow fixture"})
            assert "upstream_stdout_limit_exceeded" in str(result), result
            assert not Path(receipt.read_text()).exists()
            assert not server.lock.locked()
            with patch.object(server.asyncio, "create_subprocess_exec", fixture):
                again = await browser.ainvoke({"url": "https://example.com", "goal": "next admitted"})
            assert "upstream_stdout_limit_exceeded" in str(again), again
            try:
                os.kill(int(pid_receipt.read_text()), 0)
                raise AssertionError("child not reaped")
            except ProcessLookupError:
                pass
            print("PASS real MCP stdout overflow: explicit error, child reaped, owned download/root removed, lock released")
    finally:
        http_server.should_exit = True
        await serving
        sock.close()


asyncio.run(main())
