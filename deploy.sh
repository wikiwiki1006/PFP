#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  PFP 배포 스크립트 — 내 컴퓨터에서 외부 접속 가능하게 서빙
#
#  실행:  bash deploy.sh
#
#  동작:
#   1. 프론트엔드 빌드 (npm run build)
#   2. FastAPI 백엔드 시작 (포트 8000, 정적 파일 포함)
#   3. cloudflared 터널 시작 → HTTPS URL 생성 (외부 어디서든 접속 가능)
# ─────────────────────────────────────────────────────────────────────────────

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 인증 에뮬레이터로는 절대 공개 배포하지 않는다 ────────────────────────────
#
# firebase_admin 은 FIREBASE_AUTH_EMULATOR_HOST 가 있으면 에뮬레이터 모드로
# 들어가고, 그 모드에서는 ID 토큰의 **서명 검증을 건너뛴다**
# (_token_gen.py: `if emulated: verified_claims = payload`). kid·alg 검사도
# 함께 빠지므로 남는 검사는 aud/iss/sub 뿐이고 셋 다 위조가 자유롭다.
#
# 이 스크립트는 그 백엔드를 cloudflared 터널로 인터넷에 그대로 노출한다.
# 두 개가 겹치면 아무나 토큰을 위조해 임의 사용자로 로그인할 수 있다.
#
# 로컬 병렬 개발에서 에뮬레이터를 쓸 때는 이 변수를 .env 에 넣지 말고
# `./dev.sh --auth-emulator` 처럼 그 프로세스 환경에만 넣는다. 그러면 이
# 스크립트가 읽는 파일에는 존재하지 않는다.
check_no_auth_emulator() {
  [ -n "${FIREBASE_AUTH_EMULATOR_HOST:-}" ] && return 1
  # .env 는 uvicorn 이 읽으므로 셸 환경에 없어도 백엔드에는 켜진다.
  grep -qsE '^[[:space:]]*FIREBASE_AUTH_EMULATOR_HOST[[:space:]]*=[[:space:]]*[^[:space:]]'     "$SCRIPT_DIR/backend/.env" && return 1
  return 0
}
if ! check_no_auth_emulator; then
  echo "중단: FIREBASE_AUTH_EMULATOR_HOST 가 설정돼 있습니다." >&2
  echo "" >&2
  echo "에뮬레이터 모드에서는 ID 토큰 서명 검증이 생략됩니다. 이 스크립트는" >&2
  echo "백엔드를 cloudflared 로 인터넷에 공개하므로, 그대로 띄우면 누구나" >&2
  echo "토큰을 위조해 임의 사용자로 로그인할 수 있습니다." >&2
  echo "" >&2
  echo "환경변수에서 지우고, backend/.env 에 있으면 그 줄도 지운 뒤 다시" >&2
  echo "실행하세요. 로컬 개발용 에뮬레이터는 ./dev.sh 쪽에서 켭니다." >&2
  exit 1
fi


GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo -e "${CYAN}   PFP — 외부 공개 배포 시작${NC}"
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo ""

# ── 1. 프론트엔드 빌드 ────────────────────────────────────────────────────────
echo -e "${YELLOW}[1/3] 프론트엔드 빌드 중…${NC}"
cd "$SCRIPT_DIR/frontend"
npm run build
cd "$SCRIPT_DIR"
echo -e "${GREEN}      빌드 완료 → frontend/dist/${NC}"
echo ""

# ── 2. aiofiles 확인 (FastAPI 정적 파일 의존성) ────────────────────────────────
if ! "$SCRIPT_DIR/venv/bin/python" -c "import aiofiles" 2>/dev/null; then
    echo -e "${YELLOW}      aiofiles 설치 중…${NC}"
    "$SCRIPT_DIR/venv/bin/pip" install aiofiles -q
fi

# ── 3. cloudflared 확인 및 설치 ───────────────────────────────────────────────
echo -e "${YELLOW}[2/3] cloudflared 확인 중…${NC}"
if ! command -v cloudflared &>/dev/null; then
    echo "      cloudflared 미설치 → Homebrew로 설치 시도…"
    if command -v brew &>/dev/null; then
        brew install cloudflared
    else
        echo -e "      Homebrew 없음. 수동 설치:"
        echo -e "      https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        echo ""
        echo -e "      또는 ngrok 대신 사용:"
        echo -e "      npx ngrok http 8000"
        echo ""
        # ngrok 폴백
        USE_NGROK=1
    fi
fi
echo ""

# ── 4. 백엔드 시작 ─────────────────────────────────────────────────────────────
echo -e "${YELLOW}[3/3] 백엔드 시작 (포트 8000)…${NC}"

# 기존 프로세스 종료
pkill -f "uvicorn backend.main:app" 2>/dev/null || true
sleep 1

# 로그 파일
LOG="$SCRIPT_DIR/pfp_server.log"

# 백엔드 백그라운드 실행.
# "venv/bin/python -m uvicorn"로 부른다 — venv/bin/uvicorn 의 셔뱅(#!)이 이
# venv 가 예전에 있던 경로(다른 디렉터리)를 그대로 가리키고 있어 옮겨온 뒤로는
# 깨져 있다. venv/bin/python 자체는 정상이라 -m 으로 불러야 확실히 동작한다.
nohup "$SCRIPT_DIR/venv/bin/python" -m uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    > "$LOG" 2>&1 &
BACKEND_PID=$!
echo "      백엔드 PID: $BACKEND_PID (로그: pfp_server.log)"

# 서버 준비 대기
echo "      서버 시작 대기 중…"
for i in {1..15}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "${GREEN}      백엔드 준비 완료${NC}"
        break
    fi
    sleep 1
done
echo ""

# ── 5. 터널 시작 ──────────────────────────────────────────────────────────────
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo -e "${CYAN}   외부 접속 URL 생성 중… (잠시 대기)${NC}"
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo ""

if [ "${USE_NGROK:-0}" = "1" ]; then
    echo -e "${YELLOW}ngrok으로 터널 시작…${NC}"
    echo "(터널 URL은 아래에 표시됩니다. Ctrl+C로 종료)"
    npx ngrok http 8000
else
    echo -e "${YELLOW}cloudflared로 터널 시작…${NC}"
    echo -e "${GREEN}아래 trycloudflare.com URL로 어디서든 접속 가능합니다!${NC}"
    echo "(Ctrl+C로 종료하면 백엔드도 함께 종료됩니다)"
    echo ""
    # Ctrl+C 시 백엔드도 종료
    trap "echo ''; echo '서버 종료 중…'; kill $BACKEND_PID 2>/dev/null; exit 0" INT TERM
    cloudflared tunnel --url http://localhost:8000
fi
