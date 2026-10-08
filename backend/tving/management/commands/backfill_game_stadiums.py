from django.core.management.base import BaseCommand
from django.db import transaction

from baseball.models import Game
from tving.relational import stadium_for


class Command(BaseCommand):
    help = "TVING 경기의 누락된 구장 연결을 원본 구장명으로 복구합니다 (기본: 미리보기)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="확인된 구장 연결을 실제 저장합니다.")

    def handle(self, *args, **options):
        matched = updated = unresolved = 0
        venues = {}
        with transaction.atomic():
            rows = Game.objects.filter(source="tving", stadium__isnull=True)
            for row in rows.only("pk", "source_stadium_name").iterator():
                name = row.source_stadium_name
                if name not in venues:
                    venues[name] = stadium_for(name)
                stadium = venues[name]
                if stadium is None:
                    unresolved += 1
                    continue
                matched += 1
                if options["apply"]:
                    updated += Game.objects.filter(
                        pk=row.pk, source="tving", stadium__isnull=True, source_stadium_name=name,
                    ).update(stadium=stadium)
        self.stdout.write(f"matched={matched} updated={updated} unresolved={unresolved}")
