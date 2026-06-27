"""
daily_report.py
───────────────
매일 09:00 ET: 전날 거래 손익 레포트 생성
  - 종목별 실현 손익 (매수/매도 매칭)
  - 미실현 손익 (Alpaca 현재 포지션)
  - 최대 수익 / 최대 손실 종목 + 이유
  - 매매 기준 요약
  - Telegram 발송 + report_log.json 저장
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytz

logger = logging.getLogger(__name__)

_LOG_PATH    = Path(__file__).parent / "trade_log.json"
_REPORT_PATH = Path(__file__).parent / "report_log.json"

_ET = pytz.timezone("America/New_York")

# ── 매매 기준 설명 (레포트에 고정 포함) ──────────────────────────────────────
_TRADING_CRITERIA = """
📋 LENS Trader 매매 기준
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【진입 판단 — 멀티팩터 자체 판단】
  • RSI(14) < 35  → 과매도 → 롱 진입 신호
  • RSI(14) > 65  → 과매수 → 숏 진입 신호
  • MACD 골든크로스 → 추세 상승 전환 → 롱
  • MACD 데드크로스 → 추세 하락 전환 → 숏
  • 볼린저밴드 하단 이탈 → 평균회귀 기대 → 롱
  • 볼린저밴드 상단 돌파 → 평균회귀 기대 → 숏
  • 거래량 5일평균 > 20일평균 × 1.2 → 확신 보정 +12.5%
  ※ 2개 이상 지표가 같은 방향 동의 시 진입

【포지션 크기 — 신호 강도 비례】
  • 강도 85%↑ → 포트폴리오의 25%
  • 강도 70%↑ → 15%
  • 강도 60%↑ → 10%
  • 강도 50%↑ → 5%

【손절】 진입가 기준 ±3% (롱: -3%, 숏: +3%) GTC 주문 자동 등록

【매크로 필터】 10Y-2Y 장단기 금리역전 + HY스프레드 동시 경보 시 BUY 차단
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".strip()


def _load_trades() -> list:
    if not _LOG_PATH.exists():
        return []
    try:
        return json.loads(_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _yesterday_range():
    now_et   = datetime.now(_ET)
    yday     = now_et - timedelta(days=1)
    yday_str = yday.strftime("%Y-%m-%d")
    return yday_str


def _calc_pnl(trades: list, target_date: str) -> dict:
    """
    target_date(YYYY-MM-DD) 당일 체결된 거래로 종목별 손익 계산.
    FIFO 방식: BUY를 큐에 쌓고 SELL이 오면 매칭.
    """
    day_trades = [
        t for t in trades
        if t.get("timestamp", "").startswith(target_date)
    ]

    # 종목별 FIFO 큐
    buy_queue  = defaultdict(list)   # ticker → [(price, qty, reason), ...]
    pnl_map    = defaultdict(float)
    reason_map = {}

    for t in sorted(day_trades, key=lambda x: x["timestamp"]):
        ticker   = t["ticker"]
        action   = t["action"]
        price    = float(t.get("price", 0))
        qty      = int(t.get("qty", 0))
        strategy = t.get("strategy", "")
        reason   = t.get("reason", "")

        if action == "BUY":
            buy_queue[ticker].append((price, qty, strategy, reason))
            reason_map[ticker] = reason

        elif "숏진입" in action:
            # 숏: 나중에 높은 가격이면 손실
            buy_queue[ticker].append(("short", price, qty, strategy, reason))
            reason_map[ticker] = reason

        elif "롱청산" in action or action == "SELL":
            # 롱 포지션 청산 — FIFO 매칭
            remaining = qty
            while remaining > 0 and buy_queue[ticker]:
                entry = buy_queue[ticker][0]
                if entry[0] == "short":
                    buy_queue[ticker].pop(0)
                    continue
                ep, eq, est, er = entry
                matched = min(remaining, eq)
                pnl_map[ticker] += (price - ep) * matched
                remaining -= matched
                if matched >= eq:
                    buy_queue[ticker].pop(0)
                else:
                    buy_queue[ticker][0] = (ep, eq - matched, est, er)

        elif "숏청산" in action:
            remaining = qty
            while remaining > 0 and buy_queue[ticker]:
                entry = buy_queue[ticker][0]
                if entry[0] != "short":
                    buy_queue[ticker].pop(0)
                    continue
                _, ep, eq, est, er = entry
                matched = min(remaining, eq)
                pnl_map[ticker] += (ep - price) * matched  # 숏: 하락이 수익
                remaining -= matched
                if matched >= eq:
                    buy_queue[ticker].pop(0)
                else:
                    buy_queue[ticker][0] = ("short", ep, eq - matched, est, er)

    return dict(pnl_map), reason_map, day_trades


def generate(alpaca_client=None) -> str:
    """레포트 생성 → str 반환 (Telegram 발송용), report_log.json 저장."""
    logger.info("[REPORT] 일일 레포트 생성 시작")

    trades         = _load_trades()
    target_date    = _yesterday_range()
    pnl_map, reason_map, day_trades = _calc_pnl(trades, target_date)

    # ── 미실현 손익 (Alpaca 현재 포지션) ───────────────────────────────────
    unrealized = {}
    if alpaca_client:
        try:
            positions = alpaca_client.get_all_positions()
            for p in positions:
                unrealized[p.symbol] = {
                    "unreal_pnl": float(p.unrealized_pl),
                    "unreal_pct": float(p.unrealized_plpc) * 100,
                    "qty":        float(p.qty),
                }
        except Exception as e:
            logger.warning(f"[REPORT] 포지션 조회 실패: {e}")

    # ── 총 실현 손익 ───────────────────────────────────────────────────────
    total_realized   = sum(pnl_map.values())
    total_unrealized = sum(v["unreal_pnl"] for v in unrealized.values())

    # ── 최대 수익/손실 종목 ────────────────────────────────────────────────
    sorted_pnl = sorted(pnl_map.items(), key=lambda x: x[1], reverse=True)
    winners    = [(tk, pnl) for tk, pnl in sorted_pnl if pnl > 0][:3]
    losers     = [(tk, pnl) for tk, pnl in sorted_pnl if pnl < 0][-3:][::-1]

    # ── 메시지 조립 ────────────────────────────────────────────────────────
    lines = []
    lines.append(f"📊 LENS Trader 일일 레포트 [{target_date}]")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    lines.append(f"\n💰 실현 손익: ${total_realized:+,.2f}")
    lines.append(f"📈 미실현 손익 (현재 포지션): ${total_unrealized:+,.2f}")
    lines.append(f"📦 당일 거래 건수: {len(day_trades)}건")

    if winners:
        lines.append("\n🏆 최대 수익 종목")
        for tk, pnl in winners:
            reason_short = reason_map.get(tk, "").replace("[매수 근거] ", "").split("|")[0].strip()
            lines.append(f"  ✅ {tk:6s}  ${pnl:+,.2f}  ← {reason_short}")

    if losers:
        lines.append("\n💥 최대 손실 종목")
        for tk, pnl in losers:
            reason_short = reason_map.get(tk, "").replace("[매수 근거] ", "").split("|")[0].strip()
            lines.append(f"  ❌ {tk:6s}  ${pnl:+,.2f}  ← {reason_short}")

    if not winners and not losers:
        lines.append("\n거래 없음 (장 마감 또는 신호 없음)")

    if unrealized:
        lines.append("\n📌 현재 보유 포지션")
        for tk, v in sorted(unrealized.items(), key=lambda x: x[1]["unreal_pnl"], reverse=True):
            side = "롱" if v["qty"] > 0 else "숏"
            lines.append(f"  {tk:6s} {side}  ${v['unreal_pnl']:+,.2f} ({v['unreal_pct']:+.2f}%)")

    lines.append("")
    lines.append(_TRADING_CRITERIA)

    msg = "\n".join(lines)

    # ── report_log.json 저장 ──────────────────────────────────────────────
    report_entry = {
        "date":             target_date,
        "generated_at":     datetime.now().isoformat(timespec="seconds"),
        "total_realized":   round(total_realized, 2),
        "total_unrealized": round(total_unrealized, 2),
        "trade_count":      len(day_trades),
        "winners":          [{"ticker": tk, "pnl": round(pnl, 2),
                              "reason": reason_map.get(tk, "")} for tk, pnl in winners],
        "losers":           [{"ticker": tk, "pnl": round(pnl, 2),
                              "reason": reason_map.get(tk, "")} for tk, pnl in losers],
        "positions":        unrealized,
        "message":          msg,
    }

    reports = []
    if _REPORT_PATH.exists():
        try:
            reports = json.loads(_REPORT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    reports.append(report_entry)
    _REPORT_PATH.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(f"[REPORT] 완료 — 실현 ${total_realized:+,.2f} | 저장: {_REPORT_PATH}")
    return msg
