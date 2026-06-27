import axios from 'axios'
import type {
  PortfolioMetrics,
  EquityCurvePoint,
  HoldingsMap,
  HoldingDetail,
  SectorWeights,
  Trade,
  TradeForm,
  MarketSnapshot,
  SectorData,
  MacroData,
  NewsItem,
  DoomRadar,
  EarningsEvent,
  ScanResult,
  PairsSignal,
  MeanReversionSignal,
  MomentumSignal,
  MultiSignal,
  OptimizationResult,
  FactorAnalysisResult,
  MonteCarloPortfolioResult,
  MonteCarloStockResult,
  MacroModes,
  MacroAnalysisResult,
  AnalystFeedback,
} from '@/types'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// ── Portfolio ──────────────────────────────────────────────────────────────

export const getPortfolioMetrics = async (): Promise<PortfolioMetrics> => {
  const { data } = await api.get('/api/portfolio/metrics')
  return data
}

export const getEquityCurve = async (): Promise<EquityCurvePoint[]> => {
  const { data } = await api.get('/api/portfolio/equity-curve')
  return data
}

export const getHoldings = async (): Promise<HoldingsMap> => {
  const { data } = await api.get('/api/portfolio/holdings')
  return data
}

export const getHoldingsDetail = async (): Promise<HoldingDetail[]> => {
  const { data } = await api.get('/api/portfolio/holdings-detail')
  return data
}

export const getSectorWeights = async (): Promise<SectorWeights> => {
  const { data } = await api.get('/api/portfolio/sector-weights')
  return data
}

export const getTrades = async (): Promise<Trade[]> => {
  const { data } = await api.get('/api/portfolio/trades')
  return data
}

export const postTrade = async (trade: TradeForm): Promise<Trade> => {
  const { data } = await api.post('/api/portfolio/trades', trade)
  return data
}

export const updateHolding = async (
  ticker: string,
  body: { q: number; avg: number; sector: string }
): Promise<void> => {
  await api.put(`/api/portfolio/holdings/${ticker}`, body)
}

export const addHolding = async (
  ticker: string,
  body: { q: number; avg: number; sector: string }
): Promise<void> => {
  await api.post(`/api/portfolio/holdings/${ticker}`, body)
}

export const deleteHolding = async (ticker: string): Promise<void> => {
  await api.delete(`/api/portfolio/holdings/${ticker}`)
}

// ── Market ─────────────────────────────────────────────────────────────────

export const getMarketSnapshot = async (): Promise<MarketSnapshot> => {
  const { data } = await api.get('/api/market/snapshot')
  return data
}

export const getMarketSectors = async (): Promise<SectorData[]> => {
  const { data } = await api.get('/api/market/sectors')
  return data
}

export const getMacroData = async (): Promise<MacroData> => {
  const { data } = await api.get('/api/market/macro')
  return data
}

export const getMarketNews = async (tickers: string[]): Promise<NewsItem[]> => {
  const { data } = await api.get('/api/market/news', {
    params: { tickers: tickers.join(',') },
  })
  return data
}

export const getMarketDoomRadar = async (): Promise<DoomRadar> => {
  const { data } = await api.get('/api/market/doom-radar')
  return data
}

export const getEarnings = async (tickers: string[]): Promise<EarningsEvent[]> => {
  const { data } = await api.get('/api/market/earnings', {
    params: { tickers: tickers.join(',') },
  })
  return data
}

// ── Signals ────────────────────────────────────────────────────────────────

export const getSignalsDoomRadar = async (): Promise<DoomRadar> => {
  const { data } = await api.get('/api/signals/doom-radar')
  return data
}

export const runSignalScan = async (topN = 10): Promise<ScanResult> => {
  const { data } = await api.post(`/api/signals/scan?top_n=${topN}`)
  return data
}

export const getCachedScan = async (): Promise<ScanResult> => {
  const { data } = await api.get('/api/signals/scan/cached')
  return data
}

export const getPairsSignal = async (
  tickerA: string,
  tickerB: string,
  period = '1y'
): Promise<PairsSignal> => {
  const { data } = await api.get('/api/signals/pairs', {
    params: { ticker_a: tickerA, ticker_b: tickerB, period },
  })
  return data
}

export const getMeanReversionSignal = async (
  ticker: string,
  period = '6mo'
): Promise<MeanReversionSignal> => {
  const { data } = await api.get('/api/signals/mean-reversion', {
    params: { ticker, period },
  })
  return data
}

export const getMomentumSignal = async (ticker: string): Promise<MomentumSignal> => {
  const { data } = await api.get('/api/signals/momentum', {
    params: { ticker },
  })
  return data
}

export const getMultiSignal = async (ticker: string): Promise<MultiSignal> => {
  const { data } = await api.get('/api/signals/multi', {
    params: { ticker },
  })
  return data
}

// ── Optimizer ──────────────────────────────────────────────────────────────

export const runMaxSharpe = async (body?: {
  tickers?: string[]
  period?: string
  risk_free_rate?: number
}): Promise<OptimizationResult> => {
  const { data } = await api.post('/api/optimizer/max-sharpe', body || {})
  return data
}

export const runBlackLitterman = async (body?: {
  tickers?: string[]
  period?: string
  regime?: string
  view_confidence?: number
}): Promise<OptimizationResult> => {
  const { data } = await api.post('/api/optimizer/black-litterman', body || {})
  return data
}

export const runFactorAnalysis = async (body?: {
  tickers?: string[]
  period?: string
}): Promise<FactorAnalysisResult> => {
  const { data } = await api.post('/api/optimizer/factor-analysis', body || {})
  return data
}

export const runMonteCarloPortfolio = async (body: {
  tickers?: string[]
  target_return?: number
  n_simulations?: number
  n_days?: number
}): Promise<MonteCarloPortfolioResult> => {
  const { data } = await api.post('/api/optimizer/montecarlo/portfolio', body)
  return data
}

export const runMonteCarloStock = async (body: {
  ticker: string
  target_price: number
  period?: string
  n_simulations?: number
}): Promise<MonteCarloStockResult> => {
  const { data } = await api.post('/api/optimizer/montecarlo/stock', body)
  return data
}

// ── Macro AI ───────────────────────────────────────────────────────────────

export const getMacroModes = async (): Promise<MacroModes> => {
  const { data } = await api.get('/api/macro/modes')
  return data
}

export const runMacroAnalysis = async (body: {
  event: string
  model?: string
  mode?: string
}): Promise<MacroAnalysisResult> => {
  const { data } = await api.post('/api/macro/analyze', body)
  return data
}

export const getAnalystFeedback = async (): Promise<AnalystFeedback> => {
  const { data } = await api.get('/api/macro/analyst-feedback/auto')
  return data
}
