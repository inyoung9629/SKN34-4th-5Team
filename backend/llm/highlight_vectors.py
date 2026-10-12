"""하이라이트 장면 RAG 색인: 장면표(llm.highlights)의 장면 설명을 임베딩해 Qdrant 에 넣고 의미로 찾는다.

장면표(DB)가 원본이고 이 컬렉션은 검색용 색인이다. 영상마다 기존 점을 지우고 다시 넣으므로 재분석해도 낡은 점이 남지 않는다.
임베딩은 OpenRouter(HIGHLIGHT_EMBEDDING_MODEL, 기본 google/gemini-embedding-2)를 쓴다. 문서 컬렉션(kbo_documents_openai,
text-embedding-3-small)과 벡터 공간이 달라서 별도 컬렉션(HIGHLIGHT_QDRANT_COLLECTION, 기본 kbo_highlight_scenes)에 둔다.
"""
import os
import urllib.error
import uuid

from qdrant_client import models

from llm.highlights import HighlightError, _http_json, env

DEFAULT_MODEL = "google/gemini-embedding-2"
PAYLOAD_INDEXES = {"video_id": models.PayloadSchemaType.KEYWORD, "teams": models.PayloadSchemaType.KEYWORD,
                   "event": models.PayloadSchemaType.KEYWORD, "game_day": models.PayloadSchemaType.INTEGER}


def model_name():
    return os.getenv("HIGHLIGHT_EMBEDDING_MODEL") or DEFAULT_MODEL


def collection_name():
    return os.getenv("HIGHLIGHT_QDRANT_COLLECTION") or "kbo_highlight_scenes"


def client():
    from llm.vector_store import client as qdrant
    return qdrant()


def scene_text(scene):
    """색인할 글: 날짜·대진·이닝·공격팀·타자·투수·결과 머리말 + 장면 설명."""
    video = scene.video
    matchup = " vs ".join(t.team_code for t in (video.away_team, video.home_team) if t)
    when = f"{scene.inning}회{scene.half}" if scene.inning else ""
    who = " ".join(part for part in (
        scene.batting_team.team_code if scene.batting_team else "", scene.batter_name,
        f"투수 {scene.pitcher_name}" if scene.pitcher_name else "") if part)
    rbi = f"{scene.rbi}타점" if scene.rbi else ""
    head = " ".join(part for part in (str(video.game_date or ""), matchup, when, who, scene.event, rbi) if part)
    return f"{head}: {scene.description}" if scene.description else head


def embed(texts, _http=_http_json):
    key = env("OPENROUTER_API_KEY")
    if not key:
        raise HighlightError("OPENROUTER_API_KEY 가 없습니다.")
    try:
        response = _http("https://openrouter.ai/api/v1/embeddings", {"model": model_name(), "input": list(texts)},
                         {"Authorization": f"Bearer {key}"}, timeout=60)
    except urllib.error.HTTPError as exc:
        raise HighlightError(f"임베딩 {exc.code}: {exc.read()[:300].decode(errors='replace')}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise HighlightError(f"임베딩 호출 실패: {type(exc).__name__}: {exc}") from None
    try:
        vectors = [row["embedding"] for row in sorted(response["data"], key=lambda row: row["index"])]
    except (KeyError, TypeError) as exc:
        raise HighlightError(f"임베딩 응답 형식 오류: {str(response)[:300]}") from exc
    if len(vectors) != len(texts):
        raise HighlightError("임베딩 개수가 입력과 다릅니다.")
    return vectors


def ensure_collection(size):
    db, name = client(), collection_name()
    if not db.collection_exists(name):
        db.create_collection(name, vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE))
        for field, schema in PAYLOAD_INDEXES.items():
            db.create_payload_index(name, field_name=field, field_schema=schema)
    elif db.get_collection(name).config.params.vectors.size != size:
        raise HighlightError(f"{name} 컬렉션 차원이 {size} 와 다릅니다. 모델을 바꿨다면 컬렉션 이름도 바꾸세요.")
    return name


def game_day(value):
    return int(value.strftime("%Y%m%d")) if value else 0


def index_video(video, _embed=None):
    """영상 하나의 장면을 모두 다시 색인한다. 넣은 장면 수를 돌려준다."""
    scenes = list(video.scenes.select_related("video__home_team", "video__away_team", "batting_team"))
    if not scenes:
        return 0
    vectors = (_embed or embed)([scene_text(s) for s in scenes])
    name = ensure_collection(len(vectors[0]))
    teams = [t.team_code for t in (video.home_team, video.away_team) if t]
    by_video = models.Filter(must=[models.FieldCondition(key="video_id", match=models.MatchValue(value=video.video_id))])
    client().delete(name, points_selector=models.FilterSelector(filter=by_video), wait=True)
    client().upsert(name, wait=True, points=[models.PointStruct(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"highlight:{model_name()}:{s.pk}")), vector=vector,
        payload={"scene_id": s.pk, "video_id": video.video_id, "game_day": game_day(video.game_date), "teams": teams,
                 "event": s.event, "model": model_name()}) for s, vector in zip(scenes, vectors)])
    return len(scenes)


def search(query, team_codes=(), date_from=None, date_to=None, event=None, k=5, _embed=None):
    """질문과 의미가 가까운 장면 [(scene_id, 점수)] (가까운 순). 색인이 아직 없으면 None."""
    db, name = client(), collection_name()
    if not db.collection_exists(name):
        return None
    vector = (_embed or embed)([query])[0]
    must = [models.FieldCondition(key="teams", match=models.MatchValue(value=code)) for code in team_codes if code]
    if date_from or date_to:
        must.append(models.FieldCondition(key="game_day", range=models.Range(
            gte=game_day(date_from) if date_from else None, lte=game_day(date_to) if date_to else None)))
    if event:
        must.append(models.FieldCondition(key="event", match=models.MatchValue(value=event)))
    points = db.query_points(name, query=vector, query_filter=models.Filter(must=must) if must else None,
                             limit=k, with_payload=True).points
    return [(point.payload["scene_id"], round(float(point.score), 4)) for point in points]
