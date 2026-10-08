"""[club] 질문 임베딩 · Qdrant 검색 · 키워드 재정렬 (rag_test/common.py 의 Django 판)

Qdrant 검색 결과를 기존 RAG 행 계약으로 변환한다.
임베딩 모델은 적재(build_index) 때와 반드시 같아야 한다 → EMBEDDING_MODEL 환경변수.
"""
import os
import re
import time

from langchain_openai import OpenAIEmbeddings

EMBED_MODEL = os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small"
EF_SEARCH = 200            # rag_test STEP4 결과: 40 은 후보 유실, 200 은 유실 0

_embedder = None


def embed(text: str) -> list[float]:
    """질문 1건 → 벡터. 임베딩 클라이언트는 서버 기동 후 한 번만 만든다."""
    global _embedder
    from llm.vector_store import embedding_model
    embedding_model()
    if _embedder is None:
        _embedder = OpenAIEmbeddings(model=EMBED_MODEL, dimensions=1536)
    return _embedder.embed_query(text)


def embed_many(texts: list[str]) -> list[list[float]]:
    """쿼리 여러 개를 API 호출 1회로 임베딩. embed() 와 같은 클라이언트·같은 모델을 쓴다.

    course 가 "경기 전용 / 경기 후용" 쿼리 두 개를 한 번에 벡터로 만들 때 쓴다
    (embed() 를 두 번 부르면 네트워크 왕복이 두 번이라 그만큼 느려진다).
    """
    global _embedder
    from llm.vector_store import embedding_model
    embedding_model()
    if _embedder is None:
        _embedder = OpenAIEmbeddings(model=EMBED_MODEL, dimensions=1536)
    return _embedder.embed_documents(texts)


def search(qvec, k=5, stadium=None, categories=None, ef_search=EF_SEARCH, must_text=None):
    """벡터 검색. 반환 (rows, ms). rows 는 dict 목록 (doc_id·stadium·category·status·evidence_type·updated_at·content·dist)"""
    from llm import vector_store
    t0 = time.perf_counter()
    rows = [vector_store.legacy_row(doc) for doc in vector_store.search(
        qvec, k=k, stadium=stadium, categories=categories, must_text=must_text, ef_search=ef_search)]
    return rows, (time.perf_counter() - t0) * 1000


# ── 하이브리드: 벡터 후보를 키워드 일치로 다시 줄 세우기 ───────────────────────
JOSA = re.compile(r"(은|는|이|가|을|를|에|에서|야|이야|로|으로|랑|이랑|도|만|의)$")
STOP = {"알려줘", "어디", "어디야", "얼마", "얼마야", "있어", "근처", "추천", "추천해줘", "야구장", "구장"}
DATE = re.compile(r"-\d{2}-\d{2}")        # keywords() 가 "9월 12일" 을 "-09-12" 로 바꿔 둔 형태


def keywords(question):
    q = re.sub(r"(?<!\d)(\d{1,2})\s*(?:월\s*|[/.\-])\s*(\d{1,2})(?:\s*일)?(?!\d)",
               lambda m: f"-{int(m[1]):02d}-{int(m[2]):02d}", question)   # 9월 12일 · 9/12 · 9.12
    out = []
    for t in re.findall(r"[가-힣A-Za-z0-9\-]{2,}", q):
        for form in (t, JOSA.sub("", t)):
            if len(form) >= 2 and form not in STOP and form not in out:
                out.append(form)
    return out


def date_tokens(question):
    """질문 속 날짜를 청크 표기(-09-12)로"""
    return [t for t in keywords(question) if DATE.fullmatch(t)]


def keyword_rerank(question, rows, k=5, alpha=0.3, date_bonus=1.0):
    """벡터 점수 + 키워드 일치. 날짜가 정확히 같은 청크는 크게 가산 (일정 질문 정확도)"""
    toks = keywords(question)
    if not toks:
        return rows[:k]
    dates = [t for t in toks if DATE.fullmatch(t)]

    def score(r):
        body = r["content"].upper()
        s = (1 - r["dist"]) + alpha * sum(t.upper() in body for t in toks) / len(toks)
        if dates and any(d in body for d in dates):
            s += date_bonus
        return s

    return sorted(rows, key=score, reverse=True)[:k]
