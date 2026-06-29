// Portfolio Types
export interface PortfolioMetrics {
  total_equity: number
  total_cost: number
  total_return_pct: number
  today_change_val: number
  today_change_pct: number
  portfolio_beta: number
  vix: number
  perf_1w: number
  perf_1m: number
  alpha_vs_sp500: number
}

export interface EquityCurvePoint {
  date: string
  value: number
  benchmark_value: number | null
}

export interface Holding {
  q: number
  avg: number
  sector: string
  div?: number
}

export interface HoldingsMap {
  [ticker: string]: Holding
}

export interface HoldingDetail {
  ticker: string
  qty: number
  avg_cost: number
  current_price: number
  market_value: number
  pnl: number
  pnl_pct: number
  sector: string
  weight: number
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
  change_1m_pct: number
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

export interface DoomRadar {
  is_doom: boolean
  severity: number
  comment: string
  rate_spread: number
  hy_spread: number
  source?: string
}

export interface EarningsEvent {
  ticker: string
  earn_date: string
  div_date: string
  div_yield: string
}

export interface CorrelationMatrix {
  tickers: string[]
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
  doom: DoomRadar
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

export interface MarketRegime {
  ticker: string
  current_regime: string
  regime_pct: { Bull: number; Sideways: number; Bear: number }
  n_regimes: number
  chart_data: RegimeChartPoint[]
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

export interface MonteCarloPortfolioResult {
  probability: number
  mean_return: number
  median_return: number
  var_95: number
  message: string
  histogram?: Array<{ bin: number; count: number }>
}

export interface MonteCarloStockResult {
  probability_final: number
  probability_touch: number
  message: string
  current_price: number
  histogram?: Array<{ bin: number; count: number }>
  paths?: number[][]
}

export interface MonteCarloMacroResult {
  probability_above_zero: number
  var_95: number
  cvar_95: number
  mean_return: number
  median_return: number
  histogram: Array<{ bin: number; count: number }>
  paths: number[][]
  message: string
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
  category: string
  rating: string
  rationale: string
  risk_level: string
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
  telegram_sent: boolean
}

export interface IndustryReportResult {
  industry_id: string
  sections: { [key: string]: string }
  raw: string
  file_path: string
  telegram_sent: boolean
}
