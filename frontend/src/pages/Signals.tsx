import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Zap, AlertTriangle, ShieldCheck, RefreshCw, Search } from 'lucide-react'
import LoadingSpinner, { ErrorMessage } from '@/components/LoadingSpinner'
import {
  getSignalsDoomRadar,
  getCachedScan,
  runSignalScan,
  getMeanReversionSignal,
  getMomentumSignal,
  getPairsSignal,
} from '@/api'
import { formatCurrency, colorForValue, cn } from '@/lib/utils'

function SignalBadge({ signal }: { signal: string }) {
  const upper = signal?.toUpperCase() ?? ''
  let color = 'bg-[#64748b]/10 text-[#64748b]'
  if (upper.includes('BUY') || upper.includes('LONG') || upper.includes('OVERSOLD'))
    color = 'bg-[#10b981]/10 text-[#10b981]'
  else if (upper.includes('SELL') || upper.includes('SHORT') || upper.includes('OVERBOUGHT'))
    color = 'bg-[#ef4444]/10 text-[#ef4444]'
  else if (upper.includes('NEUTRAL') || upper.includes('HOLD'))
    color = 'bg-[#f59e0b]/10 text-[#f59e0b]'
  return (
    <span className={cn('text-xs px-2 py-0.5 rounded font-medium', color)}>{signal}</span>
  )
}

export default function Signals() {
  const qc = useQueryClient()
  const [signalTicker, setSignalTicker] = useState('')
  const [lookupTicker, setLookupTicker] = useState('')
  const [pairA, setPairA] = useState('')
  const [pairB, setPairB] = useState('')
  const [pairPeriod, setPairPeriod] = useState('1y')
  const [activePairTickers, setActivePairTickers] = useState({ a: '', b: '', period: '1y' })
  const [activeLookup, setActiveLookup] = useState('')

  const doomQ = useQuery({ queryKey: ['signals-doom'], queryFn: getSignalsDoomRadar })
  const cachedScanQ = useQuery({ queryKey: ['cached-scan'], queryFn: getCachedScan })

  const scanMutation = useMutation({
    mutationFn: () => runSignalScan(10),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cached-scan'] }),
  })

  const mrQ = useQuery({
    queryKey: ['mean-reversion', activeLookup],
    queryFn: () => getMeanReversionSignal(activeLookup),
    enabled: activeLookup.length > 0,
  })

  const momQ = useQuery({
    queryKey: ['momentum', activeLookup],
    queryFn: () => getMomentumSignal(activeLookup),
    enabled: activeLookup.length > 0,
  })

  const pairsQ = useQuery({
    queryKey: ['pairs', activePairTickers.a, activePairTickers.b, activePairTickers.period],
    queryFn: () =>
      getPairsSignal(activePairTickers.a, activePairTickers.b, activePairTickers.period),
    enabled: activePairTickers.a.length > 0 && activePairTickers.b.length > 0,
  })

  const scan = cachedScanQ.data

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-[#e2e8f0]">Signals</h1>
        <p className="text-sm text-[#64748b] mt-0.5">Doom radar, signal scanner &amp; strategy analysis</p>
      </div>

      {/* Doom Radar Panel */}
      <div
        className={cn(
          'bg-[#111827] border rounded-lg p-5',
          doomQ.data?.is_doom ? 'border-[#ef4444]/40' : 'border-[#1e2d40]'
        )}
      >
        <div className="flex items-center gap-2 mb-4">
          <Zap className="w-4 h-4 text-[#f59e0b]" />
          <h2 className="text-sm font-semibold text-[#e2e8f0]">Doom Radar</h2>
        </div>

        {doomQ.isLoading && <LoadingSpinner size="sm" text="Analyzing macro conditions..." />}
        {doomQ.isError && (
          <ErrorMessage message="Failed to load doom radar" retry={doomQ.refetch} />
        )}
        {doomQ.data && (
          <div className="flex flex-col md:flex-row items-start md:items-center gap-4">
            <div className="flex items-center gap-3">
              {doomQ.data.is_doom ? (
                <div className="w-12 h-12 rounded-full bg-[#ef4444]/20 border border-[#ef4444]/40 flex items-center justify-center">
                  <AlertTriangle className="w-6 h-6 text-[#ef4444]" />
                </div>
              ) : (
                <div className="w-12 h-12 rounded-full bg-[#10b981]/20 border border-[#10b981]/40 flex items-center justify-center">
                  <ShieldCheck className="w-6 h-6 text-[#10b981]" />
                </div>
              )}
              <div>
                <div
                  className={cn(
                    'text-base font-bold',
                    doomQ.data.is_doom ? 'text-[#ef4444]' : 'text-[#10b981]'
                  )}
                >
                  {doomQ.data.is_doom ? 'DOOM CONDITIONS ACTIVE' : 'CONDITIONS NORMAL'}
                </div>
                <div className="text-sm text-[#64748b] mt-0.5">{doomQ.data.comment}</div>
              </div>
            </div>

            <div className="flex flex-wrap gap-4 ml-auto">
              <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg px-4 py-2 text-center">
                <div className="text-xs text-[#64748b] mb-1">Severity</div>
                <div
                  className={cn(
                    'text-xl font-mono font-bold',
                    doomQ.data.severity >= 4
                      ? 'text-[#ef4444]'
                      : doomQ.data.severity >= 3
                      ? 'text-[#f59e0b]'
                      : 'text-[#10b981]'
                  )}
                >
                  {doomQ.data.severity ?? 0}/5
                </div>
              </div>
              <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg px-4 py-2 text-center">
                <div className="text-xs text-[#64748b] mb-1">Rate Spread</div>
                <div className="text-xl font-mono font-bold text-[#e2e8f0]">
                  {doomQ.data.rate_spread?.toFixed(2) ?? '—'}%
                </div>
              </div>
              <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg px-4 py-2 text-center">
                <div className="text-xs text-[#64748b] mb-1">HY Spread</div>
                <div className="text-xl font-mono font-bold text-[#e2e8f0]">
                  {doomQ.data.hy_spread?.toFixed(0) ?? '—'} bps
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Signal Scanner */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg">
        <div className="px-4 py-3 border-b border-[#1e2d40] flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[#e2e8f0]">Signal Scanner</h2>
          <button
            onClick={() => scanMutation.mutate()}
            disabled={scanMutation.isPending}
            className="flex items-center gap-2 px-3 py-1.5 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-xs rounded transition-colors"
          >
            {scanMutation.isPending ? (
              <LoadingSpinner size="sm" />
            ) : (
              <RefreshCw className="w-3.5 h-3.5" />
            )}
            Run Full Scan
          </button>
        </div>

        {(cachedScanQ.isLoading || scanMutation.isPending) && (
          <LoadingSpinner className="p-10" text="Scanning market signals..." />
        )}

        {scan && (
          <div className="p-4 space-y-4">
            <div className="flex items-center gap-4 text-xs text-[#64748b]">
              <span>Scanned: <span className="text-[#e2e8f0] font-mono">{scan.scanned}</span> tickers</span>
              <span>Long picks: <span className="text-[#10b981] font-mono">{scan.long_picks?.length ?? 0}</span></span>
              <span>Short picks: <span className="text-[#ef4444] font-mono">{scan.short_picks?.length ?? 0}</span></span>
            </div>

            {/* Long Picks */}
            {scan.long_picks && scan.long_picks.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-[#10b981] uppercase tracking-wider mb-2">
                  Long Picks
                </h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-[#1e2d40]">
                        {['Ticker', 'Method', 'Score', 'Entry', 'Target', 'Stop', 'Upside', 'Reason'].map((col) => (
                          <th
                            key={col}
                            className={cn(
                              'py-2 px-3 text-[#64748b] font-medium uppercase tracking-wider whitespace-nowrap',
                              col === 'Ticker' || col === 'Method' || col === 'Reason' ? 'text-left' : 'text-right'
                            )}
                          >
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {scan.long_picks.map((pick, i) => (
                        <tr
                          key={pick.ticker}
                          className={cn(
                            'border-b border-[#1e2d40]/30 hover:bg-[#1a2540] transition-colors',
                            i % 2 === 1 ? 'bg-[#0f1724]' : ''
                          )}
                        >
                          <td className="py-2 px-3 font-mono font-semibold text-[#e2e8f0]">
                            {pick.ticker}
                          </td>
                          <td className="py-2 px-3 text-[#64748b]">{pick.method}</td>
                          <td className="py-2 px-3 text-right font-mono text-[#f59e0b]">
                            {pick.score?.toFixed(2) ?? '—'}
                          </td>
                          <td className="py-2 px-3 text-right font-mono text-[#e2e8f0]">
                            {formatCurrency(pick.entry)}
                          </td>
                          <td className="py-2 px-3 text-right font-mono text-[#10b981]">
                            {formatCurrency(pick.target)}
                          </td>
                          <td className="py-2 px-3 text-right font-mono text-[#ef4444]">
                            {formatCurrency(pick.stop)}
                          </td>
                          <td className="py-2 px-3 text-right font-mono text-[#10b981]">
                            +{(pick.upside * 100).toFixed(1)}%
                          </td>
                          <td className="py-2 px-3 text-[#64748b] max-w-48 truncate">{pick.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Short Picks */}
            {scan.short_picks && scan.short_picks.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-[#ef4444] uppercase tracking-wider mb-2">
                  Short Picks
                </h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-[#1e2d40]">
                        {['Ticker', 'Method', 'Score', 'Entry', 'Target', 'Stop', 'Upside', 'Reason'].map((col) => (
                          <th
                            key={col}
                            className={cn(
                              'py-2 px-3 text-[#64748b] font-medium uppercase tracking-wider whitespace-nowrap',
                              col === 'Ticker' || col === 'Method' || col === 'Reason' ? 'text-left' : 'text-right'
                            )}
                          >
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {scan.short_picks.map((pick, i) => (
                        <tr
                          key={pick.ticker}
                          className={cn(
                            'border-b border-[#1e2d40]/30 hover:bg-[#1a2540] transition-colors',
                            i % 2 === 1 ? 'bg-[#0f1724]' : ''
                          )}
                        >
                          <td className="py-2 px-3 font-mono font-semibold text-[#e2e8f0]">
                            {pick.ticker}
                          </td>
                          <td className="py-2 px-3 text-[#64748b]">{pick.method}</td>
                          <td className="py-2 px-3 text-right font-mono text-[#f59e0b]">
                            {pick.score?.toFixed(2) ?? '—'}
                          </td>
                          <td className="py-2 px-3 text-right font-mono text-[#e2e8f0]">
                            {formatCurrency(pick.entry)}
                          </td>
                          <td className="py-2 px-3 text-right font-mono text-[#ef4444]">
                            {formatCurrency(pick.target)}
                          </td>
                          <td className="py-2 px-3 text-right font-mono text-[#10b981]">
                            {formatCurrency(pick.stop)}
                          </td>
                          <td className="py-2 px-3 text-right font-mono text-[#ef4444]">
                            {(pick.upside * 100).toFixed(1)}%
                          </td>
                          <td className="py-2 px-3 text-[#64748b] max-w-48 truncate">{pick.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Individual Signal Lookup */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-4">Individual Signal Lookup</h2>

        <div className="flex gap-2 mb-4">
          <input
            type="text"
            placeholder="Enter ticker (e.g. AAPL)"
            value={signalTicker}
            onChange={(e) => setSignalTicker(e.target.value.toUpperCase())}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && signalTicker) setLookupTicker(signalTicker)
            }}
            className="flex-1 bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6] uppercase"
          />
          <button
            onClick={() => setLookupTicker(signalTicker)}
            disabled={!signalTicker}
            className="flex items-center gap-2 px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm rounded transition-colors"
          >
            <Search className="w-4 h-4" />
            Analyze
          </button>
        </div>

        {(mrQ.isLoading || momQ.isLoading) && lookupTicker && (
          <LoadingSpinner className="h-32" text={`Analyzing ${lookupTicker}...`} />
        )}

        {lookupTicker && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Mean Reversion */}
            <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-4">
              <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider mb-3">
                Mean Reversion
              </h3>
              {mrQ.isError && (
                <p className="text-xs text-[#ef4444]">Failed to load signal</p>
              )}
              {mrQ.data && (
                <div className="space-y-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="text-[#64748b]">Signal</span>
                    <SignalBadge signal={mrQ.data.current_signal} />
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[#64748b]">Current Price</span>
                    <span className="font-mono text-[#e2e8f0]">
                      {formatCurrency(mrQ.data.current_price)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[#64748b]">Z-Score</span>
                    <span
                      className={cn(
                        'font-mono',
                        Math.abs(mrQ.data.current_z) > 2 ? colorForValue(-mrQ.data.current_z) : 'text-[#e2e8f0]'
                      )}
                    >
                      {mrQ.data.current_z?.toFixed(2) ?? '—'}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[#64748b]">%B (Bollinger)</span>
                    <span className="font-mono text-[#e2e8f0]">
                      {mrQ.data.pct_b?.toFixed(2) ?? '—'}
                    </span>
                  </div>
                  <div className="border-t border-[#1e2d40] pt-2 grid grid-cols-3 gap-2 text-xs">
                    <div className="text-center">
                      <div className="text-[#64748b]">Upper Band</div>
                      <div className="font-mono text-[#ef4444]">
                        {formatCurrency(mrQ.data.upper_band)}
                      </div>
                    </div>
                    <div className="text-center">
                      <div className="text-[#64748b]">Mid Band</div>
                      <div className="font-mono text-[#e2e8f0]">
                        {formatCurrency(mrQ.data.mid_band)}
                      </div>
                    </div>
                    <div className="text-center">
                      <div className="text-[#64748b]">Lower Band</div>
                      <div className="font-mono text-[#10b981]">
                        {formatCurrency(mrQ.data.lower_band)}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Momentum */}
            <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-4">
              <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider mb-3">
                Momentum
              </h3>
              {momQ.isError && (
                <p className="text-xs text-[#ef4444]">Failed to load signal</p>
              )}
              {momQ.data && (
                <div className="space-y-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="text-[#64748b]">Signal</span>
                    <SignalBadge signal={momQ.data.current_signal} />
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[#64748b]">Current Price</span>
                    <span className="font-mono text-[#e2e8f0]">
                      {formatCurrency(momQ.data.current_price)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[#64748b]">Resistance</span>
                    <span className="font-mono text-[#f59e0b]">
                      {formatCurrency(momQ.data.resistance)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[#64748b]">Volume Ratio</span>
                    <span
                      className={cn(
                        'font-mono',
                        momQ.data.volume_ratio > 1.5 ? 'text-[#f59e0b]' : 'text-[#e2e8f0]'
                      )}
                    >
                      {momQ.data.volume_ratio?.toFixed(2) ?? '—'}x
                    </span>
                  </div>
                  <div className="flex gap-3 pt-1">
                    <span
                      className={cn(
                        'text-xs px-2 py-0.5 rounded',
                        momQ.data.is_breakout_today
                          ? 'bg-[#10b981]/10 text-[#10b981]'
                          : 'bg-[#64748b]/10 text-[#64748b]'
                      )}
                    >
                      {momQ.data.is_breakout_today ? '🚀 Breakout Today' : 'No Breakout'}
                    </span>
                    <span
                      className={cn(
                        'text-xs px-2 py-0.5 rounded',
                        momQ.data.volume_surge
                          ? 'bg-[#f59e0b]/10 text-[#f59e0b]'
                          : 'bg-[#64748b]/10 text-[#64748b]'
                      )}
                    >
                      {momQ.data.volume_surge ? '📈 Volume Surge' : 'Normal Volume'}
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Pairs Trading */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-4">Pairs Trading</h2>

        <div className="flex flex-wrap gap-2 mb-4">
          <input
            type="text"
            placeholder="Ticker A (e.g. GLD)"
            value={pairA}
            onChange={(e) => setPairA(e.target.value.toUpperCase())}
            className="w-36 bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6] uppercase"
          />
          <input
            type="text"
            placeholder="Ticker B (e.g. SLV)"
            value={pairB}
            onChange={(e) => setPairB(e.target.value.toUpperCase())}
            className="w-36 bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6] uppercase"
          />
          <select
            value={pairPeriod}
            onChange={(e) => setPairPeriod(e.target.value)}
            className="bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
          >
            <option value="6mo">6 months</option>
            <option value="1y">1 year</option>
            <option value="2y">2 years</option>
          </select>
          <button
            onClick={() =>
              setActivePairTickers({ a: pairA, b: pairB, period: pairPeriod })
            }
            disabled={!pairA || !pairB}
            className="flex items-center gap-2 px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm rounded transition-colors"
          >
            <Search className="w-4 h-4" />
            Analyze Pair
          </button>
        </div>

        {pairsQ.isLoading && (
          <LoadingSpinner className="h-24" text="Analyzing pair relationship..." />
        )}
        {pairsQ.isError && (
          <ErrorMessage message="Failed to analyze pair" retry={pairsQ.refetch} />
        )}
        {pairsQ.data && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3 text-center">
              <div className="text-xs text-[#64748b] mb-1">Signal</div>
              <SignalBadge signal={pairsQ.data.current_signal} />
            </div>
            <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3 text-center">
              <div className="text-xs text-[#64748b] mb-1">Z-Score</div>
              <div
                className={cn(
                  'text-lg font-mono font-bold',
                  Math.abs(pairsQ.data.current_z) > 2
                    ? 'text-[#f59e0b]'
                    : 'text-[#e2e8f0]'
                )}
              >
                {pairsQ.data.current_z?.toFixed(2) ?? '—'}
              </div>
            </div>
            <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3 text-center">
              <div className="text-xs text-[#64748b] mb-1">Correlation</div>
              <div className="text-lg font-mono font-bold text-[#e2e8f0]">
                {pairsQ.data.correlation?.toFixed(3) ?? '—'}
              </div>
            </div>
            <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3 text-center">
              <div className="text-xs text-[#64748b] mb-1">Beta</div>
              <div className="text-lg font-mono font-bold text-[#e2e8f0]">
                {pairsQ.data.beta?.toFixed(3) ?? '—'}
              </div>
            </div>
            <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3 text-center">
              <div className="text-xs text-[#64748b] mb-1">Valid Pair</div>
              <div
                className={cn(
                  'text-sm font-semibold',
                  pairsQ.data.is_valid_pair ? 'text-[#10b981]' : 'text-[#ef4444]'
                )}
              >
                {pairsQ.data.is_valid_pair ? 'Yes' : 'No'}
              </div>
            </div>
            {pairsQ.data.lock_message && (
              <div className="col-span-full text-xs text-[#f59e0b] bg-[#f59e0b]/5 border border-[#f59e0b]/20 rounded px-3 py-2">
                {pairsQ.data.lock_message}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
