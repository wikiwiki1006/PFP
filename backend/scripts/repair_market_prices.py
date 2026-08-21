"""
backend/scripts/repair_market_prices.py
───────────────────────────────────────
market_prices 테이블에 영구 저장된 **위조 종가**를 제거한다.

배경
  prefetch_tickers 가 `close_df.ffill()` 후 저장하던 버그 때문에,
  미국 주식이 거래하지 않은 날짜(주말·공휴일)와 아직 시세가 도착하지 않은
  거래일에 전일 종가가 복제돼 저장됐다. 이 행들은 실제 종가와 구분이 불가능해
  일변동률을 0% 로 만든다.

  코드 수정만으로는 해결되지 않는다:
    · save_prices_to_db 는 UPSERT 만 하고 DELETE 가 없다
    · get_stale_tickers 는 updated_at 기준이라 오염 행을 "신선"으로 판정한다
  → 이미 저장된 행은 직접 지워야 한다.

실행 순서 (반드시 쓰기 경로 수정 배포 후)
  1) pg_dump -t market_prices  로 백업
  2) python -m backend.scripts.repair_market_prices            # dry-run (기본)
  3) python -m backend.scripts.repair_market_prices --apply

Phase 1  비거래일 행 삭제 — 결정적, 전 기간. 미국 캘린더 종목만 대상.
Phase 2  실제 거래일이지만 yfinance 에 봉이 없는 행 삭제 + 진짜 값 재수집.
         (08-03 같이 '진짜 거래일 + 위조값' 케이스를 잡는 유일한 방법)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import backend.db as bdb
from backend.db import get_conn
from backend.services.market_calendar import is_us_trading_day, uses_us_session_calendar

# Phase 2 로 검증할 범위 (일)
_PHASE2_DAYS = 120
# yfinance 응답이 이보다 적으면 신뢰하지 않고 스킵 (빈 응답으로 인한 대량 삭제 방지)
_MIN_BARS = 20


def _backup(rows: list, tag: str) -> str | None:
    """삭제 대상 행을 CSV 로 보존. 되돌릴 수 없는 DELETE 전에 항상 호출한다."""
    if not rows:
        return None
    out = Path(__file__).parent / f"backup_market_prices_{tag}.csv"
    pd.DataFrame(rows, columns=["ticker", "price_date", "close_price"]).to_csv(out, index=False)
    print(f"       백업 저장: {out} ({len(rows)}행)")
    return str(out)


def _all_tickers() -> list[str]:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT DISTINCT ticker FROM market_prices ORDER BY ticker")
            return [r[0] for r in cur.fetchall()]


def _date_span() -> tuple[date, date]:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT min(price_date), max(price_date) FROM market_prices")
            return cur.fetchone()


def phase1(apply: bool) -> int:
    """미국 캘린더 종목의 비거래일(주말·공휴일) 행 삭제."""
    us_tickers = [t for t in _all_tickers() if uses_us_session_calendar(t)]
    lo, hi = _date_span()
    if not us_tickers or lo is None:
        print("[phase1] 대상 없음")
        return 0

    bad_dates = [
        d.date() for d in pd.date_range(lo, hi, freq="D")
        if not is_us_trading_day(d.date())
    ]
    print(f"[phase1] 미국 캘린더 종목 {len(us_tickers)}개 × 비거래일 {len(bad_dates)}일 검사 "
          f"({lo} ~ {hi})")

    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ticker, price_date, close_price FROM market_prices "
                "WHERE ticker = ANY(%s) AND price_date = ANY(%s)",
                (us_tickers, bad_dates),
            )
            doomed = cur.fetchall()
            n = len(doomed)
            print(f"[phase1] 삭제 대상 {n}행")
            if apply and n:
                _backup(doomed, "phase1")
                cur.execute(
                    "DELETE FROM market_prices "
                    "WHERE ticker = ANY(%s) AND price_date = ANY(%s)",
                    (us_tickers, bad_dates),
                )
                c.commit()
                print(f"[phase1] {n}행 삭제 완료")
    return n


def phase2(apply: bool, tickers: list[str] | None = None) -> int:
    """실제 거래일이지만 yfinance 에 봉이 없는 행 삭제 + 진짜 값 재수집.

    안전장치: yfinance 응답이 비었거나 봉 수가 _MIN_BARS 미만이면 그 티커는
    **건드리지 않는다**. 일시적 다운로드 실패로 실제 이력이 삭제되는 것을 막는다.
    """
    import yfinance as yf
    from backend.db.market_cache import save_prices_to_db

    if tickers is None:
        from backend.services.market_data import ALWAYS_FETCH, SECTOR_ETF_TICKERS
        tickers = sorted(set(ALWAYS_FETCH) | set(SECTOR_ETF_TICKERS))

    since = date.today() - timedelta(days=_PHASE2_DAYS)
    total = 0

    for t in tickers:
        try:
            # 배치가 아닌 단일 티커 다운로드 — 배치의 캘린더 합집합이 애초의 원인이었다
            raw = yf.download(t, period="6mo", progress=False, auto_adjust=True)
            if raw is None or raw.empty:
                print(f"[phase2] {t:10s} 빈 응답 → 스킵 (안전)")
                continue
            close = raw["Close"] if "Close" in raw.columns else raw.iloc[:, 0]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = close.dropna()
            if len(close) < _MIN_BARS:
                print(f"[phase2] {t:10s} 봉 {len(close)}개뿐 → 스킵 (안전)")
                continue

            real_dates = sorted({d.date() for d in close.index if d.date() >= since})
            if not real_dates:
                print(f"[phase2] {t:10s} 기간 내 봉 없음 → 스킵")
                continue

            with get_conn() as c:
                with c.cursor() as cur:
                    cur.execute(
                        "SELECT ticker, price_date, close_price FROM market_prices "
                        "WHERE ticker=%s AND price_date >= %s AND price_date <> ALL(%s)",
                        (t, since, real_dates),
                    )
                    doomed = cur.fetchall()
                    n = len(doomed)
                    if n:
                        print(f"[phase2] {t:10s} 유령 {n}행")
                        if apply:
                            _backup(doomed, f"phase2_{t.replace('^','').replace('=','')}")
                            cur.execute(
                                "DELETE FROM market_prices "
                                "WHERE ticker=%s AND price_date >= %s AND price_date <> ALL(%s)",
                                (t, since, real_dates),
                            )
                            c.commit()
                    total += n

            if apply:
                # 진짜 값 재저장 (24/7 종목의 잘못 복제된 값도 여기서 교정된다)
                save_prices_to_db(close.to_frame(name=t))
        except Exception as e:
            print(f"[phase2] {t:10s} 오류 → 스킵: {e}")
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 삭제 수행 (미지정 시 dry-run)")
    ap.add_argument("--phase", choices=["1", "2", "all"], default="all")
    args = ap.parse_args()

    if not bdb.init_pool():
        print("DB 연결 실패", file=sys.stderr)
        return 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== market_prices 복구 [{mode}] ===\n")

    n1 = phase1(args.apply) if args.phase in ("1", "all") else 0
    print()
    n2 = phase2(args.apply) if args.phase in ("2", "all") else 0

    print(f"\n=== 합계: phase1 {n1}행 + phase2 {n2}행 ===")
    if not args.apply:
        print("dry-run 이었습니다. 실제 삭제하려면 --apply 를 붙이세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
