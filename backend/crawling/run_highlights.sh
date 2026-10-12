#!/usr/bin/env bash
set -uo pipefail

# KBO 하이라이트만 독립적으로 실행한다 (run_player_roster.sh 와 같은 방식).
# 최근 HIGHLIGHT_LOOKBACK_DAYS(기본 3)일 업로드 중 아직 분석 안 한 영상만 Gemini 로 분석 → 장면표 저장
# → 빈 타자·투수 칸 보정 → RAG 색인. 하루 비용 상한 HIGHLIGHT_DAILY_BUDGET(기본 2 USD), 최대 HIGHLIGHT_DAILY_LIMIT(기본 20)개.
# 이 스크립트를 실행하는 것이 곧 수집 의사이므로 이번 실행에만 HIGHLIGHT_AUTO_COLLECT=1 을 준다
# (.env 기본값은 그대로 꺼짐이라 팀원 PC 의 화면 요청 크롤링에서는 비용이 나가지 않는다).
#
#   로컬:     docker compose exec -T backend bash crawling/run_highlights.sh
#   EC2 cron: 30 1 * * * cd <프로젝트> && docker compose exec -T backend bash crawling/run_highlights.sh >> <로그> 2>&1
#             (경기 영상은 밤 경기 종료 뒤 올라오므로 새벽 실행. 선수 로스터가 비어 있으면 run_player_roster.sh 를 먼저)
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

export TZ="Asia/Seoul"

RUN_ID="$(date '+%Y%m%d_%H%M%S_KST')"
STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S KST')"

CRAWLER="kbo_highlights.py"

log() {
  printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S KST')" "$1" "$2"
}

log INFO "실행 시작 run_id=$RUN_ID 시작시간=$STARTED_AT"
log INFO "프로젝트_경로=$PROJECT_DIR 파이썬=$PYTHON_BIN"
log INFO "대상_크롤러=$CRAWLER 최근_일수=${HIGHLIGHT_LOOKBACK_DAYS:-3} 비용_상한=${HIGHLIGHT_DAILY_BUDGET:-2} 최대_영상=${HIGHLIGHT_DAILY_LIMIT:-20}"

CRAWLER_PATH="$SCRIPT_DIR/$CRAWLER"

if [[ ! -f "$CRAWLER_PATH" ]]; then
  log ERROR "상태=실패 크롤러=$CRAWLER 사유=파일_없음 경로=$CRAWLER_PATH"
  exit 1
fi

started_epoch="$(date +%s)"

log INFO "상태=시작 크롤러=$CRAWLER"

# -u로 Python 출력이 즉시 cron 로그에 기록되도록 한다.
if (cd "$PROJECT_DIR" && HIGHLIGHT_AUTO_COLLECT=1 "$PYTHON_BIN" -u "$CRAWLER_PATH"); then
  ended_epoch="$(date +%s)"
  elapsed="$((ended_epoch - started_epoch))"

  log INFO "상태=성공 크롤러=$CRAWLER 소요시간=${elapsed}초"
else
  exit_code=$?
  ended_epoch="$(date +%s)"
  elapsed="$((ended_epoch - started_epoch))"

  log ERROR "상태=실패 크롤러=$CRAWLER 종료코드=$exit_code 소요시간=${elapsed}초"
  exit "$exit_code"
fi

log INFO "상태=전체요약 성공=1 실패=0 크롤러=$CRAWLER"
