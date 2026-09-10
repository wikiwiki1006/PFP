#!/usr/bin/env bash
# 로컬 개발 실행 — 백엔드 + 프론트엔드.
#
#   ./dev.sh            로컬 DB (기본)
#   ./dev.sh --prod-db  운영 DB (조회·재현용, 쓰기 주의)
#
# 로그인은 고정 테스트 계정으로만 한다:
#     test@gmail.com / 10october@
# 없으면 아래 명령으로 만든다:
#     ./venv/bin/python -m backend.scripts.seed_test_user
#
# Firebase 프로젝트는 로컬과 운영이 같다. 그래서 로컬에서 아무 이메일로나
# 가입하면 실서비스 계정과 섞이고, 운영에 있는 이메일로는 가입도 안 된다.
# 계정을 하나로 고정하면 그런 충돌이 없다.
set -euo pipefail
cd "$(dirname "$0")"

PY=./venv/bin/python
[ -x "$PY" ] || { echo "venv 가 없습니다: $PY"; exit 1; }

cleanup() { pkill -P $$ 2>/dev/null || true; }
trap cleanup EXIT INT TERM

if [ "${1:-}" = "--prod-db" ]; then
  echo "▸ 백엔드 (8000) — 운영 DB. 쓰기 작업에 주의하세요"
  DATABASE_URL="$(gcloud secrets versions access latest \
    --secret=DATABASE_URL --project=personalfinancialplatform)" \
    "$PY" -m uvicorn backend.main:app --reload --port 8000 &
else
  echo "▸ 백엔드 (8000) — 로컬 DB"
  "$PY" -m uvicorn backend.main:app --reload --port 8000 &
fi

echo "▸ 프론트엔드 (3000)  ·  로그인: test@gmail.com / 10october@"
(cd frontend && npm run dev)
