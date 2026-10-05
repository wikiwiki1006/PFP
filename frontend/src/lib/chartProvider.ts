/**
 * lib/chartProvider.ts
 * ────────────────────
 * 종목 상세 차트를 무엇으로 그릴지 — TradingView 위젯 / 기존 자체 차트(recharts).
 *
 * **임시 전환이다. 기존 차트 코드는 지우지 않았다.** 되돌리려면:
 *   · 빌드 시 `VITE_TICKER_CHART=legacy` 를 주거나
 *   · 아래 `DEFAULT_PROVIDER` 를 'legacy' 로 바꾼다.
 * 그러면 모든 종목이 예전 차트로 돌아간다.
 *
 * 한국 종목은 TradingView **위젯**으로 그릴 수 없다. 무료 위젯은 KRX 시세를
 * 표시하지 않는다 — `KRX:005930` 을 넣으면 "TradingView 에서만 제공되는
 * 심볼입니다" 가 뜨고 차트가 빈다 (2026-10-02 실제 위젯으로 확인, 접두사 없는
 * `005930` 도 같다). 거래소 라이선스 문제라 설정으로 풀 수 없다.
 * 그래서 한국은 우리가 받은 일봉을 lightweight-charts(같은 회사의 렌더링
 * 엔진)로 그린다 — `lightweightChartFor` / components/LightweightDailyChart.tsx.
 */

import type { Market } from './market'

export type ChartProvider = 'tradingview' | 'legacy'

/** 차트 위에 겹쳐 그릴 지표. 스토캐스틱은 항상 나오고(기본 화면), 이것들은
 *  사용자가 골라 더한다. 세 차트(위젯·lightweight·recharts)가 같은 상태를 쓴다. */
export const MA_PERIODS = [20, 50, 200] as const
export type MaPeriod = typeof MA_PERIODS[number]
export type ChartOverlays = { bb: boolean; ma: Record<MaPeriod, boolean> }
export const NO_OVERLAYS: ChartOverlays = { bb: false, ma: { 20: false, 50: false, 200: false } }

const DEFAULT_PROVIDER: ChartProvider = 'tradingview'

export const TICKER_CHART_PROVIDER: ChartProvider =
  import.meta.env.VITE_TICKER_CHART === 'legacy' ? 'legacy'
  : import.meta.env.VITE_TICKER_CHART === 'tradingview' ? 'tradingview'
  : DEFAULT_PROVIDER

/** 위젯으로 못 그리는 종목(한국 등)을 lightweight-charts 로 그릴지.
 *
 *  이것도 임시 전환이다. 되돌리는 법:
 *    · `VITE_KR_TICKER_CHART=legacy` → 한국만 예전 recharts 차트로
 *    · `VITE_TICKER_CHART=legacy`    → 미국·한국 모두 예전 차트로 */
const LIGHTWEIGHT_ENABLED =
  TICKER_CHART_PROVIDER !== 'legacy' && import.meta.env.VITE_KR_TICKER_CHART !== 'legacy'

export function lightweightChartFor(ticker: string | null | undefined): boolean {
  return LIGHTWEIGHT_ENABLED && !!ticker && tradingViewSymbol(ticker) == null
}

/** 차트 가격 표기에 쓸 통화(시장). 화면의 시장 설정이 아니라 **티커**로 정한다 —
 *  미국 화면에서 한국 종목을 열어도 원화로 그려야 한다 (CLAUDE.md §1.4). */
export function priceMarketOf(ticker: string): Market {
  return /\.(KS|KQ)$/i.test(ticker) || /^\^KQ11$|^\^KS11$/i.test(ticker) ? 'KR' : 'US'
}

/** 야후 티커 → TradingView 심볼. 위젯으로 그릴 수 없으면 null (→ 기존 차트).
 *
 *  미국 보통주 모양(`AAPL`, `BRK-B`)만 넘긴다. 거래소 접두사는 붙이지 않는다 —
 *  응답에 거래소 정보가 없고, 접두사 없이 넣어도 위젯이 미국 상장을 찾는다
 *  (`AAPL` 과 `NASDAQ:AAPL` 이 같은 차트를 냈다).
 *  지수(`^GSPC`)·한국(`.KS`/`.KQ`)·암호화폐(`BTC-USD`)·선물(`CL=F`)은 표기
 *  규칙이 달라 잘못된 종목이 뜰 수 있으므로 넘기지 않는다. */
export function tradingViewSymbol(ticker: string | null | undefined): string | null {
  if (TICKER_CHART_PROVIDER !== 'tradingview' || !ticker) return null
  const t = ticker.trim().toUpperCase()
  if (!/^[A-Z]{1,5}(-[A-Z])?$/.test(t)) return null
  return t.replace('-', '.')            // 야후 BRK-B → TradingView BRK.B
}
