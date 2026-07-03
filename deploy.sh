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

# 가상환경 활성화
source "$SCRIPT_DIR/venv/bin/activate"

# 로그 파일
LOG="$SCRIPT_DIR/pfp_server.log"

# 백엔드 백그라운드 실행
nohup uvicorn backend.main:app \
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
