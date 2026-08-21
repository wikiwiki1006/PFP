"""
services/ticker_universe.py
───────────────────────────
NASDAQ + NYSE 계열 상장 티커 전체를 DB에 적재/갱신한다.

출처는 yfinance 가 아니라 **NASDAQ Trader SymDir** 공개 파일이다:
  https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt   (NASDAQ)
  https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt    (NYSE·AMEX·ARCA·BATS·IEX)
매 거래일 갱신되는 공식 목록이며 rate limit 이 없다. yfinance 로 전 종목을
탐색하는 방식은 요청 수가 수만 건이 되어 차단되므로 쓰지 않는다.

티어 (수집 빈도 결정)
  1 — 실시간 스트리밍 대상: 사용자 보유 종목 + 지수·섹터 ETF + 24시간 자산
  2 — 자주 갱신: S&P500 등 유동성 상위
  3 — 순환 갱신: 나머지 전 종목
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Iterable, Optional

import requests

logger = logging.getLogger(__name__)

_NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_URL  = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# otherlisted.txt 의 Exchange 코드
_EXCHANGE_MAP = {
    "N": "NYSE",
    "A": "NYSE MKT",     # 구 AMEX
    "P": "NYSE ARCA",
    "Z": "BATS",
    "V": "IEX",
}
# 기본 수집 대상 거래소 (사용자 요청: NASDAQ + NYSE)
DEFAULT_EXCHANGES = ("NASDAQ", "NYSE", "NYSE MKT", "NYSE ARCA")


def _fetch(url: str) -> str:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.text


def _yahoo_symbol(sym: str) -> str:
    """NASDAQ 표기 → yfinance 심볼.

    우선주·워런트 등은 '.'/'$' 대신 '-' 를 쓴다 (예: BRK.A → BRK-A).
    """
    return sym.strip().upper().replace("$", "-").replace(".", "-")


def fetch_listings(exchanges: Iterable[str] = DEFAULT_EXCHANGES) -> list[dict]:
    """공식 목록을 받아 정규화된 레코드 리스트로 반환."""
    wanted = set(exchanges)
    out: list[dict] = []
    seen: set[str] = set()

    # ── NASDAQ ────────────────────────────────────────────────────────────
    try:
        for r in csv.DictReader(io.StringIO(_fetch(_NASDAQ_URL)), delimiter="|"):
            sym = (r.get("Symbol") or "").strip()
            # 파일 끝의 "File Creation Time" 행 제외
            if not sym or "File Creation Time" in sym:
                continue
            if r.get("Test Issue") == "Y":
                continue
            if "NASDAQ" not in wanted:
                continue
            y = _yahoo_symbol(sym)
            if y in seen:
                continue
            seen.add(y)
            out.append({
                "ticker":   y,
                "name":     (r.get("Security Name") or "").strip()[:300],
                "exchange": "NASDAQ",
                "is_etf":   r.get("ETF") == "Y",
            })
    except Exception as e:
        logger.warning(f"nasdaqlisted.txt 수집 실패: {e}")

    # ── NYSE / AMEX / ARCA / BATS / IEX ───────────────────────────────────
    try:
        for r in csv.DictReader(io.StringIO(_fetch(_OTHER_URL)), delimiter="|"):
            sym = (r.get("ACT Symbol") or "").strip()
            if not sym or "File Creation Time" in sym:
                continue
            if r.get("Test Issue") == "Y":
                continue
            exch = _EXCHANGE_MAP.get((r.get("Exchange") or "").strip())
            if not exch or exch not in wanted:
                continue
            y = _yahoo_symbol(sym)
            if y in seen:
                continue
            seen.add(y)
            out.append({
                "ticker":   y,
                "name":     (r.get("Security Name") or "").strip()[:300],
                "exchange": exch,
                "is_etf":   r.get("ETF") == "Y",
            })
    except Exception as e:
        logger.warning(f"otherlisted.txt 수집 실패: {e}")

    return out


def sync_universe(exchanges: Iterable[str] = DEFAULT_EXCHANGES) -> dict:
    """공식 목록 → ticker_universe upsert. 목록에서 사라진 티커는 active=FALSE.

    반환: {"fetched": n, "upserted": n, "deactivated": n}
    """
    from backend.db import get_conn, is_available
    if not is_available():
        return {"fetched": 0, "upserted": 0, "deactivated": 0, "error": "DB 미연결"}

    rows = fetch_listings(exchanges)
    if not rows:
        # 네트워크 실패 시 기존 유니버스를 비활성화하지 않는다 (안전)
        logger.warning("유니버스 수집 결과가 비어 있음 — 갱신 스킵")
        return {"fetched": 0, "upserted": 0, "deactivated": 0, "error": "빈 응답"}

    from psycopg2.extras import execute_values

    tuples = [(r["ticker"], r["name"], r["exchange"], r["is_etf"]) for r in rows]
    tickers = [r["ticker"] for r in rows]

    with get_conn() as conn:
        with conn.cursor() as cur:
            for i in range(0, len(tuples), 5000):
                execute_values(
                    cur,
                    """INSERT INTO ticker_universe(ticker, name, exchange, is_etf)
                       VALUES %s
                       ON CONFLICT(ticker) DO UPDATE SET
                         name       = EXCLUDED.name,
                         exchange   = EXCLUDED.exchange,
                         is_etf     = EXCLUDED.is_etf,
                         active     = TRUE,
                         updated_at = NOW()""",
                    tuples[i:i + 5000],
                )
            # 이번 목록에 없는 기존 티커는 상장폐지/이관 → 비활성화 (행은 보존)
            cur.execute(
                "UPDATE ticker_universe SET active = FALSE, updated_at = NOW() "
                "WHERE active AND NOT (ticker = ANY(%s))",
                (tickers,),
            )
            deactivated = cur.rowcount
        conn.commit()

    logger.info(f"ticker_universe 동기화: {len(rows)}개 upsert, {deactivated}개 비활성화")
    return {"fetched": len(rows), "upserted": len(rows), "deactivated": deactivated}


def set_tiers(tier1: Iterable[str], tier2: Optional[Iterable[str]] = None) -> None:
    """수집 빈도 티어 지정. tier1=실시간 스트리밍, tier2=자주, 나머지=3(순환)."""
    from backend.db import get_conn, is_available
    if not is_available():
        return
    t1 = sorted({t.upper() for t in tier1})
    t2 = sorted({t.upper() for t in (tier2 or [])} - set(t1))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ticker_universe SET tier = 3 WHERE tier <> 3")
            if t2:
                cur.execute("UPDATE ticker_universe SET tier = 2 WHERE ticker = ANY(%s)", (t2,))
            if t1:
                cur.execute("UPDATE ticker_universe SET tier = 1 WHERE ticker = ANY(%s)", (t1,))
        conn.commit()


def get_tier(tier: int, limit: Optional[int] = None) -> list[str]:
    from backend.db import get_conn, is_available
    if not is_available():
        return []
    q = "SELECT ticker FROM ticker_universe WHERE active AND tier = %s ORDER BY ticker"
    if limit:
        q += f" LIMIT {int(limit)}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(q, (tier,))
            return [r[0] for r in cur.fetchall()]


def lookup(ticker: str) -> Optional[dict]:
    """상장 목록에서 티커를 찾는다. 없으면 None.

    시세 조회 전에 이걸 먼저 본다. 존재하지 않는 심볼(타이핑 중간값 등)을
    yfinance 로 확인하려 하면 실패 응답을 받는 데만 수 초가 걸리는데,
    11,000여 종목이 이미 DB 에 있으므로 몇 밀리초 만에 판정할 수 있다.
    """
    from backend.db import get_conn, is_available
    sym = (ticker or "").upper().strip()
    if not sym or not is_available():
        return None
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, name, is_etf FROM ticker_universe "
                "WHERE ticker = %s AND active = TRUE", (sym,),
            )
            r = cur.fetchone()
        return {"ticker": r[0], "name": r[1] or "", "is_etf": bool(r[2])} if r else None
    except Exception as e:
        logger.warning(f"ticker_universe 조회 실패 ({sym}): {e}")
        return None
