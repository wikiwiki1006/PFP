"""backend 패키지.

여기 있는 것은 콘솔 설정 하나뿐이다. 이 패키지의 무엇이든 import 되는
프로세스라면 로그가 인코딩 때문에 사라지지 않아야 하기 때문이다.
"""
import sys

# ── 로그가 인코딩 때문에 사라지지 않게 한다 ──────────────────────────────────
#
# Windows 콘솔은 cp949 다. 로그 문자열에 이 코드페이지에 없는 문자가 하나라도
# 있으면 (em dash `—`, 화살표 `→`, 불릿 `·` 등) StreamHandler.emit 이
# UnicodeEncodeError 를 내고 **그 로그 레코드가 통째로 버려진다.** 콘솔에는
# "--- Logging error ---" 만 남고, 파일 핸들러라면 그 줄이 흔적 없이 빠진다.
#
# 이 리포의 로그 메시지는 한국어이고 `—` 를 자주 쓴다. 즉 실패를 알리려고
# 넣은 로그가 정확히 그 이유로 사라진다 — §1.3 이 자기 자신에게 걸린 경우다.
#
# 처음에는 main.py 에 뒀는데, 그러면 main 을 import 하지 않는 경로가 전부
# 빠진다: pytest, backend/scripts/*, 진단용 프로브. 그쪽에서 실제로 로그가
# 계속 소실됐다. 패키지 __init__ 이 그 전부를 덮는 유일한 지점이다.
#
# 두 겹으로 막는다. UTF-8 로 바꾸고, 그래도 못 쓰는 문자가 나오면 버리는 대신
# 이스케이프한다. 운영(Cloud Run)은 이미 UTF-8 이라 아무것도 바뀌지 않는다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
