import axios from 'axios'
import type {
  PortfolioMetrics, EquityCurvePoint, HoldingsMap, HoldingDetail,
  SectorWeights, Trade, TradeForm, MarketSnapshot, SectorData,
  MacroData, NewsItem, DoomRadar, EarningsEvent, CorrelationMatrix,
  ScanResult, PairsSignal, MeanReversionSignal, MomentumSignal,
  MarketRegime, OptimizationResult, FactorAnalysisResult,
  MonteCarloPortfolioResult, MonteCarloStockResult, MonteCarloMacroResult,
  MacroModes, MacroAnalysisResult, AnalystFeedback,
  DailyBriefResult, ReportFile, Industry, EquityReportResult, IndustryReportResult,
} from '@/types'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',
  timeout: 300000,
  headers: { 'Content-Type': 'application/json' },
})

// ── Portfolio ──────────────────────────────────────────────────────────────────
export const getPortfolioMetrics = async (): Promise<PortfolioMetrics> =>
  (await api.get('/api/portfolio/metrics')).data

export const getEquityCurve = async (): Promise<EquityCurvePoint[]> =>
  (await api.get('/api/portfolio/equity-curve')).data

export const getHoldings = async (): Promise<HoldingsMap> =>
  (await api.get('/api/portfolio/holdings')).data

export const getHoldingsDetail = async (): Promise<HoldingDetail[]> =>
  (await api.get('/api/portfolio/holdings-detail')).data

export const getSectorWeights = async (): Promise<SectorWeights> =>
  (await api.get('/api/portfolio/sector-weights')).data

export const getTrades = async (): Promise<Trade[]> =>
  (await api.get('/api/portfolio/trades')).data

export const postTrade = async (trade: TradeForm): Promise<{ ok: boolean; record: Trade }> =>
  (await api.post('/api/portfolio/trades', trade)).data

export const updateHolding = async (ticker: string, body: { q: number; avg: number; sector?: string }): Promise<void> =>
  { await api.put(`/api/portfolio/holdings/${ticker}`, body) }

export const addHolding = async (ticker: string, body: { q: number; avg: number; sector: string }): Promise<void> =>
  { await api.post(`/api/portfolio/holdings/${ticker}`, body) }

export const deleteHolding = async (ticker: string): Promise<void> =>
  { await api.delete(`/api/portfolio/holdings/${ticker}`) }

// ── Market ─────────────────────────────────────────────────────────────────────
export const getMarketSnapshot = async (): Promise<MarketSnapshot> =>
  (await api.get('/api/market/snapshot')).data

export const getMarketSectors = async (): Promise<SectorData[]> =>
  (await api.get('/api/market/sectors')).data

export const getMacroData = async (): Promise<MacroData> =>
  (await api.get('/api/market/macro')).data

export const getMarketNews = async (tickers: string[]): Promise<NewsItem[]> =>
  (await api.get('/api/market/news', { params: { tickers: tickers.join(',') } })).data

export const getMarketDoomRadar = async (): Promise<DoomRadar> =>
  (await api.get('/api/market/doom-radar')).data

export const getEarnings = async (tickers: string[]): Promise<EarningsEvent[]> =>
  (await api.get('/api/market/earnings', { params: { tickers: tickers.join(',') } })).data

export const getCorrelation = async (tickers?: string[], period = '1y'): Promise<CorrelationMatrix> =>
  (await api.get('/api/market/correlation', {
    params: tickers?.length ? { tickers: tickers.join(','), period } : { period },
  })).data

export const getIndexPrices = async (ticker: string, period = '2y'): Promise<{ date: string; close: number }[]> => {
  const data = (await api.get('/api/market/prices', { params: { tickers: ticker, period } })).data
  return (data[0]?.series as { date: string; close: number }[]) || []
}

// ── Signals ────────────────────────────────────────────────────────────────────
export const getSignalsDoomRadar = async (): Promise<DoomRadar> =>
  (await api.get('/api/signals/doom-radar')).data

export const runSignalScan = async (topN = 10): Promise<ScanResult> =>
  (await api.post(`/api/signals/scan?top_n=${topN}`)).data

export const getCachedScan = async (): Promise<ScanResult> =>
  (await api.get('/api/signals/scan/cached')).data

export const getPairsSignal = async (tickerA: string, tickerB: string, period = '1y'): Promise<PairsSignal> =>
  (await api.get('/api/signals/pairs', { params: { ticker_a: tickerA, ticker_b: tickerB, period } })).data

export const getMeanReversionSignal = async (ticker: string, period = '6mo'): Promise<MeanReversionSignal> =>
  (await api.get('/api/signals/mean-reversion', { params: { ticker, period } })).data

export const getMomentumSignal = async (ticker: string): Promise<MomentumSignal> =>
  (await api.get('/api/signals/momentum', { params: { ticker } })).data

export const getMarketRegime = async (ticker = '^GSPC', period = '2y'): Promise<MarketRegime> =>
  (await api.get('/api/signals/regime', { params: { ticker, period } })).data

// ── Optimizer ──────────────────────────────────────────────────────────────────
export const runMaxSharpe = async (body?: object): Promise<OptimizationResult> =>
  (await api.post('/api/optimizer/max-sharpe', body || {})).data

export const runBlackLitterman = async (body?: object): Promise<OptimizationResult> =>
  (await api.post('/api/optimizer/black-litterman', body || {})).data

export const runFactorAnalysis = async (body?: object): Promise<FactorAnalysisResult> =>
  (await api.post('/api/optimizer/factor-analysis', body || {})).data

export const runMonteCarloPortfolio = async (body: object): Promise<MonteCarloPortfolioResult> =>
  (await api.post('/api/optimizer/montecarlo/portfolio', body)).data

export const runMonteCarloStock = async (body: object): Promise<MonteCarloStockResult> =>
  (await api.post('/api/optimizer/montecarlo/stock', body)).data

export const runMonteCarloMacro = async (body: object): Promise<MonteCarloMacroResult> =>
  (await api.post('/api/optimizer/montecarlo/macro', body)).data

// ── Macro AI ───────────────────────────────────────────────────────────────────
export const getMacroModes = async (): Promise<MacroModes> =>
  (await api.get('/api/macro/modes')).data

export const runMacroAnalysis = async (body: { event: string; model?: string; mode?: string }): Promise<MacroAnalysisResult> =>
  (await api.post('/api/macro/analyze', body)).data

export const getAnalystFeedback = async (): Promise<AnalystFeedback> =>
  (await api.get('/api/macro/analyst-feedback/auto')).data

// ── Reports ────────────────────────────────────────────────────────────────────
export const generateDailyBrief = async (): Promise<DailyBriefResult> =>
  (await api.post('/api/reports/daily-brief')).data

export const getDailyBriefHistory = async (): Promise<{ name: string; path: string; size: number }[]> =>
  (await api.get('/api/reports/daily-brief/history')).data

export const getDailyBriefFile = async (filename: string): Promise<{ content: string; name: string }> =>
  (await api.get(`/api/reports/daily-brief/file/${filename}`)).data

export const listIndustries = async (): Promise<Industry[]> =>
  (await api.get('/api/reports/industries')).data

export const generateEquityReport = async (ticker: string, company_name: string, send_telegram = false): Promise<EquityReportResult> =>
  (await api.post('/api/reports/equity-research', { ticker, company_name, send_telegram })).data

export const generateIndustryReport = async (industry_id: string, send_telegram = false): Promise<IndustryReportResult> =>
  (await api.post('/api/reports/industry-research', { industry_id, send_telegram })).data

export const getReportHistory = async (): Promise<ReportFile[]> =>
  (await api.get('/api/reports/history')).data

export const getReportFile = async (filename: string): Promise<{ content: string; name: string }> =>
  (await api.get(`/api/reports/file/${filename}`)).data

export const getTelegramStatus = async (): Promise<{ configured: boolean }> =>
  (await api.get('/api/reports/telegram/status')).data
