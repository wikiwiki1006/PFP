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
from pathlib import Path
from typing import Optional

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent.parent / "pfp" / "data"
_DB_FILE  = _DATA_DIR / "holdings.json"
_LOG_FILE = _DATA_DIR / "trade_log.json"


# ── Holdings ───────────────────────────────────────────────────────────────────

def get_holdings(user_id: str = "default") -> dict:
    """{ ticker: {q, avg, sector} } 반환."""
    if is_available():
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT ticker, qty, avg_cost, sector "
                        "FROM holdings WHERE user_id=%s",
                        (user_id,),
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
):
    """종목 upsert. DB 미연결 또는 오류 시 로깅 후 반환."""
    if not is_available():
        logger.error(f"DB 미연결 — {ticker} 저장 실패")
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO holdings(user_id, ticker, qty, avg_cost, sector, updated_at)
                       VALUES(%s,%s,%s,%s,%s,NOW())
                       ON CONFLICT(user_id, ticker) DO UPDATE
                       SET qty=EXCLUDED.qty, avg_cost=EXCLUDED.avg_cost,
                           sector=EXCLUDED.sector, updated_at=NOW()""",
                    (user_id, ticker, float(qty), float(avg_cost), sector or "Other"),
                )
    except Exception as e:
        logger.error(f"DB save_holding({ticker}) 실패: {e}")


def delete_holding(ticker: str, user_id: str = "default"):
    if not is_available():
        logger.error(f"DB 미연결 — {ticker} 삭제 실패")
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM holdings WHERE user_id=%s AND ticker=%s",
                    (user_id, ticker),
                )
                cur.execute(
                    "DELETE FROM trade_log WHERE user_id=%s AND ticker=%s",
                    (user_id, ticker),
                )
    except Exception as e:
        logger.error(f"DB delete_holding({ticker}) 실패: {e}")


# ── Trade Log ──────────────────────────────────────────────────────────────────

def get_trade_log(user_id: str = "default") -> list[dict]:
    """[{id, date, ticker, type, q, price, memo}, ...] 반환."""
    if is_available():
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id, trade_date, ticker, trade_type, qty, price, memo
                           FROM trade_log WHERE user_id=%s
                           ORDER BY trade_date ASC, id ASC""",
                        (user_id,),
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


def update_trade_by_id(trade_id: int, record: dict, user_id: str = "default") -> bool:
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
                       WHERE id=%s AND user_id=%s""",
                    (
                        record.get("date"),
                        record.get("ticker"),
                        record.get("type"),
                        float(record.get("q", 0)),
                        record.get("price"),
                        record.get("memo"),
                        trade_id,
                        user_id,
                    ),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"DB update_trade_by_id({trade_id}) 실패: {e}")
        return False


def delete_trade_by_id(trade_id: int, user_id: str = "default") -> bool:
    """거래 내역 삭제. 성공 시 True."""
    if not is_available():
        logger.error("DB 미연결 — 거래 이력 삭제 실패")
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM trade_log WHERE id=%s AND user_id=%s",
                    (trade_id, user_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"DB delete_trade_by_id({trade_id}) 실패: {e}")
        return False


def add_trade(record: dict, user_id: str = "default"):
    """거래 1건 추가. DB 미연결 또는 오류 시 로깅 후 반환."""
    if not is_available():
        logger.error("DB 미연결 — 거래 이력 저장 실패")
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO trade_log
                           (user_id, trade_date, ticker, trade_type, qty, price, memo)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        user_id,
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
