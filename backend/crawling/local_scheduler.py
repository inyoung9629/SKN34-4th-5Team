"""로컬 API 요청에서 하루 한 번만 크롤러를 실행한다."""

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.cache import cache

KST = ZoneInfo("Asia/Seoul")
LAST_RUN_KEY = "crawling:last_run_date"
LOCK_KEY = "crawling:daily_lock"
CRAWLERS = ("kbo_schedule.py", "kbo_standing.py", "kbo_ticket_db.py")


def run_if_needed_today():
    """오늘 성공한 실행이 없을 때만 전체 크롤러를 실행한다."""
    today = datetime.now(KST).date().isoformat()
    if cache.get(LAST_RUN_KEY) == today:
        return False
    if not cache.add(LOCK_KEY, today, timeout=60 * 60):
        return False
    try:
        crawling_dir = Path(__file__).resolve().parent
        project_dir = crawling_dir.parent
        for crawler in CRAWLERS:
            subprocess.run(
                [sys.executable, str(crawling_dir / crawler)],
                cwd=project_dir,
                check=True,
                timeout=15 * 60,
            )
        cache.set(LAST_RUN_KEY, today, timeout=None)
        return True
    finally:
        cache.delete(LOCK_KEY)
