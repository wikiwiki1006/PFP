import React, { useState, useMemo, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AreaChart, Area, PieChart, Pie, Cell, Sector,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import ReactMarkdown from 'react-markdown'
import {
  Shield, Zap, MessageSquare, RefreshCw,
  Plus, Trash2, Edit3, Check, X, Play, FileText, ChevronRight,
  PanelRightClose, PanelRightOpen, Download, History,
} from 'lucide-react'
import {
  getPortfolioMetrics, getEquityCurve, getHoldingsDetail, getSectorWeights,
  getMarketSnapshot, getMarketNews, getMacroData, getEarnings, getCorrelation,
  getSignalsDoomRadar, getAnalystFeedback, getCachedScan, runSignalScan,
  postTrade, updateHolding, deleteHolding, getHoldings,
  generateDailyBrief, getDailyBriefHistory, getDailyBriefFile,
  getIndexPrices, getTrades, updateTrade, deleteTrade, getTickerPrice, searchTickers,
  autoDetectSectors,
} from '@/api'
import { cn } from '@/lib/utils'

// null/NaN-safe 숫자 포맷터
const fn = (v: number | null | undefined, d = 2) => ((v == null || isNaN(v as number)) ? 0 : v).toFixed(d)
const fp = (v: number | null | undefined, d = 2, sign = true) => {
  const n = (v == null || isNaN(v as number)) ? 0 : (v as number)
  return sign ? `${n >= 0 ? '+' : ''}${n.toFixed(d)}%` : `${n.toFixed(d)}%`
}
const fv = (v: number | null | undefined) => (v == null || isNaN(v as number)) ? 0 : (v as number)

const SECTORS = [
  'Technology','Healthcare','Financials','Consumer Discretionary',
  'Consumer Staples','Energy','Industrials','Materials',
  'Real Estate','Utilities','Communication Services','Other',
]

const SECTOR_COLORS = [
  '#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6',
  '#06b6d4','#84cc16','#f97316','#ec4899','#94a3b8',
  '#a78bfa','#34d399','#fbbf24','#f87171',
]

// ── Marquee ───────────────────────────────────────────────────────────────────
function Marquee({ snapshot }: { snapshot: any }) {
  if (!snapshot?.prices) return null
  const pairs = Object.entries(snapshot.prices as Record<string, any>)

  const renderItems = (suffix = '') =>
    pairs.map(([t, v]) => {
      const p = fv(v?.change_1d_pct)
      const col = p >= 0 ? '#ef4444' : '#3b82f6'
      const price = fv(v?.price)
      if (!price) return null  // 가격 없으면 표시 안 함
      return (
        <span key={t + suffix} className="inline-flex items-center gap-1 mr-6 font-mono text-[14px]">
          <span className="font-bold text-[#e2e8f0]">{t}</span>
          <span className="text-[#cbd5e1]">${fn(price)}</span>
          <span style={{ color: col }}>{fp(p)}</span>
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
function Pill({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center px-4 py-2 border-r border-[#1e2d40] last:border-r-0 flex-shrink-0">
      <div className="text-[10px] text-[#64748b] font-bold tracking-widest uppercase">{label}</div>
      <div className="text-base font-mono font-bold mt-0.5 tabular-nums" style={{ color: color || '#e2e8f0' }}>{value}</div>
    </div>
  )
}

// ── Equity Curve ──────────────────────────────────────────────────────────────
type BenchmarkMode = 'sp500' | 'nasdaq' | 'both'

function EquityCurve({ curveQ }: { curveQ: any }) {
  const [range, setRange] = useState<'1M' | '3M' | '1Y' | 'ALL'>('1Y')
  const [bm,    setBm]    = useState<BenchmarkMode>('sp500')

  const rangeToPeriod = { '1M': '3mo', '3M': '6mo', '1Y': '2y', 'ALL': '5y' } as const

  const nasdaqQ = useQuery({
    queryKey: ['nasdaq-prices', rangeToPeriod[range]],
    queryFn:  () => getIndexPrices('^IXIC', rangeToPeriod[range]),
    enabled:  bm !== 'sp500',
    staleTime: 300_000,
  })

  // 날짜 기반 필터링 — 정확히 1M/3M/1Y 전부터 오늘까지
  const filterByRange = (all: any[]) => {
    if (range === 'ALL' || !all.length) return all
    const today  = new Date()
    const cutoff = new Date(today)
    if (range === '1M')      cutoff.setMonth(today.getMonth() - 1)
    else if (range === '3M') cutoff.setMonth(today.getMonth() - 3)
    else if (range === '1Y') cutoff.setFullYear(today.getFullYear() - 1)
    const cutStr = cutoff.toISOString().split('T')[0]
    return all.filter(d => d.date >= cutStr)
  }

  const data = useMemo((): { date: string; port: number; sp: number; nasdaq?: number }[] => {
    const all: any[] = curveQ.data || []
    const sliced = filterByRange(all)
    if (!sliced.length) return []
    const ip = sliced[0]?.value
    if (!ip || ip <= 0) return []
    const ib = sliced[0]?.benchmark_value  // null일 수 있음

    const nasdaqMap  = new Map((nasdaqQ.data || []).map((d: any) => [d.date, d.close]))
    const nasdaqBase = nasdaqMap.get(sliced[0].date) ?? [...nasdaqMap.values()][0] as number | undefined

    const safeCalc = (n: number, base: number): number => {
      if (!base || !isFinite(base) || !isFinite(n)) return 0
      const r = (n / base - 1) * 100
      return isNaN(r) || !isFinite(r) ? 0 : +r.toFixed(2)
    }

    return sliced.map((d: any) => {
      const nc = nasdaqMap.get(d.date) as number | undefined
      return {
        date:   d.date,
        port:   safeCalc(d.value, ip),
        sp:     ib != null && d.benchmark_value != null ? safeCalc(d.benchmark_value, ib) : 0,
        nasdaq: nc != null && nasdaqBase != null ? safeCalc(nc, nasdaqBase) : undefined,
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curveQ.data, range, nasdaqQ.data])

  const fmtDate = (v: string) => {
    const p = v?.split('-')
    return p?.length === 3 ? `${p[0].slice(2)}.${p[1]}.${p[2]}` : v
  }

  const last    = data[data.length - 1]
  const portPct = last?.port   ?? 0
  const spPct   = last?.sp     ?? 0
  const nqPct   = last?.nasdaq ?? 0
  const alpha   = portPct - (bm === 'nasdaq' ? nqPct : spPct)

  const TIP = { backgroundColor: '#0b1220', border: '1px solid #1e2d40', borderRadius: 4, fontSize: 12 }

  const BM_BTNS: { key: BenchmarkMode; label: string; color: string }[] = [
    { key: 'sp500',  label: 'S&P',  color: '#64748b' },
    { key: 'nasdaq', label: 'NQ',   color: '#a78bfa' },
    { key: 'both',   label: 'BOTH', color: '#f59e0b' },
  ]

  return (
    <div className="px-4 pt-3 pb-2 border-b border-[#1e2d40]">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-4 flex-wrap">
          <span className="text-[11px] text-[#94a3b8] font-bold tracking-[3px] uppercase">Equity Curve</span>
          <div className="flex items-center gap-2">
            <svg width="20" height="5"><line x1="0" y1="2.5" x2="20" y2="2.5" stroke="#00e6ff" strokeWidth="2.5" /></svg>
            <span className="text-sm font-mono font-bold tabular-nums"
              style={{ color: portPct >= 0 ? '#10b981' : '#ef4444' }}>
              Portfolio {fp(portPct)}
            </span>
          </div>
          {(bm === 'sp500' || bm === 'both') && (
            <div className="flex items-center gap-2">
              <svg width="20" height="5">
                <line x1="0" y1="2.5" x2="5" y2="2.5" stroke="#64748b" strokeWidth="1.5" />
                <line x1="7" y1="2.5" x2="12" y2="2.5" stroke="#64748b" strokeWidth="1.5" />
                <line x1="14" y1="2.5" x2="20" y2="2.5" stroke="#64748b" strokeWidth="1.5" />
              </svg>
              <span className="text-sm font-mono text-[#64748b] tabular-nums">
                S&P {fp(spPct)}
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
                NASDAQ {fp(nqPct)}
              </span>
            </div>
          )}
          <span className="text-sm font-mono font-bold tabular-nums px-2 py-0.5 rounded"
            style={{
              color: alpha >= 0 ? '#10b981' : '#ef4444',
              backgroundColor: alpha >= 0 ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
            }}>
            α {fp(alpha)}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex gap-0.5 bg-[#070d18] border border-[#1e2d40] rounded p-0.5">
            {BM_BTNS.map(b => (
              <button key={b.key} onClick={() => setBm(b.key)}
                className={cn('text-[11px] px-2.5 py-1 rounded font-bold transition-colors duration-100',
                  bm === b.key ? '' : 'text-[#4a5568] hover:text-[#64748b]'
                )}
                style={bm === b.key ? { backgroundColor: b.color + '28', color: b.color } : {}}>
                {b.label}
              </button>
            ))}
          </div>
          <div className="flex gap-0.5 bg-[#070d18] border border-[#1e2d40] rounded p-0.5">
            {(['1M', '3M', '1Y', 'ALL'] as const).map(r => (
              <button key={r} onClick={() => setRange(r)}
                className={cn('text-[11px] px-2.5 py-1 rounded font-bold transition-colors duration-100',
                  range === r ? 'bg-[#00e6ff]/15 text-[#00e6ff]' : 'text-[#4a5568] hover:text-[#64748b]'
                )}>
                {r}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 로딩 상태 */}
      {curveQ.isLoading && (
        <div className="flex items-center justify-center" style={{ height: 300 }}>
          <span className="text-[13px] text-[#334155] font-mono">로드 중…</span>
        </div>
      )}

      {/* 데이터 없음 */}
      {!curveQ.isLoading && !data.length && (
        <div className="flex items-center justify-center" style={{ height: 300 }}>
          <span className="text-[13px] text-[#334155] font-mono">데이터 없음</span>
        </div>
      )}

      {/* 차트 — ResponsiveContainer를 고정 높이 div로 감쌈 (width 계산 타이밍 버그 방지) */}
      {!curveQ.isLoading && data.length > 0 && (
      <div style={{ width: '100%', height: 300 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="gPort" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#00e6ff" stopOpacity={0.25} />
              <stop offset="100%" stopColor="#00e6ff" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="gSP" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#64748b" stopOpacity={0.15} />
              <stop offset="100%" stopColor="#64748b" stopOpacity={0} />
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
            interval="preserveStartEnd"
            tickFormatter={fmtDate}
          />
          <YAxis
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false} axisLine={false}
            tickFormatter={v => `${v >= 0 ? '+' : ''}${v.toFixed(0)}%`}
            width={40}
          />
          <ReferenceLine y={0} stroke="#1e2d40" strokeDasharray="3 4" strokeWidth={1} />
          <Tooltip
            contentStyle={TIP}
            formatter={(v: number, key: string) => [
              `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`,
              key === 'port' ? 'Portfolio' : key === 'sp' ? 'S&P 500' : 'NASDAQ',
            ]}
            labelFormatter={fmtDate}
            labelStyle={{ color: '#94a3b8', fontSize: 11 }}
          />
          {(bm === 'sp500' || bm === 'both') && (
            <Area type="monotone" dataKey="sp" stroke="#64748b" strokeWidth={1.5}
              strokeDasharray="4 3" fill="url(#gSP)" dot={false}
              isAnimationActive={false} />
          )}
          {(bm === 'nasdaq' || bm === 'both') && (
            <Area type="monotone" dataKey="nasdaq" stroke="#a78bfa" strokeWidth={1.5}
              strokeDasharray="4 3" fill="url(#gNQ)" dot={false}
              isAnimationActive={false} />
          )}
          <Area type="monotone" dataKey="port" stroke="#00e6ff" strokeWidth={2.5}
            fill="url(#gPort)" dot={false}
            isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
      </div>
      )}
    </div>
  )
}

// ── Holdings + History Panel ──────────────────────────────────────────────────
function HoldingsPanel({ holdQ }: { holdQ: any }) {
  const qc = useQueryClient()
  const [view, setView] = useState<'holdings' | 'history'>('holdings')

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
        }
      }).catch(() => {})
    } else if (holdings.length > 0) {
      autoSectorRan.current = true  // 이미 모두 분류됨
    }
  }, [holdQ.data, holdQ.isLoading, qc])

  // ── Trade form state ───────────────────────────────────────────────────
  const todayStr = useMemo(() => new Date().toISOString().slice(0, 10), [])
  const [form,        setForm]        = useState({ ticker: '', type: 'BUY', q: 0, price: 0, date: todayStr })
  const [suggestions, setSuggestions] = useState<{ ticker: string; name: string }[]>([])
  const [showSug,     setShowSug]     = useState(false)
  const [sugIdx,      setSugIdx]      = useState(-1)
  const [tickerError, setTickerError] = useState('')
  const [priceLoading,setPriceLoading]= useState(false)
  const tickerInputRef = useRef<HTMLInputElement>(null)

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
    onError: (e: any) => alert(`편집 실패: ${e?.response?.data?.detail || e.message}`),
  })
  const deleteMut = useMutation({
    mutationFn: (ticker: string) => deleteHolding(ticker),
    onSuccess: _invalidateAll,
    onError: (e: any) => alert(`삭제 실패: ${e?.response?.data?.detail || e.message}`),
  })
  const tradeMut = useMutation({
    mutationFn: (f: typeof form) => postTrade({ ticker: f.ticker, type: f.type as any, q: f.q, price: f.price, date: f.date }),
    onSuccess: (_data, vars) => {
      _invalidateAll()
      setForm(f => ({ ...f, ticker: '', q: 0, price: 0, date: new Date().toISOString().slice(0, 10) }))
      setTickerError('')
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
      setTickerError(`거래 실패: ${msg}`)
    },
  })
  const sellMut = useMutation({
    mutationFn: ({ ticker, q, price, date }: any) => postTrade({ ticker, type: 'SELL', q, price, date }),
    onSuccess: () => { setSellTicker(null); _invalidateAll() },
    onError: (e: any) => alert(`SELL 실패: ${e?.response?.data?.detail || e.message}`),
  })
  const updateTradeMut = useMutation({
    mutationFn: ({ id, ticker, type, vals }: any) => updateTrade(id, {
      date: vals.date, ticker, type, q: vals.q, price: vals.price, memo: vals.memo,
    }),
    // 거래 수정 → holdings도 재계산됨 (백엔드 _recalculate_holding_from_trades)
    onSuccess: () => { setEditTradeId(null); _invalidateAll() },
    onError: (e: any) => alert(`거래 수정 실패: ${e?.response?.data?.detail || e.message}`),
  })
  const deleteTradeMut = useMutation({
    mutationFn: (id: number) => deleteTrade(id),
    // 거래 삭제 → holdings도 재계산됨 (백엔드 _recalculate_holding_from_trades)
    onSuccess: _invalidateAll,
    onError: (e: any) => alert(`거래 삭제 실패: ${e?.response?.data?.detail || e.message}`),
  })

  // SELL 인라인 폼 열기 — 현재 보유수량 + 현재가 자동 설정
  const openSell = async (h: any) => {
    setSellTicker(h.ticker)
    setSellVals({ q: h.qty ?? 0, price: h.current_price ?? 0, date: new Date().toISOString().slice(0, 10) })
    if (!h.current_price) {
      setSellPriceLoading(true)
      try {
        const r = await getTickerPrice(h.ticker)
        setSellVals(v => ({ ...v, price: r.price }))
      } catch {} finally { setSellPriceLoading(false) }
    }
  }

  // ── Ticker 검색 (전체 미국 상장) ─────────────────────────────────────
  // 입력 중 300ms debounce 후 API 검색
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleTickerChange = (val: string) => {
    const upper = val.toUpperCase()
    setForm(f => ({ ...f, ticker: upper }))
    setTickerError('')
    setSugIdx(-1)
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

  const selectSuggestion = async (ticker: string) => {
    setForm(f => ({ ...f, ticker }))
    setSuggestions([]); setShowSug(false); setSugIdx(-1); setTickerError('')
    await fetchAndSetPrice(ticker)
  }

  const fetchAndSetPrice = async (ticker: string) => {
    if (!ticker) return
    setPriceLoading(true)
    try {
      const result = await getTickerPrice(ticker)
      setForm(f => ({ ...f, price: result.price }))
      setTickerError('')
    } catch {
      setTickerError(`"${ticker}"은(는) 유효하지 않은 티커입니다. 다시 시도해주세요.`)
      setForm(f => ({ ...f, ticker: '', price: 0 }))
    } finally {
      setPriceLoading(false)
    }
  }

  const handleTickerKeyDown = (e: React.KeyboardEvent) => {
    if (!showSug || suggestions.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSugIdx(i => Math.min(i + 1, suggestions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSugIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && sugIdx >= 0) {
      e.preventDefault()
      selectSuggestion(suggestions[sugIdx].ticker)
    } else if (e.key === 'Escape') {
      setShowSug(false); setSugIdx(-1)
    }
  }

  const handleTickerBlur = () => {
    setTimeout(() => {
      setShowSug(false); setSugIdx(-1)
      const t = form.ticker.trim()
      if (t && !form.price) fetchAndSetPrice(t)
    }, 180)
  }

  // QTY 마우스 스크롤
  const handleQtyWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    setForm(f => ({ ...f, q: Math.max(0, f.q + (e.deltaY < 0 ? 1 : -1)) }))
  }

  // History 최신순
  const trades = (tradesQ.data || []).slice().reverse()

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* 탭 헤더 */}
      <div className="flex items-center border-b border-[#1e2d40] flex-shrink-0 bg-[#070d18]">
        <button onClick={() => setView('holdings')}
          className={cn('text-[11px] font-bold tracking-[3px] uppercase px-3 py-2.5 transition-colors',
            view === 'holdings' ? 'text-[#e2e8f0] border-b-2 border-[#3b82f6]' : 'text-[#4a5568] hover:text-[#64748b]')}>
          Holdings
        </button>
        <button onClick={() => setView('history')}
          className={cn('flex items-center gap-1.5 text-[11px] font-bold tracking-[3px] uppercase px-3 py-2.5 transition-colors',
            view === 'history' ? 'text-[#e2e8f0] border-b-2 border-[#3b82f6]' : 'text-[#4a5568] hover:text-[#64748b]')}>
          <History className="w-3 h-3" />History
        </button>
      </div>

      {/* ── Holdings 뷰 ─────────────────────────────────────────────────── */}
      {view === 'holdings' && (
        <>
          <div className="flex-1 overflow-y-auto min-h-0">
            <table className="w-full">
              <thead className="sticky top-0 bg-[#07101c] z-10">
                <tr className="text-[#64748b] border-b border-[#1e2d40]">
                  {['Ticker','Avg','Qty','Price','1D%','P&L','Wt','',''].map((hd, i) => (
                    <th key={i} className="text-left py-2.5 px-2.5 font-semibold text-[11px] tracking-wider">{hd}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(holdQ.data || []).map((h: any) => (
                  <React.Fragment key={h.ticker}>
                  <tr className="border-b border-[#0f172a] hover:bg-[#0a1525] group transition-colors">
                    {editTicker === h.ticker ? (
                      <>
                        <td className="py-2 px-2.5 font-mono font-bold text-sm text-[#e2e8f0]">{h.ticker}</td>
                        <td className="py-2 px-2.5">
                          <input type="number" value={editVals.avg}
                            onChange={e => setEditVals(v => ({ ...v, avg: +e.target.value }))}
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
                        <td className="py-2 px-2.5 font-mono font-bold text-[15px] text-[#e2e8f0]">{h.ticker}</td>
                        <td className="py-2 px-2.5 font-mono text-[12px] text-[#94a3b8]">${fn(h.avg_cost, 0)}</td>
                        <td className="py-2 px-2.5 font-mono text-[12px] text-[#94a3b8]">{fv(h.qty)}</td>
                        <td className="py-2 px-2.5 font-mono text-[12px] text-[#cbd5e1]">${fn(h.current_price, 1)}</td>
                        <td className="py-2 px-2.5 font-mono text-[13px] font-bold" style={{ color: fv(h.pnl_pct) >= 0 ? '#10b981' : '#ef4444' }}>
                          {fp(h.pnl_pct, 1)}
                        </td>
                        <td className="py-2 px-2.5 font-mono text-[13px] font-bold" style={{ color: fv(h.pnl) >= 0 ? '#10b981' : '#ef4444' }}>
                          {fp(h.pnl, 0)}
                        </td>
                        <td className="py-2 px-2.5 font-mono text-[12px] text-[#94a3b8]">{fn(fv(h.weight) * 100, 0)}%</td>
                        {/* SELL 버튼 */}
                        <td className="py-2 px-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button type="button"
                            onPointerDown={e => { e.preventDefault(); openSell(h) }}
                            className="text-[10px] font-bold text-[#f59e0b] border border-[#f59e0b]/40 rounded px-1.5 py-0.5 hover:bg-[#f59e0b]/15 transition-colors">
                            SELL
                          </button>
                        </td>
                        {/* Edit / Delete */}
                        <td className="py-2 px-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          <div className="flex gap-1">
                            <button type="button"
                              onPointerDown={e => {
                                e.preventDefault()
                                setEditTicker(h.ticker)
                                const newVals = { q: h.qty, avg: h.avg_cost, sector: h.sector || 'Other' }
                                setEditVals(newVals)
                                editValsRef.current = newVals
                              }}
                              className="text-[#374151] hover:text-[#3b82f6] transition-colors">
                              <Edit3 className="w-3.5 h-3.5" />
                            </button>
                            <button type="button"
                              onClick={() => {
                                if (window.confirm(`${h.ticker} 보유를 삭제하시겠습니까?`))
                                  deleteMut.mutate(h.ticker)
                              }}
                              className="text-[#374151] hover:text-[#ef4444] transition-colors">
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </td>
                      </>
                    )}
                  </tr>
                  {/* 인라인 SELL 폼 */}
                  {sellTicker === h.ticker && (
                    <tr className="border-b border-[#f59e0b]/20 bg-[#0a0e18]">
                      <td colSpan={9} className="px-3 py-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[#f59e0b] font-mono font-bold text-[12px] flex-shrink-0">SELL {h.ticker}</span>
                          <input type="number" value={sellVals.q || ''}
                            onChange={e => setSellVals(v => ({ ...v, q: +e.target.value }))}
                            placeholder="Qty" min={0.001} step={0.001}
                            className="w-16 bg-[#1e2d40] border border-[#334155] text-sm text-[#e2e8f0] rounded px-2 py-1" />
                          <input type="number" value={sellVals.price || ''}
                            onChange={e => setSellVals(v => ({ ...v, price: +e.target.value }))}
                            placeholder={sellPriceLoading ? '조회중…' : 'Price'}
                            disabled={sellPriceLoading}
                            className="w-20 bg-[#1e2d40] border border-[#334155] text-sm text-[#e2e8f0] rounded px-2 py-1" />
                          <input type="date" value={sellVals.date}
                            onChange={e => setSellVals(v => ({ ...v, date: e.target.value }))}
                            max={todayStr}
                            className="bg-[#1e2d40] border border-[#334155] text-sm text-[#94a3b8] rounded px-2 py-1 [color-scheme:dark]" />
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
                            className="text-[#64748b] hover:text-[#94a3b8] text-[12px]">취소</button>
                        </div>
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>

          {/* 거래 입력 폼 */}
          <div className="flex-shrink-0 border-t border-[#1e2d40] px-3 py-2 bg-[#060b14] space-y-2">
            {tickerError && (
              <div className="text-[11px] text-[#ef4444] flex items-center gap-1">
                <X className="w-3 h-3" />{tickerError}
              </div>
            )}
            <div className="flex items-center gap-2 flex-wrap">
              {/* Ticker — 전체 미국 티커 검색 */}
              <div className="relative">
                <input
                  ref={tickerInputRef}
                  value={form.ticker}
                  onChange={e => handleTickerChange(e.target.value)}
                  onBlur={handleTickerBlur}
                  onKeyDown={handleTickerKeyDown}
                  placeholder="Ticker"
                  autoComplete="off"
                  className="w-24 bg-[#0b1220] border border-[#1e2d40] text-sm font-mono text-[#e2e8f0] rounded px-2 py-1.5 placeholder-[#334155] focus:outline-none focus:border-[#3b82f6]"
                />
                {showSug && suggestions.length > 0 && (
                  <div className="absolute bottom-full mb-1 left-0 z-50 bg-[#0b1220] border border-[#1e2d40] rounded shadow-xl min-w-[180px]">
                    {suggestions.map((s, idx) => (
                      <button key={s.ticker}
                        onMouseDown={e => { e.preventDefault(); selectSuggestion(s.ticker) }}
                        className={cn(
                          'flex items-center gap-2 w-full text-left px-3 py-2 transition-colors',
                          idx === sugIdx ? 'bg-[#1e2d40] text-[#e2e8f0]' : 'text-[#94a3b8] hover:bg-[#0f1e30] hover:text-[#e2e8f0]'
                        )}>
                        <span className="font-mono font-bold text-[13px] flex-shrink-0">{s.ticker}</span>
                        <span className="text-[11px] text-[#475569] truncate">{s.name}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* BUY / SELL */}
              <select value={form.type} onChange={e => setForm(f => ({ ...f, type: e.target.value }))}
                className="bg-[#0b1220] border border-[#1e2d40] text-sm text-[#94a3b8] rounded px-2 py-1.5 focus:outline-none focus:border-[#3b82f6]">
                <option value="BUY">BUY</option>
                <option value="SELL">SELL</option>
              </select>

              {/* QTY — 스크롤 지원 */}
              <input type="number" value={form.q || ''}
                onChange={e => setForm(f => ({ ...f, q: +e.target.value }))}
                onWheel={handleQtyWheel}
                placeholder="Qty" min={0} step={1}
                className="w-14 bg-[#0b1220] border border-[#1e2d40] text-sm font-mono text-[#e2e8f0] rounded px-2 py-1.5 placeholder-[#334155] focus:outline-none focus:border-[#3b82f6]"
              />

              {/* Price — 직접 입력만 (현재가 자동 조회 유지) */}
              <input type="number" value={form.price || ''}
                onChange={e => setForm(f => ({ ...f, price: +e.target.value }))}
                placeholder={priceLoading ? '조회중…' : 'Price'}
                disabled={priceLoading}
                className="w-24 bg-[#0b1220] border border-[#1e2d40] text-sm font-mono text-[#e2e8f0] rounded px-2 py-1.5 placeholder-[#334155] focus:outline-none focus:border-[#3b82f6]"
              />

              {/* Date — 매수/매도 날짜 */}
              <input type="date" value={form.date}
                onChange={e => setForm(f => ({ ...f, date: e.target.value }))}
                max={todayStr}
                className="bg-[#0b1220] border border-[#1e2d40] text-sm font-mono text-[#94a3b8] rounded px-2 py-1.5 focus:outline-none focus:border-[#3b82f6] [color-scheme:dark]"
              />

              {/* Submit — onPointerDown으로 ticker blur보다 먼저 발화 */}
              <button type="button"
                onPointerDown={e => {
                  e.preventDefault()
                  if (form.ticker && form.q && form.price && !priceLoading && !tradeMut.isPending)
                    tradeMut.mutate(form)
                }}
                disabled={!form.ticker || !form.q || !form.price || priceLoading || tradeMut.isPending}
                className="bg-[#1d4ed8] hover:bg-[#2563eb] disabled:opacity-40 text-white rounded px-3 py-1.5 transition-colors text-sm font-bold">
                <Plus className="w-4 h-4" />
              </button>
            </div>
          </div>
        </>
      )}

      {/* ── History 뷰 ──────────────────────────────────────────────────── */}
      {view === 'history' && (
        <div className="flex-1 overflow-y-auto min-h-0">
          {tradesQ.isLoading && <div className="text-center py-4 text-sm text-[#64748b]">로드 중…</div>}
          {!tradesQ.isLoading && trades.length === 0 && (
            <div className="flex items-center justify-center h-full text-sm text-[#4a5568]">거래 기록 없음</div>
          )}
          <table className="w-full">
            <thead className="sticky top-0 bg-[#07101c] z-10">
              <tr className="text-[#64748b] border-b border-[#1e2d40]">
                {['Date', 'Ticker', 'Type', 'Qty', 'Price', 'Memo', ''].map(h => (
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
                      <td className="py-1.5 px-2 font-mono font-bold text-sm text-[#e2e8f0]">{t.ticker}</td>
                      <td className="py-1.5 px-2 text-[12px]"
                        style={{ color: t.type === 'ADD' || t.type === 'BUY' ? '#10b981' : '#ef4444' }}>
                        {t.type}
                      </td>
                      <td className="py-1.5 px-2">
                        <input type="number" value={editTradeVals.q}
                          onChange={e => setEditTradeVals(v => ({ ...v, q: +e.target.value }))}
                          className="w-14 bg-[#1e2d40] border border-[#334155] text-[12px] text-[#e2e8f0] rounded px-1 py-1" />
                      </td>
                      <td className="py-1.5 px-2">
                        <input type="number" value={editTradeVals.price}
                          onChange={e => setEditTradeVals(v => ({ ...v, price: +e.target.value }))}
                          className="w-18 bg-[#1e2d40] border border-[#334155] text-[12px] text-[#e2e8f0] rounded px-1 py-1" />
                      </td>
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
                      <td className="py-2 px-2 font-mono text-[11px] text-[#64748b]">{t.date}</td>
                      <td className="py-2 px-2 font-mono font-bold text-[13px] text-[#e2e8f0]">{t.ticker}</td>
                      <td className="py-2 px-2 text-[11px] font-bold"
                        style={{ color: t.type === 'ADD' || t.type === 'BUY' ? '#10b981' : '#ef4444' }}>
                        {t.type}
                      </td>
                      <td className="py-2 px-2 font-mono text-[12px] text-[#94a3b8]">{t.q}</td>
                      <td className="py-2 px-2 font-mono text-[12px] text-[#cbd5e1]">{t.price ? `$${t.price}` : '—'}</td>
                      <td className="py-2 px-2 text-[11px] text-[#475569] max-w-[80px] truncate">{t.memo || ''}</td>
                      <td className="py-2 px-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <div className="flex gap-1">
                          <button onClick={() => {
                            setEditTradeId(t.id)
                            setEditTradeVals({ date: t.date, q: t.q, price: t.price || 0, memo: t.memo || '' })
                          }} className="text-[#374151] hover:text-[#3b82f6]"><Edit3 className="w-3 h-3" /></button>
                          <button
                            onClick={() => { if (confirm('삭제하시겠습니까?')) deleteTradeMut.mutate(t.id) }}
                            className="text-[#374151] hover:text-[#ef4444]"><Trash2 className="w-3 h-3" /></button>
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
          {payload.name.length > 10 ? payload.name.slice(0, 10) + '…' : payload.name}
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
      <div className="text-[11px] text-[#94a3b8] font-bold tracking-[3px] uppercase px-3 py-2.5 border-b border-[#1e2d40] flex-shrink-0 bg-[#070d18]">
        Sectors
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
              <div className="text-[10px] text-[#64748b] font-bold tracking-wider mb-1.5 uppercase">
                {hoveredSector}
              </div>
              {tooltipTickers.map(t => (
                <div key={t} className="text-[12px] font-mono text-[#cbd5e1]">{t}</div>
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
                style={{ color: active === i ? '#cbd5e1' : '#64748b' }}>
                {s.name}
              </span>
              <div className="w-12 h-2 bg-[#1e2d40] rounded-full overflow-hidden flex-shrink-0">
                <div className="h-full rounded-full transition-all duration-300"
                  style={{
                    width: `${Math.min(100, (s.value / (data[0]?.value || 1)) * 100)}%`,
                    backgroundColor: s.fill,
                    opacity: active === i ? 1 : 0.5,
                  }} />
              </div>
              <span className="text-[12px] font-mono font-bold w-9 text-right flex-shrink-0 tabular-nums"
                style={{ color: active === i ? s.fill : '#475569' }}>
                {s.value}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Correlation Heatmap ───────────────────────────────────────────────────────
function CorrelationHeatmap({ data }: { data: { tickers: string[]; labels?: string[]; matrix: number[][] } }) {
  const [hovered, setHovered] = useState<{ i: number; j: number } | null>(null)

  const label = (t: string, i: number) => {
    if (data.labels?.[i]) return data.labels[i]
    return t.replace('^', '').replace('-USD', '').slice(0, 6)
  }

  const cellColors = (v: number, isDiag: boolean) => {
    if (isDiag) return { bg: 'rgba(255,255,255,0.04)', text: 'rgba(255,255,255,0.25)', border: 'rgba(255,255,255,0.06)' }
    const c = Math.max(-1, Math.min(1, v))
    if (c >= 0) {
      const a = 0.12 + c * 0.62
      const textBrightness = c > 0.6 ? '#fca5a5' : c > 0.3 ? '#f87171' : '#94a3b8'
      return { bg: `rgba(239,68,68,${a.toFixed(2)})`, text: textBrightness, border: `rgba(239,68,68,${(a * 0.6).toFixed(2)})` }
    } else {
      const a = 0.12 + (-c) * 0.62
      const textBrightness = -c > 0.6 ? '#93c5fd' : -c > 0.3 ? '#60a5fa' : '#94a3b8'
      return { bg: `rgba(59,130,246,${a.toFixed(2)})`, text: textBrightness, border: `rgba(59,130,246,${(a * 0.6).toFixed(2)})` }
    }
  }

  const n = data.tickers.length
  const sz = n <= 8 ? 36 : n <= 12 ? 30 : 25
  const gap = 3
  const hv = hovered

  const corrLabel = (v: number) => {
    const a = Math.abs(v)
    const dir = v > 0 ? '양의' : '음의'
    if (a > 0.7) return `강한 ${dir} 상관`
    if (a > 0.4) return `중간 ${dir} 상관`
    if (a > 0.15) return `약한 ${dir} 상관`
    return '무상관'
  }

  return (
    <div className="select-none space-y-3">
      <div className="overflow-auto">
        <div style={{ display: 'inline-flex', flexDirection: 'column', gap: `${gap}px` }}>
          <div style={{ display: 'flex', gap: `${gap}px`, paddingLeft: `${sz + gap + 8}px` }}>
            {data.tickers.map((t, j) => (
              <div key={j} style={{ width: sz, textAlign: 'center' }}>
                <span style={{
                  fontSize: '9px', fontWeight: 700, fontFamily: 'monospace',
                  color: hv && (hv.i === j || hv.j === j) ? '#cbd5e1' : '#475569',
                  transition: 'color 0.15s',
                }}>
                  {label(t, j)}
                </span>
              </div>
            ))}
          </div>
          {data.matrix.map((row, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: `${gap}px` }}>
              <div style={{ width: sz, paddingRight: 8, textAlign: 'right' }}>
                <span style={{
                  fontSize: '9px', fontWeight: 700, fontFamily: 'monospace',
                  color: hv && (hv.i === i || hv.j === i) ? '#cbd5e1' : '#475569',
                  transition: 'color 0.15s',
                }}>
                  {label(data.tickers[i], i)}
                </span>
              </div>
              {row.map((v, j) => {
                const isDiag = i === j
                const { bg, text, border } = cellColors(v, isDiag)
                const isHov = hv?.i === i && hv?.j === j
                return (
                  <div key={j}
                    onMouseEnter={() => setHovered({ i, j })}
                    onMouseLeave={() => setHovered(null)}
                    style={{
                      width: sz, height: sz, background: bg, borderRadius: 5,
                      border: `1px solid ${isHov ? 'rgba(255,255,255,0.25)' : border}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: 'default',
                      transition: 'transform 0.08s ease, box-shadow 0.08s ease',
                      transform: isHov ? 'scale(1.18)' : 'scale(1)',
                      boxShadow: isHov ? `0 0 10px ${bg}` : 'none',
                      zIndex: isHov ? 10 : 1, position: 'relative',
                    }}>
                    {isDiag ? (
                      <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.2)', fontWeight: 700 }}>●</span>
                    ) : (
                      <span style={{ fontSize: sz >= 32 ? 9 : 8, fontWeight: 700, color: text, fontFamily: 'monospace', lineHeight: 1 }}>
                        {fn(v)}
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>
      <div style={{ minHeight: 28 }}>
        {hv && hv.i !== hv.j && (
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#0f172a] border border-[#1e293b] text-[11px]">
            <span className="font-mono font-bold text-[#94a3b8]">{label(data.tickers[hv.i], hv.i)}</span>
            <span className="text-[#334155]">↔</span>
            <span className="font-mono font-bold text-[#94a3b8]">{label(data.tickers[hv.j], hv.j)}</span>
            <span className="text-[#1e293b] mx-1">|</span>
            <span className={`font-mono font-bold text-[13px] ${data.matrix[hv.i][hv.j] > 0 ? 'text-[#f87171]' : 'text-[#60a5fa]'}`}>
              {fp(data.matrix[hv.i][hv.j], 4)}
            </span>
            <span className="text-[#374151] text-[10px]">{corrLabel(data.matrix[hv.i][hv.j])}</span>
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 px-1">
        <span className="text-[9px] text-[#3b82f6] font-bold">-1</span>
        <div style={{
          flex: 1, height: 5, borderRadius: 3,
          background: 'linear-gradient(to right, rgba(59,130,246,0.8) 0%, rgba(59,130,246,0.1) 45%, rgba(239,68,68,0.1) 55%, rgba(239,68,68,0.8) 100%)',
        }} />
        <span className="text-[9px] text-[#ef4444] font-bold">+1</span>
      </div>
      <div className="flex justify-between px-1">
        <span className="text-[8px] text-[#3b82f6]">음의 상관</span>
        <span className="text-[8px] text-[#374151]">무상관</span>
        <span className="text-[8px] text-[#ef4444]">양의 상관</span>
      </div>
    </div>
  )
}

// sessionStorage keys
const SK_CONTENT  = 'pfp_brief_content'
const SK_FILE     = 'pfp_brief_file'
const SK_LOGS     = 'pfp_brief_logs'
const SK_PENDING  = 'pfp_brief_pending'

// ── Daily Brief (right panel) ─────────────────────────────────────────────────
function DailyBriefPanel() {
  const [file, setFile]         = useState<string | null>(() => sessionStorage.getItem(SK_FILE))
  const [content, setContent]   = useState<string | null>(() => sessionStorage.getItem(SK_CONTENT))
  const [logs, setLogs]         = useState<string[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(SK_LOGS) || '[]') } catch { return [] }
  })
  const [wasPending, setWasPending] = useState(() => sessionStorage.getItem(SK_PENDING) === '1')
  const [showHist,  setShowHist]    = useState(false)
  const [pdfBusy,   setPdfBusy]     = useState(false)
  const contentRef                  = useRef<HTMLDivElement>(null)

  const histQ   = useQuery({ queryKey: ['daily-brief-history'], queryFn: getDailyBriefHistory, staleTime: 60_000 })
  const fileMut = useMutation({
    mutationFn: getDailyBriefFile,
    onSuccess: d => {
      setContent(d.content)
      sessionStorage.setItem(SK_CONTENT, d.content)
    },
  })
  const genMut = useMutation({
    mutationFn: generateDailyBrief,
    onMutate: () => {
      setLogs(['[1/3] 가격 데이터 수집…'])
      setContent(null)
      setWasPending(false)
      sessionStorage.removeItem(SK_CONTENT)
      sessionStorage.setItem(SK_PENDING, '1')
      sessionStorage.setItem(SK_LOGS, JSON.stringify(['[1/3] 가격 데이터 수집…']))
    },
    onSuccess: d => {
      const newLogs = d.logs?.length ? d.logs : ['완료']
      setContent(d.report)
      setLogs(newLogs)
      sessionStorage.setItem(SK_CONTENT, d.report)
      sessionStorage.setItem(SK_LOGS, JSON.stringify(newLogs))
      sessionStorage.removeItem(SK_PENDING)
      histQ.refetch()
    },
    onError: (e: any) => {
      setLogs(prev => {
        const next = [...prev, `오류: ${e.message}`]
        sessionStorage.setItem(SK_LOGS, JSON.stringify(next))
        return next
      })
      sessionStorage.removeItem(SK_PENDING)
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
      const titleStr = file ? file.replace(/\.md$/, '') : `Daily Brief · ${dateStr}`

      const wrap = document.createElement('div')
      wrap.style.cssText = 'position:fixed;top:0;left:0;width:800px;background:#fff;z-index:-9999;pointer-events:none'
      wrap.innerHTML = `
        <div style="background:#0f2044;padding:24px 40px 20px;">
          <div style="font-size:9px;letter-spacing:4px;color:#93c5fd;font-weight:700;margin-bottom:6px">PERSONAL FINANCIAL PLATFORM</div>
          <div style="font-size:22px;font-weight:900;color:#ffffff;line-height:1.2">${titleStr}</div>
          <div style="font-size:11px;color:#bfdbfe;margin-top:6px">${dateStr} · PFP Alpha Terminal</div>
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
      alert('PDF 생성 중 오류가 발생했습니다.')
    } finally {
      setPdfBusy(false)
    }
  }

  const isGenerating = genMut.isPending

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex-shrink-0 flex gap-2 p-3 border-b border-[#1e2d40]">
        <button onClick={() => genMut.mutate()} disabled={isGenerating}
          className="flex-1 flex items-center justify-center gap-2 py-2 bg-[#10b981]/12 border border-[#10b981]/30 text-[#10b981] text-[11px] font-bold rounded hover:bg-[#10b981]/20 disabled:opacity-50 transition-colors">
          <Play className="w-3.5 h-3.5" />
          {isGenerating ? 'GENERATING…' : 'GENERATE BRIEF'}
        </button>
        <button onClick={downloadPDF} disabled={!content || pdfBusy} title="PDF로 다운로드"
          className={cn('px-3 py-2 rounded border text-[11px] font-bold transition-colors flex items-center gap-1.5',
            content && !pdfBusy
              ? 'border-[#3b82f6]/50 bg-[#3b82f6]/10 text-[#3b82f6] hover:bg-[#3b82f6]/20'
              : 'border-[#1e2d40] text-[#374151] cursor-not-allowed opacity-40'
          )}>
          {pdfBusy
            ? <span className="w-3.5 h-3.5 border-2 border-[#3b82f6] border-t-transparent rounded-full animate-spin" />
            : <Download className="w-3.5 h-3.5" />}
          <span>PDF</span>
        </button>
        <button onClick={() => setShowHist(h => !h)}
          className={cn('px-3 py-2 rounded border transition-colors',
            showHist ? 'bg-[#1e2d40] border-[#64748b]/50 text-[#94a3b8]' : 'border-[#1e2d40] text-[#64748b] hover:text-[#94a3b8]'
          )}>
          <FileText className="w-4 h-4" />
        </button>
      </div>

      {wasPending && !isGenerating && !content && (
        <div className="flex-shrink-0 border-b border-[#f59e0b]/30 px-3 py-2 bg-[#f59e0b]/8 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-[#f59e0b] flex-shrink-0" />
          <span className="text-[11px] text-[#f59e0b] flex-1">생성이 진행 중이었습니다. 히스토리에서 완료된 보고서를 확인하세요.</span>
          <button onClick={() => { setWasPending(false); sessionStorage.removeItem(SK_PENDING); setShowHist(true); histQ.refetch() }}
            className="text-[10px] text-[#f59e0b] underline whitespace-nowrap">히스토리 열기</button>
        </div>
      )}

      {showHist && (
        <div className="flex-shrink-0 max-h-40 overflow-y-auto border-b border-[#1e2d40] bg-[#060b14]">
          {(histQ.data || []).map(f => (
            <button key={f.name}
              onClick={() => {
                setFile(f.name); sessionStorage.setItem(SK_FILE, f.name)
                fileMut.mutate(f.name); setShowHist(false)
                setWasPending(false); sessionStorage.removeItem(SK_PENDING)
              }}
              className={cn('w-full text-left px-3 py-2 text-[12px] border-b border-[#0f172a] font-mono truncate transition-colors',
                file === f.name ? 'text-[#10b981] bg-[#0f172a]' : 'text-[#64748b] hover:text-[#94a3b8] hover:bg-[#0a1020]'
              )}>
              {f.name}
            </button>
          ))}
        </div>
      )}

      {isGenerating && (
        <div className="flex-shrink-0 border-b border-[#1e2d40] px-3 py-2.5 space-y-1.5 bg-[#060b14]">
          {logs.map((l, i) => (
            <div key={i} className="text-[12px] text-[#10b981] font-mono flex items-center gap-2">
              <ChevronRight className="w-3.5 h-3.5 flex-shrink-0" />{l}
            </div>
          ))}
          <div className="flex items-center gap-2 pt-0.5">
            <span className="w-2 h-2 rounded-full bg-[#10b981] animate-pulse" />
            <span className="text-[11px] text-[#64748b]">AI 분석 중… (~1-2분)</span>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {!content && !isGenerating && !wasPending && (
          <div className="flex flex-col items-center justify-center h-full gap-3 p-5 text-center">
            <FileText className="w-12 h-12 text-[#10b981]/20" />
            <p className="text-sm text-[#64748b] leading-relaxed">GENERATE를 눌러<br/>AI 데일리 브리프를 생성하세요</p>
          </div>
        )}
        {content && !isGenerating && (
          <div ref={contentRef} className="p-4 brief-md">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
const BOT_TABS   = ['Correlation', 'Earnings', 'Macro']
const RIGHT_TABS = ['Brief', 'AI Feed', 'Signals', 'News']

export default function AlphaTerminal() {
  const qc = useQueryClient()
  const [botTab,    setBotTab]    = useState(0)
  const [rightTab,  setRightTab]  = useState(0)
  const [rightOpen, setRightOpen] = useState(true)

  const metricsQ  = useQuery({ queryKey: ['portfolio-metrics'], queryFn: getPortfolioMetrics,   refetchInterval: 30_000 })
  const curveQ    = useQuery({ queryKey: ['equity-curve'],      queryFn: getEquityCurve,         staleTime: 60_000 })
  const holdQ     = useQuery({ queryKey: ['holdings-detail'],   queryFn: getHoldingsDetail,      refetchInterval: 30_000 })
  const rawHoldQ  = useQuery({ queryKey: ['holdings-raw'],      queryFn: getHoldings })
  const sectorQ   = useQuery({ queryKey: ['sector-weights'],    queryFn: getSectorWeights,       staleTime: 60_000 })
  const snapQ     = useQuery({ queryKey: ['market-snapshot'],   queryFn: getMarketSnapshot,      refetchInterval: 30_000 })
  const macroQ    = useQuery({ queryKey: ['macro-data'],        queryFn: getMacroData,           staleTime: 300_000 })
  const doomQ     = useQuery({ queryKey: ['signals-doom'],      queryFn: getSignalsDoomRadar,    staleTime: 60_000 })
  const feedbackQ = useQuery({ queryKey: ['analyst-feedback'],  queryFn: getAnalystFeedback,     staleTime: 300_000 })
  const corrQ     = useQuery({ queryKey: ['correlation'],       queryFn: () => getCorrelation(), staleTime: 300_000 })
  const scanQ     = useQuery({ queryKey: ['scan'],              queryFn: getCachedScan,           retry: false })
  const scanMut   = useMutation({ mutationFn: () => runSignalScan(10), onSuccess: () => qc.invalidateQueries({ queryKey: ['scan'] }) })

  const holdTickers = Object.keys(rawHoldQ.data || {}).filter(t => t !== 'CASH').join(',')
  const newsQ = useQuery({
    queryKey: ['market-news', holdTickers],
    queryFn:  () => getMarketNews(holdTickers.split(',').filter(Boolean)),
    enabled: !!holdTickers,
    staleTime: 120_000,
  })
  const earningsQ = useQuery({
    queryKey: ['earnings', holdTickers],
    queryFn:  () => getEarnings(holdTickers.split(',').filter(Boolean)),
    enabled: !!holdTickers,
    staleTime: 3600_000,
  })

  const m    = metricsQ.data
  const doom = doomQ.data

  return (
    <div className="flex flex-col h-full bg-[#0b0f1a] overflow-hidden">

      {/* ── Metrics bar ── */}
      <div className="flex-shrink-0 bg-[#060b14] border-b border-[#1e2d40]">
        {m && (
          <div className="flex items-stretch overflow-x-auto">
            <Pill label="PORTFOLIO"  value={`$${fn(fv(m.total_equity) / 1000, 1)}K`} />
            <Pill label="TODAY"      value={fp(m.today_change_pct)}  color={fv(m.today_change_pct) >= 0 ? '#10b981' : '#ef4444'} />
            <Pill label="1W"         value={fp(m.perf_1w)}           color={fv(m.perf_1w) >= 0 ? '#10b981' : '#ef4444'} />
            <Pill label="1M"         value={fp(m.perf_1m)}           color={fv(m.perf_1m) >= 0 ? '#10b981' : '#ef4444'} />
            <Pill label="TOTAL RTN"  value={fp(m.total_return_pct)}  color={fv(m.total_return_pct) >= 0 ? '#10b981' : '#ef4444'} />
            <Pill label="BETA"       value={fn(m.portfolio_beta)} />
            <Pill label="VIX"        value={fn(m.vix)}               color={fv(m.vix) > 25 ? '#ef4444' : fv(m.vix) > 18 ? '#f59e0b' : '#10b981'} />
            <Pill label="α vs S&P"   value={fp(m.alpha_vs_sp500, 1)} color={fv(m.alpha_vs_sp500) >= 0 ? '#10b981' : '#ef4444'} />
          </div>
        )}
      </div>

      {/* ── Marquee ── */}
      <Marquee snapshot={snapQ.data} />

      {/* ── Doom banner ── */}
      {doom?.is_doom && (
        <div className="flex-shrink-0 bg-[#ef4444]/10 border-b border-[#ef4444]/30 px-4 py-2 text-sm text-[#ef4444] flex items-center gap-2">
          <Shield className="w-4 h-4" />
          <span className="font-bold tracking-wider">DOOM  SEV {doom.severity}/5</span>
          <span className="opacity-80">{doom.comment}</span>
          <span className="ml-auto font-mono text-[12px]">Rate {fp(doom.rate_spread)}p  ·  HY {fn(doom.hy_spread, 0)} bps</span>
        </div>
      )}

      {/* ── Main layout ── */}
      <div className="flex flex-1 min-h-0">

        {/* ═══ LEFT PANEL ═══ */}
        <div className="overflow-y-auto border-r border-[#1e2d40] flex-1 min-w-0">

          {/* A: Equity Curve */}
          <EquityCurve curveQ={curveQ} />

          {/* B: Holdings + Sectors */}
          <div className="flex border-b border-[#1e2d40]" style={{ height: '330px' }}>
            <div className="border-r border-[#1e2d40] overflow-hidden" style={{ width: '55%' }}>
              <HoldingsPanel holdQ={holdQ} />
            </div>
            <div className="overflow-hidden" style={{ width: '45%' }}>
              <SectorsPanel
                sectorData={sectorQ.data || {}}
                rawHoldings={rawHoldQ.data || {}}
              />
            </div>
          </div>

          {/* C: Tabbed bottom */}
          <div style={{ minHeight: '300px' }}>
            <div className="flex bg-[#060b14] border-b border-[#1e2d40] sticky top-0 z-10">
              {BOT_TABS.map((t, i) => (
                <button key={t} onClick={() => setBotTab(i)}
                  className={cn('px-5 py-2.5 text-[11px] font-bold tracking-widest transition-colors uppercase',
                    botTab === i ? 'text-[#3b82f6] border-b-2 border-[#3b82f6]' : 'text-[#64748b] hover:text-[#94a3b8]'
                  )}>
                  {t}
                </button>
              ))}
            </div>
            <div className="p-4">
              {botTab === 0 && (corrQ.data
                ? <CorrelationHeatmap data={corrQ.data} />
                : <span className="text-sm text-[#64748b]">로드 중…</span>
              )}
              {botTab === 1 && earningsQ.data && (
                <table className="w-full">
                  <thead>
                    <tr className="text-[#64748b] border-b border-[#1e2d40]">
                      {['Ticker', '실적발표일', '배당락일', '배당수익률'].map(h => (
                        <th key={h} className="text-left py-2.5 px-3 font-semibold text-[12px]">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {earningsQ.data.map(e => (
                      <tr key={e.ticker} className="border-b border-[#0f172a] hover:bg-[#0a1020]">
                        <td className="py-2.5 px-3 font-mono font-bold text-base text-[#e2e8f0]">{e.ticker}</td>
                        <td className="py-2.5 px-3 text-sm text-[#94a3b8]">{e.earn_date}</td>
                        <td className="py-2.5 px-3 text-sm text-[#94a3b8]">{e.div_date}</td>
                        <td className="py-2.5 px-3 text-sm text-[#10b981] font-mono font-bold">{e.div_yield}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {botTab === 2 && macroQ.data && (
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: 'Fed Rate',      value: `${macroQ.data.fed_rate}%` },
                    { label: 'Unemployment',  value: `${macroQ.data.unemployment}%` },
                    { label: 'CPI (YoY)',     value: `${macroQ.data.cpi}%` },
                    { label: 'GDP Growth',    value: `${macroQ.data.gdp}%` },
                    { label: '10Y-2Y Spread', value: `${fp(macroQ.data.t10y2y)}p`, color: fv(macroQ.data.t10y2y) < 0 ? '#ef4444' : '#10b981' },
                    { label: 'HY Spread',     value: `${fn(macroQ.data.bamlh0a0hym2, 0)} bps`, color: fv(macroQ.data.bamlh0a0hym2) > 500 ? '#ef4444' : '#f59e0b' },
                  ].map(item => (
                    <div key={item.label} className="bg-[#060b14] border border-[#1e2d40] rounded p-3">
                      <div className="text-[11px] text-[#64748b] font-bold tracking-wider uppercase mb-1">{item.label}</div>
                      <div className="text-2xl font-mono font-bold" style={{ color: (item as any).color || '#e2e8f0' }}>{item.value}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ═══ RIGHT PANEL — collapsible ═══ */}
        {rightOpen ? (
          <div className="flex flex-col min-h-0 flex-shrink-0" style={{ width: '30%', minWidth: '260px' }}>
            <div className="flex-shrink-0 bg-[#060b14] border-b border-[#1e2d40] flex items-center">
              <div className="flex flex-1">
                {RIGHT_TABS.map((t, i) => (
                  <button key={t} onClick={() => setRightTab(i)}
                    className={cn('flex-1 py-2.5 text-[10px] font-bold tracking-widest uppercase transition-colors',
                      rightTab === i ? 'text-[#3b82f6] border-b-2 border-[#3b82f6] bg-[#3b82f6]/5' : 'text-[#64748b] hover:text-[#94a3b8]'
                    )}>
                    {t}
                  </button>
                ))}
              </div>
              <button onClick={() => setRightOpen(false)} title="패널 닫기"
                className="flex-shrink-0 px-2.5 py-2.5 text-[#374151] hover:text-[#64748b] hover:bg-[#0f172a] transition-colors border-l border-[#1e2d40]">
                <PanelRightClose className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 min-h-0 overflow-hidden">
              {rightTab === 0 && <DailyBriefPanel />}

              {rightTab === 1 && (
                <div className="h-full flex flex-col overflow-hidden">
                  <div className="flex-shrink-0 flex items-center justify-between px-4 py-2.5 border-b border-[#1e2d40] bg-[#060b14]">
                    <div className="flex items-center gap-2">
                      <MessageSquare className="w-4 h-4 text-[#3b82f6]" />
                      <span className="text-[11px] text-[#64748b] font-bold tracking-widest">AI ANALYST</span>
                    </div>
                    <button
                      onClick={() => {
                        qc.invalidateQueries({ queryKey: ['analyst-feedback'] })
                        feedbackQ.refetch()
                      }}
                      disabled={feedbackQ.isFetching}
                      className="flex items-center gap-1.5 text-[11px] text-[#3b82f6] hover:text-[#60a5fa] disabled:opacity-40 transition-colors">
                      <RefreshCw className={cn('w-3 h-3', feedbackQ.isFetching && 'animate-spin')} />
                      재분석
                    </button>
                  </div>
                  <div className="flex-1 overflow-y-auto p-4">
                    {feedbackQ.isFetching && (
                      <div className="flex items-center gap-2 py-4 justify-center">
                        <span className="w-2 h-2 rounded-full bg-[#3b82f6] animate-pulse" />
                        <span className="text-sm text-[#64748b]">AI 분석 중…</span>
                      </div>
                    )}
                    {feedbackQ.data && !feedbackQ.isFetching && (
                      <p className="text-sm text-[#94a3b8] leading-relaxed whitespace-pre-wrap">{feedbackQ.data.feedback}</p>
                    )}
                  </div>
                </div>
              )}

              {rightTab === 2 && (
                <div className="h-full overflow-y-auto p-3 space-y-3">
                  <button onClick={() => scanMut.mutate()} disabled={scanMut.isPending}
                    className="w-full flex items-center justify-center gap-2 py-2.5 bg-[#3b82f6]/10 border border-[#3b82f6]/25 text-[#3b82f6] text-sm rounded hover:bg-[#3b82f6]/18 disabled:opacity-50 font-bold">
                    <Zap className="w-4 h-4" />
                    {scanMut.isPending ? '스캔 중…' : 'SCAN UNIVERSE'}
                  </button>
                  {scanQ.data && (
                    <>
                      <div className="text-[11px] text-[#10b981] font-bold tracking-widest mt-1">LONG TOP PICKS</div>
                      {(scanQ.data.long_picks || []).slice(0, 5).map(p => (
                        <div key={p.ticker} className="bg-[#060b14] border border-[#1e2d40] rounded p-3">
                          <div className="flex justify-between items-center">
                            <span className="font-mono font-bold text-base text-[#e2e8f0]">{p.ticker}</span>
                            <span className="text-sm text-[#10b981] font-mono font-bold">+{fn(p.upside, 1)}%</span>
                          </div>
                          <div className="text-[12px] text-[#64748b] mt-0.5">{p.method}</div>
                          <div className="text-[12px] text-[#4a5568] mt-0.5 truncate">${p.entry} → ${p.target} | stop ${p.stop}</div>
                        </div>
                      ))}
                      <div className="text-[11px] text-[#ef4444] font-bold tracking-widest mt-1">SHORT TOP PICKS</div>
                      {(scanQ.data.short_picks || []).slice(0, 3).map(p => (
                        <div key={p.ticker} className="bg-[#060b14] border border-[#1e2d40] rounded p-3">
                          <div className="flex justify-between items-center">
                            <span className="font-mono font-bold text-base text-[#e2e8f0]">{p.ticker}</span>
                            <span className="text-sm text-[#ef4444] font-mono font-bold">{fn(p.downside, 1)}%</span>
                          </div>
                          <div className="text-[12px] text-[#64748b] mt-0.5">{p.method}</div>
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}

              {rightTab === 3 && (
                <div className="h-full overflow-y-auto divide-y divide-[#0f172a]">
                  {(newsQ.data || []).map((n, i) => (
                    <a key={i} href={n.url} target="_blank" rel="noreferrer"
                      className="block p-3.5 hover:bg-[#0a1525] transition-colors">
                      <div className="flex items-center gap-2 mb-2">
                        <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded',
                          n.ticker === 'MACRO' ? 'bg-[#9b59b6]/20 text-[#9b59b6]' : 'bg-[#3b82f6]/20 text-[#3b82f6]')}>
                          {n.ticker}
                        </span>
                        <span className="text-[11px] text-[#64748b]">
                          {new Date(n.datetime * 1000).toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric' })}
                        </span>
                      </div>
                      <p className="text-sm text-[#94a3b8] leading-relaxed line-clamp-2">{n.headline}</p>
                    </a>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center bg-[#060b14] border-l border-[#1e2d40] flex-shrink-0 py-3 gap-3"
            style={{ width: '32px' }}>
            <button onClick={() => setRightOpen(true)} title="패널 열기"
              className="text-[#64748b] hover:text-[#3b82f6] transition-colors">
              <PanelRightOpen className="w-4 h-4" />
            </button>
            {RIGHT_TABS.map((t, i) => (
              <button key={t} onClick={() => { setRightTab(i); setRightOpen(true) }} title={t}
                className={cn('text-[9px] font-bold tracking-widest uppercase transition-colors px-0.5',
                  rightTab === i ? 'text-[#3b82f6]' : 'text-[#374151] hover:text-[#64748b]'
                )}
                style={{ writingMode: 'vertical-rl', textOrientation: 'mixed', transform: 'rotate(180deg)' }}>
                {t}
              </button>
            ))}
            <button onClick={() => {
              qc.invalidateQueries({ queryKey: ['analyst-feedback'] })
              qc.invalidateQueries({ queryKey: ['market-snapshot'] })
            }} className="text-[#374151] hover:text-[#64748b] transition-colors mt-auto">
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>

      <style>{`
        @keyframes marquee { 0%{transform:translateX(0)} 100%{transform:translateX(-50%)} }
        .animate-marquee { animation: marquee 60s linear infinite; }

        .brief-md h1,.brief-md h2,.brief-md h3 { font-size:13px; color:#cbd5e1; font-weight:700; margin-top:12px; margin-bottom:4px; }
        .brief-md h1 { font-size:14px; color:#e2e8f0; border-bottom:1px solid #1e2d40; padding-bottom:5px; }
        .brief-md p  { font-size:13px; color:#94a3b8; line-height:1.65; margin-bottom:6px; }
        .brief-md strong { color:#e2e8f0; }
        .brief-md ul,.brief-md ol { font-size:12px; color:#94a3b8; padding-left:16px; margin-bottom:6px; }
        .brief-md li { margin-bottom:3px; }
        .brief-md hr { border-color:#1e2d40; margin:8px 0; }
        .brief-md code { background:#0f172a; color:#10b981; padding:2px 5px; border-radius:3px; font-size:12px; }
        .brief-md blockquote { border-left:2px solid #1d4ed8; padding-left:10px; color:#64748b; margin:6px 0; }
        .brief-md table { font-size:12px; width:100%; }
        .brief-md th { color:#64748b; border-bottom:1px solid #1e2d40; padding:4px 6px; text-align:left; font-weight:600; }
        .brief-md td { color:#94a3b8; padding:4px 6px; border-bottom:1px solid #0f172a; }
      `}</style>
    </div>
  )
}
