#!/usr/bin/env bash
set -uo pipefail

# Docker backend에서 실행되는 크롤러 공통 실행기
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
export TZ="Asia/Seoul"
RUN_ID="$(date '+%Y%m%d_%H%M%S_KST')"
STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S KST')"

# 이 배열에 파일 경로를 추가하면 다음 실행부터 자동으로 포함된다.
CRAWLERS=(
  "kbo_schedule.py"
  "kbo_standing.py"
  "kbo_ticket_db.py"
  "kbo_highlights.py"  # HIGHLIGHT_AUTO_COLLECT=1 일 때만 실제 수집
)

log() {
  printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S KST')" "$1" "$2"
}

log INFO "실행 시작 run_id=$RUN_ID 크롤러_수=${#CRAWLERS[@]} 시작시간=$STARTED_AT"
log INFO "프로젝트_경로=$PROJECT_DIR 파이썬=$PYTHON_BIN"

success_count=0
failure_count=0
skipped_count=0

for crawler in "${CRAWLERS[@]}"; do
  crawler_path="$SCRIPT_DIR/$crawler"
  if [[ ! -f "$crawler_path" ]]; then
    log WARN "상태=건너뜀 크롤러=$crawler 사유=파일_없음"
    skipped_count=$((skipped_count + 1))
    continue
  fi

  started_epoch="$(date +%s)"
  log INFO "상태=시작 크롤러=$crawler"

  # -u로 Python 출력이 즉시 cron 로그에 기록되도록 한다.
  if (cd "$PROJECT_DIR" && "$PYTHON_BIN" -u "$crawler_path"); then
    ended_epoch="$(date +%s)"
    log INFO "상태=성공 크롤러=$crawler 소요 시간=$((ended_epoch - started_epoch))"
    success_count=$((success_count + 1))
  else
    exit_code=$?
    ended_epoch="$(date +%s)"
    log ERROR "상태=실패 크롤러=$crawler 종료코드=$exit_code 소요 시간=$((ended_epoch - started_epoch))"
    failure_count=$((failure_count + 1))
  fi
done

log INFO "상태=전체요약 성공=$success_count 실패=$failure_count 건너뜀=$skipped_count"

# 일부라도 실패하면 cron이 실패 실행으로 인식하도록 한다.
if (( failure_count > 0 )); then
  exit 1
fi
