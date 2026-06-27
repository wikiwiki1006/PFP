import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  Cell, ReferenceLine, PieChart, Pie,
} from 'recharts'
import { Zap, AlertTriangle, ShieldCheck, Search, RefreshCw } from 'lucide-react'
import LoadingSpinner, { ErrorMessage } from '@/components/LoadingSpinner'
import {
  getSignalsDoomRadar, getCachedScan, runSignalScan,
  getMeanReversionSignal, getMomentumSignal, getPairsSignal,
  getMarketRegime,
} from '@/api'
import { formatCurrency, colorForValue, cn } from '@/lib/utils'

const TABS = ['Doom Radar', 'Market Regime', 'Signal Scan', 'Mean Reversion', 'Momentum', 'Pairs Trading']
const TOOLTIP_STYLE = { backgroundColor: '#0f172a', border: '1px solid #1e2d40', borderRadius: 4, fontSize: 11, color: '#e2e8f0' }

function SignalBadge({ signal }: { signal: string }) {
  const upper = signal?.toUpperCase() ?? ''
  let color = 'bg-[#64748b]/10 text-[#64748b]'
  if (upper.includes('BUY') || upper.includes('LONG') || upper.includes('OVERSOLD')) color = 'bg-[#10b981]/10 text-[#10b981]'
  else if (upper.includes('SELL') || upper.includes('SHORT') || upper.includes('OVERBOUGHT')) color = 'bg-[#ef4444]/10 text-[#ef4444]'
  else if (upper.includes('NEUTRAL') || upper.includes('HOLD')) color = 'bg-[#f59e0b]/10 text-[#f59e0b]'
  return <span className={cn('text-xs px-2 py-0.5 rounded font-medium', color)}>{signal}</span>
}

export default function TimingEngine() {
  const qc = useQueryClient()
  const [tab, setTab] = useState(0)

  // Doom Radar
  const doomQ = useQuery({ queryKey: ['signals-doom'], queryFn: getSignalsDoomRadar })

  // Market Regime
  const [regimeTicker, setRegimeTicker] = useState('^GSPC')
  const [regimePeriod, setRegimePeriod] = useState('2y')
  const [activeRegimeTicker, setActiveRegimeTicker] = useState('^GSPC')
  const regimeQ = useQuery({ queryKey: ['regime', activeRegimeTicker, regimePeriod], queryFn: () => getMarketRegime(activeRegimeTicker, regimePeriod) })

  // Signal Scan
  const scanQ = useQuery({ queryKey: ['scan'], queryFn: getCachedScan, retry: false })
  const scanMut = useMutation({ mutationFn: () => runSignalScan(10), onSuccess: () => qc.invalidateQueries({ queryKey: ['scan'] }) })

  // Mean Reversion / Momentum
  const [lookupTicker, setLookupTicker] = useState('')
  const [activeLookup, setActiveLookup] = useState('')
  const mrQ = useQuery({ queryKey: ['mean-reversion', activeLookup], queryFn: () => getMeanReversionSignal(activeLookup), enabled: !!activeLookup })
  const momQ = useQuery({ queryKey: ['momentum', activeLookup], queryFn: () => getMomentumSignal(activeLookup), enabled: !!activeLookup })

  // Pairs
  const [pairA, setPairA] = useState('')
  const [pairB, setPairB] = useState('')
  const [pairPeriod, setPairPeriod] = useState('1y')
  const [activePair, setActivePair] = useState({ a: '', b: '', period: '1y' })
  const pairsQ = useQuery({ queryKey: ['pairs', activePair.a, activePair.b, activePair.period], queryFn: () => getPairsSignal(activePair.a, activePair.b, activePair.period), enabled: !!activePair.a && !!activePair.b })

  const regimePct = regimeQ.data?.regime_pct
  const regimePieData = regimePct ? [
    { name: 'Bull', value: regimePct['Bull'] || 0, color: '#10b981' },
    { name: 'Sideways', value: regimePct['Sideways'] || 0, color: '#f59e0b' },
    { name: 'Bear', value: regimePct['Bear'] || 0, color: '#ef4444' },
  ] : []

  return (
    <div className="p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Zap className="w-4 h-4 text-[#ef4444]" />
        <div>
          <h1 className="text-base font-bold text-[#e2e8f0]">TIMING ENGINE</h1>
          <p className="text-[11px] text-[#4a5568]">매크로 레이더 · K-means 시장 국면 · 페어 트레이딩 · 평균회귀 · 모멘텀</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-[#060b14] border border-[#1e2d40] rounded-lg p-1 overflow-x-auto">
        {TABS.map((t, i) => (
          <button key={t} onClick={() => setTab(i)}
            className={cn('flex-shrink-0 py-2 px-3 text-[10px] font-bold tracking-wider rounded transition-colors whitespace-nowrap',
              tab === i ? 'bg-[#ef4444] text-white' : 'text-[#64748b] hover:text-[#e2e8f0]'
            )}>
            {t}
          </button>
        ))}
      </div>

      {/* Tab 0: Doom Radar */}
      {tab === 0 && (
        <div className={cn('bg-[#060b14] border rounded-lg p-4', doomQ.data?.is_doom ? 'border-[#ef4444]/40' : 'border-[#1e2d40]')}>
          {doomQ.isLoading && <LoadingSpinner size="sm" text="매크로 조건 분석 중..." />}
          {doomQ.data && (
            <div className="space-y-4">
              <div className="flex items-center gap-4">
                {doomQ.data.is_doom
                  ? <div className="w-14 h-14 rounded-full bg-[#ef4444]/20 border border-[#ef4444]/40 flex items-center justify-center"><AlertTriangle className="w-7 h-7 text-[#ef4444]" /></div>
                  : <div className="w-14 h-14 rounded-full bg-[#10b981]/20 border border-[#10b981]/40 flex items-center justify-center"><ShieldCheck className="w-7 h-7 text-[#10b981]" /></div>
                }
                <div>
                  <div className={cn('text-lg font-bold', doomQ.data.is_doom ? 'text-[#ef4444]' : 'text-[#10b981]')}>
                    {doomQ.data.is_doom ? 'DOOM CONDITIONS ACTIVE' : 'CONDITIONS NORMAL'}
                  </div>
                  <div className="text-sm text-[#64748b] mt-0.5">{doomQ.data.comment}</div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3">
                {[
                  { label: 'SEVERITY', value: `${doomQ.data.severity ?? 0}/5`, color: doomQ.data.severity >= 4 ? '#ef4444' : doomQ.data.severity >= 3 ? '#f59e0b' : '#10b981' },
                  { label: 'RATE SPREAD', value: `${doomQ.data.rate_spread?.toFixed(2)}%p`, color: '#e2e8f0' },
                  { label: 'HY SPREAD', value: `${doomQ.data.hy_spread?.toFixed(0)} bps`, color: '#e2e8f0' },
                ].map(item => (
                  <div key={item.label} className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3 text-center">
                    <div className="text-[10px] text-[#4a5568] mb-1">{item.label}</div>
                    <div className="text-xl font-mono font-bold" style={{ color: item.color }}>{item.value}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tab 1: Market Regime */}
      {tab === 1 && (
        <div className="space-y-3">
          <div className="flex gap-2 flex-wrap">
            <input value={regimeTicker} onChange={e => setRegimeTicker(e.target.value.toUpperCase())} onKeyDown={e => { if (e.key === 'Enter') setActiveRegimeTicker(regimeTicker) }}
              placeholder="^GSPC"
              className="w-32 bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#ef4444]" />
            {['1y', '2y', '3y'].map(p => (
              <button key={p} onClick={() => setRegimePeriod(p)}
                className={cn('px-3 py-2 text-xs rounded', regimePeriod === p ? 'bg-[#ef4444] text-white' : 'bg-[#060b14] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]')}>
                {p}
              </button>
            ))}
            <button onClick={() => setActiveRegimeTicker(regimeTicker)}
              className="flex items-center gap-1 px-3 py-2 bg-[#ef4444] text-white text-xs rounded hover:bg-[#dc2626]">
              <Search className="w-3.5 h-3.5" /> ANALYZE
            </button>
          </div>

          {regimeQ.isLoading && <LoadingSpinner className="h-32" text="K-means 시장 국면 분류 중..." />}
          {regimeQ.data && (
            <div className="space-y-3">
              {/* Current Regime */}
              <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3 flex items-center justify-between">
                <div>
                  <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-1">CURRENT REGIME</div>
                  <div className={cn('text-2xl font-bold font-mono',
                    regimeQ.data.current_regime === 'Bull' ? 'text-[#10b981]' :
                    regimeQ.data.current_regime === 'Bear' ? 'text-[#ef4444]' : 'text-[#f59e0b]'
                  )}>{regimeQ.data.current_regime?.toUpperCase()}</div>
                </div>
                <div className="flex gap-4">
                  <PieChart width={100} height={100}>
                    <Pie data={regimePieData} cx={50} cy={50} innerRadius={25} outerRadius={40} dataKey="value">
                      {regimePieData.map((entry, i) => <Cell key={i} fill={entry.color} />)}
                    </Pie>
                    <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: any) => [`${v.toFixed(1)}%`, '']} />
                  </PieChart>
                  <div className="space-y-1 text-xs">
                    {regimePieData.map(r => (
                      <div key={r.name} className="flex items-center gap-2">
                        <div className="w-2 h-2 rounded-sm" style={{ backgroundColor: r.color }} />
                        <span className="text-[#64748b]">{r.name}</span>
                        <span className="font-mono font-bold" style={{ color: r.color }}>{r.value.toFixed(1)}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Regime Chart */}
              {regimeQ.data.chart_data && regimeQ.data.chart_data.length > 0 && (
                <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3">
                  <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-2">PRICE + REGIME OVERLAY</div>
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={regimeQ.data.chart_data}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#0f172a" vertical={false} />
                      <XAxis dataKey="date" tick={{ fill: '#374151', fontSize: 9 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                      <YAxis tick={{ fill: '#374151', fontSize: 9 }} tickLine={false} axisLine={false} width={50} />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Line type="monotone" dataKey="price" name="Price" stroke="#3b82f6" strokeWidth={1.5} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Tab 2: Signal Scan */}
      {tab === 2 && (
        <div className="space-y-3">
          <div className="flex gap-2">
            <button onClick={() => scanMut.mutate()} disabled={scanMut.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ef4444]/10 border border-[#ef4444]/30 text-[#ef4444] text-xs rounded hover:bg-[#ef4444]/20 disabled:opacity-50">
              <RefreshCw className="w-3 h-3" />
              {scanMut.isPending ? '스캔 중...' : 'RUN FULL SCAN'}
            </button>
          </div>
          {(scanQ.isLoading || scanMut.isPending) && <LoadingSpinner className="h-24" text="시그널 스캐닝..." />}
          {scanQ.data && (
            <div className="space-y-3">
              <div className="flex gap-4 text-xs text-[#64748b]">
                <span>스캔: <span className="text-[#e2e8f0] font-mono">{scanQ.data.scanned}</span> 종목</span>
                <span>LONG: <span className="text-[#10b981] font-mono">{scanQ.data.long_picks?.length ?? 0}</span></span>
                <span>SHORT: <span className="text-[#ef4444] font-mono">{scanQ.data.short_picks?.length ?? 0}</span></span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead><tr className="border-b border-[#1e2d40]">
                    {['Ticker', 'Method', 'Score', 'Entry', 'Target', 'Stop', 'Upside', 'Reason'].map(h => (
                      <th key={h} className="py-2 px-2 text-left text-[10px] font-bold text-[#4a5568]">{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {[...(scanQ.data.long_picks || []).map(p => ({ ...p, side: 'long' })), ...(scanQ.data.short_picks || []).map(p => ({ ...p, side: 'short' }))].map((pick, i) => (
                      <tr key={i} className="border-b border-[#0f172a] hover:bg-[#0a1628]">
                        <td className="py-2 px-2 font-mono font-bold text-[#e2e8f0]">{pick.ticker}</td>
                        <td className="py-2 px-2 text-[#64748b]">{pick.method}</td>
                        <td className="py-2 px-2 font-mono text-[#f59e0b]">{pick.score?.toFixed(2) ?? '—'}</td>
                        <td className="py-2 px-2 font-mono text-[#e2e8f0]">{formatCurrency(pick.entry)}</td>
                        <td className="py-2 px-2 font-mono text-[#10b981]">{formatCurrency(pick.target)}</td>
                        <td className="py-2 px-2 font-mono text-[#ef4444]">{formatCurrency(pick.stop)}</td>
                        <td className={cn('py-2 px-2 font-mono', pick.side === 'long' ? 'text-[#10b981]' : 'text-[#ef4444]')}>
                          {pick.side === 'long' ? '+' : ''}{((pick.upside ?? 0) * 100).toFixed(1)}%
                        </td>
                        <td className="py-2 px-2 text-[#4a5568] max-w-[160px] truncate">{pick.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tab 3: Mean Reversion */}
      {tab === 3 && (
        <div className="space-y-3">
          <div className="flex gap-2">
            <input type="text" placeholder="AAPL" value={lookupTicker} onChange={e => setLookupTicker(e.target.value.toUpperCase())}
              onKeyDown={e => { if (e.key === 'Enter' && lookupTicker) setActiveLookup(lookupTicker) }}
              className="w-32 bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#ef4444]" />
            <button onClick={() => setActiveLookup(lookupTicker)} disabled={!lookupTicker}
              className="flex items-center gap-1 px-3 py-2 bg-[#ef4444] text-white text-xs rounded hover:bg-[#dc2626] disabled:opacity-50">
              <Search className="w-3.5 h-3.5" /> ANALYZE
            </button>
          </div>
          {mrQ.isLoading && <LoadingSpinner className="h-24" text={`${activeLookup} 분석 중...`} />}
          {mrQ.data && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-[#060b14] border border-[#1e2d40] rounded p-3 space-y-2 text-sm">
                <div className="flex items-center justify-between"><span className="text-[#64748b] text-xs">신호</span><SignalBadge signal={mrQ.data.current_signal} /></div>
                <div className="flex items-center justify-between"><span className="text-[#64748b] text-xs">현재가</span><span className="font-mono text-[#e2e8f0]">{formatCurrency(mrQ.data.current_price)}</span></div>
                <div className="flex items-center justify-between"><span className="text-[#64748b] text-xs">Z-Score</span><span className={cn('font-mono', Math.abs(mrQ.data.current_z) > 2 ? colorForValue(-mrQ.data.current_z) : 'text-[#e2e8f0]')}>{mrQ.data.current_z?.toFixed(2)}</span></div>
                <div className="flex items-center justify-between"><span className="text-[#64748b] text-xs">%B (볼린저)</span><span className="font-mono text-[#e2e8f0]">{mrQ.data.pct_b?.toFixed(2)}</span></div>
                <div className="grid grid-cols-3 gap-2 pt-1 text-[10px] border-t border-[#1e2d40]">
                  <div className="text-center"><div className="text-[#ef4444]">Upper</div><div className="font-mono text-[#e2e8f0]">{formatCurrency(mrQ.data.upper_band)}</div></div>
                  <div className="text-center"><div className="text-[#64748b]">Mid</div><div className="font-mono text-[#e2e8f0]">{formatCurrency(mrQ.data.mid_band)}</div></div>
                  <div className="text-center"><div className="text-[#10b981]">Lower</div><div className="font-mono text-[#e2e8f0]">{formatCurrency(mrQ.data.lower_band)}</div></div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tab 4: Momentum */}
      {tab === 4 && (
        <div className="space-y-3">
          <div className="flex gap-2">
            <input type="text" placeholder="AAPL" value={lookupTicker} onChange={e => setLookupTicker(e.target.value.toUpperCase())}
              onKeyDown={e => { if (e.key === 'Enter' && lookupTicker) setActiveLookup(lookupTicker) }}
              className="w-32 bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#ef4444]" />
            <button onClick={() => setActiveLookup(lookupTicker)} disabled={!lookupTicker}
              className="flex items-center gap-1 px-3 py-2 bg-[#ef4444] text-white text-xs rounded hover:bg-[#dc2626] disabled:opacity-50">
              <Search className="w-3.5 h-3.5" /> ANALYZE
            </button>
          </div>
          {momQ.isLoading && <LoadingSpinner className="h-24" text={`${activeLookup} 모멘텀 분석 중...`} />}
          {momQ.data && (
            <div className="bg-[#060b14] border border-[#1e2d40] rounded p-3 space-y-2 text-sm">
              <div className="flex items-center justify-between"><span className="text-[#64748b] text-xs">신호</span><SignalBadge signal={momQ.data.current_signal} /></div>
              <div className="flex items-center justify-between"><span className="text-[#64748b] text-xs">현재가</span><span className="font-mono text-[#e2e8f0]">{formatCurrency(momQ.data.current_price)}</span></div>
              <div className="flex items-center justify-between"><span className="text-[#64748b] text-xs">저항선</span><span className="font-mono text-[#f59e0b]">{formatCurrency(momQ.data.resistance)}</span></div>
              <div className="flex items-center justify-between"><span className="text-[#64748b] text-xs">볼륨 비율</span><span className={cn('font-mono', momQ.data.volume_ratio > 1.5 ? 'text-[#f59e0b]' : 'text-[#e2e8f0]')}>{momQ.data.volume_ratio?.toFixed(2)}x</span></div>
              <div className="flex gap-2 pt-1">
                <span className={cn('text-[10px] px-2 py-0.5 rounded', momQ.data.is_breakout_today ? 'bg-[#10b981]/10 text-[#10b981]' : 'bg-[#64748b]/10 text-[#64748b]')}>
                  {momQ.data.is_breakout_today ? '🚀 오늘 브레이크아웃' : '브레이크아웃 없음'}
                </span>
                <span className={cn('text-[10px] px-2 py-0.5 rounded', momQ.data.volume_surge ? 'bg-[#f59e0b]/10 text-[#f59e0b]' : 'bg-[#64748b]/10 text-[#64748b]')}>
                  {momQ.data.volume_surge ? '📈 볼륨 급등' : '정상 볼륨'}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tab 5: Pairs Trading */}
      {tab === 5 && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <input type="text" placeholder="Ticker A (GLD)" value={pairA} onChange={e => setPairA(e.target.value.toUpperCase())}
              className="w-32 bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#ef4444]" />
            <input type="text" placeholder="Ticker B (SLV)" value={pairB} onChange={e => setPairB(e.target.value.toUpperCase())}
              className="w-32 bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#ef4444]" />
            <select value={pairPeriod} onChange={e => setPairPeriod(e.target.value)}
              className="bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-xs text-[#e2e8f0]">
              <option value="6mo">6개월</option><option value="1y">1년</option><option value="2y">2년</option>
            </select>
            <button onClick={() => setActivePair({ a: pairA, b: pairB, period: pairPeriod })} disabled={!pairA || !pairB}
              className="flex items-center gap-1 px-3 py-2 bg-[#ef4444] text-white text-xs rounded hover:bg-[#dc2626] disabled:opacity-50">
              <Search className="w-3.5 h-3.5" /> ANALYZE PAIR
            </button>
          </div>
          {pairsQ.isLoading && <LoadingSpinner className="h-24" text="페어 관계 분석 중..." />}
          {pairsQ.isError && <ErrorMessage message="페어 분석 실패" retry={pairsQ.refetch} />}
          {pairsQ.data && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {[
                { label: '신호', value: <SignalBadge signal={pairsQ.data.current_signal} /> },
                { label: 'Z-Score', value: <span className={cn('text-xl font-mono font-bold', Math.abs(pairsQ.data.current_z) > 2 ? 'text-[#f59e0b]' : 'text-[#e2e8f0]')}>{pairsQ.data.current_z?.toFixed(2)}</span> },
                { label: '상관관계', value: <span className="text-xl font-mono font-bold text-[#e2e8f0]">{pairsQ.data.correlation?.toFixed(3)}</span> },
                { label: 'Beta', value: <span className="text-xl font-mono font-bold text-[#e2e8f0]">{pairsQ.data.beta?.toFixed(3)}</span> },
                { label: '유효 페어', value: <span className={cn('text-sm font-semibold', pairsQ.data.is_valid_pair ? 'text-[#10b981]' : 'text-[#ef4444]')}>{pairsQ.data.is_valid_pair ? 'YES' : 'NO'}</span> },
              ].map((item, i) => (
                <div key={i} className="bg-[#060b14] border border-[#1e2d40] rounded p-3 text-center">
                  <div className="text-[10px] text-[#4a5568] mb-1">{item.label}</div>
                  {item.value}
                </div>
              ))}
              {pairsQ.data.lock_message && (
                <div className="col-span-full text-xs text-[#f59e0b] bg-[#f59e0b]/5 border border-[#f59e0b]/20 rounded px-3 py-2">{pairsQ.data.lock_message}</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
