"""공통: 로컬 도커 DB 연결 · 질문 임베딩(캐시) · 검색 · 키워드 재정렬  (장고 없이)"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

import numpy as np
import psycopg
from dotenv import load_dotenv
from openai import OpenAI
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

HERE = Path(__file__).resolve().parent          # rag_test/
ROOT = HERE.parent                              # 레포 루트
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
GOLDEN_DIR = HERE / "golden"                    # golden_club.jsonl : 구단·야구 도메인 시험지

load_dotenv(ROOT / ".env")                      # 9/11 develop 부터 여기로 통일: DB_* · OPENAI_API_KEY · EMBEDDING_MODEL
load_dotenv(ROOT / "backend" / ".env")          # 예전 위치 (남아 있으면 같이 읽음)
EMBED_MODEL = os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small"  # 적재 때와 반드시 같아야 함
LLM_MODEL = os.getenv("LLM_MODEL") or "gpt-6-luna"                    # 환경변수 미설정 시 기본 모델


def connect():
    env = lambda new, old: os.getenv(new) or os.getenv(old)   # 새 이름(DB_*) 우선, 없으면 예전 이름(POSTGRES_*)
    host = os.getenv("DB_HOST")
    conn = psycopg.connect(
        host=host if host and host != "db" else "localhost",  # PC에서 도커 db 로 붙을 땐 'db' 가 아니라 localhost
        port=os.getenv("DB_PORT") or 5432,
        dbname=env("DB_NAME", "POSTGRES_DB"), user=env("DB_USER", "POSTGRES_USER"),
        password=env("DB_PASSWORD", "POSTGRES_PASSWORD"),
        autocommit=True, row_factory=dict_row,
    )
    register_vector(conn)                          # numpy 배열 ↔ vector 타입 자동 변환
    return conn


_client = None


def openai_client():
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def embed(texts):
    """질문 임베딩. 같은 질문은 results/query_cache.json 에서 꺼내 OpenAI 를 다시 부르지 않는다."""
    cache_path = RESULTS / "query_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    key = lambda t: hashlib.sha1(f"{EMBED_MODEL}|{t}".encode()).hexdigest()
    todo = [t for t in dict.fromkeys(texts) if key(t) not in cache]
    if todo:
        resp = openai_client().embeddings.create(model=EMBED_MODEL, input=todo)
        for t, d in zip(todo, resp.data):
            cache[key(t)] = d.embedding
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return [np.array(cache[key(t)], dtype=np.float32) for t in texts]


def _where(stadium=None, categories=None):
    conds, params = [], {}
    if stadium:
        # 공통 반입규정·기초규칙은 stadium_code 가 null → 어느 구장 질문에도 같이 포함
        conds.append("(metadata->>'stadium_code' = %(st)s OR metadata->>'stadium_code' IS NULL)")
        params["st"] = stadium
    if categories:
        conds.append("metadata->>'category' = ANY(%(cats)s)")
        params["cats"] = list(categories)
    return ("WHERE " + " AND ".join(conds)) if conds else "", params


def count_candidates(conn, stadium=None, categories=None):
    where, params = _where(stadium, categories)
    return conn.execute(f"SELECT count(*) AS n FROM llm_documentchunk {where}", params).fetchone()["n"]


def search(conn, qvec, k=5, stadium=None, categories=None, ef_search=40, exact=False, must_text=None):
    """must_text: 이 문자열이 본문에 든 청크만 (날짜처럼 반드시 맞아야 하는 조건에 사용)"""
    where, params = _where(stadium, categories)
    if must_text:
        where = (where + " AND " if where else "WHERE ") + "content ILIKE %(mt)s"
        params["mt"] = f"%{must_text}%"
    params.update(v=qvec, k=k)
    sql = f"""
        SELECT metadata->>'doc_id' AS doc_id, metadata->>'stadium_code' AS stadium,
               metadata->>'category' AS category, metadata->>'status' AS status,
               metadata->>'evidence_type' AS evidence_type,
               left(coalesce(metadata->'metadata'->>'updated_at',
                             metadata->>'updated_at', ''), 10) AS updated_at, content,
               embedding <=> %(v)s AS dist
        FROM llm_documentchunk {where}
        ORDER BY embedding <=> %(v)s
        LIMIT %(k)s"""
    t0 = time.perf_counter()
    with conn.transaction():                       # SET LOCAL 은 이 트랜잭션 안에서만 유지
        conn.execute(f"SET LOCAL hnsw.ef_search = {int(ef_search)}")
        if exact:
            conn.execute("SET LOCAL enable_indexscan = off")   # HNSW 끄고 전수 비교
        rows = conn.execute(sql, params).fetchall()
    return rows, (time.perf_counter() - t0) * 1000


# ── 하이브리드: 벡터 후보를 키워드 일치로 다시 줄 세우기 ───────────────────────
JOSA = re.compile(r"(은|는|이|가|을|를|에|에서|야|이야|로|으로|랑|이랑|도|만|의)$")
STOP = {"알려줘", "어디", "어디야", "얼마", "얼마야", "있어", "근처", "추천", "추천해줘", "야구장", "구장"}


def keywords(question):
    q = re.sub(r"(?<!\d)(\d{1,2})\s*(?:월\s*|[/.\-])\s*(\d{1,2})(?:\s*일)?(?!\d)", lambda m: f"-{int(m[1]):02d}-{int(m[2]):02d}", question)   # 9월 12일 · 9/12 · 9.12
    out = []
    for t in re.findall(r"[가-힣A-Za-z0-9\-]{2,}", q):
        for form in (t, JOSA.sub("", t)):
            if len(form) >= 2 and form not in STOP and form not in out:
                out.append(form)
    return out


DATE = re.compile(r"-\d{2}-\d{2}")        # keywords() 가 "9월 12일" 을 "-09-12" 로 바꿔 둔 형태


def date_tokens(question):
    """질문 속 날짜를 청크 표기(-09-12)로 뽑아준다"""
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


def add_golden_arg(ap):
    ap.add_argument("--golden", default="golden_club.jsonl", help="golden 폴더 안의 시험지 파일 이름")


def load_golden(name, limit=0):
    rows = [json.loads(l) for l in (GOLDEN_DIR / name).read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[:limit] if limit else rows


def results_dir(name):
    """golden_club.jsonl → results/club/"""
    d = RESULTS / Path(name).stem.replace("golden_", "")
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_dynamic(conn, golden):
    """골든셋의 dynamic 항목을 DB 현재 값으로 채운다 (순위처럼 매일 바뀌는 정답)."""
    import structured
    table = None
    for q in golden:
        d = q.get("dynamic")
        if not d or d.get("kind") != "standing":
            continue
        table = table if table is not None else {t["team"]: t for t in structured.standings(conn)}
        row = table.get(d["team"])
        if not row:
            continue
        q["must"] = {"rank": [f"{row['rank']}위"], "rate": [row["rate"]],
                     "win": [f"{row['win']}승"], "lose": [f"{row['lose']}패"]}[d["field"]]
        q["resolved_from"] = row["as_of"]
    return golden
