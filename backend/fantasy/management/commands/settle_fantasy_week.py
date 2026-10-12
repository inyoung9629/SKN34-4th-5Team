from django.core.management.base import BaseCommand, CommandError
from fantasy.models import FantasyWeek
from fantasy.services.settlement import FantasySettlementNotReady, settle_week
from fantasy.services.weeks import fantasy_today


class Command(BaseCommand):
    help = "결산 예정일이 지난 환상야구 주차 중 기록 준비가 완료된 주차를 결산합니다."

    def handle(self, *args, **options):
        weeks = FantasyWeek.objects.filter(
            settlement_date__lte=fantasy_today(), status=FantasyWeek.OPEN
        ).order_by("week_start")
        count = 0
        blocked = []
        for week in weeks:
            try:
                settle_week(week.pk)
            except FantasySettlementNotReady as error:
                blocked.append(f"{week.week_start}: {error}")
                self.stderr.write(self.style.WARNING(blocked[-1]))
                continue
            count += 1
        self.stdout.write(self.style.SUCCESS(f"{count}개 주차를 결산했습니다."))
        if blocked:
            raise CommandError(f"{len(blocked)}개 주차가 준비되지 않았습니다. 기록 상태를 확인하세요.")
