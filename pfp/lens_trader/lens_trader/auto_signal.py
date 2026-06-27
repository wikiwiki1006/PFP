"""
auto_signal.py
──────────────
멀티팩터 자체판단 엔진 (RSI + MACD + BB + 거래량) 기반으로 신호 생성.
타점 시그널(scan_universe_with_targets)도 참고하지만 의존하지 않는다.
두 엔진의 결과를 합산해 최종 BUY/SELL 결정.
"""

import sys
import logging

import config

logger = logging.getLogger(__name__)


def _merge_signals(multi: dict, timing: dict) -> tuple[list, list]:
    """
    멀티팩터 + 타점 시그널 결과를 합산.
    같은 종목이 두 엔진 모두에서 같은 방향이면 strength 보정.
    """
    def index_by_ticker(picks):
        return {c["ticker"]: c for c in picks}

    m_long  = index_by_ticker(multi["long_picks"])
    m_short = index_by_ticker(multi["short_picks"])
    t_long  = index_by_ticker(timing["long_picks"])
    t_short = index_by_ticker(timing["short_picks"])

    long_out, short_out = [], []

    # 멀티팩터 롱 기준
    for tk, mc in m_long.items():
        s = mc["strength"]
        if tk in t_long:
            s = min(s + 0.15, 1.0)  # 타점 시그널도 동의 → 확신 보정
        long_out.append({**mc, "strength": s, "strategy": f"멀티팩터({mc['reason']})"})

    # 타점 시그널 롱 중 멀티팩터에 없는 것만 추가 (낮은 확신)
    for tk, tc in t_long.items():
        if tk not in m_long and tk not in m_short:
            long_out.append({
                **tc,
                "strength": tc.get("strength", 0.5) * 0.7,  # 타점 단독 → 30% 할인
                "strategy": f"타점({tc.get('method','BB')})",
            })

    # 멀티팩터 숏 기준
    for tk, mc in m_short.items():
        s = mc["strength"]
        if tk in t_short:
            s = min(s + 0.15, 1.0)
        short_out.append({**mc, "strength": s, "strategy": f"멀티팩터({mc['reason']})"})

    # 타점 시그널 숏 중 멀티팩터에 없는 것만 추가
    for tk, tc in t_short.items():
        if tk not in m_short and tk not in m_long:
            short_out.append({
                **tc,
                "strength": tc.get("strength", 0.5) * 0.7,
                "strategy": f"타점({tc.get('method','BB')})",
            })

    # 롱/숏 충돌 종목 제거
    conflict = set(k for k in [c["ticker"] for c in long_out]) & \
               set(k for k in [c["ticker"] for c in short_out])
    long_out  = [c for c in long_out  if c["ticker"] not in conflict]
    short_out = [c for c in short_out if c["ticker"] not in conflict]

    # 강도 내림차순 정렬
    long_out.sort(key=lambda x: x["strength"], reverse=True)
    short_out.sort(key=lambda x: x["strength"], reverse=True)

    return long_out[:15], short_out[:15]


def run_auto_scan(alpaca_client=None) -> bool:
    """
    멀티팩터 + 타점 시그널 합산해 signals.json 갱신.
    시장이 닫혀 있으면 스킵.
    """
    if alpaca_client and not alpaca_client.is_market_open():
        logger.info("[AUTO] 시장 마감 중 — 자동 스캔 스킵")
        return False

    if config.PFP_PATH not in sys.path:
        sys.path.insert(0, config.PFP_PATH)

    try:
        import yfinance as yf
        from trading_signals import (
            SP500_NASDAQ_UNIVERSE,
            scan_universe_with_targets,
            multi_factor_scan,
            fetch_macro_doom_indicators,
            evaluate_doom_radar,
        )
        from lens_exporter import export_signals
    except ImportError as e:
        logger.error(f"[AUTO] 임포트 실패 (PFP_PATH 확인): {e}")
        return False

    universe = sorted(set(SP500_NASDAQ_UNIVERSE + config.AUTO_WATCHLIST))
    logger.info(f"[AUTO] 멀티팩터+타점 통합 스캔 시작 — {len(universe)}종목")

    try:
        data = yf.download(universe, period="6mo", progress=False, auto_adjust=True)
    except Exception as e:
        logger.error(f"[AUTO] yfinance 다운로드 실패: {e}")
        return False

    if data.empty:
        logger.warning("[AUTO] 데이터 없음 — 스캔 중단")
        return False

    # MultiIndex 처리 (단일 종목 vs 다수)
    if hasattr(data.columns, 'levels'):
        price_df  = data["Close"].ffill()
        volume_df = data["Volume"].ffill() if "Volume" in data.columns.get_level_values(0) else None
    else:
        price_df  = data[["Close"]].ffill()
        price_df.columns = universe[:1]
        volume_df = None

    try:
        # ① 멀티팩터 자체판단 (RSI+MACD+BB+거래량)
        multi_result = multi_factor_scan(price_df, volume_df, top_n=15)

        # ② 타점 시그널 (보조 참고용)
        timing_result = scan_universe_with_targets(price_df, volume_df, top_n=10)

        # ③ 합산
        long_picks, short_picks = _merge_signals(multi_result, timing_result)

        # ④ 매크로 doom radar
        macro = fetch_macro_doom_indicators()
        doom  = evaluate_doom_radar(macro["rate_spread"], macro["hy_spread"])

    except Exception as e:
        logger.error(f"[AUTO] 신호 생성 실패: {e}", exc_info=True)
        return False

    signals = []
    for c in long_picks:
        signals.append({
            "ticker":   c["ticker"],
            "action":   "BUY",
            "strategy": c.get("strategy", "멀티팩터"),
            "strength": round(c["strength"], 3),
            "price":    c.get("entry", 0),
            "reason":   c.get("reason", ""),
        })
    for c in short_picks:
        signals.append({
            "ticker":   c["ticker"],
            "action":   "SELL",
            "strategy": c.get("strategy", "멀티팩터"),
            "strength": round(c["strength"], 3),
            "price":    c.get("entry", 0),
            "reason":   c.get("reason", ""),
        })

    export_signals(
        signals=signals,
        macro_block=doom["is_doom"],
        output_path=config.LENS_SIGNALS_PATH,
    )

    logger.info(
        f"[AUTO] 스캔 완료 — BUY {len(long_picks)}개 / SELL {len(short_picks)}개 "
        f"| 멀티팩터 {multi_result['scanned']}종목 검토 "
        f"| doom={'🚨 경보' if doom['is_doom'] else '🟢 정상'}"
    )
    return True
