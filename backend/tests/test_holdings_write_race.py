"""
동시 쓰기 경쟁 재현 — PUT/POST /holdings/{ticker} 에 user_write_lock 이 없다.

`routers/portfolio.py` 의 DELETE(:201)·trades(:244,:373,:425)·setup(:1010) 은
전부 `with user_write_lock(uid):` 로 감싸는데 PUT(:131)·POST(:165) 두 곳만
빠져 있다. 둘 다 read-modify-write 다:

- PUT  : `get_holdings` 로 old_q 를 읽고 delta 를 계산해 DEPOSIT 을 기록한 뒤
         `save_holding` 으로 **절대값**을 쓴다. 동시 2건이 같은 old_q 를 읽으면
         DEPOSIT 이 두 번 남는데 수량은 한 번만 오른다 → 원장과 잔액이 갈라진다.
- POST : `if ticker in holdings: 409` 가 TOCTOU 다. 동시 2건이 모두 통과하면
         `save_holding` 이 `ON CONFLICT DO UPDATE` 라 **에러조차 안 난다.**
         CASH 면 DEPOSIT 만 두 번 남는다.

`portfolio_repo.user_write_lock` 의 docstring 이 이 사고를 이미 적어놨다 —
"제출 버튼 더블클릭만으로 재현".

## 왜 이 파일만 실DB 에 붙는가

나머지 테스트는 전부 모킹이다. 그런데 락은 PostgreSQL advisory lock 이라
모킹하면 검증이 성립하지 않는다 — 가짜 락은 언제나 성공하므로 락이 아예
없어도 테스트가 통과한다. 창마다 전용 DB(`pfp_test`)를 만든 것이 바로 이걸
가능하게 하려는 것이다.

같은 이유로 DB 미연결은 skip 이 아니라 **fail** 이다. `user_write_lock` 은
`if not is_available(): yield; return` 이라 DB 가 없으면 락 없이 그냥 통과한다.
그 상태로 초록불이 뜨면 "락이 동작한다" 가 아니라 "락을 재보지도 못했다" 는
뜻인데, skip 은 CI 에서 조용히 사라져 그 구분이 안 남는다.
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

import backend.db as db
from backend.db.portfolio_repo import save_holding
from backend.models.portfolio import HoldingItem, UpdateHoldingRequest
from backend.routers import portfolio as portfolio_router

MARKET = "US"

# 이 테스트는 자기 uid 의 행만 지우지만, 붙은 곳이 어디인지부터 확인한다.
# Neon 에는 실사용자 데이터가 있고(users 12 · holdings 30 · reports 46)
# 역할 창이 붙을 곳이 아니다. 원격 호스트면 아무것도 하지 않고 멈춘다.
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", "", None}


# ── 픽스처 ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def live_pool():
    """실제 연결 풀. 붙지 못하면 fail — 이 파일은 모킹으로 대체할 수 없다.

    끝나면 풀을 원래 상태로 되돌린다. 나머지 테스트는 전부 모킹이고 그중
    일부는 `is_available()` 이 False 인 것에 기대어 동작한다 — 열어둔 채
    나가면 이 파일이 뒤따르는 파일들을 통째로 깨뜨린다 (실제로 그랬다:
    test_job_cancel 이 2건 깨졌다).
    """
    previous = db._pool
    if previous is None and not db.init_pool(minconn=2, maxconn=10):
        pytest.fail(
            "DB 에 붙지 못했다. 이 테스트는 실제 advisory lock 을 재는 것이라 "
            "모킹으로 대체할 수 없다. 로컬 도커 postgres(5433)를 띄우고 "
            "backend/.env 의 DB_NAME 이 이 워크트리 전용 DB 인지 확인하라."
        )
    try:
        yield
    finally:
        if previous is None:
            db.close_pool()          # 우리가 연 것만 닫는다
        db._pool = previous


@pytest.fixture(scope="module", autouse=True)
def guard_target(live_pool):
    """붙은 DB 가 로컬 전용 DB 가 맞는지 확인한다. 아니면 즉시 멈춘다."""
    with db.get_conn() as conn:
        # 서버가 스스로 말하는 주소(inet_server_addr)를 보면 안 된다 — 도커 포트
        # 매핑을 거치면 컨테이너 내부 IP(172.17.x.x)가 나와서 로컬인데도 원격으로
        # 읽힌다. 우리가 실제로 다이얼한 클라이언트 쪽 호스트를 본다.
        host = (conn.info.host or "").lower()
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            dbname = cur.fetchone()[0]

    if host not in _ALLOWED_HOSTS:
        pytest.fail(f"원격 DB({host})에 붙었다. 로컬 전용 DB 에서만 돌린다.")
    if dbname == "postgres" or not dbname.startswith("pfp_"):
        pytest.fail(
            f"현재 DB 가 '{dbname}' 다. 'postgres' 는 다섯 창의 원본 템플릿이라 "
            "여기에 쓰면 격리가 무의미해진다. backend/.env 의 DB_NAME 을 "
            "이 워크트리 전용 DB(pfp_test)로 맞춰라."
        )


@pytest.fixture
def uid():
    """테스트마다 새 사용자. 남의 행을 건드리지 않고, 끝나면 자기 행만 지운다.

    holdings·trade_log 가 users(id) 를 FK 로 물고 있어 사용자 행이 먼저 있어야
    한다. 지울 때는 ON DELETE CASCADE 가 딸린 행까지 걷어간다.
    """
    u = f"racetest-{uuid.uuid4().hex[:12]}"
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users(id, name, email) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (u, "race test", f"{u}@example.invalid"),
            )
    yield u
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id=%s", (u,))


# ── 경쟁 창을 결정적으로 벌리는 장치 ───────────────────────────────────────────

def _delay_first_reader(seconds: float = 0.6):
    """`get_holdings` 를 감싸 **첫 호출자만** 읽은 직후 잠시 멈추게 한다.

    두 스레드를 배리어로 동시에 출발시키는 것만으로는 재현이 확률적이다.
    read-modify-write 창이 수 마이크로초라 대개 순차로 지나간다.

    핸들러 **안쪽**에 배리어를 박으면 안 된다. 락이 생긴 뒤(= 고쳐진 뒤)에는
    두 번째 스레드가 락에서 막혀 배리어 지점에 영영 오지 못해, 정상 동작하는
    코드에서 테스트가 거꾸로 깨진다. 한쪽만 재우면 락이 있든 없든 같은 코드로
    양쪽을 잰다:

      락 없음: A 가 읽고 잠든 사이 B 가 같은 옛 값을 읽는다 → 경쟁 재현
      락 있음: B 는 A 가 락을 놓을 때까지 핸들러에 들어오지도 못한다 →
               B 의 읽기는 A 가 쓴 뒤라 항상 최신값
    """
    real = portfolio_router.get_holdings
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


def _run_concurrently(calls):
    """두 호출을 배리어로 모아 동시에 출발시킨다. (결과, 예외) 목록을 돌려준다."""
    gate = threading.Barrier(len(calls))
    out: list = [None] * len(calls)

    def runner(i, fn):
        gate.wait()
        try:
            out[i] = ("ok", fn())
        except Exception as e:            # HTTPException 포함
            out[i] = ("err", e)

    threads = [threading.Thread(target=runner, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "핸들러가 30초 안에 끝나지 않았다 (락 데드락?)"
    return out


def _cash_rows(uid: str):
    """(최종 수량, DEPOSIT 건수, 원장 순변화) — 원장은 부호를 붙여 합산한다."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT qty FROM holdings WHERE user_id=%s AND market=%s AND ticker='CASH'",
                (uid, MARKET),
            )
            row = cur.fetchone()
            qty = float(row[0]) if row else 0.0

            cur.execute(
                "SELECT trade_type, qty FROM trade_log "
                "WHERE user_id=%s AND market=%s AND ticker='CASH'",
                (uid, MARKET),
            )
            trades = cur.fetchall()

    deposits = sum(1 for t, _ in trades if t == "DEPOSIT")
    ledger = sum(float(q) if t == "DEPOSIT" else -float(q) for t, q in trades)
    return qty, deposits, ledger


# ── 재현 ──────────────────────────────────────────────────────────────────────

def test_lock_is_actually_exercised():
    """DB 가 붙어 있어야 락이 실제로 걸린다 — 아니면 아래 두 테스트가 무의미하다.

    `user_write_lock` 은 DB 미연결이면 아무 일도 하지 않고 통과한다. 그래서
    "통과했지만 아무것도 증명 못 한" 상태와 진짜 통과를 구분해 둔다.
    """
    assert db.is_available(), (
        "DB 풀이 없다. user_write_lock 이 락 없이 그냥 통과하므로 "
        "이 파일의 결과는 아무것도 증명하지 못한다."
    )


def test_concurrent_put_records_one_deposit(uid, monkeypatch):
    """PUT 동시 2건: 같은 목표 수량이면 DEPOSIT 은 1건이어야 한다.

    현금 100 에서 두 요청이 모두 150 으로 맞춘다.
    - 직렬화되면 A 가 +50 을 기록하고, B 는 이미 150 이라 delta 가 0 → 기록 없음.
    - 직렬화되지 않으면 둘 다 old=100 을 읽어 +50 을 두 번 기록한다.
      수량은 150 인데 원장은 +100 이라, 거래 이력과 잔액이 갈라진다.
    """
    save_holding("CASH", 100.0, 1.0, "Cash", uid, market=MARKET)

    real, wrapper = _delay_first_reader()
    monkeypatch.setattr(portfolio_router, "get_holdings", wrapper)

    def put():
        return portfolio_router.update_holding(
            "CASH",
            UpdateHoldingRequest(q=150.0, avg=1.0, date="2026-01-02"),
            _auth={"uid": uid},
            market=MARKET,
        )

    results = _run_concurrently([put, put])
    monkeypatch.setattr(portfolio_router, "get_holdings", real)

    errors = [r for kind, r in results if kind == "err"]
    assert not errors, f"PUT 이 예외로 끝났다: {errors}"

    qty, deposits, ledger = _cash_rows(uid)

    assert qty == pytest.approx(150.0), f"최종 수량이 150 이어야 하는데 {qty}"
    assert deposits == 1, (
        f"DEPOSIT 이 {deposits}건이다 (1건이어야 함). 두 요청이 같은 old_q 를 읽어 "
        "각자 +50 을 기록했다 — routers/portfolio.py 의 update_holding 에 "
        "user_write_lock 이 빠져 있다."
    )
    assert ledger == pytest.approx(qty - 100.0), (
        f"거래 이력과 잔액이 갈라졌다: 원장 순변화 {ledger}, 실제 변화 {qty - 100.0}"
    )


def test_concurrent_post_creates_holding_once(uid, monkeypatch):
    """POST 동시 2건: 하나만 성공하고 나머지는 409 여야 한다.

    `if ticker in holdings: 409` 검사와 `save_holding` 사이가 벌어져 있고,
    `save_holding` 은 `ON CONFLICT DO UPDATE` 라 중복 생성이 에러도 내지 않는다.
    현금이면 DEPOSIT 만 두 번 남아 없던 돈이 원장에 생긴다.
    """
    real, wrapper = _delay_first_reader()
    monkeypatch.setattr(portfolio_router, "get_holdings", wrapper)

    def post():
        return portfolio_router.add_holding(
            "CASH",
            HoldingItem(q=100.0, avg=1.0, sector="Cash", date="2026-01-02"),
            _auth={"uid": uid},
            market=MARKET,
        )

    results = _run_concurrently([post, post])
    monkeypatch.setattr(portfolio_router, "get_holdings", real)

    ok = [r for kind, r in results if kind == "ok"]
    conflicts = [r for kind, r in results if kind == "err" and getattr(r, "status_code", None) == 409]
    other = [r for kind, r in results if kind == "err" and getattr(r, "status_code", None) != 409]

    assert not other, f"409 가 아닌 예외가 났다: {other}"

    qty, deposits, ledger = _cash_rows(uid)

    assert len(ok) == 1 and len(conflicts) == 1, (
        f"성공 {len(ok)}건 / 409 {len(conflicts)}건. 동시 생성 2건이 모두 통과했다 — "
        "routers/portfolio.py 의 add_holding 에 user_write_lock 이 빠져 있어 "
        "존재 검사와 저장 사이가 TOCTOU 다."
    )
    assert deposits == 1, f"DEPOSIT 이 {deposits}건이다 (1건이어야 함). 없던 현금이 원장에 생겼다."
    assert ledger == pytest.approx(qty), f"원장 순변화 {ledger} 와 잔액 {qty} 가 다르다"
