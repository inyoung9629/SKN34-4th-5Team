from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from fantasy.models import FantasyWeek

FANTASY_TIME_ZONE = ZoneInfo("Asia/Seoul")


def fantasy_today():
    return timezone.localdate(timezone=FANTASY_TIME_ZONE)


def week_bounds(day: date):
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def get_or_create_week(start: date):
    end = start + timedelta(days=6)
    settlement = end + timedelta(days=2)
    week, _ = FantasyWeek.objects.get_or_create(
        week_start=start, week_end=end, defaults={"settlement_date": settlement}
    )
    return week


def current_week():
    return get_or_create_week(week_bounds(fantasy_today())[0])


def next_week():
    return get_or_create_week(week_bounds(fantasy_today())[0] + timedelta(days=7))
