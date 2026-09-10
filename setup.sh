#!/usr/bin/env bash
# 새 머신에서 클론한 뒤 한 번 돌린다.
#
#   git clone https://github.com/wikiwiki1006/PFP.git pfp
#   cd pfp && ./setup.sh
#
# git 은 추적되는 파일만 준다. 개발에 필요한 것 중 추적되지 않는 게 넷이고,
# 없으면 백엔드가 기동조차 하지 않는다:
#
#   venv/                  파이썬 의존성
#   frontend/node_modules  프론트 의존성
#   backend/.env           DB·API 키          → Secret Manager 에서 받는다
#   secrets/firebase-admin.json  Firebase Admin → Secret Manager 에서 받는다
#
# 비밀값은 리포에 없다. gcloud 로그인이 되어 있어야 한다:
#   gcloud auth login && gcloud config set project personalfinancialplatform
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
PROJECT=personalfinancialplatform

say(){ printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
warn(){ printf '  \033[33m! %s\033[0m\n' "$1"; }

# ── 1. 파이썬 ────────────────────────────────────────────
say "파이썬 가상환경"
if [ -x venv/bin/python ]; then
  echo "  이미 있음 — $(venv/bin/python -V 2>&1)"
else
  PY=""
  for c in python3.12 python3.11 python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    v="$("$c" -c 'import sys;print("%d%d"%sys.version_info[:2])')"
    [ "$v" -ge 311 ] 2>/dev/null && { PY="$c"; break; }
  done
  [ -n "$PY" ] || { echo "  python 3.11+ 이 필요합니다"; exit 1; }
  echo "  $PY ($("$PY" -V 2>&1)) 로 생성"
  "$PY" -m venv venv
  venv/bin/python -m pip install -q --upgrade pip
  venv/bin/python -m pip install -q -r requirements.txt
  [ -f requirements-dev.txt ] && venv/bin/python -m pip install -q -r requirements-dev.txt
  echo "  의존성 설치 완료"
fi
# venv/bin/uvicorn 의 셔뱅은 옛 경로를 가리켜 깨져 있을 수 있다.
# 항상 venv/bin/python -m uvicorn 으로 부른다 — 스크립트도 전부 그렇게 한다.

# ── 2. 프론트 ────────────────────────────────────────────
say "프론트엔드 의존성"
if [ -d frontend/node_modules ]; then
  echo "  이미 있음"
else
  command -v npm >/dev/null 2>&1 || { echo "  node/npm 이 필요합니다"; exit 1; }
  (cd frontend && npm ci --silent 2>/dev/null || npm install --silent)
  echo "  설치 완료"
fi

# ── 3. 비밀값 ────────────────────────────────────────────
say "비밀값 (Secret Manager)"
HAVE_GCLOUD=0
if command -v gcloud >/dev/null 2>&1 && \
   gcloud auth print-access-token >/dev/null 2>&1; then HAVE_GCLOUD=1; fi

sm(){ gcloud secrets versions access latest --secret="$1" --project="$PROJECT" 2>/dev/null; }

if [ -f backend/.env ]; then
  echo "  backend/.env 이미 있음 — 건드리지 않음"
elif [ "$HAVE_GCLOUD" = 1 ]; then
  {
    echo "# setup.sh 가 Secret Manager 에서 생성했다. 커밋되지 않는다."
    echo "#"
    echo "# DATABASE_URL 은 일부러 쓰지 않는다. backend/db/__init__.py 가 그 값을"
    echo "# DB_* 보다 우선하므로, 여기에 운영 Neon URL 을 넣으면 로컬 개발이"
    echo "# 조용히 운영 DB 에 붙는다. 운영 DB 가 필요하면 ./dev.sh --prod-db 로"
    echo "# 그때만 명시적으로 건다."
    echo "DB_HOST=localhost"
    echo "DB_PORT=5432"
    echo "DB_NAME=postgres"
    echo "DB_USER=${USER:-postgres}"
    echo "DB_PASSWORD="
    for k in ANTHROPIC_API_KEY PERPLEXITY_API_KEY KOREA_BANK_API_KEY \
             KAKAO_REST_API_KEY KAKAO_CLIENT_SECRET \
             NAVER_CLIENT_ID NAVER_CLIENT_SECRET; do
      echo "$k=$(sm "$k")"
    done
  } > backend/.env
  chmod 600 backend/.env
  MISSING="$(grep -cE '^[A-Z_]+_KEY=$|^[A-Z_]+_SECRET=$|^[A-Z_]+_ID=$' backend/.env || true)"
  [ "$MISSING" -gt 0 ] && warn "값이 비어 있는 키가 $MISSING 개 있습니다 — backend/.env 를 확인하세요"
  echo "  backend/.env 생성 (로컬 DB · 운영은 ./dev.sh --prod-db 로만)"
else
  warn "gcloud 인증이 없어 backend/.env 를 만들지 못했습니다."
  warn "  gcloud auth login && gcloud config set project $PROJECT"
  warn "  이후 ./setup.sh 를 다시 돌리세요."
fi

mkdir -p secrets
if [ -f secrets/firebase-admin.json ]; then
  echo "  secrets/firebase-admin.json 이미 있음"
elif [ "$HAVE_GCLOUD" = 1 ] && sm FIREBASE_ADMIN_JSON > secrets/firebase-admin.json 2>/dev/null \
     && [ -s secrets/firebase-admin.json ]; then
  chmod 600 secrets/firebase-admin.json
  echo "  secrets/firebase-admin.json 받음"
else
  rm -f secrets/firebase-admin.json
  warn "secrets/firebase-admin.json 이 없습니다 — 로그인 검증이 실패합니다."
  warn "  기존 머신에서 한 번만 Secret Manager 에 올려두면 이후 자동으로 받습니다:"
  warn "    gcloud secrets create FIREBASE_ADMIN_JSON --project=$PROJECT \\"
  warn "      --data-file=secrets/firebase-admin.json"
fi

say "로컬 PostgreSQL"
if lsof -nP -iTCP:5432 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "  5432 리스닝 확인"
else
  warn "5432 에 PostgreSQL 이 없습니다. 로컬 개발은 여기에 붙는다."
  warn "  macOS:  brew install postgresql@16 && brew services start postgresql@16"
  warn "  스키마는 기동 시 init_schema() 가 자동 적용하므로 빈 DB 면 된다."
fi

if [ ! -f frontend/.env.local ]; then
  echo "VITE_USE_AUTH_EMULATOR=false" > frontend/.env.local
  echo "  frontend/.env.local 생성 (Firebase 웹 설정은 공개값이라 .env.example 기본값을 쓴다)"
fi

# ── 4. 검증 ─────────────────────────────────────────────
say "검증"
if venv/bin/python -m pytest backend/tests -q 2>&1 | tail -1; then :; fi
(cd frontend && npm run build >/dev/null 2>&1 && echo "  npm run build OK" \
  || echo "  npm run build 실패 — 위 pytest 결과와 함께 확인하세요")

say "완료"
cat <<EOF
  단독 개발:   ./dev.sh
  병렬 개발:   CLAUDE.md 의 "병렬 에이전트 협업" 절을 따른다.
               ./worktree-setup.sh <역할> <슬롯>
EOF
