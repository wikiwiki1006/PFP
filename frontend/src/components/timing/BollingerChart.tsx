import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ComposedChart, Line, Area, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceArea, ReferenceDot, ReferenceLine,
} from 'recharts'
import { getTechnicalChart } from '@/api'
import type { TechnicalChartPoint } from '@/types'
import { COLOR_UP, COLOR_DOWN } from './colors'

// ── debounce hook ─────────────────────────────────────────────────────────────
function useDebounced<T>(value: T, ms = 700): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

// ── candlestick bar shape ─────────────────────────────────────────────────────
function CandleShape(props: any) {
  const { x, width, payload, yAxis } = props
  if (!payload || !yAxis?.scale) return <g />
  const { open, high, low } = payload
  const close = payload.price
  if (close == null || open == null) return <g />

  const ys      = yAxis.scale
  const highPx  = ys(high  ?? close)
  const lowPx   = ys(low   ?? close)
  const openPx  = ys(open)
  const closePx = ys(close)

  const isUp    = close >= open
  const color   = isUp ? COLOR_UP : COLOR_DOWN
  const cx      = x + width / 2
  const bodyTop = Math.min(openPx, closePx)
  const bodyH   = Math.max(1, Math.abs(openPx - closePx))
  const bodyW   = Math.max(2, width - 2)

  return (
    <g>
      {/* upper wick */}
      <line x1={cx} y1={highPx} x2={cx} y2={bodyTop} stroke={color} strokeWidth={1} />
      {/* lower wick */}
      <line x1={cx} y1={bodyTop + bodyH} x2={cx} y2={lowPx} stroke={color} strokeWidth={1} />
      {/* body */}
      <rect x={cx - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH}
        fill={color} stroke={color} strokeWidth={0.5} />
    </g>
  )
}

// ── weekly aggregation ────────────────────────────────────────────────────────
function weekMonday(d: Date): string {
  const dow  = d.getDay()
  const diff = dow === 0 ? -6 : 1 - dow
  const m    = new Date(d)
  m.setDate(d.getDate() + diff)
  return m.toISOString().split('T')[0]
}

function aggregateWeekly(data: TechnicalChartPoint[]): TechnicalChartPoint[] {
  if (!data.length) return []
  const weeks = new Map<string, TechnicalChartPoint[]>()
  for (const d of data) {
    const k = weekMonday(new Date(d.date))
    if (!weeks.has(k)) weeks.set(k, [])
    weeks.get(k)!.push(d)
  }
  return Array.from(weeks.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([wk, pts]) => {
      const real = pts.filter(p => p.price != null)
      const last = real[real.length - 1] ?? pts[pts.length - 1]
      const closes = real.map(p => p.price)
      return {
        date:       wk,
        open:       real[0]?.open  ?? real[0]?.price   ?? null,
        high:       real.length ? Math.max(...real.map(p => p.high ?? p.price)) : null,
        low:        real.length ? Math.min(...real.map(p => p.low  ?? p.price)) : null,
        price:      closes[closes.length - 1] ?? null,
        mid:        last?.mid        ?? null,
        upper:      last?.upper      ?? null,
        lower:      last?.lower      ?? null,
        zscore:     last?.zscore     ?? null,
        ma5:        last?.ma5        ?? null,
        ma30:       last?.ma30       ?? null,
        ma60:       last?.ma60       ?? null,
        ma120:      last?.ma120      ?? null,
        resistance: last?.resistance ?? null,
      } as TechnicalChartPoint
    })
}

// ── right-padding (future dates with extended indicator lines) ─────────────────
function addPadding(data: TechnicalChartPoint[], n: number, weekly: boolean): TechnicalChartPoint[] {
  if (!data.length || n <= 0) return data
  const last   = data[data.length - 1]
  const result = [...data]
  const cur    = new Date(last.date)
  let added    = 0
  while (added < n) {
    cur.setDate(cur.getDate() + (weekly ? 7 : 1))
    if (!weekly && (cur.getDay() === 0 || cur.getDay() === 6)) continue
    result.push({
      date:       cur.toISOString().split('T')[0],
      open:       null,
      high:       null,
      low:        null,
      price:      null as any,
      mid:        last.mid,
      upper:      last.upper,
      lower:      last.lower,
      zscore:     null,
      ma5:        last.ma5,
      ma30:       last.ma30,
      ma60:       last.ma60,
      ma120:      last.ma120,
      resistance: last.resistance,
    })
    added++
  }
  return result
}

// ── MA config ─────────────────────────────────────────────────────────────────
const MA_COLORS: Record<string, string> = { ma5: '#f59e0b', ma30: '#a78bfa', ma60: '#34d399', ma120: '#fb923c' }
const MA_LABELS: Record<string, string> = { ma5: '5일', ma30: '30일', ma60: '60일', ma120: '120일' }
const MA_ORDER  = ['ma5', 'ma30', 'ma60', 'ma120'] as const

// ── tooltip ───────────────────────────────────────────────────────────────────
function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0]?.payload as TechnicalChartPoint
  if (!p) return null
  const isUp = (p.price ?? 0) >= (p.open ?? p.price ?? 0)
  return (
    <div className="bg-[#1a2035] border border-[#1e2d40] rounded-lg p-3 text-[11px] shadow-xl min-w-[148px] space-y-0.5">
      <p className="text-[#64748b] mb-1">{label}</p>
      {p.price != null && <>
        {p.open  != null && <p className="font-mono text-[#94a3b8]">O  ${p.open.toFixed(2)}</p>}
        {p.high  != null && <p className="font-mono" style={{ color: COLOR_UP }}>H  ${p.high.toFixed(2)}</p>}
        {p.low   != null && <p className="font-mono" style={{ color: COLOR_DOWN }}>L  ${p.low.toFixed(2)}</p>}
        <p className="font-mono font-bold" style={{ color: isUp ? COLOR_UP : COLOR_DOWN }}>C  ${p.price.toFixed(2)}</p>
      </>}
      {p.mid        != null && <p className="font-mono text-[#64748b]">BB mid  ${p.mid.toFixed(2)}</p>}
      {p.upper      != null && <p className="font-mono text-[#64748b]">BB 상단 ${p.upper.toFixed(2)}</p>}
      {p.lower      != null && <p className="font-mono text-[#64748b]">BB 하단 ${p.lower.toFixed(2)}</p>}
      {p.resistance != null && <p className="font-mono" style={{ color: '#ef4444' }}>저항선  ${p.resistance.toFixed(2)}</p>}
      {p.zscore     != null && <p className="font-mono text-[#64748b]">Z-Score {p.zscore.toFixed(2)}</p>}
      {MA_ORDER.filter(k => p[k] != null).map(k => (
        <p key={k} className="font-mono" style={{ color: MA_COLORS[k] }}>MA{MA_LABELS[k].replace('일', '')}  ${(p[k] as number).toFixed(2)}</p>
      ))}
    </div>
  )
}

// ── small numeric input ────────────────────────────────────────────────────────
function NumInput({ label, value, min, max, step, onChange }: {
  label: string; value: number; min: number; max: number; step: number
  onChange: (v: number) => void
}) {
  return (
    <label className="flex items-center gap-1 text-[11px] text-[#64748b] select-none">
      <span className="whitespace-nowrap">{label}</span>
      <input
        type="number" min={min} max={max} step={step} value={value}
        onChange={e => {
          const v = Number(e.target.value)
          if (!isNaN(v) && v >= min && v <= max) onChange(v)
        }}
        className="w-14 bg-[#060b14] border border-[#1e2d40] rounded px-1.5 py-0.5 text-[11px] font-mono text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
      />
    </label>
  )
}

// ── checkbox label ─────────────────────────────────────────────────────────────
function CB({ label, checked, onChange, color }: {
  label: string; checked: boolean; onChange: () => void; color?: string
}) {
  return (
    <label className="flex items-center gap-1 text-[11px] cursor-pointer select-none">
      <input type="checkbox" checked={checked} onChange={onChange} className="w-3 h-3 accent-[#3b82f6]" />
      <span style={{ color: color ?? (checked ? '#94a3b8' : '#374151') }}>{label}</span>
    </label>
  )
}

// ── price input with toggle ───────────────────────────────────────────────────
function PriceLine({ label, enabled, price, onToggle, onChange, color }: {
  label: string; enabled: boolean; price: number; onToggle: () => void
  onChange: (v: number) => void; color: string
}) {
  return (
    <div className="flex items-center gap-1.5">
      <CB label={label} checked={enabled} onChange={onToggle} color={enabled ? color : undefined} />
      {enabled && (
        <input
          type="number" step="0.01" min={0} value={price || ''}
          onChange={e => onChange(Number(e.target.value))}
          placeholder="0.00"
          className="w-20 bg-[#060b14] border rounded px-1.5 py-0.5 text-[11px] font-mono text-[#e2e8f0] focus:outline-none"
          style={{ borderColor: color + '66' }}
        />
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────
interface BollingerChartProps {
  ticker: string
  height?: number
}

export default function BollingerChart({ ticker, height = 420 }: BollingerChartProps) {

  // ── BB params (trigger API refetch when debounced) ────────────────────────
  const [bbPeriod,       setBBPeriod]       = useState(20)
  const [bbStd,          setBBStd]          = useState(2.0)
  const [resistLookback, setResistLookback] = useState(55)
  const bbPeriodD  = useDebounced(bbPeriod)
  const bbStdD     = useDebounced(bbStd)
  const resistD    = useDebounced(resistLookback)

  // ── display params ────────────────────────────────────────────────────────
  const [rightPad, setRightPad] = useState(30)

  // ── overlay toggles ───────────────────────────────────────────────────────
  const [useCandle,   setUseCandle]   = useState(true)
  const [showBands,   setShowBands]   = useState(true)
  const [showMid,     setShowMid]     = useState(true)
  const [showResist,  setShowResist]  = useState(true)
  const [activeMAs,   setActiveMAs]   = useState({ ma5: false, ma30: true, ma60: true, ma120: false })
  const [tpEnabled,   setTpEnabled]   = useState(false)
  const [slEnabled,   setSlEnabled]   = useState(false)
  const [tpPrice,     setTpPrice]     = useState(0)
  const [slPrice,     setSlPrice]     = useState(0)

  // ── zoom state ────────────────────────────────────────────────────────────
  const [domain,   setDomain]   = useState<[string, string] | null>(null)
  const [refLeft,  setRefLeft]  = useState<string | null>(null)
  const [refRight, setRefRight] = useState<string | null>(null)
  const lastLbl = useRef<string | null>(null)

  // ── reset zoom when ticker changes ────────────────────────────────────────
  useEffect(() => {
    setDomain(null); setRefLeft(null); setRefRight(null)
    lastLbl.current = null
  }, [ticker])

  // ── global mouseup for drag-zoom ──────────────────────────────────────────
  useEffect(() => {
    function onUp() {
      if (refLeft == null) return
      const right = refRight ?? lastLbl.current
      if (right && right !== refLeft) {
        const [a, b] = refLeft < right ? [refLeft, right] : [right, refLeft]
        setDomain([a, b])
      }
      setRefLeft(null); setRefRight(null)
      lastLbl.current = null
    }
    window.addEventListener('mouseup', onUp)
    return () => window.removeEventListener('mouseup', onUp)
  }, [refLeft, refRight])

  // ── API call ──────────────────────────────────────────────────────────────
  const q = useQuery({
    queryKey: ['timing-technical-chart', ticker, bbPeriodD, bbStdD, resistD],
    queryFn:  () => getTechnicalChart(ticker, '3y', bbPeriodD, bbStdD, resistD),
    staleTime: 600_000,
    enabled:  !!ticker,
  })

  const series    = q.data?.series     ?? []
  const keyPoints = q.data?.key_points ?? []

  // ── zoom-filtered data ────────────────────────────────────────────────────
  const visible = useMemo(() => {
    if (!domain) return series
    const [a, b] = domain
    return series.filter(p => p.date >= a && p.date <= b)
  }, [series, domain])

  // ── weekly / daily mode (based on visible date span) ─────────────────────
  const isWeekly = useMemo(() => {
    const real = visible.filter(p => p.price != null)
    if (real.length < 2) return false
    const span = (new Date(real[real.length - 1].date).getTime() - new Date(real[0].date).getTime()) / 86400000
    return span > 30
  }, [visible])

  // ── final display data (aggregate + right pad) ────────────────────────────
  const displayData = useMemo((): TechnicalChartPoint[] => {
    const real = visible.filter(p => p.price != null)
    if (isWeekly) {
      const weekly = aggregateWeekly(real)
      return addPadding(weekly, Math.max(1, Math.ceil(rightPad / 5)), true)
    }
    return addPadding(real, rightPad, false)
  }, [visible, isWeekly, rightPad])

  // ── y-axis domain: include high/low/bands ────────────────────────────────
  const yDomain = useMemo<[number, number] | ['auto', 'auto']>(() => {
    const vals: number[] = []
    displayData.forEach(d => {
      if (d.high  != null) vals.push(d.high)
      else if (d.price != null) vals.push(d.price)
      if (d.low   != null) vals.push(d.low)
      if (d.upper != null) vals.push(d.upper)
      if (d.lower != null) vals.push(d.lower)
      if (tpEnabled && tpPrice > 0) vals.push(tpPrice)
      if (slEnabled && slPrice > 0) vals.push(slPrice)
    })
    if (!vals.length) return ['auto', 'auto']
    const mn  = Math.min(...vals)
    const mx  = Math.max(...vals)
    const pad = (mx - mn) * 0.08
    return [mn - pad, mx + pad]
  }, [displayData, tpEnabled, tpPrice, slEnabled, slPrice])

  // ── key points within visible range ──────────────────────────────────────
  const visibleKPs = useMemo(() => {
    if (!visible.length) return keyPoints
    const [a, b] = [visible[0].date, visible[visible.length - 1].date]
    return keyPoints.filter(k => k.date >= a && k.date <= b)
  }, [keyPoints, visible])

  // ── MA cross detection ────────────────────────────────────────────────────
  const maCrosses = useMemo(() => {
    const active = MA_ORDER.filter(k => activeMAs[k])
    if (active.length < 2 || !visible.length) return []
    const [va, vb] = [visible[0].date, visible[visible.length - 1].date]
    const crosses: { date: string; price: number; type: 'golden' | 'dead' }[] = []
    for (let i = 0; i < active.length - 1; i++) {
      for (let j = i + 1; j < active.length; j++) {
        const [fast, slow] = [active[i], active[j]]
        for (let k = 1; k < series.length; k++) {
          const c = series[k]
          if (c.date < va || c.date > vb) continue
          const p = series[k - 1]
          const pf = p[fast] as number | null
          const ps = p[slow] as number | null
          const cf = c[fast] as number | null
          const cs = c[slow] as number | null
          if (!pf || !ps || !cf || !cs) continue
          if (pf <= ps && cf > cs) crosses.push({ date: c.date, price: c.price, type: 'golden' })
          else if (pf >= ps && cf < cs) crosses.push({ date: c.date, price: c.price, type: 'dead' })
        }
      }
    }
    return crosses
  }, [series, visible, activeMAs])

  // ── handlers ─────────────────────────────────────────────────────────────
  function toggleMA(k: string) { setActiveMAs(p => ({ ...p, [k]: !p[k as keyof typeof p] })) }
  function onDown(e: any) { if (!e?.activeLabel) return; setRefLeft(e.activeLabel); setRefRight(null); lastLbl.current = e.activeLabel }
  function onMove(e: any) { if (!refLeft || !e?.activeLabel) return; setRefRight(e.activeLabel); lastLbl.current = e.activeLabel }
  function onReset() { setDomain(null); setRefLeft(null); setRefRight(null); lastLbl.current = null }

  const CandleOrEmpty = useCallback((props: any) =>
    useCandle ? <CandleShape {...props} /> : <g />, [useCandle])

  const zColor = !q.data ? '#64748b' : q.data.current_z <= -1 ? COLOR_UP : q.data.current_z >= 1 ? COLOR_DOWN : '#94a3b8'
  const resistBreaks = visibleKPs.filter(k => k.type === 'RESISTANCE_BREAK').length

  return (
    <div>
      {/* ── controls ───────────────────────────────────────────────────── */}
      <div className="px-1 pb-2 space-y-2 border-b border-[#1e2d40] mb-3 text-[11px]">

        {/* Row 1 — BB params */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <NumInput label="BB 기간"    value={bbPeriod}       min={5}  max={100} step={1}   onChange={setBBPeriod} />
          <NumInput label="σ 배수"     value={bbStd}          min={0.5} max={5}  step={0.1} onChange={setBBStd} />
          <NumInput label="저항선 기간" value={resistLookback} min={10} max={200} step={5}   onChange={setResistLookback} />
          <NumInput label="우측 여백"  value={rightPad}       min={0}  max={90}  step={5}   onChange={setRightPad} />
          <span className="text-[10px] px-1.5 py-0.5 rounded border border-[#1e2d40] text-[#64748b]">
            {isWeekly ? '주봉' : '일봉'}
          </span>
          {q.isFetching && <span className="text-[10px] text-[#3b82f6]">계산 중…</span>}
          {domain && (
            <button onClick={onReset} className="text-[#3b82f6] hover:text-[#60a5fa] underline">
              줌 초기화
            </button>
          )}
          <span className="ml-auto font-mono font-bold" style={{ color: zColor }}>
            {q.data && `Z ${q.data.current_z.toFixed(2)}`}
          </span>
        </div>

        {/* Row 2 — overlays */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <CB label="캔들" checked={useCandle} onChange={() => setUseCandle(v => !v)}
            color={useCandle ? '#e2e8f0' : undefined} />
          <span className="text-[#1e2d40]">|</span>
          <CB label="볼린저밴드" checked={showBands}  onChange={() => setShowBands(v => !v)}  color={showBands  ? '#3b82f6' : undefined} />
          <CB label="중앙선"     checked={showMid}    onChange={() => setShowMid(v => !v)}    color={showMid    ? '#64748b' : undefined} />
          <CB label="저항선"     checked={showResist} onChange={() => setShowResist(v => !v)} color={showResist ? '#ef4444' : undefined} />
          <span className="text-[#1e2d40]">|</span>
          <span className="text-[10px] text-[#374151] font-bold">MA</span>
          {MA_ORDER.map(k => (
            <CB key={k} label={MA_LABELS[k]}
              checked={activeMAs[k as keyof typeof activeMAs]}
              onChange={() => toggleMA(k)}
              color={activeMAs[k as keyof typeof activeMAs] ? MA_COLORS[k] : undefined} />
          ))}
        </div>

        {/* Row 3 — TP / SL */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <PriceLine label="익절선 TP" enabled={tpEnabled} price={tpPrice}
            onToggle={() => setTpEnabled(v => !v)} onChange={setTpPrice} color={COLOR_UP} />
          <PriceLine label="손절선 SL" enabled={slEnabled} price={slPrice}
            onToggle={() => setSlEnabled(v => !v)} onChange={setSlPrice} color={COLOR_DOWN} />
        </div>
      </div>

      {/* ── loading / error ─────────────────────────────────────────────── */}
      {q.isLoading && (
        <div className="flex items-center justify-center" style={{ height }}>
          <span className="text-sm text-[#64748b]">{ticker} 데이터 로딩 중…</span>
        </div>
      )}
      {q.isError && (
        <div className="flex items-center justify-center" style={{ height }}>
          <span className="text-sm text-[#ef4444]">{ticker} 데이터를 찾을 수 없습니다.</span>
        </div>
      )}

      {/* ── chart ───────────────────────────────────────────────────────── */}
      {q.data && (
        <ResponsiveContainer width="100%" height={height}>
          <ComposedChart
            data={displayData}
            margin={{ top: 6, right: 20, left: 0, bottom: 0 }}
            onMouseDown={onDown}
            onMouseMove={onMove}
            onDoubleClick={onReset}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
            <XAxis dataKey="date"
              tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false}
              minTickGap={50} />
            <YAxis domain={yDomain}
              tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false}
              width={60} tickFormatter={v => `$${Number(v).toFixed(0)}`} />
            <Tooltip content={<ChartTooltip />} />

            {/* BB filled channel: upper area fills down, lower area erases with background */}
            {showBands && (
              <Area type="monotone" dataKey="upper"
                stroke="#334155" strokeWidth={1}
                fill="#3b82f6" fillOpacity={0.07}
                dot={false} isAnimationActive={false} legendType="none" />
            )}
            {showBands && (
              <Area type="monotone" dataKey="lower"
                stroke="#334155" strokeWidth={1}
                fill="#0b0f1a" fillOpacity={1}
                dot={false} isAnimationActive={false} legendType="none" />
            )}

            {/* BB middle line */}
            {showMid && (
              <Line type="monotone" dataKey="mid"
                stroke="#4b5563" strokeWidth={1} strokeDasharray="4 2"
                dot={false} isAnimationActive={false} />
            )}

            {/* resistance line */}
            {showResist && (
              <Line type="monotone" dataKey="resistance"
                stroke="#ef4444" strokeWidth={1} strokeDasharray="3 3"
                dot={false} isAnimationActive={false} connectNulls={false} />
            )}

            {/* MA lines (extend into right padding via connectNulls) */}
            {MA_ORDER.map(k => activeMAs[k] ? (
              <Line key={k} type="monotone" dataKey={k}
                stroke={MA_COLORS[k]} strokeWidth={1.5}
                dot={false} isAnimationActive={false} connectNulls />
            ) : null)}

            {/* candlestick bar — always rendered (invisible when useCandle=false) to keep band scale */}
            <Bar dataKey="price" shape={CandleOrEmpty} isAnimationActive={false} maxBarSize={20} />

            {/* price line when candle is off */}
            {!useCandle && (
              <Line type="monotone" dataKey="price"
                stroke="#3b82f6" strokeWidth={2}
                dot={false} isAnimationActive={false} connectNulls={false} />
            )}

            {/* MA cross markers */}
            {maCrosses.map((cp, i) => (
              <ReferenceDot key={`cross-${i}`} x={cp.date} y={cp.price}
                r={5} fill={cp.type === 'golden' ? '#fbbf24' : '#a855f7'}
                stroke="#0b0f1a" strokeWidth={1.5} />
            ))}

            {/* TP line */}
            {tpEnabled && tpPrice > 0 && (
              <ReferenceLine y={tpPrice} stroke={COLOR_UP} strokeDasharray="6 3" strokeWidth={1.5}
                label={{ value: `TP  $${tpPrice.toFixed(2)}`, fill: COLOR_UP, fontSize: 10, position: 'insideTopRight' }} />
            )}
            {/* SL line */}
            {slEnabled && slPrice > 0 && (
              <ReferenceLine y={slPrice} stroke={COLOR_DOWN} strokeDasharray="6 3" strokeWidth={1.5}
                label={{ value: `SL  $${slPrice.toFixed(2)}`, fill: COLOR_DOWN, fontSize: 10, position: 'insideBottomRight' }} />
            )}

            {/* drag-zoom highlight */}
            {refLeft && refRight && (
              <ReferenceArea x1={refLeft} x2={refRight}
                strokeOpacity={0.3} fill="#3b82f6" fillOpacity={0.15} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* ── bottom legend ───────────────────────────────────────────────── */}
      {q.data && (
        <div className="flex flex-wrap items-center gap-3 justify-center mt-1.5 text-[10px] text-[#374151]">
          <span>드래그 확대 · 더블클릭 초기화</span>
          {maCrosses.filter(c => c.type === 'golden').length > 0 && (
            <span style={{ color: '#fbbf24' }}>● 골든크로스 {maCrosses.filter(c => c.type === 'golden').length}</span>
          )}
          {maCrosses.filter(c => c.type === 'dead').length > 0 && (
            <span style={{ color: '#a855f7' }}>● 데드크로스 {maCrosses.filter(c => c.type === 'dead').length}</span>
          )}
          {resistBreaks > 0 && (
            <span style={{ color: '#3b82f6' }}>● 저항돌파 {resistBreaks}</span>
          )}
        </div>
      )}
    </div>
  )
}
