"""Explicitly install/remove the two presentation games; never loaded at startup."""
from datetime import date, time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from baseball.data_loader import stable_id
from baseball.models import Game, Stadium, Team


DEMO_DATE = date(2026, 10, 16)
SOURCE = "demo"
GAME_TYPE = "DEMO"
FIXTURES = (
    ("demo:20261016:GWANGJU:KIA:NC", "GWANGJU", "KIA", "NC", time(14)),
    ("demo:20261016:JAMSIL:DOOSAN:LOTTE", "JAMSIL", "DOOSAN", "LOTTE", time(17)),
)


def demo_rows():
    # All markers must agree: unrelated demo rows and official games are untouched.
    return Game.objects.filter(
        game_code__in=[row[0] for row in FIXTURES], source=SOURCE,
        game_type=GAME_TYPE, source_external_code=F("game_code"),
    )


class Command(BaseCommand):
    help = "2026-10-16 광주 14:00 / 잠실 17:00 가상 경기: apply, status, remove"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("apply", "status", "remove"), nargs="?", default="status")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "apply":
            self.apply()
        elif action == "remove":
            with transaction.atomic():
                rows = demo_rows().select_for_update()
                count = rows.count()
                rows.delete()
            self.stdout.write(self.style.SUCCESS(f"removed={count} (이 명령으로 등록한 가상 경기만 삭제)"))
        self.show_status()

    @transaction.atomic
    def apply(self):
        created = 0
        for code, stadium_code, home_code, away_code, clock in FIXTURES:
            try:
                stadium = Stadium.objects.get(stadium_code=stadium_code)
                home = Team.objects.get(team_code=home_code)
                away = Team.objects.get(team_code=away_code)
            except (Stadium.DoesNotExist, Team.DoesNotExist) as exc:
                raise CommandError("필요한 구장·팀 데이터가 없습니다. 등록을 모두 취소합니다.") from exc

            conflicts = Game.objects.filter(game_date=DEMO_DATE).filter(
                Q(stadium=stadium) | Q(home_team__in=(home, away)) | Q(away_team__in=(home, away))
            ).exclude(pk__in=demo_rows().values("pk"))
            if conflicts.exists():
                raise CommandError(f"{DEMO_DATE} {stadium_code} 구장 또는 팀의 기존 일정이 있습니다. 덮어쓰지 않습니다.")

            existing = Game.objects.select_for_update().filter(game_code=code).first()
            if existing:
                if not demo_rows().filter(pk=existing.pk).exists():
                    raise CommandError(f"가상 경기 식별자 충돌: {code}. 기존 경기를 수정하지 않습니다.")
                if (existing.game_date, existing.game_time, existing.stadium_id,
                    existing.home_team_id, existing.away_team_id, existing.status_code) != (
                    DEMO_DATE, clock, stadium.pk, home.pk, away.pk, "scheduled"
                ):
                    raise CommandError(f"가상 일정이 변경되어 있습니다: {code}. remove 후 apply로 복구하세요.")
                continue

            pk = stable_id(Game, code)
            if Game.objects.filter(pk=pk).exists() or Game.objects.filter(source_external_code=code).exists():
                raise CommandError(f"가상 경기 ID 충돌: {code}. 기존 경기를 수정하지 않습니다.")
            Game.objects.create(
                id=pk, game_code=code, game_date=DEMO_DATE, game_time=clock,
                stadium=stadium, home_team=home, away_team=away,
                status_code="scheduled", game_type=GAME_TYPE, source=SOURCE,
                # Non-null identity keeps TVING ingestion from adopting this row.
                source_external_code=code, source_status_label="가상 경기",
                source_stadium_name=stadium.stadium_name_ko,
                source_home_name=home.team_name_ko, source_away_name=away.team_name_ko,
                collected_at=timezone.now(),
            )
            created += 1
        self.stdout.write(self.style.SUCCESS(f"created={created}"))

    def show_status(self):
        rows = demo_rows().select_related("stadium", "home_team", "away_team").order_by("game_time")
        self.stdout.write(f"active_demo_games={rows.count()}")
        for row in rows:
            self.stdout.write(
                f"[가상] {row.game_date} {row.game_time:%H:%M} KST | "
                f"{row.stadium.stadium_name_ko} | {row.home_team.team_name_ko} 홈 vs {row.away_team.team_name_ko} 원정"
            )
