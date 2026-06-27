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
  benchmark_value: number
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
  date: string
  ticker: string
  type: 'BUY' | 'SELL'
  q: number
  price: number
  memo?: string
}

export interface TradeForm {
  date: string
  ticker: string
  type: 'BUY' | 'SELL'
  q: number
  price: number
  memo?: string
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
  t10y2y: number
  bamlh0a0hym2: number
}

export interface NewsItem {
  ticker: string
  headline: string
  url: string
  datetime: string
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
  event_type: string
  date: string
  value: number
}

// Signals Types
export interface SignalPick {
  ticker: string
  method: string
  score: number
  entry: number
  target: number
  stop: number
  upside: number
  reason: string
}

export interface ScanResult {
  long_picks: SignalPick[]
  short_picks: SignalPick[]
  scanned: number
  doom: boolean
}

export interface PairsSignal {
  current_z: number
  current_signal: string
  beta: number
  correlation: number
  is_valid_pair: boolean
  lock_message?: string
}

export interface MeanReversionSignal {
  current_signal: string
  current_price: number
  upper_band: number
  lower_band: number
  mid_band: number
  pct_b: number
  current_z: number
}

export interface MomentumSignal {
  current_signal: string
  current_price: number
  resistance: number
  is_breakout_today: boolean
  volume_surge: boolean
  volume_ratio: number
}

export interface MultiSignal {
  ticker: string
  mean_reversion: {
    current_signal: string
    current_z: number
    pct_b: number
  }
  momentum: {
    current_signal: string
    is_breakout_today: boolean
  }
  signals_agree: boolean
  combined_view: string
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
}

export interface MonteCarloStockResult {
  probability_final: number
  probability_touch: number
  message: string
  current_price: number
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
  id: string
  name: string
  text: string
  elapsed: number
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
