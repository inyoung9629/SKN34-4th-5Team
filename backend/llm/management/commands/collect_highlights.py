"""KBO 공식 채널 경기 하이라이트를 받아 장면표(HighlightScene)를 만든다.

  # 먼저 어떤 영상이 분석될지만 본다 (비용 없음)
  docker compose exec backend python manage.py collect_highlights --team KIA --limit 2 --dry-run
  # 실제 분석 (OpenRouter → Gemini, 영상당 약 $0.07~0.20)
  docker compose exec backend python manage.py collect_highlights --team KIA --limit 2 --budget 0.5
  # 날짜 이후 10개 구단 전체
  docker compose exec backend python manage.py collect_highlights --since 2026-10-01 --limit 30 --budget 3

  # 저장된 장면의 빈 타자·투수 칸 다시 채우기 (재분석·비용 없음, 먼저 --dry-run 으로 숫자 확인)
  docker compose exec backend python manage.py collect_highlights --refill-players --dry-run
  docker compose exec backend python manage.py collect_highlights --refill-players

이미 분석한 영상은 건너뛴다(--reanalyze 로 다시). 누적 비용이 --budget(USD)에 닿으면 멈춘다.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from llm import highlights


class Command(BaseCommand):
    help = "KBO 하이라이트 영상을 Gemini(OpenRouter)로 분석해 장면표를 저장한다."

    def add_arguments(self, parser):
        parser.add_argument("--since", type=date.fromisoformat, help="이 날짜(YYYY-MM-DD) 이후 업로드만")
        parser.add_argument("--team", choices=[code for _, code in highlights.TITLE_TEAM], help="이 팀 경기만")
        parser.add_argument("--limit", type=int, default=5, help="최대 영상 수 (기본 5)")
        parser.add_argument("--budget", type=float, default=1.0, help="이번 실행 비용 상한 USD (기본 1.0)")
        parser.add_argument("--model", default=highlights.DEFAULT_MODEL)
        parser.add_argument("--video-ids", default="", help="쉼표로 구분한 영상 ID 직접 지정")
        parser.add_argument("--reanalyze", action="store_true", help="이미 분석한 영상도 다시")
        parser.add_argument("--dry-run", action="store_true", help="목록만 보고 분석·저장하지 않음")
        parser.add_argument("--no-index", action="store_true", help="분석 후 RAG 색인(index_highlights)을 하지 않음")
        parser.add_argument("--refill-players", "--refill-pitchers", dest="refill_players", action="store_true",
                            help="저장된 장면의 빈 타자·투수 칸을 설명 속 로스터 이름·같은 이닝 앞 장면으로 채움 "
                                 "(재분석·비용 없음, --dry-run 이면 숫자만)")

    def handle(self, *args, **o):
        if o["limit"] < 1 or o["budget"] <= 0:
            raise CommandError("--limit 은 1 이상, --budget 은 0 보다 커야 합니다.")
        if o["refill_players"]:
            highlights.refill_players(dry_run=o["dry_run"], index_rag=not o["no_index"], report=self.stdout.write)
            return
        ids = [v.strip() for v in o["video_ids"].split(",") if v.strip()]
        try:
            summary = highlights.collect(o["since"], o["team"], o["limit"], o["budget"], o["model"], ids,
                                         o["reanalyze"], o["dry_run"], report=self.stdout.write,
                                         index_rag=not o["no_index"])
        except highlights.HighlightError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(self.style.SUCCESS(
            f"영상 {summary['found']}개 | 분석 {summary['analyzed']} · 건너뜀 {summary['skipped']} · 실패 {summary['failed']} "
            f"| 비용 ${summary['cost']}"))
