"""모델 리랭커: OpenRouter rerank API(POST /api/v1/rerank)로 후보 문서를 질문과의 관련도 순으로 다시 줄 세운다.

하이브리드 검색은 후보를 넓게 모으는 데(벡터가 놓친 문서를 키워드로 회수) 쓰고, 최종 순서는 질문과 문서를 함께 읽는
cross-encoder 리랭커가 정한다. OPENROUTER_API_KEY 하나로 쓰고, 실패하거나 키가 없으면 None 을 돌려 호출부가 원래 순서를 쓴다.
  RAG_RERANK_MODEL      기본 voyageai/rerank-2.5 (10/12 골든셋: 규정 Hit@1 0.903 > cohere 0.871, 구장은 cohere 0.870 > 0.826,
                        합계 정확도 같고 비용 절반). 예: cohere/rerank-v3.5, qwen/qwen3-reranker-8b
  RAG_RERANK_CANDIDATES 리랭커에 넘길 후보 수(기본 30)
"""
import json
import logging
import math
import os
import urllib.request

URL = "https://openrouter.ai/api/v1/rerank"
DEFAULT_MODEL = "voyageai/rerank-2.5"
MAX_CHARS = 2000  # 규정 청크 최대 1,400~2,100자
COSTS = []  # (model, cost) — eval_retrieval 이 비용을 합산할 때 읽는다

log = logging.getLogger(__name__)


def model_name():
    return os.getenv("RAG_RERANK_MODEL") or DEFAULT_MODEL


def candidates():
    return max(5, min(int(os.getenv("RAG_RERANK_CANDIDATES", "30")), 100))


def _post(body, key, timeout=20):
    request = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}", "X-Title": "kbo-rag-rerank"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _key():
    """.env 에 'KEY =값'처럼 공백이 섞여 있어도 찾는다."""
    for name, value in os.environ.items():
        if name.strip() == "OPENROUTER_API_KEY":
            return value.strip().strip("'\"")
    return ""


def rerank(query, rows, top_n, model=None, _http=_post):
    """rows(content 포함)를 관련도 순으로 top_n 개. 실패하면 None."""
    key = _key()
    if not key or not rows:
        return None
    model = model or model_name()
    body = {"model": model, "query": query, "top_n": min(top_n, len(rows)),
            "documents": [(row.get("content") or "")[:MAX_CHARS] or " " for row in rows]}
    total_tokens = None
    try:
        data = _http(body, key)
        if not isinstance(data, dict):
            return None
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        total_tokens = usage.get("total_tokens")
        if isinstance(total_tokens, bool) or not isinstance(total_tokens, int) or total_tokens < 0:
            total_tokens = None
        try:
            cost = float(usage.get("cost") or 0)
            if isinstance(usage.get("cost"), bool) or not math.isfinite(cost) or cost < 0:
                cost = 0.0
        except (ValueError, TypeError, OverflowError):
            cost = 0.0
        COSTS.append((model, cost))
        results = data.get("results")
        if not isinstance(results, list):
            return None
        ranked = []
        for item in results:
            if not isinstance(item, dict):
                return None
            index, score = item.get("index"), item.get("relevance_score")
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(rows):
                return None
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                return None
            ranked.append({**rows[index], "rerank_score": float(score)})
        return ranked[:body["top_n"]] or None
    except Exception as exc:  # noqa: BLE001 - 외부 응답/전송 실패는 기존 검색 순서를 쓴다
        log.warning("rerank failed (%s): %s", model, exc)
        return None
    finally:
        try:  # 시도한 외부 호출은 결과 파싱 성공 여부와 관계없이 정확히 한 번 센다
            from llm.service.usage import record_external
            record_external(total_tokens, 0)
        except Exception:  # noqa: BLE001
            pass
