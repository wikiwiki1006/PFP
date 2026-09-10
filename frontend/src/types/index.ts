// Portfolio Types
export interface PortfolioMetrics {
  total_equity: number
  total_cost: number
  total_return_pct: number
  today_change_val: number
  today_change_pct: number
  portfolio_beta: number
  vix: number
  /** 포트폴리오가 해당 기간보다 짧으면 null (계산 불가와 보합을 구분) */
  perf_1w: number | null
  perf_1m: number | null
  alpha_vs_sp500: number
  /** 일변동률의 기준 거래일 (YYYY-MM-DD). 장 외에는 마지막 확정 거래일. */
  as_of?: string | null
  /** 미국 증시 개장 여부 — true면 today_change_*가 실시간 값이다. */
  market_open?: boolean
}

export interface EquityCurvePoint {
  date: string
  value: number
  benchmark_value: number | null
}

export interface Holding {
  q: number
  /** 화면용 종목명 (서버가 채운다). */
  name?: string
  avg: number
  sector: string
  div?: number
}

export interface HoldingsMap {
  [ticker: string]: Holding
}

export interface HoldingDetail {
  ticker: string
  /** 화면용 종목명. 한국 종목은 코드만으로 회사를 알 수 없어 서버가 채워 준다. */
  name?: string
  qty: number
  avg_cost: number
  current_price: number
  market_value: number
  pnl: number
  pnl_pct: number
  sector: string
  weight: number
  /** 일변동률(%). null = 관측치 부족으로 계산 불가 (0%와 구분해야 함). */
  chg_pct: number | null
  /** 이 값의 기준 거래일 (YYYY-MM-DD). */
  as_of?: string | null
  /** 장중 실시간 가격으로 계산됐는지. */
  is_live?: boolean
}

export interface SectorWeights {
  [sector: string]: number
}

export interface Trade {
  id?: number
  date: string
  ticker: string
  type: 'ADD' | 'SOLD' | 'UPDATE' | 'BUY' | 'SELL'
  q: number
  price: number | null
  memo?: string | null
}

export interface TradeForm {
  ticker: string
  type: 'BUY' | 'SELL'
  q: number
  price: number
  memo?: string
  date?: string   // YYYY-MM-DD, 없으면 서버가 오늘로 처리
}

// Market Types
export interface TickerSnapshot {
  price: number
  change_1d: number
  change_1d_pct: number
}

export interface MarketSnapshot {
  prices: { [ticker: string]: TickerSnapshot }
  timestamp: string
}

export interface SectorData {
  sector: string
  etf: string
  price: number
  change_1d_pct: number
  change_1w_pct: number
  change_1m_pct: number | null
  change_3m_pct?: number | null
  change_6m_pct?: number
}

export interface MacroData {
  fed_rate: number
  unemployment: number
  cpi: number
  gdp: number
  y10?: number
  y2?: number
  t10y2y: number
  bamlh0a0hym2: number
  source?: string
}

export interface NewsItem {
  ticker: string
  headline: string
  url: string
  datetime: number
}


export interface EarningsEvent {
  ticker: string
  earn_date: string
  div_date: string
  div_yield: string
}

export interface CorrelationMatrix {
  tickers: string[]
  labels?: string[]
  matrix: number[][]
}

// Signals Types
export interface SignalPick {
  ticker: string
  method: string
  score: number
  entry: number
  target: number
  stop: number
  upside: number | null
  downside: number | null
  reason: string
}

export interface ScanResult {
  long_picks: SignalPick[]
  short_picks: SignalPick[]
  scanned: number
}

export interface PairsSignal {
  current_z: number
  current_signal: string | null
  beta: number
  correlation: number
  is_valid_pair: boolean
  lock_message?: string
}

export interface MeanReversionSignal {
  current_signal: string | null
  current_price: number
  upper_band: number
  lower_band: number
  mid_band: number
  pct_b: number
  current_z: number
}

export interface MomentumSignal {
  current_signal: string | null
  current_price: number
  resistance: number | null
  is_breakout_today: boolean
  volume_surge: boolean
  volume_ratio: number
}

export interface RegimeChartPoint {
  date: string
  price: number
  regime: string
}

/** 카우프만 ER 기반 3국면. */
export type RegimeLabel = 'Bull' | 'Sideways' | 'Bear' | 'Unknown'

export interface MarketRegime {
  ticker: string
  current_regime: RegimeLabel
  regime_pct: Partial<Record<RegimeLabel, number>>
  n_regimes: number
  chart_data: RegimeChartPoint[]
  /** 'efficiency_ratio' */
  method?: string
  /** 현재 효율성 비율 (0~1). 1에 가까울수록 한 방향으로 직진. */
  current_er?: number | null
  /** ER 계산 기간(거래일) */
  window?: number
  /** 추세 판정 임계값 */
  threshold?: number
}

// Timing Engine Types
export type MarketSituationLevel = 'Low' | 'Normal' | 'High'

export interface MarketSituationMetric {
  value: number
  percentile: number
  level: MarketSituationLevel
  color: string
}

export interface MarketSituation {
  rate_spread: MarketSituationMetric
  hy_spread: MarketSituationMetric
  source: string
}

/** SMA 1차 필터 + MACD/RSI 스코어링 매매신호 스캔 결과 종목. */
export interface SignalScanPick {
  ticker: string
  price: number
  /** 통합 점수 0~100 */
  score: number
  /** 당일 거래량 / 20일 평균 거래량 */
  volume_ratio: number
  /** RSI(14) */
  rsi: number
  /** 당일 MACD 히스토그램 */
  macd_hist: number
  macd_hist_prev: number
  /** 항목별 배점 (수급 /40, 모멘텀 /30, 추세 /30) */
  components: { volume: number; momentum: number; trend: number }
  reason: string
  /** 종목명. 한국 시장에서만 채워진다 — 코드만으로는 회사를 알 수 없어서다. */
  name?: string
}

export interface SignalScanResult {
  long_picks: SignalScanPick[]
  short_picks: SignalScanPick[]
  scanned: number
  as_of?: string | null
  /** 완화 사다리 적용 단계. 0 = 원래 기준 그대로 통과. */
  long_filter_level?: number
  long_filter_note?: string
  short_filter_level?: number
  short_filter_note?: string
}

/** 검색된 임의 종목의 매수/매도 참고 점수 — Signal Scan 상위 N개에 없어도 조회 가능. */
export interface SignalScoreResult {
  ticker: string
  price: number | null
  insufficient_history: boolean
  long: SignalScanPick | null
  long_filter_pass: boolean
  short: SignalScanPick | null
  short_filter_pass: boolean
}

export interface TechnicalChartPoint {
  date: string
  // OHLC (open/high/low null이면 price를 close로만 사용)
  open: number | null
  high: number | null
  low: number | null
  price: number        // close
  mid: number | null
  upper: number | null
  lower: number | null
  zscore: number | null
  ma5: number | null
  ma30: number | null
  ma60: number | null
  ma120: number | null
  resistance: number | null
}

export interface TechnicalChartKeyPoint {
  date: string
  price: number
  type: 'BAND_BREAK_UP' | 'BAND_BREAK_DOWN' | 'RESISTANCE_BREAK'
}

export interface TechnicalChartResult {
  ticker: string
  series: TechnicalChartPoint[]
  key_points: TechnicalChartKeyPoint[]
  current_z: number
  current_signal: string | null
  bias: 'LONG' | 'SHORT' | 'NEUTRAL'
}

export interface PairsAutoMatch {
  ticker: string
  correlation: number
  sector?: string
}

export interface PairsAutoChartPoint {
  date: string
  a: number
  b: number
  spread: number
}

export interface PairsAutoBreach {
  date: string
  spread: number
}

export interface PairsAutoResult {
  ticker: string
  base_sector?: string
  matches: PairsAutoMatch[]
  best: { ticker: string; correlation: number } | null
  chart: PairsAutoChartPoint[]
  breaches: PairsAutoBreach[]
  charts: Record<string, PairsAutoChartPoint[]>
  all_breaches: Record<string, PairsAutoBreach[]>
  threshold_pct: number
}

// Optimizer Types
export interface FrontierPoint {
  return: number
  volatility: number
}

export interface OptimizationResult {
  weights: { [ticker: string]: number }
  expected_return: number
  volatility: number
  sharpe_ratio: number
  method: string
  frontier: FrontierPoint[]
  equal_weight_sharpe: number
  equal_weight_return: number
  equal_weight_volatility: number
  implied_returns?: { [ticker: string]: number }
  posterior_returns?: { [ticker: string]: number }
  views_applied?: boolean
  has_views?: boolean
}

export interface FactorAnalysisResult {
  alpha: number
  betas: {
    Market: number
    SMB: number
    HML: number
    MOM: number
    [key: string]: number
  }
  r_squared: number
  factor_contribution: { [key: string]: number }
  residual_vol: number
}

// AI Portfolio Optimization Types
export interface AIView {
  expected_return: number
  confidence: number
  sentiment: 'Bullish' | 'Neutral' | 'Bearish'
  key_driver?: string
}

export interface PriceStats {
  current_price: number
  // multi-period returns (%)
  ret_1m: number | null
  ret_3m: number | null
  ret_6m: number | null
  ret_1y: number | null
  annual_vol_1y: number
  vs_52w_high_pct: number | null
  vs_52w_low_pct: number | null
  // legacy compat
  annual_return_1y?: number
  ytd_return?: number
}

export interface OptimizationMode {
  weights: { [ticker: string]: number }
  expected_return: number
  volatility: number
  sharpe_ratio: number
  // 확장 리스크 지표 (역사 데이터 기반)
  sortino_ratio?: number
  max_drawdown?: number
  calmar_ratio?: number
  beta?: number | null
  cvar_95?: number
}

export interface AIOptimizationResult {
  tickers: string[]
  ai_views: { [ticker: string]: AIView }
  price_stats: { [ticker: string]: PriceStats }
  optimizations: {
    black_litterman: OptimizationMode | null
    max_sharpe_hist: OptimizationMode | null
    hrp: OptimizationMode | null
    target_return: OptimizationMode | null
  }
  effective_target_return: number
  posterior_returns: { [ticker: string]: number }
  frontier: Array<{ return: number; volatility: number }>
  correlation: {
    tickers: string[]
    matrix: number[][]
  }
  data_period?: string
}

// Macro AI Types
export interface MacroModes {
  modes: {
    fast: number[]
    standard: number[]
    full: number[]
  }
  models: string[]
}

export interface MacroAgent {
  id: string | number
  name: string
  text: string
  elapsed: number
  ok?: boolean
}

export interface VerdictCard {
  // 백엔드 실제 필드
  title?: string
  icon?: string
  color?: string       // "danger" | "warning" | "success" | "info"
  headline?: string
  summary?: string
  details?: string
  // 구버전 호환
  category?: string
  rating?: string
  rationale?: string
}

export interface MacroAnalysisResult {
  event: string
  agents: MacroAgent[]
  verdict_cards: VerdictCard[]
  portfolio_actions: Array<{
    action: string
    ticker?: string
    reason?: string
    [key: string]: unknown
  }>
}

export interface AnalystFeedback {
  feedback: string
  metrics_snapshot: Partial<PortfolioMetrics>
}

// Reports Types
export interface DailyBriefResult {
  report: string
  price_data: { [ticker: string]: {
    close: number
    chg_pct: number
    day_pnl: number
    total_pnl: number
    sector: string
  }}
  file_path: string
  logs: string[]
}

export interface ReportFile {
  name: string
  type: 'daily' | 'equity' | 'industry'
  size_kb: number
  mtime: number
  model_tier?: string
}

export interface Industry {
  id: string
  name_kr: string
  name_en: string
  tagline: string
  benchmark: string
  coverage: string
  icon: string
}

export interface EquityReportResult {
  ticker: string
  company_name: string
  sections: { [key: string]: string }
  raw: string
  file_path: string
}

export interface IndustryReportResult {
  industry_id: string
  sections: { [key: string]: string }
  raw: string
  file_path: string
}

// Ticker Detail Types
export interface OHLCVPoint {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  ma20: number | null
  ma50: number | null
  ma200: number | null
  bb_upper: number | null
  bb_mid: number | null
  bb_lower: number | null
  stoch_k: number | null
  stoch_d: number | null
}

export interface TickerDetailInfo {
  name: string
  sector: string
  industry: string
  market_cap: string
  pe: number | null
  div_yield: number
}

export interface TickerDetailPerformance {
  '1w': number | null
  '1m': number | null
  '6m': number | null
  ytd: number | null
  '1y': number | null
  '5y': number | null
  s52w_high: number
  s52w_low: number
}

export interface TickerDetailRisk {
  beta: number
  volatility: number | null
  avg_volume: number
  rsi14: number | null
  current_price: number
  change_pct: number
}

export interface TickerDetailVar {
  var95: number | null
  return_dist: { x: number; count: number }[]
}

export interface TickerDetailQuant {
  /** 4팩터 합성 점수 (0~100). 계산 불가 시 null. */
  score: number | null
  score_label: string
  /** 팩터별 원점수 — 합성 점수의 근거 */
  factors?: { momentum?: number; trend?: number; quality?: number; value?: number }
  /** 한국어 국면 라벨 */
  regime: string
  /** 'Bull' | 'Sideways' | 'Bear' */
  regime_code?: string
  /** 효율성 비율 (0~1) */
  regime_er?: number | null
  optimizer: {
    target_weight: number | null
    risk_contribution: number | null
    current_weight: number | null
    correlation: number | null
    correlation_label: string | null
    beta_exposure: number | null
    in_portfolio?: boolean
    note?: string | null
  }
  panic_score: number | null
  panic_status: string
  /** 패닉 점수 구성 요소 */
  panic_components?: {
    rsi?: number; drawdown?: number; vs_52w_high?: number
    volume?: number; volatility?: number
  }
}

export interface TickerDetail {
  ticker: string
  period: string
  ohlcv: OHLCVPoint[]
  info: TickerDetailInfo
  performance: TickerDetailPerformance
  risk: TickerDetailRisk
  var: TickerDetailVar
  quant: TickerDetailQuant
}
