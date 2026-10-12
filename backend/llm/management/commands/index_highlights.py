"""분석된 하이라이트 장면 설명을 임베딩해 Qdrant(HIGHLIGHT_QDRANT_COLLECTION)에 넣는다 (하이라이트 RAG 색인).

  # 어떤 글이 색인될지만 본다 (비용 없음)
  docker compose exec backend python manage.py index_highlights --dry-run
  # 실제 색인 (OpenRouter google/gemini-embedding-2, 장면 1,000개에 1센트 안팎)
  docker compose exec backend python manage.py index_highlights

collect_highlights 가 새로 분석한 영상은 자동으로 색인한다(HIGHLIGHT_RAG_INDEX=0 이면 끔).
이 명령은 기존에 분석해 둔 영상을 처음 색인하거나 다시 색인할 때 쓴다. 영상마다 지우고 다시 넣으므로 여러 번 돌려도 된다.
"""
from django.core.management.base import BaseCommand, CommandError

from llm import highlight_vectors
from llm.highlights import HighlightError


class Command(BaseCommand):
    help = "하이라이트 장면 설명을 임베딩해 RAG 색인(Qdrant)을 만든다."

    def add_arguments(self, parser):
        parser.add_argument("--video-ids", default="", help="쉼표로 구분한 영상 ID만")
        parser.add_argument("--dry-run", action="store_true", help="색인할 글만 보고 임베딩하지 않음")

    def handle(self, *args, **o):
        from llm.models import HighlightScene, HighlightVideo
        videos = HighlightVideo.objects.filter(status=HighlightVideo.ANALYZED).order_by("game_date")
        ids = [v.strip() for v in o["video_ids"].split(",") if v.strip()]
        if ids:
            videos = videos.filter(video_id__in=ids)
        if o["dry_run"]:
            scenes = HighlightScene.objects.filter(video__in=videos).select_related(
                "video__home_team", "video__away_team", "batting_team")
            for scene in scenes[:5]:
                self.stdout.write(highlight_vectors.scene_text(scene))
            self.stdout.write(f"영상 {videos.count()}개 · 장면 {scenes.count()}개 | 모델 {highlight_vectors.model_name()} "
                              f"→ {highlight_vectors.collection_name()}")
            return
        total = 0
        for video in videos:
            try:
                total += highlight_vectors.index_video(video)
            except HighlightError as exc:
                raise CommandError(f"{video.video_id}: {exc}") from None
        self.stdout.write(self.style.SUCCESS(f"영상 {videos.count()}개 · 장면 {total}개 색인 완료"))
