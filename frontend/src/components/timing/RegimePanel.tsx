import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts'
import { Search } from 'lucide-react'
import { getMarketRegime } from '@/api'
import { COLOR_UP, COLOR_DOWN, COLOR_NEUTRAL } from './colors'

const PRESETS = ['^GSPC', 'AAPL', 'TSLA', 'NVDA', 'QQQ']

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const p = payload.find((x: any) => x.value != null)
  if (!p) return null
  const regime = p.payload.regime
  const color = regime === 'Bull' ? COLOR_UP : regime === 'Bear' ? COLOR_DOWN : COLOR_NEUTRAL
  return (
    <div className="bg-[#1a2035] border border-[#1e2d40] rounded-lg p-3 text-[11px] shadow-xl">
      <p className="text-[#64748b] mb-1">{label}</p>
      <p className="text-[#e2e8f0] font-mono">${Number(p.payload.price).toFixed(2)}</p>
      <p className="font-bold" style={{ color }}>{regime}</p>
    </div>
  )
}

export default function RegimePanel() {
  const [ticker, setTicker] = useState('^GSPC')
  const [input, setInput]   = useState('')

  const q = useQuery({
    queryKey: ['timing-regime', ticker],
    queryFn: () => getMarketRegime(ticker, '2y'),
    staleTime: 300_000,
  })

  const chartData = useMemo(() => {
    if (!q.data) return []
    return q.data.chart_data.map(p => ({
      date: p.date,
      price: p.price,
      regime: p.regime,
      bull:     p.regime === 'Bull'     ? p.price : null,
      sideways: p.regime === 'Sideways' ? p.price : null,
      bear:     p.regime === 'Bear'     ? p.price : null,
    }))
  }, [q.data])

  function submit() {
    const t = input.trim().toUpperCase()
    if (t) setTicker(t)
    setInput('')
  }

  const current = q.data?.current_regime
  const currentColor = current === 'Bull' ? COLOR_UP : current === 'Bear' ? COLOR_DOWN : COLOR_NEUTRAL

  return (
    <div className="p-4 space-y-4">
      <div className="text-[11px] text-[#64748b] font-bold tracking-widest uppercase">종목별 시장 상황 — K-means 국면 분석</div>

      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#374151]" />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder="티커 입력 (예: AAPL)"
            className="w-full bg-[#060b14] border border-[#1e2d40] rounded pl-8 pr-3 py-2 text-sm text-[#e2e8f0] placeholder:text-[#374151] focus:outline-none focus:border-[#3b82f6]"
          />
        </div>
        <button onClick={submit} className="px-3 py-2 bg-[#3b82f6]/10 border border-[#3b82f6]/25 text-[#3b82f6] text-sm rounded hover:bg-[#3b82f6]/18 font-bold">
          조회
        </button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {PRESETS.map(p => (
          <button key={p} onClick={() => setTicker(p)}
            className={`px-2.5 py-1 text-[11px] font-mono rounded border ${ticker === p ? 'border-[#3b82f6] text-[#3b82f6] bg-[#3b82f6]/10' : 'border-[#1e2d40] text-[#64748b] hover:text-[#94a3b8]'}`}>
            {p}
          </button>
        ))}
      </div>

      {q.isLoading && <div className="text-sm text-[#64748b]">로드 중…</div>}
      {q.isError && <div className="text-sm text-[#ef4444]">{ticker} 데이터를 불러올 수 없습니다.</div>}

      {q.data && (
        <>
          <div className="flex items-center gap-3">
            <span className="font-mono font-bold text-lg text-[#e2e8f0]">{q.data.ticker}</span>
            <span className="text-[11px] font-bold px-2 py-1 rounded uppercase tracking-wider" style={{ color: currentColor, backgroundColor: `${currentColor}1a` }}>
              현재 국면: {current === 'Bull' ? '상승장' : current === 'Bear' ? '하락장' : '횡보장'}
            </span>
          </div>

          <div className="grid grid-cols-3 gap-2">
            {(['Bull', 'Sideways', 'Bear'] as const).map(r => {
              const c = r === 'Bull' ? COLOR_UP : r === 'Bear' ? COLOR_DOWN : COLOR_NEUTRAL
              const pct = q.data!.regime_pct[r] ?? 0
              return (
                <div key={r} className="bg-[#060b14] border border-[#1e2d40] rounded p-2.5 text-center">
                  <div className="text-[10px] font-bold tracking-wider uppercase" style={{ color: c }}>
                    {r === 'Bull' ? '상승' : r === 'Bear' ? '하락' : '횡보'}
                  </div>
                  <div className="text-lg font-mono font-bold text-[#e2e8f0]">{pct.toFixed(0)}%</div>
                </div>
              )
            })}
          </div>

          <ResponsiveContainer width="100%" height={320}>
            <ComposedChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={50} />
              <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} domain={['auto', 'auto']} width={55} />
              <Tooltip content={<ChartTooltip />} />
              <Legend wrapperStyle={{ fontSize: '11px', color: '#64748b' }} />
              <Line type="monotone" dataKey="bull" stroke={COLOR_UP} strokeWidth={2.5} dot={false} connectNulls={false} name="상승장" isAnimationActive={false} />
              <Line type="monotone" dataKey="sideways" stroke={COLOR_NEUTRAL} strokeWidth={2.5} dot={false} connectNulls={false} name="횡보장" isAnimationActive={false} />
              <Line type="monotone" dataKey="bear" stroke={COLOR_DOWN} strokeWidth={2.5} dot={false} connectNulls={false} name="하락장" isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  )
}
