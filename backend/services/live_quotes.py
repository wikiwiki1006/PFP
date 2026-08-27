"""
services/live_quotes.py
───────────────────────
장중 실시간 시세 수집. 결과는 **market_snapshot** 에만 기록한다.

market_prices(일별 확정 종가)에는 절대 쓰지 않는다 — 장중 부분 가격을 종가로
저장하면 일변동률이 오염된다 (실제로 그 버그가 있었고 save_prices_to_db 에
캘린더 가드를 넣어 구조적으로 막아두었다).

수집 경로
  ① WebSocket 스트리밍 (기본) — Yahoo 실시간 푸시. 체결이 일어날 때만 메시지가
     오므로 폴링과 달리 요청 수가 티커 수에 비례하지 않는다. 이것이 전 종목
     실시간 수집을 가능하게 하는 유일한 경로다.
  ② 배치 폴링 (보조) — 스트리밍이 커버하지 못한 티커를 순환 갱신.

왜 "전 종목 1분 폴링"을 하지 않는가
  실측: yfinance 일괄 다운로드는 약 23 티커/초. 11,483 종목이면 1회에 약 8.3분.
  60초 주기로 강행하면 사이클이 겹치고 Yahoo 차단을 부른다. 그래서 스트리밍을
  1차 경로로 쓰고, 폴링은 순환 백필로만 사용한다.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# 24시간 거래 자산 — 미국 장 마감 후에도 계속 움직이므로 별도 주기로 갱신
ROUND_THE_CLOCK = [
    "BTC-USD", "ETH-USD",
    "CL=F", "GC=F", "SI=F", "NG=F",       # 원유·금·은·천연가스
    "^TNX", "^IRX", "^FVX", "^TYX",       # 미 국채 금리
    "USDKRW=X", "JPYKRW=X", "EURUSD=X", "DX-Y.NYB",
]


def _finite(v) -> Optional[float]:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def save_quotes(quotes: dict, source: str, session: str) -> int:
    """{ticker: {price, change_1d, change_1d_pct, volume, ...}} → market_snapshot upsert."""
    from backend.db import get_conn, is_available
    if not is_available() or not quotes:
        return 0
    from psycopg2.extras import execute_values

    rows = []
    for t, q in quotes.items():
        px = _finite(q.get("price"))
        if px is None or px <= 0:
            continue
        rows.append((
            str(t), px,
            _finite(q.get("change_1d")), _finite(q.get("change_1d_pct")),
            _finite(q.get("volume")), _finite(q.get("day_high")),
            _finite(q.get("day_low")), _finite(q.get("prev_close")),
            session, source,
        ))
    if not rows:
        return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """INSERT INTO market_snapshot
                     (ticker, price, change_1d, change_1d_pct, volume,
                      day_high, day_low, prev_close, session, source, updated_at)
                   VALUES %s
                   ON CONFLICT(ticker) DO UPDATE SET
                     price         = EXCLUDED.price,
                     change_1d     = COALESCE(EXCLUDED.change_1d,     market_snapshot.change_1d),
                     change_1d_pct = COALESCE(EXCLUDED.change_1d_pct, market_snapshot.change_1d_pct),
                     volume        = COALESCE(EXCLUDED.volume,        market_snapshot.volume),
                     day_high      = COALESCE(EXCLUDED.day_high,      market_snapshot.day_high),
                     day_low       = COALESCE(EXCLUDED.day_low,       market_snapshot.day_low),
                     prev_close    = COALESCE(EXCLUDED.prev_close,    market_snapshot.prev_close),
                     session       = EXCLUDED.session,
                     source        = EXCLUDED.source,
                     updated_at    = NOW()""",
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
            )
        conn.commit()
    return len(rows)


# ─────────────────────────────────────────────────────────────────────────────
# ① WebSocket 스트리밍
# ─────────────────────────────────────────────────────────────────────────────

class QuoteStream:
    """yfinance WebSocket 래퍼. 수신 메시지를 모아 주기적으로 DB flush.

    체결이 있을 때만 메시지가 오므로, 거래가 없는 종목은 갱신되지 않는다
    (0% 로 덮어쓰지 않고 직전 값을 유지하는 것이 올바른 동작이다).
    """

    def __init__(self, flush_interval: int = 60):
        self.flush_interval = flush_interval
        self._buf: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._ws = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.received = 0

    def _on_message(self, msg: dict) -> None:
        sym = msg.get("id") or msg.get("symbol")
        if not sym:
            return
        px = _finite(msg.get("price"))
        if px is None:
            return
        pct = _finite(msg.get("change_percent"))
        chg = _finite(msg.get("change"))
        prev = None
        if pct is not None and pct != -100:
            prev = px / (1 + pct / 100.0)
        with self._lock:
            self._buf[str(sym)] = {
                "price":         px,
                "change_1d":     chg,
                "change_1d_pct": pct,
                "volume":        _finite(msg.get("day_volume")),
                "day_high":      _finite(msg.get("day_high")),
                "day_low":       _finite(msg.get("day_low")),
                "prev_close":    prev,
            }
            self.received += 1

    def _flush_loop(self) -> None:
        from backend.services.market_calendar import us_market_status
        while not self._stop.wait(self.flush_interval):
            with self._lock:
                batch, self._buf = self._buf, {}
            if batch:
                try:
                    n = save_quotes(batch, source="ws", session=us_market_status())
                    logger.debug(f"QuoteStream flush: {n}개 티커")
                except Exception as e:
                    logger.warning(f"QuoteStream flush 실패: {e}")

    def start(self, tickers: Iterable[str]) -> None:
        import yfinance as yf
        syms = sorted({str(t).upper() for t in tickers if t})
        if not syms:
            return
        self._ws = yf.WebSocket()
        self._ws.subscribe(syms)
        t = threading.Thread(target=lambda: self._ws.listen(self._on_message), daemon=True)
        t.start()
        self._threads.append(t)
        f = threading.Thread(target=self._flush_loop, daemon=True)
        f.start()
        self._threads.append(f)
        logger.info(f"QuoteStream 시작: {len(syms)}개 구독, flush {self.flush_interval}s")

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# ② 배치 폴링 (스트리밍 보조 / 순환 백필)
# ─────────────────────────────────────────────────────────────────────────────

def poll_quotes(tickers: list[str], batch_size: int = 200, interval: str = "1m") -> dict:
    """yfinance 일괄 다운로드로 현재가·거래량 수집. 반환 {ticker: quote}.

    interval 은 받아올 봉의 간격이다. 기본 1m 은 2일치가 780행이라 단일 티커에도
    1초 안팎이 걸린다. 화면에 값 하나만 채우면 되는 온디맨드 경로는 "5m" 을 주면
    156행으로 줄어 4배 빠르다 — 최신 봉의 시각이 최대 5분 늦을 뿐이다.
    """
    import yfinance as yf
    from backend.db.market_cache import _yf_sem

    out: dict[str, dict] = {}
    for i in range(0, len(tickers), batch_size):
        chunk = tickers[i:i + batch_size]
        try:
            with _yf_sem:
                data = yf.download(
                    chunk, period="2d", interval=interval,
                    progress=False, auto_adjust=True, threads=True,
                )
            if data is None or data.empty:
                continue
            multi = hasattr(data.columns, "levels")
            close = data["Close"] if multi else data[["Close"]].rename(columns={"Close": chunk[0]})
            vol   = data["Volume"] if (multi and "Volume" in data.columns.levels[0]) else None
            for t in chunk:
                if t not in close.columns:
                    continue
                s = close[t].dropna()
                if s.empty:
                    continue
                px = float(s.iloc[-1])
                # 전일 종가: 마지막 관측 '날짜' 이전의 마지막 값
                last_day = s.index[-1].date()
                prior = s[[ts.date() < last_day for ts in s.index]]
                prev = float(prior.iloc[-1]) if not prior.empty else None
                q = {"price": px, "prev_close": prev,
                     "day_high": float(s.max()), "day_low": float(s.min())}
                if prev:
                    q["change_1d"] = px - prev
                    q["change_1d_pct"] = (px / prev - 1) * 100
                if vol is not None and t in vol.columns:
                    v = vol[t].dropna()
                    if not v.empty:
                        q["volume"] = float(v.sum())
                out[t] = q
        except Exception as e:
            logger.warning(f"poll_quotes 배치 실패 ({chunk[:3]}...): {e}")
    return out


def refresh_round_the_clock() -> int:
    """24시간 자산(원유·금·금리·환율·암호화폐) 갱신. 5분 주기로 호출."""
    from backend.services.market_calendar import us_market_status
    q = poll_quotes(ROUND_THE_CLOCK, batch_size=len(ROUND_THE_CLOCK))
    if not q:
        return 0
    status = us_market_status()
    return save_quotes(q, source="poll", session=status if status == "open" else "closed24h")


def refresh_tier1() -> int:
    """티어 1(S&P500 + 보유종목 + 지수·섹터ETF) 전량 갱신. 1분 주기로 호출.

    약 530종목 × 실측 23종목/초 ≈ 23초 → 60초 주기 안에 들어온다.
    (전 종목 11,483개는 1회 8.3분이라 불가능해 온디맨드 방식으로 분리했다.)
    """
    from backend.services.market_calendar import us_market_status
    from backend.services.ticker_universe import get_tier
    tickers = get_tier(1)
    if not tickers:
        return 0
    q = poll_quotes(tickers)
    return save_quotes(q, source="poll", session=us_market_status())


# ─────────────────────────────────────────────────────────────────────────────
# ③ 온디맨드 조회 (검색·보유 종목) — DB 캐시 + 신선도 규칙
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MAX_AGE = 60   # 초. 이보다 오래된 스냅샷은 yfinance 에서 다시 받아온다.


def _read_snapshot(tickers: list[str]) -> dict[str, dict]:
    """market_snapshot 에서 티커별 시세 + 경과 시간(age_sec) 조회."""
    from backend.db import get_conn, is_available
    if not is_available() or not tickers:
        return {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ticker, price, change_1d, change_1d_pct, volume,
                          day_high, day_low, prev_close, session, source,
                          EXTRACT(EPOCH FROM (NOW() - updated_at))
                   FROM market_snapshot WHERE ticker = ANY(%s)""",
                (tickers,),
            )
            cols = ("price", "change_1d", "change_1d_pct", "volume",
                    "day_high", "day_low", "prev_close", "session", "source", "age_sec")
            return {r[0]: dict(zip(cols, r[1:])) for r in cur.fetchall()}


def _is_fresh(snap: dict, max_age: int) -> bool:
    age = snap.get("age_sec")
    return age is not None and age <= max_age


def _tickers_missing_last_close(tickers: list[str]) -> list[str]:
    """마지막 확정 거래일 종가가 DB(market_prices)에 없는 미국 종목 목록.

    장외 시간에는 가격이 움직이지 않으므로 _can_move() 가 재수집을 막는다.
    하지만 그 캐시가 **며칠 전 종가**일 수도 있다 (티어1 이 아닌 종목은 스케줄러가
    갱신하지 않으므로 흔한 상황). 이 경우 '변하지 않는다'와 '최신이다'는 다르다.
    → 확정 종가 보유 여부를 별도로 확인해 누락된 종목만 수집 대상으로 돌린다.
    """
    from backend.db import get_conn, is_available
    from backend.services.market_calendar import (
        last_completed_session, uses_us_session_calendar,
    )
    us = [t for t in tickers if uses_us_session_calendar(t)]
    if not us or not is_available():
        return []
    target = last_completed_session()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ticker, MAX(price_date) FROM market_prices
                   WHERE ticker = ANY(%s) GROUP BY ticker""",
                (us,),
            )
            have = {r[0]: r[1] for r in cur.fetchall()}
    return [t for t in us if have.get(t) is None or have[t] < target]


def backfill_last_close(tickers: list[str]) -> int:
    """확정 종가가 누락된 종목을 yfinance 에서 받아 market_prices + snapshot 에 반영.

    market_prices 저장은 save_prices_to_db 의 캘린더 가드를 통과하므로
    비거래일·미확정 당일 행은 애초에 기록되지 않는다.
    """
    import pandas as pd
    import yfinance as yf
    from backend.db.market_cache import _yf_sem, save_prices_to_db
    from backend.services.market_calendar import last_completed_session

    if not tickers:
        return 0
    try:
        with _yf_sem:
            data = yf.download(
                tickers, period="1mo", progress=False, auto_adjust=True, threads=False,
            )
    except Exception as e:
        logger.warning(f"backfill_last_close 다운로드 실패: {e}")
        return 0
    if data is None or data.empty:
        return 0

    multi = hasattr(data.columns, "levels")
    close = data["Close"] if multi else data[["Close"]].rename(columns={"Close": tickers[0]})
    close = close.dropna(how="all")
    if close.empty:
        return 0

    save_prices_to_db(close)          # 일별 확정 종가 반영

    # 스냅샷도 확정 종가 기준으로 맞춘다 (장외에 보이는 값이 최신 종가가 되도록)
    target = pd.Timestamp(last_completed_session())
    quotes: dict[str, dict] = {}
    for t in close.columns:
        s = close[t].dropna()
        s = s[s.index.normalize() <= target]
        if s.empty:
            continue
        px = float(s.iloc[-1])
        prev = float(s.iloc[-2]) if len(s) >= 2 else None
        q = {"price": px, "prev_close": prev}
        if prev:
            q["change_1d"] = px - prev
            q["change_1d_pct"] = (px / prev - 1) * 100
        quotes[str(t)] = q
    if quotes:
        save_quotes(quotes, source="close", session="closed")
    return len(quotes)


def _can_move(ticker: str) -> bool:
    """지금 이 티커의 가격이 변할 수 있는지.

    · 24시간 자산(암호화폐·환율·선물·해외지수) → 항상 True
    · 미국 주식 → 프리마켓 04:00 ~ 애프터마켓 20:00 ET 사이에만 True
      (정규장 마감 후에도 시간외 거래로 가격이 실제로 움직이므로 16:00 이 아니라 20:00)
    가격이 변할 수 없는 시간대에는 1분 규칙을 적용하지 않는다 — 값이 고정인데
    매 검색마다 yfinance 를 호출하면 요청만 늘고 차단 위험이 커진다.
    """
    from backend.services.market_calendar import (
        is_us_extended_hours, uses_us_session_calendar,
    )
    if not uses_us_session_calendar(ticker):
        return True
    return is_us_extended_hours()


def get_quotes(
    tickers: list[str],
    max_age: int = DEFAULT_MAX_AGE,
    force: bool = False,
    backfill: bool = True,
    interval: str = "1m",
) -> dict[str, dict]:
    """티커 시세 조회 — DB 캐시 우선, 오래됐으면 yfinance 재수집 후 DB 갱신.

    규칙
      · DB 에 없으면            → yfinance 수집 → DB 저장
      · DB 에 있고 max_age 이내 → 그대로 반환 (yfinance 호출 없음)
      · DB 에 있고 오래됐으면   → yfinance 재수집 → DB 갱신
      · 단, 가격이 변할 수 없는 시간대(미국 주식의 심야·주말·공휴일)에는
        오래됐더라도 재수집하지 않는다 — _can_move() 참조.
      · 그 시간대라도 **마지막 확정 종가가 DB 에 없으면** 받아온다.
        '변하지 않는다'와 '최신이다'는 다르기 때문 — 티어1 이 아닌 종목은
        스케줄러가 갱신하지 않으므로 며칠 전 종가가 남아 있을 수 있다.

    backfill=False 를 주면 확정 종가 백필을 건너뛴다. 화면에 현재가 하나만
    띄우면 되는 경로(거래 입력 폼 등)에서 쓴다 — 백필은 yfinance 를 한 번 더
    부르므로 사용자를 그만큼 더 기다리게 한다. 일별 종가는 스케줄러가 채운다.
    """
    from backend.services.market_calendar import us_market_status
    syms = sorted({str(t).upper().strip() for t in tickers if t})
    if not syms:
        return {}

    cached = _read_snapshot(syms)
    status = us_market_status()

    stale: list[str] = []
    for t in syms:
        if force:
            stale.append(t)
            continue
        snap = cached.get(t)
        if snap is None:
            stale.append(t)          # 캐시에 아예 없으면 시간대 무관하게 수집
            continue
        if not _is_fresh(snap, max_age) and _can_move(t):
            stale.append(t)

    if stale:
        fresh = poll_quotes(stale, interval=interval)
        if fresh:
            save_quotes(fresh, source="poll", session=status)
            for t, q in fresh.items():
                q = dict(q)
                q["age_sec"] = 0.0
                q["source"] = "poll"
                q["session"] = status
                cached[t] = q

    # 확정 종가 누락분 백필 (market_prices + snapshot 동시 갱신).
    # 위의 poll_quotes 는 분봉 기반이라 market_snapshot 만 채운다 — 일별 확정 종가
    # 테이블은 별도로 채워야 하므로 방금 폴링한 종목도 검사 대상에 포함한다.
    missing = _tickers_missing_last_close(syms) if backfill else []
    if missing:
        logger.info(f"확정 종가 누락 {len(missing)}종목 백필: {missing[:5]}")
        if backfill_last_close(missing):
            cached.update(_read_snapshot(missing))

    return {t: cached[t] for t in syms if t in cached}


def get_quote(ticker: str, max_age: int = DEFAULT_MAX_AGE, force: bool = False,
              backfill: bool = True, interval: str = "1m") -> Optional[dict]:
    """단일 티커 온디맨드 조회. 없으면 None."""
    return get_quotes([ticker], max_age=max_age, force=force, backfill=backfill,
                      interval=interval).get(str(ticker).upper().strip())


def seed_closing_prices() -> int:
    """장 마감 후: 확정 종가를 스냅샷에 반영해 장외에 종가가 보이도록 한다.

    24시간 자산(암호화폐·선물·환율·해외지수)은 '마감'이 없으므로 제외한다 —
    포함하면 계속 움직이는 실시간 가격을 낡은 종가로 덮어쓰게 된다.
    """
    from backend.db import get_conn, is_available
    from backend.services.market_calendar import (
        last_completed_session, uses_us_session_calendar,
    )
    if not is_available():
        return 0
    d = last_completed_session()
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 마지막 확정 거래일 종가 + 그 직전 종가로 변동률까지 계산
            cur.execute(
                """WITH ranked AS (
                     SELECT ticker, price_date, close_price,
                            ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY price_date DESC) rn
                     FROM market_prices WHERE price_date <= %s
                   )
                   SELECT c.ticker, c.close_price, p.close_price
                   FROM ranked c LEFT JOIN ranked p
                     ON p.ticker = c.ticker AND p.rn = 2
                   WHERE c.rn = 1""",
                (d,),
            )
            rows = cur.fetchall()
    quotes = {}
    for ticker, close, prev in rows:
        if not uses_us_session_calendar(ticker):
            continue   # 24시간 자산은 실시간 값을 유지
        q = {"price": close, "prev_close": prev}
        if prev:
            q["change_1d"] = close - prev
            q["change_1d_pct"] = (close / prev - 1) * 100
        quotes[ticker] = q
    return save_quotes(quotes, source="close", session="closed")
