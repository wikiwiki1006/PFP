/**
 * lib/demoData.ts
 * ───────────────
 * 비로그인 미리보기용 예시 데이터.
 *
 * 실제 API 를 부르지 않고 화면 형태만 보여주기 위한 것이다. LockedPreview 가
 * 흐림 처리를 하므로 숫자가 읽히지는 않지만, 차트 곡선과 표 길이가 그럴듯해야
 * "어떤 기능인지" 전달된다. 그래서 값은 임의지만 형태는 현실적으로 만들었다.
 *
 * 여기 값이 실제 시세로 오해되면 안 되므로, 어떤 코드도 로그인 상태에서
 * 이 데이터를 쓰지 않는다 — 호출부는 반드시 `!isAuthed` 조건 아래에서만 쓴다.
 *
 * **리포트는 예시를 만들지 않는다.** 종목·산업 리포트, 데일리 브리프,
 * 시장 시나리오, 과거 이력은 로그인 전에는 아무것도 보여주지 않고 로그인을
 * 요구한다. 여기 있는 건 포트폴리오 화면의 형태를 보여주기 위한 것뿐이다.
 */
import type {
  PortfolioMetrics, EquityCurvePoint, HoldingDetail, HoldingsMap,
  SectorWeights, NewsItem, EarningsEvent,
} from '@/types'
import { getMarket } from './market'

export const DEMO_METRICS: PortfolioMetrics = {
  total_equity:     128_450,
  stock_value:      118_197,   // 보유 표의 증권 6종 합
  cash_value:        10_253,   // 표의 CASH 행
  // 아래 보유 표에서 계산해 넣었다. 한국 데모가 같은 자리에서 틀려 있었고
  // (집계가 표와 안 맞았다) 미국 쪽도 같았다 — 표 안쪽은 일관된데
  // (stock_value - total_cost == pnl 합) 집계 셋이 표와 무관한 값이었다.
  //   total_cost        104,200 → 102,069   (2,131 차이)
  //   total_return_pct    23.27% →   15.80%
  //   today_change_pct     1.06% →    0.69%  (비중가중)
  total_cost:       102_069,
  total_return_pct: 15.80,
  today_change_pct: 0.69,
  portfolio_beta:   1.14,
  vix:              16.8,
  perf_1w:          2.41,
  perf_1m:          5.83,
  alpha_vs_benchmark: 4.12,
  benchmark:        '^GSPC',
  benchmark_label:  'S&P 500',
  as_of:            null,
  market_open:      false,
  // 일변동 집계 근거. 데모는 전 종목이 반영된 상태로 둔다 — 미리보기에서
  // "일부만 반영" 경고가 뜨면 방문자는 그게 데모의 한계인지 서비스의 상태인지
  // 구분할 수 없다. DEMO_HOLDINGS_DETAIL 의 CASH 제외 6종목과 맞춘다.
  change_counted:   6,
  change_holdings:  6,
  change_stale:     [],
}

export const DEMO_HOLDINGS_DETAIL: HoldingDetail[] = [
  { ticker: 'AAPL', qty: 120, avg_cost: 182.40, current_price: 214.60, market_value: 25_752,
    pnl: 3_864,  pnl_pct: 17.65, sector: 'Technology',        weight: 20.0, chg_pct:  0.84 },
  { ticker: 'NVDA', qty:  85, avg_cost: 118.20, current_price: 168.90, market_value: 14_357,
    pnl: 4_310,  pnl_pct: 42.89, sector: 'Technology',        weight: 11.2, chg_pct:  2.31 },
  { ticker: 'MSFT', qty:  55, avg_cost: 392.10, current_price: 441.75, market_value: 24_296,
    pnl: 2_731,  pnl_pct: 12.66, sector: 'Technology',        weight: 18.9, chg_pct: -0.42 },
  { ticker: 'TSLA', qty:  70, avg_cost: 241.80, current_price: 262.35, market_value: 18_365,
    pnl: 1_439,  pnl_pct:  8.50, sector: 'Consumer Cyclical', weight: 14.3, chg_pct:  1.77 },
  { ticker: 'SPY',  qty:  40, avg_cost: 512.30, current_price: 587.20, market_value: 23_488,
    pnl: 2_996,  pnl_pct: 14.62, sector: 'ETF',               weight: 18.3, chg_pct:  0.36 },
  { ticker: 'JEPQ', qty: 210, avg_cost:  53.10, current_price:  56.85, market_value: 11_939,
    pnl:   788,  pnl_pct:  7.06, sector: 'ETF',               weight:  9.3, chg_pct:  0.21 },
  { ticker: 'CASH', qty: 10_253, avg_cost: 1, current_price: 1, market_value: 10_253,
    pnl: 0, pnl_pct: 0, sector: 'Cash', weight: 8.0, chg_pct: 0 },
]

export const DEMO_HOLDINGS_RAW: HoldingsMap = {
  AAPL: { q: 120,    avg: 182.40, sector: 'Technology' },
  NVDA: { q:  85,    avg: 118.20, sector: 'Technology' },
  MSFT: { q:  55,    avg: 392.10, sector: 'Technology' },
  TSLA: { q:  70,    avg: 241.80, sector: 'Consumer Cyclical' },
  SPY:  { q:  40,    avg: 512.30, sector: 'ETF' },
  JEPQ: { q: 210,    avg:  53.10, sector: 'ETF' },
  CASH: { q: 10_253, avg: 1,      sector: 'Cash' },
}

export const DEMO_SECTOR_WEIGHTS: SectorWeights = {
  Technology:         50.1,
  ETF:                27.6,
  'Consumer Cyclical': 14.3,
  Cash:                8.0,
}

/** 2년치 주간 곡선 — 완만한 우상향에 조정 구간을 섞어 실제처럼 보이게 한다.
 *
 *  **서버 응답과 같은 모양이어야 한다.** 예전에는 `{ value, benchmark_value }`
 *  를 만들었는데 서버는 `{ port, benchmark_pct, total_equity, ... }` 를 준다.
 *  AlphaTerminal 은 서버 쪽 키를 읽으므로 비로그인 방문자의 미리보기
 *  자산곡선이 통째로 비어 있었다 — firstOf('port') 가 전 포인트에서 null 이라
 *  선이 그려지지 않았다.
 *
 *  데모 상수는 타입 선언이 아니라 **실제 응답**을 따라야 한다. 타입이 틀리면
 *  데모가 그 틀린 모양을 성실히 따라가고, 그게 화면에서만 드러난다. */
function makeCurve(finalEquity: number): EquityCurvePoint[] {
  // 결정적 유사난수 — 렌더마다 곡선이 달라지지 않도록 시드를 고정한다.
  let seed = 42
  const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648 }

  const vs: number[] = []
  const bs: number[] = []
  let v = 1, b = 1
  for (let i = 0; i < 105; i++) {
    // 20~32주 구간에 조정을 넣어 곡선이 밋밋하지 않게 한다
    const drawdown = i > 20 && i < 32 ? -0.004 : 0
    v *= 1 + drawdown + (rnd() - 0.42) * 0.022
    b *= 1 + drawdown * 0.7 + (rnd() - 0.44) * 0.016
    vs.push(v); bs.push(b)
  }

  // 마지막 점을 선언된 총 자산에 맞춘다. 배율은 `port`(누적 수익률)를 바꾸지
  // 않는다 — v 를 통째로 곱해도 v/v0 가 같기 때문이다. 곡선이 끝나는 자리와
  // 상단 "총 자산" 이 다른 숫자를 말하면 미리보기가 자기와 안 맞는다.
  const scale = finalEquity / vs[vs.length - 1]
  const startDate = new Date('2024-08-01T00:00:00Z')

  return vs.map((vi, i) => ({
    date:          new Date(startDate.getTime() + i * 7 * 86400_000).toISOString().slice(0, 10),
    // 서버는 금액이 아니라 **누적 수익률(%)** 을 준다.
    port:          +((vi / vs[0] - 1) * 100).toFixed(2),
    benchmark_pct: +((bs[i] / bs[0] - 1) * 100).toFixed(2),
    total_equity:  Math.round(vi * scale),
    cash_flow:     null,
    trades:        [],
    holdings:      [],
  }))
}

export const DEMO_EQUITY_CURVE: EquityCurvePoint[] = makeCurve(128_450)



export const DEMO_EARNINGS: EarningsEvent[] = [
  { ticker: 'AAPL', earn_date: '2026-10-30', div_date: '2026-11-07', div_yield: '0.42%' },
  { ticker: 'NVDA', earn_date: '2026-11-19', div_date: '2026-12-04', div_yield: '0.03%' },
  { ticker: 'MSFT', earn_date: '2026-10-28', div_date: '2026-11-20', div_yield: '0.71%' },
  { ticker: 'TSLA', earn_date: '2026-10-22', div_date: '—',          div_yield: '—' },
  { ticker: 'SPY',  earn_date: '—',          div_date: '2026-09-19', div_yield: '1.24%' },
  { ticker: 'JEPQ', earn_date: '—',          div_date: '2026-09-02', div_yield: '9.18%' },
]





export const DEMO_ANALYST_FEEDBACK = {
  feedback: [
    '## 포트폴리오 진단',
    '',
    '기술주 비중이 50%를 넘어 섹터 집중도가 높습니다. AAPL·NVDA·MSFT 세 종목의',
    '상관계수가 0.7 이상으로 형성돼 있어, 금리 충격이 왔을 때 분산 효과를 기대하기',
    '어려운 구조입니다.',
    '',
    '### 주요 관찰',
    '',
    '- **베타 1.14** — 시장 대비 변동성이 14% 큽니다. 현재 VIX 16.8 구간에서는 부담이 크지 않으나, 20을 넘어서면 낙폭이 확대될 수 있습니다.',
    '- **NVDA 미실현 수익률 42.9%** — 비중이 11.2%로 관리 범위 안에 있습니다. 일부 이익 실현으로 현금 비중을 높이는 선택지를 고려할 만합니다.',
    '- **현금 8.0%** — 조정 시 대응 여력이 제한적입니다. 12~15% 수준을 권장합니다.',
    '',
    '### 제안',
    '',
    '방어 섹터(헬스케어·필수소비재) 편입으로 기술주 편중을 완화하면, 기대수익을 크게',
    '희생하지 않으면서 최대낙폭을 줄일 수 있습니다.',
  ].join('\n'),
  metrics_snapshot: DEMO_METRICS,
}

export const DEMO_NEWS: NewsItem[] = [
  { ticker: 'NVDA',  headline: '엔비디아, 차세대 데이터센터 GPU 공급 계약 확대 — 하이퍼스케일러 수요 지속',
    url: '#', datetime: 1_755_000_000 },
  { ticker: 'AAPL',  headline: '애플 서비스 부문 매출 사상 최대치 경신, 구독 생태계 성장 가속',
    url: '#', datetime: 1_754_900_000 },
  { ticker: 'MACRO', headline: '연준 위원 발언 — "인플레이션 둔화 확인되면 완화 사이클 지속 가능"',
    url: '#', datetime: 1_754_820_000 },
  { ticker: 'TSLA',  headline: '테슬라 에너지 저장 사업 분기 최대 실적, 자동차 부문 마진 압박은 지속',
    url: '#', datetime: 1_754_700_000 },
  { ticker: 'MSFT',  headline: '마이크로소프트 클라우드 매출 성장률 시장 기대치 상회',
    url: '#', datetime: 1_754_600_000 },
]


/* ── 한국 데모 ────────────────────────────────────────────────────────────────
 *
 * 예시 데이터가 시장과 무관한 상수 하나였다. 통화 포맷터만 시장을 따라가서
 * 한국 탭에서 이렇게 보였다 (익명 /terminal 실측):
 *
 *     총 자산 ₩128,450   ← 달러 규모 숫자에 원화 기호만 붙은 것
 *     베타 (S&P 500 대비)
 *     보유에 SPY · JEPQ
 *
 * `LockedPreview` 의 흐림이 덮어서 심각도는 낮았지만, 미리보기는 **서비스가
 * 무엇을 하는지 보여 주는 화면**이다. 한국 사용자에게 미국 종목과 달러
 * 규모를 보여 주면 그 화면이 하는 일을 잘못 말한다.
 *
 * 금액은 실제 한국 개인 계좌 규모(1.7억)로 두었다. 종목·평단은 실재 종목의
 * 대략적인 값이고, 미리보기용 예시임은 LockedPreview 가 밝힌다.
 */
/* 숫자는 아래 보유 표에서 **계산해 넣었다.** 데모라도 불변식은 지킨다:
 *
 *   total_equity   == 증권 평가액 합 + 현금        170,895,000
 *   total_cost     == qty × avg_cost 합             71,000,000
 *   total_return_pct == 증권/원가 - 1                  121.68%
 *   today_change_pct == 비중가중 chg_pct                 0.07%
 *   change_counted == change_holdings == CASH 제외 종목 수   4
 *
 * 처음에는 이 값들을 눈대중으로 적고 `...DEMO_METRICS` 로 나머지를 상속했다.
 * 그 결과 총 자산이 표와 1,505,000 어긋나고 원가가 두 배였으며, 수익률·
 * 일변동·베타·알파가 전부 **미국 데이터에서 나온 수치**였다. 이 두 필드를
 * 넣는 목적이 "소비자가 검산할 수 있게" 인데 데모가 그 검산을 통과 못 하면,
 * 나중에 누가 자기 계산이 틀렸다고 읽는다. 흐림 뒤라 신호도 없다 (§1.3).
 */
const DEMO_METRICS_KR: PortfolioMetrics = {
  ...DEMO_METRICS,
  total_equity:      170_895_000,
  stock_value:       157_395_000,   // 보유 표의 증권 4종 합
  cash_value:         13_500_000,   // 표의 CASH 행
  total_cost:         71_000_000,
  total_return_pct:      121.68,
  today_change_pct:        0.07,
  portfolio_beta:          0.92,   // 코스피 대비
  alpha_vs_benchmark:      6.40,
  perf_1w:                 1.80,
  perf_1m:                 4.20,
  // 표는 CASH 제외 4종목이다. 미국 데모는 6종목이라 상속하면 표와 어긋난다.
  change_counted:             4,
  change_holdings:            4,
  benchmark:        '^KS11',
  benchmark_label:  '코스피',
}

const DEMO_HOLDINGS_DETAIL_KR: HoldingDetail[] = [
  { ticker: '005930.KS', name: '삼성전자',   qty: 200, avg_cost: 71_900,  current_price: 260_250,
    market_value: 52_050_000, pnl: 37_670_000, pnl_pct: 261.96, sector: 'Technology',        weight: 30.5, chg_pct: -0.19 },
  { ticker: '000660.KS', name: 'SK하이닉스', qty:  20, avg_cost: 245_000, current_price: 1_807_500,
    market_value: 36_150_000, pnl: 31_250_000, pnl_pct: 637.76, sector: 'Technology',        weight: 21.2, chg_pct:  1.24 },
  { ticker: '005380.KS', name: '현대차',     qty: 120, avg_cost: 198_500, current_price: 241_000,
    market_value: 28_920_000, pnl:  5_100_000, pnl_pct:  21.41, sector: 'Consumer Cyclical', weight: 16.9, chg_pct:  0.62 },
  { ticker: '035420.KS', name: 'NAVER',      qty: 150, avg_cost: 186_000, current_price: 268_500,
    market_value: 40_275_000, pnl: 12_375_000, pnl_pct:  44.35, sector: 'Communication',     weight: 23.6, chg_pct: -1.03 },
  { ticker: 'CASH', qty: 13_500_000, avg_cost: 1, current_price: 1, market_value: 13_500_000,
    pnl: 0, pnl_pct: 0, sector: 'Cash', weight: 7.9, chg_pct: 0 },
]

const DEMO_HOLDINGS_RAW_KR: HoldingsMap = {
  '005930.KS': { q: 200,        avg:  71_900, sector: 'Technology' },
  '000660.KS': { q:  20,        avg: 245_000, sector: 'Technology' },
  '005380.KS': { q: 120,        avg: 198_500, sector: 'Consumer Cyclical' },
  '035420.KS': { q: 150,        avg: 186_000, sector: 'Communication' },
  CASH:        { q: 13_500_000, avg: 1,       sector: 'Cash' },
}

const DEMO_SECTOR_WEIGHTS_KR: SectorWeights = {
  Technology:          51.7,   // 삼성전자 30.5 + SK하이닉스 21.2
  Communication:       23.6,
  'Consumer Cyclical': 16.9,
  Cash:                 7.9,
}

/* ── 시장별 선택자 ────────────────────────────────────────────────────────────
 *
 * 호출 시점에 `getMarket()` 을 읽는다. 모듈 최상위에서 한 번 고르면 시장
 * 전환이 전체 새로고침을 유지하는 동안만 맞고, 그 동작이 바뀌면 조용히
 * 틀려진다 — 미리보기라서 아무도 안 눈치챈다.
 */
export const demoMetrics        = (): PortfolioMetrics => getMarket() === 'KR' ? DEMO_METRICS_KR : DEMO_METRICS
export const demoHoldingsDetail = (): HoldingDetail[]  => getMarket() === 'KR' ? DEMO_HOLDINGS_DETAIL_KR : DEMO_HOLDINGS_DETAIL
export const demoHoldingsRaw    = (): HoldingsMap      => getMarket() === 'KR' ? DEMO_HOLDINGS_RAW_KR : DEMO_HOLDINGS_RAW
export const demoSectorWeights  = (): SectorWeights    => getMarket() === 'KR' ? DEMO_SECTOR_WEIGHTS_KR : DEMO_SECTOR_WEIGHTS

/* ── 나머지 셋도 시장을 가른다 ────────────────────────────────────────────────
 *
 * 보유 표는 갈리는데 **바로 아래 실적/배당 표는 미국 종목**이었다. 한 화면
 * 안에서 한쪽은 갈리고 한쪽은 안 갈리면 눈으로 보면 정상으로 읽힌다 —
 * 티커가 여섯 개나 있어도 "이 표는 원래 이런가 보다" 가 된다. 테스트 창이
 * G3(한국 화면에 미국 지수 문자열 없음)를 켜면서 찾았다.
 *
 * 곡선은 `total_equity` 가 달러 스케일이었다. 화면이 원화 기호만 붙이므로
 * 툴팁에 ₩128,450 같은 값이 뜬다 — DEMO_METRICS 가 같은 이유로 틀렸던 자리다.
 */
const DEMO_EQUITY_CURVE_KR: EquityCurvePoint[] = makeCurve(170_895_000)

const DEMO_EARNINGS_KR: EarningsEvent[] = [
  { ticker: '005930.KS', earn_date: '2026-10-29', div_date: '2026-11-14', div_yield: '0.56%' },
  { ticker: '000660.KS', earn_date: '2026-10-24', div_date: '2026-12-30', div_yield: '0.08%' },
  { ticker: '005380.KS', earn_date: '2026-10-23', div_date: '2026-12-30', div_yield: '4.71%' },
  { ticker: '035420.KS', earn_date: '2026-11-06', div_date: '2026-12-30', div_yield: '0.34%' },
]

const DEMO_NEWS_KR: NewsItem[] = [
  { ticker: '000660.KS', headline: 'SK하이닉스, HBM 증설 투자 확대 — 고대역폭 메모리 공급 부족 지속',
    url: '#', datetime: 1_755_000_000 },
  { ticker: '005930.KS', headline: '삼성전자 파운드리 가동률 회복, 하반기 수익성 개선 전망',
    url: '#', datetime: 1_754_900_000 },
  { ticker: 'MACRO',     headline: '한국은행 기준금리 동결 — "물가 둔화 흐름 확인 필요"',
    url: '#', datetime: 1_754_820_000 },
  { ticker: '005380.KS', headline: '현대차 미국 공장 증산, 전기차 라인 가동률 상향',
    url: '#', datetime: 1_754_700_000 },
]

export const demoEquityCurve = (): EquityCurvePoint[] => getMarket() === 'KR' ? DEMO_EQUITY_CURVE_KR : DEMO_EQUITY_CURVE
export const demoEarnings    = (): EarningsEvent[]    => getMarket() === 'KR' ? DEMO_EARNINGS_KR : DEMO_EARNINGS
export const demoNews        = (): NewsItem[]         => getMarket() === 'KR' ? DEMO_NEWS_KR : DEMO_NEWS
