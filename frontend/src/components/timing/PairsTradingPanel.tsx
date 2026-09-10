import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, ReferenceLine,
} from 'recharts'
import { Star } from 'lucide-react'
import { getPairsAuto } from '@/api'
import { COLOR_DOWN } from './colors'
import { useTouchDismissTooltip } from '@/lib/useTouchDismissTooltip'
import { useIsMobile } from '@/lib/useIsMobile'
import { cn } from '@/lib/utils'
import type { HoldingsMap } from '@/types'
import { formatAxisPrice, formatPrice, getMarket } from '@/lib/market'

// yfinance 가 주는 영문 섹터를 한글로. 한국 화면에 'Healthcare' 가 그대로 뜨면
// 다른 화면(섹터 변동율)의 '헬스케어' 와 같은 것인지 알 수 없다.
const SECTOR_KO: Record<string, string> = {
  'Technology': '기술', 'Healthcare': '헬스케어', 'Financial Services': '금융',
  'Financial': '금융', 'Consumer Cyclical': '경기소비재', 'Consumer Defensive': '필수소비재',
  'Consumer': '소비재', 'Energy': '에너지', 'Industrials': '산업재',
  'Basic Materials': '소재', 'Real Estate': '부동산', 'Utilities': '유틸리티',
  'Communication Services': '커뮤니케이션',
}
const toKoSector = (s?: string | null) =>
  !s ? '' : (getMarket() === 'KR' ? (SECTOR_KO[s] ?? s) : s)
import { useTickerNames, displayTicker } from '@/lib/useTickerNames'

// 주가 비교 차트와 스프레드 차트가 syncId 로 커서를 공유한다(아래 참조) — 어느
// 쪽을 가리켜도 같은 날짜의 두 값을 한 번에 보여줘야 "동일 시점 비교"가 된다.
// 그래서 툴팁도 하나로 합쳐 a/b 가격과 스프레드를 항상 함께 보여준다.
function PairsTooltip({ active, payload, label, tickerA, tickerB }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0]?.payload
  if (!p) return null
  return (
    <div className="bg-[#1a2035] border border-[#1e2d40] rounded-lg p-3 text-[11px] shadow-xl space-y-1 min-w-[160px]">
      <p className="text-[#64748b] mb-1">{label}</p>
      {p.a != null && <p style={{ color: '#3b82f6' }}>{tickerA}: {formatPrice(Number(p.a))}</p>}
      {p.b != null && <p style={{ color: '#f59e0b' }}>{tickerB}: {formatPrice(Number(p.b))}</p>}
      {p.spread != null && (
        <p className="font-mono font-bold" style={{ color: '#8b5cf6' }}>스프레드: {Number(p.spread).toFixed(2)}%p</p>
      )}
    </div>
  )
}

interface PairsTradingPanelProps {
  holdings?: HoldingsMap
}

export default function PairsTradingPanel({ holdings = {} }: PairsTradingPanelProps) {
  const names = useTickerNames()
  const [tickerInput, setTickerInput]       = useState('')
  const [thresholdInput, setThresholdInput] = useState('5')
  const [ticker, setTicker]                 = useState<string | null>(null)
  const [threshold, setThreshold]           = useState(5)
  const [selectedPair, setSelectedPair]     = useState<string | null>(null)
  const { tooltipActive, onPointerDown, onPointerUp } = useTouchDismissTooltip()
  const isMobile = useIsMobile()

  const q = useQuery({
    queryKey: ['timing-pairs-auto', ticker, threshold],
    queryFn: () => getPairsAuto(ticker as string, threshold, 5),
    enabled: !!ticker,
    staleTime: 600_000,
  })

  // 보유 종목 프리셋 — 평가금액(수량×평단) 큰 순으로 정렬해 자주 쓰는 종목이 앞에 오게 한다
  const holdingTickers = useMemo(
    () => Object.entries(holdings)
      .filter(([t]) => t !== 'CASH')
      .sort(([, a], [, b]) => b.q * b.avg - a.q * a.avg)
      .map(([t]) => t),
    [holdings],
  )

  function submit() {
    const t  = tickerInput.trim().toUpperCase()
    const th = parseFloat(thresholdInput)
    if (t) { setTicker(t); setSelectedPair(null) }
    if (!isNaN(th) && th > 0) setThreshold(th)
  }

  /** 프리셋 클릭 — 입력창도 함께 채워 현재 선택을 명확히 보여준다 */
  function pickHolding(t: string) {
    setTickerInput(t)
    setTicker(t)
    setSelectedPair(null)
    const th = parseFloat(thresholdInput)
    if (!isNaN(th) && th > 0) setThreshold(th)
  }

  // activePair: user-selected or fallback to best
  const activePair = selectedPair ?? q.data?.best?.ticker ?? null

  // Pick chart data for the active pair from the new `charts` map
  const activeChart = useMemo(() => {
    if (!q.data || !activePair) return []
    return q.data.charts?.[activePair] ?? q.data.chart ?? []
  }, [q.data, activePair])

  const activeBreaches = useMemo(() => {
    if (!q.data || !activePair) return []
    return q.data.all_breaches?.[activePair] ?? q.data.breaches ?? []
  }, [q.data, activePair])

  const breachDates = useMemo(
    () => new Set(activeBreaches.map(b => b.date)),
    [activeBreaches]
  )

  return (
    <div className="p-2 md:p-4 space-y-4">
      <div className="text-[11px] text-[#64748b] font-bold tracking-widest uppercase">페어 트레이딩 — 자동 유사종목 탐색</div>

      {/* 입력 */}
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <label className="text-[11px] text-[#64748b] block mb-1">기준 종목</label>
          <input
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder={getMarket() === 'KR' ? "예: 005930.KS" : "예: KO"}
            className="w-full bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm text-[#e2e8f0] placeholder:text-[#374151] focus:outline-none focus:border-[#10b981]"
          />
        </div>
        <div className="w-[120px]">
          <label className="text-[11px] text-[#64748b] block mb-1">임계값 %</label>
          <input
            type="number"
            value={thresholdInput}
            onChange={(e) => setThresholdInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            className="w-full bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#10b981]"
          />
        </div>
        <button onClick={submit} className="px-4 py-2 bg-[#10b981]/10 border border-[#10b981]/25 text-[#10b981] text-sm rounded hover:bg-[#10b981]/18 font-bold">
          탐색
        </button>
      </div>

      {/* 보유 종목 프리셋 */}
      {holdingTickers.length > 0 && (
        <div>
          <div className="flex items-center gap-1.5 mb-1.5">
            <Star className="w-3 h-3 text-[#f59e0b]" />
            <span className="text-[10px] text-[#64748b] font-bold tracking-widest uppercase">보유 종목</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {holdingTickers.map(t => (
              <button key={t} onClick={() => pickHolding(t)}
                className={`px-2.5 py-1 text-[11px] rounded border transition-colors ${
                  ticker === t
                    ? 'border-[#f59e0b] text-[#f59e0b] bg-[#f59e0b]/10'
                    : 'border-[#1e2d40] text-[#64748b] hover:text-[#94a3b8]'
                }`} title={t}>
                {displayTicker(t, names)}
              </button>
            ))}
          </div>
        </div>
      )}

      {q.isLoading && <div className="text-sm text-[#64748b]">{displayTicker(ticker as string, names)} 유사 종목 탐색 중… (최초 1회, 다소 소요)</div>}
      {q.isError   && <div className="text-sm text-[#ef4444]">{displayTicker(ticker as string, names)}에 대한 데이터를 찾을 수 없습니다.</div>}

      {q.data && q.data.best && (
        <>
          {/* 활성 페어 헤더 */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm text-[#94a3b8]">기준:</span>
            <span className="font-bold text-[#e2e8f0]">{displayTicker(q.data.ticker, names)}</span>
            {q.data.base_sector && q.data.base_sector !== 'Unknown' && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#f59e0b]/10 text-[#f59e0b] border border-[#f59e0b]/20">
                {toKoSector(q.data.base_sector)}
              </span>
            )}
            <span className="text-[#64748b]">↔</span>
            <span className="font-bold text-[#3b82f6]">{displayTicker(activePair, names)}</span>
            {(() => {
              const m = q.data.matches.find(x => x.ticker === activePair)
              return m ? (
                <>
                  {m.sector && m.sector !== 'Unknown' && (
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
                      m.sector === q.data.base_sector
                        ? 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/20'
                        : 'bg-[#3b82f6]/10 text-[#3b82f6] border-[#3b82f6]/20'
                    }`}>
                      {m.sector}
                    </span>
                  )}
                  <span className="text-[11px] text-[#64748b]">
                    변동성 유사도 {m.correlation.toFixed(3)}
                  </span>
                </>
              ) : null
            })()}
          </div>

          {/* 상위 5개 유사 종목 클릭 버튼 */}
          <div>
            <div className="text-[10px] text-[#374151] font-bold tracking-widest uppercase mb-1.5">유사 종목 Top 5</div>
            <div className="flex flex-wrap gap-1.5">
              {q.data.matches.map(m => (
                <button
                  key={m.ticker}
                  onClick={() => setSelectedPair(m.ticker)}
                  className={`text-left text-[11px] font-mono px-2.5 py-1 rounded border transition-colors ${
                    activePair === m.ticker
                      ? 'border-[#10b981] bg-[#10b981]/10 text-[#10b981]'
                      : 'border-[#1e2d40] text-[#94a3b8] hover:border-[#10b981]/50 hover:text-[#e2e8f0]'
                  }`}
                >
                  <span>{displayTicker(m.ticker, names)}</span>
                  <span className="ml-1 text-[10px] opacity-60">{m.correlation.toFixed(2)}</span>
                  {m.sector && m.sector !== 'Unknown' && (
                    <span className={`ml-1 text-[9px] opacity-70 ${
                      m.sector === q.data.base_sector ? 'text-[#10b981]' : ''
                    }`}>
                      {toKoSector(m.sector)}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>

          {/* 주가 비교 + 스프레드 — 하나의 날짜 x축을 syncId 로 공유한다.
              두 패널을 시각적으로 하나처럼 붙이기 위해 위 차트는 x축 눈금을 숨기고
              (아래 차트가 대신 보여준다), 좌우 여백·축 폭을 동일하게 맞춰 두 차트의
              같은 날짜가 같은 x좌표에 오게 한다 — 그래야 커서를 어디에 올려도
              동일 시점의 주가와 스프레드를 함께 읽을 수 있다. */}
          {/* 모바일에서는 테두리를 없애고(item 5) 축 폭/여백도 줄여서(item 6) 좌우 최대폭을 확보한다.
              두 차트가 syncId 로 x좌표를 맞추려면 margin.right·YAxis width 를 반드시 서로 같게 유지해야 한다. */}
          <div
            onPointerDown={onPointerDown}
            onPointerUp={onPointerUp}
            className={cn(
              'overflow-hidden',
              isMobile ? '' : 'border border-[#1e2d40] rounded-lg',
            )}
          >
            <div className="flex items-center justify-between px-1 md:px-3 pt-3 pb-1">
              <span className="text-[11px] text-[#64748b] font-bold tracking-widest">
                주가 비교 (좌축 {displayTicker(q.data.ticker, names)} · 우축 {displayTicker(activePair, names)}) & 스프레드
              </span>
              <span className="text-[10px] text-[#374151]">임계값 초과 시점 {activeBreaches.length}건</span>
            </div>

            <ResponsiveContainer width="100%" height={240}>
              <ComposedChart data={activeChart} syncId="pairs-chart" margin={{ top: 5, right: isMobile ? 15 : 60, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
                <XAxis dataKey="date" tick={false} tickLine={false} axisLine={false} height={4} />
                <YAxis
                  yAxisId="left"
                  tick={{ fill: '#3b82f6', fontSize: 10 }}
                  tickLine={false} axisLine={false}
                  width={isMobile ? 34 : 55}
                  tickFormatter={(v: number) => formatAxisPrice(v)}
                />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  tick={{ fill: '#f59e0b', fontSize: 10 }}
                  tickLine={false} axisLine={false}
                  width={isMobile ? 34 : 55}
                  tickFormatter={(v: number) => formatAxisPrice(v)}
                />
                <Tooltip active={tooltipActive} content={<PairsTooltip tickerA={displayTicker(q.data.ticker, names)} tickerB={displayTicker(activePair, names)} />} />
                <Legend wrapperStyle={{ fontSize: '11px', color: '#64748b' }} />
                <Line yAxisId="left"  type="monotone" dataKey="a" stroke="#3b82f6" strokeWidth={2} dot={false} name={displayTicker(q.data.ticker, names)}  isAnimationActive={false} />
                <Line yAxisId="right" type="monotone" dataKey="b" stroke="#f59e0b" strokeWidth={2} dot={false} name={displayTicker(activePair ?? q.data.best.ticker, names)} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>

            <ResponsiveContainer width="100%" height={200}>
              <ComposedChart data={activeChart} syncId="pairs-chart" margin={{ top: 0, right: isMobile ? 15 : 60, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={50} />
                {/* 좌측 축 폭을 위 차트와 맞춰 두 패널의 x좌표를 정렬한다 */}
                <YAxis yAxisId="left"  tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} width={isMobile ? 34 : 55}
                  tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
                <YAxis yAxisId="right" orientation="right" tick={false} tickLine={false} axisLine={false} width={isMobile ? 34 : 55} />
                <ReferenceLine yAxisId="left" y={threshold}  stroke={COLOR_DOWN} strokeDasharray="4 2" />
                <ReferenceLine yAxisId="left" y={-threshold} stroke={COLOR_DOWN} strokeDasharray="4 2" />
                <ReferenceLine yAxisId="left" y={0}          stroke="#374151" />
                <Area
                  yAxisId="left"
                  type="monotone"
                  dataKey="spread"
                  stroke="#8b5cf6"
                  fill="#8b5cf6"
                  fillOpacity={0.15}
                  strokeWidth={1.5}
                  name="스프레드"
                  isAnimationActive={false}
                  dot={(props: any) => {
                    if (!breachDates.has(props.payload.date)) return <g key={`d-${props.index}`} />
                    return (
                      <circle key={`b-${props.index}`} cx={props.cx} cy={props.cy} r={4}
                        fill={COLOR_DOWN} stroke="#0b0f1a" strokeWidth={1.5} />
                    )
                  }}
                />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="text-[10px] text-[#374151] px-3 pb-2">
              빨간 점 = 임계값 초과 순간 (지속 구간 아님)
            </div>
          </div>
        </>
      )}

      {q.data && !q.data.best && (
        <div className="text-sm text-[#f59e0b]">유사한 페어 종목을 찾지 못했습니다.</div>
      )}
    </div>
  )
}
