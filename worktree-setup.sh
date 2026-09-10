#!/usr/bin/env bash
# 병렬 에이전트용 worktree 를 만들고 바로 돌아가는 상태로 맞춘다.
#
#   ./worktree-setup.sh <역할이름> <슬롯번호>
#   ./worktree-setup.sh db 1        → ../pfp-db, 브랜치 agent/db, 포트 8001/3001
#
# git worktree 는 추적되는 파일만 준다. 이 리포에서 개발에 필요한 것 중
# 추적되지 않는 것이 네 가지 있고, 없으면 백엔드가 아예 기동하지 않는다:
#
#   venv/                 784M  — 복사하면 슬롯 5개에 4GB. 심볼릭 링크로 건다.
#   frontend/node_modules 413M  — 위와 같음.
#   backend/.env                — API 키·DB URL. 없으면 기동 실패.
#   frontend/.env.local         — Firebase 설정. 없으면 로그인 불가.
#
# venv 를 링크해도 되는 이유: 항상 `<venv>/python -m uvicorn` 으로 부르고,
# python -m 은 cwd 를 sys.path 에 넣는다. 따라서 인터프리터는 공유하되
# backend 패키지는 각 worktree 것을 import 한다.
#
# macOS/Linux 와 Windows(Git Bash) 양쪽에서 돈다. 다른 점 두 가지:
#   - venv 인터프리터가 bin/python (posix) vs Scripts/python.exe (windows)
#   - ln -s 가 Windows 에선 MSYS=winsymlinks:nativestrict 없이는 복사본을 만든다
set -euo pipefail
MAIN="$(cd "$(dirname "$0")" && pwd)"

# Windows Git Bash 에서 ln -s 가 진짜 심볼릭 링크를 만들게 한다. 이게 없으면
# 조용히 디렉터리를 복사해 버려서 워크트리마다 784M 이 쌓인다.
export MSYS=winsymlinks:nativestrict

# 이 리포의 venv 인터프리터 상대경로를 고른다.
if   [ -x "$MAIN/venv/bin/python" ];        then PY_REL="venv/bin/python"
elif [ -x "$MAIN/venv/Scripts/python.exe" ]; then PY_REL="venv/Scripts/python.exe"
else echo "venv 를 찾을 수 없습니다: $MAIN/venv (setup.sh 를 먼저 돌리세요)"; exit 1
fi

ROLE="${1:?사용법: ./worktree-setup.sh <역할이름> <슬롯번호>}"
SLOT="${2:?사용법: ./worktree-setup.sh <역할이름> <슬롯번호>}"
case "$SLOT" in ''|*[!0-9]*) echo "슬롯은 정수여야 합니다: $SLOT"; exit 1 ;; esac
[ "$SLOT" = 0 ] && { echo "슬롯 0 은 메인 워킹트리가 씁니다. 1 이상을 쓰세요."; exit 1; }

DEST="$MAIN/../pfp-$ROLE"
BRANCH="agent/$ROLE"

[ -e "$DEST" ] && { echo "이미 있습니다: $DEST"; exit 1; }

echo "▸ worktree 생성  $DEST  (브랜치 $BRANCH)"
git -C "$MAIN" worktree add -b "$BRANCH" "$DEST"
DEST="$(cd "$DEST" && pwd)"

echo "▸ 추적되지 않는 개발 자산 연결"
ln -s "$MAIN/venv"                  "$DEST/venv"
ln -s "$MAIN/frontend/node_modules" "$DEST/frontend/node_modules"
# .env 는 링크가 아니라 복사한다. 에이전트가 키를 바꿔 실험하다 메인의
# 파일을 덮어쓰면 다른 창 넷이 같이 죽는다. 대신 키를 회전하면 전부 고쳐야 한다.
cp "$MAIN/backend/.env" "$DEST/backend/.env"
if [ -f "$MAIN/frontend/.env.local" ]; then
  cp "$MAIN/frontend/.env.local" "$DEST/frontend/.env.local"
else
  # 메인에도 없다. 백엔드는 뜨지만 프론트 로그인이 안 된다 — 조용히 넘어가면
  # 창마다 같은 증상을 따로 디버깅하게 되므로 여기서 말해 둔다.
  echo "  ! frontend/.env.local 이 메인에도 없습니다 — 프론트 로그인 불가 (frontend/.env.example 참고)"
fi

cat > "$DEST/ROLE.md" <<ROLEDOC
# 이 워크트리의 역할: $ROLE

- 브랜치: \`$BRANCH\`
- 포트 슬롯: $SLOT  (백엔드 $((8000+SLOT)) · 프론트 $((3000+SLOT)))
- 실행: \`./dev.sh --slot $SLOT\`
- 파이썬: \`$PY_REL\`

## 규칙
- 이 브랜치에서만 커밋한다. \`main\` 에 직접 커밋하지 않는다.
- 소유하지 않은 경로는 고치지 않는다. 필요하면 통합 세션에 SendMessage 로
  요청한다. 어떤 경로를 소유하는지는 배치표를 따른다.
- 핸드오프 전 \`$PY_REL -m pytest backend/tests -q\` 와
  \`cd frontend && npm run build\` 를 통과시킨다.
- CLAUDE.md 는 통합 담당만 고친다. 다섯 창이 전부 고치면 매번 충돌한다.
ROLEDOC

echo "▸ 검증"
[ -x "$DEST/$PY_REL" ] || { echo "  venv 링크 실패 ($DEST/$PY_REL)"; exit 1; }
[ -L "$DEST/venv" ] || echo "  ! venv 가 심볼릭 링크가 아니라 복사본입니다 (MSYS 설정 확인)"
"$DEST/$PY_REL" -c "import sys; print('  python', sys.version.split()[0])"
echo "  브랜치 $(git -C "$DEST" rev-parse --abbrev-ref HEAD)"
echo
echo "완료. 새 Claude Code 창에서:  cd $DEST"
