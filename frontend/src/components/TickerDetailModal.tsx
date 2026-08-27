/**
 * TickerDetailModal
 * 종목 검색 후 표시되는 전체화면 상세 분석 모달
 * 차트: 캔들스틱 + 이평선(MA) + 볼린저밴드(BB) + 거래량 + 스토케스틱
 * 우측 상단: Quant Scoreboard / Optimizer / Panic (placeholder)
 * 우측 하단: VaR 히스토그램 (CVaR 제외)
 * 하단: 펀드 정보 / 성과 / 리스크
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import {
  ComposedChart, BarChart, LineChart,
  Bar, Line, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Cell, Customized,
} from 'recharts'
import { X, Search, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { getTickerDetail, searchTickers } from '@/api'
import type { TickerDetail, OHLCVPoint } from '@/types'
import { useTheme } from '@/lib/ThemeContext'

// ── 색상 팔레트 ──────────────────────────────────────────────────────────────
/**
 * 이 파일은 색을 전부 인라인 style 로 넣는다. 인라인 style 은 CSS 클래스
 * 재정의가 닿지 않으므로, light-theme.css 로는 라이트 모드를 만들 수 없다.
 * 그래서 팔레트 자체를 테마별로 나눈다.
 *
 * 라이트 값은 light-theme.css 가 같은 다크 색에 매기는 값과 일치시켰다.
 * 어긋나면 이 모달만 사이트의 나머지와 다른 톤으로 보인다.
 */
const DARK = {
  up:     '#10b981',
  down:   '#ef4444',
  flat:   '#94a3b8',
  warn:   '#f59e0b',
  accent: '#3b82f6',
  ma20:   '#f59e0b',
  ma50:   '#3b82f6',
  ma200:  '#a855f7',
  bbUpper:'#64748b',
  bbLower:'#64748b',
  bbFill: 'rgba(100,116,139,0.08)',
  stochK: '#f59e0b',
  stochD: '#3b82f6',
  vol:    '#1e3a5f',
  volUp:  '#10b981',
  volDn:  '#ef4444',
  var:    '#3b82f6',
  varFill:'rgba(239,68,68,0.25)',
  grid:   '#1e2d40',
  bg:     '#060b14',
  panel:  '#0b0f1a',
  border: '#1e2d40',
  text:   '#e2e8f0',
  muted:  '#94a3b8',
  dim:    '#64748b',
  inputBg:'#0b1220',
  track:  '#1e2d40',
  hover:  '#1e2d40',
  chipBg: '#1e3a5f',
  chipText:'#93c5fd',
  btn:    '#1d4ed8',
  backdrop:'rgba(0,0,0,0.85)',
}

// 흰 배경에서는 형광에 가까운 색이 글자로 읽히지 않는다. 상승/하락/경고는
// 한 톤 어둡게 잡아 큰 면적(차트 채움)과 작은 글자 모두에서 버티게 했다.
const LIGHT: typeof DARK = {
  up:     '#059669',
  down:   '#dc2626',
  flat:   '#4b5563',
  warn:   '#d97706',
  accent: '#2563eb',
  ma20:   '#d97706',
  ma50:   '#2563eb',
  ma200:  '#9333ea',
  bbUpper:'#94a3b8',
  bbLower:'#94a3b8',
  bbFill: 'rgba(100,116,139,0.10)',
  stochK: '#d97706',
  stochD: '#2563eb',
  vol:    '#c7d7ea',
  volUp:  '#059669',
  volDn:  '#dc2626',
  var:    '#2563eb',
  varFill:'rgba(220,38,38,0.22)',
  grid:   '#e3e9f2',
  bg:     '#ffffff',
  panel:  '#f6f8fb',
  border: '#dfe5ee',
  text:   '#111827',
  muted:  '#4b5563',
  dim:    '#5b6a80',
  inputBg:'#f1f4f9',
  track:  '#dfe5ee',
  hover:  '#e6ebf3',
  chipBg: '#dbeafe',
  chipText:'#1d4ed8',
  btn:    '#1d4ed8',
  backdrop:'rgba(15,23,42,0.45)',
}

/** 좁은 화면인지. 이 파일은 색과 크기를 전부 인라인 style 로 넣기 때문에
 *  CSS 미디어 쿼리가 닿지 않는다 — 분기를 JS 에서 해야 한다.
 *  기준값 768px 은 Tailwind 의 md 와 같아서 나머지 화면과 어긋나지 않는다. */
function useIsMobile(): boolean {
  const [m, setM] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    const on = () => setM(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return m
}

type Palette = typeof DARK

/** 현재 테마의 팔레트. 컴포넌트 안에서만 쓸 수 있다. */
function usePalette(): Palette {
  const { theme } = useTheme()
  return theme === 'light' ? LIGHT : DARK
}

const PERIODS = ['1m', '3m', '6m', '1y', '2y', '5y'] as const
type Period = typeof PERIODS[number]

// ── 숫자 포맷 헬퍼 ────────────────────────────────────────────────────────────
const fp = (v: number | null | undefined, d = 2) =>
  v == null ? 'N/A' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`
const fn = (v: number | null | undefined, d = 2) =>
  v == null ? 'N/A' : v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
const fvol = (v: number) => {
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`
  return `${v}`
}
const perfColor = (v: number | null | undefined, C: Palette) =>
  v == null ? C.muted : v >= 0 ? C.up : C.down

// ── 캔들스틱 SVG 레이어 (offset 기반 직접 픽셀 계산 — recharts 내부 scale 의존 없음) ──
function makeCandleRenderer(visData: OHLCVPoint[], priceDomain: [number, number], C: Palette) {
  return function CandlestickLayer({ offset }: any) {
    if (!offset || !visData?.length) return null
    const { left, top, width, height } = offset
    const [yMin, yMax] = priceDomain
    const N = visData.length
    const bw = Math.max(2, (width / N) * 0.7)

    const yPx = (v: number) =>
      Number.isFinite(v) ? top + (yMax - v) / (yMax - yMin) * height : top

    return (
      <g>
        {visData.map((d, i) => {
          const cx = left + (i + 0.5) * (width / N)
          const isUp = d.close >= d.open
          const color = isUp ? C.up : C.down
          const yH = yPx(d.high)
          const yL = yPx(d.low)
          const yO = yPx(d.open)
          const yC = yPx(d.close)
          const bodyTop = Math.min(yO, yC)
          const bodyH   = Math.max(1, Math.abs(yC - yO))
          return (
            <g key={i}>
              <line x1={cx} y1={yH} x2={cx} y2={yL} stroke={color} strokeWidth={1} />
              <rect x={cx - bw / 2} y={bodyTop} width={bw} height={bodyH}
                fill={color} stroke={color} strokeWidth={0.5} opacity={0.9} />
            </g>
          )
        })}
      </g>
    )
  }
}

// ── Quant 게이지 (반원 SVG) ───────────────────────────────────────────────────
const QuantGauge = ({ score }: { score: number }) => {
  const C = usePalette()
  const r = 52, sw = 12
  const cx = 70, cy = 70
  const startAngle = Math.PI
  const endAngle   = 0
  const totalArc   = Math.PI

  const toXY = (angle: number) => ({
    x: cx + r * Math.cos(Math.PI - angle),
    y: cy - r * Math.sin(Math.PI - angle),
  })

  const segments = [
    { from: 0,   to: 0.33, color: C.down },
    { from: 0.33,to: 0.67, color: C.warn },
    { from: 0.67,to: 1.0,  color: C.up },
  ]

  const arcPath = (fromPct: number, toPct: number) => {
    const a1 = startAngle - fromPct * totalArc
    const a2 = startAngle - toPct   * totalArc
    const p1 = toXY(fromPct * totalArc)
    const p2 = toXY(toPct   * totalArc)
    return `M ${p1.x} ${p1.y} A ${r} ${r} 0 0 1 ${p2.x} ${p2.y}`
  }

  const needlePct = score / 100
  const needleAngle = Math.PI - needlePct * Math.PI
  const nx = cx + (r - 10) * Math.cos(needleAngle)
  const ny = cy - (r - 10) * Math.sin(needleAngle)
  const needleColor = score >= 67 ? C.up : score >= 33 ? C.warn : C.down

  return (
    <svg width={140} height={80} viewBox="0 0 140 80">
      {/* Background track */}
      <path d={arcPath(0, 1)} fill="none" stroke={C.track} strokeWidth={sw} strokeLinecap="round" />
      {/* Colored segments */}
      {segments.map((s, i) => (
        <path key={i} d={arcPath(s.from, s.to)} fill="none"
          stroke={s.color} strokeWidth={sw - 2} strokeLinecap="butt" opacity={0.6} />
      ))}
      {/* Needle */}
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={needleColor} strokeWidth={2.5} strokeLinecap="round" />
      <circle cx={cx} cy={cy} r={4} fill={needleColor} />
      {/* Score text */}
      <text x={cx} y={cy - 18} textAnchor="middle" fill={needleColor}
        fontSize={22} fontWeight="bold" fontFamily="monospace">{score}</text>
    </svg>
  )
}

// ── Panic 점수 바 ─────────────────────────────────────────────────────────────
const PanicBar = ({ score }: { score: number }) => {
  const C = usePalette()
  const color = score <= 25 ? C.down : score <= 50 ? C.warn : C.up
  return (
    <div>
      <div className="flex justify-between mb-1">
        <span style={{ fontSize: 10, color: C.muted }}>0</span>
        <span style={{ fontSize: 10, color: C.muted }}>50</span>
        <span style={{ fontSize: 10, color: C.muted }}>100</span>
      </div>
      <div style={{ height: 8, background: C.track, borderRadius: 4, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${score}%`, background: color, borderRadius: 4, transition: 'width 0.5s' }} />
      </div>
    </div>
  )
}

// ── VaR 히스토그램 ─────────────────────────────────────────────────────────────
const VarChart = ({ dist, var95 }: { dist: { x: number; count: number }[]; var95: number | null }) => {
  const C = usePalette()
  if (!dist.length) return <div style={{ color: C.muted, fontSize: 11 }}>데이터 없음</div>

  // 백엔드가 이미 퍼센트 단위(-3.78 = -3.78%)로 내려준다.
  // 여기서 또 100을 곱하면 축이 -378% 로 표시된다.
  const data = dist.map(d => ({
    ...d,
    fill: var95 != null && d.x <= var95 ? C.down : C.vol,
    displayX: d.x.toFixed(1),
  }))

  // 라벨은 부모가 'Warning: 95% VaR = …' 로 이미 보여준다. 여기서 또 그리면
  // 같은 값이 두 번 나올 뿐 아니라, 높이 100px 짜리 상자 안에 차트(100%)와
  // 라벨을 함께 넣은 탓에 라벨이 상자 밖으로 밀려 아래 문구와 겹쳤다.
  return (
    <div style={{ height: 100 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 6, left: 6, bottom: 0 }}>
          <XAxis dataKey="displayX" tick={{ fill: C.muted, fontSize: 8 }} interval={9}
            tickFormatter={v => `${v}%`} />
          <YAxis hide />
          {var95 != null && (
            <ReferenceLine x={var95.toFixed(1)} stroke={C.down} strokeDasharray="3 2" />
          )}
          <Bar dataKey="count" isAnimationActive={false}>
            {data.map((d, i) => <Cell key={i} fill={d.fill} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── 커스텀 툴팁 ───────────────────────────────────────────────────────────────
const CandleTooltip = ({ active, payload, label }: any) => {
  const C = usePalette()
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload as OHLCVPoint
  if (!d) return null
  const isUp = d.close >= d.open

  return (
    <div style={{
      background: C.inputBg, border: `1px solid ${C.border}`, borderRadius: 6,
      padding: '8px 12px', fontSize: 11, color: C.text, minWidth: 160,
    }}>
      <div style={{ color: C.muted, marginBottom: 4 }}>{label}</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 12px' }}>
        {[
          ['O', d.open], ['H', d.high], ['L', d.low],
          ['C', d.close],
        ].map(([k, v]) => (
          <div key={k as string} style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: C.muted }}>{k}</span>
            <span style={{ color: isUp ? C.up : C.down, fontFamily: 'monospace' }}>
              ${(v as number).toFixed(2)}
            </span>
          </div>
        ))}
      </div>
      <div style={{ borderTop: `1px solid ${C.border}`, marginTop: 4, paddingTop: 4, display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ color: C.muted }}>Vol</span>
        <span style={{ fontFamily: 'monospace' }}>{fvol(d.volume)}</span>
      </div>
      {d.ma20 && <div style={{ color: C.ma20, fontSize: 10 }}>MA20: ${d.ma20.toFixed(2)}</div>}
      {d.ma50 && <div style={{ color: C.ma50, fontSize: 10 }}>MA50: ${d.ma50.toFixed(2)}</div>}
    </div>
  )
}

// ── 메인 모달 ─────────────────────────────────────────────────────────────────
interface Props {
  initialTicker?: string
  onClose: () => void
}

export default function TickerDetailModal({ initialTicker, onClose }: Props) {
  const C = usePalette()
  const isMobile = useIsMobile()
  const [ticker,   setTicker]   = useState(initialTicker || '')
  const [query,    setQuery]    = useState(initialTicker || '')
  const [period,   setPeriod]   = useState<Period>('1y')
  const [showMA,   setShowMA]   = useState(true)
  const [showBB,   setShowBB]   = useState(true)
  const [data,     setData]     = useState<TickerDetail | null>(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [suggests, setSuggests] = useState<{ ticker: string; name: string }[]>([])
  const [showSug,  setShowSug]  = useState(false)

  // ── zoom/pan state ─────────────────────────────────────────────────────
  const [viewStart, setViewStart] = useState(0)
  const [viewEnd,   setViewEnd]   = useState(0)
  const chartRef  = useRef<HTMLDivElement>(null)
  const panRef    = useRef<{ x: number; start: number; end: number } | null>(null)
  const isPanning = useRef(false)
  const sugTimer  = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── 데이터 로드 ──────────────────────────────────────────────────────────
  const load = useCallback(async (sym: string, p: Period) => {
    if (!sym.trim()) return
    setLoading(true); setError(null)
    try {
      const d = await getTickerDetail(sym, p)
      setData(d)
      setViewStart(0); setViewEnd(d.ohlcv.length)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '데이터 로드 실패')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { if (ticker) load(ticker, period) }, [ticker, period, load])

  // ── 줌 · 팬 ────────────────────────────────────────────────────────────
  // 예전에는 mousedown/mousemove 만 붙어 있어 휴대폰에서는 차트가 아예
  // 움직이지 않았다. 포인터 이벤트는 마우스와 터치를 같은 방식으로 주므로
  // 하나로 둘 다 처리한다. 두 손가락 간격 변화로 확대/축소도 받는다.
  const zoomBy = useCallback((factor: number) => {
    if (!data) return
    const total = data.ohlcv.length
    const center = (viewStart + viewEnd) / 2
    const half   = ((viewEnd - viewStart) / 2) * factor
    const ns = Math.max(0,     Math.floor(center - half))
    const ne = Math.min(total, Math.ceil(center  + half))
    if (ne - ns >= 10) { setViewStart(ns); setViewEnd(ne) }
  }, [data, viewStart, viewEnd])

  const resetZoom = useCallback(() => {
    if (!data) return
    setViewStart(0); setViewEnd(data.ohlcv.length)
  }, [data])

  useEffect(() => {
    const el = chartRef.current
    if (!el || !data) return
    const total = data.ohlcv.length

    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      zoomBy(e.deltaY > 0 ? 1.15 : 0.85)
    }

    // 화면에 닿아 있는 포인터들. 두 개가 되면 핀치로 본다.
    const pts = new Map<number, { x: number; y: number }>()
    let pinchStart: { dist: number; start: number; end: number } | null = null

    const spread = () => {
      const v = [...pts.values()]
      return Math.hypot(v[0].x - v[1].x, v[0].y - v[1].y)
    }

    const onDown = (e: PointerEvent) => {
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY })
      if (pts.size === 2) {
        pinchStart = { dist: spread(), start: viewStart, end: viewEnd }
        isPanning.current = false
        panRef.current = null
      } else if (pts.size === 1) {
        isPanning.current = true
        panRef.current = { x: e.clientX, start: viewStart, end: viewEnd }
        el.style.cursor = 'grabbing'
      }
    }

    const onMove = (e: PointerEvent) => {
      if (!pts.has(e.pointerId)) return
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY })

      if (pts.size === 2 && pinchStart) {
        const d = spread()
        if (d > 0 && pinchStart.dist > 0) {
          const range  = pinchStart.end - pinchStart.start
          const center = (pinchStart.start + pinchStart.end) / 2
          const half   = (range / 2) * (pinchStart.dist / d)
          const ns = Math.max(0,     Math.floor(center - half))
          const ne = Math.min(total, Math.ceil(center  + half))
          if (ne - ns >= 10) { setViewStart(ns); setViewEnd(ne) }
        }
        e.preventDefault()
        return
      }

      if (!isPanning.current || !panRef.current) return
      const range = panRef.current.end - panRef.current.start
      const dx = panRef.current.x - e.clientX
      // 손가락이 살짝 흔들린 정도로는 반응하지 않게 한다 — 세로 스크롤과 겹친다.
      if (Math.abs(dx) < 4) return
      const shift = Math.round(dx / el.offsetWidth * range)
      const ns = Math.max(0, Math.min(total - range, panRef.current.start + shift))
      setViewStart(ns)
      setViewEnd(ns + range)
    }

    const onUp = (e: PointerEvent) => {
      pts.delete(e.pointerId)
      if (pts.size < 2) pinchStart = null
      if (pts.size === 0) {
        isPanning.current = false
        panRef.current = null
        el.style.cursor = 'default'
      }
    }

    el.addEventListener('wheel', onWheel, { passive: false })
    el.addEventListener('pointerdown', onDown)
    window.addEventListener('pointermove', onMove, { passive: false })
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [data, viewStart, viewEnd, zoomBy])

  // ── 자동완성 ─────────────────────────────────────────────────────────────
  const onQueryChange = (v: string) => {
    setQuery(v)
    if (sugTimer.current) clearTimeout(sugTimer.current)
    if (!v.trim()) { setSuggests([]); return }
    sugTimer.current = setTimeout(async () => {
      try { setSuggests(await searchTickers(v)) } catch { setSuggests([]) }
    }, 250)
  }

  const selectSuggest = (s: { ticker: string; name: string }) => {
    setQuery(s.ticker); setTicker(s.ticker); setSuggests([]); setShowSug(false)
  }

  const onSearch = () => {
    const sym = query.trim().toUpperCase()
    if (!sym) return
    setTicker(sym); setSuggests([]); setShowSug(false)
  }

  // ── 가시 데이터 슬라이스 ─────────────────────────────────────────────────
  const visData = useMemo(() =>
    data ? data.ohlcv.slice(viewStart, viewEnd) : [],
    [data, viewStart, viewEnd]
  )

  // ── Y축 도메인 계산 ──────────────────────────────────────────────────────
  const priceDomain = useMemo<[number, number]>(() => {
    if (!visData.length) return [0, 100]
    let lo = Infinity, hi = -Infinity
    visData.forEach(d => {
      lo = Math.min(lo, d.low, d.bb_lower ?? d.low)
      hi = Math.max(hi, d.high, d.bb_upper ?? d.high)
      if (showMA) {
        if (d.ma20)  { lo = Math.min(lo, d.ma20);  hi = Math.max(hi, d.ma20) }
        if (d.ma50)  { lo = Math.min(lo, d.ma50);  hi = Math.max(hi, d.ma50) }
        if (d.ma200) { lo = Math.min(lo, d.ma200); hi = Math.max(hi, d.ma200) }
      }
    })
    const pad = (hi - lo) * 0.05
    return [Math.max(0, lo - pad), hi + pad]
  }, [visData, showMA])

  // 캔들스틱 레이어: priceDomain이 바뀔 때마다 재생성 (클로저로 최신 데이터 캡처)
  const CandleLayer = useMemo(() => makeCandleRenderer(visData, priceDomain, C), [visData, priceDomain, C])

  // ── 날짜 라벨 (밀집도에 따라 자동 조절) ─────────────────────────────────
  const xInterval = Math.max(1, Math.floor(visData.length / 6))

  // ── 공통 축 스타일 ────────────────────────────────────────────────────────
  const axisStyle = { fill: C.muted, fontSize: 10 }

  // ── 렌더 ─────────────────────────────────────────────────────────────────
  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        background: C.backdrop,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div style={{
        // 좁은 화면에서 95vw×92vh 는 내용을 우겨넣기만 하고 읽히지 않는다.
        // 전체화면으로 열고 본문을 세로로 흘려 스크롤하게 한다.
        width: isMobile ? '100vw' : '95vw',
        height: isMobile ? '100dvh' : '92vh',
        background: C.bg,
        border: isMobile ? 'none' : `1px solid ${C.border}`,
        borderRadius: isMobile ? 0 : 10,
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>

        {/* ── 헤더 바 ────────────────────────────────────────────────────── */}
        <div style={{
          flexShrink: 0, padding: '8px 14px',
          borderBottom: `1px solid ${C.border}`,
          background: C.panel,
          display: 'flex', alignItems: 'center', gap: 10,
          // 좁은 화면에서는 검색창+기간 버튼이 한 줄에 안 들어간다.
          // 옆으로 미는 대신 줄바꿈한다 — 모달 안에서는 세로 스크롤 하나로 통일한다.
          flexWrap: isMobile ? 'wrap' : 'nowrap',
          rowGap: isMobile ? 6 : undefined,
          overflowX: 'visible',
        }}>
          {/* 검색창 */}
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 0 }}>
            <input
              value={query}
              onChange={e => { onQueryChange(e.target.value); setShowSug(true) }}
              onKeyDown={e => e.key === 'Enter' && onSearch()}
              onFocus={() => setShowSug(true)}
              onBlur={() => setTimeout(() => setShowSug(false), 150)}
              placeholder="티커 검색 (예: AAPL)"
              style={{
                background: C.inputBg, border: `1px solid ${C.border}`,
                borderRadius: '6px 0 0 6px', color: C.text, padding: '5px 10px',
                fontSize: 12, width: 160, outline: 'none',
              }}
            />
            <button onClick={onSearch}
              style={{
                background: C.btn, border: 'none', borderRadius: '0 6px 6px 0',
                color: '#fff', padding: '5px 10px', cursor: 'pointer', display: 'flex',
              }}>
              <Search size={13} />
            </button>
            {showSug && suggests.length > 0 && (
              <div style={{
                position: 'absolute', top: '100%', left: 0, zIndex: 100,
                background: C.inputBg, border: `1px solid ${C.border}`, borderRadius: 6,
                minWidth: 220, overflow: 'hidden', marginTop: 2,
              }}>
                {suggests.map(s => (
                  <div key={s.ticker} onClick={() => selectSuggest(s)}
                    style={{
                      padding: '7px 12px', cursor: 'pointer', fontSize: 12,
                      display: 'flex', gap: 8, alignItems: 'center',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = C.hover)}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <span style={{ color: C.text, fontFamily: 'monospace', fontWeight: 700 }}>{s.ticker}</span>
                    <span style={{ color: C.muted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 기간 버튼 */}
          <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
            {PERIODS.map(p => (
              <button key={p} onClick={() => setPeriod(p)}
                style={{
                  padding: '3px 9px', borderRadius: 4, fontSize: 11,
                  border: `1px solid ${period === p ? C.accent : C.border}`,
                  background: period === p ? C.chipBg : 'transparent',
                  color: period === p ? C.chipText : C.muted,
                  cursor: 'pointer', fontWeight: period === p ? 700 : 400,
                }}>
                {p.toUpperCase()}
              </button>
            ))}
          </div>

          {/* MA/BB 토글 */}
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={() => setShowMA(v => !v)}
              style={{
                padding: '3px 10px', borderRadius: 4, fontSize: 11,
                border: `1px solid ${showMA ? C.ma20 : C.border}`,
                background: showMA ? 'rgba(245,158,11,0.15)' : 'transparent',
                color: showMA ? C.ma20 : C.muted, cursor: 'pointer',
              }}>MA</button>
            <button onClick={() => setShowBB(v => !v)}
              style={{
                padding: '3px 10px', borderRadius: 4, fontSize: 11,
                border: `1px solid ${showBB ? C.dim : C.border}`,
                background: showBB ? 'rgba(100,116,139,0.15)' : 'transparent',
                color: C.muted, cursor: 'pointer',
              }}>BB</button>
          </div>

          {/* 타이틀 */}
          {data && (
            <div style={{ flex: 1, display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <span style={{ color: C.text, fontSize: 15, fontWeight: 700, fontFamily: 'monospace' }}>
                {data.ticker}
              </span>
              <span style={{ color: C.muted, fontSize: 12 }}>{data.info.name}</span>
              <span style={{ color: perfColor(data.risk.change_pct, C), fontSize: 13, fontWeight: 700, fontFamily: 'monospace' }}>
                ${fn(data.risk.current_price)}
              </span>
              <span style={{ color: perfColor(data.risk.change_pct, C), fontSize: 12 }}>
                {fp(data.risk.change_pct)}
              </span>
            </div>
          )}

          {/* 닫기 */}
          <button onClick={onClose}
            style={{ background: 'none', border: 'none', color: C.muted, cursor: 'pointer', padding: 4 }}>
            <X size={18} />
          </button>
        </div>

        {/* ── 로딩/에러 ────────────────────────────────────────────────────── */}
        {loading && (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.muted }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ width: 36, height: 36, border: `3px solid ${C.track}`, borderTopColor: C.accent, borderRadius: '50%', animation: 'spin 0.8s linear infinite', margin: '0 auto 12px' }} />
              데이터 로드 중…
            </div>
          </div>
        )}
        {!loading && error && (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.down }}>
            {error}
          </div>
        )}
        {!loading && !error && !data && (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.muted, flexDirection: 'column', gap: 12 }}>
            <Search size={36} opacity={0.3} />
            <div style={{ fontSize: 13 }}>티커를 검색하세요</div>
          </div>
        )}

        {/* ── 메인 콘텐츠 ─────────────────────────────────────────────────── */}
        {!loading && !error && data && (
          <div style={{
            flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column',
            // 모바일에서는 내용이 화면보다 길다 — 잘라내지 말고 세로로 스크롤시킨다.
            // 가로 스크롤은 만들지 않는다: 한 화면에서 두 방향으로 밀어야 하면
            // 어느 쪽으로 움직여야 할지 헷갈리고 오조작이 잦다.
            overflowY: isMobile ? 'auto' : 'visible',
            overflowX: isMobile ? 'hidden' : 'visible',
            WebkitOverflowScrolling: 'touch',
          }}>

            {/* 차트 + 우측 패널 */}
            <div style={{
              flex: isMobile ? '0 0 auto' : 1,
              minHeight: isMobile ? undefined : 0,
              display: 'flex', flexDirection: isMobile ? 'column' : 'row',
            }}>

              {/* ─── 차트 컬럼 (70%) ──────────────────────────────────────── */}
              <div ref={chartRef} style={{
                flex: isMobile ? '0 0 auto' : '0 0 70%',
                width: isMobile ? '100%' : undefined,
                display: 'flex', flexDirection: 'column',
                borderRight: isMobile ? 'none' : `1px solid ${C.border}`,
                borderBottom: isMobile ? `1px solid ${C.border}` : 'none',
                // 세로 스크롤은 브라우저에 맡기고 가로 드래그만 우리가 받는다.
                touchAction: 'pan-y',
              }}>

                {/* 메인 캔들 차트 */}
                <div style={{ flex: isMobile ? '0 0 auto' : '0 0 55%', height: isMobile ? 300 : undefined, borderBottom: `1px solid ${C.border}` }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart
                      data={visData}
                      margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke={C.grid} opacity={0.5} />
                      <XAxis dataKey="date" tick={axisStyle} interval={xInterval}
                        tickFormatter={v => v?.slice(5)} />
                      <YAxis domain={priceDomain} tick={axisStyle} width={60}
                        tickFormatter={v => `$${v.toFixed(0)}`} />
                      <Tooltip content={<CandleTooltip />} />

                      {/* 볼린저밴드 */}
                      {showBB && (
                        <>
                          <Area dataKey="bb_upper" fill={C.bbFill} stroke={C.bbUpper}
                            strokeWidth={1} strokeDasharray="4 2"
                            dot={false} activeDot={false} legendType="none" isAnimationActive={false} />
                          <Line dataKey="bb_mid" stroke={C.bbUpper} strokeWidth={1}
                            dot={false} activeDot={false} strokeDasharray="2 2" isAnimationActive={false} />
                          <Area dataKey="bb_lower" fill="transparent" stroke={C.bbUpper}
                            strokeWidth={1} strokeDasharray="4 2"
                            dot={false} activeDot={false} legendType="none" isAnimationActive={false} />
                        </>
                      )}

                      {/* 이평선 */}
                      {showMA && (
                        <>
                          <Line dataKey="ma20"  stroke={C.ma20}  strokeWidth={1.5}
                            dot={false} activeDot={false} isAnimationActive={false} connectNulls />
                          <Line dataKey="ma50"  stroke={C.ma50}  strokeWidth={1.5}
                            dot={false} activeDot={false} isAnimationActive={false} connectNulls />
                          <Line dataKey="ma200" stroke={C.ma200} strokeWidth={1.5}
                            dot={false} activeDot={false} isAnimationActive={false} connectNulls />
                        </>
                      )}

                      {/* dummy bar to set X scale; Customized layer draws real candles */}
                      <Bar dataKey="close" fill="transparent" isAnimationActive={false} />
                      <Customized component={CandleLayer} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>

                {/* MA 범례 */}
                {showMA && (
                  <div style={{
                    padding: '2px 12px', display: 'flex', gap: 14,
                    // MA 범례 넷 + 확대 버튼 셋이 390px 에 한 줄로 안 들어간다.
                    // 줄바꿈을 허용하지 않으면 버튼이 화면 밖으로 밀려 눌리지 않는다.
                    flexWrap: 'wrap', rowGap: 6, alignItems: 'center',
                    borderBottom: `1px solid ${C.border}`,
                  }}>
                    {[['MA20', C.ma20], ['MA50', C.ma50], ['MA200', C.ma200]].map(([l, c]) => (
                      <div key={l} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10 }}>
                        <div style={{ width: 20, height: 2, background: c as string, borderRadius: 1 }} />
                        <span style={{ color: C.muted }}>{l}</span>
                      </div>
                    ))}
                    {showBB && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10 }}>
                        <div style={{ width: 20, height: 2, background: C.bbUpper, borderRadius: 1, borderTop: '1px dashed' }} />
                        <span style={{ color: C.muted }}>BB(20,2)</span>
                      </div>
                    )}
                    {/* 휴대폰에는 스크롤 휠이 없다. 핀치도 되지만 한 손으로는
                        어려우므로 확실히 눌리는 버튼을 함께 둔다. */}
                    {isMobile ? (
                      <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexShrink: 0 }}>
                        {([['−', () => zoomBy(1.3), '축소'],
                           ['+', () => zoomBy(0.7), '확대'],
                           ['⤢', resetZoom, '전체 보기']] as const).map(([label, fn, aria]) => (
                          <button key={aria} onClick={fn} aria-label={aria}
                            style={{
                              minWidth: 34, minHeight: 30, borderRadius: 6,
                              background: C.inputBg, border: `1px solid ${C.border}`,
                              color: C.text, fontSize: 14, lineHeight: 1, cursor: 'pointer',
                            }}>{label}</button>
                        ))}
                      </div>
                    ) : (
                      <div style={{ marginLeft: 'auto', fontSize: 9, color: C.muted }}>
                        스크롤로 확대/축소 · 드래그로 이동
                      </div>
                    )}
                  </div>
                )}

                {/* 거래량 */}
                <div style={{ flex: isMobile ? '0 0 auto' : '0 0 22%', height: isMobile ? 130 : undefined, borderBottom: `1px solid ${C.border}` }}>
                  <div style={{ padding: '2px 6px', fontSize: 10, color: C.muted }}>Volume</div>
                  <ResponsiveContainer width="100%" height="80%">
                    <BarChart data={visData} margin={{ top: 0, right: 12, left: 0, bottom: 0 }}>
                      <XAxis dataKey="date" hide />
                      <YAxis tick={axisStyle} width={60} tickFormatter={fvol} />
                      <Bar dataKey="volume" isAnimationActive={false}>
                        {visData.map((d, i) => (
                          <Cell key={i} fill={d.close >= d.open ? C.volUp : C.volDn} fillOpacity={0.7} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                {/* 스토케스틱 */}
                <div style={{ flex: isMobile ? '0 0 auto' : 1, height: isMobile ? 130 : undefined, minHeight: 0 }}>
                  <div style={{ padding: '2px 6px', fontSize: 10, color: C.muted }}>Stochastic(14,3,3)</div>
                  <ResponsiveContainer width="100%" height="80%">
                    <LineChart data={visData} margin={{ top: 0, right: 12, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={C.grid} opacity={0.3} />
                      <XAxis dataKey="date" tick={axisStyle} interval={xInterval} tickFormatter={v => v?.slice(5)} />
                      <YAxis domain={[0, 100]} tick={axisStyle} width={60} />
                      <ReferenceLine y={80} stroke={C.down} strokeDasharray="3 2" strokeWidth={0.8} />
                      <ReferenceLine y={20} stroke={C.up} strokeDasharray="3 2" strokeWidth={0.8} />
                      <Line dataKey="stoch_k" stroke={C.stochK} strokeWidth={1.5}
                        dot={false} isAnimationActive={false} connectNulls />
                      <Line dataKey="stoch_d" stroke={C.stochD} strokeWidth={1.5}
                        dot={false} isAnimationActive={false} connectNulls strokeDasharray="4 2" />
                      <Tooltip formatter={(v: any) => [typeof v === 'number' ? v.toFixed(1) : v, '']}
                        contentStyle={{ background: C.inputBg, border: `1px solid ${C.border}`, color: C.text, fontSize: 10 }} />
                    </LineChart>
                  </ResponsiveContainer>
                  <div style={{ padding: '0 6px', display: 'flex', gap: 10, fontSize: 9 }}>
                    {[['%K', C.stochK], ['%D', C.stochD]].map(([l, c]) => (
                      <div key={l} style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                        <div style={{ width: 14, height: 2, background: c as string }} />
                        <span style={{ color: C.muted }}>{l}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* ─── 우측 패널 (30%) ──────────────────────────────────────── */}
              <div style={{
                flex: isMobile ? '0 0 auto' : '0 0 30%',
                width: isMobile ? '100%' : undefined,
                display: 'flex', flexDirection: 'column',
                overflow: isMobile ? 'visible' : 'hidden',
              }}>

                {/* 퀀트 스코어보드 */}
                <div style={{ padding: '10px 14px', borderBottom: `1px solid ${C.border}` }}>
                  <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 4 }}>
                    퀀트 스코어보드
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <QuantGauge score={data.quant.score ?? 0} />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 10, color: C.muted }}>퀀트 점수: <span style={{ color: C.up, fontWeight: 700 }}>{data.quant.score ?? '—'}/100</span></div>
                      <div style={{ fontSize: 11, color: C.up, fontWeight: 700, marginTop: 2 }}>{data.quant.score_label}</div>
                      {/* 합성 점수만 보여주면 실제보다 정밀해 보인다 — 팩터별 근거를 함께 표시 */}
                      {data.quant.factors && (
                        <div style={{ display: 'flex', gap: 8, marginTop: 5 }}>
                          {([['모멘텀','momentum'],['추세','trend'],['퀄리티','quality'],['밸류','value']] as const).map(([ko,k]) => {
                            const v = (data.quant.factors as any)?.[k]
                            if (v == null) return null
                            const col = v >= 67 ? C.up : v >= 34 ? C.warn : C.down
                            return (
                              <div key={k} style={{ textAlign: 'center' }}>
                                <div style={{ fontSize: 9, color: C.muted }}>{ko}</div>
                                <div style={{ fontSize: 11, fontWeight: 700, color: col, fontFamily: 'monospace' }}>{v}</div>
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* 시장 국면 + 옵티마이저 */}
                <div style={{ padding: '10px 14px', borderBottom: `1px solid ${C.border}` }}>
                  <div style={{ fontSize: 10, color: C.warn, fontWeight: 700, marginBottom: 6 }}>
                    시장 국면: <span style={{ color: C.text }}>{data.quant.regime}</span>
                    {data.quant.regime_er != null && (
                      <span style={{ color: C.muted, fontWeight: 400, marginLeft: 6 }}>
                        (효율성비율 {data.quant.regime_er.toFixed(2)})
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 6 }}>
                    옵티마이저 인사이트
                  </div>
                  {(() => {
                    const o = data.quant.optimizer
                    const n = (v: number | null | undefined, suffix = '%') =>
                      v == null ? '—' : `${v}${suffix}`
                    const over = o.target_weight != null && o.current_weight != null
                      && o.current_weight > o.target_weight * 1.3 && o.current_weight > 1
                    return (
                      <>
                        <div style={{ fontSize: 10, color: C.muted, marginBottom: 3 }}>
                          목표 최적 비중(HRP): <span style={{ color: C.text }}>{n(o.target_weight)}</span>
                          {' · 현재 '}<span style={{ color: over ? C.down : C.text }}>{n(o.current_weight)}</span>
                          {over && <span style={{ color: C.down, marginLeft: 4 }}>과다 편중</span>}
                        </div>
                        <div style={{ fontSize: 10, color: C.muted, marginBottom: 3 }}>
                          리스크 기여도: <span style={{ color: C.text }}>{n(o.risk_contribution)}</span>
                        </div>
                        <div style={{ fontSize: 10, color: C.muted, marginBottom: 3 }}>
                          포트 상관관계: <span style={{ color: C.text }}>{o.correlation ?? '—'}</span>
                          {o.correlation_label && ` (${o.correlation_label})`}
                        </div>
                        <div style={{ fontSize: 10, color: C.muted, marginBottom: 3 }}>
                          Beta 익스포저: <span style={{ color: C.text }}>{n(o.beta_exposure, 'x')}</span>
                        </div>
                        {o.note && (
                          <div style={{ fontSize: 9, color: C.warn, marginTop: 4 }}>{o.note}</div>
                        )}
                      </>
                    )
                  })()}
                  <div style={{ fontSize: 9, color: C.dim, marginTop: 4, fontStyle: 'italic' }}>
                    (주) 수학적 기반 참고 추천으로, 강제 포지션이 아닙니다.
                  </div>
                </div>

                {/* 패닉 헌팅 점수 */}
                <div style={{ padding: '10px 14px', borderBottom: `1px solid ${C.border}`, background: 'rgba(239,68,68,0.05)', border: `1px solid rgba(239,68,68,0.2)`, margin: '8px', borderRadius: 6 }}>
                  <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 6 }}>
                    패닉 헌팅 점수
                  </div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 8 }}>
                    <span style={{ fontSize: 10, color: C.muted }}>패닉 상태 점수:</span>
                    <span style={{ fontSize: 22, fontWeight: 700, color: C.down, fontFamily: 'monospace' }}>
                      {data.quant.panic_score ?? '—'}
                    </span>
                    <span style={{ fontSize: 11, color: C.muted }}>/100</span>
                  </div>
                  <PanicBar score={data.quant.panic_score ?? 0} />
                  {data.quant.panic_components && (
                    <div style={{ display: 'flex', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
                      {([['RSI','rsi'],['낙폭','drawdown'],['고점대비','vs_52w_high'],['거래량','volume'],['변동성','volatility']] as const).map(([ko,k]) => {
                        const v = (data.quant.panic_components as any)?.[k]
                        if (v == null) return null
                        return (
                          <span key={k} style={{ fontSize: 9, color: C.muted }}>
                            {ko} <span style={{ color: C.text, fontFamily: 'monospace' }}>{v}</span>
                          </span>
                        )
                      })}
                    </div>
                  )}
                  <div style={{ fontSize: 10, color: C.down, marginTop: 6, fontWeight: 600 }}>
                    시그널 상태: {data.quant.panic_status}
                  </div>
                </div>

                {/* VaR */}
                <div style={{ flex: 1, padding: '0 14px 10px', minHeight: 0 }}>
                  <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 4 }}>
                    95% VaR (Historical)
                  </div>
                  <VarChart dist={data.var.return_dist} var95={data.var.var95} />
                  {data.var.var95 != null && (
                    <div style={{ fontSize: 10, color: C.down, marginTop: 4 }}>
                      Warning: 95% VaR = {data.var.var95.toFixed(2)}% daily
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* ─── 하단 정보 바 ──────────────────────────────────────────────── */}
            <div style={{
              flexShrink: 0, display: 'flex',
              // 세 칸을 나란히 두면 390px 에서 한 칸이 120px 남짓이라
              // '산업 Consumer Electronics' 같은 값이 글자마다 접힌다.
              flexDirection: isMobile ? 'column' : 'row',
              borderTop: `1px solid ${C.border}`, background: C.panel,
            }}>

              {/* 펀드 정보 */}
              <div style={{
                flex: 1, padding: '10px 14px',
                borderRight: isMobile ? 'none' : `1px solid ${C.border}`,
                borderBottom: isMobile ? `1px solid ${C.border}` : 'none',
              }}>
                <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>ℹ</span> 펀드 정보
                </div>
                {[
                  ['티커',        data.ticker],
                  ['종목명',      data.info.name],
                  ['섹터',        data.info.sector],
                  ['산업',        data.info.industry],
                  ['시가총액',    data.info.market_cap],
                  ['P/E',         data.info.pe != null ? fn(data.info.pe, 1) : 'N/A'],
                  ['배당수익률',  `${fn(data.info.div_yield, 2)}%`],
                ].map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 3, fontSize: 11 }}>
                    {/* 라벨은 짧다. 줄바꿈을 허용하면 '산업'이 '산/업'으로 쪼개진다. */}
                    <span style={{ color: C.muted, whiteSpace: 'nowrap', flexShrink: 0 }}>{k}</span>
                    <span style={{ color: C.text, textAlign: 'right', minWidth: 0,
                                   fontWeight: k === '티커' ? 700 : 400,
                                   fontFamily: k === '티커' ? 'monospace' : undefined }}>
                      {v}
                    </span>
                  </div>
                ))}
              </div>

              {/* 성과 */}
              <div style={{
                flex: 1, padding: '10px 14px',
                borderRight: isMobile ? 'none' : `1px solid ${C.border}`,
                borderBottom: isMobile ? `1px solid ${C.border}` : 'none',
              }}>
                <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <TrendingUp size={11} /> 성과
                </div>
                {[
                  ['1W',   data.performance['1w']],
                  ['1M',   data.performance['1m']],
                  ['6M',   data.performance['6m']],
                  ['YTD',  data.performance.ytd],
                  ['1Y',   data.performance['1y']],
                  ['5Y',   data.performance['5y']],
                ].map(([k, v]) => (
                  <div key={k as string} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3, fontSize: 11 }}>
                    <span style={{ color: C.muted }}>{k}</span>
                    <span style={{ color: perfColor(v as number | null, C), fontFamily: 'monospace', fontWeight: 600 }}>
                      {fp(v as number | null)}
                    </span>
                  </div>
                ))}
                <div style={{ borderTop: `1px solid ${C.border}`, marginTop: 4, paddingTop: 4 }}>
                  {[
                    ['52W 고가', `$${fn(data.performance.s52w_high)}`],
                    ['52W 저가', `$${fn(data.performance.s52w_low)}`],
                  ].map(([k, v]) => (
                    <div key={k} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2, fontSize: 11 }}>
                      <span style={{ color: C.muted }}>{k}</span>
                      <span style={{ color: C.text }}>{v}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* 리스크/기술적 지표 */}
              <div style={{ flex: 1, padding: '10px 14px' }}>
                <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <TrendingDown size={11} /> 리스크/기술적 지표
                </div>
                {[
                  ['Beta',          fn(data.risk.beta)],
                  ['변동성',        data.risk.volatility != null ? `${fn(data.risk.volatility, 1)}%` : 'N/A'],
                  ['평균 거래량',   fvol(data.risk.avg_volume)],
                  ['RSI(14)',       data.risk.rsi14 != null ? fn(data.risk.rsi14, 1) : 'N/A'],
                  ['현재가',        `$${fn(data.risk.current_price)}`],
                  ['등락률',        null],
                ].map(([k, v]) => (
                  <div key={k as string} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3, fontSize: 11 }}>
                    <span style={{ color: C.muted }}>{k}</span>
                    {k === '등락률' ? (
                      <span style={{ color: perfColor(data.risk.change_pct, C), fontFamily: 'monospace', fontWeight: 600 }}>
                        {fp(data.risk.change_pct)}
                      </span>
                    ) : (
                      <span style={{ color: C.text }}>{v as string}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 스핀 애니메이션 */}
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
