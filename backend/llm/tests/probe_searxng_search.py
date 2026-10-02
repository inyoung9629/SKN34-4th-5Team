"""Manual, retrieval-only probe. Does not import a model client or load .env.

From repository root (PowerShell):
  ./docker/start-searxng.ps1
  ./.venv/Scripts/python.exe -X utf8 backend/llm/tests/probe_searxng_search.py
  ./.venv/Scripts/python.exe -X utf8 backend/llm/tests/probe_searxng_search.py --query '잠실 돈까스 메뉴'
  ./docker/start-searxng.ps1 -Action stop

Default: three previously tested businesses, one query per business, sequential.
Each local search can contact two engines. No retries, extra pages, LLM calls,
page downloads, or paid fallback. Console-only output; no DB/evidence cache.
Snippets are deliberately omitted from the report; body_read remains false.
This is not a menu accuracy evaluation or an end-to-end chatbot integration.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from llm.v2.course.place_search import SearXNGSearch, place_query  # noqa: E402


CASES = (
    ("J076", "베스킨라빈스은마 사거리점", "서울특별시 강남구 도곡로 504", "카페라떼"),
    ("J024", "깐부치킨잠실새내역점", "서울특별시 송파구 백제고분로7길 19", "후라이드 치킨"),
    ("J255", "동경", "서울특별시 강남구 영동대로 513", "돈카츠"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8888")
    parser.add_argument("--query", help="Run only this explicit public-business query instead of all three cases")
    args = parser.parse_args()
    search = SearXNGSearch(args.base_url)
    queries = [("custom", args.query)] if args.query else [
        (key, place_query({"name": name, "address": address}, [menu, "메뉴"]))
        for key, name, address, menu in CASES]
    for index, (key, query) in enumerate(queries):
        if index:
            time.sleep(2)  # Small sequential smoke test, not a batch crawler.
        started = time.monotonic()
        result = search.search(query)
        print(json.dumps({
            "id": key, "query": result.query, "status": result.status,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "returned_hits": len(result.hits), "unavailable_engines": result.unavailable_engines,
            "engine_errors": result.engine_errors,
            "error": result.error, "search_requests": result.search_requests,
            "paid_search_calls": result.paid_search_calls, "model_calls": result.model_calls,
            "hits": [{"title": hit.title, "url": hit.url, "engines": hit.engines,
                      "body_read": hit.body_read, "evidence_status": hit.evidence_status}
                     for hit in result.hits],
        }, ensure_ascii=False), flush=True)
        if result.status == "unavailable":
            # Do not keep sending the rest of the batch into a blocked engine.
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
