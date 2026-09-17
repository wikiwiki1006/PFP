import React, { useState, useMemo, useRef, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AreaChart, Area, PieChart, Pie, Cell, Sector,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceArea,
} from 'recharts'
import ReactMarkdown from 'react-markdown'
import {
  MessageSquare, RefreshCw,
  Plus, Trash2, Edit3, Check, X, Play, FileText, ChevronRight, ChevronLeft,
  Download, History, Search, Briefcase,
} from 'lucide-react'
import {
  getPortfolioMetrics, getEquityCurve, getHoldingsDetail, getSectorWeights,
  getMarketSnapshot, getMarketNews, getMacroData, getEarnings,
  getAnalystFeedback, getMarketSectors,
  postTrade, addHolding, updateHolding, deleteHolding, getHoldings,
  generateDailyBrief, getDailyBriefHistory, getDailyBriefFile,
  getIndexPrices, getTrades, updateTrade, deleteTrade, getTickerPrice, searchTickers,
  autoDetectSectors,
} from '@/api'
import { cn } from '@/lib/utils'
import TickerDetailModal from '@/components/TickerDetailModal'
import { FinancialTips } from '@/components/FinancialTips'
import LockedPreview, { AuthOverlay } from '@/components/auth/LockedPreview'
import SetupWizard from '@/components/portfolio/SetupWizard'
import { dailyChangeHeader } from '@/components/portfolio/dailyChangeHeader'
import { useTour } from '@/lib/TourContext'
import ConfirmDialog from '@/components/auth/ConfirmDialog'
import { useDemoQuery } from '@/lib/useDemoQuery'
import { useAuth } from '@/lib/AuthContext'
import { useIsMobile } from '@/lib/useIsMobile'
// 시장에 따라 갈리는 넷은 **함수로** 받는다. 상수로 받으면 모듈 최상위에서
// 한 번 고른 값이 박히고, 그건 시장 전환이 전체 새로고침을 유지하는 동안만
// 맞다. 나머지 넷은 시장 중립이라 상수 그대로다 (곡선은 퍼센트).
import {
  demoMetrics, demoHoldingsDetail, demoHoldingsRaw, demoSectorWeights,
  DEMO_EQUITY_CURVE, DEMO_ANALYST_FEEDBACK, DEMO_NEWS, DEMO_EARNINGS,
} from '@/lib/demoData'
import { formatPrice, formatCompact, formatMoney, marketSymbol,
         MARKETS, moneyInputProps, type Market } from '@/lib/market'
import { useMarket } from '@/lib/useMarket'
import { useTickerNames, displayTicker } from '@/lib/useTickerNames'
import TickerLabel from '@/components/TickerLabel'
import SuggestionList from '@/components/SuggestionList'
import { pickOnEnter, moveHighlight, selectionLabel, type Suggestion } from '@/lib/suggestions'
import { marketSession } from '@/lib/marketStorage'
import type { EquityCurvePoint, PortfolioMetrics } from '@/types'

// 표시용 포맷터는 계산 불가를 '—'로 그린다. 0으로 위장하지 않는다.
//
// 예전에는 fn/fp 가 null 을 0 으로 바꿔 '0.00' / '+0.00%' 를 그렸다. 그러면
// '진짜 보합'과 '데이터 없음'이 화면에서 구분되지 않는다. 더 나쁜 것은 색이다 —
// 0 은 `>= 0` 을 통과하므로 계산 불가가 초록(이익)으로 칠해졌다. 보유 테이블에서
// 일변동률 칸은 '—' 인데 바로 옆 손익 칸은 초록 +0.00% 인 상태가 실제로 있었다.
//
// 필드마다 안전한 포맷터를 따로 두면 다음 필드에서 같은 일이 반복된다. 그래서
// 기본값을 안전한 쪽으로 뒤집었다.
const NA = '—'
const isNA = (v: number | null | undefined): boolean => v == null || isNaN(v as number)

const fn = (v: number | null | undefined, d = 2) => isNA(v) ? NA : (v as number).toFixed(d)
const fp = (v: number | null | undefined, d = 2, sign = true) => {
  if (isNA(v)) return NA
  const n = v as number
  return sign ? `${n >= 0 ? '+' : ''}${n.toFixed(d)}%` : `${n.toFixed(d)}%`
}

// 계산에만 쓴다 (곱셈·비교·합계). **표시나 색 결정에 쓰지 말 것** — null 이 0 이
// 되므로, 색에 쓰면 계산 불가가 초록으로 칠해진다. 색은 chgColor 를 쓴다.
const fv = (v: number | null | undefined) => isNA(v) ? 0 : (v as number)

// 실현손익을 계산하지 못한 이유. 서버는 **코드**로 싣는다 — 문구는 표시
// 계층의 결정이다 (portfolio_calculator:422-426).
//
// 이 사전이 있어야 '—' 가 두 가지를 뜻하지 않는다. 매도가 없어서 0 인 것과
// 매도는 했는데 원가를 몰라 계산 못 한 것은 사용자가 할 수 있는 일이 다르다
// — 후자는 거래 이력을 고치면 풀린다 (§1.3).
const RPNL_REASON: Record<string, string> = {
  no_trade_log:       '거래 이력을 불러오지 못해 계산할 수 없습니다.',
  no_cost_basis:      '매수 기록 없는 매도가 있어 취득원가를 알 수 없습니다. 거래 이력에 해당 매수를 추가하면 계산됩니다.',
  missing_sale_price: '매도 단가가 비어 있는 거래가 있습니다.',
  missing_buy_price:  '매수 단가가 비어 있어 평단을 만들 수 없습니다.',
}

/** 실현손익 금액. 부호를 **통화기호 앞**에 둔다.
 *
 *  `formatPrice` 는 `기호 + 값` 이라 음수를 그대로 넘기면 `$-300.00` 이
 *  나온다 (실측). 부호가 숫자 안쪽에 묻혀 손실이 즉시 안 읽힌다. 절댓값을
 *  포맷하고 부호를 앞에 붙여 `-$300.00` / `-₩300` 으로 만든다.
 *
 *  양수에도 `+` 를 붙인다 — 손익은 부호가 값의 일부고, 옆 칸들이 이미
 *  `+1.06%` 처럼 부호를 달고 있어 여기만 빠지면 기준이 달라 보인다. */
const fmtRealized = (v: number | null | undefined) => {
  if (isNA(v)) return NA
  const n = v as number
  const sign = n > 0 ? '+' : n < 0 ? '-' : ''
  return sign + formatPrice(Math.abs(n))
}

/** 실현손익 칸의 툴팁. **'—' 가 왜 '—' 인지** 를 여기서 말한다. */
function realizedTitle(m: PortfolioMetrics): string {
  if (m.realized_pnl == null) {
    return RPNL_REASON[m.realized_pnl_reason ?? ''] ?? '실현손익을 계산할 수 없습니다.'
  }
  if (!m.realized_sales) {
    return '매도 이력이 없습니다. 0 은 "아직 확정한 손익이 없다" 는 뜻입니다.'
  }
  const pct = m.realized_pnl_pct == null
    ? '수익률은 취득원가가 0 이라 계산할 수 없습니다.'
    : `수익률 ${fp(m.realized_pnl_pct)} (매도된 주식의 취득원가 ${formatPrice(m.realized_cost)} 기준 — 총 투자원가가 아니다).`
  return `매도 ${m.realized_sales}건으로 확정한 손익. ${pct}`
}

// 상승 녹색 / 하락 빨강 / 정확히 0 또는 없음 → 회색 (0%를 녹색으로 칠하지 않는다)
const chgColor = (v: number | null | undefined) =>
  isNA(v) || v === 0 ? '#64748b' : (v as number) > 0 ? '#10b981' : '#ef4444'

// 변동성 지수(VIX·VKOSPI) 색 — 30 이상 위험 · 20 이상 주의 · 나머지 정상, 모르면 회색.
// 화면에 **적힌 숫자**(fn 의 소수 둘째 자리)로 가른다. 근거는 지표 줄의 주석.
const volColor = (v: number | null | undefined) => {
  if (isNA(v)) return '#64748b'
  const shown = Number((v as number).toFixed(2))
  return shown >= 30 ? '#ef4444' : shown >= 20 ? '#f59e0b' : '#10b981'
}

const SECTORS = [
  'Technology','Healthcare','Financials','Consumer Discretionary',
  'Consumer Staples','Energy','Industrials','Materials',
  'Real Estate','Utilities','Communication Services','Other',
]

const SECTOR_KO: Record<string, string> = {
  'Technology':             '기술',
  'Healthcare':             '헬스케어',
  'Financials':             '금융',
  'Consumer Discretionary': '경기소비재',
  'Consumer Staples':       '필수소비재',
  'Energy':                 '에너지',
  'Industrials':            '산업재',
  'Materials':              '소재',
  'Real Estate':            '부동산',
  'Utilities':              '유틸리티',
  'Communication Services': '커뮤니케이션',
  'Cash':                   '현금',
  'Other':                  '기타',
}
const toKoSector = (s: string) => SECTOR_KO[s] ?? s

// 섹터 대표 ETF 의 표시 이름. 미국은 티커(XLK)가 널리 통용되지만 한국은
// '091160.KS' 를 알아보는 사람이 없다 — 어느 ETF 로 잰 수치인지 알려면
// 상품명이 필요하다.
const SECTOR_ETF_LABEL: Record<string, string> = {
  '091160.KS': 'KODEX 반도체',
  '091170.KS': 'KODEX 은행',
  '266390.KS': 'KODEX 경기소비재',
  '266420.KS': 'KODEX 헬스케어',
  '102960.KS': 'KODEX 조선',
  '266410.KS': 'KODEX 필수소비재',
  '117460.KS': 'KODEX 에너지화학',
  '117680.KS': 'KODEX 철강',
  '266370.KS': 'KODEX IT하드웨어',
}
const sectorEtfLabel = (etf: string) => SECTOR_ETF_LABEL[etf] ?? etf

// 섹터 테이블 API 레이블 (대문자) → 한글
const SECTOR_LABEL_KO: Record<string, string> = {
  'TECHNOLOGY':       '기술',
  'FINANCIALS':       '금융',
  'COMMUNICATION':    '커뮤니케이션',
  'CONSUMER_DISC':    '소비재',
  'HEALTHCARE':       '헬스케어',
  'INDUSTRIALS':      '산업재',
  'CONSUMER_STAPLES': '필수소비',
  'ENERGY':           '에너지',
  'UTILITIES':        '유틸리티',
  'MATERIALS':        '소재',
  'REAL_ESTATE':      '부동산',
  // 한국 전용 — KODEX 업종 ETF 가 미국 GICS 와 1:1 로 대응되지 않아
  // 실제 ETF 가 담는 업종 이름을 그대로 쓴다.
  'SHIPBUILDING':     '조선',
  'ENERGY_CHEM':      '에너지화학',
  'STEEL':            '철강',
  'IT_HARDWARE':      'IT하드웨어',
}

const SECTOR_COLORS = [
  '#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6',
  '#06b6d4','#84cc16','#f97316','#ec4899','#94a3b8',
  '#a78bfa','#34d399','#fbbf24','#f87171',
]

// ── Marquee ───────────────────────────────────────────────────────────────────
const MARQUEE_CONFIG: { ticker: string; label: string; fmt: 'usd' | 'krw' | 'plain' }[] = [
  { ticker: '^GSPC',    label: 'S&P500',   fmt: 'plain' },
  { ticker: '^IXIC',    label: 'NASDAQ',   fmt: 'plain' },
  { ticker: '^KS11',    label: 'KOSPI',    fmt: 'plain' },
  { ticker: '^KQ11',    label: 'KOSDAQ',   fmt: 'plain' },
  { ticker: '^N225',    label: 'NIKKEI',fmt: 'plain' },
  { ticker: 'BTC-USD',  label: 'BTC',      fmt: 'usd'   },
  { ticker: 'USDKRW=X', label: 'USD/KRW', fmt: 'krw'   },
  { ticker: 'JPYKRW=X', label: 'YEN/KRW', fmt: 'krw'   },
  { ticker: 'CL=F',     label: 'WTI',      fmt: 'usd'   },
]

function Marquee({ snapshot }: { snapshot: any }) {
  if (!snapshot?.prices) return null
  const prices = snapshot.prices as Record<string, any>

  const fmtPrice = (price: number, fmt: 'usd' | 'krw' | 'plain') => {
    if (fmt === 'krw') return `₩${price.toLocaleString('ko-KR', { maximumFractionDigits: 0 })}`
    if (fmt === 'usd') return `$${fn(price)}`
    return price.toLocaleString('en-US', { maximumFractionDigits: 2 })
  }

  const renderItems = (suffix = '') =>
    MARQUEE_CONFIG.map(({ ticker, label, fmt }) => {
      const v = prices[ticker]
      if (!v) return null
      // 변동률이 없으면 0%로 위장하지 않고 '—' 로 표시 (마퀴는 한국식 색상: 상승 적색)
      const p: number | null =
        v?.change_1d_pct == null || !Number.isFinite(v.change_1d_pct)
          ? null : Number(v.change_1d_pct)
      const col = p == null || p === 0 ? '#64748b' : p > 0 ? '#ef4444' : '#3b82f6'
      const price = fv(v?.price)
      if (!price) return null
      return (
        <span key={ticker + suffix} className="inline-flex items-center gap-1 mr-6 font-mono text-[14px]">
          <span className="font-bold text-[#e2e8f0]">{label}</span>
          <span className="text-[#cbd5e1]">{fmtPrice(price, fmt)}</span>
          <span style={{ color: col }}>{p == null ? '—' : `${p >= 0 ? '+' : ''}${p.toFixed(2)}%`}</span>
        </span>
      )
    })

  return (
    <div className="overflow-hidden bg-[#070d18] border-b border-[#1e2d40] py-1.5 select-none">
      <div className="whitespace-nowrap animate-marquee inline-block">
        {renderItems('')}
        <span className="text-[#1e2d40] mr-6">·</span>
        {renderItems('_2')}
        <span className="text-[#1e2d40] mr-6">·</span>
      </div>
    </div>
  )
}

// ── Metric Pill ───────────────────────────────────────────────────────────────
function Pill({ label, value, color, title }: { label: string; value: string; color?: string; title?: string }) {
  return (
    <div className="metric-pill text-center px-6 py-3 border-r border-[#1e2d40] last:border-r-0 flex-shrink-0"
         title={title}>
      <div className="metric-pill-label text-[14px] text-[#94a3b8] font-bold tracking-widest uppercase">{label}</div>
      {/* 색 미지정(중립) 값은 인라인 style 대신 클래스로 — 라이트모드에서 light-theme.css 가
          text-[#e2e8f0] 를 검정으로 재정의해야 흰 배경에서 읽힌다. 인라인 style 은 그 재정의가 닿지 않는다. */}
      <div
        className={`metric-pill-value text-[22px] font-mono font-bold mt-0.5 tabular-nums${color ? '' : ' text-[#e2e8f0]'}`}
        style={color ? { color } : undefined}
      >{value}</div>
    </div>
  )
}

// ── Clock ─────────────────────────────────────────────────────────────────────
function Clock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const kr = now.toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  const ny = now.toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  // 모바일에서는 오른쪽에 세로로 세우면 폭을 40% 넘게 먹는다.
  // 아래쪽에 가로 한 줄로 눕혀 본문 폭을 돌려준다.
  return (
    <div className="hidden md:flex w-full md:w-auto flex-shrink-0 border-t md:border-t-0 md:border-l border-[#1e2d40]
                    flex-row md:flex-col justify-end items-center md:items-end
                    gap-3 md:gap-2 px-3 md:px-5 py-1 md:py-3">
      <div className="flex items-center gap-1.5 md:gap-3">
        <span className="text-[9px] md:text-[11px] text-[#64748b] md:text-[#94a3b8] font-bold tracking-wide">서울</span>
        <span className="text-[11px] md:text-[16px] font-mono font-semibold md:font-bold text-[#94a3b8] md:text-[#e2e8f0] tabular-nums">{kr}</span>
      </div>
      <div className="flex items-center gap-1.5 md:gap-3">
        <span className="text-[9px] md:text-[11px] text-[#64748b] md:text-[#94a3b8] font-bold tracking-wide">뉴욕</span>
        <span className="text-[11px] md:text-[16px] font-mono font-semibold md:font-bold text-[#94a3b8] md:text-[#cbd5e1] tabular-nums">{ny}</span>
      </div>
    </div>
  )
}

// ── Equity Curve helpers (module-level, no closure over state) ────────────────
const fmtCurveDate = (v: string) => {
  const p = v?.split('-')
  return p?.length === 3 ? `${p[0].slice(2)}.${p[1]}.${p[2]}` : v
}

// ── Equity Curve ──────────────────────────────────────────────────────────────
// 'sp500' 이 아니라 'benchmark' 다. 한국 포트폴리오의 비교 대상이 S&P 인 것은
// 근거가 없고, 서버는 이미 시장별 지수로 곡선을 그린다(US ^GSPC / KR ^KS11).
// 표시 이름은 /metrics 의 benchmark_label 에서 온다.
type BenchmarkMode = 'benchmark' | 'nasdaq' | 'both'

// 2차 비교 지수는 **프론트가 티커를 직접 지정해 직접 그린다**(getIndexPrices).
// 주 벤치마크와 달리 서버가 관여하지 않으므로 이름도 여기서 갖는다 — 생산자가
// 이름을 갖는 게 일관되고, 서버에 아무도 안 쓰는 필드를 늘리지 않는다.
const SECONDARY_INDEX: Record<Market, { ticker: string; label: string }> = {
  US: { ticker: '^IXIC', label: 'NASDAQ' },
  KR: { ticker: '^KQ11', label: '코스닥' },
}

type CurvePoint = {
  date: string
  /** undefined = 그 날짜의 포트폴리오 수익률을 모른다 (시세 이력 시작 전 등).
   *  0 으로 채우면 정체한 것처럼 그려지므로 선을 끊는다. benchmark_pct 와 같은 취급. */
  port?: number
  /** 벤치마크 대비 수익률. 어느 지수인지는 /metrics 의 benchmark_label 이 말한다 —
   *  포인트마다 이름을 반복하지 않는다. 키에 'sp' 를 박지 않는 이유는
   *  `alpha_vs_sp500` 과 같다: 계산이 ^KS11 로 바뀌어도 이름이 거짓말을 계속한다. */
  benchmark_pct?: number
  nasdaq?: number
  total_equity?: number
  cash_flow?: number   // 양수=입금, 음수=출금
  trades: { ticker: string; type: string; q: number; price: number }[]
  // return_pct·price 는 null 일 수 있다. 시세 이력이 시작되기 전(상장 전·
  // 백필 불가 구간)에는 값이 없다. 예전에는 가격을 0 으로 채워
  // (0/avg - 1)*100 = **-100%** 를 보고했다 — 유한한 값이라 NaN 가드에
  // 걸리지 않는다. 0 은 '보합'이라는 오해지만 -100% 는 '전액 손실'이라는
  // 확신이라 더 나쁘다.
  holdings: { ticker: string; return_pct: number | null; price: number | null }[]
}

/**
 * "보유 종목 없음" 안내.
 * 조회 실패와 구분해서 쓴다 — 방금 가입한 사용자에게 "불러오지 못했습니다"가
 * 뜨면 뭔가 고장난 것처럼 보인다.
 */
function EmptyHoldings({ compact, label = '보유 종목 없음' }: { compact?: boolean; label?: string }) {
  return compact ? (
    <div className="flex items-center gap-2 px-4 py-2 text-xs text-[#64748b]">
      <Briefcase className="w-3.5 h-3.5" />
      <span>{label}</span>
      <span className="text-[#374151]">· 종목을 추가하면 지표가 계산됩니다</span>
    </div>
  ) : (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
      <Briefcase className="w-8 h-8 text-[#1e2d40]" />
      <p className="text-sm text-[#64748b]">{label}</p>
      <p className="text-[11px] text-[#374151]">보유 종목을 추가하면 여기에 표시됩니다</p>
    </div>
  )
}

function EquityCurve({ curveQ }: { curveQ: any }) {
  const names = useTickerNames()
  // 기본값은 ALL — 백엔드는 첫 거래일부터 전 구간을 내려주는데 여기서 1년으로
  // 잘라 버리면 "최초 입금일부터 보이지 않는" 문제가 그대로 남는다.
  const [range, setRange] = useState<'1M' | '3M' | '1Y' | 'ALL'>('ALL')
  const [bm,    setBm]    = useState<BenchmarkMode>('benchmark')
  // 벤치마크 표시 이름. 서버가 시장별로 정해 준다(US 'S&P 500' / KR '코스피').
  // 여기서 시장을 보고 직접 고르지 않는다 — 곡선을 그리는 지수와 라벨이
  // 갈라지면 화면이 다른 지수 이름으로 같은 선을 설명하게 된다.
  // 아직 안 왔으면 이름을 지어내지 말고 중립어를 쓴다.
  //
  // **useDemoQuery 를 쓴다.** 평범한 useQuery 로 두면 비로그인 게이트가 없어
  // 방문자가 페이지를 열 때마다 /portfolio/metrics 에 401 이 나간다. 화면은
  // 안 깨지지만(라벨이 '벤치마크' 로 떨어진다) 인증 실패가 정상 트래픽에 섞인다.
  //
  // 그리고 아래 metricsQ 와 **같은 키**를 쓴다. react-query 는 키로 중복
  // 제거하므로, 게이트가 있는 쪽과 없는 쪽이 같은 캐시 항목을 두고 경쟁하면
  // 어느 쪽이 먼저 도느냐가 상태를 정한다. 둘 다 같은 훅으로 맞춰야 그 경쟁이
  // 없어진다.
  const benchLabelQ = useDemoQuery(['portfolio-metrics'], getPortfolioMetrics,
                                   demoMetrics(), { staleTime: 55_000 })
  const benchLabel = benchLabelQ.data?.benchmark_label ?? '벤치마크'
  // 모바일에서는 드래그 확대(스와이프 줌)를 끈다 — 스크롤하려고 짚은 손가락이
  // 그대로 확대 영역 선택으로 잡혀 페이지 스크롤을 막았다.
  const isMobile = useIsMobile()

  // ── 줌 상태 ─────────────────────────────────────────────────────────────
  const [dragBounds, setDragBounds] = useState<{
    left: string; right: string; startY: number; endY: number
  } | null>(null)
  const [zoomDomain, setZoomDomain] = useState<{ left: string; right: string } | null>(null)
  const [yDomain,    setYDomain]    = useState<[number, number] | ['auto', 'auto']>(['auto', 'auto'])
  const dragging         = useRef(false)
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartMouseYRef    = useRef<number>(0)

  // ── 십자선 상태 ──────────────────────────────────────────────────────────
  const [crosshairX,  setCrosshairX]  = useState<string | null>(null)
  const [chartMouseY, setChartMouseY] = useState<number | null>(null)

  const rangeToPeriod = { '1M': '3mo', '3M': '6mo', '1Y': '2y', 'ALL': 'max' } as const

  // 2차 비교 지수. 주 벤치마크와 **같은 축에 같은 역할로** 그려지므로 시장을
  // 따라가야 한다 — 한국 포트폴리오의 성과를 NASDAQ 에 견주는 근거는 S&P 에
  // 견주는 근거와 다르지 않고, 그 S&P 는 이미 코스피로 갈랐다.
  //
  // 코스닥이 완벽한 대응물은 아니다. 나스닥보다 소형주·투기 비중이 크다.
  // 다만 "성장·기술 쪽 2차 지수" 라는 역할이 가장 가깝고 대안이 없다.
  //
  // VIX 와 다르게 보는 이유: VIX 는 별도 타일의 시장 분위기 지표라 한국
  // 투자자도 실제로 보지만, 이건 성과 비교선이다.
  const market = useMarket()
  const secondary = SECONDARY_INDEX[market]
  const nasdaqQ = useQuery({
    // 키에 **티커**를 넣는다. 시장이 아니라 티커다 — 이 결과가 실제로
    // 의존하는 것이 티커이고, 나중에 사용자가 비교 지수를 직접 고르게 되면
    // 그때도 맞다. 티커가 빠진 채로 시장별 전환만 하면 시장을 바꿔도
    // react-query 가 같은 키를 보고 이전 지수 데이터를 돌려준다 (§1.1).
    queryKey: ['secondary-index', secondary.ticker, rangeToPeriod[range]],
    queryFn:  () => getIndexPrices(secondary.ticker, rangeToPeriod[range]),
    enabled:  bm !== 'benchmark',
    staleTime: 300_000,
  })

  const filterByRange = (all: EquityCurvePoint[]) => {
    if (range === 'ALL' || !all.length) return all
    const today  = new Date()
    const cutoff = new Date(today)
    if (range === '1M')      cutoff.setMonth(today.getMonth() - 1)
    else if (range === '3M') cutoff.setMonth(today.getMonth() - 3)
    else if (range === '1Y') cutoff.setFullYear(today.getFullYear() - 1)
    const cutStr = cutoff.toISOString().split('T')[0]
    return all.filter((d: any) => d.date >= cutStr)
  }

  const data = useMemo((): CurvePoint[] => {
    // any 캐스트를 쓰지 않는다. 이게 EquityCurvePoint 선언이 서버 응답과
    // 아예 다른 스키마인데도 build 가 통과한 이유였다 — 틀린 선언만으로는
    // 조용하고, any 캐스트가 겹쳐야 타입체크가 통째로 비활성화된다.
    const all: EquityCurvePoint[] = curveQ.data || []
    const sliced = filterByRange(all)
    if (!sliced.length) return []

    const nasdaqMap = new Map((nasdaqQ.data || []).map((d: any) => [d.date, d.close]))

    // NASDAQ: 표시 구간의 첫 가격을 기준(0%)으로 정규화
    const firstNqPrice = (() => {
      for (const d of sliced) {
        const v = nasdaqMap.get(d.date)
        if (v != null && isFinite(v)) return v as number
      }
      return null
    })()

    // 포트폴리오·벤치마크도 **보고 있는 구간의 첫 날을 0%** 로 다시 잡는다.
    //
    // 서버가 주는 port/sp 는 포트폴리오 시작 시점부터의 누적 수익률이다. 1M 을
    // 눌러도 그 값을 그대로 그리면 선이 +45% 같은 자리에서 시작해, 정작 보고
    // 싶은 '최근 한 달 성과'를 읽을 수 없고 NASDAQ(0% 시작)과도 축이 맞지 않는다.
    //
    // 누적 수익률을 다시 기준 잡는 식은 (1+r_t)/(1+r_0) - 1 이다. 단순 뺄셈
    // (r_t - r_0)은 복리를 무시해 구간이 길수록 오차가 커진다.
    const firstOf = (key: 'port' | 'benchmark_pct'): number | null => {
      for (const d of sliced) {
        const v = d[key]
        if (v != null && isFinite(v)) return v as number
      }
      return null
    }
    const basePort = firstOf('port')
    const baseBench = firstOf('benchmark_pct')

    const rebase = (v: any, base: number | null): number | undefined => {
      if (v == null || !isFinite(v)) return undefined
      if (base == null) return +Number(v).toFixed(2)
      const denom = 100 + base
      // 기준일 수익률이 -100%(전액 손실)면 나눌 수 없다. 그대로 둔다.
      if (Math.abs(denom) < 1e-9) return +Number(v).toFixed(2)
      return +(((100 + Number(v)) / denom - 1) * 100).toFixed(2)
    }

    return sliced.map((d: any) => {
      const nc = nasdaqMap.get(d.date) as number | undefined
      const nv = firstNqPrice != null && nc != null && isFinite(nc)
        ? +((nc / firstNqPrice - 1) * 100).toFixed(2) : undefined
      return {
        date:         d.date,
        // `?? 0` 을 쓰지 않는다. 값이 없는 구간을 0% 로 채우면 **포트폴리오가
        // 그동안 정체한 것처럼** 그려진다 — 같은 구간에서 벤치마크 선은
        // 움직이므로 "시장은 올랐는데 내 포트폴리오만 제자리" 로 읽힌다.
        // 바로 아래 sp 는 이미 undefined 로 두고 있었다. 값을 모르는 구간은
        // 선을 끊는 게 맞다.
        port:         rebase(d.port, basePort),
        benchmark_pct: rebase(d.benchmark_pct, baseBench),
        nasdaq:       nv,
        total_equity: d.total_equity ?? undefined,
        cash_flow:    d.cash_flow ?? undefined,
        trades:       d.trades ?? [],
        holdings:     d.holdings ?? [],
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curveQ.data, range, nasdaqQ.data])

  const displayData = useMemo(() => {
    if (!zoomDomain) return data
    return data.filter(d => d.date >= zoomDomain.left && d.date <= zoomDomain.right)
  }, [data, zoomDomain])

  // 현재 Y 범위 — pixel→data 변환 기준 (auto일 땐 displayData에서 계산)
  const computedYRange = useMemo((): [number, number] => {
    if (yDomain[0] !== 'auto') return yDomain as [number, number]
    const vals = displayData.flatMap(d =>
      [d.port, d.benchmark_pct, d.nasdaq].filter((v): v is number => v != null)
    )
    if (!vals.length) return [-10, 10]
    const minV = Math.min(...vals), maxV = Math.max(...vals)
    const pad = Math.max((maxV - minV) * 0.1, 0.5)
    return [minV - pad, maxV + pad]
  }, [yDomain, displayData])

  // XAxis에 보여줄 날짜 레이블 — 대략 6개, 첫/마지막 포함, 두번째~끝 인덱스 생략 없음
  const xAxisTicks = useMemo(() => {
    const n = displayData.length
    if (n <= 1) return displayData.map(d => d.date)
    const TARGET = 6
    const step = Math.max(1, Math.round((n - 1) / (TARGET - 1)))
    const ticks: string[] = []
    for (let i = 0; i < n - 1; i += step) ticks.push(displayData[i].date)
    ticks.push(displayData[n - 1].date)
    return ticks
  }, [displayData])

  // chart 컨테이너 픽셀 Y → 데이터 값 (margin.top=8, XAxis height≈30, plotH≈262)
  const pixelYToData = (py: number): number => {
    const [dMin, dMax] = computedYRange
    const ratio = Math.max(0, Math.min(1, (py - 8) / 262))
    return dMax - ratio * (dMax - dMin)
  }

  // 픽셀 X → displayData 날짜 (YAxis width=52, right margin=8)
  const pixelXToDate = useCallback((px: number): string | null => {
    const N = displayData.length
    if (!N || !chartContainerRef.current) return null
    const plotLeft  = 52
    const plotRight = chartContainerRef.current.offsetWidth - 8
    const plotWidth = plotRight - plotLeft
    const i = Math.round((px - plotLeft) / plotWidth * N - 0.5)
    return displayData[Math.max(0, Math.min(N - 1, i))]?.date ?? null
  }, [displayData])

  // 오늘의 일간 변동: 전체 원본 데이터 마지막 2개 포인트 차이
  const allRawData: EquityCurvePoint[] = curveQ.data || []
  const portAll  = allRawData.filter((d: any) => d.port != null)
  // 장 마감·휴장 시 ffill로 인한 0% 방지: 마지막 실제 변동값 반환
  const lastChange = (arr: any[], key: string): number => {
    for (let i = arr.length - 1; i >= 1; i--) {
      const diff = (arr[i][key] as number) - (arr[i - 1][key] as number)
      if (Math.abs(diff) > 0.001) return diff
    }
    return 0
  }

  const portPct = lastChange(portAll, 'port')

  const benchRawAll = allRawData.filter(d => d.benchmark_pct != null)
  const benchPct = lastChange(benchRawAll, 'benchmark_pct')

  const nqFiltered = data.filter(d => d.nasdaq != null)
  const nqPct = lastChange(nqFiltered, 'nasdaq')

  const BM_BTNS: { key: BenchmarkMode; label: string; color: string }[] = [
    { key: 'benchmark', label: benchLabel, color: '#dc143c' },
    { key: 'nasdaq', label: secondary.label, color: '#a78bfa' },
    { key: 'both',   label: '전체', color: '#f59e0b' },
  ]

  // ── native 마우스 핸들러 (chart 컨테이너에 부착 — 실제 픽셀 Y 추적) ─────
  const handleNativeMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = chartContainerRef.current?.getBoundingClientRect()
    if (!rect) return
    const py = e.clientY - rect.top
    chartMouseYRef.current = py
    setChartMouseY(py)
  }
  const handleNativeMouseLeave = () => {
    setChartMouseY(null)
    setCrosshairX(null)
    chartMouseYRef.current = 0
  }

  // ── 드래그 줌 핸들러 ────────────────────────────────────────────────────────
  // 컨테이너 mouseDown: 차트 전체 영역에서 드래그 시작 가능 (넓은 드래그 영역)
  // 모바일에서는 시작하지 않는다 — 스크롤과 스와이프 확대가 같은 제스처라 겹친다.
  const handleContainerMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (isMobile) return
    const rect = chartContainerRef.current?.getBoundingClientRect()
    if (!rect) return
    const px = e.clientX - rect.left
    const label = pixelXToDate(px)
    if (!label) return
    dragging.current = true
    const sy = chartMouseYRef.current
    setCrosshairX(null)
    setDragBounds({ left: label, right: label, startY: sy, endY: sy })
  }
  const handleMouseDown = (e: any) => {
    // Recharts onMouseDown: dragging already started by container, just update if activeLabel present
    if (isMobile || !e?.activeLabel || dragging.current) return
    dragging.current = true
    const sy = chartMouseYRef.current
    setCrosshairX(null)
    setDragBounds({ left: e.activeLabel, right: e.activeLabel, startY: sy, endY: sy })
  }
  const handleChartMouseMove = (e: any) => {
    if (!e?.activeLabel) { setCrosshairX(null); return }
    if (dragging.current) {
      setDragBounds(prev => prev
        ? { ...prev, right: e.activeLabel, endY: chartMouseYRef.current }
        : null)
      return
    }
    setCrosshairX(e.activeLabel)
  }
  const handleMouseUp = () => {
    dragging.current = false
    if (dragBounds && dragBounds.left !== dragBounds.right) {
      const [l, r] = dragBounds.left <= dragBounds.right
        ? [dragBounds.left, dragBounds.right]
        : [dragBounds.right, dragBounds.left]
      setZoomDomain({ left: l, right: r })
      // pixel Y → data Y 로 변환해 Y 도메인 설정
      const minPy = Math.min(dragBounds.startY, dragBounds.endY)
      const maxPy = Math.max(dragBounds.startY, dragBounds.endY)
      const yHigh = pixelYToData(minPy)
      const yLow  = pixelYToData(maxPy)
      if (yHigh > yLow) setYDomain([yLow, yHigh])
    }
    setDragBounds(null)
  }
  const resetZoom   = () => { setZoomDomain(null); setDragBounds(null); setYDomain(['auto', 'auto']) }
  const changeRange = (r: typeof range) => { setRange(r); resetZoom() }
  const selLeft  = dragBounds && dragBounds.left  <= dragBounds.right ? dragBounds.left  : dragBounds?.right
  const selRight = dragBounds && dragBounds.left  <= dragBounds.right ? dragBounds.right : dragBounds?.left

  // ── 커스텀 툴팁 (useCallback으로 메모이즈 — 컴포넌트 재생성 방지) ─────────
  const renderTooltip = useCallback(({ active, payload, label }: any) => {
    if (!active || !payload?.length) return null
    const d: CurvePoint = payload[0]?.payload
    if (!d) return null
    return (
      <div style={{ backgroundColor: '#0b1220', border: '1px solid #1e2d40', borderRadius: 4, padding: '8px 12px', fontSize: 12, minWidth: 200 }}>
        <div className="flex items-center justify-between mb-2">
          <span className="text-[#cbd5e1] text-[11px]">{fmtCurveDate(label)}</span>
          {d.total_equity != null && d.total_equity > 0 && (
            <span className="font-mono text-[11px] text-[#e2e8f0] font-bold">{formatMoney(d.total_equity)}</span>
          )}
        </div>
        <div className="flex items-center justify-between gap-3 mb-0.5">
          <span className="flex items-center gap-1.5">
            <span style={{ color: '#00e6ff' }}>●</span>
            <span className="text-[#94a3b8] text-[10px]">포트폴리오</span>
          </span>
          {/* 값이 없으면 회색 '—'. raw 삼항이면 `null >= 0` 이 거짓이라
              **계산 불가가 빨강(손실)** 으로 칠해진다. */}
          <span className="font-mono font-bold" style={{ color: chgColor(d.port) }}>{fp(d.port, 2)}</span>
        </div>
        {d.benchmark_pct != null && (bm === 'benchmark' || bm === 'both') && (
          <div className="flex items-center justify-between gap-3 mb-0.5">
            <span className="flex items-center gap-1.5">
              <span style={{ color: '#dc143c' }}>●</span>
              <span style={{ color: '#dc143c' }} className="text-[10px]">{benchLabel}</span>
            </span>
            <span className="font-mono" style={{ color: '#dc143c' }}>{fp(d.benchmark_pct, 2)}</span>
          </div>
        )}
        {d.nasdaq != null && (bm === 'nasdaq' || bm === 'both') && (
          <div className="flex items-center justify-between gap-3 mb-0.5">
            <span className="flex items-center gap-1.5">
              <span style={{ color: '#a78bfa' }}>●</span>
              <span className="text-[#94a3b8] text-[10px]">{secondary.label}</span>
            </span>
            <span className="font-mono text-[#a78bfa]">{fp(d.nasdaq, 2)}</span>
          </div>
        )}
        {d.cash_flow != null && (
          <div className="mt-2 pt-2 border-t border-[#1e2d40]">
            <div className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5 text-[10px]" style={{ color: d.cash_flow > 0 ? '#f59e0b' : '#f43f5e' }}>
                <span>◆</span>
                <span>{d.cash_flow > 0 ? '입금' : '출금'}</span>
              </span>
              <span className="font-mono text-[11px] font-bold" style={{ color: d.cash_flow > 0 ? '#f59e0b' : '#f43f5e' }}>
                {d.cash_flow > 0 ? '+' : ''}{fn(Math.abs(d.cash_flow), 0).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
              </span>
            </div>
          </div>
        )}
        {d.holdings?.length > 0 && (
          <div className="mt-2 pt-2 border-t border-[#1e2d40]">
            {d.holdings.map((h, i) => (
              <div key={i} className="flex items-center justify-between gap-3 mt-0.5">
                <span className="text-[11px] text-[#cbd5e1] truncate">{displayTicker(h.ticker, names)}</span>
                <span className="font-mono text-[11px]" style={{ color: chgColor(h.return_pct) }}>
                  {fp(h.return_pct, 2)}
                </span>
              </div>
            ))}
          </div>
        )}
        {d.trades?.length > 0 && (
          <div className="mt-2 pt-2 border-t border-[#1e2d40]">
            {d.trades.map((t, i) => {
              const isBuy  = t.type === 'ADD' || t.type === 'BUY'
              const color  = isBuy ? '#10b981' : '#ef4444'
              return (
                <div key={i} className="flex items-center justify-between gap-3 mt-0.5">
                  <span className="flex items-center gap-1" style={{ color }}>
                    <span style={{ fontSize: 9 }}>{isBuy ? '▲' : '▼'}</span>
                    <span className="font-bold text-[11px]">{displayTicker(t.ticker, names)}</span>
                    <span className="text-[10px] opacity-70">{isBuy ? '매수' : '매도'}</span>
                  </span>
                  <span className="text-[#cbd5e1] text-[10px] font-mono">{t.q}주 @{formatPrice(t.price)}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    )
    // names 가 빠지면 안 된다. 이 콜백은 이름 사전이 오기 **전에** 한 번 만들어지고
    // bm 을 안 바꾸는 한 다시 만들어지지 않아서, 툴팁이 계속 빈 사전으로
    // `000660.KS` 를 그렸다 (실측 — 보유 표는 이름인데 툴팁만 코드였다).
    // benchLabel·secondary.label 도 같은 이유로 넣는다.
  }, [bm, names, benchLabel, secondary.label])

  // ── 매매 / 입출금 포인트 dot 렌더러 ────────────────────────────────────────
  // recharts 가 이 렌더러의 반환을 **배열로** 그린다. key 가 없으면 React 가
  // 위치로 매칭해 이전 포인트의 DOM 을 재사용한다 — 시장을 전환하거나 구간을
  // 바꿔 포인트 집합이 달라질 때 엉뚱한 자리에 마커가 남는다.
  // 날짜가 포인트의 고유 식별자다.
  const tradeDot = (props: any) => {
    const { cx, cy, payload } = props
    const k = `dot-${payload?.date ?? `${cx}-${cy}`}`
    const hasCashFlow = payload?.cash_flow != null
    const hasTrades   = payload?.trades?.length > 0

    if (!hasCashFlow && !hasTrades) return <g key={k} />

    // 입출금 이벤트: 다이아몬드 ◆ (입금=amber, 출금=rose)
    if (hasCashFlow) {
      const isDeposit = (payload.cash_flow ?? 0) > 0
      const color     = isDeposit ? '#f59e0b' : '#f43f5e'
      const s = 6  // 반지름
      return (
        <g key={k}>
          <polygon
            points={`${cx},${cy - s} ${cx + s},${cy} ${cx},${cy + s} ${cx - s},${cy}`}
            fill={color} opacity={0.9}
          />
          <polygon
            points={`${cx},${cy - s - 2} ${cx + s + 2},${cy} ${cx},${cy + s + 2} ${cx - s - 2},${cy}`}
            fill="none" stroke={color} strokeWidth={1} opacity={0.5}
          />
        </g>
      )
    }

    // 주식 매매 이벤트: 원형 ●
    const hasBuy  = payload.trades.some((t: any) => t.type === 'ADD' || t.type === 'BUY')
    const hasSell = payload.trades.some((t: any) => t.type === 'SOLD' || t.type === 'SELL')
    const color   = hasBuy && hasSell ? '#f59e0b' : hasBuy ? '#10b981' : '#ef4444'
    return (
      <g key={k}>
        <circle cx={cx} cy={cy} r={6}  fill="#0b1220" stroke={color} strokeWidth={1.5} />
        <circle cx={cx} cy={cy} r={3}  fill={color} />
      </g>
    )
  }

  return (
    <div className="px-4 pt-3 pb-3 border-b border-[#1e2d40]">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-4 flex-wrap">
          <span className="text-[11px] text-[#cbd5e1] font-bold tracking-[3px] uppercase">포트폴리오 수익률</span>
          <div className="flex items-center gap-2">
            <svg width="20" height="5"><line x1="0" y1="2.5" x2="20" y2="2.5" stroke="#00e6ff" strokeWidth="2.5" /></svg>
            <span className="text-sm font-mono font-bold tabular-nums"
              style={{ color: portPct >= 0 ? '#10b981' : '#ef4444' }}>
              {fp(portPct, 2)}
            </span>
          </div>
          {(bm === 'benchmark' || bm === 'both') && (
            <div className="flex items-center gap-2">
              <svg width="20" height="5">
                <line x1="0" y1="2.5" x2="5" y2="2.5" stroke="#dc143c" strokeWidth="1.5" />
                <line x1="7" y1="2.5" x2="12" y2="2.5" stroke="#dc143c" strokeWidth="1.5" />
                <line x1="14" y1="2.5" x2="20" y2="2.5" stroke="#dc143c" strokeWidth="1.5" />
              </svg>
              <span className="text-sm font-mono tabular-nums" style={{ color: '#dc143c' }}>
                {benchLabel} {fp(benchPct, 2)}
              </span>
            </div>
          )}
          {(bm === 'nasdaq' || bm === 'both') && (
            <div className="flex items-center gap-2">
              <svg width="20" height="5">
                <line x1="0" y1="2.5" x2="5" y2="2.5" stroke="#a78bfa" strokeWidth="1.5" />
                <line x1="7" y1="2.5" x2="12" y2="2.5" stroke="#a78bfa" strokeWidth="1.5" />
                <line x1="14" y1="2.5" x2="20" y2="2.5" stroke="#a78bfa" strokeWidth="1.5" />
              </svg>
              <span className="text-sm font-mono text-[#a78bfa] tabular-nums">
                {secondary.label} {fp(nqPct, 2)}
              </span>
            </div>
          )}
          {zoomDomain && (
            <button onClick={resetZoom}
              className="text-[10px] font-bold px-2 py-0.5 rounded border border-[#f59e0b]/50 text-[#f59e0b] hover:bg-[#f59e0b]/10 transition-colors">
              줌 초기화
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <div className="flex gap-0.5 bg-[#070d18] border border-[#1e2d40] rounded p-0.5">
            {BM_BTNS.map(b => (
              <button key={b.key} onClick={() => setBm(b.key)}
                className={cn('text-[11px] px-2.5 py-1 rounded font-bold transition-colors duration-100',
                  bm === b.key ? '' : 'text-[#94a3b8] hover:text-[#94a3b8]'
                )}
                style={bm === b.key ? { backgroundColor: b.color + '28', color: b.color } : {}}>
                {b.label}
              </button>
            ))}
          </div>
          <div className="flex gap-0.5 bg-[#070d18] border border-[#1e2d40] rounded p-0.5">
            {(['1M', '3M', '1Y', 'ALL'] as const).map(r => (
              <button key={r} onClick={() => changeRange(r)}
                className={cn('text-[11px] px-2.5 py-1 rounded font-bold transition-colors duration-100',
                  range === r ? 'bg-[#00e6ff]/15 text-[#00e6ff]' : 'text-[#94a3b8] hover:text-[#94a3b8]'
                )}>
                {r === 'ALL' ? '전체' : r}
              </button>
            ))}
          </div>
        </div>
      </div>

      {curveQ.isLoading && (
        <div className="flex items-center justify-center" style={{ height: 300 }}>
          <span className="text-[13px] text-[#94a3b8] font-mono">로드 중…</span>
        </div>
      )}
      {/* 조회 실패와 빈 포트폴리오를 가르는 근거가 **에러 여부**로 바뀌었다.
          서버가 보유 없는 사용자에게 200 + `[]` 를 주므로, 빈 목록은 에러가
          아니라 정상 응답이다. 실패는 실패대로 따로 말한다 — 예전처럼
          "데이터 없음" 한 줄로 덮으면 조회 실패가 빈 화면으로 위장된다. */}
      {!curveQ.isLoading && !data.length && (
        <div className="flex items-center justify-center" style={{ height: 300 }}>
          {curveQ.isError
            ? <span className="text-[13px] text-[#ef4444] font-mono">자산 곡선을 불러오지 못했습니다</span>
            : <EmptyHoldings />}
        </div>
      )}

      {!curveQ.isLoading && data.length > 0 && (
      <div style={{ margin: '0 12px 8px', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 4 }}>
      <div
        ref={chartContainerRef}
        // 세로 스크롤은 브라우저에 맡긴다 — 모바일에서 확대 드래그를 꺼도
        // (위 handleContainerMouseDown 참고) 제스처 인식 자체는 브라우저가
        // 더 먼저 하므로 이 힌트가 없으면 여전히 스크롤이 걸릴 수 있다.
        style={{ width: '100%', height: 300, position: 'relative', touchAction: 'pan-y' }}
        onMouseMove={handleNativeMouseMove}
        onMouseLeave={handleNativeMouseLeave}
        onMouseDown={handleContainerMouseDown}
        // 터치는 mouseleave 가 안 와서 손을 떼도 십자선/툴팁이 안 사라진다.
        // 손 뗄 때(pointerup)는 무조건 지운다 — 마우스는 이미 mouseleave 로 되므로 겹쳐도 무해하다.
        onPointerUp={e => { if (e.pointerType === 'touch') handleNativeMouseLeave() }}
      >
        {/* 가로 십자선: HTML overlay로 정확한 마우스 Y 위치에 표시 */}
        {crosshairX && chartMouseY != null && !dragBounds && (
          <div style={{
            position: 'absolute',
            top: chartMouseY,
            left: 52,
            right: 8,
            height: 1,
            background: 'rgba(255,255,255,0.22)',
            pointerEvents: 'none',
            zIndex: 10,
          }} />
        )}
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={displayData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleChartMouseMove}
          onMouseUp={handleMouseUp}
          onDoubleClick={resetZoom}
          style={{ cursor: 'crosshair' }}
        >
          <defs>
            <linearGradient id="gPort" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#00e6ff" stopOpacity={0.25} />
              <stop offset="100%" stopColor="#00e6ff" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="gSP" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#dc143c" stopOpacity={0.12} />
              <stop offset="100%" stopColor="#dc143c" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="gNQ" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#a78bfa" stopOpacity={0.15} />
              <stop offset="100%" stopColor="#a78bfa" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="1 8" stroke="#111827" vertical={false} />
          <XAxis dataKey="date"
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false} axisLine={false}
            ticks={xAxisTicks}
            tickFormatter={fmtCurveDate}
          />
          <YAxis
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false} axisLine={false}
            tickFormatter={v => `${v.toFixed(1)}%`}
            width={52}
            domain={yDomain}
          />
          <Tooltip content={renderTooltip} />
          {(bm === 'benchmark' || bm === 'both') && (
            <Area key="benchmark_pct" type="monotone" dataKey="benchmark_pct" stroke="#dc143c" strokeWidth={1.5}
              strokeDasharray="4 3" fill="url(#gSP)" dot={false}
              isAnimationActive={false} />
          )}
          {(bm === 'nasdaq' || bm === 'both') && (
            <Area key="nasdaq" type="monotone" dataKey="nasdaq" stroke="#a78bfa" strokeWidth={1.5}
              strokeDasharray="4 3" fill="url(#gNQ)" dot={false}
              isAnimationActive={false} />
          )}
          {/* 포트폴리오 라인 — 매매일에 컬러 포인트 표시 */}
          <Area key="port" type="monotone" dataKey="port" stroke="#00e6ff" strokeWidth={2.5}
            fill="url(#gPort)"
            dot={tradeDot}
            activeDot={{ r: 5, fill: '#00e6ff', stroke: '#fff', strokeWidth: 2 }}
            isAnimationActive={false} />
          {/* 세로 십자선 — 드래그 중이 아닐 때 (가로선은 HTML overlay로 렌더) */}
          {crosshairX && chartMouseY != null && !dragBounds && (
            <ReferenceLine x={crosshairX}
              stroke="rgba(255,255,255,0.22)" strokeWidth={1} />
          )}
          {/* 드래그 줌 선택 직사각형 (X + Y 모두 표시) */}
          {dragBounds && selLeft && selRight && selLeft !== selRight && (
            <ReferenceArea
              x1={selLeft} x2={selRight}
              y1={pixelYToData(dragBounds.startY)}
              y2={pixelYToData(dragBounds.endY)}
              fill="#10b981" fillOpacity={0.12}
              stroke="#10b981" strokeOpacity={0.5} strokeWidth={1}
            />
          )}
        </AreaChart>
      </ResponsiveContainer>
      </div>
      </div>
      )}
    </div>
  )
}

// ── Holdings + History Panel ──────────────────────────────────────────────────
function HoldingsPanel({ holdQ, rawHoldings, onTickerClick }: { holdQ: any; rawHoldings: Record<string, any>; onTickerClick?: (ticker: string) => void }) {
  const names = useTickerNames()
  // 통화 표기는 시장을 따른다. 한국 화면에 (USD) 라고 적혀 있으면
  // 사용자가 원화를 달러로 잘못 입력한다.
  const tradeMarket = useMarket()

  // 종목을 무엇으로 부를지는 시장마다 다르다.
  // 미국은 티커(AAPL)가 이미 이름 노릇을 하므로 그대로 쓴다. 한국은 코드
  // (034020.KS)만 봐서는 어느 회사인지 알 수 없어 **이름만** 쓴다 — 코드를 밑에
  // 작게 붙이던 것도 뺐다 (사용자 요청). 행에 이름이 없으면 사전에서, 거기도
  // 없으면 티커로 떨어진다.
  const holdingLabel = (h: { ticker: string; name?: string | null }) =>
    tradeMarket === 'KR'
      ? (h.name && h.name !== h.ticker ? h.name : displayTicker(h.ticker, names))
      : h.ticker
  const labelOf = (ticker: string) =>
    holdingLabel((holdQ.data || []).find((h: any) => h.ticker === ticker) ?? { ticker })

  const curLabel = tradeMarket === 'KR' ? 'KRW' : 'USD'
  const qc = useQueryClient()
  const { isAuthed } = useAuth()
  const [view, setView] = useState<'holdings' | 'history'>('holdings')
  // 등록 마법사 — 'new' 는 기존 포트폴리오를 지우고 새로 만드는 경우다.
  const [wizard, setWizard] = useState<null | 'first' | 'new'>(null)

  // '아직 아무것도 없음' 판정. 종목이 없고 현금도 0일 때만 최초 등록으로 본다 —
  // 현금만 넣어 둔 사용자에게 "등록하기"를 띄우면 기존 입력을 지우라는 뜻이 된다.
  const nonCash = Object.keys(rawHoldings || {}).filter(t => t !== 'CASH')
  const cashQty = Number(rawHoldings?.CASH?.q ?? 0)
  const isEmptyPortfolio = isAuthed && nonCash.length === 0 && cashQty === 0

  // 투어 마지막 단계에서 '등록 시작'을 누르면 곧바로 등록 마법사를 연다.
  // 기존 포트폴리오가 없으면(가입 직후가 대부분) 'first' 로 열어 삭제 경고를
  // 건너뛴다 — 지울 것이 없는데 "기존 포트폴리오가 삭제됩니다"를 보여주면 안 된다.
  const { wantsSetup, clearWantsSetup } = useTour()
  useEffect(() => {
    if (wantsSetup) { setWizard(isEmptyPortfolio ? 'first' : 'new'); clearWantsSetup() }
  }, [wantsSetup, clearWantsSetup, isEmptyPortfolio])

  // 알림·확인 팝업. 브라우저 기본 alert/confirm 은 앱과 생김새가 따로 놀고
  // 라이트 모드에서 특히 이질적이라 쓰지 않는다.
  const [cashAlert, setCashAlert] = useState('')
  const [confirmDlg, setConfirmDlg] = useState<
    { title: string; message?: string; onOk: () => void } | null
  >(null)

  const afterSetup = () => {
    setWizard(null)
    // 보유·거래·지표·그래프가 전부 바뀌므로 관련 캐시를 통째로 무효화한다.
    for (const k of ['holdings-detail', 'holdings-raw', 'trades', 'portfolio-metrics',
                     'equity-curve', 'sector-weights', 'earnings', 'market-news']) {
      qc.invalidateQueries({ queryKey: [k] })
    }
  }

  // 일변동률 컬럼 헤더: 실시간인지 / 어느 거래일 종가 기준인지 표시.
  // 각 행이 as_of·is_live 를 갖고 있으므로 여기서 파생한다 (metrics 를 prop 으로 받지 않음).
  const chgHeader = dailyChangeHeader(holdQ.data)

  // ── Holdings edit state ────────────────────────────────────────────────
  const [editTicker, setEditTicker] = useState<string | null>(null)
  const [editVals,   setEditVals]   = useState({ q: 0, avg: 0, sector: 'Other' })
  const editValsRef = useRef({ q: 0, avg: 0, sector: 'Other' })

  // editValsRef: stale closure 방지용 — 입력값 최신 상태 추적
  useEffect(() => { editValsRef.current = editVals }, [editVals])

  // ── SELL 인라인 폼 state ───────────────────────────────────────────────
  const [sellTicker,       setSellTicker]       = useState<string | null>(null)
  const [sellVals,         setSellVals]         = useState({ q: 0, price: 0, date: '' })
  const sellValsRef = useRef({ q: 0, price: 0, date: '' })
  const [sellPriceLoading, setSellPriceLoading] = useState(false)
  // 현재가는 참고용 표시 전용 — sellVals.price(실제 매도가)에는 자동으로 채우지 않는다.
  const [sellCurrentPrice, setSellCurrentPrice] = useState<number | null>(null)
  // sellValsRef: stale closure 방지 — 입력값 최신 상태 추적
  useEffect(() => { sellValsRef.current = sellVals }, [sellVals])

  // ── 자동 섹터 분류: 페이지 최초 로드 시 Other 섹터 종목 자동 분류 ────────
  const autoSectorRan = useRef(false)
  useEffect(() => {
    if (autoSectorRan.current || holdQ.isLoading) return
    const holdings: any[] = holdQ.data || []
    const hasOther = holdings.some((h: any) => !h.sector || h.sector === 'Other')
    if (hasOther) {
      autoSectorRan.current = true
      autoDetectSectors().then(res => {
        if (res.count > 0) {
          qc.invalidateQueries({ queryKey: ['holdings-detail'] })
          qc.invalidateQueries({ queryKey: ['sector-weights'] })
          // holdings-raw 도 무효화 — staleTime 5분 + placeholderData 때문에
          // 무효화하지 않으면 도넛의 섹터 범례와 종목 인덱스가 5분간 어긋난다
          qc.invalidateQueries({ queryKey: ['holdings-raw'] })
        }
      }).catch(() => {})
    } else if (holdings.length > 0) {
      autoSectorRan.current = true  // 이미 모두 분류됨
    }
  }, [holdQ.data, holdQ.isLoading, qc])

  // ── Cash state ─────────────────────────────────────────────────────────
  const cashBalance = fv((rawHoldings?.CASH as any)?.q)
  const hasCash     = 'CASH' in (rawHoldings || {})
  const [cashOpen,  setCashOpen]  = useState(false)
  const [cashAmt,   setCashAmt]   = useState(0)
  const [cashDate,  setCashDate]  = useState(new Date().toISOString().slice(0, 10))
  const [cashType,  setCashType]  = useState<'deposit' | 'withdraw'>('deposit')
  const cashMut = useMutation({
    mutationFn: () => {
      if (!hasCash) {
        return addHolding('CASH', { q: cashAmt, avg: 1, sector: 'Cash', date: cashDate })
      }
      // 예전에는 Math.max(0, ...) 로 깎았다. 그러면 출금액이 조용히 줄어들어
      // 사용자는 요청한 금액이 빠진 줄 알고 장부가 어긋난다.
      if (cashType === 'withdraw' && cashAmt > cashBalance + 1e-6) {
        return Promise.reject(new Error(
          `현금이 부족합니다.\n출금 요청 ${formatPrice(cashAmt)} · 보유 ${formatPrice(cashBalance)}`))
      }
      const newQ = cashType === 'deposit' ? cashBalance + cashAmt : cashBalance - cashAmt
      return updateHolding('CASH', { q: newQ, avg: 1, date: cashDate })
    },
    onSuccess: () => {
      setCashOpen(false); setCashAmt(0)
      qc.invalidateQueries({ queryKey: ['holdings-raw'] })
      qc.invalidateQueries({ queryKey: ['holdings-detail'] })
      qc.invalidateQueries({ queryKey: ['equity-curve'] })
      qc.invalidateQueries({ queryKey: ['portfolio-metrics'] })
    },
    onError: (e: any) => setCashAlert(e?.response?.data?.detail || e.message),
  })

  // ── Trade form state ───────────────────────────────────────────────────
  const todayStr = useMemo(() => new Date().toISOString().slice(0, 10), [])
  const [form,        setForm]        = useState({ ticker: '', type: 'BUY', q: 0, price: 0, date: todayStr })
  // 입력칸에 **보이는** 글자. form.ticker 는 거래·시세 조회에 보내는 티커다.
  // 둘을 한 값으로 두면 한국 종목을 고른 뒤 칸에 '005930.KS' 가 남는다 —
  // 목록에서 고르면 칸에는 이름을, form.ticker 에는 티커를 넣는다.
  const [tickerText,  setTickerText]  = useState('')
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [showSug,     setShowSug]     = useState(false)
  const [sugIdx,      setSugIdx]      = useState(-1)
  const [tickerError, setTickerError] = useState('')
  const [priceLoading,setPriceLoading]= useState(false)
  // 현재가는 참고용 표시 전용 — form.price(평단가/거래가)에는 자동으로 채우지 않는다.
  const [currentPrice, setCurrentPrice] = useState<number | null>(null)
  // 입력칸이 두 번 그려진다(데스크탑 하단 바 · 모바일 팝업). 한 ref 를 같이 쓰면
  // 팝업이 닫힐 때 React 가 ref 를 null 로 비워, 남아 있는 데스크탑 칸의 제안
  // 목록이 기준 요소를 잃는다. 그래서 칸마다 따로 둔다.
  const tickerInputRef = useRef<HTMLInputElement>(null)
  const tickerInputRefMobile = useRef<HTMLInputElement>(null)
  // 모바일 전용 — "추가 매수/매도" 팝업 표시 여부
  const [showTradeModal, setShowTradeModal] = useState(false)

  // ── History edit state ─────────────────────────────────────────────────
  const [editTradeId,   setEditTradeId]   = useState<number | null>(null)
  const [editTradeVals, setEditTradeVals] = useState({ date: '', q: 0, price: 0, memo: '' })

  const tradesQ = useQuery({ queryKey: ['trades'], queryFn: getTrades, enabled: view === 'history', staleTime: 30_000 })

  const _invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ['holdings-detail'] })
    qc.invalidateQueries({ queryKey: ['holdings-raw'] })
    qc.invalidateQueries({ queryKey: ['trades'] })
    qc.invalidateQueries({ queryKey: ['sector-weights'] })
    qc.invalidateQueries({ queryKey: ['equity-curve'] })
    qc.invalidateQueries({ queryKey: ['portfolio-metrics'] })
  }

  const updateMut = useMutation({
    mutationFn: ({ ticker, q, avg, sector }: any) => updateHolding(ticker, { q, avg, sector }),
    onSuccess: _invalidateAll,
    onError: (e: any) => setCashAlert(`편집 실패: ${e?.response?.data?.detail || e.message}`),
  })
  const deleteMut = useMutation({
    mutationFn: (ticker: string) => deleteHolding(ticker),
    onSuccess: _invalidateAll,
    onError: (e: any) => setCashAlert(`삭제 실패: ${e?.response?.data?.detail || e.message}`),
  })
  const tradeMut = useMutation({
    mutationFn: (f: typeof form) => postTrade({ ticker: f.ticker, type: f.type as any, q: f.q, price: f.price, date: f.date }),
    onSuccess: (_data, vars) => {
      _invalidateAll()
      setForm(f => ({ ...f, ticker: '', q: 0, price: 0, date: new Date().toISOString().slice(0, 10) }))
      setTickerText('')
      setCurrentPrice(null)
      setTickerError('')
      setShowTradeModal(false)
      // BUY 후 섹터 자동 분류 (백그라운드 스레드 완료 대기 후 재조회)
      if (vars.type === 'BUY') {
        setTimeout(() => {
          autoDetectSectors().then(res => {
            if (res.count > 0) _invalidateAll()
          }).catch(() => {})
        }, 3000)
      }
    },
    onError: (e: any) => {
      const msg = e?.response?.data?.detail || e.message
      // 잔고 부족은 입력 실수가 아니라 '지금은 불가능한 거래'다 — 눈에 띄게 알린다.
      if (String(msg).includes('현금이 부족')) setCashAlert(msg)
      else setTickerError(`거래 실패: ${msg}`)
    },
  })
  const sellMut = useMutation({
    mutationFn: ({ ticker, q, price, date }: any) => postTrade({ ticker, type: 'SELL', q, price, date }),
    onSuccess: () => { setSellTicker(null); _invalidateAll() },
    onError: (e: any) => setCashAlert(e?.response?.data?.detail || e.message),
  })
  const updateTradeMut = useMutation({
    mutationFn: ({ id, ticker, type, vals }: any) => updateTrade(id, {
      date: vals.date, ticker, type, q: vals.q, price: vals.price, memo: vals.memo,
    }),
    // 거래 수정 → holdings도 재계산됨 (백엔드 _recalculate_holding_from_trades)
    onSuccess: () => { setEditTradeId(null); _invalidateAll() },
    onError: (e: any) => setCashAlert(`거래 수정 실패: ${e?.response?.data?.detail || e.message}`),
  })
  const deleteTradeMut = useMutation({
    mutationFn: (id: number) => deleteTrade(id),
    // 거래 삭제 → holdings도 재계산됨 (백엔드 _recalculate_holding_from_trades)
    onSuccess: _invalidateAll,
    onError: (e: any) => setCashAlert(`거래 삭제 실패: ${e?.response?.data?.detail || e.message}`),
  })
  // SELL 인라인 폼 열기 — 현재 보유수량은 채우지만, 매도가는 사용자가 직접 입력한다.
  // 현재가는 참고용으로만 별도 표시한다(오늘 시세를 그대로 매도가로 등록해버리는
  // 걸 막기 위해 — 실제 체결가는 오늘 시세와 다를 수 있다).
  const openSell = async (h: any) => {
    setSellTicker(h.ticker)
    setSellVals({ q: h.qty ?? 0, price: 0, date: new Date().toISOString().slice(0, 10) })
    setSellCurrentPrice(h.current_price ?? null)
    if (!h.current_price) {
      setSellPriceLoading(true)
      try {
        const r = await getTickerPrice(h.ticker)
        setSellCurrentPrice(r.price)
      } catch {} finally { setSellPriceLoading(false) }
    }
  }

  // ── Ticker 검색 (전체 미국 상장) ─────────────────────────────────────
  // 입력 중 300ms debounce 후 API 검색
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleTickerChange = (val: string) => {
    const upper = val.toUpperCase()
    // 직접 친 글자는 그대로 티커 후보다 — 미국 티커를 끝까지 치고 목록을 안 거쳐도
    // 예전처럼 거래가 된다. 목록에서 고르면 selectSuggestion 이 티커로 바꾼다.
    setTickerText(upper)
    setForm(f => ({ ...f, ticker: upper }))
    setTickerError('')
    setSugIdx(-1)
    setCurrentPrice(null)   // 티커가 바뀌면 이전 현재가 표시는 더 이상 유효하지 않다
    if (upper.length === 0) { setSuggestions([]); setShowSug(false); return }
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await searchTickers(upper)
        setSuggestions(res)
        setShowSug(res.length > 0)
      } catch { setSuggestions([]); setShowSug(false) }
    }, 200)
  }

  const selectSuggestion = async (s: Suggestion) => {
    const label = selectionLabel(tradeMarket, s.ticker, s.name || names[s.ticker])
    setForm(f => ({ ...f, ticker: s.ticker }))
    setTickerText(label)
    setSuggestions([]); setShowSug(false); setSugIdx(-1); setTickerError('')
    await fetchCurrentPrice(s.ticker, label)
  }

  /** 현재가를 조회해 참고용으로만 표시한다 — form.price(직접 입력하는 거래 단가)는
      건드리지 않는다. 자동으로 채우면 사용자가 알아채지 못하고 오늘 시세를
      그대로 평단가로 등록해버릴 수 있다. */
  const fetchCurrentPrice = async (ticker: string, label: string = ticker) => {
    if (!ticker) return
    setPriceLoading(true)
    try {
      const result = await getTickerPrice(ticker)
      setCurrentPrice(result.price)
      setTickerError('')
    } catch {
      setCurrentPrice(null)
      setTickerError(`"${label}"은(는) 유효하지 않은 종목입니다. 다시 시도해주세요.`)
      setForm(f => ({ ...f, ticker: '' }))
      setTickerText('')
    } finally {
      setPriceLoading(false)
    }
  }

  const handleTickerKeyDown = (e: React.KeyboardEvent) => {
    if (!showSug || suggestions.length === 0) return
    // 한글 조합 중(isComposing)의 키는 IME 가 조합을 확정하는 입력이다. 여기서
    // 받으면 확정용 Enter 가 선택으로도 처리될 수 있어 넘긴다. (headless 브라우저는
    // IME 를 거치지 않아 이 분기는 실측하지 못했다.)
    if (e.nativeEvent.isComposing) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setSugIdx(i => moveHighlight(i, e.key === 'ArrowDown' ? 1 : -1, suggestions.length))
    } else if (e.key === 'Enter') {
      // 목록이 보이면 Enter 는 목록에서 고른다 (lib/suggestions.ts 의 규칙).
      // 예전에는 화살표로 고른 경우만 받아서 "삼성" + Enter 가 아무 일도 안 했다.
      const pick = pickOnEnter(tickerText, suggestions, sugIdx)
      if (pick) { e.preventDefault(); selectSuggestion(pick) }
    } else if (e.key === 'Escape') {
      setShowSug(false); setSugIdx(-1)
    }
  }

  const handleTickerBlur = () => {
    setTimeout(() => {
      setShowSug(false); setSugIdx(-1)
      const t = form.ticker.trim()
      // 가격을 이미 입력했어도 참고용 현재가는 갱신한다(평단가엔 영향 없음).
      if (t && currentPrice == null) fetchCurrentPrice(t)
    }, 180)
  }

  // QTY 마우스 스크롤
  const handleQtyWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    setForm(f => ({ ...f, q: Math.max(0, f.q + (e.deltaY < 0 ? 1 : -1)) }))
  }

  // History 최신순
  const trades = (tradesQ.data || []).slice().reverse()

  // 매수/매도 입력 필드 — 데스크탑 하단 바와 모바일 팝업이 이 하나를 공유한다.
  // (똑같은 마크업을 두 곳에 따로 두면 나중에 한쪽만 고쳐 어긋나기 쉽다.)
  const renderTradeFields = (inMobilePopup = false) => (
    <>
      {tickerError && (
        <div className="text-[11px] text-[#ef4444] flex items-center gap-1">
          <X className="w-3 h-3" />{tickerError}
        </div>
      )}
      <div className="flex items-center gap-2 flex-wrap">
        {/* Ticker — 시장별 상장 종목 검색 (한국은 이름으로도 찾는다) */}
        <div className="relative">
          <input
            ref={inMobilePopup ? tickerInputRefMobile : tickerInputRef}
            autoFocus={inMobilePopup}
            value={tickerText}
            onChange={e => handleTickerChange(e.target.value)}
            onBlur={handleTickerBlur}
            onKeyDown={handleTickerKeyDown}
            placeholder={MARKETS[tradeMarket].tickerExample}
            autoComplete="off"
            className={cn(
              'w-32 bg-[#0b1220] border border-[#1e2d40] text-sm text-[#e2e8f0] rounded px-2 py-1.5 placeholder-[#334155] focus:outline-none focus:border-[#10b981]',
              // 이름(한글)에는 고정폭을 쓰지 않는다 — 글자 사이가 벌어진다.
              tradeMarket !== 'KR' && 'font-mono',
            )}
          />
          {/* 목록은 입력칸 **아래**로 편다 (아래가 모자랄 때만 위로). 보유 패널이
              overflow-hidden 이라 칸 옆에 absolute 로 붙이면 패널 경계에서 잘려
              첫 줄만 보였다 — 그래서 body 로 띄운다 (SuggestionList 주석). */}
          <SuggestionList
            anchorRef={inMobilePopup ? tickerInputRefMobile : tickerInputRef}
            open={showSug}
            items={suggestions}
            highlighted={sugIdx}
            onPick={selectSuggestion}
            minWidth={180}
          />
        </div>

        {/* BUY / SELL */}
        <select value={form.type} onChange={e => setForm(f => ({ ...f, type: e.target.value }))}
          className="bg-[#0b1220] border border-[#1e2d40] text-sm text-[#cbd5e1] rounded px-2 py-1.5 focus:outline-none focus:border-[#10b981]">
          <option value="BUY">매수</option>
          <option value="SELL">매도</option>
        </select>

        {/* QTY — 스크롤 지원 */}
        <input type="number" value={form.q || ''}
          onChange={e => setForm(f => ({ ...f, q: +e.target.value }))}
          onWheel={handleQtyWheel}
          placeholder="수량" min={0} step={1}
          className="w-14 bg-[#0b1220] border border-[#1e2d40] text-sm font-mono text-[#e2e8f0] rounded px-2 py-1.5 placeholder-[#334155] focus:outline-none focus:border-[#10b981]"
        />

        {/* 현재가 — 참고용 표시 전용. 옆 입력칸들과 같은 디자인으로 맞춘다.
            평단가/거래가 입력에 자동으로 들어가지 않는다. */}
        {form.ticker && (
          <div
            title="현재 시장가 — 참고용입니다. 자동으로 입력되지 않습니다."
            className="flex items-center gap-1.5 px-2 py-1.5 text-sm font-mono rounded border border-[#1e2d40] bg-[#0b1220] flex-shrink-0"
          >
            <span className="text-[10px] text-[#64748b]">현재가</span>
            <span className="text-[#e2e8f0]">{priceLoading ? '…' : formatPrice(currentPrice)}</span>
          </div>
        )}

        {/* Price — 사용자가 직접 입력하는 매수/매도 단가. 자동으로 채우지 않는다. */}
        <input {...moneyInputProps(form.price || '', price => setForm(f => ({ ...f, price })), tradeMarket)}
          placeholder={form.type === 'SELL' ? '매도가' : '매수가'}
          className="w-32 bg-[#0b1220] border border-[#1e2d40] text-sm font-mono text-[#e2e8f0] rounded px-2 py-1.5 placeholder-[#334155] focus:outline-none focus:border-[#10b981]"
        />

        {/* Date — 매수/매도 날짜 */}
        <input type="date" value={form.date}
          onChange={e => setForm(f => ({ ...f, date: e.target.value }))}
          max={todayStr}
          className="bg-[#0b1220] border border-[#1e2d40] text-sm font-mono text-[#cbd5e1] rounded px-2 py-1.5 focus:outline-none focus:border-[#10b981] [color-scheme:dark]"
        />

        {/* Submit — onPointerDown으로 ticker blur보다 먼저 발화 */}
        <button type="button"
          onPointerDown={e => {
            e.preventDefault()
            if (!form.ticker || !form.q || !form.price || tradeMut.isPending) return
            tradeMut.mutate(form)
          }}
          disabled={!form.ticker || !form.q || !form.price || tradeMut.isPending}
          className="bg-[#10b981] hover:bg-[#059669] disabled:opacity-40 text-white rounded px-3 py-1.5 transition-colors text-sm font-bold">
          <Plus className="w-4 h-4" />
        </button>
      </div>
    </>
  )

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* 탭 헤더 */}
      <div className="flex items-center border-b border-[#1e2d40] flex-shrink-0 bg-[#070d18]">
        <button onClick={() => setView('holdings')}
          className={cn('text-[11px] font-bold tracking-[3px] uppercase px-3 py-2.5 transition-colors',
            view === 'holdings' ? 'text-[#e2e8f0] border-b-2 border-[#10b981]' : 'text-[#94a3b8] hover:text-[#94a3b8]')}>
          보유 종목
        </button>
        <button onClick={() => setView('history')}
          className={cn('flex items-center gap-1.5 text-[11px] font-bold tracking-[3px] uppercase px-3 py-2.5 transition-colors',
            view === 'history' ? 'text-[#e2e8f0] border-b-2 border-[#10b981]' : 'text-[#94a3b8] hover:text-[#94a3b8]')}>
          <History className="w-3 h-3" />거래 내역
        </button>

        {/* 모바일 전용 — 상시 노출 거래 입력 폼 대신 버튼+팝업으로 (화면 사용 효율) */}
        {isAuthed && (
          <button
            onClick={() => setShowTradeModal(true)}
            className="md:hidden ml-auto mr-2 flex items-center gap-1 rounded border border-[#10b981]/40 bg-[#10b981]/10 px-2 py-1 text-[10px] font-bold text-[#10b981]">
            <Plus className="w-2.5 h-2.5" />추가 매수/매도
          </button>
        )}

        {/* 이미 등록된 상태에서만 노출 — 비어 있으면 가운데 큰 버튼으로 유도한다 */}
        {isAuthed && !isEmptyPortfolio && (
          <button
            onClick={() => setWizard('new')}
            title="기존 포트폴리오를 지우고 새로 등록합니다"
            className="ml-auto mr-2 flex items-center gap-1 rounded border border-[#1e2d40] px-2 py-1 text-[10px] text-[#4a5568] transition hover:border-[#10b981]/40 hover:text-[#94a3b8]">
            <Plus className="w-2.5 h-2.5" />포트폴리오 새로 등록
          </button>
        )}
      </div>

      <ConfirmDialog
        open={!!confirmDlg}
        tone="danger"
        title={confirmDlg?.title ?? ''}
        message={confirmDlg?.message}
        confirmText="삭제"
        onConfirm={() => { confirmDlg?.onOk(); setConfirmDlg(null) }}
        onCancel={() => setConfirmDlg(null)}
      />

      <ConfirmDialog
        open={!!cashAlert}
        alert
        tone="danger"
        title="확인해 주세요"
        message={cashAlert}
        confirmText="확인"
        onConfirm={() => setCashAlert('')}
        onCancel={() => setCashAlert('')}
      />

      {wizard && (
        <SetupWizard
          open
          replace={wizard === 'new'}
          onClose={() => setWizard(null)}
          onDone={afterSetup}
        />
      )}

      {/* 조회 실패를 '보유 없음'으로 보여주지 않는다 (그 반대도 마찬가지다).
          빈 상태는 **데이터**로 판단한다. 예전에는 `isEmptyPortfolioError(holdQ.error)`
          였는데 /holdings-detail 은 처음부터 200 + `[]` 를 줬다 — 즉 이 분기는
          한 번도 그려진 적이 없고, 보유가 없는 사용자는 빈 표만 봤다. */}
      {view === 'holdings' && !holdQ.isError && !holdQ.isLoading && !(holdQ.data || []).length && (
        <div className="flex-1"><EmptyHoldings /></div>
      )}
      {view === 'holdings' && holdQ.isError && (
        <div className="flex-1 flex flex-col items-center justify-center gap-2 text-xs text-[#ef4444]">
          <span>보유 종목을 불러오지 못했습니다</span>
          <button onClick={() => holdQ.refetch()}
            className="px-3 py-1 rounded border border-[#ef4444]/40 hover:bg-[#ef4444]/10">
            재시도
          </button>
        </div>
      )}

      {/* 아직 아무것도 등록하지 않은 상태 — 등록으로 유도한다 */}
      {view === 'holdings' && !holdQ.isError && isEmptyPortfolio && (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 p-6 text-center">
          <Briefcase className="w-9 h-9 text-[#1e2d40]" />
          <div>
            <p className="text-sm text-[#cbd5e1]">등록된 포트폴리오가 없습니다</p>
            <p className="mt-1 text-[11px] text-[#4a5568]">
              보유 종목과 현금을 등록하면 수익률과 그래프가 계산됩니다
            </p>
          </div>
          <button
            onClick={() => setWizard('first')}
            className="mt-1 flex items-center gap-1.5 rounded-lg bg-[#10b981] px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-[#059669]">
            <Plus className="w-4 h-4" />포트폴리오 등록하기
          </button>
        </div>
      )}

      {/* ── Holdings 뷰 ─────────────────────────────────────────────────── */}
      {view === 'holdings' && !holdQ.isError && !isEmptyPortfolio && (
        <>
          <div className="flex-1 overflow-y-auto min-h-0">
            <table className="w-full">
              <thead className="sticky top-0 bg-[#07101c] z-10">
                <tr className="text-[#94a3b8] border-b border-[#1e2d40]">
                  {['티커','평단가','수량','현재가', chgHeader,
                    '누적수익률','비중','',''].map((hd, i) => (
                    <th key={i} className="text-left py-2.5 px-2.5 font-semibold text-[11px] tracking-wider whitespace-nowrap">{hd}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(holdQ.data || []).filter((h: any) => h.ticker !== 'CASH').map((h: any) => (
                  <React.Fragment key={h.ticker}>
                  <tr className="border-b border-[#0f172a] hover:bg-[#0a1525] group transition-colors">
                    {editTicker === h.ticker ? (
                      <>
                        <td className="py-2 px-2.5"><TickerLabel ticker={h.ticker} name={h.name} primaryClass="text-sm font-bold" /></td>
                        <td className="py-2 px-2.5">
                          <input {...moneyInputProps(editVals.avg, avg => setEditVals(v => ({ ...v, avg })), tradeMarket)}
                            className="w-16 bg-[#1e2d40] border border-[#334155] text-sm text-[#e2e8f0] rounded px-2 py-1" />
                        </td>
                        <td className="py-2 px-2.5">
                          <input type="number" value={editVals.q}
                            onChange={e => setEditVals(v => ({ ...v, q: +e.target.value }))}
                            className="w-14 bg-[#1e2d40] border border-[#334155] text-sm text-[#e2e8f0] rounded px-2 py-1" />
                        </td>
                        <td colSpan={4} />
                        <td className="py-2 px-2.5">
                          <div className="flex gap-1.5">
                            {/* onPointerDown: blur보다 먼저 발화, editValsRef로 최신값 보장 */}
                            <button type="button"
                              onPointerDown={e => {
                                e.preventDefault()
                                const v = editValsRef.current
                                // 입력칸을 비우면 +'' === 0 이 되어 수량 0·평단 0 이 그대로 저장된다.
                                // 저장 전에 막고 편집 상태를 유지해 값을 되찾을 수 있게 한다.
                                if (!Number.isFinite(v.q) || v.q <= 0) {
                                  setCashAlert('수량은 0보다 커야 합니다.')
                                  return
                                }
                                if (!Number.isFinite(v.avg) || v.avg < 0) {
                                  setCashAlert('평단가가 올바르지 않습니다.')
                                  return
                                }
                                setEditTicker(null)
                                updateMut.mutate({ ticker: h.ticker, q: v.q, avg: v.avg, sector: h.sector })
                              }}
                              className="text-[#10b981] hover:text-[#34d399]">
                              <Check className="w-4 h-4" />
                            </button>
                            <button type="button"
                              onPointerDown={e => {
                                e.preventDefault()
                                setEditTicker(null)
                              }}
                              className="text-[#ef4444] hover:text-[#f87171]">
                              <X className="w-4 h-4" />
                            </button>
                          </div>
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="py-2 px-2.5">
                          {/* 한국 종목은 코드(034020.KS)만으로 회사를 알 수 없다 —
                              이름만 쓴다. 미국은 티커가 곧 이름 역할을 한다. */}
                          <span
                            onClick={() => onTickerClick?.(h.ticker)}
                            className={cn('block', onTickerClick ? 'cursor-pointer hover:text-[#10b981] transition-colors' : '')}
                          >
                            <span className="font-bold text-[14px] text-[#e2e8f0]">
                              {holdingLabel(h)}
                            </span>
                          </span>
                        </td>
                        <td className="py-2 px-2.5 font-mono text-[12px] text-[#cbd5e1]">{formatPrice(h.avg_cost)}</td>
                        <td className="py-2 px-2.5 font-mono text-[12px] text-[#cbd5e1]">{fv(h.qty)}</td>
                        <td className="py-2 px-2.5 font-mono text-[12px] text-[#cbd5e1]">{formatPrice(h.current_price)}</td>
                        <td className="py-2 px-2.5 font-mono text-[13px] font-bold" style={{ color: chgColor(h.chg_pct) }}
                          title={h.as_of ? `${h.as_of} 기준${h.is_live ? ' (실시간)' : ' 종가'}` : '데이터 부족'}>
                          {fp(h.chg_pct, 2)}
                        </td>
                        <td className="py-2 px-2.5 font-mono text-[13px] font-bold" style={{ color: chgColor(h.pnl_pct) }}>
                          {fp(h.pnl_pct, 2)}
                        </td>
                        {/* 소수점 0자리로 반올림하면 1% 미만 포지션이 전부 "0%"가 되어
                            실제 0과 구분되지 않는다 → 1% 미만은 소수 2자리로 표시 */}
                        <td className="py-2 px-2.5 font-mono text-[12px] text-[#cbd5e1]">
                          {isNA(h.weight)
                            ? NA
                            : `${fn((h.weight as number) * 100, (h.weight as number) * 100 < 1 ? 2 : 1)}%`}
                        </td>
                        {/* SELL 버튼 — 데스크탑은 hover 시에만, 모바일은 hover 가 없어 항상 보인다 */}
                        <td className="py-2 px-1 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                          <button type="button"
                            onPointerDown={e => { e.preventDefault(); openSell(h) }}
                            className="text-[10px] font-bold text-[#f59e0b] border border-[#f59e0b]/40 rounded px-1.5 py-0.5 hover:bg-[#f59e0b]/15 transition-colors">
                            매도
                          </button>
                        </td>
                        {/* Edit / Delete — 마찬가지로 모바일에서는 항상 보인다 */}
                        <td className="py-2 px-1 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                          <div className="flex gap-1">
                            <button type="button"
                              onPointerDown={e => {
                                e.preventDefault()
                                setEditTicker(h.ticker)
                                const newVals = { q: h.qty, avg: h.avg_cost, sector: h.sector || 'Other' }
                                setEditVals(newVals)
                                editValsRef.current = newVals
                              }}
                              className="text-[#94a3b8] hover:text-[#10b981] transition-colors">
                              <Edit3 className="w-3.5 h-3.5" />
                            </button>
                            <button type="button"
                              onClick={() => {
                                setConfirmDlg({
                                  title: `${holdingLabel(h)} 보유를 삭제할까요?`,
                                  message: '해당 종목의 거래 이력도 함께 삭제되며, 사용된 현금은 되돌아옵니다.',
                                  onOk: () => deleteMut.mutate(h.ticker),
                                })
                              }}
                              className="text-[#94a3b8] hover:text-[#ef4444] transition-colors">
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </td>
                      </>
                    )}
                  </tr>
                  {/* 인라인 SELL 폼 — 데스크탑 전용. 모바일은 아래 팝업(모달)으로 대신한다. */}
                  {sellTicker === h.ticker && (
                    <tr className="hidden md:table-row border-b border-[#f59e0b]/20 bg-[#0a0e18]">
                      <td colSpan={9} className="px-3 py-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[#f59e0b] font-bold text-[12px] flex-shrink-0">매도 {holdingLabel(h)}</span>
                          {/* 현재가 — 참고용 표시 전용. 매도가 입력에 자동으로 들어가지 않는다. */}
                          <span className="text-[11px] text-[#64748b] font-mono flex-shrink-0"
                            title="현재 시장가 — 참고용입니다. 자동으로 입력되지 않습니다.">
                            현재가 {sellPriceLoading ? '…' : formatPrice(sellCurrentPrice)}
                          </span>
                          <input type="number" value={sellVals.q || ''}
                            onChange={e => setSellVals(v => ({ ...v, q: +e.target.value }))}
                            placeholder="수량" min={0.001} step={0.001}
                            className="w-16 bg-[#1e2d40] border border-[#334155] text-sm text-[#e2e8f0] rounded px-2 py-1" />
                          <input {...moneyInputProps(sellVals.price || '', price => setSellVals(v => ({ ...v, price })), tradeMarket)}
                            placeholder="매도가"
                            className="w-28 bg-[#1e2d40] border border-[#334155] text-sm text-[#e2e8f0] rounded px-2 py-1" />
                          <input type="date" value={sellVals.date}
                            onChange={e => setSellVals(v => ({ ...v, date: e.target.value }))}
                            max={todayStr}
                            className="bg-[#1e2d40] border border-[#334155] text-sm text-[#cbd5e1] rounded px-2 py-1 [color-scheme:dark]" />
                          <button type="button"
                            onClick={() => {
                              const v = sellValsRef.current
                              if (v.q > 0 && v.price > 0 && !sellMut.isPending)
                                sellMut.mutate({ ticker: h.ticker, q: v.q, price: v.price, date: v.date })
                            }}
                            className="bg-[#f59e0b]/15 border border-[#f59e0b]/40 text-[#f59e0b] rounded px-3 py-1 text-[12px] font-bold hover:bg-[#f59e0b]/25 transition-colors">
                            {sellMut.isPending ? '…' : '확인'}
                          </button>
                          <button type="button" onClick={() => setSellTicker(null)}
                            className="text-[#94a3b8] hover:text-[#cbd5e1] text-[12px]">취소</button>
                        </div>
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>


          {/* 거래 입력 폼 — 데스크탑에서만 상시 노출. 모바일은 화면을 계속 차지하면
              효율이 떨어져서 "추가 매수/매도" 버튼 + 팝업(아래)으로 뺐다. */}
          <div className="hidden md:block flex-shrink-0 border-t border-[#1e2d40] px-3 py-2 bg-[#060b14] space-y-2">
            {renderTradeFields()}
          </div>
        </>
      )}

      {/* 모바일 전용 매수/매도 팝업 — 상단 "추가 매수/매도" 버튼으로 연다 */}
      {showTradeModal && (
        <div
          role="dialog" aria-modal="true" aria-label="추가 매수/매도"
          className="md:hidden fixed inset-0 z-[110] flex items-end justify-center bg-black/70 backdrop-blur-sm"
          onClick={() => setShowTradeModal(false)}
        >
          <div
            className="w-full max-h-[80vh] overflow-y-auto rounded-t-2xl border border-[#1e2d40] bg-[#0b0f1a] p-4 space-y-3"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-[#e2e8f0]">추가 매수/매도</h3>
              <button onClick={() => setShowTradeModal(false)} className="text-[#94a3b8] hover:text-[#e2e8f0]">
                <X className="w-5 h-5" />
              </button>
            </div>
            {renderTradeFields(true)}
          </div>
        </div>
      )}

      {/* 모바일 전용 매도 팝업 — 보유 종목 행의 "매도" 버튼으로 연다 */}
      {sellTicker && (
        <div
          role="dialog" aria-modal="true" aria-label={`${labelOf(sellTicker)} 매도`}
          className="md:hidden fixed inset-0 z-[110] flex items-end justify-center bg-black/70 backdrop-blur-sm"
          onClick={() => setSellTicker(null)}
        >
          <div
            className="w-full max-h-[80vh] overflow-y-auto rounded-t-2xl border border-[#1e2d40] bg-[#0b0f1a] p-4 space-y-3"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              {/* 이름(한글)에는 고정폭을 쓰지 않는다 — 글자 사이가 벌어진다. */}
              <h3 className={cn('text-sm font-bold text-[#f59e0b]', tradeMarket !== 'KR' && 'font-mono')}>매도 {labelOf(sellTicker)}</h3>
              <button onClick={() => setSellTicker(null)} className="text-[#94a3b8] hover:text-[#e2e8f0]">
                <X className="w-5 h-5" />
              </button>
            </div>
            {/* 현재가 — 참고용 표시 전용. 매도가 입력에 자동으로 들어가지 않는다. */}
            <div className="text-[11px] text-[#64748b] font-mono"
              title="현재 시장가 — 참고용입니다. 자동으로 입력되지 않습니다.">
              현재가 {sellPriceLoading ? '…' : formatPrice(sellCurrentPrice)}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <input type="number" value={sellVals.q || ''}
                onChange={e => setSellVals(v => ({ ...v, q: +e.target.value }))}
                placeholder="수량" min={0.001} step={0.001}
                className="w-20 bg-[#1e2d40] border border-[#334155] text-sm text-[#e2e8f0] rounded px-2 py-1.5" />
              <input {...moneyInputProps(sellVals.price || '', price => setSellVals(v => ({ ...v, price })), tradeMarket)}
                placeholder="매도가"
                className="w-28 bg-[#1e2d40] border border-[#334155] text-sm text-[#e2e8f0] rounded px-2 py-1.5" />
              <input type="date" value={sellVals.date}
                onChange={e => setSellVals(v => ({ ...v, date: e.target.value }))}
                max={todayStr}
                className="bg-[#1e2d40] border border-[#334155] text-sm text-[#cbd5e1] rounded px-2 py-1.5 [color-scheme:dark]" />
            </div>
            <div className="flex items-center gap-2 pt-1">
              <button type="button"
                onClick={() => {
                  const v = sellValsRef.current
                  const t = sellTicker
                  if (t && v.q > 0 && v.price > 0 && !sellMut.isPending)
                    sellMut.mutate({ ticker: t, q: v.q, price: v.price, date: v.date })
                }}
                disabled={sellMut.isPending}
                className="flex-1 bg-[#f59e0b]/15 border border-[#f59e0b]/40 text-[#f59e0b] rounded px-3 py-2 text-sm font-bold hover:bg-[#f59e0b]/25 transition-colors disabled:opacity-50">
                {sellMut.isPending ? '처리 중…' : '매도 확인'}
              </button>
              <button type="button" onClick={() => setSellTicker(null)}
                className="px-4 py-2 text-sm text-[#94a3b8] hover:text-[#cbd5e1]">취소</button>
            </div>
          </div>
        </div>
      )}

      {/* 현금 잔고 — 항상 표시 */}
      <div className="flex-shrink-0 border-t border-[#1e2d40] px-3 py-2 bg-[#060b14]">
        {!cashOpen ? (
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-[#94a3b8] font-bold tracking-wider uppercase">현금 잔고</span>
            <div className="flex items-center gap-2">
              <span className="font-mono text-[13px] text-[#cbd5e1]">
                {hasCash ? formatPrice(cashBalance) : '—'}
              </span>
              <button
                onClick={() => { setCashOpen(true); setCashType('deposit'); setCashAmt(0) }}
                className="text-[11px] text-[#10b981] hover:text-[#34d399] border border-[#1e3a5f] rounded px-2 py-0.5 transition-colors">
                {hasCash ? '입금/출금' : '+ 초기 투자금 설정'}
              </button>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] text-[#94a3b8] font-bold">현금</span>
            <select
              value={cashType}
              onChange={e => setCashType(e.target.value as 'deposit' | 'withdraw')}
              className="bg-[#0b1220] border border-[#1e2d40] text-[12px] text-[#cbd5e1] rounded px-2 py-1 focus:outline-none">
              <option value="deposit">입금</option>
              <option value="withdraw">출금</option>
            </select>
            <input
              {...moneyInputProps(cashAmt || '', setCashAmt, tradeMarket)}
              placeholder={`금액 (${curLabel})`}
              className="w-32 bg-[#0b1220] border border-[#1e2d40] text-[12px] font-mono text-[#e2e8f0] rounded px-2 py-1 focus:outline-none focus:border-[#10b981]"
            />
            <input
              type="date"
              value={cashDate}
              onChange={e => setCashDate(e.target.value)}
              className="bg-[#0b1220] border border-[#1e2d40] text-[12px] text-[#cbd5e1] rounded px-2 py-1 [color-scheme:dark] focus:outline-none"
            />
            <button
              onClick={() => { if (cashAmt > 0 && !cashMut.isPending) cashMut.mutate() }}
              disabled={!cashAmt || cashMut.isPending}
              className="bg-[#10b981]/80 hover:bg-[#059669] disabled:opacity-40 text-white rounded px-3 py-1 text-[12px] font-bold transition-colors">
              {cashMut.isPending ? '…' : '확인'}
            </button>
            <button onClick={() => setCashOpen(false)} className="text-[#94a3b8] hover:text-[#cbd5e1] text-[12px]">취소</button>
          </div>
        )}
      </div>

      {/* ── History 뷰 ──────────────────────────────────────────────────── */}
      {view === 'history' && (
        <div className="flex-1 overflow-y-auto min-h-0">
          {tradesQ.isLoading && <div className="text-center py-4 text-sm text-[#94a3b8]">로드 중…</div>}
          {!tradesQ.isLoading && trades.length === 0 && (
            <div className="flex items-center justify-center h-full text-sm text-[#94a3b8]">거래 기록 없음</div>
          )}
          <table className="w-full">
            <thead className="sticky top-0 bg-[#07101c] z-10">
              <tr className="text-[#94a3b8] border-b border-[#1e2d40]">
                {['날짜', 'Ticker', '유형', '수량', '가격', '메모', ''].map(h => (
                  <th key={h} className="text-left py-2.5 px-2 font-semibold text-[11px] tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t: any) => (
                <tr key={t.id} className="border-b border-[#0f172a] hover:bg-[#0a1525] group transition-colors">
                  {editTradeId === t.id ? (
                    <>
                      <td className="py-1.5 px-2">
                        <input type="date" value={editTradeVals.date}
                          onChange={e => setEditTradeVals(v => ({ ...v, date: e.target.value }))}
                          className="w-28 bg-[#1e2d40] border border-[#334155] text-[12px] text-[#e2e8f0] rounded px-1 py-1" />
                      </td>
                      <td className="py-1.5 px-2"><TickerLabel ticker={t.ticker} name={names[t.ticker]} primaryClass="text-sm font-bold" /></td>
                      <td className="py-1.5 px-2 text-[12px]"
                        style={{ color: t.type === 'ADD' || t.type === 'BUY' ? '#10b981' : '#ef4444' }}>
                        {t.type}
                      </td>
                      {t.ticker === 'CASH' ? (
                        <>
                          {/* 현금은 **금액**을 고친다. 저장 형식(q=금액, price=1)은 그대로다 —
                              현금 원장·자산곡선이 q 를 금액으로 읽는다. 수량 칸에 금액을
                              받으면 사용자는 '수량' 을 고치는 줄 안다. */}
                          <td className="py-1.5 px-2 font-mono text-[12px] text-[#94a3b8]">1</td>
                          <td className="py-1.5 px-2">
                            <input {...moneyInputProps(editTradeVals.q, q => setEditTradeVals(v => ({ ...v, q })), tradeMarket)}
                              aria-label="입출금 금액"
                              className="w-24 bg-[#1e2d40] border border-[#334155] text-[12px] text-[#e2e8f0] rounded px-1 py-1" />
                          </td>
                        </>
                      ) : (
                        <>
                          <td className="py-1.5 px-2">
                            <input type="number" value={editTradeVals.q}
                              onChange={e => setEditTradeVals(v => ({ ...v, q: +e.target.value }))}
                              className="w-14 bg-[#1e2d40] border border-[#334155] text-[12px] text-[#e2e8f0] rounded px-1 py-1" />
                          </td>
                          <td className="py-1.5 px-2">
                            <input {...moneyInputProps(editTradeVals.price, price => setEditTradeVals(v => ({ ...v, price })), tradeMarket)}
                              className="w-18 bg-[#1e2d40] border border-[#334155] text-[12px] text-[#e2e8f0] rounded px-1 py-1" />
                          </td>
                        </>
                      )}
                      <td className="py-1.5 px-2">
                        <input value={editTradeVals.memo}
                          onChange={e => setEditTradeVals(v => ({ ...v, memo: e.target.value }))}
                          className="w-24 bg-[#1e2d40] border border-[#334155] text-[12px] text-[#e2e8f0] rounded px-1 py-1" />
                      </td>
                      <td className="py-1.5 px-2">
                        <div className="flex gap-1.5">
                          <button
                            onMouseDown={e => e.preventDefault()}
                            onClick={() => updateTradeMut.mutate({ id: t.id, ticker: t.ticker, type: t.type, vals: editTradeVals })}
                            className="text-[#10b981] hover:text-[#34d399]">
                            <Check className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onMouseDown={e => e.preventDefault()}
                            onClick={() => setEditTradeId(null)}
                            className="text-[#ef4444] hover:text-[#f87171]">
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="py-2 px-2 font-mono text-[11px] text-[#94a3b8]">{t.date}</td>
                      <td className="py-2 px-2"><TickerLabel ticker={t.ticker} name={names[t.ticker]} primaryClass="text-[13px] font-bold" stacked /></td>
                      <td className="py-2 px-2 text-[11px] font-bold"
                        style={{ color: t.type === 'ADD' || t.type === 'BUY' ? '#10b981' : '#ef4444' }}>
                        {t.type}
                      </td>
                      {/* 현금 입출금은 수량 1 · 금액=입출금액으로 보여 준다. 저장은 q=금액,
                          price=1 이라 그대로 그리면 수량 칸에 12000000, 가격 칸에 ₩1 이
                          찍혔다. **표시만** 바꾼다 — 원장 세 곳이 q 를 금액으로 읽는다
                          (backend/tests/test_cash_events_agree.py). */}
                      <td className="py-2 px-2 font-mono text-[12px] text-[#cbd5e1]">{t.ticker === 'CASH' ? 1 : t.q}</td>
                      <td className="py-2 px-2 font-mono text-[12px] text-[#cbd5e1]">
                        {t.ticker === 'CASH' ? formatMoney(t.q) : formatPrice(t.price)}
                      </td>
                      <td className="py-2 px-2 text-[11px] text-[#94a3b8] max-w-[80px] truncate">{t.memo || ''}</td>
                      <td className="py-2 px-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <div className="flex gap-1">
                          <button onClick={() => {
                            setEditTradeId(t.id)
                            setEditTradeVals({ date: t.date, q: t.q, price: t.price || 0, memo: t.memo || '' })
                          }} className="text-[#94a3b8] hover:text-[#10b981]"><Edit3 className="w-3 h-3" /></button>
                          <button
                            onClick={() => setConfirmDlg({
                              title: '이 거래를 삭제할까요?',
                              message: '삭제하면 보유 수량과 현금 잔고가 다시 계산됩니다.',
                              onOk: () => deleteTradeMut.mutate(t.id),
                            })}
                            className="text-[#94a3b8] hover:text-[#ef4444]"><Trash2 className="w-3 h-3" /></button>
                        </div>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Sectors (donut + hover ticker tooltip) ────────────────────────────────────
function SectorsPanel({
  sectorData,
  rawHoldings,
}: {
  sectorData: Record<string, number>
  rawHoldings: Record<string, any>
}) {
  const names = useTickerNames()
  const [active, setActive] = useState(0)
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 })
  const [hoveredSector, setHoveredSector] = useState<string | null>(null)

  const data = Object.entries(sectorData)
    .sort(([, a], [, b]) => b - a)
    .map(([name, v], i) => ({
      name,
      value: +(v * 100).toFixed(1),
      fill: SECTOR_COLORS[i % SECTOR_COLORS.length],
    }))

  // 섹터별 티커 목록
  const sectorTickers: Record<string, string[]> = {}
  Object.entries(rawHoldings || {}).forEach(([ticker, info]) => {
    if (ticker === 'CASH') return
    const sec = (info as any).sector || 'Other'
    if (!sectorTickers[sec]) sectorTickers[sec] = []
    sectorTickers[sec].push(ticker)
  })

  const renderActiveShape = (props: any) => {
    const { cx, cy, innerRadius, outerRadius, startAngle, endAngle, fill, payload, percent } = props
    return (
      <g>
        <Sector cx={cx} cy={cy} innerRadius={outerRadius + 6} outerRadius={outerRadius + 11}
          startAngle={startAngle} endAngle={endAngle} fill={fill} opacity={0.4} />
        <Sector cx={cx} cy={cy} innerRadius={innerRadius} outerRadius={outerRadius + 5}
          startAngle={startAngle} endAngle={endAngle} fill={fill} />
        <text x={cx} y={cy - 12} textAnchor="middle" fill="#94a3b8" fontSize={11} fontFamily="ui-monospace,monospace">
          {toKoSector(payload.name)}
        </text>
        <text x={cx} y={cy + 12} textAnchor="middle" fill={fill} fontSize={20} fontWeight="700" fontFamily="ui-monospace,monospace">
          {`${(percent * 100).toFixed(1)}%`}
        </text>
      </g>
    )
  }

  const activeSectorName = data[active]?.name ?? null
  const tooltipTickers = hoveredSector ? (sectorTickers[hoveredSector] || []) : []

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="text-[11px] text-[#cbd5e1] font-bold tracking-[3px] uppercase px-3 py-2.5 border-b border-[#1e2d40] flex-shrink-0 bg-[#070d18]">
        섹터 비중
      </div>
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* 원형 그래프 — 40% 증가: 150→210 */}
        <div
          className="flex items-center justify-center relative"
          style={{ width: '50%' }}
          onMouseMove={e => setMousePos({ x: e.clientX, y: e.clientY })}
        >
          <PieChart width={210} height={210}>
            <Pie
              activeIndex={active}
              activeShape={renderActiveShape}
              data={data}
              cx={105} cy={105}
              innerRadius={50} outerRadius={78}
              dataKey="value"
              onMouseEnter={(entry: any, i: number) => {
                setActive(i)
                setHoveredSector(entry.name)
              }}
              onMouseLeave={() => setHoveredSector(null)}
              strokeWidth={0}
              isAnimationActive={false}
            >
              {data.map((e, i) => (
                <Cell key={i} fill={e.fill} opacity={active === i ? 1 : 0.6} />
              ))}
            </Pie>
          </PieChart>

          {/* 마우스 옆 섹터 종목 툴팁 */}
          {hoveredSector && tooltipTickers.length > 0 && (
            <div
              className="fixed z-50 bg-[#0b1220] border border-[#1e2d40] rounded shadow-lg px-3 py-2 pointer-events-none"
              style={{ left: mousePos.x + 16, top: mousePos.y - 10 }}>
              <div className="text-[10px] text-[#94a3b8] font-bold tracking-wider mb-1.5 uppercase">
                {toKoSector(hoveredSector!)}
              </div>
              {/* 한국은 이름으로 (사전에 없으면 티커). 이름에는 고정폭을 쓰지 않는다. */}
              {tooltipTickers.map(t => (
                <div key={t} className={cn('text-[12px] text-[#cbd5e1]', !names[t] && 'font-mono')}>
                  {displayTicker(t, names)}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto py-2 pr-3 space-y-1.5">
          {data.map((s, i) => (
            <div key={s.name} onMouseEnter={() => { setActive(i); setHoveredSector(s.name) }}
              onMouseLeave={() => setHoveredSector(null)}
              className={cn('flex items-center gap-2 px-1.5 py-1 rounded cursor-pointer transition-all',
                active === i ? 'bg-[#0f172a]' : 'hover:bg-[#0a1020]'
              )}>
              <div className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                style={{ backgroundColor: s.fill, opacity: active === i ? 1 : 0.65 }} />
              <span className="text-[12px] truncate flex-1 transition-colors"
                style={{ color: active === i ? '#cbd5e1' : '#94a3b8' }}>
                {toKoSector(s.name)}
              </span>
              <div className="sector-legend-bar w-12 h-2 bg-[#1e2d40] rounded-full overflow-hidden flex-shrink-0">
                <div className="h-full rounded-full transition-all duration-300"
                  style={{
                    width: `${Math.min(100, (s.value / (data[0]?.value || 1)) * 100)}%`,
                    backgroundColor: s.fill,
                    opacity: active === i ? 1 : 0.5,
                  }} />
              </div>
              <span className="text-[12px] font-mono font-bold w-12 text-right flex-shrink-0 tabular-nums whitespace-nowrap"
                style={{ color: active === i ? s.fill : '#94a3b8' }}>
                {s.value}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Sector Performance Panel ──────────────────────────────────────────────────
type PerfPeriod = '1d' | '1w' | '1m' | '3m' | '6m'

const PERF_PERIODS: { key: PerfPeriod; label: string; field: string }[] = [
  { key: '1d', label: '1D', field: 'change_1d_pct' },
  { key: '1w', label: '1W', field: 'change_1w_pct' },
  { key: '1m', label: '1M', field: 'change_1m_pct' },
  { key: '3m', label: '3M', field: 'change_3m_pct' },
  { key: '6m', label: '6M', field: 'change_6m_pct' },
]

function SectorPerfPanel({ sectorTableQ }: { sectorTableQ: any }) {
  const [period, setPeriod] = useState<PerfPeriod>('1d')

  const pDef = PERF_PERIODS.find(p => p.key === period)!
  const rows: any[] = (sectorTableQ.data || [])
  // 값이 없는 기간은 0% 로 바꾸지 않는다. 0으로 채우면 '변동 없음'인 섹터와
  // '아직 계산할 수 없는' 섹터가 화면에서 구분되지 않고, 정렬에도 섞여 든다.
  const sorted = [...rows]
    .map(r => {
      const raw = r[pDef.field]
      return {
        name: SECTOR_LABEL_KO[r.sector] ?? r.sector,
        etf:  r.etf as string,
        val:  (raw == null || !Number.isFinite(raw) ? null : raw) as number | null,
      }
    })
    .sort((a, b) => {
      if (a.val == null) return 1        // 값 없는 항목은 항상 아래로
      if (b.val == null) return -1
      return b.val - a.val
    })

  const maxAbs = Math.max(...sorted.map(s => Math.abs(s.val ?? 0)), 0.01)

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-[#1e2d40] flex-shrink-0 bg-[#070d18]">
        <span className="text-[11px] text-[#cbd5e1] font-bold tracking-[3px] uppercase">섹터 변동율</span>
        <div className="tab-row flex gap-0.5">
          {PERF_PERIODS.map(p => (
            <button key={p.key} onClick={() => setPeriod(p.key)}
              className={cn(
                'px-2 py-0.5 rounded text-[10px] font-mono font-bold transition-colors',
                period === p.key
                  ? 'bg-[#1e2d40] text-[#00e6ff]'
                  : 'text-[#94a3b8] hover:text-[#cbd5e1]'
              )}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Bars */}
      <div className="flex-1 min-h-0 px-3 py-2 overflow-hidden">
        {sectorTableQ.isLoading ? (
          <div className="flex h-full items-center justify-center text-[11px] text-[#94a3b8]">로딩 중...</div>
        ) : sorted.length === 0 ? (
          <div className="flex h-full items-center justify-center text-[11px] text-[#94a3b8]">데이터 없음</div>
        ) : (
          <div className="flex flex-col h-full justify-around">
            {sorted.map(s => {
              const known = s.val != null
              const pos = (s.val ?? 0) >= 0
              const barW = known ? (Math.abs(s.val as number) / maxAbs) * 100 : 0
              return (
                <div key={s.etf} className="flex items-center gap-2">
                  <span className="text-[10px] text-[#94a3b8] w-[60px] text-right flex-shrink-0 font-medium truncate">
                    {s.name}
                  </span>
                  <div className="flex-1 h-[14px] bg-[#0a1422] rounded-sm overflow-hidden">
                    <div
                      className="h-full rounded-sm transition-all duration-300"
                      style={{
                        width: `${barW}%`,
                        backgroundColor: pos ? '#10b981' : '#ef4444',
                        opacity: 0.9,
                      }}
                    />
                  </div>
                  <span className={cn(
                    'text-[10px] font-mono font-bold w-[50px] text-right flex-shrink-0 tabular-nums whitespace-nowrap',
                    !known ? 'text-[#64748b]' : pos ? 'text-[#10b981]' : 'text-[#ef4444]'
                  )}>
                    {known ? `${pos ? '+' : ''}${(s.val as number).toFixed(1)}%` : '—'}
                  </span>
                  <span className="text-[9px] text-[#1e3a5f] w-[34px] flex-shrink-0 truncate" title={s.etf}>{sectorEtfLabel(s.etf)}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

// sessionStorage keys
const SK_CONTENT  = 'pfp_brief_content'
const SK_FILE     = 'pfp_brief_file'
const SK_LOGS     = 'pfp_brief_logs'
const SK_PENDING  = 'pfp_brief_pending'
const SK_START    = 'pfp_brief_start'

// 이 시각을 넘기면 '진행 중' 표시를 스스로 접는다.
//
// 생성은 단일 HTTP 요청이라 탭을 새로고침하거나 네트워크가 끊기면 응답이
// 영영 오지 않는다. 그때 SK_PENDING 이 남아 있으면 화면이 95%(진행률 상한)에
// 붙박이로 멈추고, 취소할 방법이 없어 세션 저장소를 비우기 전에는 복구되지
// 않는다. 실측 평균 48초라 3분이면 실패로 봐도 안전하다.
const BRIEF_TIMEOUT_MS = 180_000

// ── Daily Brief (right panel) ─────────────────────────────────────────────────
function DailyBriefPanel() {
  const { isAuthed } = useAuth()
  const [file, setFile]         = useState<string | null>(() => marketSession.get(SK_FILE))
  const [content, setContent]   = useState<string | null>(() => marketSession.get(SK_CONTENT))
  const [logs, setLogs]         = useState<string[]>(() => {
    try { return JSON.parse(marketSession.get(SK_LOGS) || '[]') } catch { return [] }
  })
  const [wasPending,  setWasPending]  = useState(() => marketSession.get(SK_PENDING) === '1')
  const [generating,  setGenerating]  = useState(false)
  const [showHist,    setShowHist]    = useState(false)
  const [pdfBusy,     setPdfBusy]     = useState(false)
  const [pdfError,    setPdfError]    = useState('')
  const [progress,    setProgress]    = useState(0)
  const [elapsedMs,   setElapsedMs]   = useState(0)
  const contentRef                    = useRef<HTMLDivElement>(null)

  // 과거 브리프는 개인 이력이다. 로그인 전에는 조회하지 않고 목록도 비운다.
  const histQ   = useQuery({
    queryKey: ['daily-brief-history'],
    queryFn:  getDailyBriefHistory,
    staleTime: 60_000,
    enabled:  isAuthed,
  })
  // 예시 리포트는 만들지 않는다 — 로그인 전에는 생성 결과가 없다.
  const shownContent = isAuthed ? content : null
  const fileMut = useMutation({
    mutationFn: getDailyBriefFile,
    onSuccess: d => {
      setContent(d.content)
      marketSession.set(SK_CONTENT, d.content)
    },
  })
  const genMut = useMutation({
    mutationFn: generateDailyBrief,
    onMutate: () => {
      setGenerating(true)
      setContent(null)
      setLogs([])
      setWasPending(false)
      setProgress(0)
      setElapsedMs(0)
      marketSession.remove(SK_CONTENT)
      marketSession.set(SK_PENDING, '1')
      marketSession.set(SK_START, String(Date.now()))
      marketSession.set(SK_LOGS, JSON.stringify([]))
    },
    onSuccess: d => {
      const newLogs = d.logs?.length ? d.logs : ['완료']
      setGenerating(false)
      setWasPending(false)
      setContent(d.report)
      setLogs(newLogs)
      setProgress(100)
      marketSession.set(SK_CONTENT, d.report)
      marketSession.set(SK_LOGS, JSON.stringify(newLogs))
      marketSession.remove(SK_PENDING)
      marketSession.remove(SK_START)
      histQ.refetch()
    },
    onError: (e: any) => {
      setGenerating(false)
      setWasPending(false)
      setProgress(0)
      setLogs(prev => {
        const next = [...prev, `오류: ${e.message}`]
        marketSession.set(SK_LOGS, JSON.stringify(next))
        return next
      })
      marketSession.remove(SK_PENDING)
      marketSession.remove(SK_START)
    },
  })

  const downloadPDF = async () => {
    if (!content) return
    setPdfBusy(true)
    try {
      const [jspdfMod, h2cMod] = await Promise.all([import('jspdf'), import('html2canvas')])
      const JsPDF       = (jspdfMod as any).jsPDF ?? (jspdfMod as any).default
      const html2canvas = (h2cMod as any).default ?? h2cMod

      const dateStr  = new Date().toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
      const titleStr = file ? file.replace(/\.md$/, '') : `전날 브리핑 · ${dateStr}`

      const wrap = document.createElement('div')
      wrap.style.cssText = 'position:fixed;top:0;left:0;width:800px;background:#fff;z-index:-9999;pointer-events:none'
      wrap.innerHTML = `
        <div style="background:#0f2044;padding:24px 40px 20px;">
          <div style="font-size:9px;letter-spacing:4px;color:#93c5fd;font-weight:700;margin-bottom:6px">ZOOPZOOP</div>
          <div style="font-size:22px;font-weight:900;color:#ffffff;line-height:1.2">${titleStr}</div>
          <div style="font-size:11px;color:#bfdbfe;margin-top:6px">${dateStr} · ZOOPZOOP Alpha Terminal</div>
        </div>
        <div id="pfp-pdf-body" style="padding:32px 40px 48px;color:#111827;font-family:'Helvetica Neue',Arial,sans-serif;font-size:13.5px;line-height:1.75;"></div>
      `
      document.body.appendChild(wrap)
      const body = wrap.querySelector('#pfp-pdf-body')!
      body.innerHTML = contentRef.current?.innerHTML ?? content.replace(/\n/g, '<br/>')

      const overrideStyle = document.createElement('style')
      overrideStyle.id = 'pfp-pdf-override'
      overrideStyle.textContent = `
        #pfp-pdf-body *          { color:#111827!important; background:transparent!important; border-color:#d1d5db!important; }
        #pfp-pdf-body h1         { font-size:20px!important; font-weight:900!important; color:#0f2044!important; border-bottom:2px solid #0f2044!important; padding-bottom:8px!important; margin:0 0 16px!important; }
        #pfp-pdf-body h2         { font-size:16px!important; font-weight:700!important; color:#1e3a5f!important; margin:20px 0 8px!important; }
        #pfp-pdf-body h3         { font-size:14px!important; font-weight:700!important; color:#374151!important; margin:14px 0 6px!important; }
        #pfp-pdf-body p          { margin:0 0 10px!important; }
        #pfp-pdf-body ul,#pfp-pdf-body ol { padding-left:20px!important; margin:0 0 10px!important; }
        #pfp-pdf-body li         { margin-bottom:3px!important; }
        #pfp-pdf-body strong     { color:#111827!important; font-weight:700!important; }
        #pfp-pdf-body code       { background:#f3f4f6!important; color:#1d4ed8!important; padding:1px 5px!important; border-radius:3px!important; font-size:12px!important; }
        #pfp-pdf-body blockquote { border-left:3px solid #1e3a5f!important; padding-left:12px!important; color:#6b7280!important; margin:8px 0!important; }
        #pfp-pdf-body hr         { border:none!important; border-top:1px solid #d1d5db!important; margin:12px 0!important; }
        #pfp-pdf-body table      { width:100%!important; border-collapse:collapse!important; font-size:12px!important; }
        #pfp-pdf-body th         { background:#f9fafb!important; color:#374151!important; border:1px solid #e5e7eb!important; padding:6px 8px!important; font-weight:600!important; }
        #pfp-pdf-body td         { color:#4b5563!important; border:1px solid #e5e7eb!important; padding:6px 8px!important; }
      `
      document.head.appendChild(overrideStyle)
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))

      const canvas = await html2canvas(wrap, { scale: 2, backgroundColor: '#ffffff', useCORS: true, logging: false, windowWidth: 800 })
      document.body.removeChild(wrap)
      document.head.removeChild(overrideStyle)

      const pdf     = new JsPDF('p', 'mm', 'a4')
      const pageW   = pdf.internal.pageSize.getWidth()
      const pageH   = pdf.internal.pageSize.getHeight()
      const pxPerMm = canvas.width / pageW
      const slicePx = pageH * pxPerMm

      let srcY = 0, page = 0
      while (srcY < canvas.height) {
        const rowH  = Math.min(slicePx, canvas.height - srcY)
        const slice = document.createElement('canvas')
        slice.width = canvas.width; slice.height = rowH
        const ctx = slice.getContext('2d')!
        ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, slice.width, slice.height)
        ctx.drawImage(canvas, 0, -srcY, canvas.width, canvas.height)
        if (page > 0) pdf.addPage()
        pdf.addImage(slice.toDataURL('image/jpeg', 0.92), 'JPEG', 0, 0, pageW, rowH / pxPerMm)
        srcY += slicePx; page++
      }
      const fname = (file || `brief_${new Date().toISOString().slice(0, 10)}`).replace(/\.md$/, '')
      pdf.save(`${fname}.pdf`)
    } catch (err) {
      console.error('[PDF]', err)
      setPdfError('PDF 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.')
    } finally {
      setPdfBusy(false)
    }
  }

  const isActivelyGenerating = generating || (wasPending && !content)

  /** 진행 표시를 접고 '생성 중' 상태를 완전히 푼다. */
  const clearPending = (note?: string) => {
    setGenerating(false)
    setWasPending(false)
    setProgress(0)
    setElapsedMs(0)
    marketSession.remove(SK_PENDING)
    marketSession.remove(SK_START)
    if (note) {
      setLogs(prev => {
        const next = [...prev, note]
        marketSession.set(SK_LOGS, JSON.stringify(next))
        return next
      })
    }
  }

  useEffect(() => {
    if (!isActivelyGenerating) return
    const startMs = parseInt(marketSession.get(SK_START) || String(Date.now()), 10)
    const MAX_MS  = 55_000   // 실측 평균 생성 시간 ~48s
    const tick = () => {
      const ms = Date.now() - startMs
      // 상한을 넘겼으면 응답이 오지 않은 것이다. 95% 에 붙박이로 두지 않는다.
      if (ms > BRIEF_TIMEOUT_MS) {
        clearPending('응답이 오지 않아 중단했습니다. 다시 시도해 주세요.')
        return
      }
      setElapsedMs(ms)
      setProgress(Math.min(95, (ms / MAX_MS) * 100))
    }
    tick()
    const id = setInterval(tick, 500)
    return () => clearInterval(id)
  }, [isActivelyGenerating])

  // 경과 시간 기반 단계 표시 (백엔드 로그는 완료 시에만 도착하므로 프론트에서 시뮬레이션)
  const STAGE_LABELS = [
    '1/3  가격 데이터 수집 중...',
    '2/3  뉴스 헤드라인 수집 중...',
    '3/3  AI 브리프 생성 중 (약 30~60초)...',
  ]
  const displayLogs = isActivelyGenerating
    ? STAGE_LABELS.slice(0, elapsedMs < 10_000 ? 1 : elapsedMs < 30_000 ? 2 : 3)
    : logs

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <ConfirmDialog
        open={!!pdfError}
        alert
        tone="danger"
        title="PDF를 만들지 못했습니다"
        message={pdfError}
        confirmText="확인"
        onConfirm={() => setPdfError('')}
        onCancel={() => setPdfError('')}
      />
      <div className="flex-shrink-0 flex gap-2 p-3 border-b border-[#1e2d40]">
        <button onClick={() => genMut.mutate()} disabled={isActivelyGenerating}
          className="flex-1 flex items-center justify-center gap-2 py-2 bg-[#10b981]/12 border border-[#10b981]/30 text-[#10b981] text-[11px] font-bold rounded hover:bg-[#10b981]/20 disabled:opacity-50 transition-colors">
          <Play className="w-3.5 h-3.5" />
          {isActivelyGenerating ? '생성 중…' : '브리핑 생성'}
        </button>
        {/* 생성 중에는 언제든 접을 수 있어야 한다. 진행 표시가 응답을 기다리는
            동안 화면이 잠기면, 요청이 유실됐을 때 사용자가 할 수 있는 일이 없다. */}
        {isActivelyGenerating && (
          <button onClick={() => clearPending('사용자가 중단했습니다.')}
            title="진행 표시 중단"
            className="px-3 py-2 rounded border border-[#ef4444]/40 bg-[#ef4444]/10 text-[#ef4444] text-[11px] font-bold hover:bg-[#ef4444]/20 transition-colors">
            중단
          </button>
        )}
        <button onClick={downloadPDF} disabled={!shownContent || pdfBusy} title="PDF로 다운로드"
          className={cn('px-3 py-2 rounded border text-[11px] font-bold transition-colors flex items-center gap-1.5',
            shownContent && !pdfBusy
              ? 'border-[#10b981]/50 bg-[#10b981]/10 text-[#10b981] hover:bg-[#10b981]/20'
              : 'border-[#1e2d40] text-[#94a3b8] cursor-not-allowed opacity-40'
          )}>
          {pdfBusy
            ? <span className="w-3.5 h-3.5 border-2 border-[#10b981] border-t-transparent rounded-full animate-spin" />
            : <Download className="w-3.5 h-3.5" />}
          <span>PDF</span>
        </button>
        <button onClick={() => setShowHist(h => !h)}
          className={cn('px-3 py-2 rounded border transition-colors',
            showHist ? 'bg-[#1e2d40] border-[#64748b]/50 text-[#cbd5e1]' : 'border-[#1e2d40] text-[#94a3b8] hover:text-[#cbd5e1]'
          )}>
          <FileText className="w-4 h-4" />
        </button>
      </div>

      {showHist && (
        <div className="flex-shrink-0 max-h-40 overflow-y-auto border-b border-[#1e2d40] bg-[#060b14]">
          {(histQ.data || []).map(f => (
            <button key={f.name}
              onClick={() => {
                setFile(f.name); marketSession.set(SK_FILE, f.name)
                fileMut.mutate(f.name); setShowHist(false)
                setWasPending(false); marketSession.remove(SK_PENDING)
              }}
              className={cn('w-full text-left px-3 py-2 text-[12px] border-b border-[#0f172a] font-mono truncate transition-colors',
                file === f.name ? 'text-[#10b981] bg-[#0f172a]' : 'text-[#94a3b8] hover:text-[#cbd5e1] hover:bg-[#0a1020]'
              )}>
              {f.name}
            </button>
          ))}
        </div>
      )}

      {isActivelyGenerating && (
        <div className="flex-shrink-0 border-b border-[#1e2d40] px-3 py-2.5 space-y-1.5 bg-[#060b14]">
          {displayLogs.map((l, i) => (
            <div key={i} className="text-[12px] text-[#10b981] font-mono flex items-center gap-2">
              <ChevronRight className="w-3.5 h-3.5 flex-shrink-0" />{l}
            </div>
          ))}
          <div className="flex items-center gap-2 pt-0.5">
            <span className="w-2 h-2 rounded-full bg-[#10b981] animate-pulse flex-shrink-0" />
            <span className="text-[11px] text-[#94a3b8]">
              {wasPending && !generating ? '백그라운드 생성 중… (잠시 후 자동 완료)' : 'AI 분석 중… (~1-2분)'}
            </span>
          </div>
          {/* 진행률 막대 */}
          <div className="pt-1 space-y-1">
            <div className="h-1.5 bg-[#1e2d40] rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-[#10b981] to-[#34d399] rounded-full transition-all duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
            <div className="flex justify-between">
              <span className="text-[10px] text-[#94a3b8]">분석 진행 중</span>
              <span className="text-[10px] text-[#10b981] font-mono tabular-nums">{Math.round(progress)}%</span>
            </div>
          </div>
          {/* 금융 용어 캐러셀 */}
          <FinancialTips />
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {!shownContent && !isActivelyGenerating && (
          <div className="flex flex-col items-center justify-center h-full gap-3 p-5 text-center">
            <FileText className="w-12 h-12 text-[#10b981]/20" />
            <p className="text-sm text-[#94a3b8] leading-relaxed">브리핑 생성 버튼을 눌러<br/>AI 데일리 브리핑을 생성하세요</p>
          </div>
        )}
        {shownContent && !isActivelyGenerating && (
          <div ref={contentRef} className="p-4 brief-md">
            <ReactMarkdown>{shownContent}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
const BOT_TABS   = ['실적/배당', '시장지표']
const RIGHT_TABS = ['전날 브리핑', 'AI 피드백', '뉴스']

export default function AlphaTerminal() {
  const names = useTickerNames()
  const market = useMarket()
  const qc = useQueryClient()
  const [botTab,       setBotTab]       = useState(0)
  const [rightTab,     setRightTab]     = useState(0)
  // 모바일 전용 — 브리핑·피드백·뉴스 패널을 기본은 접어 두고(배너만), 배너를
  // 누르면 전체화면으로 연다. 데스크탑은 이 값과 무관하게 항상 인라인으로 보인다.
  const [rightPanelOpen, setRightPanelOpen] = useState(false)

  // 배너의 세로 위치(뷰포트 높이 대비 %) — 사용자가 위아래로 드래그해 옮길 수
  // 있다. 탭(누르기)과 드래그(옮기기)를 구분해야 해서 onClick 대신 포인터
  // down/move/up 을 직접 다룬다: 눌렀다 뗄 때까지 거의 안 움직였으면 탭(=열기),
  // 일정량 이상 움직였으면 드래그(=위치만 바꾸고 열지 않음)로 본다.
  const [bannerTop, setBannerTop] = useState<number>(() => {
    try {
      const s = Number(localStorage.getItem('pfp_brief_banner_top'))
      return Number.isFinite(s) && s > 5 && s < 95 ? s : 42
    } catch { return 42 }
  })
  const bannerTopRef = useRef(bannerTop)
  useEffect(() => { bannerTopRef.current = bannerTop }, [bannerTop])
  const bannerDragRef = useRef<{ startY: number; startTop: number; moved: boolean } | null>(null)

  const handleBannerPointerDown = (e: React.PointerEvent) => {
    bannerDragRef.current = { startY: e.clientY, startTop: bannerTopRef.current, moved: false }
  }
  useEffect(() => {
    function onMove(e: PointerEvent) {
      const d = bannerDragRef.current
      if (!d) return
      const dy = e.clientY - d.startY
      if (Math.abs(dy) > 6) d.moved = true
      if (!d.moved) return
      const vh = window.innerHeight || 800
      const nextTop = Math.min(92, Math.max(8, d.startTop + (dy / vh) * 100))
      setBannerTop(nextTop)
    }
    function onUp() {
      const d = bannerDragRef.current
      if (!d) return
      if (d.moved) {
        try { localStorage.setItem('pfp_brief_banner_top', String(bannerTopRef.current)) } catch {}
      } else {
        setRightPanelOpen(true)   // 거의 안 움직였다 = 탭 = 열기
      }
      bannerDragRef.current = null
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])

  // ── 우측 패널(브리핑·피드백·뉴스) 폭 — 드래그로 조절, 값은 기억해 둔다.
  // 인라인 style 로 CSS 변수만 세팅하고 실제 width 는 Tailwind 클래스
  // (md:w-[var(--right-panel-w)])가 md 이상에서만 적용하므로, 모바일에서는
  // 이 값과 무관하게 항상 w-full 이다 — JS 로 화면폭을 따로 판별할 필요가 없다.
  const [rightPanelW, setRightPanelW] = useState<number>(() => {
    try {
      const s = Number(localStorage.getItem('pfp_right_panel_w'))
      return Number.isFinite(s) && s > 0 ? Math.min(720, Math.max(260, s)) : 340
    } catch { return 340 }
  })
  const rightPanelWRef = useRef(rightPanelW)
  useEffect(() => { rightPanelWRef.current = rightPanelW }, [rightPanelW])
  const resizingRef = useRef(false)
  const resizeStartRef = useRef({ x: 0, w: 0 })

  const handlePanelResizeStart = (e: React.PointerEvent) => {
    resizingRef.current = true
    resizeStartRef.current = { x: e.clientX, w: rightPanelW }
  }
  useEffect(() => {
    function onMove(e: PointerEvent) {
      if (!resizingRef.current) return
      // 오른쪽 패널 기준 — 마우스가 왼쪽으로 갈수록(dx<0) 패널이 넓어진다
      const dx = e.clientX - resizeStartRef.current.x
      const next = Math.min(720, Math.max(260, resizeStartRef.current.w - dx))
      setRightPanelW(next)
    }
    function onUp() {
      if (!resizingRef.current) return
      resizingRef.current = false
      try { localStorage.setItem('pfp_right_panel_w', String(rightPanelWRef.current)) } catch {}
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])
  const [tickerModal,  setTickerModal]  = useState<string | null>(null)
  const [searchQuery,  setSearchQuery]  = useState('')
  const [searchSugs,   setSearchSugs]   = useState<Suggestion[]>([])
  const [showTopSugs,  setShowTopSugs]  = useState(false)
  const [searchIdx,    setSearchIdx]    = useState(-1)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)

  const openTickerModal = (ticker: string) => {
    setTickerModal(ticker); setSearchQuery(''); setSearchSugs([]); setShowTopSugs(false); setSearchIdx(-1)
  }
  /** Enter 와 검색 버튼이 같이 쓴다. 목록이 보이면 목록에서 고른다 —
   *  예전에는 "삼성" 을 그대로 티커로 열어 모달이 "데이터 없음" 을 띄웠다. */
  const submitSearch = () => {
    const listOpen = showTopSugs && searchSugs.length > 0
    const pick = listOpen ? pickOnEnter(searchQuery, searchSugs, searchIdx) : null
    if (pick) { openTickerModal(pick.ticker); return }
    const sym = searchQuery.trim().toUpperCase()
    if (sym) openTickerModal(sym)
  }

  // 핵심 지표: 60초 주기 (30초는 너무 자주 백엔드 호출)
  // 개인 데이터 쿼리 — 비로그인이면 서버를 부르지 않고(어차피 401) 예시 데이터를
  // 돌려준다. 화면은 정상적으로 그려지고 LockedPreview 가 그 위에 흐림을 씌운다.
  const metricsQ  = useDemoQuery(['portfolio-metrics'], getPortfolioMetrics, demoMetrics(),        { refetchInterval: 60_000, staleTime: 55_000 })
  // 에쿼티 커브/섹터: 5분 캐시 (자주 변하지 않음)
  const curveQ    = useDemoQuery(['equity-curve'],      getEquityCurve,      DEMO_EQUITY_CURVE,  { staleTime: 300_000 })
  // 보유 종목 상세: 60초 (현재가 업데이트용)
  const holdQ     = useDemoQuery(['holdings-detail'],   getHoldingsDetail,   demoHoldingsDetail(), { refetchInterval: 60_000, staleTime: 55_000 })
  const rawHoldQ  = useDemoQuery(['holdings-raw'],      getHoldings,         demoHoldingsRaw(),  { staleTime: 300_000, placeholderData: (prev: any) => prev })
  const sectorQ   = useDemoQuery(['sector-weights'],    getSectorWeights,    demoSectorWeights(), { staleTime: 300_000 })
  const sectorTableQ = useQuery({ queryKey: ['sector-table'],    queryFn: getMarketSectors,   staleTime: 300_000, refetchInterval: 300_000 })
  // 시장 스냅샷: 60초 주기 (마커 바 업데이트)
  const snapQ     = useQuery({ queryKey: ['market-snapshot'],   queryFn: getMarketSnapshot,      refetchInterval: 60_000,  staleTime: 55_000 })
  const macroQ    = useQuery({ queryKey: ['macro-data'],        queryFn: getMacroData,           staleTime: 600_000 })
  // analyst-feedback(LLM): AI Feed 탭 활성 시에만 요청 (느린 LLM 호출 — 페이지 로드에서 제외)
  const feedbackQ = useDemoQuery(
    ['analyst-feedback'],
    () => getAnalystFeedback(m ? {
      vix: m.vix,
      portfolio_beta: m.portfolio_beta,
      today_chg_pct: m.today_change_pct,
    } : undefined),
    DEMO_ANALYST_FEEDBACK,
    { staleTime: 300_000, enabled: rightTab === 1 },
  )

  const holdTickers = Object.keys(rawHoldQ.data || {}).filter(t => t !== 'CASH').join(',')
  // 뉴스: News 탭 활성 시에만 요청
  const newsQ = useDemoQuery(
    ['market-news', holdTickers],
    () => getMarketNews(holdTickers.split(',').filter(Boolean)),
    DEMO_NEWS,
    { enabled: !!holdTickers && rightTab === 2, staleTime: 300_000 },
  )
  // 실적/배당: Earnings 탭 활성 시에만 요청 (병렬화했지만 여전히 yfinance N개 호출)
  const earningsQ = useDemoQuery(
    ['earnings', holdTickers],
    () => getEarnings(holdTickers.split(',').filter(Boolean)),
    DEMO_EARNINGS,
    { enabled: !!holdTickers && botTab === 0, staleTime: 3600_000 },
  )

  const m = metricsQ.data

  // 모바일에서는 화면 높이에 가두지 않는다. h-full + overflow-hidden 이면
  // 내용이 잘려 나가고, 잘린 만큼은 스크롤할 대상 자체가 사라져 페이지가
  // 아예 안 내려간다 — 실제로 그렇게 막혀 있었다.
  // 데스크탑은 기존대로 화면에 맞추고 패널별로 스크롤한다.
  return (
    <div className="flex flex-col md:h-full bg-[#0b0f1a] md:overflow-hidden">

      {/* ── Ticker Detail Modal ── */}
      {tickerModal && (
        <TickerDetailModal
          initialTicker={tickerModal}
          onClose={() => setTickerModal(null)}
        />
      )}

      {/* ── Metrics bar ── */}
      {/* 모바일: 지표 줄과 시계를 세로로 쌓되 각자 제 높이만 쓴다.
          items-stretch 를 그대로 두면 한 줄이 통째로 늘어나 헤더만 193px 을 먹었다. */}
      <div data-tour="metrics" className="flex-shrink-0 bg-[#060b14] border-b border-[#1e2d40] flex flex-col md:flex-row md:items-stretch">
        <div className="flex flex-1 min-w-0 overflow-x-auto">
          {/* 실패를 '0원 포트폴리오'로 위장하지 않는다 — 조회 실패와 빈 포트폴리오는 다르다.
              빈 포트폴리오는 **응답**에서 읽는다(`is_empty`). 예전에는 400 을 보고
              판단했는데, 그건 서버가 정상 상태를 오류라고 부르는 동안만 맞았다. */}
          {!metricsQ.isError && metricsQ.data?.is_empty && <EmptyHoldings compact />}
          {metricsQ.isError && (
            <div className="flex items-center gap-2 px-4 py-2 text-xs text-[#ef4444]">
              <span>지표를 불러오지 못했습니다</span>
              <button onClick={() => metricsQ.refetch()}
                className="px-2 py-0.5 rounded border border-[#ef4444]/40 hover:bg-[#ef4444]/10">
                재시도
              </button>
            </div>
          )}
          {!metricsQ.isError && metricsQ.isLoading && (
            <div className="px-4 py-2 text-xs text-[#64748b]">지표 불러오는 중...</div>
          )}
          {/* `!m.is_empty` 가 필요하다. 빈 포트폴리오의 total_equity 는 **0 이고
              그건 참이라서** `typeof === 'number'` 를 그대로 통과한다 — 빼면
              EmptyHoldings 와 0원 지표 바가 함께 뜬다. */}
          {!metricsQ.isError && m && !m.is_empty && typeof m.total_equity === 'number' && (
            <LockedPreview silent>
            <div className="flex items-stretch">
              {/* 총 자산은 축약하지 않는다. formatCompact 는 ₩1,235만 / $1.23M 처럼
                  끊어 원·센트 단위가 사라지는데, 내 자산 총액은 반올림된 값이
                  아니라 실제 금액으로 보여야 한다. */}
              <Pill label="총 자산" value={formatPrice(m.total_equity)} />
              <Pill
                label={m.market_open ? '1Day · LIVE' : m.as_of ? `1Day (${m.as_of.slice(5).replace('-', '/')})` : '1Day'}
                value={fp(m.today_change_pct)}
                color={chgColor(m.today_change_pct)} />
              <Pill label="1Week"        value={fp(m.perf_1w)}   color={chgColor(m.perf_1w)} />
              <Pill label="1Month"       value={fp(m.perf_1m)}   color={chgColor(m.perf_1m)} />
              <Pill label="누적 수익"  value={fp(m.total_return_pct)}  color={chgColor(m.total_return_pct)}
                    title="보유 중인 주식의 평가손익. 매도로 확정한 손익은 옆의 '실현 손익' 에 있다." />
              {/* 값은 **금액**이다. 옆 칸들이 전부 포트폴리오 기준 비율인데
                  realized_pnl_pct 의 분모는 '매도된 주식의 취득원가' 라, 나란히
                  놓으면 읽는 사람이 같은 기준으로 비교한다. 금액에는 그 혼동이
                  없다. 비율은 툴팁에서 분모와 함께 밝힌다.

                  0 과 '—' 를 색으로도 가른다: chgColor 가 0 을 회색으로 주므로
                  "안 팔았다" 가 초록으로 칠해지지 않는다. */}
              <Pill label="실현 손익"
                    value={fmtRealized(m.realized_pnl)}
                    color={chgColor(m.realized_pnl)}
                    title={realizedTitle(m)} />
              {/* 라벨은 '베타' 만 쓴다 — 괄호 출처는 사용자 요청으로 뺐다. 서버가
                  시장별 벤치마크로 계산한다는 사실(^GSPC / ^KS11)은 사라지면 안
                  되므로 마우스를 올렸을 때 제목으로 밝힌다. */}
              <Pill label="베타"
                    title={m.benchmark_label ? `${m.benchmark_label} 대비 베타` : undefined}
                    value={fn(m.portfolio_beta)} />
              {/* 값(`m.vix`)은 **그 시장의** 내재 변동성 지수다 — 미국 VIX, 한국
                  VKOSPI (서버 ba60ceb · markets.MarketSpec.volatility_index). 예전
                  한국 화면은 야후에 VKOSPI 가 없어 미국 VIX 를 쓰고 라벨에 "(미국
                  VIX)" 로 출처를 밝혔는데, 지금 그 라벨이면 VKOSPI 값이 미국 VIX
                  라는 이름을 단다. 필드 이름은 서버 호환으로 `vix` 그대로다. 라벨의
                  괄호는 사용자 요청으로 뺐고, 어느 지수인지는 제목(마우스를 올리면
                  보인다)에 남긴다.
                  색은 구간 기준이라 chgColor(증감 기준)를 못 쓴다. 대신 null 을
                  먼저 갈라낸다 — fv 로 0 을 만들면 `0 >= 30`·`0 >= 20` 이 둘 다
                  거짓이라 **읽지 못한 상태가 초록(정상)** 으로 칠해진다.
                  구간은 30 이상 빨강(위험) · 20 이상 노랑(주의)이고 두 시장에 같다.
                  AI 피드백 프롬프트의 등급(ai_analysis._VOL_BANDS)과 같은 경계다 —
                  예전 18/25 로는 같은 화면 오른쪽 AI 피드백이 '정상' 이라 쓰는 19 가
                  노랑, '주의' 라 쓰는 27 이 빨강이었다. 그 파일에 두 지수의 분포가
                  적혀 있다(2013-08 이후 20 은 VIX 73.5 · VKOSPI 72.6 백분위, 30 은
                  95.2 · 91.0). 등급은 **적힌 숫자**(소수 둘째 자리)로 가른다 —
                  서버가 이미 둘째 자리로 반올림해 보내 지금은 원값과 같지만, 자리가
                  바뀌어도 칸의 숫자와 색이 어긋나지 않게. AI 프롬프트는 첫째 자리로
                  적고 가르므로, 첫째 자리로 적으면 20.0·30.0 이 되는 값에서는 둘의
                  등급이 한 칸 다르다 (실측: 19.99 칸 초록·AI 주의, 29.99 칸 노랑·AI
                  위험. 19.95 는 파이썬이 19.9 로 적어 둘 다 정상). */}
              <Pill label="변동성" value={fn(m.vix)}
                    title={market === 'KR' ? 'VKOSPI — 코스피200 변동성 지수' : 'VIX — S&P 500 변동성 지수'}
                    color={volColor(m.vix)} />
            </div>
            </LockedPreview>
          )}
        </div>
        <Clock />
      </div>

      {/* ── Marquee ── */}
      <Marquee snapshot={snapQ.data} />

      {/* ── 종목 검색 바 (Marquee 아래) ── */}
      <div data-tour="search" className="flex-shrink-0 bg-[#060b14] border-b border-[#1e2d40] px-4 py-2" style={{ position: 'relative' }}>
        <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
          <input
            ref={searchInputRef}
            value={searchQuery}
            onChange={e => {
              setSearchQuery(e.target.value)
              setShowTopSugs(true)
              setSearchIdx(-1)
              if (searchTimer.current) clearTimeout(searchTimer.current)
              if (!e.target.value.trim()) { setSearchSugs([]); return }
              searchTimer.current = setTimeout(async () => {
                try { setSearchSugs(await searchTickers(e.target.value)) } catch { setSearchSugs([]) }
              }, 250)
            }}
            onKeyDown={e => {
              // 한글 조합 중의 키는 IME 몫이다 — 받으면 확정용 Enter 가 검색으로도 처리될 수 있다.
              if (e.nativeEvent.isComposing) return
              const listOpen = showTopSugs && searchSugs.length > 0
              if (listOpen && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
                e.preventDefault()
                setSearchIdx(i => moveHighlight(i, e.key === 'ArrowDown' ? 1 : -1, searchSugs.length))
              } else if (listOpen && e.key === 'Escape') {
                setShowTopSugs(false); setSearchIdx(-1)
              } else if (e.key === 'Enter') {
                e.preventDefault()
                submitSearch()
              }
            }}
            onFocus={() => setShowTopSugs(true)}
            onBlur={() => setTimeout(() => { setShowTopSugs(false); setSearchIdx(-1) }, 150)}
            placeholder="종목 검색 (티커·이름)…"
            autoComplete="off"
            className="bg-[#0b1220] border border-[#1e2d40] text-[#e2e8f0] rounded-l text-[12px] focus:outline-none focus:border-[#10b981]"
            style={{ padding: '5px 10px', width: 280 }}
          />
          <button
            // 누르는 순간 입력칸 포커스를 뺏지 않는다 — 뺏으면 blur 가 목록을 닫는
            // 타이머와 클릭이 경쟁해, 같은 입력이 Enter 와 다르게 처리될 수 있다.
            onMouseDown={e => e.preventDefault()}
            onClick={submitSearch}
            className="bg-[#10b981] hover:bg-[#059669] border border-[#10b981] rounded-r flex items-center gap-1.5 transition-colors"
            style={{ padding: '5px 12px' }}>
            <Search size={13} color="#fff" />
            <span className="text-white text-[11px] font-bold">검색</span>
          </button>
          <SuggestionList
            anchorRef={searchInputRef}
            open={showTopSugs}
            items={searchSugs}
            highlighted={searchIdx}
            onPick={s => openTickerModal(s.ticker)}
            minWidth={300}
          />
        </div>
      </div>

      {/* ── Main layout ── */}
      {/* relative: 로그인 안내를 이 영역 한가운데에 띄우기 위한 기준점 */}
      {/* 모바일에서는 좌우로 나눌 폭이 없다. 세로로 쌓고 바깥(main)에서 스크롤한다 —
          좁은 화면에서 스크롤 영역을 안쪽에 또 만들면 어디를 밀어야 할지 헷갈린다. */}
      <div className="relative flex flex-col md:flex-row md:flex-1 md:min-h-0">
        <AuthOverlay />

        {/* ═══ LEFT PANEL ═══ */}
        <div className="mobile-flatten md:overflow-y-auto border-b md:border-b-0 md:border-r border-[#1e2d40] flex-1 min-w-0">

          {/* A: Equity Curve — 개인 자산 추이 (모바일 1번째) */}
          <div data-tour="equity" className="m-order-1">
          <LockedPreview silent>
            <EquityCurve curveQ={curveQ} />
          </LockedPreview>
          </div>

          {/* B: Holdings (모바일 2번째) */}
          <div data-tour="holdings" className="m-order-2 border-b border-[#1e2d40] md:h-[460px]">
            <LockedPreview silent>
              <HoldingsPanel holdQ={holdQ} rawHoldings={rawHoldQ.data || {}} onTickerClick={t => setTickerModal(t)} />
            </LockedPreview>
          </div>

          {/* B2: 섹터 비중 + 섹터 변동율 (모바일 4·5번째) */}
          <div className="m-order-4 border-b border-[#1e2d40] flex flex-col md:flex-row md:h-[300px]">
            <div className="border-b md:border-b-0 md:border-r border-[#1e2d40] w-full md:w-1/2 h-[260px] md:h-auto">
              <LockedPreview silent>
                <SectorsPanel
                  sectorData={sectorQ.data || {}}
                  rawHoldings={rawHoldQ.data || {}}
                />
              </LockedPreview>
            </div>
            <div className="w-full md:w-1/2 h-[300px] md:h-auto">
              {/* 공개 데이터지만, 옆 칸(보유 비중)과 한 행이라 함께 흐리게 처리한다 */}
              <LockedPreview silent>
                <SectorPerfPanel sectorTableQ={sectorTableQ} />
              </LockedPreview>
            </div>
          </div>

          {/* C: 실적/배당·시장지표 (모바일 6번째) */}
          <div className="m-order-6" style={{ minHeight: '300px' }}>
            <div className="tab-row flex bg-[#060b14] border-b border-[#1e2d40] sticky top-0 z-10">
              {BOT_TABS.map((t, i) => (
                <button key={t} onClick={() => setBotTab(i)}
                  className={cn('px-5 py-2.5 text-[11px] font-bold tracking-widest transition-colors uppercase',
                    botTab === i ? 'text-[#10b981] border-b-2 border-[#10b981]' : 'text-[#94a3b8] hover:text-[#cbd5e1]'
                  )}>
                  {t}
                </button>
              ))}
            </div>
            <div className="p-4">
              {botTab === 0 && !holdTickers && (
                <div className="py-6"><EmptyHoldings label="보유 종목 없음" /></div>
              )}
              {botTab === 0 && !!holdTickers && earningsQ.data && (
                <LockedPreview silent>
                <table className="w-full">
                  <thead>
                    <tr className="text-[#94a3b8] border-b border-[#1e2d40]">
                      {['Ticker', '실적발표일', '배당락일', '배당수익률'].map(h => (
                        <th key={h} className="text-left py-2.5 px-3 font-semibold text-[12px]">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {earningsQ.data.map(e => (
                      <tr key={e.ticker} className="border-b border-[#0f172a] hover:bg-[#0a1020]">
                        <td className="py-2.5 px-3"><TickerLabel ticker={e.ticker} name={names[e.ticker]} primaryClass="text-base font-bold" /></td>
                        <td className="py-2.5 px-3 text-sm text-[#cbd5e1]">{e.earn_date}</td>
                        <td className="py-2.5 px-3 text-sm text-[#cbd5e1]">{e.div_date}</td>
                        {/* null 이면 React 가 아무것도 안 그려 칸이 빈다 —
                            '배당 없음'인지 '조회 실패'인지 구분되지 않는다.
                            그리고 초록은 값이 있을 때만 의미가 있다. */}
                        <td className="py-2.5 px-3 text-sm font-mono font-bold"
                            style={{ color: e.div_yield == null ? '#64748b' : '#10b981' }}>
                          {e.div_yield ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </LockedPreview>
              )}
              {botTab === 1 && macroQ.data && (
                <div>
                {/* '—' 만 뜨면 조회에 실패한 것인지 원래 없는 값인지 구분되지
                    않는다. 서버가 어느 시리즈를 못 읽었는지 알려주므로 그대로 밝힌다. */}
                {macroQ.data.missing && macroQ.data.missing.length > 0 && (
                  <div className="mb-2 text-[11px] text-[#f59e0b]">
                    일부 지표를 FRED 에서 읽지 못했습니다 ({macroQ.data.missing.join(', ')}).
                    해당 항목은 '—' 로 표시됩니다.
                  </div>
                )}
                <div className="grid grid-cols-3 gap-3">
                  {[
                    // 템플릿 리터럴에 그대로 보간하면 null 이 "null%" 로 찍힌다.
                    // 아래 두 항목은 fp/fn 을 거쳐 이미 '—' 를 내는데 위 넷만
                    // 위장하고 있었다 — 같은 배열 안에서 관례가 갈렸다.
                    { label: '기준금리',       value: `${fp(macroQ.data.fed_rate, 2, false)}` },
                    { label: '실업률',         value: `${fp(macroQ.data.unemployment, 2, false)}` },
                    { label: 'CPI (전년비)',   value: `${fp(macroQ.data.cpi, 2, false)}` },
                    { label: 'GDP 성장률',     value: `${fp(macroQ.data.gdp, 2, false)}` },
                    // 색도 값과 같은 판단을 해야 한다. fv 는 null 을 0 으로 만드므로
                    // 스프레드를 못 읽었을 때 '역전 아님'(초록)을 단정하게 된다.
                    { label: '10Y-2Y 스프레드', value: `${fp(macroQ.data.t10y2y)}p`,
                      color: macroQ.data.t10y2y == null ? '#64748b'
                             : macroQ.data.t10y2y < 0 ? '#ef4444' : '#10b981' },
                    { label: 'HY 스프레드',    value: `${fn(macroQ.data.bamlh0a0hym2, 0)} bps`,
                      color: macroQ.data.bamlh0a0hym2 == null ? '#64748b'
                             : macroQ.data.bamlh0a0hym2 > 500 ? '#ef4444' : '#f59e0b' },
                  ].map(item => (
                    <div key={item.label} className="bg-[#060b14] border border-[#1e2d40] rounded p-3">
                      <div className="text-[11px] text-[#94a3b8] font-bold tracking-wider uppercase mb-1">{item.label}</div>
                      <div
                        className={`text-2xl font-mono font-bold${(item as any).color ? '' : ' text-[#e2e8f0]'}`}
                        style={(item as any).color ? { color: (item as any).color } : undefined}
                      >{item.value}</div>
                    </div>
                  ))}
                </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* 리사이즈 핸들 — 데스크탑에서만 (모바일은 세로로 쌓이므로 폭 조절이 의미 없다) */}
        <div
          onPointerDown={handlePanelResizeStart}
          title="드래그해서 패널 폭 조절"
          className="hidden md:block w-1.5 flex-shrink-0 cursor-col-resize hover:bg-[#10b981]/30 active:bg-[#10b981]/50 transition-colors"
        />

        {/* 모바일 전용 배너 — 우측 가장자리에 세로 탭처럼 붙여 두고, 누르면 패널이
            오른쪽에서 왼쪽으로 슬라이드해 들어온다(닫을 땐 반대로 왼쪽→오른쪽).
            화살표는 "누르면 이 방향으로 열린다"는 뜻 — 세로 위치는 드래그로 옮길 수 있다.
            데스크탑에서는 패널이 항상 인라인으로 보이므로 필요 없다. */}
        <button
          onPointerDown={handleBannerPointerDown}
          style={{ top: `${bannerTop}%`, touchAction: 'none' }}
          className={cn(
            'md:hidden fixed right-0 z-30 -translate-y-1/2 flex flex-col items-center gap-1 rounded-l-lg',
            'border border-r-0 border-[#2d3f56] bg-[#1a2035] px-1.5 py-2.5 text-[10px] font-bold',
            'tracking-widest text-[#94a3b8] shadow-lg select-none transition-opacity',
            rightPanelOpen ? 'opacity-0 pointer-events-none' : 'opacity-100',
          )}
        >
          <ChevronLeft className="w-3.5 h-3.5 flex-shrink-0" />
          {/* 세로쓰기에서도 줄바꿈은 일어난다 — 높이가 모자라면 두 번째 '열'로
              접혀 글자가 두 줄처럼 보인다. nowrap 으로 한 줄을 보장한다. */}
          <span style={{ writingMode: 'vertical-rl', whiteSpace: 'nowrap' }}>브리핑</span>
        </button>

        {/* ═══ RIGHT PANEL — 데스크탑엔 항상 보임, 모바일에선 배너를 눌러야 오른쪽에서
            왼쪽으로 슬라이드해 열리고, 닫기를 누르면 왼쪽에서 오른쪽으로 슬라이드해 닫힌다.
            display:none 으로는 트랜지션이 안 걸려서, 항상 마운트해 두고 translate-x 로만
            보이고/안 보이고를 조절한다(닫혀 있을 땐 pointer-events 도 꺼서 안 눌리게 한다). */}
        <div
          data-tour="brief"
          style={{ ['--right-panel-w' as any]: `${rightPanelW}px` }}
          className={cn(
            'm-order-3 flex-shrink-0 min-h-0 flex-col fixed inset-0 z-40 flex bg-[#0b0f1a]',
            'transition-transform duration-300 ease-out',
            rightPanelOpen ? 'translate-x-0' : 'translate-x-full pointer-events-none',
            'md:flex md:flex-row md:static md:inset-auto md:z-auto md:bg-transparent md:translate-x-0 md:pointer-events-auto md:transition-none',
            'w-full md:w-[var(--right-panel-w)] md:min-w-[260px] min-h-[420px] md:min-h-0',
          )}>
          <div className="flex flex-col min-h-0 flex-1 overflow-hidden">
            {/* 모바일 전용 닫기 헤더 — 데스크탑엔 없음 */}
            {/* 닫기는 왼쪽에 둔 '오른쪽 화살표'다. 배너를 왼쪽으로 당겨 열었으니
                오른쪽으로 밀어 닫는 것이 손의 방향과 맞는다. X 는 어느 쪽으로
                사라지는지 알려주지 않는다. */}
            <div className="md:hidden flex-shrink-0 flex items-center gap-2 px-3 py-3 border-b border-[#1e2d40] bg-[#060b14]">
              <button
                onClick={() => setRightPanelOpen(false)}
                aria-label="브리핑 패널 닫기"
                className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-[#94a3b8] transition hover:bg-[#0d1526] hover:text-[#e2e8f0]"
              >
                <ChevronRight className="w-5 h-5" />
              </button>
              <span className="text-sm font-bold text-[#e2e8f0]">브리핑 · 피드백 · 뉴스</span>
            </div>
            <div className="tab-row flex-shrink-0 bg-[#060b14] border-b border-[#1e2d40] flex">
              {RIGHT_TABS.map((t, i) => (
                <button key={t} onClick={() => setRightTab(i)}
                  className={cn('flex-1 py-2.5 text-[10px] font-bold tracking-widest uppercase transition-colors',
                    rightTab === i ? 'text-[#10b981] border-b-2 border-[#10b981] bg-[#10b981]/5' : 'text-[#94a3b8] hover:text-[#cbd5e1]'
                  )}>
                  {t}
                </button>
              ))}
            </div>

            <div className="flex-1 min-h-0 overflow-hidden">
            {rightTab === 0 && (
              <LockedPreview silent>
                <DailyBriefPanel />
              </LockedPreview>
            )}

            {rightTab === 1 && (
              <LockedPreview silent>
              <div className="h-full flex flex-col overflow-hidden">
                <div className="flex-shrink-0 flex items-center justify-between px-4 py-2.5 border-b border-[#1e2d40] bg-[#060b14]">
                  <div className="flex items-center gap-2">
                    <MessageSquare className="w-4 h-4 text-[#10b981]" />
                    <span className="text-[11px] text-[#94a3b8] font-bold tracking-widest">AI 분석</span>
                  </div>
                  <button
                    onClick={() => {
                      qc.invalidateQueries({ queryKey: ['analyst-feedback'] })
                      feedbackQ.refetch()
                    }}
                    disabled={feedbackQ.isFetching}
                    className="flex items-center gap-1.5 text-[11px] text-[#10b981] hover:text-[#34d399] disabled:opacity-40 transition-colors">
                    <RefreshCw className={cn('w-3 h-3', feedbackQ.isFetching && 'animate-spin')} />
                    재분석
                  </button>
                </div>
                <div className="flex-1 overflow-y-auto p-4">
                  {feedbackQ.isFetching && (
                    <div className="flex items-center gap-2 py-4 justify-center">
                      <span className="w-2 h-2 rounded-full bg-[#10b981] animate-pulse" />
                      <span className="text-sm text-[#94a3b8]">AI 분석 중…</span>
                    </div>
                  )}
                  {feedbackQ.data && !feedbackQ.isFetching && (
                    <p className="text-sm text-[#cbd5e1] leading-relaxed whitespace-pre-wrap">{feedbackQ.data.feedback}</p>
                  )}
                </div>
              </div>
              </LockedPreview>
            )}

            {rightTab === 2 && !holdTickers && <EmptyHoldings label="보유 종목 없음" />}
            {rightTab === 2 && !!holdTickers && (
              <LockedPreview silent>
              <div className="h-full overflow-y-auto divide-y divide-[#0f172a]">
                {(newsQ.data || []).map((n, i) => (
                  <a key={i} href={n.url} target="_blank" rel="noreferrer"
                    className="block p-3.5 hover:bg-[#0a1525] transition-colors">
                    <div className="flex items-center gap-2 mb-2">
                      <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded',
                        n.ticker === 'MACRO' ? 'bg-[#9b59b6]/20 text-[#9b59b6]' : 'bg-[#3b82f6]/20 text-[#3b82f6]')}>
                        {n.ticker === 'MACRO' ? n.ticker : displayTicker(n.ticker, names)}
                      </span>
                      <span className="text-[11px] text-[#94a3b8]">
                        {new Date(n.datetime * 1000).toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric' })}
                      </span>
                    </div>
                    <p className="text-sm text-[#cbd5e1] leading-relaxed line-clamp-2">{n.headline}</p>
                  </a>
                ))}
              </div>
              </LockedPreview>
            )}
          </div>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes marquee { 0%{transform:translateX(0)} 100%{transform:translateX(-50%)} }
        .animate-marquee { animation: marquee 60s linear infinite; }

        .brief-md h1,.brief-md h2,.brief-md h3 { font-size:13px; color:#cbd5e1; font-weight:700; margin-top:12px; margin-bottom:4px; }
        .brief-md h1 { font-size:14px; color:#e2e8f0; border-bottom:1px solid #1e2d40; padding-bottom:5px; }
        .brief-md p  { font-size:13px; color:#cbd5e1; line-height:1.65; margin-bottom:6px; }
        .brief-md strong { color:#e2e8f0; }
        .brief-md ul,.brief-md ol { font-size:12px; color:#cbd5e1; padding-left:16px; margin-bottom:6px; }
        .brief-md li { margin-bottom:3px; }
        .brief-md hr { border-color:#1e2d40; margin:8px 0; }
        .brief-md code { background:#0f172a; color:#10b981; padding:2px 5px; border-radius:3px; font-size:12px; }
        .brief-md blockquote { border-left:2px solid #1d4ed8; padding-left:10px; color:#94a3b8; margin:6px 0; }
        .brief-md table { font-size:12px; width:100%; }
        .brief-md th { color:#94a3b8; border-bottom:1px solid #1e2d40; padding:4px 6px; text-align:left; font-weight:600; }
        .brief-md td { color:#cbd5e1; padding:4px 6px; border-bottom:1px solid #0f172a; }
      `}</style>
    </div>
  )
}
