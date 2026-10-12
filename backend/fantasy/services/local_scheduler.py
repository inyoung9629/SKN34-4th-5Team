import logging
from datetime import timedelta
from io import StringIO
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.core.management import CommandError, call_command

from fantasy.services.weeks import fantasy_today

logger = logging.getLogger(__name__)

LAST_RUN_KEY = "fantasy:local_daily_jobs:last_run_date"
LOCK_KEY = "fantasy:local_daily_jobs:lock"
LOCK_TIMEOUT_SECONDS = 60 * 60
LAST_RUN_TIMEOUT_SECONDS = 2 * 24 * 60 * 60
SETTLEMENT_NOT_READY_MESSAGE = "주차가 준비되지 않았습니다. 기록 상태를 확인하세요."


def run_local_jobs_if_needed():
    if not settings.FANTASY_LOCAL_REQUEST_JOBS_ENABLED:
        return False

    today = fantasy_today().isoformat()
    if cache.get(LAST_RUN_KEY) == today:
        return False
    lock_token = uuid4().hex
    if not cache.add(LOCK_KEY, lock_token, timeout=LOCK_TIMEOUT_SECONDS):
        return False

    output = StringIO()
    try:
        call_command("confirm_fantasy_selections", stdout=output, stderr=output)
        try:
            call_command("settle_fantasy_week", stdout=output, stderr=output)
        except CommandError as error:
            if SETTLEMENT_NOT_READY_MESSAGE not in str(error):
                raise
            logger.warning(
                "Local fantasy settlement deferred: %s\n%s",
                error,
                output.getvalue().strip(),
            )
        else:
            logger.info("Local fantasy daily jobs completed:\n%s", output.getvalue().strip())

        cache.set(LAST_RUN_KEY, today, timeout=LAST_RUN_TIMEOUT_SECONDS)
        return True
    finally:
        if cache.get(LOCK_KEY) == lock_token:
            cache.delete(LOCK_KEY)
