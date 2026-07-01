import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts'
import { Search, Star } from 'lucide-react'
import { getMarketRegime } from '@/api'
import { COLOR_UP, COLOR_DOWN, COLOR_NEUTRAL } from './colors'
import type { HoldingsMap } from '@/types'

const YEAR_OPTIONS = [1, 2, 3, 5] as const

interface RegimePanelProps {
  holdings?: HoldingsMap
}

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

export default function RegimePanel({ holdings = {} }: RegimePanelProps) {
  const [ticker, setTicker] = useState('^GSPC')
  const [input, setInput]   = useState('')
  const [years, setYears]   = useState<1 | 2 | 3 | 5>(1)

  const holdingTickers = Object.keys(holdings).filter(t => t !== 'CASH')

  const q = useQuery({
    queryKey: ['timing-regime', ticker, years],
    queryFn: () => getMarketRegime(ticker, years),
    staleTime: 300_000,
  })

  // Bridge regime transitions so the line is continuous at color-change boundaries.
  // Each point at a regime switch appears in BOTH the exiting and entering series.
  const chartData = useMemo(() => {
    if (!q.data) return []
    const raw = q.data.chart_data
    return raw.map((p, i) => {
      const prev = i > 0 ? raw[i - 1] : null
      const isBull = p.regime === 'Bull'
      const isSide = p.regime === 'Sideways'
      const isBear = p.regime === 'Bear'
      const prevBull = prev?.regime === 'Bull'
      const prevSide = prev?.regime === 'Sideways'
      const prevBear = prev?.regime === 'Bear'
      return {
        date:     p.date,
        price:    p.price,
        regime:   p.regime,
        // Include own regime + bridge from the previous regime so no gap forms
        bull:     isBull || prevBull ? p.price : null,
        sideways: isSide || prevSide ? p.price : null,
        bear:     isBear || prevBear ? p.price : null,
      }
    })
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

      {/* 검색 */}
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

      {/* 보유 종목 즐겨찾기 */}
      {holdingTickers.length > 0 && (
        <div>
          <div className="flex items-center gap-1.5 mb-1.5">
            <Star className="w-3 h-3 text-[#f59e0b]" />
            <span className="text-[10px] text-[#64748b] font-bold tracking-widest uppercase">보유 종목</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {holdingTickers.map(t => (
              <button key={t} onClick={() => setTicker(t)}
                className={`px-2.5 py-1 text-[11px] font-mono rounded border ${ticker === t ? 'border-[#f59e0b] text-[#f59e0b] bg-[#f59e0b]/10' : 'border-[#1e2d40] text-[#64748b] hover:text-[#94a3b8]'}`}>
                {t}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 기간 선택 */}
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] text-[#64748b] font-bold tracking-widest">기간</span>
        {YEAR_OPTIONS.map(y => (
          <button key={y} onClick={() => setYears(y as 1 | 2 | 3 | 5)}
            className={`px-2.5 py-1 text-[11px] font-mono rounded border transition-colors ${years === y ? 'border-[#3b82f6] text-[#3b82f6] bg-[#3b82f6]/10' : 'border-[#1e2d40] text-[#64748b] hover:text-[#94a3b8]'}`}>
            {y}Y
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
            <span className="text-[10px] text-[#374151] ml-auto">{years}년 기간 내 비율</span>
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
              <Line type="monotone" dataKey="bull"     stroke={COLOR_UP}      strokeWidth={2.5} dot={false} connectNulls={false} name="상승장" isAnimationActive={false} />
              <Line type="monotone" dataKey="sideways" stroke={COLOR_NEUTRAL} strokeWidth={2.5} dot={false} connectNulls={false} name="횡보장" isAnimationActive={false} />
              <Line type="monotone" dataKey="bear"     stroke={COLOR_DOWN}    strokeWidth={2.5} dot={false} connectNulls={false} name="하락장" isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  )
}
