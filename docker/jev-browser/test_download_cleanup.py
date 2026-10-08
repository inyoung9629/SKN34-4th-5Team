"""Run inside the built image as node with its Chromium sandbox seccomp policy.

Real CDP download, no paid APIs, no external destination or global deletion.
"""
import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("browser_server", "/opt/service/server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
spawn = asyncio.create_subprocess_exec


async def main():
    for mode in ("success", "timeout", "cancel"):
        with tempfile.TemporaryDirectory(prefix="sentinel-receipt-") as receipt_root:
            receipt = Path(receipt_root) / "path"
            async def fixture(*args, **kwargs):
                env = kwargs["env"]
                env.update(SENTINEL_MODE=mode, SENTINEL_RECEIPT=str(receipt))
                return await spawn("node", "/checks/download-sentinel.mjs", **kwargs)
            with patch.object(server.asyncio, "create_subprocess_exec", fixture), patch.dict(os.environ, {"JEV_TIMEOUT_SECONDS": "5" if mode == "timeout" else "60"}):
                task = asyncio.create_task(server.jev_browse("https://example.com", "fixture download"))
                for _ in range(300):
                    if receipt.exists() or task.done():
                        break
                    await asyncio.sleep(.05)
                assert receipt.exists(), f"{mode}: download not completed; result={task.result() if task.done() else 'running'}"
                path = Path(receipt.read_text())
                if mode == "timeout":
                    assert (await task)["status"] == "timeout"
                elif mode == "cancel":
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                else:
                    assert (await task)["result"]["status"] == "done"
                assert not path.exists(), f"{mode}: downloaded file leaked"
                assert not path.parents[2].exists(), f"{mode}: request root leaked"
                assert not server.lock.locked()
                print(f"PASS real CDP download cleanup: {mode}")


asyncio.run(main())
