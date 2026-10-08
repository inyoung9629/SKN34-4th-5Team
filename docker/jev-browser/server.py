"""Single-flight upstream CLI, served by the official MCP Streamable HTTP SDK."""
import asyncio
import json
import hashlib
import secrets
from datetime import datetime, timezone
from functools import wraps
from time import perf_counter

try:
    from langsmith import Client, trace, tracing_context
except ImportError:
    Client = None

tracing_client = None


def request_traced(function):
    """Only allowlisted request summaries cross the tracing boundary."""
    @wraps(function)
    async def wrapped(url, *args, **kwargs):
        global tracing_client
        started = perf_counter()
        domain = ""
        status, body_bytes = "error", 0
        try:
            domain = urlsplit(url).hostname or ""
        except ValueError:
            pass
        try:
            result = await function(url, *args, **kwargs)
            status = result.get("status", "ok")
            if isinstance(result.get("result"), dict):
                status = result["result"].get("status", "error")
                if status == "done":
                    status = "ok"
            if result.get("reason") == "upstream_stdout_limit_exceeded":
                status = "overflow"
            if status not in {"ok", "busy", "blocked", "timeout", "overflow", "partial", "error"}:
                status = "error"
            body = result.get("body")
            if status == "ok" and isinstance(body, str):
                body_bytes = len(body.encode("utf-8"))
            return result
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except ValueError:
            status = "blocked"
            raise
        finally:
            if Client is not None and os.getenv("JEV_LANGSMITH_TRACING", "false").lower() == "true" and os.getenv("LANGSMITH_API_KEY"):
                try:
                    if tracing_client is None:
                        tracing_client = Client(timeout_ms=2000, omit_traced_runtime_info=True)
                    # Trace only after execution: no request exception or raw result reaches the SDK.
                    with tracing_context(enabled=True):
                        with trace(function.__name__, run_type="tool", inputs={"source_domain": domain},
                                   client=tracing_client, parent="ignore") as run:
                            try:
                                run.end(outputs={"status": status, "source_domain": domain,
                                                 "body_bytes": body_bytes, "duration_ms": round((perf_counter() - started) * 1000)},
                                        end_time=datetime.now(timezone.utc))
                            except Exception:
                                pass
                except Exception:
                    pass
    return wrapped

from starlette.responses import Response
import shutil
from pathlib import Path
import os
import signal
import tempfile
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP("jev-browser", host="0.0.0.0", port=8080, stateless_http=True,
              transport_security=TransportSecuritySettings(
                  allowed_hosts=["jev-browser:*", "127.0.0.1:*", "localhost:*"],
                  allowed_origins=["http://jev-browser:*", "http://127.0.0.1:*", "http://localhost:*"],
              ))
# ponytail: no queue; callers retry busy rather than accumulate browser work.
lock = asyncio.Lock()


class BearerAuth:
    def __init__(self, app, token):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = [value for name, value in scope["headers"] if name == b"authorization"]
            parts = headers[0].split() if len(headers) == 1 else []
            if len(parts) != 2 or parts[0].lower() != b"bearer" or not secrets.compare_digest(parts[1], self.token):
                await Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
        await self.app(scope, receive, send)


def authenticated_app():
    token = os.getenv("JEV_MCP_TOKEN", "").strip()
    if not token:
        raise RuntimeError("JEV_MCP_TOKEN is required")
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuth, token=token)
    return app


async def bounded_stdout(process, limit):
    output = bytearray()
    while chunk := await process.stdout.read(min(65536, limit + 1 - len(output))):
        if len(output) + len(chunk) > limit:
            raise OverflowError("upstream stdout limit exceeded")
        output.extend(chunk)
    await process.wait()
    return output


@mcp.tool()
@request_traced
async def jev_browse(url: str, goal: str, max_steps: int = 20) -> dict:
    """Research public web evidence; returns upstream observations and source URL, not recommendations."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Public HTTP(S) URL required")
    if not goal.strip() or len(goal) > 8000 or not 1 <= max_steps <= 40:
        raise ValueError("Goal required (<=8000 characters), max_steps 1..40")
    if lock.locked():
        return {"status": "busy", "retryable": True}
    async with lock:
        with tempfile.TemporaryDirectory(prefix="jev-") as profile:
            temporary = str(Path(profile) / "tmp")
            Path(temporary).mkdir()
            env = {**os.environ, "JEV_PROFILE": profile, "JEV_PROVIDER": "openrouter",
                   "TMPDIR": temporary, "TMP": temporary, "TEMP": temporary}
            process = await asyncio.create_subprocess_exec(
                "node", "/opt/jev/bundled/cli.mjs", "--url", url, "--goal", goal,
                "--max-steps", str(max_steps), "--stop-at-challenge",
                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                stdout = await asyncio.wait_for(bounded_stdout(process, 1024 * 1024), float(os.getenv("JEV_TIMEOUT_SECONDS", "120")))
                if process.returncode not in (0, 2):
                    return {"status": "error", "reason": "upstream_browser_failed", "source_url": url}
                return {"source_url": url, "result": json.loads(stdout)}
            except OverflowError:
                return {"status": "error", "reason": "upstream_stdout_limit_exceeded", "source_url": url}
            except TimeoutError:
                return {"status": "timeout", "source_url": url}
            finally:
                # Kill the entire CLI/Chrome process group, including on cancellation.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # Drain without retaining bytes so asyncio can finish pipe transport/reaping.
                while await process.stdout.read(65536):
                    pass
                await process.wait()
                lock_key = hashlib.sha1(profile.encode()).hexdigest()[:12]
                shutil.rmtree(Path.home() / ".jev-browse" / f"run-{lock_key}.lock", ignore_errors=True)


@mcp.tool()
@request_traced
async def jev_read_body(url: str) -> dict:
    """Return genuine rendered DOM visible text, never an LLM observation or original HTML claim."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        return {"status": "blocked", "source_url": url}
    if lock.locked():
        return {"status": "busy", "source_url": url}
    async with lock:
        with tempfile.TemporaryDirectory(prefix="jev-body-") as profile:
            process = await asyncio.create_subprocess_exec(
                "node", "/opt/service/read-body.mjs", url, profile,
                env={**os.environ, "TMPDIR": profile}, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
            )
            try:
                raw = await asyncio.wait_for(bounded_stdout(process, 4 * 1024 * 1024), 45)
                if process.returncode != 0:
                    return {"status": "blocked", "source_url": url}
                result = json.loads(raw)
                if result.get("status") == "ok" and (not isinstance(result.get("body"), str) or len(result["body"].encode("utf-8")) > 2 * 1024 * 1024):
                    return {"status": "overflow", "source_url": url}
                return result
            except OverflowError:
                return {"status": "overflow", "source_url": url}
            except TimeoutError:
                return {"status": "timeout", "source_url": url}
            finally:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                while await process.stdout.read(65536): pass
                await process.wait()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(authenticated_app(), host="0.0.0.0", port=8080, access_log=False)
