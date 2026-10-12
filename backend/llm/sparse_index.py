"""한국어 키워드(BM25) 검색용 Qdrant sparse 컬렉션 — 문서 벡터 검색과 RRF 로 합쳐 하이브리드 검색을 만든다 (#5).

문서 컬렉션(llm.vector_store, text-embedding-3-small)은 그대로 두고, 같은 point id·payload 로 sparse 컬렉션을 따로 만든다.
- 토큰: NFKC·소문자 → 한글 낱말(끝 조사 제거)과 글자 2-gram, 영문·숫자 낱말("5.09", "abs"). 형태소 분석기 없이 조사·띄어쓰기 차이를 흡수한다.
- 문서 값: BM25 tf 포화식(k1=1.2, b=0.75, 평균 길이는 컬렉션 metadata), IDF 는 Qdrant(Modifier.IDF)가 계산한다.
- 컬렉션이 없으면 None 을 돌려주고 호출부는 기존 벡터 검색만 쓴다. 만들기·다시 만들기: manage.py build_sparse_index
"""
import os
import re
import unicodedata
import zlib
from collections import Counter

from qdrant_client import models

VECTOR = "text"
K1, B = 1.2, 0.75
DEFAULT_AVGDL = 200.0
RRF_K = int(os.getenv("RAG_RRF_K", "60"))
SPARSE_WEIGHT = float(os.getenv("RAG_SPARSE_WEIGHT", "0.1"))
SPARSE_RRF_WEIGHT = float(os.getenv("RAG_SPARSE_RRF_WEIGHT", "1.0"))  # 가중 RRF: 키워드 순위 목록의 가중치(벡터는 1)  # hybrid_score: 키워드 점수(최고점 대비 0~1)에 곱하는 가중치  # RRF 상수: 작을수록 상위 순위에 무게 (eval_retrieval --rrf-k 로 비교)
JOSA = re.compile(r"(에서|에게|까지|부터|으로|이랑|이나|이야|은|는|이|가|을|를|에|의|와|과|로|도|만|랑|나|야)$")
STOP = {"알려줘", "알려", "어디", "어디야", "얼마", "얼마야", "있어", "뭐야", "뭐", "어떻게", "언제", "왜", "설명해줘", "궁금해", "되나요", "돼"}
WORD = re.compile(r"[가-힣]+|[a-z0-9]+(?:\.[0-9]+)*")


def collection_name():
    from llm import vector_store
    name = os.getenv("QDRANT_SPARSE_COLLECTION", "kbo_documents_sparse")
    if name == vector_store.collection_name():
        raise ValueError("Sparse and dense Qdrant collections must have different names")
    return name


def tokens(text):
    """검색어·문서 공통 토큰. 'w:' 낱말, 'b:' 한글 2-gram."""
    out = []
    for word in WORD.findall(unicodedata.normalize("NFKC", text or "").lower()):
        if word in STOP:
            continue
        if word[0] < "가":  # 영문·숫자
            if len(word) >= 2 or word.isdigit():
                out.append(f"w:{word}")
            continue
        stem = JOSA.sub("", word) if len(word) > 2 else word
        if len(stem) < 2 or stem in STOP:
            continue
        out.append(f"w:{stem}")
        out.extend(f"b:{stem[i:i + 2]}" for i in range(len(stem) - 1) if len(stem) > 2)
    return out


def _index(token):
    return zlib.crc32(token.encode("utf-8")) & 0x7FFFFFFF


def _sparse(weights):
    merged = Counter()
    for token, weight in weights.items():
        merged[_index(token)] += weight  # 해시 충돌은 합친다
    indices = sorted(merged)
    return models.SparseVector(indices=indices, values=[float(merged[i]) for i in indices])


def document_vector(text, avgdl=DEFAULT_AVGDL):
    counts = Counter(tokens(text))
    if not counts:
        return None
    length = sum(counts.values())
    norm = K1 * (1 - B + B * length / max(avgdl, 1.0))
    return _sparse({t: tf * (K1 + 1) / (tf + norm) for t, tf in counts.items()})


def query_vector(text):
    unique = dict.fromkeys(tokens(text), 1.0)
    return _sparse(unique) if unique else None


def client():
    from llm.vector_store import client as dense_client
    return dense_client()


def exists():
    try:
        return client().collection_exists(collection_name())
    except Exception:  # noqa: BLE001 - Qdrant 연결 실패면 벡터 검색만 쓴다
        return False


def avgdl():
    try:
        return float((client().get_collection(collection_name()).config.metadata or {}).get("avgdl") or DEFAULT_AVGDL)
    except Exception:  # noqa: BLE001
        return DEFAULT_AVGDL


def recreate(average_length):
    """sparse 컬렉션을 비우고 새로 만든다(문서 컬렉션에서 다시 만들 수 있는 파생 색인)."""
    db, name = client(), collection_name()
    if db.collection_exists(name):
        db.delete_collection(name)
    db.create_collection(name, vectors_config={},
                         sparse_vectors_config={VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF)},
                         metadata={"avgdl": round(float(average_length), 2), "tokenizer": "ko-word-bigram-v1"})
    for key in ("metadata.stadium_code", "metadata.category", "metadata.in_stadium_flag"):
        db.create_payload_index(name, field_name=key, field_schema=models.PayloadSchemaType.KEYWORD)
    return name


def upsert_payloads(docs, average_length=None):
    """docs: 문서 컬렉션 payload(content·metadata·external_id) + point_id. 같은 point id 로 넣는다."""
    name = collection_name()
    average_length = average_length or avgdl()
    points, deletes = [], []
    # 같은 ID가 여러 번 오면 dense upsert와 같이 마지막 내용을 남긴다.
    for doc in {doc["point_id"]: doc for doc in docs}.values():
        vector = document_vector(doc.get("content") or "", average_length)
        if vector is None:
            deletes.append(doc["point_id"])
            continue
        payload = {k: doc[k] for k in ("content", "metadata", "external_id") if k in doc}
        points.append(models.PointStruct(id=doc["point_id"], vector={VECTOR: vector}, payload=payload))
    if deletes:
        client().delete(name, points_selector=models.PointIdsList(points=deletes), wait=True)
    if points:
        client().upsert(name, points=points, wait=True)
    return len(points)


def upsert_documents(documents):
    """vector_store.upsert_documents 와 같은 입력({id, content, metadata}). sparse 컬렉션이 있을 때만 갱신한다."""
    if not documents or not exists():
        return 0
    from llm.vector_store import point_id
    return upsert_payloads([{"point_id": point_id(d.get("id")), "content": d.get("content") or "", "metadata": d["metadata"],
                             "external_id": str(d["id"])} for d in documents])


def build_from_documents(batch=256, report=print):
    """문서 컬렉션 전체를 읽어 sparse 컬렉션을 다시 만든다. 반환 (문서 수, 평균 길이)."""
    from llm.vector_store import iter_documents
    docs = list(iter_documents())
    lengths = [len(tokens(d.get("content") or "")) for d in docs]
    average_length = (sum(lengths) / len(lengths)) if lengths else DEFAULT_AVGDL
    recreate(average_length)
    done = 0
    for start in range(0, len(docs), batch):
        done += upsert_payloads(docs[start:start + batch], average_length)
        report(f"sparse 색인 {done}/{len(docs)}")
    return done, average_length


def search(query, k=50, stadium=None, categories=None, flag=None, include_common=True):
    """키워드(BM25) 검색. sparse 컬렉션이 없거나 검색어 토큰이 없으면 None."""
    vector = query_vector(query)
    if vector is None or not exists():
        return None
    from llm.vector_store import metadata_filter
    points = client().query_points(collection_name(), query=vector, using=VECTOR, limit=k, with_payload=True,
                                   query_filter=metadata_filter(stadium, categories, flag, include_common)).points
    return [{**point.payload, "point_id": str(point.id), "dist": None, "sparse_score": float(point.score)} for point in points]


def rrf(*ranked_lists, key="doc_id", k=None, weights=None):
    """(가중) Reciprocal Rank Fusion: 여러 순위 목록을 weight/(k+순위) 합으로 합친다. 앞 목록의 행을 대표로 남긴다.
    key 는 행의 키 이름이나 행 → 식별자 함수. weights 가 없으면 모두 1."""
    k = RRF_K if k is None else k
    weights = weights or [1.0] * len(ranked_lists)
    ident_of = key if callable(key) else (lambda row: row.get(key))
    scores, rows = Counter(), {}
    for weight, ranked in zip(weights, ranked_lists):
        for rank, row in enumerate(ranked, 1):
            ident = ident_of(row)
            scores[ident] += weight / (k + rank)
            rows.setdefault(ident, row)
    return [{**rows[ident], "rrf": score} for ident, score in scores.most_common()]


def dense_distances(vector, point_ids):
    """키워드 검색으로만 들어온 후보의 벡터 거리(1-cosine)를 문서 컬렉션에서 구한다. {point_id: dist}"""
    if not point_ids:
        return {}
    from llm import vector_store
    points = client().query_points(vector_store.ensure_collection(), query=list(vector), limit=len(point_ids),
                                   query_filter=models.Filter(must=[models.HasIdCondition(has_id=list(point_ids))])).points
    return {str(p.id): 1.0 - float(p.score) for p in points}
