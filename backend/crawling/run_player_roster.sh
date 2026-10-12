#!/usr/bin/env bash
set -uo pipefail

# EC2 cron에서 선수 로스터만 독립적으로 실행한다.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

export TZ="Asia/Seoul"

RUN_ID="$(date '+%Y%m%d_%H%M%S_KST')"
STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S KST')"

CRAWLER="kbo_player.py"

log() {
  printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S KST')" "$1" "$2"
}

log INFO "실행 시작 run_id=$RUN_ID 시작시간=$STARTED_AT"
log INFO "프로젝트_경로=$PROJECT_DIR 파이썬=$PYTHON_BIN"
log INFO "대상_크롤러=$CRAWLER"

CRAWLER_PATH="$SCRIPT_DIR/$CRAWLER"

if [[ ! -f "$CRAWLER_PATH" ]]; then
  log ERROR "상태=실패 크롤러=$CRAWLER 사유=파일_없음 경로=$CRAWLER_PATH"
  exit 1
fi

started_epoch="$(date +%s)"

log INFO "상태=시작 크롤러=$CRAWLER"

# -u로 Python 출력이 즉시 cron 로그에 기록되도록 한다.
if (cd "$PROJECT_DIR" && "$PYTHON_BIN" -u "$CRAWLER_PATH"); then
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