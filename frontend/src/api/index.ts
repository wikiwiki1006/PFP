import axios from 'axios'
import { getIdToken } from '@/lib/firebase'
import { getMarket } from '@/lib/market'
import type {
  PortfolioMetrics, EquityCurvePoint, HoldingsMap, HoldingDetail,
  SectorWeights, Trade, TradeForm, MarketSnapshot, SectorData,
  MacroData, NewsItem, EarningsEvent,
  ScanResult, PairsSignal, MeanReversionSignal, MomentumSignal,
  MarketRegime,
  MacroModes, MacroAnalysisResult, AnalystFeedback,
  DailyBriefResult, ReportFile, Industry, EquityReportResult, IndustryReportResult,
  SignalScanResult, SignalScoreResult, TechnicalChartResult, PairsAutoResult,
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

  // 지금 보고 있는 시장을 모든 요청에 싣는다. 호출 지점이 60곳이 넘어
  // 하나씩 넘기면 빠뜨리는 곳이 생기고, 그 하나가 다른 시장 데이터를
  // 가져와 화면에 섞인다. 여기서 한 번에 붙이면 누락이 없다.
  // 이미 명시된 요청은 건드리지 않는다 (그 호출이 의도를 갖고 지정한 것이다).
  if (config.params?.market == null) {
    config.params = { ...(config.params ?? {}), market: getMarket() }
  }
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

// isEmptyPortfolioError 는 지웠다. 서버가 빈 포트폴리오에 400 을 주던 시절의
// 헬퍼인데, 이제 200 + 빈 값 + `is_empty` 를 준다 (develop bd3461b).
//
// 남겨 두면 다음 사람이 **400 을 기준으로 빈 상태를 판단하는 분기를 다시**
// 만든다. 실제로 그랬다 — AlphaTerminal 의 보유 표 분기가 `isEmptyPortfolioError`
// 로 걸려 있었는데 `/holdings-detail` 은 처음부터 200 + [] 를 줘서, 보유가 없는
// 사용자는 안내 대신 **빈 표**를 보고 있었다. 한 번도 그려진 적이 없는 분기였다.
//
// 빈 상태는 오류가 아니라 **데이터**로 판단한다.

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

/** 티커가 실제 시장에 존재하는지 확인. 로그인 불필요. */
export const checkTickerExists = async (ticker: string): Promise<{ ticker: string; exists: boolean; name: string | null }> =>
  (await api.get('/api/portfolio/ticker-exists', { params: { ticker } })).data

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
export const getSignalScan = async (topN = 10): Promise<SignalScanResult> =>
  (await api.get('/api/signals/signal-scan', { params: { top_n: topN } })).data

/** 검색된 임의 종목의 매수/매도 참고 점수 — 상위 N개 리스트 밖이어도 조회 가능. */
export const getSignalScore = async (ticker: string): Promise<SignalScoreResult> =>
  (await api.get('/api/signals/signal-score', { params: { ticker } })).data

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
// `/api/optimizer/max-sharpe` · `/black-litterman` 의 래퍼가 여기 있었다.
// 호출부가 0곳이었다 — 화면은 `/ai-optimize-job` 으로만 돈다.
//
// **엔드포인트는 살아 있다.** 여기서 지운 것은 프론트의 래퍼뿐이다.
// 죽은 래퍼를 남기면 다음 사람이 그걸 보고 "이 경로는 쓰인다" 로 읽는다.
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
  event: string; model?: string; mode?: string; portfolio?: Record<string, unknown>
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

// null 을 그대로 보낸다. 서버의 LiveMetrics 가 Optional[float] 이고,
// 프롬프트에서 '산출 불가'로 처리한다 — 여기서 값을 지어내 채우면 그 판단이
// 무너진다 (VIX 20 은 '변동성 정상'이라는 실측 주장이 된다).
export const getAnalystFeedback = async (metrics?: {
  vix?: number | null
  portfolio_beta?: number | null
  today_chg_pct?: number | null
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
