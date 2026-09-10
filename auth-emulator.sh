#!/usr/bin/env bash
# 인증 에뮬레이터를 띄운다. 병렬 개발 창 전부가 이 하나를 같이 쓴다.
#
#   ./auth-emulator.sh
#
# 왜 필요한가
#   Firebase 프로젝트는 로컬과 운영이 같다. 그냥 두면 로컬에서 만든 계정이
#   실서비스 계정과 같은 공간에 쌓이고, 운영에 이미 있는 이메일로는 가입도
#   안 된다. 창이 다섯이면 그 오염이 다섯 배로 빨라진다.
#   에뮬레이터는 완전히 분리된 인증 저장소라 계정을 마음껏 만들어도 된다.
#
# 왜 창마다 하나씩이 아닌가
#   firebase.json 의 singleProjectMode 로 프로젝트 하나만 받는다. 계정 저장소를
#   공유하는 편이 오히려 낫다 — 한 창에서 시드한 test@gmail.com 을 다섯 창이
#   전부 쓸 수 있다. 포트를 갈라야 하는 것은 백엔드·프론트지 인증이 아니다.
#
# 이 창은 에뮬레이터 전용으로 남겨 둔다. Ctrl+C 로 내리면 다섯 창의 로그인이
# 동시에 끊긴다.
#
# 데이터는 종료 시 사라진다. 계정을 남기려면 시드를 다시 돌리면 된다:
#   ./dev.sh 와 같은 방식으로 FIREBASE_AUTH_EMULATOR_HOST 를 준 뒤
#   python -m backend.scripts.seed_test_user   (seed-test-user.sh 가 해 준다)
set -euo pipefail
cd "$(dirname "$0")"

PORT=9099

# 이미 떠 있으면 두 번 띄우지 않는다. 두 번째는 포트 충돌로 죽으면서 첫 번째를
# 건드리진 않지만, 창을 잘못 닫아 멀쩡한 에뮬레이터를 내리는 사고가 난다.
if command -v netstat >/dev/null 2>&1 &&
   netstat -ano -p tcp 2>/dev/null | grep -qE "[:.]$PORT[[:space:]].*(LISTENING|LISTEN)"; then
  echo "이미 $PORT 에 떠 있습니다. 이 창은 닫아도 됩니다."
  echo "각 개발 창에서:  ./dev.sh --slot N --auth-emulator"
  exit 0
fi

echo "▸ 인증 에뮬레이터 시작 (포트 $PORT · UI http://127.0.0.1:4000)"
echo "  이 창은 켜 둡니다. 내리면 다섯 창의 로그인이 함께 끊깁니다."
echo ""
echo "  각 개발 창에서:  ./dev.sh --slot N --auth-emulator"
echo "  계정 시드:       ./seed-test-user.sh"
echo ""

# --project 를 명시한다. .firebaserc 가 있어도 로그인하지 않은 상태에서는
# 프로젝트를 못 고르고 멈추는 경우가 있다. 에뮬레이터는 인증이 필요 없다.
exec npx -y firebase-tools@15 emulators:start \
  --only auth \
  --project personalfinancialplatform
