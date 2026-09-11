"""
routers/portfolio.py
─────────────────────
포트폴리오 CRUD + 에쿼티 커브 + 핵심 지표 API.
데이터 저장: DB 우선 (portfolio_repo), DB 미연결 시 JSON 파일 폴백.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd
from backend.services.auth import current_user
from backend.services.markets import market_param
from fastapi import Depends, APIRouter, HTTPException, Header
from pydantic import BaseModel

from backend.db.portfolio_repo import (
    get_holdings, save_holding, delete_holding, update_holding_sector, user_write_lock,
    get_trade_log, add_trade, wipe_portfolio,
    update_trade_by_id, delete_trade_by_id,
)
from backend.models.portfolio import (
    HoldingItem, AddTradeRequest, UpdateHoldingRequest, PortfolioSetupRequest,
)
from backend.services.market_data import get_close_df
from backend.services.portfolio_calculator import (
    build_equity_curve,
    calculate_metrics,
    get_holdings_detail,
    build_return_pct_curve,
    return_pct_to_records,
    realized_pnl_from_log,
    portfolio_beta_detail,
    _market_open_flag,
)

# ─── 시세·현금·섹터 로직은 services 로 분리 ──────────────────────────────────
from backend.services.live_prices import (
    _get_live_prices, _ensure_prev_close, _inject_live,
)
from backend.services.cash_ledger import (
    _cash_event_delta, _revert_cash_event, _cash_delta, _adjust_cash,
    _recalculate_holding_from_trades,
)
from backend.services.sector_lookup import _fetch_sector


import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])




def _portfolio_close_df(
    holdings: dict,
    period: str = "2y",
    ttl: int = 3600,
    extra_tickers: list | None = None,
    market: str = "US",
):
    """
    포트폴리오 전용 close_df.
    extra_tickers: 현재 미보유이나 이력이 필요한 종목 (매도 완료 종목 등).
    metrics 와 equity-curve 가 동일 파라미터로 호출 → 두 번째 요청은 메모리 캐시 히트.

    벤치마크는 **시장을 따라간다** (US→^GSPC, KR→^KS11). 예전에는 ^GSPC 가
    하드코딩이라 한국 포트폴리오의 프레임에 ^KS11 이 없었고, 베타·알파가
    계산 자체를 못 해 영구히 null 이었다 — 응답은 benchmark_label 로
    "코스피" 라고 말하면서 값은 비어 있는 상태였다.

    ^VIX 는 시장과 무관하게 남긴다. VKOSPI 를 야후가 주지 않아(^VKOSPI ·
    ^VKOSPI200 · VKOSPI.KS · ^KSVKOSPI 전부 0건) 한국 화면에서도 미국 VIX 를
    쓰고, 그 사실은 라벨("변동성 (미국 VIX)")로 밝힌다.
    """
    from backend.services.markets import benchmark_for
    tickers = sorted(set(
        [t for t in holdings if t != "CASH"]
        + (extra_tickers or [])
        + [benchmark_for(market), "^VIX"]
    ))
    df = get_close_df(tickers, period=period, ttl=ttl, include_market=False)
    df = _inject_live(df)
    # 이 프레임은 에쿼티 곡선·베타·알파 전용이다 (곡선 밀도를 위해 ffill 유지).
    # 일변동률은 더 이상 여기서 계산하지 않고 services/price_series.py 의
    # '마지막 두 실제 관측치' primitive 가 담당한다. 예전의 유령행 제거 휴리스틱은
    # 전 종목 값 일치를 요구하고 한 행만 지워 실제로 동작하지 않았으므로 제거했다.
    return df


def _period_covering_first_trade(trade_log: list, minimum: str = "2y") -> str:
    """첫 거래일(입금 포함)까지 포함하는 최소 가격 기간을 고른다.

    period 를 "2y" 로 고정하면 그보다 오래된 계좌는 그래프 왼쪽이 잘려
    "최초 입금일부터" 보이지 않는다. 첫 거래일까지의 경과 연수를 보고
    yfinance 가 지원하는 구간 중 이를 덮는 가장 짧은 것을 고른다
    (필요 이상으로 길게 받으면 조회·계산 비용만 늘어난다).
    """
    from datetime import date
    dates = []
    for tr in (trade_log or []):
        d = str(tr.get("date", ""))[:10]
        if len(d) == 10:
            try:
                dates.append(date.fromisoformat(d))
            except ValueError:
                pass
    if not dates:
        return minimum

    years = (date.today() - min(dates)).days / 365.25
    # 문자열 비교("10y" >= "2y" 는 False)로 최소값을 강제하면 안 된다 — 연수로 비교한다.
    _MIN_YEARS = {"1y": 1, "2y": 2, "5y": 5, "10y": 10}.get(minimum, 2)
    # 첫 거래일을 확실히 덮도록 여유를 두되, 최소 기간보다 짧아지지 않게 한다.
    need = max(years + 0.05, _MIN_YEARS)
    for span, yrs in (("2y", 2), ("5y", 5), ("10y", 10)):
        if need <= yrs:
            return span
    return "max"


# ── Holdings ───────────────────────────────────────────────────────────────────

@router.get("/holdings")
def get_holdings_endpoint(_auth: dict = Depends(current_user), market: str = Depends(market_param)):
    """보유 종목. 각 항목에 화면용 이름(name)을 함께 담는다.

    한국 종목은 '034020.KS' 처럼 숫자 코드라 목록에서 어느 회사인지 알 수 없다.
    이름을 프론트가 따로 조회하게 하면 종목 수만큼 왕복이 생기므로 여기서 채운다.
    """
    from backend.services.markets import name_map_for

    holdings = get_holdings(_auth["uid"], market=market)
    tickers = [t for t in holdings if t != "CASH"]
    names = name_map_for(tickers, market) if tickers else {}
    for t, row in holdings.items():
        if t != "CASH" and isinstance(row, dict):
            row["name"] = names.get(t, t)
    return holdings


@router.put("/holdings/{ticker}")
def update_holding(
    ticker: str,
    body: UpdateHoldingRequest,
    _auth: dict = Depends(current_user),
    market: str = Depends(market_param),
):
    uid = _auth["uid"]
    ticker = ticker.upper()
    # 읽기부터 쓰기까지 전부 락 안이어야 한다. get_holdings 가 밖에 남으면
    # 두 요청이 같은 old_q 를 읽고 같은 delta 를 계산해 DEPOSIT 이 두 번 남는다
    # (제출 버튼 더블클릭으로 재현). 락은 사용자 단위라 다른 사용자는 안 막힌다.
    with user_write_lock(uid):
        holdings = get_holdings(uid, market=market)
        if ticker not in holdings:
            raise HTTPException(status_code=404, detail=f"{ticker} 미보유")

        # 현금 직접 수정 시 DEPOSIT/WITHDRAW 이벤트를 거래 이력에 기록
        if ticker == "CASH":
            old_q = float(holdings["CASH"].get("q", 0))
            new_q = float(body.q)
            delta = round(new_q - old_q, 2)
            if abs(delta) >= 0.01:
                event_date = (body.date or "").strip() or datetime.now().strftime("%Y-%m-%d")
                add_trade({
                    "date":   event_date,
                    "ticker": "CASH",
                    "type":   "DEPOSIT" if delta > 0 else "WITHDRAW",
                    "q":      abs(delta),
                    "price":  1.0,
                    "memo":   None,
                }, uid, market=market)

        sector = body.sector or holdings[ticker].get("sector", "Other")
        save_holding(ticker, body.q, body.avg, sector, uid, market=market)
        return {"ok": True, "ticker": ticker}


@router.post("/holdings/{ticker}")
def add_holding(
    ticker: str,
    item: HoldingItem,
    _auth: dict = Depends(current_user),
    market: str = Depends(market_param),
):
    uid = _auth["uid"]
    ticker = ticker.upper()
    # 존재 검사가 락 밖에 있으면 TOCTOU 다. 동시 두 요청이 모두 "없음" 을 보고
    # 409 를 통과한 뒤 각자 save_holding 을 부르는데, ON CONFLICT DO UPDATE 라
    # 에러도 나지 않는다 — 둘 다 200 을 받고 DEPOSIT 만 두 번 남는다.
    with user_write_lock(uid):
        holdings = get_holdings(uid, market=market)
        if ticker in holdings:
            raise HTTPException(status_code=409, detail=f"{ticker} 이미 존재. PUT으로 수정하세요.")

        # 현금 최초 등록 시 DEPOSIT 이벤트 기록
        if ticker == "CASH" and float(item.q) > 0:
            event_date = (item.date or "").strip() or datetime.now().strftime("%Y-%m-%d")
            add_trade({
                "date":   event_date,
                "ticker": "CASH",
                "type":   "DEPOSIT",
                "q":      float(item.q),
                "price":  1.0,
                "memo":   None,
            }, uid, market=market)

        save_holding(ticker, item.q, item.avg, item.sector or "Other", uid, market=market)
        return {"ok": True, "ticker": ticker}


@router.delete("/holdings/{ticker}")
def delete_holding_endpoint(
    ticker: str,
    _auth: dict = Depends(current_user),
    market: str = Depends(market_param),
):
    uid = _auth["uid"]
    with user_write_lock(uid):
        return _delete_holding_locked(ticker.upper(), uid, market)


def _delete_holding_locked(ticker: str, uid: str, market: str):
    holdings = get_holdings(uid, market=market)
    if ticker not in holdings:
        raise HTTPException(status_code=404, detail=f"{ticker} 미보유")

    # 거래 이력을 함께 지우면 그 거래들이 소비했던 현금이 영영 복구되지 않는다.
    # (매수로 차감된 현금이 그대로 사라져 총자산이 줄고, 에쿼티 곡선에서도
    #  해당 종목 구간이 통째로 없어진다.) 삭제 전에 순 현금 델타를 되돌린다.
    net_delta = 0.0
    for tr in get_trade_log(uid, market=market):
        if str(tr.get("ticker", "")).upper() != ticker:
            continue
        net_delta += _cash_delta(
            tr.get("type", ""), float(tr.get("q", 0)), float(tr.get("price") or 0)
        )

    delete_holding(ticker, uid, with_trades=True, market=market)
    if abs(net_delta) >= 0.001:
        _adjust_cash(uid, -net_delta, market=market)
    return {"ok": True, "ticker": ticker, "cash_restored": round(-net_delta, 2)}


# ── 섹터 헬퍼 (내부 함수, 엔드포인트 아님) ────────────────────────────────────

# ── Trade Log ──────────────────────────────────────────────────────────────────

@router.get("/trades")
def get_trades(_auth: dict = Depends(current_user), market: str = Depends(market_param)):
    return get_trade_log(_auth["uid"], market=market)


@router.post("/trades")
def add_trade_endpoint(
    body: AddTradeRequest,
    _auth: dict = Depends(current_user),
    market: str = Depends(market_param),
):
    uid = _auth["uid"]
    # 사용자별 쓰기 직렬화 — 동시 매매의 read-modify-write 경쟁 방지
    with user_write_lock(uid):
        return _add_trade_locked(body, uid, market)


def _add_trade_locked(body: AddTradeRequest, uid: str, market: str):
    import threading
    holdings = get_holdings(uid, market=market)
    ticker = body.ticker.upper()

    _type_map = {"BUY": "ADD", "SELL": "SOLD"}
    trade_type = _type_map.get(body.type.upper(), body.type.upper())

    # ── 입력 검증 ────────────────────────────────────────────────────────────
    # 검증이 없으면 음수 수량·미보유 종목 매도·초과 매도가 모두 통과해
    # _adjust_cash 가 없는 현금을 만들어낸다 (보유 갱신은 건너뛰고 현금만 증가).
    try:
        qty_in = float(body.q)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="수량이 올바르지 않습니다")
    if not (qty_in > 0) or qty_in != qty_in or qty_in in (float("inf"), float("-inf")):
        raise HTTPException(status_code=400, detail="수량은 0보다 커야 합니다")

    price_in = float(body.price or 0)
    if price_in < 0 or price_in != price_in:
        raise HTTPException(status_code=400, detail="가격이 올바르지 않습니다")

    if trade_type == "SOLD" and ticker != "CASH":
        held = float(holdings.get(ticker, {}).get("q", 0) or 0)
        if held <= 0:
            raise HTTPException(
                status_code=400, detail=f"{ticker} 미보유 — 매도할 수 없습니다"
            )
        if qty_in > held + 1e-9:
            raise HTTPException(
                status_code=400,
                detail=f"보유 수량({held:g})보다 많이 매도할 수 없습니다",
            )

    # ── 현금 잔고 검사 ───────────────────────────────────────────────────────
    # 매수·출금이 잔고를 넘으면 막는다. 예전에는 음수 잔고를 그대로 두었는데,
    # 사용자는 그 상태를 오류로 인식하지 못하고 계속 거래해 장부가 어긋났다.
    # (포트폴리오 최초 등록만 예외다 — /setup 이 따로 처리한다.)
    cash_now = float(holdings.get("CASH", {}).get("q", 0) or 0)
    need = 0.0
    if ticker == "CASH" and trade_type == "WITHDRAW":
        need = abs(qty_in)
    elif ticker != "CASH" and trade_type == "ADD":
        need = qty_in * price_in

    if need > 0 and need > cash_now + 1e-6:
        # 통화 포맷을 여기서 다시 구현하지 않는다. '$' 와 소수점 2자리를 고정하면
        # 한국 거래에서 '필요 $71,900.00' 이 사용자 화면에 그대로 뜬다 — 기호도
        # 틀리고, 원화는 호가 단위가 1원이라 소수점 자체가 없는 정밀도다.
        from backend.services.markets import get_market
        from backend.services.report_writer import _fmt_price
        cur = get_market(market).currency
        raise HTTPException(
            status_code=400,
            detail=f"현금이 부족합니다. 필요 {_fmt_price(need, cur)} · "
                   f"보유 {_fmt_price(cash_now, cur)}",
        )

    if ticker not in holdings and trade_type == "ADD":
        save_holding(ticker, 0.0, float(body.price or 0), "Other", uid, market=market)
        holdings = get_holdings(uid, market=market)
        # 백그라운드에서 섹터 자동 조회 후 업데이트
        def _bg_sector():
            try:
                sector = _fetch_sector(ticker)
                if sector and sector != "Other":
                    # 섹터만 UPDATE — 조회를 기다리는 사이 체결된 매매를 되돌리지 않는다
                    update_holding_sector(ticker, sector, uid, market=market)
            except Exception as e:
                # 데몬 스레드라 예외를 안 잡으면 stderr 트레이스백만 남고 끝난다.
                # 섹터는 부가 정보라 요청을 실패시킬 이유는 없지만, 어디서 왜
                # 실패했는지는 남겨야 한다.
                logger.warning(f"섹터 자동 조회 실패 (ticker={ticker}, "
                               f"uid={uid}, market={market}): {e}")
        threading.Thread(target=_bg_sector, daemon=True).start()

    trade_date = (body.date or "").strip() or datetime.now().strftime("%Y-%m-%d")
    record = {
        "date":   trade_date,
        "ticker": ticker,
        "type":   trade_type,
        "q":      body.q,
        "price":  body.price,
        "memo":   body.memo,
    }
    add_trade(record, uid, market=market)

    # holdings 자동 업데이트
    if ticker in holdings:
        q     = float(body.q)
        price = float(body.price or 0)
        cur   = holdings[ticker]
        if trade_type == "ADD":
            prev_q, prev_avg = cur["q"], cur.get("avg", price)
            new_q   = prev_q + q
            new_avg = round((prev_avg * prev_q + price * q) / new_q, 4) if new_q > 0 else price
            save_holding(ticker, round(new_q, 6), new_avg, cur.get("sector", "Other"), uid,
                         market=market)
        elif trade_type == "SOLD":
            new_qty = max(0.0, round(cur["q"] - q, 6))
            if new_qty == 0.0:
                delete_holding(ticker, uid, market=market)
            else:
                save_holding(ticker, new_qty, cur["avg"], cur.get("sector", "Other"), uid,
                             market=market)
        elif trade_type == "UPDATE":
            save_holding(ticker, q, cur["avg"], cur.get("sector", "Other"), uid,
                         market=market)

    # 현금 잔고 반영.
    # 종목 매매는 대금만큼 가감하고, CASH 입출금은 그 금액 자체를 가감한다.
    # 예전에는 CASH 입출금이 거래 이력에만 남고 잔고를 건드리지 않아,
    # 입금해도 잔고가 그대로인 채 성공 응답만 돌아갔다.
    if ticker == "CASH":
        _adjust_cash(uid, _cash_event_delta(trade_type, float(body.q)), market=market)
    else:
        _adjust_cash(uid, _cash_delta(trade_type, float(body.q), float(body.price or 0)),
                     market=market)

    return {"ok": True, "record": record}


class UpdateTradeRequest(BaseModel):
    date:   str
    ticker: str
    type:   str
    q:      float
    price:  Optional[float] = None
    memo:   Optional[str]   = None


@router.put("/trades/{trade_id}")
def update_trade_endpoint(
    trade_id: int,
    body: UpdateTradeRequest,
    _auth: dict = Depends(current_user),
    market: str = Depends(market_param),
):
    uid = _auth["uid"]
    with user_write_lock(uid):
        return _update_trade_locked(trade_id, body, uid, market)


def _update_trade_locked(trade_id: int, body: "UpdateTradeRequest", uid: str, market: str):
    # 수정 전에 기존 티커 파악 (ticker가 변경될 수 있으므로 old/new 모두 재계산)
    all_trades = get_trade_log(uid, market=market)
    old_trade  = next((t for t in all_trades if t.get("id") == trade_id), None)
    old_ticker = old_trade["ticker"] if old_trade else None

    # 티커·거래유형을 다른 쓰기 경로와 동일하게 정규화한다.
    # 정규화하지 않으면 소문자/BUY 표기가 그대로 저장되고, 재생 로직은
    # 정확히 일치하는 문자열만 세므로 해당 거래가 통째로 누락돼 보유가 삭제된다.
    payload = body.dict()
    payload["ticker"] = str(body.ticker).upper().strip()
    payload["type"] = {"BUY": "ADD", "SELL": "SOLD"}.get(
        str(body.type).upper(), str(body.type).upper()
    )

    ok = update_trade_by_id(trade_id, payload, uid, market=market)
    if not ok:
        raise HTTPException(status_code=404, detail="거래 내역 없음")

    # 거래 수정 후 보유수량 재계산
    _recalculate_holding_from_trades(body.ticker.upper(), uid, market=market)
    if old_ticker and old_ticker.upper() != body.ticker.upper():
        _recalculate_holding_from_trades(old_ticker.upper(), uid, market=market)

    # CASH 잔고: 기존 거래 델타와 새 거래 델타의 차이만큼 조정
    if body.ticker.upper() != "CASH" and old_trade:
        old_delta = _cash_delta(
            old_trade.get("type", ""),
            float(old_trade.get("q", 0)),
            float(old_trade.get("price") or 0),
        )
        new_delta = _cash_delta(body.type, float(body.q), float(body.price or 0))
        _adjust_cash(uid, new_delta - old_delta, market=market)
    elif body.ticker.upper() == "CASH" and old_trade:
        # DEPOSIT/WITHDRAW 수정도 잔고에 반영해야 한다 (_cash_delta 는 0 을 반환하므로 별도 처리)
        old_delta = _cash_event_delta(old_trade.get("type", ""), float(old_trade.get("q", 0)))
        new_delta = _cash_event_delta(body.type, float(body.q))
        _adjust_cash(uid, new_delta - old_delta, market=market)
    return {"ok": True}


@router.delete("/trades/{trade_id}")
def delete_trade_endpoint(
    trade_id: int,
    _auth: dict = Depends(current_user),
    market: str = Depends(market_param),
):
    uid = _auth["uid"]
    with user_write_lock(uid):
        return _delete_trade_locked(trade_id, uid, market)


def _delete_trade_locked(trade_id: int, uid: str, market: str):
    all_trades = get_trade_log(uid, market=market)
    trade = next((t for t in all_trades if t.get("id") == trade_id), None)
    if not trade:
        raise HTTPException(status_code=404, detail="거래 내역 없음")

    ok = delete_trade_by_id(trade_id, uid, market=market)
    if not ok:
        raise HTTPException(status_code=404, detail="거래 내역 없음")

    ttype  = str(trade.get("type",   "")).upper()
    ticker = str(trade.get("ticker", "")).upper()

    # 예전에는 BUY 를 지우면 그 날짜 이후의 SELL 을 전부 연쇄 삭제했다.
    # 남은 BUY 가 그 SELL 들을 충분히 커버하는 경우까지 지워버려 사용자 거래
    # 이력이 소실됐으므로 제거했다. 재생(_recalculate_holding_from_trades)이
    # 수량을 0 이하로 클램프하므로 보유 수량은 어차피 정합성을 유지한다.
    # 이 종목의 거래를 방금 지웠다. 남은 거래가 0건이면 보유도 함께 지운다 —
    # 매수 이력을 지웠는데 보유 종목이 그대로 남아 있으면 근거 없는 자산이 된다.
    _recalculate_holding_from_trades(ticker, uid, market=market,
                                     drop_when_no_trades=True)

    # CASH 잔고: 삭제된 거래 델타를 역방향으로 복원
    if ticker != "CASH":
        _adjust_cash(uid, -_cash_delta(
            trade.get("type", ""),
            float(trade.get("q", 0)),
            float(trade.get("price") or 0),
        ), market=market)
    else:
        # CASH DEPOSIT/WITHDRAW 삭제 시 잔고를 되돌린다 (_cash_delta 는 0 을 반환)
        _revert_cash_event(uid, ttype, float(trade.get("q", 0)), market=market)
    return {"ok": True}


# 시총 순 미국 상장 주요 티커 (rank 낮을수록 대형주)
_US_TICKERS: list[tuple[str, str]] = [
    ("AAPL","Apple Inc."),("MSFT","Microsoft Corp."),("NVDA","NVIDIA Corp."),
    ("AMZN","Amazon.com Inc."),("GOOGL","Alphabet Inc."),("META","Meta Platforms"),
    ("TSLA","Tesla Inc."),("BRK-B","Berkshire Hathaway"),("AVGO","Broadcom Inc."),
    ("LLY","Eli Lilly"),("JPM","JPMorgan Chase"),("V","Visa Inc."),
    ("XOM","ExxonMobil"),("UNH","UnitedHealth Group"),("MA","Mastercard"),
    ("COST","Costco Wholesale"),("NFLX","Netflix Inc."),("WMT","Walmart Inc."),
    ("PG","Procter & Gamble"),("JNJ","Johnson & Johnson"),
    ("HD","Home Depot"),("CRM","Salesforce Inc."),("BAC","Bank of America"),
    ("ORCL","Oracle Corp."),("ABBV","AbbVie Inc."),("MRK","Merck & Co."),
    ("CVX","Chevron Corp."),("KO","Coca-Cola Co."),("CSCO","Cisco Systems"),
    ("AMD","Advanced Micro Devices"),("ADBE","Adobe Inc."),("PEP","PepsiCo"),
    ("ACN","Accenture"),("INTC","Intel Corp."),("DIS","Walt Disney Co."),
    ("TXN","Texas Instruments"),("MCD","McDonald's Corp."),("VZ","Verizon"),
    ("QCOM","Qualcomm Inc."),("CAT","Caterpillar Inc."),("WFC","Wells Fargo"),
    ("TMO","Thermo Fisher"),("AMGN","Amgen Inc."),("IBM","IBM Corp."),
    ("SPGI","S&P Global"),("INTU","Intuit Inc."),("MS","Morgan Stanley"),
    ("GS","Goldman Sachs"),("BKNG","Booking Holdings"),("LOW","Lowe's Cos."),
    ("PFE","Pfizer Inc."),("UBER","Uber Technologies"),("ISRG","Intuitive Surgical"),
    ("RTX","RTX Corp."),("AMAT","Applied Materials"),("AXP","American Express"),
    ("LRCX","Lam Research"),("NOW","ServiceNow"),("DE","Deere & Co."),
    ("T","AT&T Inc."),("HON","Honeywell"),("C","Citigroup"),
    ("GILD","Gilead Sciences"),("ETN","Eaton Corp."),("BMY","Bristol-Myers"),
    ("BSX","Boston Scientific"),("MDT","Medtronic"),("SYK","Stryker Corp."),
    ("REGN","Regeneron Pharma"),("PANW","Palo Alto Networks"),("ADI","Analog Devices"),
    ("KLAC","KLA Corp."),("MU","Micron Technology"),("VRTX","Vertex Pharma"),
    ("PLD","Prologis"),("SNPS","Synopsys"),("CDNS","Cadence Design"),
    ("MMC","Marsh & McLennan"),("BLK","BlackRock"),("CI","Cigna Group"),
    ("SHW","Sherwin-Williams"),("CME","CME Group"),("ZTS","Zoetis"),
    ("TMUS","T-Mobile US"),("PGR","Progressive Corp."),("AON","Aon plc"),
    ("GEV","GE Vernova"),("COP","ConocoPhillips"),("SO","Southern Co."),
    ("DUK","Duke Energy"),("NEE","NextEra Energy"),("APD","Air Products"),
    ("FI","Fiserv Inc."),("MCO","Moody's Corp."),("DASH","DoorDash"),
    ("SHOP","Shopify Inc."),("SNOW","Snowflake"),("PLTR","Palantir"),
    ("ARM","Arm Holdings"),("ASML","ASML Holding"),("TSM","TSMC"),
    ("NVO","Novo Nordisk"),("SAP","SAP SE"),("SONY","Sony Group"),
    ("TM","Toyota Motor"),("BABA","Alibaba Group"),("PDD","PDD Holdings"),
    ("JD","JD.com"),("BIDU","Baidu"),("NKE","Nike Inc."),
    ("SBUX","Starbucks"),("BA","Boeing Co."),("MMM","3M Co."),
    ("GE","GE Aerospace"),("F","Ford Motor"),("GM","General Motors"),
    ("RIVN","Rivian Auto."),("LCID","Lucid Group"),("NIO","NIO Inc."),
    ("XPEV","Xpeng Inc."),("LI","Li Auto"),("COIN","Coinbase"),
    ("HOOD","Robinhood Markets"),("XYZ","Block Inc."),("PYPL","PayPal"),
    ("SOFI","SoFi Technologies"),("AFRM","Affirm Holdings"),("UPST","Upstart"),
    ("APP","Applovin Corp."),("RBLX","Roblox Corp."),("U","Unity Software"),
    ("DKNG","DraftKings"),("PENN","PENN Entertainment"),("MGM","MGM Resorts"),
    ("WYNN","Wynn Resorts"),("LVS","Las Vegas Sands"),("CZR","Caesars"),
    ("MAR","Marriott Intl."),("HLT","Hilton Worldwide"),("H","Hyatt Hotels"),
    ("UAL","United Airlines"),("DAL","Delta Air Lines"),("AAL","American Airlines"),
    ("LUV","Southwest Airlines"),("CCL","Carnival Corp."),("RCL","Royal Caribbean"),
    ("NCLH","Norwegian Cruise"),("MO","Altria Group"),("PM","Philip Morris"),
    ("BTI","British American"),("KMB","Kimberly-Clark"),("CL","Colgate-Palmolive"),
    ("CHD","Church & Dwight"),("EL","Estee Lauder"),("ULTA","Ulta Beauty"),
    ("LULU","Lululemon"),("GPS","Gap Inc."),("PVH","PVH Corp."),
    ("RL","Ralph Lauren"),("TPR","Tapestry"),("CPRI","Capri Holdings"),
    ("TGT","Target Corp."),("DLTR","Dollar Tree"),("DG","Dollar General"),
    ("KR","Kroger Co."),("SYY","Sysco Corp."),("MKC","McCormick"),
    ("CPB","Campbell Soup"),("HRL","Hormel Foods"),("GIS","General Mills"),
    ("K","Kellanova"),("HSY","Hershey Co."),("MDLZ","Mondelez Intl."),
    ("TSN","Tyson Foods"),("CAG","Conagra Brands"),("ADM","Archer-Daniels"),
    ("BG","Bunge Global"),("MOS","Mosaic Co."),("CF","CF Industries"),
    ("NTR","Nutrien Ltd."),("DVN","Devon Energy"),("OXY","Occidental Petroleum"),
    ("HAL","Halliburton"),("SLB","SLB (Schlumberger)"),("BKR","Baker Hughes"),
    ("FANG","Diamondback Energy"),("EOG","EOG Resources"),("MPC","Marathon Petroleum"),
    ("VLO","Valero Energy"),("PSX","Phillips 66"),("HES","Hess Corp."),
    ("APA","APA Corp."),("PXD","Pioneer Natural"),("AR","Antero Resources"),
    ("RRC","Range Resources"),("EQT","EQT Corp."),("CTRA","Coterra Energy"),
    ("ACGL","Arch Capital"),("ALL","Allstate"),("CB","Chubb Ltd."),
    ("TRV","Travelers Cos."),("MET","MetLife"),("PRU","Prudential Fin."),
    ("AFL","Aflac"),("LNC","Lincoln National"),("UNM","Unum Group"),
    ("HIG","Hartford Financial"),("GL","Globe Life"),("CINF","Cincinnati Fin."),
    ("WRB","W.R. Berkley"),("AJG","Arthur Gallagher"),("MMB","Marsh McLennan"),
    ("USB","U.S. Bancorp"),("TFC","Truist Financial"),("PNC","PNC Financial"),
    ("FITB","Fifth Third"),("KEY","KeyCorp"),("RF","Regions Financial"),
    ("CFG","Citizens Financial"),("HBAN","Huntington Bancshares"),("MTB","M&T Bank"),
    ("CMA","Comerica"),("ZION","Zions Bancorp"),("WAL","Western Alliance"),
    ("WBS","Webster Financial"),("VLY","Valley National"),("PACW","PacWest"),
    ("SCHW","Charles Schwab"),("RJF","Raymond James"),("ETFC","E*TRADE"),
    ("IBKR","Interactive Brokers"),("LPLA","LPL Financial"),("SF","Stifel Fin."),
    ("HDB","HDFC Bank"),("ITUB","Itau Unibanco"),("BBD","Bradesco"),
    ("SAN","Banco Santander"),("ING","ING Groep"),("CS","Credit Suisse"),
    ("UBS","UBS Group"),("DB","Deutsche Bank"),("BCS","Barclays"),
    ("LYG","Lloyds Banking"),("NWG","NatWest Group"),("HSBC","HSBC Holdings"),
    ("TD","Toronto-Dominion"),("RY","Royal Bank Canada"),("BMO","Bank of Montreal"),
    ("CM","CIBC"),("BNS","Bank of Nova Scotia"),("MFC","Manulife Fin."),
    ("SU","Suncor Energy"),("CNQ","Canadian Natural"),("CVE","Cenovus Energy"),
    ("ENB","Enbridge"),("TRP","TC Energy"),("BCE","BCE Inc."),
    ("AMCR","Amcor plc"),("IP","Intl. Paper"),("PKG","Packaging Corp."),
    ("WRK","WestRock"),("SON","Sonoco Products"),("SEE","Sealed Air"),
    ("BALL","Ball Corp."),("BLL","Ball Corp."),("AVY","Avery Dennison"),
    ("IFF","Intl. Flavors"),("EMN","Eastman Chemical"),("LYB","LyondellBasell"),
    ("HUN","Huntsman Corp."),("CC","Chemours Co."),("OLN","Olin Corp."),
    ("CE","Celanese"),("ASH","Ashland Inc."),("RPM","RPM Intl."),
    ("ECL","Ecolab"),("PPG","PPG Industries"),("FMC","FMC Corp."),
    ("NUE","Nucor Corp."),("STLD","Steel Dynamics"),("X","U.S. Steel"),
    ("CLF","Cleveland-Cliffs"),("CMC","Commercial Metals"),("MTX","Minerals Tech."),
    ("MLM","Martin Marietta"),("VMC","Vulcan Materials"),("MDU","MDU Resources"),
    ("PKX","POSCO Holdings"),("VALE","Vale S.A."),("FCX","Freeport-McMoRan"),
    ("SCCO","Southern Copper"),("HBM","Hudbay Minerals"),("TECK","Teck Resources"),
    ("AA","Alcoa Corp."),("CENX","Century Aluminum"),("KALU","Kaiser Aluminum"),
    ("ARNC","Arconic"),("ATI","ATI Inc."),("HWM","Howmet Aerospace"),
    ("GD","General Dynamics"),("LMT","Lockheed Martin"),("NOC","Northrop Grumman"),
    ("LHX","L3Harris"),("HII","Huntington Ingalls"),("TDG","TransDigm"),
    ("SPR","Spirit AeroSystems"),("HXL","Hexcel Corp."),("TXT","Textron"),
    ("PWR","Quanta Services"),("FLR","Fluor Corp."),("J","Jacobs Solutions"),
    ("ACM","AECOM"),("MTZ","MasTec"),("MYR","MYR Group"),("MYRG","MYR Group"),
    ("EME","EMCOR Group"),("TTEK","Tetra Tech"),("ABM","ABM Industries"),
    ("AYI","Acuity Brands"),("CSGP","CoStar Group"),("VNO","Vornado Realty"),
    ("SLG","SL Green"),("BXP","BXP Inc."),("KIM","Kimco Realty"),
    ("O","Realty Income"),("SPG","Simon Property"),("MAC","Macerich"),
    ("CBL","CBL & Associates"),("WPG","WP Glimcher"),("SKT","Tanger Factory"),
    ("ADC","Agree Realty"),("NNN","NNN REIT"),("EPRT","Essential Prop."),
    ("STAG","STAG Industrial"),("EGP","EastGroup Prop."),("REXR","Rexford Ind."),
    ("FR","First Industrial"),("DRE","Duke Realty"),("TRNO","Terreno Realty"),
    ("AMT","American Tower"),("CCI","Crown Castle"),("SBAC","SBA Comm."),
    ("IRM","Iron Mountain"),("DLR","Digital Realty"),("EQIX","Equinix"),
    ("QTS","QTS Realty"),("CONE","CyrusOne"),("NLOK","NortonLifeLock"),
    ("AKAM","Akamai Tech."),("FTNT","Fortinet"),("CRWD","CrowdStrike"),
    ("S","SentinelOne"),("ZS","Zscaler"),("OKTA","Okta Inc."),
    ("CYBR","CyberArk"),("QLYS","Qualys"),("VRNS","Varonis Systems"),
    ("TENB","Tenable Holdings"),("RPD","Rapid7"),("BB","BlackBerry"),
    ("NET","Cloudflare"),("FSLY","Fastly"),("DDOG","Datadog"),
    ("GTLB","GitLab"),("MDB","MongoDB"),("ESTC","Elastic N.V."),
    ("NEWR","New Relic"),("SUMO","Sumo Logic"),("SPLK","Splunk"),
    ("SMAR","Smartsheet"),("APPN","Appian Corp."),("ASAN","Asana"),
    ("TEAM","Atlassian"),("ZM","Zoom Video"),("RNG","RingCentral"),
    ("TWLO","Twilio"),("BAND","Bandwidth"),("CIEN","Ciena Corp."),
    ("JNPR","Juniper Networks"),("ANET","Arista Networks"),("NTAP","NetApp"),
    ("NTNX","Nutanix"),("DT","Dynatrace"),("NCNO","nCino"),
    ("ALTR","Altair Eng."),("PTC","PTC Inc."),("CDLX","Cardlytics"),
    ("TTD","The Trade Desk"),("PUBM","PubMatic"),("MGNI","Magnite"),
    ("DV","DoubleVerify"),("IAS","Integral Ad Sci."),("CARG","CarGurus"),
    ("CAR","Avis Budget"),("HTZ","Hertz Global"),("URI","United Rentals"),
    ("GATX","GATX Corp."),("AL","Air Lease"),("AER","AerCap Holdings"),
    ("FTAI","FTAI Aviation"),("WSC","WillScot Mobile"),
    ("FWLD","Foreworld Corp."),
    ("HIMS","Hims & Hers"),("W","Wayfair"),("CHWY","Chewy"),
    ("ETSY","Etsy Inc."),("EBAY","eBay"),("OSTK","Overstock.com"),
    ("FTCH","Farfetch"),("RH","RH (Restoration)"),("WSM","Williams-Sonoma"),
    ("BBY","Best Buy"),("GME","GameStop"),("BBBY","Bed Bath Beyond"),
    ("SFIX","Stitch Fix"),("REAL","RealReal"),("POSH","Poshmark"),
    ("ACMR","ACM Research"),("BRKS","Brooks Automation"),("CEVA","CEVA Inc."),
    ("DIOD","Diodes Inc."),("ENTG","Entegris"),("FN","Fabrinet"),
    ("MKSI","MKS Instruments"),("ONTO","Onto Innovation"),("ACLS","Axcelis"),
    ("CAMT","Camtek Ltd."),("COHU","Cohu Inc."),("ICHR","Ichor Holdings"),
    ("KLIC","Kulicke & Soffa"),("NXPI","NXP Semiconductors"),
    ("ON","ON Semiconductor"),("SWKS","Skyworks"),("QRVO","Qorvo"),
    ("MCHP","Microchip Tech."),("MPWR","Monolithic Power"),("WOLF","Wolfspeed"),
    ("AEHR","Aehr Test"),("LSCC","Lattice Semi."),("MLAB","Mesa Labs"),
    ("PI","Impinj"),("FORM","FormFactor"),("ACMR","ACM Research"),
    ("AAON","AAON Inc."),("AIR","AAR Corp."),("ALLE","Allegion"),
    ("AWK","American Water"),("ATO","Atmos Energy"),("CNP","CenterPoint"),
    ("LNT","Alliant Energy"),("IDA","IDACORP"),("PNW","Pinnacle West"),
    ("AEE","Ameren Corp."),("WEC","WEC Energy"),("PPL","PPL Corp."),
    ("FE","FirstEnergy"),("NI","NiSource"),("AES","AES Corp."),
    ("CMS","CMS Energy"),("ETR","Entergy"),("EXC","Exelon"),
    ("PCG","PG&E Corp."),("SCG","SCANA Corp."),("XEL","Xcel Energy"),
    ("SRE","Sempra"),("AWR","American States"),("CTWS","ConnSavings"),
    ("MSEX","Middlesex Water"),("SJW","SJW Group"),("YORW","York Water"),
    ("CPK","Chesapeake Utils."),("LADR","Ladder Capital"),("KREF","KKR Real Estate"),
    ("GPMT","Granite Point"),("RC","Ready Capital"),("BXMT","Blackstone Mtg."),
    ("ARI","Apollo Commercial"),("BRSP","BrightSpire"),("TPVG","TriplePoint Vent."),
    ("GAIN","Gladstone Invest."),("MAIN","Main Street Capital"),
    ("ARCC","Ares Capital"),("FSCO","FS Credit Opps"),
]


@router.get("/ticker-search")
def ticker_search(q: str = "", limit: int = 5, market: str = Depends(market_param)):
    """상장 티커 검색. 시장에 따라 찾는 목록이 다르다.

    미국은 ticker_universe(NASDAQ+NYSE 전 종목)를, 한국은 KRX 상장목록을 본다.
    한 목록에서 둘 다 찾으면 안 된다 — 한국 화면에서 'LG' 를 쳤을 때 미국
    종목이 섞여 나오면 그대로 등록되어 시장 구분이 깨진다.
    """
    if not q:
        return []
    if market == "KR":
        from backend.services.korea_universe import search_listed
        return search_listed(q, limit)
    q = q.upper().strip()

    try:
        from backend.db import get_conn, is_available
        if is_available():
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT ticker, name FROM ticker_universe
                           WHERE active AND (ticker LIKE %s OR UPPER(name) LIKE %s)
                           ORDER BY
                             CASE WHEN ticker = %s THEN 0
                                  WHEN ticker LIKE %s THEN 1
                                  ELSE 2 END,
                             is_etf,          -- 보통주를 ETF보다 앞에
                             length(ticker),
                             ticker
                           LIMIT %s""",
                        (f"%{q}%", f"%{q}%", q, f"{q}%", limit),
                    )
                    rows = cur.fetchall()
            if rows:
                return [{"ticker": r[0], "name": r[1] or ""} for r in rows]
    except Exception as e:
        # 폴백은 내장 시총 상위 목록이라 검색 결과가 조용히 좁아진다. 값이
        # 틀리지는 않지만 "왜 이 종목이 안 나오지" 의 원인이 로그에만 남는다.
        logger.warning(f"종목 검색 DB 조회 실패, 내장 목록으로 폴백 (q={q!r}): {e}")

    # 폴백: 내장 시총 상위 목록
    prefix  = [(t, n) for t, n in _US_TICKERS if t.startswith(q)]
    in_mid  = [(t, n) for t, n in _US_TICKERS if q in t and not t.startswith(q)]
    by_name = [(t, n) for t, n in _US_TICKERS if q in n.upper() and not t.startswith(q) and q not in t]
    merged = (prefix + in_mid + by_name)[:limit]
    return [{"ticker": t, "name": n} for t, n in merged]


@router.get("/ticker-exists")
def ticker_exists(ticker: str = "", market: str = Depends(market_param)):
    """티커가 실제 시장에 존재하는지 확인. 로그인 불필요 — 포트폴리오 최적화처럼
    비로그인에서도 종목을 입력받는 화면이 저장 전에 검증할 수 있어야 한다.

    ① 상장 목록(ticker_universe, DB) 조회 — 몇 ms, 대부분의 US 종목을 커버.
    ② 목록에 없으면 yfinance 로 최종 확인 — 목록 동기화 지연·ETF 등 예외 대응.
    결과는 1시간 캐시한다 — 오탈자를 여러 번 시도해도 같은 티커에 yfinance 를
    반복 호출하지 않는다.
    """
    from backend.services.markets import universe_lookup
    from backend.services.market_data import _cached

    sym = ticker.upper().strip()
    if not sym:
        return {"ticker": sym, "exists": False, "name": None}

    if market == "KR":
        # 한국은 KRX 상장목록에서 바로 확인한다. 코드만 입력해도(005930)
        # 접미사를 붙여 준다 — 사용자가 '.KS' 를 알 이유가 없다.
        from backend.services.korea_universe import get_listed_all
        code = sym.split(".")[0]
        for row in get_listed_all():
            if row["ticker"].split(".")[0] == code:
                return {"ticker": row["ticker"], "exists": True,
                        "name": row.get("name", "")}
        return {"ticker": sym, "exists": False, "name": None}

    known = universe_lookup(sym, market)
    if known is not None:
        return {"ticker": sym, "exists": True, "name": known.get("name", "")}

    def _check_yfinance() -> bool:
        try:
            import yfinance as yf
            from backend.db.market_cache import _yf_sem
            with _yf_sem:
                hist = yf.Ticker(sym).history(period="5d")
            return not hist.empty
        except Exception as e:
            logger.warning(f"[ticker-exists] {sym} yfinance 확인 실패: {e}")
            return False

    exists = _cached(f"ticker_exists::{sym}", 3600, _check_yfinance)
    return {"ticker": sym, "exists": bool(exists), "name": None}


@router.get("/ticker-price")
def get_ticker_price(ticker: str, _auth: dict = Depends(current_user), market: str = Depends(market_param)):
    """티커 현재가 조회 — 거래 입력 폼의 가격 자동 채움용.

    **속도가 핵심인 경로다.** 사용자가 티커를 입력하고 값이 채워질 때까지
    기다리는 화면이라, 예전에는 없는 심볼에 17초까지 걸렸다. 원인은 yfinance 를
    순차로 최대 네 번 부른 것이었다(폴링 → 종가 백필 → 단일조회 → fast_info).

    지금은 이렇게 줄였다.
      ① 상장 목록(11,000여 종목)에서 심볼 존재 여부를 먼저 본다 — 없으면 즉시
         404. 타이핑 중간값처럼 존재하지 않는 심볼을 yfinance 에 물어보느라
         수 초를 쓰지 않는다.
      ② 확정 종가 백필을 건너뛴다 — 여기서 필요한 건 현재가 하나뿐이고,
         일별 종가는 스케줄러가 채운다.
      ③ 마지막 수단인 단일 조회도 fast_info 를 부르지 않는다 — 이름은 이미
         상장 목록에 있다.
    """
    from backend.services.live_quotes import get_quote, DEFAULT_MAX_AGE
    from backend.services.markets import universe_lookup, get_market

    sym = ticker.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="티커를 입력하세요")
    # 통화는 시장이 정한다. 한국 종목에 USD 를 붙이면 25만원짜리 주식이
    # 25만 달러로 읽힌다.
    currency = get_market(market).currency

    # ① 상장 목록 조회 (DB, 약 1ms). 목록에 없으면 여기서 끝낸다 —
    #    타이핑 중간값 같은 없는 심볼을 yfinance 에 물어보면 수 초가 그냥 날아간다.
    known = universe_lookup(sym, market)
    if known is None:
        raise HTTPException(status_code=404, detail=f"티커 {sym}를 찾을 수 없습니다")
    # 사용자가 '005930' 만 입력했어도 시세 조회에는 정식 심볼('005930.KS')이 필요하다.
    sym = known.get("ticker") or sym

    try:
        # interval="5m": 화면에 값 하나 채우는 용도라 1분봉까지 받을 이유가 없다.
        q = get_quote(sym, max_age=DEFAULT_MAX_AGE, backfill=False, interval="5m")
    except Exception as e:
        logger.warning(f"[ticker-price] {sym} 조회 오류: {e}")
        q = None

    if q and q.get("price"):
        return {
            "ticker":        sym,
            "price":         round(float(q["price"]), 4),
            "name":          (known or {}).get("name", ""),
            "currency":      currency,
            "change_pct":    q.get("change_1d_pct"),
            "volume":        q.get("volume"),
            "as_of_session": q.get("session"),
            "age_sec":       round(float(q.get("age_sec") or 0), 1),
        }

    # 상장은 돼 있는데 시세만 못 받은 경우 — 마지막으로 직접 조회한다.
    try:
        import yfinance as yf
        hist = yf.Ticker(sym).history(period="5d")
        if hist.empty:
            raise HTTPException(status_code=404, detail=f"티커 {sym}의 시세가 없습니다")
        return {
            "ticker":   sym,
            "price":    round(float(hist["Close"].iloc[-1]), 4),
            "name":     known.get("name", ""),
            "currency": currency,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"티커 조회 실패: {e}")


@router.post("/auto-sector")
def auto_sector(_auth: dict = Depends(current_user), market: str = Depends(market_param)):
    """보유 종목 중 sector='Other'인 종목의 섹터를 yfinance로 자동 분류."""
    uid      = _auth["uid"]
    holdings = get_holdings(uid, market=market)
    updated  = []
    for ticker, info in holdings.items():
        if ticker == "CASH":
            continue
        current_sector = info.get("sector", "Other")
        if current_sector and current_sector != "Other":
            continue
        sector = _fetch_sector(ticker)
        if sector and sector != "Other":
            # 섹터만 UPDATE — save_holding 으로 스냅샷을 되쓰면, yfinance 응답을
            # 기다리는 수 초 사이에 사용자가 체결한 매매가 되돌아간다.
            if update_holding_sector(ticker, sector, uid, market=market):
                updated.append({"ticker": ticker, "sector": sector})
    return {"updated": updated, "count": len(updated)}


# ── 분석 데이터 ─────────────────────────────────────────────────────────────────

@router.get("/metrics")
def get_metrics(_auth: dict = Depends(current_user), market: str = Depends(market_param)):
    uid = _auth["uid"]
    holdings  = get_holdings(uid, market=market)
    trade_log = get_trade_log(uid, market=market)
    if not holdings and not trade_log:
        # 라벨은 계산기와 **같은 자리**에서 낸다 (portfolio_calculator:811).
        # 여기서 따로 지어내면 두 경로가 서로 다른 이름을 말하게 된다.
        from backend.services.markets import benchmark_for, get_market
        _bench = benchmark_for(market)
        _rp = realized_pnl_from_log([])
        # 베타 근거도 같은 방식이다 — 빈 보유·빈 프레임으로 정상 경로 함수를
        # 그대로 부른다. 실측으로 counted 0 · holdings 0 · value_share None 이
        # 나오는데, 그 값을 여기 적어 두면 계산이 바뀔 때 이 세 줄만 옛 답을
        # 계속 말한다.
        _bd = portfolio_beta_detail({}, pd.DataFrame(), _bench)
        # 보유가 없는 것은 **새 사용자의 정상 상태**다 (§1.3 마지막 항목).
        # 예전에는 400 을 던졌는데, 그러면 서버가 정상 상태를 클라이언트
        # 오류라고 부르고 그 사용자는 화면을 열 때마다 콘솔에 400 을 둘씩
        # 쌓는다. 같은 파일의 /holdings-detail·/sector-weights·/refresh 는
        # 이미 200 + 빈 값을 준다 — 그쪽이 맞고 여기가 틀렸다.
        #
        # 금액은 **0 이 참이다.** 아무것도 없는 사용자의 자산은 실제로 0 이지,
        # "모름" 이 아니다. 반대로 수익률·베타는 분모가 없어 계산 불가이므로
        # null 이다. 0 으로 채우면 '본전'·'시장과 같은 변동성' 이라는 단정이
        # 된다 (§1.3 (a)).
        #
        # `is_empty` 를 따로 싣는 이유: 소비자가 0 을 보고 "빈 포트폴리오" 와
        # "전량 매도해 평가액이 0" 을 구별할 수 없다. 후자는 trade_log 가
        # 있으므로 이 분기로 오지 않지만, 그 구별을 값 추론에 맡기면 나중에
        # 조건이 바뀌었을 때 조용히 틀린다.
        return {
            "is_empty":           True,
            "total_equity":       0.0,
            "stock_value":        0.0,
            "cash_value":         0.0,
            "total_cost":         0.0,
            "total_return_pct":   None,
            "today_change_pct":   None,
            "perf_1w":            None,
            "perf_1m":            None,
            "portfolio_beta":     None,
            "alpha_vs_benchmark": None,
            # VIX 는 포트폴리오와 무관한 시장 지표지만, 아무것도 없는 사용자를
            # 위해 외부 조회를 돌리지 않는다. 값이 없다는 사실만 남긴다.
            "vix":                None,
            "as_of":              None,
            "change_counted":     0,
            "change_holdings":    0,
            "change_stale":       [],
            # 정상 경로와 **같은 함수**로 낸다. 값을 손으로 적으면 그 함수가
            # 바뀔 때 여기만 옛 계약을 계속 말한다 — 빈 포트폴리오는 테스트가
            # 없어서(400→200 을 바꿔도 게이트가 통과했다) 아무도 못 본다.
            # 거래 이력이 실제로 없으므로 `[]` 다. `None` 은 "호출자가 안
            # 알려줬다" 라는 다른 뜻이고 reason 도 달라진다.
            **{
                "realized_pnl":        _rp.pnl,
                "realized_cost":       _rp.cost,
                "realized_pnl_pct":    _rp.pct,
                "realized_pnl_reason": _rp.reason,
                "realized_sales":      _rp.sales,
                "beta_counted":        _bd.counted,
                "beta_holdings":       _bd.holdings_n,
                "beta_value_share":    _bd.value_share,
                # 장 개폐는 보유와 무관하게 시장이 정한다. 빈 계정이라고
                # 시장이 닫혀 있는 것은 아니다.
                "market_open":         _market_open_flag(market),
            },
            # 벤치마크는 보유와 무관하게 시장이 정한다. 빈 포트폴리오라고
            # 라벨까지 지울 이유가 없다 — 차트 범례가 '벤치마크' 로 떨어진다.
            "benchmark":          _bench,
            "benchmark_label":    get_market(market).indices.get(_bench, _bench),
        }
    # 과거 매도 종목 포함 → 전량 매도 이후에도 수익률 계산 가능
    traded_tickers = list({
        str(tr.get("ticker", "")).upper()
        for tr in (trade_log or [])
        if str(tr.get("ticker", "")).upper() not in ("CASH", "")
    })
    _period = _period_covering_first_trade(trade_log)
    close_df = _portfolio_close_df(holdings, period=_period, ttl=300,
                                   extra_tickers=traded_tickers, market=market)
    if close_df.empty:
        raise HTTPException(status_code=400, detail="가격 데이터 없음")
    equity_curve = build_equity_curve(holdings, trade_log, close_df, market=market)

    # 1일 변동은 곡선의 위치 기반 차분(iloc[-1]-iloc[-2])이 아니라
    # 종목별 '마지막 두 실제 관측치'의 합으로 계산한다 →
    # 화면의 종목별 행 합계와 정의상 일치하고, 유령 행에 영향받지 않는다.
    _1d_tickers = [t for t in holdings if t != "CASH"]
    raw_df = (
        get_close_df(_1d_tickers, period="1mo", ttl=1800, include_market=False, fill=False)
        if _1d_tickers else pd.DataFrame()
    )
    # /holdings-detail 과 동일하게 얇은 종목을 보완한다. 빠뜨리면 그 종목만
    # daily_change 가 None 이 되어 변동액에서는 빠지고 분모에는 그대로 남아
    # 포트폴리오 변동률이 실제보다 작게 나온다.
    if _1d_tickers and not raw_df.empty:
        raw_df = _ensure_prev_close(raw_df, _1d_tickers)
    # 개장 여부로 미리 거르지 않는다. _is_market_open() 은 **미국** 하나만
    # 답하는데 보유 목록에는 시장이 섞인다 — KRX 정규장(ET 20:00~02:30)에는
    # False 라 한국 종목의 실시간이 통째로 막혔다. 반대로 미국장 시간에는
    # True 라 KRX 가 닫힌 한국 종목까지 계속 조회했다.
    # 가격이 변할 수 있는지는 _get_live_prices 가 티커별로 판단한다
    # (market_calendar.price_can_move).
    live = _get_live_prices(_1d_tickers) if _1d_tickers else {}
    # trade_log 를 **그대로** 넘긴다. `or []` 로 뭉개면 안 된다 —
    # None 은 "호출자가 안 알려줬다", [] 는 "거래 이력이 실제로 없다" 로
    # 의미가 다르고, calculate_metrics 가 그 둘을 갈라 판정한다.
    metrics = calculate_metrics(holdings, close_df, equity_curve, raw_df=raw_df,
                                live=live, market=market, trade_log=trade_log)

    # total_return_pct 를 곡선 마지막 점으로 덮어쓰던 블록을 지웠다.
    #
    # 그 보정은 "calculate_metrics 가 equity_curve 를 쓰므로 추가 입금 시
    # 왜곡된다" 는 전제에서 나왔다. **그 전제가 낡았다.** 지금은
    # portfolio_calculator:640 이 취득원가 기준으로 계산하고 곡선을 쓰지
    # 않는다 — 입금은 현금을 바꾸지 주식 평가액도 원가도 안 바꾸므로
    # 구조적으로 입금에 면역이다. 고치려던 왜곡이 사라졌는데 보정만 남았다.
    #
    # 그래서 **맞는 값이 다른 정의의 값으로 교체되고 있었다.** 곡선 마지막
    # 점은 창(window)에 의존하는 시간가중수익률이고, 원가 기준과는 기준일만
    # 다른 게 아니라 다른 양이다. 합성 이력에서 575.82 대 6.60 이 나온다.
    # 그 결과 화면의 세 숫자(총자산·원가·누적수익)로 서로를 검산할 수 없었다.
    #
    # dropna() 가드도 함께 지웠다. 그건 **곡선 값을 쓰는 동안** NaN 이 응답에
    # 새는 것을 막는 가드였다 — 곡선 값을 안 쓰면 지킬 대상이 없다. 조각만
    # 남기면 다음 사람이 왜 있는지 모른다.
    #
    # 남는 트레이드오프: 원가 기준은 **매도로 실현한 손익을 포함하지 않는다**
    # (현금에 남고 현금은 이 계산에서 빠진다). 매도가 많은 사용자에게는 누적
    # 수익이 과소 표시된다. 그 답은 실현손익 별도 필드지, 옆 두 숫자와 기준이
    # 다른 필드가 아니다.
    # `calculate_metrics` 는 프레임이 비었거나 **행이 2개 미만**이면 `{}` 를
    # 돌려준다 (portfolio_calculator:697·703). 위의 `close_df.empty` 가드는
    # 그중 앞의 절반만 막는다 — 행이 정확히 1개면 통과해서 `{}` 가 200 으로
    # 나간다.
    #
    # 그러면 화면이 **아무 말 없이 빈다.** 프론트는 `isError` 도 아니고
    # `is_empty` 도 아니라서 오류도 "보유 없음" 도 못 띄우고, 지표 바의
    # 게이트(`typeof m.total_equity === 'number'`)만 조용히 거짓이 된다.
    # 실패가 성공처럼 보이는 자리다 (§1.3).
    #
    # 바로 위 `close_df.empty` 와 같은 답을 준다 — 원인이 같기 때문이다
    # (지표를 낼 만큼의 가격 이력이 없다).
    if not metrics:
        raise HTTPException(status_code=400, detail="가격 데이터 부족")

    return metrics


@router.get("/equity-curve")
def get_equity_curve(
    benchmark: Optional[str] = "sp500",
    _auth: dict = Depends(current_user),
    market: str = Depends(market_param),
):
    uid = _auth["uid"]
    holdings  = get_holdings(uid, market=market)
    trade_log = get_trade_log(uid, market=market)
    if not holdings and not trade_log:
        # /metrics 와 같은 이유로 200 + 빈 목록이다. 곡선은 원래 레코드
        # 목록이므로 "그릴 점이 없다" 가 그대로 `[]` 다 — 별도 표식이
        # 필요 없고, /holdings-detail 이 이미 같은 모양을 쓴다.
        return []

    # 과거 매도 종목도 가격 데이터 포함 — close_df에 없으면 해당 기간 수익률이 0으로 계산됨
    traded_tickers = list({
        str(tr.get("ticker", "")).upper()
        for tr in (trade_log or [])
        if str(tr.get("ticker", "")).upper() not in ("CASH", "")
    })
    _period = _period_covering_first_trade(trade_log)
    close_df = _portfolio_close_df(holdings, period=_period, ttl=300,
                                   extra_tickers=traded_tickers, market=market)

    return_pct, holdings_by_date, initial_equity, cash_events, equity = build_return_pct_curve(
        holdings, trade_log, close_df, market=market)

    # 주식 매매 포인트 마커 (매수/매도 날짜에 점 표시용)
    trade_markers = [
        tr for tr in (trade_log or [])
        if str(tr.get("ticker", "")).upper() not in ("CASH", "")
        and tr.get("type", "") in ("ADD", "BUY", "SOLD", "SELL")
        and float(tr.get("price") or 0) > 0
    ]

    # market 을 넘겨야 비교선이 그 시장의 기준 지수가 된다 (US ^GSPC · KR ^KS11).
    # 안 넘기면 기본값 "US" 로 한국 차트에도 S&P 500 이 그려진다.
    return return_pct_to_records(return_pct, holdings_by_date, close_df, trade_markers,
                                 initial_equity, cash_events, equity, market=market)


@router.get("/holdings-detail")
def get_holdings_detail_endpoint(_auth: dict = Depends(current_user), market: str = Depends(market_param)):
    uid = _auth["uid"]
    holdings = get_holdings(uid, market=market)
    if not holdings:
        return []
    tickers = [t for t in holdings if t != "CASH"]
    # 현금만 있는 경우: 가격 조회 없이 CASH 행 직접 반환
    if not tickers:
        cash_q = float(holdings.get("CASH", {}).get("q", 0))
        return [{
            "ticker": "CASH", "sector": "Cash",
            "qty": round(cash_q, 2), "avg_cost": 1.0,
            "current_price": 1.0, "chg_pct": 0.0,
            "pnl_pct": 0.0, "pnl": 0.0,
            "market_value": round(cash_q, 2), "weight": 1.0,
        }]
    # fill=False: 실제 관측치만 담은 희소 프레임.
    # ffill 된 프레임을 쓰면 유령 행이 '마지막 종가'로 잡혀 변동률이 0%가 된다.
    # period="1mo": 연휴+공휴일이 겹쳐도 모든 종목에 실제 관측치가 2개 이상 남도록 넉넉히.
    raw_df = get_close_df(tickers, period="1mo", ttl=1800, include_market=False, fill=False)
    # 전일 종가가 없는 종목(DB에 1행만 존재)을 yfinance에서 보완
    raw_df = _ensure_prev_close(raw_df, tickers)

    # 장중에만 실시간 가격을 쓰는 것은 맞지만, **어느 장인지는 티커마다 다르다.**
    # _is_market_open() 은 미국 하나만 답해서 한국 종목이 장중 내내 종가로
    # 표시됐다 — 실측으로 삼성전자가 269,000(전일 종가) 대 259,250(실시간)
    # 으로 3.6% 과대 표시됐다. _get_live_prices 가 티커별로 거른다.
    live = _get_live_prices(tickers)

    # 희소 프레임(raw_df)에 관측치가 없는 종목을 채울 **ffill 된 종가 프레임**.
    # 안 넘기면 그 종목이 가격 ₩0 행으로 표시되고, 표 합계가 총자산과
    # 어긋난다 (실측 차이 1,500,000). 채운 행은 마지막 확정 종가를 쓰고
    # chg_pct·as_of 는 null 로 둔다 — 0.0 을 넣으면 '보합' 이 된다.
    _period = _period_covering_first_trade(get_trade_log(uid, market=market))
    close_df = _portfolio_close_df(holdings, period=_period, ttl=300, market=market)

    rows = get_holdings_detail(holdings, raw_df, live=live, fallback_df=close_df)

    # 화면용 이름을 붙인다. 한국 종목은 '034020.KS' 처럼 숫자 코드라
    # 목록만 보고는 어느 회사인지 알 수 없다.
    from backend.services.markets import name_map_for
    names = name_map_for([r["ticker"] for r in rows if r.get("ticker") != "CASH"], market)
    for r in rows:
        t = r.get("ticker")
        if t and t != "CASH":
            r["name"] = names.get(t, t)
    return rows


@router.get("/sector-weights")
def get_sector_weights(_auth: dict = Depends(current_user), market: str = Depends(market_param)):
    uid = _auth["uid"]
    holdings = get_holdings(uid, market=market)
    if not holdings:
        return {}
    tickers = [t for t in holdings if t != "CASH"]
    # holdings-detail 과 동일 캐시키 공유 (5d, include_market=False, 같은 tickers)
    close_df = _inject_live(get_close_df(tickers, period="5d", ttl=1800, include_market=False)) if tickers else None
    rows: dict[str, float] = {}
    for t, info in holdings.items():
        price = 1.0 if t == "CASH" else (
            float(close_df.iloc[-1].get(t, 0))
            if close_df is not None and t in close_df.columns else 0.0
        )
        val = price * info["q"]
        sector = info.get("sector", "Other")
        rows[sector] = rows.get(sector, 0) + val
    total = sum(rows.values())
    if total <= 0:
        return {}
    return {s: round(v / total, 4) for s, v in sorted(rows.items(), key=lambda x: -x[1])}


# ── 포트폴리오 최초 등록 ───────────────────────────────────────────────────────

@router.post("/setup")
def setup_portfolio(body: PortfolioSetupRequest, _auth: dict = Depends(current_user), market: str = Depends(market_param)):
    """포트폴리오 최초 등록 / 새로 등록.

    사용자는 "지금 이만큼 갖고 있다"를 입력하는 것이지 거래를 하는 게 아니다.
    그런데 내부 장부는 거래의 누적으로 잔고를 만든다. 그 간극을 여기서 메운다.

      ① 최초 거래일에 (매수금 합계 + 현재 현금) 을 입금한 것으로 기록한다.
         이 입금이 없으면 매수 시점마다 현금이 모자라 잔고가 음수가 된다.
      ② 각 종목을 입력한 매수일·단가로 매수 처리한다.
      ③ 결과적으로 현금은 사용자가 입력한 값과 정확히 일치한다.

    그래프와 이력에는 ①의 입금이 시작점으로 보이므로, 수익률이 "입금 이후의
    성과"로 올바르게 계산된다.
    """
    uid = _auth["uid"]

    # ── 검증: 저장을 시작하기 전에 전부 확인한다 ────────────────────────────
    cash = float(body.cash or 0)
    if cash < 0:
        raise HTTPException(status_code=400, detail="현금 잔고는 0 이상이어야 합니다")

    rows = []
    for h in body.holdings:
        t = (h.ticker or "").upper().strip()
        if not t or t == "CASH":
            raise HTTPException(status_code=400, detail="종목 코드를 확인해 주세요")
        if not (h.q > 0):
            raise HTTPException(status_code=400, detail=f"{t}: 수량은 0보다 커야 합니다")
        if not (h.price > 0):
            raise HTTPException(status_code=400, detail=f"{t}: 매수 단가를 입력해 주세요")
        d = (h.date or "").strip()[:10]
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{t}: 매수일 형식이 올바르지 않습니다")
        rows.append({"ticker": t, "q": float(h.q), "price": float(h.price), "date": d})

    if not rows and cash <= 0:
        raise HTTPException(status_code=400, detail="보유 종목이나 현금 중 하나는 입력해 주세요")

    with user_write_lock(uid):
        existing = get_holdings(uid, market=market)
        has_data = bool([k for k in existing if k != "CASH"]) or \
                   float(existing.get("CASH", {}).get("q", 0) or 0) != 0
        if has_data and not body.replace:
            raise HTTPException(
                status_code=409,
                detail="이미 등록된 포트폴리오가 있습니다. 새로 등록하려면 기존 정보를 삭제해야 합니다.",
            )
        if body.replace:
            wipe_portfolio(uid, market=market)

        invested = sum(r["q"] * r["price"] for r in rows)
        first_date = min((r["date"] for r in rows), default=None) or \
                     datetime.now().strftime("%Y-%m-%d")

        # ① 시드 입금 — 매수금 전액 + 남은 현금
        add_trade({"date": first_date, "ticker": "CASH", "type": "DEPOSIT",
                   "q": round(invested + cash, 2), "price": 1.0,
                   "memo": "포트폴리오 등록"}, uid, market=market)
        save_holding("CASH", round(invested + cash, 2), 1.0, "Cash", uid, market=market)

        # ② 종목 매수 — 매수일 순서대로 기록해 이력이 시간순으로 읽히게 한다
        for r in sorted(rows, key=lambda x: x["date"]):
            add_trade({"date": r["date"], "ticker": r["ticker"], "type": "ADD",
                       "q": r["q"], "price": r["price"], "memo": "포트폴리오 등록"}, uid,
                      market=market)
            save_holding(r["ticker"], r["q"], r["price"], "Other", uid, market=market)

        # ③ 현금은 입력값 그대로
        save_holding("CASH", round(cash, 2), 1.0, "Cash", uid, market=market)

    # 섹터는 백그라운드로 채운다 — 등록 응답을 그만큼 기다리게 할 이유가 없다.
    import threading

    def _bg_sectors():
        for r in rows:
            try:
                sec = _fetch_sector(r["ticker"])
                if sec and sec != "Other":
                    update_holding_sector(r["ticker"], sec, uid, market=market)
            except Exception as e:
                # 한 종목이 실패해도 나머지는 계속 채운다. 다만 조용히 넘기지는
                # 않는다 — 섹터가 전부 'Other' 로 남아도 화면은 정상으로 보이고,
                # 그러면 원인을 찾을 단서가 아무 데도 없다.
                logger.warning(f"섹터 자동 조회 실패 (ticker={r['ticker']}, "
                               f"uid={uid}, market={market}): {e}")
    threading.Thread(target=_bg_sectors, daemon=True).start()

    return {
        "ok": True,
        "holdings": len(rows),
        "invested": round(invested, 2),
        "cash": round(cash, 2),
        "seed_deposit": round(invested + cash, 2),
        "first_date": first_date,
    }


# ── 사용자 관리 ─────────────────────────────────────────────────────────────────
#
# GET /users (전체 사용자 목록)와 POST /users/{id} (임의 계정 생성)는 삭제했다.
# 전자는 모든 가입자의 ID·이메일을 인증 없이 노출했고, 후자는 아무나 계정을
# 만들 수 있게 했다. 계정 수명주기는 이제 Firebase Auth 와 /api/auth 가 맡는다.


# ── 개인 데이터 새로고침 ────────────────────────────────────────────────────────

@router.post("/refresh")
def refresh_portfolio(_auth: dict = Depends(current_user), market: str = Depends(market_param)):
    """
    사용자 포트폴리오 종목의 최신 가격 강제 수집.
    새로고침 버튼 클릭 시 프론트엔드에서 호출.
    """
    uid = _auth["uid"]
    holdings = get_holdings(uid, market=market)
    tickers = [t for t in holdings if t != "CASH"]
    if not tickers:
        return {"ok": True, "tickers": [], "message": "보유 종목 없음"}

    try:
        from backend.db.scheduler import refresh_user_prices
        refresh_user_prices(tickers)
        return {"ok": True, "tickers": tickers, "message": f"{len(tickers)}개 종목 가격 갱신 완료"}
    except Exception as e:
        return {"ok": False, "tickers": tickers, "message": str(e)}
