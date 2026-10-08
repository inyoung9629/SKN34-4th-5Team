import calendar as month_calendar
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.test import override_settings

KST = ZoneInfo("Asia/Seoul")

def _setup():
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()

def _vector_upsert(items):
    from langchain_openai import OpenAIEmbeddings
    from llm import vector_store
    items = list({item["doc_id"]: item for item in items}.values())
    if not items:
        return {"new": 0, "updated": 0, "skipped": 0}
    collection = vector_store.ensure_collection(create=True)
    db = vector_store.client()
    new = updated = skipped = 0
    for start in range(0, len(items), 256):
        batch = items[start:start + 256]
        existing = {str(point.id): point for point in db.retrieve(
            collection, ids=[vector_store.point_id(item["doc_id"]) for item in batch],
            with_payload=True, with_vectors=True)}
        pending = []
        for item in batch:
            digest = hashlib.sha256(item["content"].encode()).hexdigest()
            old = existing.get(vector_store.point_id(item["doc_id"]))
            old_metadata = old.payload.get("metadata", {}) if old else {}
            # 기존 청크의 추적 필드(source_file 등)는 보존하고 크롤러 값만 덮어쓴다.
            metadata = {**old_metadata, **item.get("metadata", {}), "doc_id": item["doc_id"], "content_hash": digest}
            if old and old.payload.get("content") == item["content"]:
                if old_metadata != metadata:
                    vector_store.upsert_documents([{"id": item["doc_id"], "content": item["content"],
                                                    "metadata": metadata, "embedding": old.vector}])
                skipped += 1
            else:
                pending.append((item, metadata, old))
        if pending:
            vectors = OpenAIEmbeddings(model=vector_store.embedding_model(), dimensions=1536).embed_documents(
                [item["content"] for item, _, _ in pending])
            if len(vectors) != len(pending):
                raise ValueError("Crawler embedding count mismatch")
            vector_store.upsert_documents([{"id": item["doc_id"], "content": item["content"],
                                            "metadata": metadata, "embedding": vector}
                                           for (item, metadata, _), vector in zip(pending, vectors)])
            updated += sum(old is not None for _, _, old in pending)
            new += sum(old is None for _, _, old in pending)
    return {"new": new, "updated": updated, "skipped": skipped}

def collect_schedule(month=None):
    _setup()
    from django.utils import timezone
    from tving.parsers import parse_calendar, parse_schedule
    from tving.relational import persist_month
    from tving.service import _provider_json
    month = month or datetime.now(KST).strftime("%Y-%m")
    calendar_payload = _provider_json("/kbo/schedule/day", {"date": month.replace("-", "")})
    game_days = parse_calendar(calendar_payload, month)
    games, games_by_day = [], {}
    for day in game_days:
        date = f"{month}-{day:02d}"
        parsed = parse_schedule(_provider_json("/kbo/schedule", {"date": date.replace("-", "")}), date, calendar_payload)
        games.extend(parsed)
        games_by_day[day] = parsed
    # 조회기는 월 전체가 수집되었는지 확인하므로, 원천 달력의 휴식일도 기록한다.
    # 모든 응답의 검증이 끝난 뒤 저장하여 수집 실패를 경기 없음으로 바꾸지 않는다.
    days = []
    for day in range(1, month_calendar.monthrange(int(month[:4]), int(month[5:]))[1] + 1):
        day_games = games_by_day.get(day, [])
        days.append({"date": f"{month}-{day:02d}", "status": "ready" if day_games else "empty", "gameCount": len(day_games)})
    data = {"year": int(month[:4]), "month": month, "today": datetime.now(KST).date().isoformat(), "games": games, "days": days, "loading": False}
    with override_settings(EXTERNAL_DATA_SYNC_INTERVAL_SECONDS=0): persist_month(data, timezone.now())
    return len(games)

def collect_standing():
    _setup()
    from django.utils import timezone
    from tving.parsers import parse_rankings, parse_schedule, parse_standings
    from tving.service import _provider_json
    from tving.relational import persist_daily
    year, now = str(datetime.now(KST).year), timezone.now()
    day = now.astimezone(KST).date().isoformat()
    rows = parse_standings(_provider_json("/kbo/history/team", {"yearSeason": year, "gameSeason": "0"}), day)
    rankings = {
        "pitchers": parse_rankings(_provider_json("/kbo/history/athlete/ranking", {"yearSeason": year, "gameSeason": "regular", "athleteType": "pitcher", "pitcherRankOrder": "earnedRunAverage", "screenCode": "CSSD0100", "osCode": "CSOD0900"}), "pitcher"),
        "hitters": parse_rankings(_provider_json("/kbo/history/athlete/ranking", {"yearSeason": year, "gameSeason": "regular", "athleteType": "hitter", "hitterRankOrder": "battingAverage", "screenCode": "CSSD0100", "osCode": "CSOD0900"}), "hitter"),
    }
    games = parse_schedule(_provider_json("/kbo/schedule", {"date": day.replace("-", "")}), day)
    persist_daily({"date": day, "games": games, "standings": rows, "individualRankings": rankings}, now)
    return len(rows)
