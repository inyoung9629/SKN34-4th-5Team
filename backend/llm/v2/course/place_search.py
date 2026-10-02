"""Search-only pilot for existing catalogue places; not a menu/review verifier.

The provider returns discovery links/snippets, never verified facts. No LLM,
paid fallback, page crawler, database writes, or automatic pagination/retries.
Production FoodVerifier/ReviewVerifier are deliberately unchanged until quality
and a separate, permitted page-reading step have been tested.
"""
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
import ipaddress
import json
import re
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _plain(value, limit):
    if not isinstance(value, str):
        return ""
    parser = _Text()
    parser.feed(value[:10000])
    return " ".join(unescape(" ".join(parser.parts)).split())[:limit]


def _public_citation(value):
    """Lexical citation guard only: returned URLs are NOT fetched here."""
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme not in ("http", "https") or not host
                or parsed.username or parsed.password or "\\" in value
                or any(c.isspace() or ord(c) < 32 for c in value)
                or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))
                or parsed.port not in (None, 80, 443)):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if "." not in host or host.replace(".", "").isdigit():
                return None
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, parsed.query, ""))
    except ValueError:
        return None


def place_query(place, terms=()):
    """Deterministic query: known business name/address + at most 3 keywords.

    Never pass chat history, user profiles, keys or private user locations here.
    Strip SearXNG engine/language bang syntax from catalogue/user keywords.
    """
    name = place.get("name")
    address = place.get("address")
    if not isinstance(name, str) or not name.strip() or not isinstance(address, str) or not address.strip():
        raise ValueError("place_name_and_business_address_required")
    if isinstance(terms, str) or len(terms) > 3 or any(not isinstance(t, str) for t in terms):
        raise ValueError("at_most_three_keyword_strings_required")
    parts = [name[:100], address[:150], *[t[:40] for t in terms]]
    query = " ".join(" ".join(re.sub(r"[!:/\\]", " ", p).split()) for p in parts).strip()
    if len(query) > 400:
        raise ValueError("query_too_long")
    return query


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    engines: tuple[str, ...]
    # This is search result text, not a downloaded menu or customer review.
    body_read: Literal[False] = field(default=False, init=False)
    evidence_status: Literal["discovery_only"] = field(default="discovery_only", init=False)


@dataclass(frozen=True)
class SearchPacket:
    query: str
    status: Literal["ok", "partial", "empty", "unavailable"]
    hits: tuple[SearchHit, ...] = ()
    unavailable_engines: tuple[str, ...] = ()
    error: str | None = None
    engine_errors: tuple[tuple[str, str], ...] = ()
    # One local HTTP request can fan out to multiple upstream engines.
    search_requests: int = 1
    paid_search_calls: int = 0
    model_calls: int = 0
    provider: str = "searxng"


class PlaceSearchProvider(Protocol):
    def search(self, query: str) -> SearchPacket: ...


class SearXNGSearch:
    """Local JSON API adapter. An error returns unavailable, never another provider.

    Restrict the pilot endpoint to loopback, reject redirects, ignore environment
    proxies, cap the response size and use an explicit non-paid engine allowlist.
    No public/free instance is selected implicitly.
    """
    ALLOWED_ENGINES = frozenset(("duckduckgo", "brave"))
    MAX_RESPONSE_BYTES = 1_000_000

    def __init__(self, base_url="http://127.0.0.1:8888", *, engines=("duckduckgo", "brave"),
                 timeout=15.0, max_results=10, transport=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or parsed.port is None):
            raise ValueError("local_searxng_origin_with_port_required")
        if not engines or not set(engines) <= self.ALLOWED_ENGINES:
            raise ValueError("unsupported_search_engine")
        if not 1 <= max_results <= 20 or not 0 < timeout <= 30:
            raise ValueError("invalid_search_limits")
        self.endpoint = base_url.rstrip("/") + "/search"
        self.engines = tuple(dict.fromkeys(engines))
        self.timeout = timeout
        self.max_results = max_results
        self.transport = transport

    def search(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 400:
            raise ValueError("query_required_max_400_characters")
        if any(c in query for c in ("!", ":", "\n", "\r")):
            raise ValueError("engine_or_language_query_overrides_not_allowed")
        params = {"q": query, "format": "json", "language": "ko-KR", "categories": "general",
                  "engines": ",".join(self.engines), "pageno": 1, "safesearch": 1}
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False,
                              transport=self.transport) as client:
                with client.stream("GET", self.endpoint, params=params) as response:
                    response.raise_for_status()
                    if "application/json" not in response.headers.get("content-type", "").lower():
                        return SearchPacket(query, "unavailable", error="json_disabled_or_invalid_response")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.MAX_RESPONSE_BYTES:
                            return SearchPacket(query, "unavailable", error="search_response_too_large")
                        chunks.append(chunk)
                    payload = json.loads(b"".join(chunks))
            return self._packet(query, payload)
        except httpx.TimeoutException:
            return SearchPacket(query, "unavailable", error="search_timeout")
        except httpx.HTTPStatusError as exc:
            return SearchPacket(query, "unavailable", error=f"search_http_{exc.response.status_code}")
        except httpx.RequestError:
            return SearchPacket(query, "unavailable", error="search_connection_failed")
        except (ValueError, TypeError):
            return SearchPacket(query, "unavailable", error="invalid_search_response")

    def _packet(self, query, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("invalid_search_response")
        failures = payload.get("unresponsive_engines") or []
        if not isinstance(failures, list):
            raise ValueError("invalid_search_response")
        unavailable = tuple(sorted({str(f[0])[:80] for f in failures if isinstance(f, (list, tuple)) and f}))
        engine_errors = tuple((_plain(str(f[0]), 80), _plain(str(f[1]), 120))
                              for f in failures if isinstance(f, (list, tuple)) and len(f) >= 2)
        hits, seen = [], set()
        for item in payload["results"]:
            if not isinstance(item, dict):
                continue
            url = _public_citation(item.get("url"))
            engines = item.get("engines") or [item.get("engine")]
            if not isinstance(engines, list):
                continue
            engines = tuple(sorted({e for e in engines if isinstance(e, str) and e in self.engines}))
            if not url or url in seen or not engines:
                continue
            hits.append(SearchHit(url, _plain(item.get("title"), 200),
                                  _plain(item.get("content"), 500), engines))
            seen.add(url)
            if len(hits) >= self.max_results:
                break
        status = "partial" if hits and unavailable else "ok" if hits else "unavailable" if unavailable else "empty"
        return SearchPacket(query, status, tuple(hits), unavailable,
                            error="upstream_search_unavailable" if status == "unavailable" else None,
                            engine_errors=engine_errors)
