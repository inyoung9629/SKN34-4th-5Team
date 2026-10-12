#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
DOCKER_BIN="${DOCKER_BIN:-docker}"
cd "$PROJECT_DIR"

usage() {
  echo "Usage: FANTASY_ENV=dev|prod $0 confirm|settle|all" >&2
}

if [[ -z "${FANTASY_ENV:-}" ]]; then
  echo "FANTASY_ENV must be set to local, dev, or prod." >&2
  usage
  exit 2
fi

if [[ "$#" -lt 1 ]]; then
  usage
  exit 2
fi

case "$FANTASY_ENV" in
  dev)
    COMPOSE_ARGS=(-f "$PROJECT_DIR/docker-compose.yml" -f "$PROJECT_DIR/docker-compose.dev.yml")
    ;;
  prod)
    COMPOSE_ARGS=(-f "$PROJECT_DIR/docker-compose.prod.yml")
    ;;
  *)
    echo "Unsupported FANTASY_ENV: $FANTASY_ENV" >&2
    usage
    exit 2
    ;;
esac

run_confirm() {
  "$DOCKER_BIN" compose "${COMPOSE_ARGS[@]}" exec -T backend python manage.py confirm_fantasy_selections "$@"
}

run_settle() {
  "$DOCKER_BIN" compose "${COMPOSE_ARGS[@]}" exec -T backend python manage.py settle_fantasy_week "$@"
}

case "$1" in
  confirm)
    shift
    run_confirm "$@"
    ;;
  settle)
    shift
    run_settle "$@"
    ;;
  all)
    shift
    if [[ "$#" -ne 0 ]]; then
      usage
      exit 2
    fi
    run_confirm
    run_settle
    ;;
  *)
    usage
    exit 2
    ;;
esac
