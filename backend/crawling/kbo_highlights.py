"""매일 크롤러 실행 때 최근 KBO 경기 하이라이트를 장면표로 채운다(llm.highlights.collect).

HIGHLIGHT_AUTO_COLLECT=1 일 때만 동작한다. 기본은 꺼짐: local_scheduler 는 API 요청으로 하루 한 번 돌기 때문에
켜 두면 팀원 PC 마다 OpenRouter 비용이 나간다. 운영 서버 cron(run_crawlers.sh)에서만 켠다.
최근 HIGHLIGHT_LOOKBACK_DAYS(기본 3)일 업로드 중 아직 분석 안 한 영상만 분석하고(실패한 영상은 다음 날 다시),
누적 비용이 HIGHLIGHT_DAILY_BUDGET(USD, 기본 2)에 닿으면 멈춘다.
"""
import os
from datetime import datetime, timedelta

try:  # run_crawlers.sh 는 crawling/ 을 경로에 두고 실행한다
    from common.cron_tving import KST, _setup
except ImportError:  # 테스트에서 패키지로 import 할 때
    from crawling.common.cron_tving import KST, _setup


def main(today=None):
    if os.getenv("HIGHLIGHT_AUTO_COLLECT") != "1":
        print("하이라이트 수집 건너뜀: HIGHLIGHT_AUTO_COLLECT=1 이 아님")
        return None
    _setup()
    from llm import highlights
    since = (today or datetime.now(KST).date()) - timedelta(days=int(os.getenv("HIGHLIGHT_LOOKBACK_DAYS", "3")))
    try:
        summary = highlights.collect(since=since, limit=int(os.getenv("HIGHLIGHT_DAILY_LIMIT", "20")),
                                     budget=float(os.getenv("HIGHLIGHT_DAILY_BUDGET", "2")),
                                     index_rag=os.getenv("HIGHLIGHT_RAG_INDEX", "1") != "0")
    except (highlights.HighlightError, OSError) as exc:  # 키 없음·YouTube 접속 실패: 다른 크롤러의 하루 1회 기록을 막지 않는다
        print(f"하이라이트 수집 건너뜀: {type(exc).__name__}: {exc}")
        return None
    print(f"하이라이트 적재 완료: 영상={summary['found']} 분석={summary['analyzed']} 건너뜀={summary['skipped']} "
          f"실패={summary['failed']} 비용=${summary['cost']}")
    return summary


if __name__ == "__main__":
    main()
