#!/usr/bin/env bash
# 로컬 개발 실행 — 백엔드 + 프론트엔드.
#
#   ./dev.sh                로컬 DB, 포트 8000/3000 (기본)
#   ./dev.sh --prod-db      운영 DB (조회·재현용, 쓰기 주의)
#   ./dev.sh --slot 1       포트 8001/3001 — 병렬 worktree 용
#   ./dev.sh --slot 2 --db-branch agent-test
#
# 로그인은 고정 테스트 계정으로만 한다:
#     test@gmail.com / 10october@
# 없으면 아래 명령으로 만든다:
#     "$PY" -m backend.scripts.seed_test_user   (PY 는 아래에서 자동 탐지)
#
# Firebase 프로젝트는 로컬과 운영이 같다. 그래서 로컬에서 아무 이메일로나
# 가입하면 실서비스 계정과 섞이고, 운영에 있는 이메일로는 가입도 안 된다.
# 계정을 하나로 고정하면 그런 충돌이 없다.
#
# --slot 은 병렬 창(worktree)마다 포트를 갈라 준다. 슬롯 N 은 백엔드
# 8000+N, 프론트 3000+N 을 쓴다. 창을 여러 개 띄우면서 슬롯을 안 주면
# strictPort 때문에 두 번째 창이 즉시 실패한다 — 의도된 동작이다.
# 조용히 다른 포트로 옮겨가면 두 창이 같은 백엔드를 보고 있는 걸
# 눈치채지 못한다.
#
# --db-branch 는 Neon 브랜치 이름이다. 창마다 DB 를 갈라 두지 않으면
# 한 창의 테스트가 다른 창의 holdings 를 밀어낸다
# (기본키가 (user_id, market, ticker) 라 같은 종목이 서로 덮인다).
set -euo pipefail
cd "$(dirname "$0")"

# venv 인터프리터 — macOS/Linux 는 bin/python, Windows 는 Scripts/python.exe
if   [ -x ./venv/bin/python ];         then PY=./venv/bin/python
elif [ -x ./venv/Scripts/python.exe ]; then PY=./venv/Scripts/python.exe
else echo "venv 가 없습니다 (./setup.sh 를 먼저 돌리세요)"; exit 1
fi

SLOT=0
PROD_DB=0
DB_BRANCH=""

while [ $# -gt 0 ]; do
  case "$1" in
    --prod-db)   PROD_DB=1; shift ;;
    --slot)      SLOT="${2:?--slot 에 숫자가 필요합니다}"; shift 2 ;;
    --db-branch) DB_BRANCH="${2:?--db-branch 에 이름이 필요합니다}"; shift 2 ;;
    -h|--help)   sed -n '2,30p' "$0"; exit 0 ;;
    *)           echo "알 수 없는 옵션: $1"; exit 1 ;;
  esac
done

case "$SLOT" in ''|*[!0-9]*) echo "--slot 은 0 이상의 정수여야 합니다: $SLOT"; exit 1 ;; esac

BE_PORT=$((8000 + SLOT))
FE_PORT=$((3000 + SLOT))

# 포트가 이미 물려 있으면 여기서 멈춘다. 백엔드를 띄운 뒤 프론트가 실패하면
# 고아 uvicorn 이 남는다.
# lsof 는 Windows Git Bash 에 없다. 없으면 netstat 으로 본다.
port_busy() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  elif command -v netstat >/dev/null 2>&1; then
    netstat -ano -p tcp 2>/dev/null | grep -qE "[:.]$1[[:space:]].*(LISTENING|LISTEN)"
  else
    return 1   # 검사할 수단이 없으면 막지 않는다. vite 의 strictPort 가 뒤에서 잡는다.
  fi
}
for p in "$BE_PORT" "$FE_PORT"; do
  if port_busy "$p"; then
    echo "포트 $p 가 이미 사용 중입니다. 다른 --slot 을 쓰세요."
    exit 1
  fi
done

# 프론트가 죽었을 때 고아 uvicorn 이 남지 않게 한다. pkill 은 Git Bash 에 없어서
# 백엔드 PID 를 직접 들고 있다가 죽인다 — Windows 에선 --reload 가 자식을 하나 더
# 띄우므로 taskkill //T 로 트리째 정리한다.
BE_PID=""
cleanup() {
  if [ -n "$BE_PID" ]; then
    if command -v taskkill >/dev/null 2>&1; then
      WINPID="$(ps -p "$BE_PID" -o winpid= 2>/dev/null | tr -d ' ')"
      [ -n "$WINPID" ] && taskkill //PID "$WINPID" //T //F >/dev/null 2>&1 || true
    fi
    kill "$BE_PID" 2>/dev/null || true
  fi
  pkill -P $$ 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [ "$PROD_DB" = 1 ]; then
  [ -n "$DB_BRANCH" ] && { echo "--prod-db 와 --db-branch 는 함께 쓸 수 없습니다"; exit 1; }
  echo "▸ 백엔드 ($BE_PORT) — 운영 DB. 쓰기 작업에 주의하세요"
  DB_URL="$(gcloud secrets versions access latest \
    --secret=DATABASE_URL --project=personalfinancialplatform)"
elif [ -n "$DB_BRANCH" ]; then
  # Neon 브랜치별 연결 문자열을 환경변수로 미리 넣어 둔다:
  #   export PFP_DB_agent_test="postgres://..."
  VAR="PFP_DB_${DB_BRANCH//-/_}"
  DB_URL="${!VAR:-}"
  [ -n "$DB_URL" ] || { echo "$VAR 가 설정되어 있지 않습니다 (Neon 브랜치 연결 문자열)"; exit 1; }
  echo "▸ 백엔드 ($BE_PORT) — Neon 브랜치 '$DB_BRANCH'"
else
  DB_URL="${DATABASE_URL:-}"
  echo "▸ 백엔드 ($BE_PORT) — 로컬 DB"
fi

if [ -n "$DB_URL" ]; then
  DATABASE_URL="$DB_URL" "$PY" -m uvicorn backend.main:app --reload --port "$BE_PORT" &
else
  "$PY" -m uvicorn backend.main:app --reload --port "$BE_PORT" &
fi
BE_PID=$!

echo "▸ 프론트엔드 ($FE_PORT)  ·  로그인: test@gmail.com / 10october@"
(cd frontend && PFP_FE_PORT="$FE_PORT" PFP_BE_PORT="$BE_PORT" npm run dev)
