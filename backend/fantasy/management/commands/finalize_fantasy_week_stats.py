from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from fantasy.models import FantasyWeek
from fantasy.services.settlement import FantasySettlementNotReady, validate_week_stats_complete


class Command(BaseCommand):
    help = "경기 기록 적재가 끝난 주차를 명시적으로 결산 가능 상태로 표시합니다."

    def add_arguments(self, parser):
        parser.add_argument("--week-start", type=date.fromisoformat, required=True)
        parser.add_argument(
            "--confirm-loaded",
            action="store_true",
            help="해당 주차의 선수별 경기 기록 적재가 완료되었음을 확인합니다.",
        )

    def handle(self, *args, **options):
        if not options["confirm_loaded"]:
            raise CommandError("기록 적재 완료를 확인한 경우에만 --confirm-loaded를 지정하세요.")
        try:
            week = FantasyWeek.objects.get(week_start=options["week_start"])
        except FantasyWeek.DoesNotExist as error:
            raise CommandError("해당 시작일의 환상야구 주차가 없습니다.") from error
        if week.status == FantasyWeek.SETTLED:
            raise CommandError("이미 결산된 주차의 기록 상태는 변경할 수 없습니다.")

        try:
            validate_week_stats_complete(week)
        except FantasySettlementNotReady as error:
            raise CommandError(str(error)) from error

        week.stats_finalized_at = timezone.now()
        week.save(update_fields=("stats_finalized_at", "updated_at"))
        self.stdout.write(self.style.SUCCESS(f"{week.week_start} 주차의 기록 적재 완료를 확인했습니다."))
