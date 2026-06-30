import { useMemo, useState } from 'react'
import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceArea,
} from 'recharts'
import type { TechnicalChartPoint, TechnicalChartKeyPoint } from '@/types'
import { biasColor, biasLabel, COLOR_UP, COLOR_DOWN } from './colors'

interface BollingerChartProps {
  series: TechnicalChartPoint[]
  keyPoints: TechnicalChartKeyPoint[]
  bias: 'LONG' | 'SHORT' | 'NEUTRAL'
  currentZ: number
  overlays?: { bands?: boolean; mid?: boolean; resistance?: boolean }
  height?: number
}

const KEY_POINT_COLOR: Record<string, string> = {
  BAND_BREAK_UP:    COLOR_DOWN, // 상단 밴드 이탈 → 과매수(매도 신호)
  BAND_BREAK_DOWN:  COLOR_UP,   // 하단 밴드 이탈 → 과매도(매수 신호)
  RESISTANCE_BREAK: '#3b82f6',
}

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0]?.payload
  if (!p) return null
  return (
    <div className="bg-[#1a2035] border border-[#1e2d40] rounded-lg p-3 text-[11px] shadow-xl">
      <p className="text-[#64748b] mb-1.5">{label}</p>
      <p className="text-[#e2e8f0] font-mono">가격 ${p.price?.toFixed(2)}</p>
      {p.mid != null && <p className="text-[#94a3b8] font-mono">중앙선 ${p.mid.toFixed(2)}</p>}
      {p.upper != null && <p className="text-[#94a3b8] font-mono">상단밴드 ${p.upper.toFixed(2)}</p>}
      {p.lower != null && <p className="text-[#94a3b8] font-mono">하단밴드 ${p.lower.toFixed(2)}</p>}
      {p.resistance != null && <p className="font-mono" style={{ color: '#8b5cf6' }}>저항선 ${p.resistance.toFixed(2)}</p>}
      {p.zscore != null && <p className="text-[#64748b] font-mono">Z {p.zscore.toFixed(2)}</p>}
    </div>
  )
}

export default function BollingerChart({
  series, keyPoints, bias, currentZ, overlays, height = 360,
}: BollingerChartProps) {
  const showBands      = overlays?.bands ?? true
  const showMid        = overlays?.mid ?? true
  const showResistance = overlays?.resistance ?? true

  const [domain, setDomain]     = useState<[string, string] | null>(null)
  const [refLeft, setRefLeft]   = useState<string | null>(null)
  const [refRight, setRefRight] = useState<string | null>(null)

  const visible = useMemo(() => {
    if (!domain) return series
    const [a, b] = domain
    return series.filter(p => p.date >= a && p.date <= b)
  }, [series, domain])

  const visibleKeyPoints = useMemo(() => {
    if (visible.length === 0) return []
    const start = visible[0].date
    const end   = visible[visible.length - 1].date
    return keyPoints.filter(kp => kp.date >= start && kp.date <= end)
  }, [keyPoints, visible])

  const keyPointByDate = useMemo(() => {
    const map = new Map<string, TechnicalChartKeyPoint>()
    visibleKeyPoints.forEach(kp => map.set(kp.date, kp))
    return map
  }, [visibleKeyPoints])

  const summary = useMemo(() => {
    const buy   = visibleKeyPoints.filter(k => k.type === 'BAND_BREAK_DOWN').length
    const sell  = visibleKeyPoints.filter(k => k.type === 'BAND_BREAK_UP').length
    const brk   = visibleKeyPoints.filter(k => k.type === 'RESISTANCE_BREAK').length
    const dominant: 'LONG' | 'SHORT' | 'NEUTRAL' = buy > sell ? 'LONG' : sell > buy ? 'SHORT' : 'NEUTRAL'
    return { buy, sell, brk, dominant, total: visibleKeyPoints.length }
  }, [visibleKeyPoints])

  function handleMouseDown(e: any) {
    if (e?.activeLabel == null) return
    setRefLeft(e.activeLabel)
    setRefRight(null)
  }
  function handleMouseMove(e: any) {
    if (refLeft == null || e?.activeLabel == null) return
    setRefRight(e.activeLabel)
  }
  function handleMouseUp() {
    if (!refLeft || !refRight || refLeft === refRight) {
      setRefLeft(null); setRefRight(null)
      return
    }
    const [a, b] = refLeft < refRight ? [refLeft, refRight] : [refRight, refLeft]
    setDomain([a, b])
    setRefLeft(null); setRefRight(null)
  }
  function handleReset() {
    setDomain(null); setRefLeft(null); setRefRight(null)
  }

  const biasC = biasColor(bias)
  const dominantC = biasColor(summary.dominant)

  function renderPriceDot(props: any) {
    const { cx, cy, payload, index } = props
    const kp = keyPointByDate.get(payload.date)
    if (!kp) return <g key={`d-${index}`} />
    const color = KEY_POINT_COLOR[kp.type] || '#f59e0b'
    return <circle key={`kp-${index}`} cx={cx} cy={cy} r={4} fill={color} stroke="#0b0f1a" strokeWidth={1.5} />
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-2">
        <div className="flex items-center gap-2 text-[11px]">
          <span
            className="font-bold tracking-widest uppercase px-2 py-1 rounded"
            style={{ color: biasC, backgroundColor: `${biasC}1a` }}
          >
            현재 신호: {biasLabel(bias)}
          </span>
          <span className="text-[#64748b]">
            Z-Score <span className="font-mono font-bold text-[#e2e8f0]">{currentZ.toFixed(2)}</span>
          </span>
        </div>
        <div className="flex items-center gap-2.5 text-[11px] text-[#64748b]">
          <span>구간 내 주요지점 <span className="font-mono font-bold text-[#e2e8f0]">{summary.total}</span></span>
          <span style={{ color: COLOR_UP }}>매수 {summary.buy}</span>
          <span style={{ color: COLOR_DOWN }}>매도 {summary.sell}</span>
          <span style={{ color: '#3b82f6' }}>돌파 {summary.brk}</span>
          <span
            className="font-bold px-1.5 py-0.5 rounded"
            style={{ color: dominantC, backgroundColor: `${dominantC}1a` }}
          >
            {biasLabel(summary.dominant)}
          </span>
          {domain && (
            <button onClick={handleReset} className="text-[#3b82f6] hover:text-[#60a5fa] underline">
              초기화
            </button>
          )}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart
          data={visible}
          margin={{ top: 5, right: 10, left: 0, bottom: 0 }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onDoubleClick={handleReset}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
          <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={50} />
          <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} domain={['auto', 'auto']} width={55} />
          <Tooltip content={<ChartTooltip />} />
          {showBands && <Line type="monotone" dataKey="upper" stroke="#475569" strokeWidth={1} dot={false} name="상단밴드" isAnimationActive={false} />}
          {showMid && <Line type="monotone" dataKey="mid"   stroke="#64748b" strokeWidth={1} strokeDasharray="4 2" dot={false} name="평균회귀선(중앙)" isAnimationActive={false} />}
          {showBands && <Line type="monotone" dataKey="lower" stroke="#475569" strokeWidth={1} dot={false} name="하단밴드" isAnimationActive={false} />}
          {showResistance && <Line type="monotone" dataKey="resistance" stroke="#8b5cf6" strokeWidth={1} strokeDasharray="2 2" dot={false} name="저항선" isAnimationActive={false} />}
          <Line type="monotone" dataKey="price" stroke="#3b82f6" strokeWidth={2} dot={renderPriceDot} name="가격" isAnimationActive={false} />
          {refLeft && refRight && (
            <ReferenceArea x1={refLeft} x2={refRight} strokeOpacity={0.3} fill="#3b82f6" fillOpacity={0.15} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
      <div className="text-[10px] text-[#374151] text-center mt-1">드래그하여 확대 · 더블클릭하여 초기화</div>
    </div>
  )
}
