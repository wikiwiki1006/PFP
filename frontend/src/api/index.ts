import axios from 'axios'
import type {
  PortfolioMetrics, EquityCurvePoint, HoldingsMap, HoldingDetail,
  SectorWeights, Trade, TradeForm, MarketSnapshot, SectorData,
  MacroData, NewsItem, EarningsEvent, CorrelationMatrix,
  ScanResult, PairsSignal, MeanReversionSignal, MomentumSignal,
  MarketRegime, OptimizationResult, FactorAnalysisResult,
  MonteCarloPortfolioResult, MonteCarloStockResult, MonteCarloMacroResult,
  MacroModes, MacroAnalysisResult, AnalystFeedback,
  DailyBriefResult, ReportFile, Industry, EquityReportResult, IndustryReportResult,
  MarketSituation, BBScanFullResult, TechnicalChartResult, PairsAutoResult,
} from '@/types'

const _apiBase =
  import.meta.env.VITE_API_URL ||
  (typeof window !== 'undefined'
    ? `http://${window.location.hostname}:8000`
    : 'http://localhost:8000')

export const api = axios.create({
  baseURL: _apiBase,
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

export const updateTrade = async (id: number, body: {
  date: string; ticker: string; type: string; q: number; price?: number; memo?: string
}): Promise<void> =>
  { await api.put(`/api/portfolio/trades/${id}`, body) }

export const deleteTrade = async (id: number): Promise<void> =>
  { await api.delete(`/api/portfolio/trades/${id}`) }

export const getTickerPrice = async (ticker: string): Promise<{
  ticker: string; price: number; name: string; currency: string; sector?: string
}> =>
  (await api.get('/api/portfolio/ticker-price', { params: { ticker } })).data

export const searchTickers = async (q: string): Promise<{ ticker: string; name: string }[]> =>
  (await api.get('/api/portfolio/ticker-search', { params: { q, limit: 5 } })).data

export const autoDetectSectors = async (): Promise<{ updated: { ticker: string; sector: string }[]; count: number }> =>
  (await api.post('/api/portfolio/auto-sector')).data

// ── Market ─────────────────────────────────────────────────────────────────────
export const getMarketSnapshot = async (): Promise<MarketSnapshot> =>
  (await api.get('/api/market/snapshot')).data

export const getMarketSectors = async (): Promise<SectorData[]> =>
  (await api.get('/api/market/sectors')).data

export const getMacroData = async (): Promise<MacroData> =>
  (await api.get('/api/market/macro')).data

export const getMarketNews = async (tickers: string[]): Promise<NewsItem[]> =>
  (await api.get('/api/market/news', { params: { tickers: tickers.join(',') } })).data

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

export const getMarketRegime = async (ticker = '^GSPC', years = 1): Promise<MarketRegime> =>
  (await api.get('/api/signals/regime', { params: { ticker, years } })).data

// ── Timing Engine ──────────────────────────────────────────────────────────────
export const getMarketSituation = async (): Promise<MarketSituation> =>
  (await api.get('/api/signals/market-situation')).data

export const getBBScanFull = async (topN = 10): Promise<BBScanFullResult> =>
  (await api.get('/api/signals/bb-scan-full', { params: { top_n: topN } })).data

export const getTechnicalChart = async (ticker: string, period = '3y'): Promise<TechnicalChartResult> =>
  (await api.get('/api/signals/technical-chart', { params: { ticker, period } })).data

export const getPairsAuto = async (ticker: string, thresholdPct = 5, topN = 5): Promise<PairsAutoResult> =>
  (await api.get('/api/signals/pairs-auto', { params: { ticker, threshold_pct: thresholdPct, top_n: topN } })).data

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
