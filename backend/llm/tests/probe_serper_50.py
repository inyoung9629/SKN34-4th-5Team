"""Opt-in Serper-only discovery probe. Never calls a model or fetches result pages.

30 exact queries from the saved Luna run + 20 predeclared keyword queries.
One page / 10 organic results, one request at a time, no retry or fallback.
Results are evaluation artifacts, NOT verified restaurant facts or a RAG corpus.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
from statistics import mean, median
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
from llm.v2.course.place_search import _plain, _public_citation

PRIOR = ROOT / "output/evals/search-30-20261001-145126"
ENDPOINT = "https://google.serper.dev/search"
KST = timezone(timedelta(hours=9))
MAX_REQUESTS = 50
# Deliberately predeclared before seeing results. These are questions, not menu facts.
TARGETS = [
    ("S03", "menu", "BHC 한강뚝섬 3호점", "후라이드치킨 양념치킨 메뉴"),
    ("S05", "menu", "지구당 현대타워점", "규동 돈카츠 메뉴"),
    ("S07", "menu", "일품돼지국밥 휘문고사거리점", "돼지국밥 수육 메뉴"),
    ("S11", "menu", "이화원 코엑스", "짜장면 짬뽕 메뉴"),
    ("S23", "menu", "온더보더 코엑스도심공항점", "타코 퀘사디아 메뉴"),
    ("S02", "menu", "바로군 삼전역점", "소금빵 크루아상 메뉴"),
    ("S08", "menu", "멜랑쥬", "크루아상 식빵 메뉴"),
    ("S12", "menu", "르뱅룰즈 선릉", "사워도우 크루아상 메뉴"),
    ("S18", "menu", "메가커피 잠실새내역점", "카페라떼 디카페인 메뉴"),
    ("S30", "menu", "카페 만월경 삼전역점", "아메리카노 라떼 메뉴"),
    ("S06", "review", "이치고", "조용한 대화 후기"),
    ("S09", "review", "벌스", "데이트 분위기 후기"),
    ("S13", "review", "잠실생삼겹살", "깔끔한 위생 후기"),
    ("S26", "review", "생활맥주 잠실장미점", "단체 좌석 후기"),
    ("S27", "review", "훠궈야 파르나스몰점", "혼밥 후기"),
    ("S01", "review", "블루미니", "조용한 후기"),
    ("S14", "review", "코지카커피", "데이트 분위기 후기"),
    ("S17", "review", "그레이커피", "깔끔한 후기"),
    ("S21", "review", "로칼커피", "넓은 좌석 후기"),
    ("S24", "review", "만월경 잠실레이크팰리스점", "콘센트 공부 후기"),
]


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append(path, row):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def digest(data):
    return sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def build_cases(candidates):
    if len(candidates) != 30 or len({p["case_id"] for p in candidates}) != 30:
        raise ValueError("Expected the saved 30 distinct candidates")
    cases = [{**p, "case_id": f'P{i:02}', "prior_case_id": p["case_id"], "group": "baseline"}
             for i, p in enumerate(candidates, 1)]
    lookup = {p["case_id"]: p for p in candidates}
    for i, (old_id, group, alias, terms) in enumerate(TARGETS, 31):
        p = lookup[old_id]
        district = p["address"].split()[1]
        cases.append({**p, "case_id": f"P{i:02}", "prior_case_id": old_id, "group": group,
                      "query": f"{alias} {district} {terms}", "alias_hypothesis": alias,
                      "requested_terms": terms,
                      "query_change": "Manual name spacing/brand hypothesis + district, no full street address; not an isolated keyword-only experiment"})
    return cases


def prepare(folder):
    previous = json.loads((PRIOR / "manifest.json").read_text(encoding="utf-8"))
    cases = build_cases(previous["candidates"])
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"prepared_at": datetime.now(KST).isoformat(), "cases": cases,
                "cases_hash": digest(cases), "prior_folder": str(PRIOR.relative_to(ROOT)),
                "max_requests": MAX_REQUESTS, "expected_max_credits": 50,
                "parameters": {"gl": "kr", "hl": "ko", "num": 10, "page": 1},
                "minimum_gap_seconds": 1, "timeout_seconds": 30,
                "new_model_calls": 0, "result_page_fetches": 0,
                "retry": False, "fallback": False, "production_changes": False,
                "stop_on": "Any API/network/schema error, or observed credits != 1",
                "scope": "Discovery relevance from SERP titles/snippets, not current menu/review truth"}
    write_json(folder / "manifest.json", manifest)
    emit({"folder": str(folder), "requests": len(cases), "places": len({p["placeId"] for p in cases}),
          "groups": dict(Counter(p["group"] for p in cases)), "new_model_calls": 0})


def parse_payload(data):
    if not isinstance(data, dict) or data.get("error") or data.get("message"):
        raise ValueError("Invalid API response")
    organic = data.get("organic", [])
    if not isinstance(organic, list):
        raise ValueError("Invalid organic result schema")
    hits, seen = [], set()
    for item in organic[:10]:
        if not isinstance(item, dict):
            continue
        url = _public_citation(item.get("link"))
        if not url or url in seen:
            continue
        seen.add(url)
        hits.append({"url": url, "title": _plain(item.get("title"), 250),
                     "snippet": _plain(item.get("snippet"), 600),
                     "date_label": _plain(item.get("date"), 60),
                     "body_read": False, "evidence_status": "discovery_only"})
    credit = data.get("credits")
    if credit is not None and (type(credit) not in (int, float) or credit < 0):
        raise ValueError("Invalid credits schema")
    graph = data.get("knowledgeGraph") or {}
    if not isinstance(graph, dict):
        graph = {}
    knowledge = {k: _plain(graph.get(k), 400) for k in ("title", "type", "description") if graph.get(k)}
    attributes = graph.get("attributes") or {}
    if isinstance(attributes, dict):
        knowledge["attributes"] = {str(k)[:80]: _plain(v, 250) for k, v in list(attributes.items())[:12]
                                   if isinstance(v, str)}
    parameters = data.get("searchParameters") or {}
    if not isinstance(parameters, dict):
        raise ValueError("Invalid search parameters schema")
    # Do not copy arbitrary response fields or headers (could echo credentials).
    return {"status": "ok" if hits else "empty", "hits": hits,
            "organic_count": len(hits), "credits_reported": credit,
            "echoed_query": _plain(parameters.get("q"), 500), "knowledge_graph": knowledge}


def search(client, key, query):
    started = time.monotonic()
    row = {"http_status": None, "credits_reported": None, "hits": [], "organic_count": 0,
           "body_read": False, "model_calls": 0}
    try:
        with client.stream("POST", ENDPOINT, headers={"X-API-KEY": key},
                           json={"q": query, "gl": "kr", "hl": "ko", "num": 10, "page": 1}) as response:
            row["http_status"] = response.status_code
            if response.status_code != 200:
                row.update(status="api_error", error_type="http_status", retry_after=response.headers.get("retry-after"))
            else:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_000_000:
                        raise ValueError("Oversized response")
                row.update(parse_payload(json.loads(body)))
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        # Never log exception text: request objects can contain the API key.
        row.update(status="api_error", error_type=type(exc).__name__)
    row["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return row


def validate(manifest):
    cases = manifest["cases"]
    if (len(cases) != MAX_REQUESTS or digest(cases) != manifest["cases_hash"]
            or len({p["case_id"] for p in cases}) != MAX_REQUESTS
            or any(not isinstance(p["query"], str) or not 1 <= len(p["query"]) <= 500 for p in cases)):
        raise ValueError("Invalid or changed manifest; no searches sent")


def run(folder, manifest):
    from dotenv import dotenv_values
    validate(manifest)
    key = dotenv_values(ROOT / ".env").get("Serper_API_KEY")
    if not key or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError("Missing or malformed Serper_API_KEY in root .env")
    # Exclusive start marker: re-running an interrupted job cannot spend again.
    with (folder / "run-started.json").open("x", encoding="utf-8") as stream:
        json.dump({"started_at": datetime.now(KST).isoformat(), "limit": MAX_REQUESTS}, stream)
    stopped, attempted = None, 0
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False,
                      transport=httpx.HTTPTransport(retries=0)) as client:
        for p in manifest["cases"]:
            row = {"case_id": p["case_id"], "group": p["group"], "query": p["query"],
                   "started_at": datetime.now(KST).isoformat()}
            if stopped:
                row.update(status="skipped", reason=stopped)
            else:
                if attempted >= MAX_REQUESTS:
                    raise RuntimeError("Request cap reached")
                if attempted:
                    time.sleep(1)
                append(folder / "attempts.jsonl", {**row, "state": "request_start"})
                attempted += 1
                row.update(search(client, key, p["query"]))
                if row["status"] == "api_error":
                    stopped = "api_error_no_retry"
                elif row.get("credits_reported") not in (None, 1):
                    stopped = "unexpected_credit_charge"
            append(folder / "results.jsonl", row)
            emit({k: row.get(k) for k in ("case_id", "status", "http_status", "organic_count", "credits_reported", "elapsed_seconds")})
    summarize(folder, manifest)


def stats(rows):
    attempted = [r for r in rows if r["status"] != "skipped"]
    times = sorted(r["elapsed_seconds"] for r in attempted)
    return {"attempted": len(attempted), "status_counts": dict(Counter(r["status"] for r in rows)),
            "with_organic_results": sum(bool(r.get("hits")) for r in attempted),
            "organic_result_count": sum(r.get("organic_count", 0) for r in attempted),
            "credits_reported_sum": sum(r.get("credits_reported") or 0 for r in attempted),
            "unknown_credit_requests": sum(r.get("credits_reported") is None for r in attempted),
            "median_seconds": median(times) if times else None,
            "mean_seconds": mean(times) if times else None,
            "max_seconds": max(times) if times else None}


def summarize(folder, manifest):
    rows = read_rows(folder / "results.jsonl")
    baseline = [r for r in rows if r["group"] == "baseline"]
    prior_summary = json.loads((PRIOR / "summary.json").read_text(encoding="utf-8"))["providers"]["luna"]
    prior_rows = {r["case_id"]: r for r in read_rows(PRIOR / "luna-network-recovery.jsonl")}
    annotations = {}
    if (folder / "review.json").exists():
        annotations = json.loads((folder / "review.json").read_text(encoding="utf-8")).get("cases", {})
    lookup = {r["case_id"]: r for r in rows}
    intersections = []
    for p in manifest["cases"][:30]:
        current = lookup[p["case_id"]]
        old = prior_rows[p["prior_case_id"]]
        intersections.append({"case_id": p["case_id"], "query_identical": p["query"] == old["query"],
                              "shared_urls": sorted({h["url"] for h in current.get("hits", [])} & set(old["observed_urls"]))})
    summary = {"serper_all_50": stats(rows), "serper_baseline_30": stats(baseline),
               "groups": {group: stats([r for r in rows if r["group"] == group]) for group in ("baseline", "menu", "review")},
               "prior_luna_30_saved_only": prior_summary, "new_luna_calls": 0, "new_other_model_calls": 0,
               "target_page_fetches": 0, "url_overlap_not_quality": intersections,
               "manual_serp_review_count": len(annotations),
               "manual_identity_counts": dict(Counter(v["identity"] for v in annotations.values())),
               "manual_top3_review_by_group": {
                   group: {
                       "identity_signals_not_accuracy": dict(Counter(annotations[p["case_id"]]["identity"]
                           for p in manifest["cases"] if p["group"] == group and p["case_id"] in annotations)),
                       "condition_hints_not_verification": dict(Counter(annotations[p["case_id"]]["condition_hint"]
                           for p in manifest["cases"] if p["group"] == group and p["case_id"] in annotations
                           and "condition_hint" in annotations[p["case_id"]]))}
                   for group in ("baseline", "menu", "review")},
               "limitations": ["30 places / 50 queries, not 50 distinct places", "SERP snippets only; no body or current operating-status verification",
                               "Luna saved metadata has URLs but no snippets, so no fair menu/atmosphere accuracy comparison",
                               "Provider/time/locale/context sizes differ; speed comparison is descriptive, not controlled",
                               "Targeted queries also change aliases/address length, not just keywords",
                               "No provider balance/invoice checked; credits are the search response field"]}
    write_json(folder / "summary.json", summary)
    html = ['<!doctype html><html lang="ko"><meta charset="utf-8"><title>Serper 50회 검색 검증</title>',
            '<style>body{font:16px/1.65 sans-serif;max-width:1160px;margin:40px auto;padding:0 24px;color:#172337}table{border-collapse:collapse;width:100%}td,th{border:1px solid #d8dfe8;padding:9px;text-align:left}th{background:#edf2f8}details{border:1px solid #d8dfe8;border-radius:8px;padding:12px;margin:12px 0}small{color:#556276}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#145caa}li{margin:8px 0}</style>',
            '<h1>Serper 50회 검색 검증</h1><p>식당·주점 15곳 + 카페·베이커리 15곳. 기존 검색어 30회 + 메뉴 10회 + 후기 조건 10회. Luna/Jev 신규 호출 0회.</p>',
            '<p>Serper는 한국·한국어, 일반 검색 10개/1페이지. 순차 호출, 요청 사이 1초 휴식, 재시도·추가 검색·본문 열기 없음. HTTP 성공과 매장 정보의 정확성은 별개다.</p>',
            '<p>추가 20회는 사전 지정한 상호 표기·구 단위 주소로 검색했다. 별칭은 동일 업체라는 확정값이 아니라 검색 가설이다. 크레딧은 API 응답값이며 계정 잔액은 확인하지 않았다.</p>',
            '<h2>측정 결과</h2><table><tr><th>구분</th><th>요청</th><th>결과 있음</th><th>중앙 시간</th><th>크레딧/비용</th></tr>']
    for label, stat in (("Serper 전체", summary["serper_all_50"]), ("Serper 동일 검색어", summary["serper_baseline_30"])):
        html.append(f'<tr><td>{label}</td><td>{stat["attempted"]}</td><td>{stat["with_organic_results"]}</td><td>{stat["median_seconds"]}초</td><td>{stat["credits_reported_sum"]}크레딧 · 미상 {stat["unknown_credit_requests"]}건</td></tr>')
    html += [f'<tr><td>Luna 이전 기록만</td><td>{prior_summary["attempted"]}</td><td>{prior_summary["with_urls"]}</td><td>{prior_summary["median_seconds_attempted"]}초</td><td>이전 추정 ${prior_summary["estimated_cost_usd"]:.5f}; 이번 추가 $0</td></tr></table>',
             '<p>두 공급자의 결과 개수는 비교 단위가 다르다(Serper organic 상위 10개 vs Luna 도구 출처 목록). Luna에는 제목·요약 기록이 없어 음식/분위기 정확도의 우열은 판정하지 않는다. 검색 시점과 지역 설정도 다를 수 있다.</p>']
    if annotations:
        review = json.loads((folder / "review.json").read_text(encoding="utf-8"))
        html += ['<h2>검토 결론</h2><p>' + escape(review.get("conclusion", "")) + '</p>',
                 '<p>50개 검색의 상위 3개 제목·요약을 전부 검토하고 불명확한 사례는 추가 순위를 확인했다. 집계는 상위 3개 기준이다. 매장·메뉴·후기 사실 확정이나 현재 영업 여부 검증이 아니다.</p>',
                 '<h3>후속 로직 권고 — 이번에는 미적용</h3><ol>' + ''.join('<li>' + escape(t) + '</li>' for t in review.get("recommendations", [])) + '</ol>',
                 '<h3>제목·요약의 단서 분포 (정확도 아님)</h3><table><tr><th>질문 유형</th><th>매장 관련 단서</th><th>요청 조건 단서</th></tr>']
        for group, counts in summary["manual_top3_review_by_group"].items():
            html.append('<tr><td>' + group + '</td><td>' + escape(str(counts["identity_signals_not_accuracy"])) + '</td><td>' + escape(str(counts["condition_hints_not_verification"])) + '</td></tr>')
        html.append('</table><p>추가 20회는 검색어·별칭·주소 길이가 함께 달라졌으므로 조건 키워드만의 효과라고 단정하지 않는다. 상위 3개에 근거가 없는 경우에도 4위 이후에는 있을 수 있다.</p>')
    html += ['<h2>50회 전체 기록</h2>']
    for p in manifest["cases"]:
        row = lookup[p["case_id"]]
        review = annotations.get(p["case_id"], {})
        html.append('<details><summary>' + escape(f'{p["case_id"]} · {p["name"]} · {p["group"]} · {row["status"]} · 결과 {row.get("organic_count", 0)}개') + '</summary>')
        html.append('<p><b>검색어:</b> ' + escape(p["query"]) + '<br><b>기준 주소:</b> ' + escape(p["address"]) + '</p>')
        if review:
            html.append('<p><b>제목·요약 검토:</b> ' + escape(review["identity"] + ' / ' + review.get("usefulness", "")) + '<br>' + escape(review.get("note", "")) + '</p>')
        html.append('<ol>')
        for hit in row.get("hits", []):
            html.append('<li><a target="_blank" rel="noopener noreferrer" href="' + escape(hit["url"], quote=True) + '">' + escape(hit["title"]) + '</a><br><small>' + escape(hit["snippet"]) + '</small></li>')
        html.append('</ol></details>')
    html += ['<details><summary>집계 JSON / 한계</summary><pre>' + escape(json.dumps(summary, ensure_ascii=False, indent=2)) + '</pre></details></html>']
    (folder / "report.html").write_text("\n".join(html), encoding="utf-8")
    emit({"summary": summary, "report": str(folder / "report.html")})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--summary", action="store_true")
    parser.add_argument("--folder", type=Path)
    args = parser.parse_args()
    folder = (args.folder or ROOT / "output/evals" / ("serper-50-" + datetime.now(KST).strftime("%Y%m%d-%H%M%S"))).resolve()
    if not folder.is_relative_to(ROOT / "output/evals"):
        parser.error("Output must stay in output/evals")
    if args.prepare:
        prepare(folder)
    else:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        (run if args.run else summarize)(folder, manifest)


if __name__ == "__main__":
    main()
