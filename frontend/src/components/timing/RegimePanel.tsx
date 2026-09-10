import { useMemo, useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts'
import { Search, Star } from 'lucide-react'
import { getMarketRegime } from '@/api'
import { COLOR_UP, COLOR_DOWN, COLOR_NEUTRAL, regimeColor, regimeLabel } from './colors'
import { useTouchDismissTooltip } from '@/lib/useTouchDismissTooltip'
import { formatPrice, getMarket } from '@/lib/market'
import { useMarket } from '@/lib/useMarket'
import type { HoldingsMap } from '@/types'
import { useTickerNames, displayTicker } from '@/lib/useTickerNames'

const YEAR_OPTIONS = [1, 2, 3, 5] as const

// 국면 판단의 기준 지수와 입력 예시는 시장을 따라간다.
// 한국 화면에서 ^GSPC(S&P500) 를 기본으로 보여 주면 시장 구분이 무너진다.
const MARKET_DEFAULTS = {
  US: { index: '^GSPC', hint: '티커 입력 (예: AAPL)' },
  KR: { index: '^KS11', hint: '종목 입력 (예: 삼성전자)' },
} as const

interface RegimePanelProps {
  holdings?: HoldingsMap
}

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const p = payload.find((x: any) => x.value != null)
  if (!p) return null
  const regime = p.payload.regime
  const color = regimeColor(regime)
  return (
    <div className="bg-[#1a2035] border border-[#1e2d40] rounded-lg p-3 text-[11px] shadow-xl">
      <p className="text-[#64748b] mb-1">{label}</p>
      <p className="text-[#e2e8f0] font-mono">{formatPrice(Number(p.payload.price))}</p>
      <p className="font-bold" style={{ color }}>{regimeLabel(regime)}</p>
    </div>
  )
}

export default function RegimePanel({ holdings = {} }: RegimePanelProps) {
  const market = useMarket()
  const names = useTickerNames()
  const [ticker, setTicker]             = useState<string>(() => MARKET_DEFAULTS[getMarket()].index)
  const [tickerInitialized, setTickerInitialized] = useState(false)
  const [input, setInput]   = useState('')
  const [years, setYears]   = useState<1 | 2 | 3 | 5>(1)
  const { tooltipActive, onPointerDown, onPointerUp } = useTouchDismissTooltip()

  const holdingTickers = Object.keys(holdings).filter(t => t !== 'CASH')

  // 시장을 바꾸면 이전 시장의 종목을 그대로 두지 않는다.
  // 보유 종목 기준 자동 선택도 다시 하도록 초기화 플래그를 푼다.
  useEffect(() => {
    setTicker(MARKET_DEFAULTS[market].index)
    setTickerInitialized(false)
  }, [market])

  // Set default ticker to the largest holding by cost basis (once, when holdings load)
  useEffect(() => {
    if (tickerInitialized) return
    const entries = Object.entries(holdings).filter(([t]) => t !== 'CASH')
    if (!entries.length) return
    const top = entries.sort(([, a], [, b]) => b.q * b.avg - a.q * a.avg)[0]?.[0]
    if (top) { setTicker(top); setTickerInitialized(true) }
  }, [holdings, tickerInitialized])

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
    // 국면이 바뀌는 지점은 양쪽 시리즈에 모두 넣어 선이 끊기지 않게 한다
    return raw.map((p, i) => {
      const prev = i > 0 ? raw[i - 1] : null
      const up   = p.regime === 'Bull',     pUp   = prev?.regime === 'Bull'
      const side = p.regime === 'Sideways', pSide = prev?.regime === 'Sideways'
      const down = p.regime === 'Bear',     pDown = prev?.regime === 'Bear'
      return {
        date:     p.date,
        price:    p.price,
        regime:   p.regime,
        bull:     up   || pUp   ? p.price : null,
        sideways: side || pSide ? p.price : null,
        bear:     down || pDown ? p.price : null,
      }
    })
  }, [q.data])

  function submit() {
    const t = input.trim().toUpperCase()
    if (t) setTicker(t)
    setInput('')
  }

  const current = q.data?.current_regime
  const currentColor = regimeColor(current ?? '')

  return (
    <div className="p-4 space-y-4">
      <div className="text-[11px] text-[#64748b] font-bold tracking-widest uppercase">종목별 시장 상황 — 카우프만 효율성 비율(ER) 추세 판단</div>

      {/* 검색 */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#374151]" />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder={MARKET_DEFAULTS[market].hint}
            className="w-full bg-[#060b14] border border-[#1e2d40] rounded pl-8 pr-3 py-2 text-sm text-[#e2e8f0] placeholder:text-[#374151] focus:outline-none focus:border-[#10b981]"
          />
        </div>
        <button onClick={submit} className="px-3 py-2 bg-[#10b981]/10 border border-[#10b981]/25 text-[#10b981] text-sm rounded hover:bg-[#10b981]/18 font-bold">
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
                {displayTicker(t, names)}
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
            className={`px-2.5 py-1 text-[11px] font-mono rounded border transition-colors ${years === y ? 'border-[#10b981] text-[#10b981] bg-[#10b981]/10' : 'border-[#1e2d40] text-[#64748b] hover:text-[#94a3b8]'}`}>
            {y}Y
          </button>
        ))}
      </div>

      {q.isLoading && <div className="text-sm text-[#64748b]">로드 중…</div>}
      {q.isError && <div className="text-sm text-[#ef4444]">{displayTicker(ticker, names)} 데이터를 불러올 수 없습니다.</div>}

      {q.data && (
        <>
          <div className="flex items-center gap-3">
            <span className="font-bold text-lg text-[#e2e8f0]">{displayTicker(q.data.ticker, names)}</span>
            <span className="text-[11px] font-bold px-2 py-1 rounded uppercase tracking-wider" style={{ color: currentColor, backgroundColor: `${currentColor}1a` }}>
              현재 국면: {regimeLabel(current ?? '')}
            </span>
            <span className="text-[10px] text-[#374151] ml-auto">{years}년 기간 내 비율</span>
          </div>

          <div className="grid grid-cols-3 gap-2">
            {(['Bull', 'Sideways', 'Bear'] as const).map(r => {
              const pct = q.data!.regime_pct[r] ?? 0
              return (
                <div key={r} className="bg-[#060b14] border border-[#1e2d40] rounded p-2.5 text-center">
                  <div className="text-[10px] font-bold tracking-wider uppercase" style={{ color: regimeColor(r) }}>
                    {regimeLabel(r)}
                  </div>
                  <div className="text-lg font-mono font-bold text-[#e2e8f0]">{pct.toFixed(0)}%</div>
                </div>
              )
            })}
          </div>

          {/* ER 판정 근거 — 현재 값과 임계값을 함께 보여준다 */}
          {q.data.current_er != null && (
            <div className="bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2.5">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[10px] text-[#64748b] font-bold tracking-wider uppercase">
                  효율성 비율 (ER · {q.data.window ?? 20}일)
                </span>
                <span className="font-mono text-sm font-bold" style={{ color: currentColor }}>
                  {q.data.current_er.toFixed(3)}
                </span>
              </div>
              {/* 0~1 게이지 + 임계값 마커 */}
              <div className="relative h-1.5 bg-[#1e2d40] rounded-full overflow-hidden">
                <div className="h-full rounded-full transition-all"
                  style={{ width: `${Math.min(100, q.data.current_er * 100)}%`, backgroundColor: currentColor }} />
                {/* 임계값을 모르면 선을 긋지 않는다. 0.3 을 가정해 그리면
                    실제로 쓰인 기준과 다른 위치를 보여주게 된다. */}
                {q.data.threshold != null && (
                  <div className="absolute top-0 bottom-0 w-px bg-[#94a3b8]"
                    style={{ left: `${q.data.threshold * 100}%` }} />
                )}
              </div>
              <p className="text-[10px] text-[#4a5568] mt-1.5 leading-relaxed">
                기간 순변동 ÷ 일별 절대변동 합. 1에 가까울수록 한 방향으로 직진, 0에 가까울수록 제자리 등락.
                {q.data.threshold != null
                  ? `임계값 ${q.data.threshold.toFixed(2)} 미만이면 횡보로 판정합니다.`
                  : '판정 임계값을 서버에서 받지 못했습니다.'}
              </p>
            </div>
          )}

          {/* 터치로 짚어 값을 보다가 손을 떼면 팝업이 안 사라지는 문제 — onPointerDown/Up 으로 강제 정리 */}
          <div onPointerDown={onPointerDown} onPointerUp={onPointerUp}>
          <ResponsiveContainer width="100%" height={320}>
            <ComposedChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={50} />
              <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} domain={['auto', 'auto']} width={55} />
              <Tooltip active={tooltipActive} content={<ChartTooltip />} />
              <Legend wrapperStyle={{ fontSize: '11px', color: '#64748b' }} />
              <Line type="monotone" dataKey="bull"     stroke={COLOR_UP}      strokeWidth={2.5} dot={false} connectNulls={false} name="상승" isAnimationActive={false} />
              <Line type="monotone" dataKey="sideways" stroke={COLOR_NEUTRAL} strokeWidth={2.5} dot={false} connectNulls={false} name="횡보" isAnimationActive={false} />
              <Line type="monotone" dataKey="bear"     stroke={COLOR_DOWN}    strokeWidth={2.5} dot={false} connectNulls={false} name="하락" isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  )
}
