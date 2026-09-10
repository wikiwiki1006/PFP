"""
backend/tests/helpers/concurrency.py
────────────────────────────────────
동시 요청 경쟁을 **결정적으로** 재현하는 도구.

read-modify-write 경쟁은 그냥 두 번 부르면 재현되지 않는다. 창이 수 마이크로초라
대개 순차로 지나가고, 어쩌다 한 번 터지는 테스트는 CI 에서 무작위로 빨개져
아무도 믿지 않게 된다.
"""
from __future__ import annotations

import threading
import time


def delay_first_call(target, attr: str, seconds: float = 0.6):
    """`target.attr` 을 감싸 **첫 호출자만** 반환 직전에 잠시 멈추게 한다.

    `(real, wrapper)` 를 돌려준다. `monkeypatch.setattr(target, attr, wrapper)`
    로 끼우고, 끝나면 `real` 로 되돌린다.

    ## 왜 배리어가 아니라 한쪽만 재우는가

    핸들러 **안쪽**에 배리어를 박으면 안 된다. 락이 걸린 뒤(= 고쳐진 뒤)에는
    두 번째 스레드가 락에서 막혀 배리어 지점에 영영 오지 못한다. 그러면 정상
    동작하는 코드에서 테스트가 거꾸로 깨지고, 고친 사람은 자기 수정이 뭔가를
    부쉈다고 읽는다.

    한쪽만 재우면 락이 있든 없든 같은 코드로 양쪽을 잰다:

      락 없음: A 가 읽고 잠든 사이 B 가 같은 옛 값을 읽는다 → 경쟁 재현
      락 있음: B 는 A 가 락을 놓을 때까지 핸들러에 들어오지도 못한다 →
               B 의 읽기는 A 가 쓴 뒤라 항상 최신값
    """
    real = getattr(target, attr)
    seen = {"n": 0}
    mutex = threading.Lock()

    def wrapper(*args, **kwargs):
        out = real(*args, **kwargs)
        with mutex:
            seen["n"] += 1
            first = seen["n"] == 1
        if first:
            time.sleep(seconds)
        return out

    return real, wrapper


def run_concurrently(calls, timeout: float = 30.0):
    """호출들을 배리어로 모아 동시에 출발시킨다.

    각 호출마다 `("ok", 반환값)` 또는 `("err", 예외)` 를 순서대로 담아 돌려준다.
    예외를 삼키지 않고 그대로 넘기는 이유는, HTTPException(409 등)이 결과의
    일부이기 때문이다 — 어느 쪽이 409 를 받았는지가 곧 검증 대상이다.

    스레드마다 별도 커넥션을 잡는다는 점이 중요하다. advisory lock 은 세션
    (커넥션) 단위라, 두 호출이 같은 커넥션을 쓰면 락이 의미를 잃어 재현이
    되지 않는다.
    """
    gate = threading.Barrier(len(calls))
    out: list = [None] * len(calls)

    def runner(i, fn):
        gate.wait()
        try:
            out[i] = ("ok", fn())
        except Exception as e:            # HTTPException 포함 — 결과의 일부다
            out[i] = ("err", e)

    threads = [threading.Thread(target=runner, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)

    stuck = [i for i, t in enumerate(threads) if t.is_alive()]
    assert not stuck, (
        f"call(s) {stuck} did not finish within {timeout}s -- lock deadlock? "
        "(핸들러가 끝나지 않았다.)"
    )
    return out
