#!/usr/bin/env bash
# 인증 에뮬레이터에 고정 테스트 계정을 만든다.
#
#   ./seed-test-user.sh
#
# 에뮬레이터의 인증 저장소는 운영과 완전히 별개라, 켜고 나면 계정이 하나도
# 없다. 한 번 시드하면 다섯 창이 전부 그 계정으로 로그인한다 (저장소를
# 공유하므로 창마다 돌릴 필요는 없다).
#
# 에뮬레이터를 내렸다 다시 띄우면 데이터가 사라지므로 다시 돌린다.
set -euo pipefail
cd "$(dirname "$0")"

if   [ -x ./venv/bin/python ];         then PY=./venv/bin/python
elif [ -x ./venv/Scripts/python.exe ]; then PY=./venv/Scripts/python.exe
else echo "venv 가 없습니다 (./setup.sh 를 먼저 돌리세요)"; exit 1
fi

PORT=9099
if ! (command -v netstat >/dev/null 2>&1 &&
      netstat -ano -p tcp 2>/dev/null | grep -qE "[:.]$PORT[[:space:]].*(LISTENING|LISTEN)"); then
  echo "인증 에뮬레이터가 $PORT 에 떠 있지 않습니다. 먼저 ./auth-emulator.sh 를 돌리세요."
  exit 1
fi

# 변수는 이 호출에만 준다. 파일에 남기지 않는 이유는 dev.sh 주석 참고.
echo "▸ 에뮬레이터에 test@gmail.com 시드"
FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:$PORT "$PY" -m backend.scripts.seed_test_user
