"""Model-bound Qdrant document storage; PostgreSQL remains migration input."""
import math
import os
import uuid
from functools import lru_cache

from qdrant_client import QdrantClient, models

DOCUMENT_MODEL = "text-embedding-3-small"
DOCUMENT_DIMENSION = 1536


def collection_name():
    return os.getenv("QDRANT_DOCUMENT_COLLECTION", "kbo_documents_openai")


def dimension():
    return DOCUMENT_DIMENSION


def embedding_model():
    if os.getenv("EMBEDDING_MODEL", DOCUMENT_MODEL) != DOCUMENT_MODEL:
        raise ValueError("Document embeddings must remain text-embedding-3-small")
    return DOCUMENT_MODEL


def validate_vector(vector, size):
    if len(vector) != size or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
        raise ValueError(f"Expected {size} finite numeric vector values")
    if not any(vector):
        raise ValueError("Cosine vector must not be zero")
    return list(vector)


@lru_cache(maxsize=1)
def client():
    return QdrantClient(url=os.getenv("QDRANT_URL", "http://qdrant:6333"),
                        api_key=os.getenv("QDRANT_API_KEY") or None, timeout=30)


def ensure_collection(create=False):
    name, size, model = collection_name(), dimension(), embedding_model()
    db = client()
    if not db.collection_exists(name):
        if not create:
            raise RuntimeError(f"Missing Qdrant collection {name}; migrate/build before retrieval")
        db.create_collection(name, vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE),
                             metadata={"embedding_model": model, "dimension": size})
    info = db.get_collection(name)
    config = info.config.params.vectors
    if not isinstance(config, models.VectorParams) or config.size != size or config.distance != models.Distance.COSINE:
        raise ValueError(f"Incompatible vector configuration: {name}")
    if (info.config.metadata or {}).get("embedding_model") != model:
        raise ValueError(f"Incompatible or unbound embedding model: {name}")
    return name


def point_id(identifier):
    if (isinstance(identifier, bool) or not isinstance(identifier, (str, int, float))
            or (isinstance(identifier, float) and not math.isfinite(identifier))
            or not str(identifier).strip()):
        raise ValueError("Stable document id must be a nonblank string or finite number")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"kbo:{embedding_model()}:{identifier}"))


def upsert_documents(documents):
    """One bounded batch of {id, content, metadata, embedding}; never delete data."""
    if len(documents) > 500:
        raise ValueError("Upsert batches must not exceed 500")
    points = []
    for doc in documents:
        points.append(models.PointStruct(id=point_id(doc.get("id")),
            vector=validate_vector(doc["embedding"], dimension()),
            payload={"content": doc.get("content") or "", "metadata": doc["metadata"],
                     "external_id": str(doc["id"]), "embedding_model": embedding_model()}))
    if points:
        client().upsert(ensure_collection(create=True), points=points, wait=True)
    return len(points)


def metadata_filter(stadium=None, categories=None, flag=None, include_common=True, **equals):
    must = []
    if stadium:
        condition = models.FieldCondition(key="metadata.stadium_code", match=models.MatchValue(value=stadium))
        must.append(models.Filter(should=[condition, models.IsEmptyCondition(is_empty=models.PayloadField(key="metadata.stadium_code"))])
                    if include_common else condition)
    if categories:
        must.append(models.FieldCondition(key="metadata.category", match=models.MatchAny(any=list(categories))))
    if flag:
        equals["in_stadium_flag"] = flag
    for key, value in equals.items():
        must.append(models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value)))
    return models.Filter(must=must) if must else None


def iter_documents(stadium=None, categories=None, flag=None, include_common=True, page_size=256, **equals):
    if not 1 <= page_size <= 500:
        raise ValueError("Page size must be 1..500")
    name = ensure_collection()
    offset = None
    while True:
        points, offset = client().scroll(name, scroll_filter=metadata_filter(stadium, categories, flag, include_common, **equals),
                                        offset=offset, limit=page_size, with_payload=True, with_vectors=False)
        for point in points:
            yield {**point.payload, "point_id": str(point.id)}
        if offset is None:
            break


def search(vector, k=5, stadium=None, categories=None, flag=None, include_common=True, must_text=None, ef_search=200):
    if not 1 <= k <= 500:
        raise ValueError("Search limit must be 1..500")
    name = ensure_collection()
    vector = validate_vector(vector, dimension())
    query_filter = metadata_filter(stadium, categories, flag, include_common)
    def query(query_filter):
        return client().query_points(name, query=vector, query_filter=query_filter, limit=k,
                                    with_payload=True, search_params=models.SearchParams(hnsw_ef=int(ef_search))).points

    if must_text:
        # ponytail: exact ILIKE-compatible substring scan; add a text index when corpus size warrants it.
        points, ids = [], []
        for doc in iter_documents(stadium, categories, flag, include_common):
            if must_text.casefold() in doc["content"].casefold():
                ids.append(doc["point_id"])
            if len(ids) == 256:
                points = sorted(points + query(models.Filter(must=[models.HasIdCondition(has_id=ids)])),
                                key=lambda point: point.score, reverse=True)[:k]
                ids = []
        if ids:
            points = sorted(points + query(models.Filter(must=[models.HasIdCondition(has_id=ids)])),
                            key=lambda point: point.score, reverse=True)[:k]
    else:
        points = query(query_filter)
    return [{**point.payload, "point_id": str(point.id), "dist": 1.0 - float(point.score)} for point in points]


def legacy_row(doc):
    m = doc["metadata"]
    nested = m.get("metadata")
    updated_at = nested.get("updated_at") if isinstance(nested, dict) else None
    return {**m, "doc_id": m.get("doc_id", doc.get("external_id")), "stadium": m.get("stadium_code"),
            "category": m.get("category"), "status": m.get("status"), "evidence_type": m.get("evidence_type"),
            "updated_at": str(updated_at or m.get("updated_at") or "")[:10],
            "content": doc["content"], "dist": doc.get("dist"), "metadata": m}
