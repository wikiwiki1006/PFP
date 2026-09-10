"""
backend/db/portfolio_repo.py
──────────────────────────────
holdings / trade_log 사용자별 CRUD.
쓰기: DB 전용 (파일 쓰기 없음 — 클라우드 배포 대응).
읽기: DB 우선, DB 미연결 시 pfp/data/*.json 에서 읽어 로컬 개발 지원.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent.parent / "pfp" / "data"
_DB_FILE  = _DATA_DIR / "holdings.json"
_LOG_FILE = _DATA_DIR / "trade_log.json"


# ── Holdings ───────────────────────────────────────────────────────────────────

def get_holdings(user_id: str = "default", market: str = "US") -> dict:
    """{ ticker: {q, avg, sector} } 반환. 해당 시장 보유분만.

    market 을 안 주면 미국이다. 프론트가 시장을 보내지 않는 옛 요청도
    기존과 똑같이 동작하게 하기 위한 기본값이다."""
    if is_available():
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT ticker, qty, avg_cost, sector "
                        "FROM holdings WHERE user_id=%s AND market=%s",
                        (user_id, market),
                    )
                    rows = cur.fetchall()
            return {r[0]: {"q": r[1], "avg": r[2], "sector": r[3]} for r in rows}
        except Exception as e:
            logger.warning(f"DB get_holdings 실패, 파일 폴백: {e}")

    # 로컬 개발용 읽기 전용 폴백
    if not _DB_FILE.exists():
        return {}
    raw = json.loads(_DB_FILE.read_text())
    return raw.get("my_holdings", raw)


def save_holding(
    ticker: str,
    qty: float,
    avg_cost: float,
    sector: str = "Other",
    user_id: str = "default",
    market: str = "US",
):
    """종목 upsert. DB 미연결 또는 오류 시 로깅 후 반환."""
    if not is_available():
        logger.error(f"DB 미연결 — {ticker} 저장 실패")
        return
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
    DB 미연결이면 아무것도 하지 않는다 (단일 프로세스 폴백).
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
        # 락을 못 잡아도 요청 자체는 처리한다 (가용성 우선). 경쟁 위험만 남는다.
        logger.warning(f"user_write_lock({user_id}) 획득 실패 — 락 없이 진행: {e}")
        if conn is not None:
            try:
                import backend.db as _db
                _db._pool.putconn(conn)     # type: ignore[union-attr]
            except Exception:
                pass
            conn = None

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
    """
    if not is_available():
        logger.error(f"DB 미연결 — {ticker} 섹터 갱신 실패")
        return False
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
    """보유 종목 삭제. with_trades=True 일 때만 거래 이력도 함께 삭제."""
    if not is_available():
        logger.error(f"DB 미연결 — {ticker} 삭제 실패")
        return
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
    """[{id, date, ticker, type, q, price, memo}, ...] 반환."""
    if is_available():
        try:
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
        except Exception as e:
            logger.warning(f"DB get_trade_log 실패, 파일 폴백: {e}")

    # 로컬 개발용 읽기 전용 폴백
    if not _LOG_FILE.exists():
        return []
    return json.loads(_LOG_FILE.read_text())


def update_trade_by_id(trade_id: int, record: dict, user_id: str = "default",
                       market: str = "US") -> bool:
    """거래 내역 수정. 성공 시 True."""
    if not is_available():
        logger.error("DB 미연결 — 거래 이력 수정 실패")
        return False
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
        return False


def delete_trade_by_id(trade_id: int, user_id: str = "default",
                       market: str = "US") -> bool:
    """거래 내역 삭제. 성공 시 True."""
    if not is_available():
        logger.error("DB 미연결 — 거래 이력 삭제 실패")
        return False
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
        return False


def add_trade(record: dict, user_id: str = "default", market: str = "US"):
    """거래 1건 추가. DB 미연결 또는 오류 시 로깅 후 반환."""
    if not is_available():
        logger.error("DB 미연결 — 거래 이력 저장 실패")
        return
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


# ── Users ──────────────────────────────────────────────────────────────────────

def list_users() -> list[dict]:
    if not is_available():
        return [{"id": "default", "name": "Default User", "created_at": None}]
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, email, created_at FROM users ORDER BY created_at"
                )
                rows = cur.fetchall()
        return [
            {"id": r[0], "name": r[1], "email": r[2], "created_at": str(r[3])}
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"DB list_users 실패: {e}")
        return []


def create_user(user_id: str, name: str, email: str = "") -> bool:
    if not is_available():
        logger.error("DB 미연결 — 유저 생성 실패")
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users(id, name, email) VALUES(%s,%s,%s) "
                    "ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name, email=EXCLUDED.email",
                    (user_id, name, email),
                )
        return True
    except Exception as e:
        logger.error(f"DB create_user 실패: {e}")
        return False


def wipe_portfolio(user_id: str, market: str = "US") -> dict:
    """사용자의 한 시장 포트폴리오를 전부 삭제한다.

    '포트폴리오 새로 등록하기' 전용이다. 종목을 하나씩 지우면 그 사이 상태가
    반쯤 남아 잔고 계산이 어긋나므로, 한 트랜잭션에서 통째로 비운다.

    market 으로 범위를 반드시 좁힌다. 미국·한국은 별개의 포트폴리오라
    한쪽을 새로 등록한다고 다른 쪽까지 지워서는 안 된다.
    """
    if not is_available() or not user_id:
        return {"holdings": 0, "trades": 0}
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
