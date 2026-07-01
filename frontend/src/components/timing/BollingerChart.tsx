import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceArea, ReferenceDot,
} from 'recharts'
import type { TechnicalChartPoint, TechnicalChartKeyPoint } from '@/types'
import { COLOR_UP, COLOR_DOWN } from './colors'

interface BollingerChartProps {
  series: TechnicalChartPoint[]
  keyPoints: TechnicalChartKeyPoint[]
  bias: 'LONG' | 'SHORT' | 'NEUTRAL'
  currentZ: number
  overlays?: { bands?: boolean; mid?: boolean; resistance?: boolean }
  height?: number
}

const MA_COLORS: Record<string, string> = {
  ma5:   '#f59e0b',
  ma30:  '#a78bfa',
  ma60:  '#34d399',
  ma120: '#fb923c',
}

const MA_LABELS: Record<string, string> = {
  ma5: '5일', ma30: '30일', ma60: '60일', ma120: '120일',
}

const MA_ORDER = ['ma5', 'ma30', 'ma60', 'ma120']

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0]?.payload
  if (!p) return null
  return (
    <div className="bg-[#1a2035] border border-[#1e2d40] rounded-lg p-3 text-[11px] shadow-xl">
      <p className="text-[#64748b] mb-1.5">{label}</p>
      <p className="text-[#e2e8f0] font-mono">가격 ${p.price?.toFixed(2)}</p>
      {p.resistance != null && <p className="font-mono" style={{ color: '#ef4444' }}>저항선 ${p.resistance.toFixed(2)}</p>}
      {p.mid   != null && <p className="text-[#94a3b8] font-mono">중앙선 ${p.mid.toFixed(2)}</p>}
      {p.upper != null && <p className="text-[#94a3b8] font-mono">상단밴드 ${p.upper.toFixed(2)}</p>}
      {p.lower != null && <p className="text-[#94a3b8] font-mono">하단밴드 ${p.lower.toFixed(2)}</p>}
      {p.zscore != null && <p className="text-[#64748b] font-mono">Z {p.zscore.toFixed(2)}</p>}
      {p.ma5   != null && <p className="font-mono" style={{ color: MA_COLORS.ma5 }}>MA5 ${p.ma5.toFixed(2)}</p>}
      {p.ma30  != null && <p className="font-mono" style={{ color: MA_COLORS.ma30 }}>MA30 ${p.ma30.toFixed(2)}</p>}
      {p.ma60  != null && <p className="font-mono" style={{ color: MA_COLORS.ma60 }}>MA60 ${p.ma60.toFixed(2)}</p>}
      {p.ma120 != null && <p className="font-mono" style={{ color: MA_COLORS.ma120 }}>MA120 ${p.ma120.toFixed(2)}</p>}
    </div>
  )
}

export default function BollingerChart({
  series, keyPoints, currentZ, overlays, height = 360,
}: BollingerChartProps) {
  const showBands      = overlays?.bands      ?? true
  const showMid        = overlays?.mid        ?? true
  const showResistance = overlays?.resistance ?? true

  const [activeMAs, setActiveMAs] = useState<Record<string, boolean>>({
    ma5: false, ma30: true, ma60: true, ma120: false,
  })
  const [domain, setDomain]     = useState<[string, string] | null>(null)
  const [refLeft, setRefLeft]   = useState<string | null>(null)
  const [refRight, setRefRight] = useState<string | null>(null)
  const lastLabelRef            = useRef<string | null>(null)

  function toggleMA(key: string) {
    setActiveMAs(prev => ({ ...prev, [key]: !prev[key] }))
  }

  // Global mouseup so drag-to-zoom completes even when mouse leaves the chart area.
  useEffect(() => {
    function onUp() {
      if (refLeft == null) return
      const right = refRight ?? lastLabelRef.current
      if (right && right !== refLeft) {
        const [a, b] = refLeft < right ? [refLeft, right] : [right, refLeft]
        setDomain([a, b])
      }
      setRefLeft(null)
      setRefRight(null)
      lastLabelRef.current = null
    }
    window.addEventListener('mouseup', onUp)
    return () => window.removeEventListener('mouseup', onUp)
  }, [refLeft, refRight])

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
    // Only RESISTANCE_BREAK dots shown on the price line
    visibleKeyPoints.filter(kp => kp.type === 'RESISTANCE_BREAK').forEach(kp => map.set(kp.date, kp))
    return map
  }, [visibleKeyPoints])

  const resistanceBreakCount = useMemo(
    () => visibleKeyPoints.filter(k => k.type === 'RESISTANCE_BREAK').length,
    [visibleKeyPoints]
  )

  // MA cross detection: golden cross (fast > slow) and dead cross (fast < slow) transitions
  const maCrossPoints = useMemo(() => {
    const activeKeys = MA_ORDER.filter(k => activeMAs[k])
    if (activeKeys.length < 2) return []
    const visStart = visible.length > 0 ? visible[0].date : ''
    const visEnd   = visible.length > 0 ? visible[visible.length - 1].date : ''
    const crosses: { date: string; price: number; type: 'golden' | 'dead' }[] = []

    for (let i = 0; i < activeKeys.length; i++) {
      for (let j = i + 1; j < activeKeys.length; j++) {
        const fast = activeKeys[i]
        const slow = activeKeys[j]
        for (let k = 1; k < series.length; k++) {
          const curr = series[k]
          if (curr.date < visStart || curr.date > visEnd) continue
          const prev = series[k - 1]
          const pf = prev[fast as keyof typeof prev] as number | null
          const ps = prev[slow as keyof typeof prev] as number | null
          const cf = curr[fast as keyof typeof curr] as number | null
          const cs = curr[slow as keyof typeof curr] as number | null
          if (pf == null || ps == null || cf == null || cs == null) continue
          if (pf <= ps && cf > cs) crosses.push({ date: curr.date, price: curr.price, type: 'golden' })
          else if (pf >= ps && cf < cs) crosses.push({ date: curr.date, price: curr.price, type: 'dead' })
        }
      }
    }
    return crosses
  }, [series, visible, activeMAs])

  function handleMouseDown(e: any) {
    if (e?.activeLabel == null) return
    setRefLeft(e.activeLabel); setRefRight(null)
    lastLabelRef.current = e.activeLabel
  }
  function handleMouseMove(e: any) {
    if (refLeft == null) return
    if (e?.activeLabel != null) {
      setRefRight(e.activeLabel)
      lastLabelRef.current = e.activeLabel
    }
  }
  function handleReset() {
    setDomain(null); setRefLeft(null); setRefRight(null)
    lastLabelRef.current = null
  }

  function renderPriceDot(props: any) {
    const { cx, cy, payload, index } = props
    const kp = keyPointByDate.get(payload.date)
    if (!kp) return <g key={`d-${index}`} />
    return <circle key={`kp-${index}`} cx={cx} cy={cy} r={4} fill="#3b82f6" stroke="#0b0f1a" strokeWidth={1.5} />
  }

  const zColor = currentZ <= -1 ? COLOR_UP : currentZ >= 1 ? COLOR_DOWN : '#94a3b8'

  return (
    <div>
      {/* 상단 바 */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-2">
        <div className="text-[11px] text-[#64748b]">
          Z-Score <span className="font-mono font-bold" style={{ color: zColor }}>{currentZ.toFixed(2)}</span>
        </div>
        <div className="flex items-center gap-2.5 text-[11px] text-[#64748b]">
          <span style={{ color: '#3b82f6' }}>저항선 돌파 <span className="font-mono font-bold">{resistanceBreakCount}</span></span>
          {maCrossPoints.length > 0 && (
            <>
              <span style={{ color: '#fbbf24' }}>
                골든크로스 <span className="font-mono font-bold">{maCrossPoints.filter(c => c.type === 'golden').length}</span>
              </span>
              <span style={{ color: '#a855f7' }}>
                데드크로스 <span className="font-mono font-bold">{maCrossPoints.filter(c => c.type === 'dead').length}</span>
              </span>
            </>
          )}
          {domain && (
            <button onClick={handleReset} className="text-[#3b82f6] hover:text-[#60a5fa] underline">
              초기화
            </button>
          )}
        </div>
      </div>

      {/* MA 토글 */}
      <div className="flex items-center gap-3 px-1 pb-3">
        <span className="text-[10px] text-[#374151] font-bold tracking-widest">MA</span>
        {Object.entries(MA_LABELS).map(([key, label]) => (
          <label key={key} className="flex items-center gap-1 text-[11px] cursor-pointer select-none">
            <input
              type="checkbox"
              checked={activeMAs[key]}
              onChange={() => toggleMA(key)}
              className="w-3 h-3"
            />
            <span style={{ color: activeMAs[key] ? MA_COLORS[key] : '#374151' }}>{label}</span>
          </label>
        ))}
      </div>

      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart
          data={visible}
          margin={{ top: 5, right: 10, left: 0, bottom: 0 }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onDoubleClick={handleReset}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
          <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={50} />
          <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} domain={['auto', 'auto']} width={55} />
          <Tooltip content={<ChartTooltip />} />

          {/* 볼린저 밴드 색채우기 */}
          {showBands && (
            <Area type="monotone" dataKey="upper" stroke="#475569" strokeWidth={1} fill="#3b82f6" fillOpacity={0.06}
              dot={false} name="상단밴드" isAnimationActive={false} legendType="none" />
          )}
          {showBands && (
            <Area type="monotone" dataKey="lower" stroke="#475569" strokeWidth={1} fill="#3b82f6" fillOpacity={0}
              dot={false} name="하단밴드" isAnimationActive={false} legendType="none" />
          )}

          {showMid && (
            <Line type="monotone" dataKey="mid" stroke="#64748b" strokeWidth={1} strokeDasharray="4 2"
              dot={false} name="중앙선" isAnimationActive={false} />
          )}

          {/* 저항선 */}
          {showResistance && (
            <Line type="monotone" dataKey="resistance" stroke="#ef4444" strokeWidth={1} strokeDasharray="3 3"
              dot={false} name="저항선" isAnimationActive={false} connectNulls={false} />
          )}

          {/* 이동평균선 */}
          {Object.entries(MA_COLORS).map(([key, color]) =>
            activeMAs[key] ? (
              <Line key={key} type="monotone" dataKey={key} stroke={color} strokeWidth={1.5}
                dot={false} name={MA_LABELS[key]} isAnimationActive={false} />
            ) : null
          )}

          {/* 가격선: 저항선 돌파 시만 점 표시 */}
          <Line type="monotone" dataKey="price" stroke="#3b82f6" strokeWidth={2}
            dot={renderPriceDot} name="가격" isAnimationActive={false} />

          {/* MA 크로스 마커 */}
          {maCrossPoints.map((cp, i) => (
            <ReferenceDot key={`cross-${i}`} x={cp.date} y={cp.price}
              r={5}
              fill={cp.type === 'golden' ? '#fbbf24' : '#a855f7'}
              stroke="#0b0f1a" strokeWidth={1.5}
            />
          ))}

          {refLeft && refRight && (
            <ReferenceArea x1={refLeft} x2={refRight} strokeOpacity={0.3} fill="#3b82f6" fillOpacity={0.15} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
      <div className="flex items-center gap-4 justify-center mt-1 text-[10px] text-[#374151]">
        <span>드래그 확대 · 더블클릭 초기화</span>
        <span style={{ color: '#fbbf24' }}>● 골든크로스</span>
        <span style={{ color: '#a855f7' }}>● 데드크로스</span>
        <span style={{ color: '#3b82f6' }}>● 저항선 돌파</span>
      </div>
    </div>
  )
}
