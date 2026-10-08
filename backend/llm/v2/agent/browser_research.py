"""Lazy async MCP calls behind the existing synchronous LangChain tool boundary."""
import asyncio
import os
from datetime import timedelta
from functools import cache

import json

from langchain_core.tools import ToolException


@cache
def research_model():
    from django.conf import settings
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=os.getenv("RESEARCH_MODEL", "gpt-6-luna"),
                      base_url=os.getenv("RESEARCH_BASE_URL", "https://api.openai.com/v1"),
                      api_key=os.getenv("RESEARCH_API_KEY") or os.getenv("OPENAI_API_KEY"),
                      timeout=30, max_retries=0, reasoning_effort="low", use_responses_api=True,
                      max_tokens=settings.USAGE_MAX_CALL_OUTPUT_TOKENS)


async def discover_tools():
    token = os.getenv("JEV_MCP_TOKEN", "").strip()
    if not token:
        raise RuntimeError("JEV_MCP_TOKEN is required")
    from langchain_mcp_adapters.client import MultiServerMCPClient
    client = MultiServerMCPClient({"browser": {
        "transport": "streamable_http", "url": os.getenv("JEV_MCP_URL", "http://jev-browser:8080/mcp"),
        "headers": {"Authorization": f"Bearer {token}"},
        "timeout": timedelta(seconds=135), "sse_read_timeout": timedelta(seconds=140),
    }})
    # Sessions/tools belong to this loop, never cache them across sync invocations.
    tools = await client.get_tools()
    names = {"jev_browse", "jev_read_body"}
    selected = [t for t in tools if t.name in names]
    if {t.name for t in selected} != names:
        raise RuntimeError("Required JEV MCP tools unavailable")
    return selected


async def cancellable_call(awaitable):
    from llm.service.chat_runs import check_cancelled
    task = asyncio.create_task(awaitable)
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.25)
            check_cancelled()
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


# ponytail: one process-wide browser admission, no queue; scale by separate browser services.
from threading import Lock
_admission = Lock()


def web_body(url):
    """Deterministic specialist mode; never ask an LLM whether to fetch or accept generated text."""
    import json
    from llm.service.attachments import reference_url, MAX_TEXT
    from llm.service.chat_runs import check_cancelled
    url = reference_url(url)
    check_cancelled()
    if not _admission.acquire(blocking=False):
        return {"status": "busy", "source_url": url}
    try:
        result = asyncio.run(browse(url, "", body=True))
        check_cancelled()
        if isinstance(result, list):
            result = json.loads(next(block["text"] for block in result if block.get("type") == "text"))
        if not isinstance(result, dict):
            return {"status": "error", "source_url": url}
        if result.get("status") != "ok":
            return {"status": result.get("status", "error"), "source_url": url}
        text = result.get("body")
        if not isinstance(text, str) or not text or result.get("source_kind") != "rendered_dom_snapshot":
            return {"status": "partial", "source_url": url}
        if len(text.encode("utf-8")) > MAX_TEXT:
            return {"status": "overflow", "source_url": url}
        source = reference_url(result.get("source_url", url))
        return {"status": "ok", "source_url": source, "body": text}
    finally:
        _admission.release()


def read_evidence(reader, url, terms):
    """Existing conservative allowlisted reader, owned by the web service, no agent recursion."""
    from llm.service.chat_runs import check_cancelled
    check_cancelled()
    result = reader.read(url, terms, complete_text=True)
    check_cancelled()
    return result


def structured_search(**kwargs):
    """Domain evidence schemas stay with their validators; external search belongs here."""
    from openai import OpenAI
    from llm.service import usage
    from llm.service.chat_runs import check_cancelled
    check_cancelled()
    from django.conf import settings
    timeout = kwargs.pop("timeout", 45)
    domains = kwargs.pop("allowed_domains", None)
    search = {"type": "web_search", "search_context_size": "medium"}
    if domains:
        search["filters"] = {"allowed_domains": domains}
    kwargs.update(tools=[search], tool_choice="required", max_tool_calls=8,
                  include=["web_search_call.action.sources"], store=False)
    kwargs["max_output_tokens"] = min(kwargs.get("max_output_tokens", settings.USAGE_MAX_CALL_OUTPUT_TOKENS), settings.USAGE_MAX_CALL_OUTPUT_TOKENS)
    response = None
    try:
        response = OpenAI(timeout=timeout, max_retries=0).responses.create(**kwargs)
        check_cancelled()
        return response
    finally:
        reported = getattr(response, "usage", None)
        usage.record_external(getattr(reported, "input_tokens", None), getattr(reported, "output_tokens", None))


def result_status(value):
    """MCP text blocks/structured envelopes are evidence, not proof of success."""
    if isinstance(value, str):
        try:
            return result_status(json.loads(value))
        except (ValueError, TypeError):
            return "error"
    if isinstance(value, list):
        statuses = [result_status(v.get("text")) for v in value
                    if isinstance(v, dict) and v.get("type") == "text"]
        return next((s for s in statuses if s != "ok"), "ok" if statuses else "error")
    if not isinstance(value, dict):
        return "error"
    status = value.get("status")
    if status is not None and status not in {"ok", "done"}:
        return status if status in {"blocked", "busy", "timeout", "error", "partial", "overflow", "cancelled"} else "error"
    if "result" in value:
        return result_status(value["result"])
    return "ok" if status in {"ok", "done"} else "error"


async def browse(url, goal, *, body=False):
    tools = await cancellable_call(discover_tools())
    browser = next(t for t in tools if t.name == ("jev_read_body" if body else "jev_browse"))
    return await cancellable_call(browser.ainvoke({"url": url} if body else {"url": url, "goal": goal}))


def direct_tools():
    """Keep discovered schemas; the adapter's session-free coroutine opens its own loop-local session."""
    tools = asyncio.run(cancellable_call(discover_tools()))
    adapted = []
    for discovered in tools:
        def invoke(_tool=discovered, **arguments):
            from llm.service.attachments import reference_url, MAX_TEXT
            from llm.service.chat_runs import check_cancelled
            arguments["url"] = reference_url(arguments["url"])
            check_cancelled()
            if not _admission.acquire(blocking=False):
                raise ToolException(json.dumps({"status": "busy"}))
            try:
                try:
                    content, artifact = asyncio.run(cancellable_call(_tool.coroutine(**arguments)))
                except Exception as exc:
                    from llm.service.chat_runs import Stopped
                    if isinstance(exc, Stopped):
                        raise
                    raise ToolException(json.dumps({"status": "timeout" if isinstance(exc, TimeoutError) else "error"})) from exc
                check_cancelled()
                structured = artifact.get("structured_content") if isinstance(artifact, dict) else None
                if len(json.dumps(content, ensure_ascii=False).encode()) > MAX_TEXT or (structured is not None and len(json.dumps(structured, ensure_ascii=False).encode()) > MAX_TEXT):
                    raise ToolException(json.dumps({"status": "overflow"}))
                status = result_status(content)
                if status == "ok" and structured is not None:
                    status = result_status(structured)
                if status != "ok":
                    raise ToolException(json.dumps({"status": status, "source_url": arguments["url"]}))
                return content, artifact
            finally:
                _admission.release()
        adapted.append(discovered.model_copy(update={"func": invoke, "coroutine": None,
                                                     "handle_tool_error": True}))
    return adapted
