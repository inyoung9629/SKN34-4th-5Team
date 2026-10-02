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
    from django.db import transaction
    from langchain_openai import OpenAIEmbeddings
    from llm.models import Document, DocumentChunk
    pending, skipped = [], 0
    for item in items:
        digest = hashlib.sha256(item["content"].encode()).hexdigest()
        old = DocumentChunk.objects.filter(metadata__doc_id=item["doc_id"]).first()
        if old is None and item.get("metadata", {}).get("category") == "TICKET_POLICY":
            old = DocumentChunk.objects.filter(content=item["content"]).first()
        if old and old.metadata.get("content_hash") == digest:
            # 기존 청크의 추적 필드(source_file 등)는 보존하고 크롤러 값만 덮어쓴다.
            metadata = {**old.metadata, **item.get("metadata", {}), "doc_id": item["doc_id"], "content_hash": digest}
            if old.metadata != metadata:
                old.metadata = metadata
                old.save(update_fields=("metadata",))
            skipped += 1
        else:
            pending.append((item, digest, old))
    if not pending:
        return {"new": 0, "updated": 0, "skipped": skipped}
    vectors = OpenAIEmbeddings(model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")).embed_documents([x[0]["content"] for x in pending])
    with transaction.atomic():
        document, _ = Document.objects.get_or_create(source="tving-crawler", defaults={"title": "TVING 크롤러"})
        new = updated = 0
        for (item, digest, old), vector in zip(pending, vectors):
            metadata = {**(old.metadata if old else {}), **item.get("metadata", {}), "doc_id": item["doc_id"], "content_hash": digest}
            if old:
                old.content, old.metadata, old.embedding = item["content"], metadata, vector
                old.save(update_fields=("content", "metadata", "embedding")); updated += 1
            else:
                DocumentChunk.objects.create(document=document, content=item["content"], chunk_index=0, metadata=metadata, embedding=vector); new += 1
    return {"new": new, "updated": updated, "skipped": skipped}

def collect_schedule(month=None):
    _setup()
    from django.utils import timezone
    from tving.parsers import parse_calendar, parse_schedule
    from tving.relational import persist_month
    from tving.service import _provider_json
    month = month or datetime.now(KST).strftime("%Y-%m")
    calendar = parse_calendar(_provider_json("/kbo/schedule/day", {"date": month.replace("-", "")}), month)
    games, days = [], []
    for day in calendar:
        date = f"{month}-{day:02d}"
        parsed = parse_schedule(_provider_json("/kbo/schedule", {"date": date.replace("-", "")}), date)
        games.extend(parsed); days.append({"date": date, "status": "ready" if parsed else "empty", "gameCount": len(parsed)})
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
