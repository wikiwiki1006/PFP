"""
backend/db/portfolio_repo.py
──────────────────────────────
holdings / trade_log 사용자별 CRUD. 읽기·쓰기 모두 DB 전용이다.

예전에는 읽기에 파일 폴백이 있었다 — DB 가 없거나 조회가 실패하면
`pfp/data/holdings.json` · `trade_log.json` 을 읽어 돌려줬다. 인증이 붙기 전,
사용자가 한 명이던 시절의 로컬 개발 편의다. 제거한 이유:

  1. 그 파일에는 `user_id` 도 `market` 도 없다. 폴백이 도는 순간 **모든
     사용자가 같은 파일 하나를 자기 포트폴리오로 받고** 미국·한국 구분도
     사라진다. CLAUDE.md §1.1 과 §1.2 를 동시에 어긴다.
  2. 파일이 없을 때 돌려주던 빈 값이 더 위험하다. 보유·거래 변경은 전부
     read-modify-write 다 (`user_write_lock` 주석 참고) — 읽어서 계산한 뒤
     절대값으로 덮어쓴다. 조회가 일시적으로 실패해 `{}` 가 돌아오면 그 다음
     쓰기가 **보유를 지운다.** 커넥션 단위 오류는 다음 커넥션에서 회복되므로
     읽기만 실패하고 쓰기는 성공하는 조합이 실제로 가능하다.
  3. 실패와 "보유 없음" 이 구분되지 않는다(§1.3). 화면은 빈 포트폴리오를
     정상으로 그리므로 사용자에게는 데이터가 사라진 것으로 보인다.
  4. 원본이 없다. `pfp/data/` 는 리포에 존재하지 않는다. 남은 건 `.gitignore`
     의 경로 두 줄뿐인데 그게 오히려 위험하다 — 누가 그 파일을 만들면 잠복이
     그대로 유출로 바뀐다.

그래서 DB 를 못 읽으면 빈 값 대신 예외를 올린다. `get_conn()` 이 이미 3회
재시도와 풀 재초기화를 하므로, 여기까지 올라온 예외는 그 사다리가 전부
실패했다는 뜻이다. 조용히 비우는 것보다 500 이 정직하다.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)


# ── Holdings ───────────────────────────────────────────────────────────────────

def get_holdings(user_id: str = "default", market: str = "US") -> dict:
    """{ ticker: {q, avg, sector} } 반환. 해당 시장 보유분만.

    market 을 안 주면 미국이다. 프론트가 시장을 보내지 않는 옛 요청도
    기존과 똑같이 동작하게 하기 위한 기본값이다.

    DB 를 못 읽으면 예외를 올린다 (모듈 docstring 참고). 빈 dict 는 "보유
    없음" 이라는 뜻으로만 쓴다."""
    if not is_available():
        raise RuntimeError("DB 미연결 — 보유 종목을 읽을 수 없다")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, qty, avg_cost, sector "
                "FROM holdings WHERE user_id=%s AND market=%s",
                (user_id, market),
            )
            rows = cur.fetchall()
    return {r[0]: {"q": r[1], "avg": r[2], "sector": r[3]} for r in rows}


def save_holding(
    ticker: str,
    qty: float,
    avg_cost: float,
    sector: str = "Other",
    user_id: str = "default",
    market: str = "US",
):
    """종목 upsert. DB 를 못 쓰면 예외를 올린다.

    예전에는 DB 미연결일 때 로그만 남기고 조용히 반환했다. 호출자에게는 저장
    성공과 구별되지 않아서, 사용자는 종목 추가 버튼을 누르고 200 을 받고
    아무것도 저장되지 않았다 (§1.3)."""
    if not is_available():
        raise RuntimeError(f"DB 미연결 — {ticker} 을(를) 저장할 수 없다")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO holdings(user_id, market, ticker, qty, avg_cost, sector, updated_at)
                       VALUES(%s,%s,%s,%s,%s,%s,NOW())
                       ON CONFLICT(user_id, market, ticker) DO UPDATE
                       SET qty=EXCLUDED.qty, avg_cost=EXCLUDED.avg_cost,
                           sector=EXCLUDED.sector, updated_at=NOW()""",
                    (user_id, market, ticker, float(qty), float(avg_cost), sector or "Other"),
                )
    except Exception as e:
        logger.error(f"DB save_holding({ticker}) 실패: {e}")
        raise


@contextmanager
def user_write_lock(user_id: str = "default"):
    """사용자별 쓰기 직렬화 (PostgreSQL advisory lock).

    보유·거래 변경은 전부 read-modify-write 다 (읽어서 계산한 뒤 절대값으로 덮어씀).
    save_holding 은 `SET qty=EXCLUDED.qty` 라 원자적이지 않으므로, 동시에 들어온
    두 매수가 같은 수량을 읽고 같은 값을 써서 한 건이 통째로 사라진다
    (제출 버튼 더블클릭만으로 재현). 핸들러 전체를 사용자 단위로 직렬화한다.

    락은 세션 단위이므로 획득·해제를 같은 커넥션에서 해야 한다.
    DB 미연결이면 아무것도 하지 않는다 — 직렬화할 쓰기 자체가 없다. 이 블록
    안의 쓰기 함수가 전부 그 상태에서 예외를 올리기 때문이다.

    **락을 못 잡으면 요청을 거절한다** (`WriteLockUnavailable` → 503).
    예전에는 경고만 남기고 락 없이 진행했다. 가용성을 택한 판단이었는데,
    락 획득이 실패하는 주된 원인이 풀 고갈이고 풀 고갈은 동시 요청이 몰렸다는
    뜻이다 — 즉 **경쟁이 실제로 터지는 바로 그 순간에만 보호가 사라졌다.**
    잃은 매매는 되돌릴 수 없고 사용자에게 보이지도 않는 반면, 503 은 다시
    누르면 된다.
    """
    if not is_available():
        yield
        return

    key = f"pfp:{user_id}"
    conn = None
    try:
        import backend.db as _db
        conn = _db._pool.getconn()          # type: ignore[union-attr]
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(hashtext(%s))", (key,))
    except Exception as e:
        from backend.db import WriteLockUnavailable
        logger.error(f"user_write_lock({user_id}) 획득 실패, 요청을 거절한다: {e}")
        if conn is not None:
            try:
                import backend.db as _db
                _db._pool.putconn(conn)     # type: ignore[union-attr]
            except Exception:
                pass
            conn = None
        raise WriteLockUnavailable(
            "요청이 몰려 지금 처리할 수 없습니다. 잠시 후 다시 시도해 주세요."
        ) from e

    try:
        yield
    finally:
        if conn is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
            except Exception:
                pass
            try:
                import backend.db as _db
                _db._pool.putconn(conn)     # type: ignore[union-attr]
            except Exception:
                pass


def update_holding_sector(ticker: str, sector: str, user_id: str = "default",
                          market: str = "US") -> bool:
    """섹터만 갱신. 수량·평단은 건드리지 않는다.

    /auto-sector 와 백그라운드 섹터 조회는 yfinance 응답을 수 초간 기다리는데,
    그동안 사용자가 매매하면 save_holding 으로 스냅샷 전체를 되쓰면서 그 매매가
    되돌아간다. 섹터만 UPDATE 하면 동시에 기록된 수량·평단이 보존된다.
    행이 없으면(그사이 삭제됨) False — 삭제된 종목을 되살리지 않는다.

    DB 미연결이면 예외를 올린다. 반면 쿼리가 실패했을 때는 False 를 유지한다 —
    §1.3 이 허용하는 "가용성 때문에 여는" 쪽이고, 판단 근거는 이렇다: 이 함수는
    /auto-sector 와 등록 직후 백그라운드에서 **여러 종목을 훑으며** 호출된다.
    한 종목의 쿼리 실패로 예외를 올리면 나머지 종목의 섹터까지 통째로 못 채운다.
    섹터가 'Other' 로 남는 것은 사용자 데이터를 잃는 게 아니고, 실패는
    logger.error 로 남는다. 두 경로가 갈리는 것을 알고 그렇게 둔 것이다.
    """
    if not is_available():
        raise RuntimeError(f"DB 미연결 — {ticker} 섹터를 갱신할 수 없다")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE holdings SET sector=%s, updated_at=NOW() "
                    "WHERE user_id=%s AND market=%s AND ticker=%s",
                    (sector or "Other", user_id, market, ticker),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"DB update_holding_sector({ticker}) 실패: {e}")
        return False


def delete_holding(ticker: str, user_id: str = "default", with_trades: bool = False,
                   market: str = "US"):
    """보유 종목 삭제. with_trades=True 일 때만 거래 이력도 함께 삭제.

    save_holding 과 같다 — DB 미연결이면 삭제된 척하지 않고 예외를 올린다."""
    if not is_available():
        raise RuntimeError(f"DB 미연결 — {ticker} 을(를) 삭제할 수 없다")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM holdings WHERE user_id=%s AND market=%s AND ticker=%s",
                    (user_id, market, ticker),
                )
                if with_trades:
                    cur.execute(
                        "DELETE FROM trade_log WHERE user_id=%s AND market=%s AND ticker=%s",
                        (user_id, market, ticker),
                    )
    except Exception as e:
        logger.error(f"DB delete_holding({ticker}) 실패: {e}")
        raise


# ── Trade Log ──────────────────────────────────────────────────────────────────

def get_trade_log(user_id: str = "default", market: str = "US") -> list[dict]:
    """[{id, date, ticker, type, q, price, memo}, ...] 반환.

    get_holdings 와 같다 — DB 를 못 읽으면 빈 목록 대신 예외를 올린다.
    거래 이력이 비어 보이면 그 위에서 계산하는 현금 원장·수익률이 전부
    조용히 틀어진다."""
    if not is_available():
        raise RuntimeError("DB 미연결 — 거래 이력을 읽을 수 없다")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, trade_date, ticker, trade_type, qty, price, memo
                   FROM trade_log WHERE user_id=%s AND market=%s
                   ORDER BY trade_date ASC, id ASC""",
                (user_id, market),
            )
            rows = cur.fetchall()
    return [
        {
            "id":     r[0],
            "date":   str(r[1]),
            "ticker": r[2],
            "type":   r[3],
            "q":      r[4],
            "price":  r[5],
            "memo":   r[6],
        }
        for r in rows
    ]


def update_trade_by_id(trade_id: int, record: dict, user_id: str = "default",
                       market: str = "US") -> bool:
    """거래 내역 수정. 성공 시 True, **그런 거래가 없으면** False.

    False 의 뜻을 하나로 좁혔다. 예전에는 DB 미연결·쿼리 실패도 False 였는데,
    호출자(`routers/portfolio.py`)가 그걸 404 "거래 내역 없음" 으로 바꾼다.
    DB 가 흔들리는 동안 사용자는 자기 거래가 사라졌다는 말을 듣고, 새로고침하면
    그대로 있다. 실패는 실패로 올린다 (§1.3)."""
    if not is_available():
        raise RuntimeError("DB 미연결 — 거래 이력을 수정할 수 없다")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE trade_log
                       SET trade_date=%s, ticker=%s, trade_type=%s, qty=%s, price=%s, memo=%s
                       WHERE id=%s AND user_id=%s AND market=%s""",
                    (
                        record.get("date"),
                        record.get("ticker"),
                        record.get("type"),
                        float(record.get("q", 0)),
                        record.get("price"),
                        record.get("memo"),
                        trade_id,
                        user_id,
                        market,
                    ),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"DB update_trade_by_id({trade_id}) 실패: {e}")
        raise


def delete_trade_by_id(trade_id: int, user_id: str = "default",
                       market: str = "US") -> bool:
    """거래 내역 삭제. 성공 시 True, **그런 거래가 없으면** False.

    update_trade_by_id 와 같다 — False 는 "그런 거래가 없다" 만 뜻한다."""
    if not is_available():
        raise RuntimeError("DB 미연결 — 거래 이력을 삭제할 수 없다")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM trade_log WHERE id=%s AND user_id=%s AND market=%s",
                    (trade_id, user_id, market),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"DB delete_trade_by_id({trade_id}) 실패: {e}")
        raise


def add_trade(record: dict, user_id: str = "default", market: str = "US"):
    """거래 1건 추가. DB 를 못 쓰면 예외를 올린다.

    save_holding 과 같은 이유다 — 조용히 반환하면 기록되지 않은 매매가 200 을
    받는다. 그 뒤 현금 원장·보유 재계산이 없는 거래 위에서 돈다."""
    if not is_available():
        raise RuntimeError("DB 미연결 — 거래 이력을 저장할 수 없다")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO trade_log
                           (user_id, market, trade_date, ticker, trade_type, qty, price, memo)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        user_id,
                        market,
                        record["date"],
                        record["ticker"],
                        record["type"],
                        float(record["q"]),
                        record.get("price"),
                        record.get("memo"),
                    ),
                )
    except Exception as e:
        logger.error(f"DB add_trade({record.get('ticker')}) 실패: {e}")
        raise


# 사용자 CRUD 는 `backend/db/users_repo.py` 에 있다. 여기 있던 list_users /
# create_user 는 호출자가 리포 전체에 하나도 없는 화석이라 지웠다. list_users 는
# DB 미연결 시 `{"id": "default", "name": "Default User"}` 를 **지어내서**
# 돌려주기까지 했다 — 없는 사용자를 있는 것처럼 만드는 §1.3 결함이다.


def wipe_portfolio(user_id: str, market: str = "US") -> dict:
    """사용자의 한 시장 포트폴리오를 전부 삭제한다.

    '포트폴리오 새로 등록하기' 전용이다. 종목을 하나씩 지우면 그 사이 상태가
    반쯤 남아 잔고 계산이 어긋나므로, 한 트랜잭션에서 통째로 비운다.

    market 으로 범위를 반드시 좁힌다. 미국·한국은 별개의 포트폴리오라
    한쪽을 새로 등록한다고 다른 쪽까지 지워서는 안 된다.

    예전에는 DB 미연결이나 빈 user_id 에서 `{"holdings": 0, "trades": 0}` 을
    돌려줬다. 로그도 없었다. 호출자에게는 "지울 게 없어서 0건" 과 구별되지
    않으므로, 새로 등록하기가 옛 포트폴리오를 그대로 둔 채 진행된다 (§1.3).
    """
    if not user_id:
        raise ValueError("wipe_portfolio 에 user_id 가 없다")
    if not is_available():
        raise RuntimeError("DB 미연결 — 포트폴리오를 비울 수 없다")
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM trade_log WHERE user_id=%s AND market=%s",
                        (user_id, market))
            trades = cur.rowcount
            cur.execute("DELETE FROM holdings WHERE user_id=%s AND market=%s",
                        (user_id, market))
            holdings = cur.rowcount
        return {"holdings": holdings, "trades": trades}
    except Exception as e:
        logger.error(f"wipe_portfolio({user_id}) 실패: {e}")
        raise
