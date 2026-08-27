import axios from 'axios'
import { getIdToken } from '@/lib/firebase'
import type {
  PortfolioMetrics, EquityCurvePoint, HoldingsMap, HoldingDetail,
  SectorWeights, Trade, TradeForm, MarketSnapshot, SectorData,
  MacroData, NewsItem, EarningsEvent, CorrelationMatrix,
  ScanResult, PairsSignal, MeanReversionSignal, MomentumSignal,
  MarketRegime, OptimizationResult, FactorAnalysisResult,
  MacroModes, MacroAnalysisResult, AnalystFeedback,
  DailyBriefResult, ReportFile, Industry, EquityReportResult, IndustryReportResult,
  MarketSituation, BBScanFullResult, TechnicalChartResult, PairsAutoResult,
  TickerDetail, AIOptimizationResult,
} from '@/types'

// 개발(로컬+LAN): VITE_API_URL 미설정 → undefined → axios 상대경로 → Vite proxy가 /api/* 를 localhost:8000 으로 중계
// 배포(단일서버): VITE_API_URL 미설정 → FastAPI 가 /api/* 직접 처리, 정적파일도 FastAPI 서빙
// 배포(분리서버): VITE_API_URL=https://api.yourdomain.com 으로 빌드
const _apiBase: string | undefined = import.meta.env.VITE_API_URL || undefined

export const api = axios.create({
  baseURL: _apiBase,
  timeout: 300000,
  headers: { 'Content-Type': 'application/json' },
})

// ── 인증 ───────────────────────────────────────────────────────────────────────
// 모든 요청에 Firebase ID 토큰을 붙인다. 예전에는 X-User-Id 헤더로 신원을
// "주장"했는데, 서버가 그 말을 그대로 믿어 아무나 남의 데이터를 볼 수 있었다.
// 이제 서버는 이 토큰의 서명을 검증하고 그 안의 uid 만 신뢰한다.
api.interceptors.request.use(async (config) => {
  const token = await getIdToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 401 이 한 번 나면 토큰이 만료됐을 수 있다. 강제 갱신 후 딱 한 번만 재시도한다
// (무한 재시도를 막기 위해 _retried 플래그를 단다).
api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const cfg = error?.config
    if (error?.response?.status === 401 && cfg && !cfg._retried) {
      cfg._retried = true
      const fresh = await getIdToken(true).catch(() => null)
      if (fresh) {
        cfg.headers.Authorization = `Bearer ${fresh}`
        return api.request(cfg)
      }
    }
    return Promise.reject(error)
  },
)

/** 401 여부 — 화면에서 "로그인 필요" 안내를 띄울지 판단할 때 쓴다. */
export function isAuthError(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 401
}

/**
 * "보유 종목이 없어서" 실패한 요청인지.
 *
 * 서버는 빈 포트폴리오에 400 "보유 종목 없음" 을 돌려준다. 이걸 일반 오류와
 * 뭉뚱그리면 방금 가입한 사용자에게 "불러오지 못했습니다"라는 잘못된 안내가
 * 뜬다 — 조회에 실패한 게 아니라 보여줄 게 없는 것이다.
 */
export function isEmptyPortfolioError(err: unknown): boolean {
  const r = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response
  if (r?.status !== 400) return false
  return typeof r.data?.detail === 'string' && r.data.detail.includes('보유 종목')
}

// ── Portfolio ──────────────────────────────────────────────────────────────────
export const getPortfolioMetrics = async (): Promise<PortfolioMetrics> =>
  (await api.get('/api/portfolio/metrics')).data

export const getEquityCurve = async (): Promise<EquityCurvePoint[]> =>
  (await api.get('/api/portfolio/equity-curve')).data

export interface SetupHolding { ticker: string; q: number; price: number; date: string }
export interface SetupResult {
  ok: boolean; holdings: number; invested: number; cash: number
  seed_deposit: number; first_date: string
}
/** 포트폴리오 최초 등록 / 새로 등록. replace=true 면 기존 정보를 지운다. */
export const setupPortfolio = async (
  holdings: SetupHolding[], cash: number, replace = false,
): Promise<SetupResult> =>
  (await api.post('/api/portfolio/setup', { holdings, cash, replace })).data

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

export const updateHolding = async (ticker: string, body: { q: number; avg: number; sector?: string; date?: string }): Promise<void> =>
  { await api.put(`/api/portfolio/holdings/${ticker}`, body) }

export const addHolding = async (ticker: string, body: { q: number; avg: number; sector: string; date?: string }): Promise<void> =>
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

export const getTechnicalChart = async (
  ticker: string,
  period = '3y',
  bbPeriod = 20,
  bbStd = 2.0,
  resistanceLookback = 55,
): Promise<TechnicalChartResult> =>
  (await api.get('/api/signals/technical-chart', {
    params: { ticker, period, bb_period: bbPeriod, bb_std: bbStd, resistance_lookback: resistanceLookback },
  })).data

export const getPairsAuto = async (ticker: string, thresholdPct = 5, topN = 5): Promise<PairsAutoResult> =>
  (await api.get('/api/signals/pairs-auto', { params: { ticker, threshold_pct: thresholdPct, top_n: topN } })).data

// ── Optimizer ──────────────────────────────────────────────────────────────────
export const runMaxSharpe = async (body?: object): Promise<OptimizationResult> =>
  (await api.post('/api/optimizer/max-sharpe', body || {})).data

export const runBlackLitterman = async (body?: object): Promise<OptimizationResult> =>
  (await api.post('/api/optimizer/black-litterman', body || {})).data

export const runFactorAnalysis = async (body?: object): Promise<FactorAnalysisResult> =>
  (await api.post('/api/optimizer/factor-analysis', body || {})).data

export const runAIOptimize = async (body: {
  tickers?: string[]
  period?: string
  target_return?: number
  risk_free_rate?: number
  holding_period_years?: number
  weight_bounds?: [number, number]
}): Promise<AIOptimizationResult> =>
  (await api.post('/api/optimizer/ai-optimize', body)).data

export const startAIOptimizeJob = async (body: {
  tickers?: string[]
  period?: string
  target_return?: number
  risk_free_rate?: number
  holding_period_years?: number
  weight_bounds?: [number, number]
}): Promise<{ job_id: string }> =>
  (await api.post('/api/optimizer/ai-optimize-job', body)).data

export const getAIOptimizeJob = async (jobId: string): Promise<{
  status: 'running' | 'done' | 'error' | 'cancelled'
  stage: number
  stage_text: string
  result?: AIOptimizationResult
  detail?: string
}> => (await api.get(`/api/optimizer/ai-optimize-job/${jobId}`)).data

export const cancelAIOptimizeJob = async (jobId: string): Promise<{ ok: boolean }> =>
  (await api.delete(`/api/optimizer/ai-optimize-job/${jobId}`)).data

// ── Macro AI ───────────────────────────────────────────────────────────────────
export const getMacroModes = async (): Promise<MacroModes> =>
  (await api.get('/api/macro/modes')).data

export const startMacroAnalysis = async (body: {
  event: string; model?: string; mode?: string; portfolio?: Record<string, unknown>; provider?: string
}): Promise<{ job_id: string }> =>
  (await api.post('/api/macro/analyze', body)).data

export const getMacroJob = async (jobId: string): Promise<{
  status: 'pending' | 'done' | 'error' | 'cancelled'
  result?: MacroAnalysisResult
  message?: string
}> => (await api.get(`/api/macro/job/${jobId}`)).data

export const cancelMacroJob = async (jobId: string): Promise<{ ok: boolean }> =>
  (await api.delete(`/api/macro/job/${jobId}`)).data

export const startEquityReport = async (body: {
  ticker: string; model_tier?: string
}): Promise<{ job_id: string }> =>
  (await api.post('/api/reports/equity-research/start', body)).data

export const startIndustryReport = async (body: {
  industry_id: string; model_tier?: string
}): Promise<{ job_id: string }> =>
  (await api.post('/api/reports/industry-research/start', body)).data

export const getReportJob = async (jobId: string): Promise<{
  status: 'pending' | 'done' | 'error' | 'cancelled'
  result?: Record<string, unknown>
  message?: string
}> => (await api.get(`/api/reports/job/${jobId}`)).data

export const cancelReportJob = async (jobId: string): Promise<{ ok: boolean }> =>
  (await api.delete(`/api/reports/job/${jobId}`)).data

export const getMacroReportHistory = async (): Promise<{ name: string; event: string; mode: string; created_at: string }[]> =>
  (await api.get('/api/macro/reports')).data

export const getMacroReportFile = async (filename: string): Promise<MacroAnalysisResult> =>
  (await api.get(`/api/macro/reports/${filename}`)).data

export const getAnalystFeedback = async (metrics?: {
  vix?: number
  portfolio_beta?: number
  today_chg_pct?: number
}): Promise<AnalystFeedback> =>
  (await api.post('/api/macro/analyst-feedback/auto', metrics ?? {})).data

// ── Reports ────────────────────────────────────────────────────────────────────
export const generateDailyBrief = async (): Promise<DailyBriefResult> =>
  (await api.post('/api/reports/daily-brief')).data

export const getDailyBriefHistory = async (): Promise<{ name: string; path: string; size: number }[]> =>
  (await api.get('/api/reports/daily-brief/history')).data

export const getDailyBriefFile = async (filename: string): Promise<{ content: string; name: string }> =>
  (await api.get(`/api/reports/daily-brief/file/${filename}`)).data

export const listIndustries = async (): Promise<Industry[]> =>
  (await api.get('/api/reports/industries')).data

export const generateEquityReport = async (ticker: string, company_name: string): Promise<EquityReportResult> =>
  (await api.post('/api/reports/equity-research', { ticker, company_name })).data

export const generateIndustryReport = async (industry_id: string): Promise<IndustryReportResult> =>
  (await api.post('/api/reports/industry-research', { industry_id })).data

export const getReportHistory = async (): Promise<(ReportFile & { created_at?: string })[]> =>
  (await api.get('/api/reports/history')).data

export const getReportFile = async (filename: string): Promise<{ content: string; name: string }> =>
  (await api.get(`/api/reports/file/${filename}`)).data


// ── Ticker Detail ──────────────────────────────────────────────────────────────
export const getTickerDetail = async (ticker: string, period = '1y'): Promise<TickerDetail> =>
  (await api.get(`/api/ticker/${ticker.toUpperCase()}/detail`, { params: { period } })).data
