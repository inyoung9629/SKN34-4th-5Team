from datetime import date
from secrets import randbelow

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from fantasy.models import FantasySelection, FantasyWeek
from fantasy.services.scoring import BATTER_STATS, PITCHER_STATS
from fantasy.services.weeks import current_week, fantasy_today, week_bounds


class Command(BaseCommand):
    help = "게임 주차가 시작된 선택의 점수 배율을 서버에서 확정합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--week-start",
            type=date.fromisoformat,
            help="복구 실행할 주차의 월요일 날짜(YYYY-MM-DD). 생략하면 현재 주차를 사용합니다.",
        )

    def handle(self, *args, **options):
        week_start = options["week_start"]
        if week_start is None:
            week = current_week()
        else:
            if week_bounds(week_start)[0] != week_start:
                raise CommandError("--week-start는 해당 주차의 월요일이어야 합니다.")
            try:
                week = FantasyWeek.objects.get(week_start=week_start)
            except FantasyWeek.DoesNotExist as error:
                raise CommandError("해당 시작일의 환상야구 주차가 없습니다.") from error
        if week.status == FantasyWeek.SETTLED:
            self.stdout.write("이미 결산된 주차이므로 배율 확정을 건너뜁니다.")
            return
        if week.week_start > fantasy_today():
            raise CommandError("미래 주차의 배율은 미리 확정할 수 없습니다.")
        count = self.confirm_week(week)
        self.stdout.write(self.style.SUCCESS(f"{count}개 선택을 확정했습니다."))

    @transaction.atomic
    def confirm_week(self, week):
        week = FantasyWeek.objects.select_for_update().get(pk=week.pk)
        if week.status == FantasyWeek.SETTLED:
            return 0
        if week.week_start > fantasy_today():
            raise CommandError("미래 주차의 배율은 미리 확정할 수 없습니다.")
        count = 0
        selections = FantasySelection.objects.select_for_update().select_related("player").filter(
            week=week, is_confirmed=False
        )
        for selection in selections:
            stats = PITCHER_STATS if selection.fantasy_type == "PITCHER" else BATTER_STATS
            selection.stat_weights = {key: randbelow(9) + 1 for key in stats}
            selection.is_confirmed = True
            selection.confirmed_at = timezone.now()
            selection.save(update_fields=("stat_weights", "is_confirmed", "confirmed_at", "updated_at"))
            count += 1
        return count
