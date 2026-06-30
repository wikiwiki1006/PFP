import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, ReferenceLine,
} from 'recharts'
import { getPairsAuto } from '@/api'
import { COLOR_UP, COLOR_DOWN } from './colors'

function PriceTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-[#1a2035] border border-[#1e2d40] rounded-lg p-3 text-[11px] shadow-xl">
      <p className="text-[#64748b] mb-1.5">{label}</p>
      {payload.map((p: any) => (
        <p key={p.dataKey} className="font-mono" style={{ color: p.color }}>{p.name}: {Number(p.value).toFixed(2)}</p>
      ))}
    </div>
  )
}

export default function PairsTradingPanel() {
  const [tickerInput, setTickerInput] = useState('')
  const [thresholdInput, setThresholdInput] = useState('5')
  const [ticker, setTicker] = useState<string | null>(null)
  const [threshold, setThreshold] = useState(5)

  const q = useQuery({
    queryKey: ['timing-pairs-auto', ticker, threshold],
    queryFn: () => getPairsAuto(ticker as string, threshold, 5),
    enabled: !!ticker,
    staleTime: 600_000,
  })

  function submit() {
    const t = tickerInput.trim().toUpperCase()
    const th = parseFloat(thresholdInput)
    if (t) setTicker(t)
    if (!isNaN(th) && th > 0) setThreshold(th)
  }

  const breachDates = useMemo(() => new Set((q.data?.breaches || []).map(b => b.date)), [q.data])

  return (
    <div className="p-4 space-y-4">
      <div className="text-[11px] text-[#64748b] font-bold tracking-widest uppercase">페어 트레이딩 — 자동 유사종목 탐색</div>

      <div className="flex items-end gap-2">
        <div className="flex-1">
          <label className="text-[11px] text-[#64748b] block mb-1">기준 종목</label>
          <input
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder="예: KO"
            className="w-full bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm text-[#e2e8f0] placeholder:text-[#374151] focus:outline-none focus:border-[#3b82f6]"
          />
        </div>
        <div className="w-[120px]">
          <label className="text-[11px] text-[#64748b] block mb-1">임계값 %</label>
          <input
            type="number"
            value={thresholdInput}
            onChange={(e) => setThresholdInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            className="w-full bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
          />
        </div>
        <button onClick={submit} className="px-4 py-2 bg-[#3b82f6]/10 border border-[#3b82f6]/25 text-[#3b82f6] text-sm rounded hover:bg-[#3b82f6]/18 font-bold">
          탐색
        </button>
      </div>

      {q.isLoading && <div className="text-sm text-[#64748b]">{ticker} 유사 종목 탐색 중… (최초 1회, 다소 소요)</div>}
      {q.isError && <div className="text-sm text-[#ef4444]">{ticker}에 대한 데이터를 찾을 수 없습니다.</div>}

      {q.data && q.data.best && (
        <>
          <div className="flex items-center gap-3">
            <span className="text-sm text-[#94a3b8]">최적 페어:</span>
            <span className="font-mono font-bold text-[#e2e8f0]">{q.data.ticker}</span>
            <span className="text-[#64748b]">↔</span>
            <span className="font-mono font-bold text-[#3b82f6]">{q.data.best.ticker}</span>
            <span className="text-[11px] text-[#64748b]">상관계수 {q.data.best.correlation.toFixed(3)}</span>
          </div>

          <div className="flex flex-wrap gap-1.5">
            {q.data.matches.map(m => (
              <span key={m.ticker} className="text-[11px] font-mono px-2 py-1 rounded bg-[#060b14] border border-[#1e2d40] text-[#94a3b8]">
                {m.ticker} ({m.correlation.toFixed(2)})
              </span>
            ))}
          </div>

          <div>
            <div className="text-[11px] text-[#64748b] font-bold tracking-widest mb-1.5">인덱스화 가격 비교 (시작=100)</div>
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={q.data.chart} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={50} />
                <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} width={50} />
                <Tooltip content={<PriceTooltip />} />
                <Legend wrapperStyle={{ fontSize: '11px', color: '#64748b' }} />
                <Line type="monotone" dataKey="a" stroke="#3b82f6" strokeWidth={2} dot={false} name={q.data.ticker} isAnimationActive={false} />
                <Line type="monotone" dataKey="b" stroke="#f59e0b" strokeWidth={2} dot={false} name={q.data.best.ticker} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[11px] text-[#64748b] font-bold tracking-widest">스프레드 (%p) — 임계값 초과 구간 {q.data.breaches.length}건</span>
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={q.data.chart} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={50} />
                <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} width={50} />
                <Tooltip content={<PriceTooltip />} />
                <ReferenceLine y={threshold} stroke={COLOR_DOWN} strokeDasharray="4 2" />
                <ReferenceLine y={-threshold} stroke={COLOR_DOWN} strokeDasharray="4 2" />
                <ReferenceLine y={0} stroke="#374151" />
                <Area
                  type="monotone"
                  dataKey="spread"
                  stroke="#8b5cf6"
                  fill="#8b5cf6"
                  fillOpacity={0.15}
                  strokeWidth={1.5}
                  name="스프레드"
                  isAnimationActive={false}
                  dot={(props: any) => {
                    const isBreach = breachDates.has(props.payload.date)
                    if (!isBreach) return <g key={`d-${props.index}`} />
                    return <circle key={`b-${props.index}`} cx={props.cx} cy={props.cy} r={3} fill={COLOR_DOWN} stroke="#0b0f1a" strokeWidth={1} />
                  }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </>
      )}

      {q.data && !q.data.best && (
        <div className="text-sm text-[#f59e0b]">유사한 페어 종목을 찾지 못했습니다.</div>
      )}
    </div>
  )
}
