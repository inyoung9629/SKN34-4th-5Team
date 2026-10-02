import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from travel.place_rag import KINDS, SCOPES, STADIUMS, RagUnavailable, index_report, retrieve


class Command(BaseCommand):
    help = "장소 RAG 읽기 전용 확인. --include-research는 확정되지 않은 실험 기록도 표시"
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("query", nargs="?", default="")
        parser.add_argument("--index", type=Path)
        parser.add_argument("--stats", action="store_true")
        parser.add_argument("--stadium", choices=sorted(STADIUMS))
        parser.add_argument("--kind", choices=sorted(KINDS))
        parser.add_argument("--scope", choices=sorted(SCOPES | {"all"}), default="external_candidate")
        parser.add_argument("--place-id")
        parser.add_argument("--limit", type=int, default=5)
        parser.add_argument("--include-research", action="store_true")

    def handle(self, *args, **options):
        try:
            data = index_report(options["index"]) if options["stats"] else retrieve(
                options["query"], path=options["index"], stadium_code=options["stadium"],
                kind=options["kind"], scope=options["scope"], place_id=options["place_id"],
                limit=options["limit"], include_research=options["include_research"],
            )
        except (ValueError, RagUnavailable) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
