import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  LineChart, Line, ReferenceLine,
} from 'recharts'
import { Dice5 } from 'lucide-react'
import LoadingSpinner, { ErrorMessage } from '@/components/LoadingSpinner'
import {
  runMonteCarloMacro, runMonteCarloPortfolio, runMonteCarloStock,
} from '@/api'
import type { MonteCarloMacroResult, MonteCarloPortfolioResult, MonteCarloStockResult } from '@/types'
import { formatCurrency, cn } from '@/lib/utils'

const TOOLTIP_STYLE = { backgroundColor: '#0f172a', border: '1px solid #1e2d40', borderRadius: 4, color: '#e2e8f0', fontSize: 11 }

const MACRO_PRESETS = [
  { label: '기준', key: 'baseline', fed_shock: 0, vix_mult: 1.0, vol_adj: 0, drift_adj: 0, credit_spread: 0, fx_shock: 0 },
  { label: '매파적 Fed', key: 'fed_hike', fed_shock: 0.5, vix_mult: 1.5, vol_adj: 0.03, drift_adj: -0.03, credit_spread: 0.5, fx_shock: 0 },
  { label: '은행 위기', key: 'bank_crisis', fed_shock: -0.25, vix_mult: 2.5, vol_adj: 0.08, drift_adj: -0.08, credit_spread: 3.0, fx_shock: 0 },
  { label: '스태그플레이션', key: 'stagflation', fed_shock: 0.25, vix_mult: 2.0, vol_adj: 0.05, drift_adj: -0.04, credit_spread: 1.5, fx_shock: 0.05 },
  { label: '피벗 랠리', key: 'pivot_rally', fed_shock: -0.5, vix_mult: 0.7, vol_adj: -0.02, drift_adj: 0.05, credit_spread: -0.5, fx_shock: -0.03 },
]

function ProbabilityGauge({ probability }: { probability: number }) {
  const pct = Math.max(0, Math.min(100, probability * 100))
  const color = pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#ef4444'
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="w-28 h-28 rounded-full border-8 flex items-center justify-center" style={{ borderColor: color, boxShadow: `0 0 20px ${color}30` }}>
        <div className="text-center">
          <div className="text-2xl font-mono font-bold" style={{ color }}>{pct.toFixed(0)}%</div>
          <div className="text-[10px] text-[#64748b]">달성 확률</div>
        </div>
      </div>
    </div>
  )
}

// ── Tab 1: Macro Scenario (Jump-Diffusion) ────────────────────────────────────
function MacroTab() {
  const [preset, setPreset] = useState(MACRO_PRESETS[0])
  const [shocks, setShocks] = useState({
    fed_shock: 0, vix_mult: 1.0, vol_adj: 0, drift_adj: 0, credit_spread: 0, fx_shock: 0,
  })
  const [sims, setSims] = useState(1000)
  const [result, setResult] = useState<MonteCarloMacroResult | null>(null)

  const applyPreset = (p: typeof MACRO_PRESETS[0]) => {
    setPreset(p)
    setShocks({ fed_shock: p.fed_shock, vix_mult: p.vix_mult, vol_adj: p.vol_adj, drift_adj: p.drift_adj, credit_spread: p.credit_spread, fx_shock: p.fx_shock })
  }

  const mut = useMutation({
    mutationFn: () => runMonteCarloMacro({ ...shocks, n_simulations: sims }),
    onSuccess: setResult,
  })

  const sliders: { key: keyof typeof shocks; label: string; min: number; max: number; step: number; fmt: (v: number) => string }[] = [
    { key: 'fed_shock', label: 'Fed Rate 충격 (%p)', min: -1, max: 1, step: 0.05, fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%p` },
    { key: 'vix_mult', label: 'VIX 배율', min: 0.5, max: 4, step: 0.1, fmt: v => `×${v.toFixed(1)}` },
    { key: 'vol_adj', label: '변동성 조정 (%)', min: -0.1, max: 0.2, step: 0.01, fmt: v => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%` },
    { key: 'drift_adj', label: 'Drift 조정 (%)', min: -0.15, max: 0.1, step: 0.01, fmt: v => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%` },
    { key: 'credit_spread', label: 'Credit Spread (%p)', min: -2, max: 5, step: 0.25, fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%p` },
    { key: 'fx_shock', label: 'FX 충격 (%)', min: -0.15, max: 0.15, step: 0.01, fmt: v => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%` },
  ]

  const histData = (result?.histogram || []).map((count, i) => ({ bin: i, count }))
  const pathData = (result?.paths || []).slice(0, 10)

  return (
    <div className="space-y-4">
      {/* Presets */}
      <div className="flex flex-wrap gap-2">
        {MACRO_PRESETS.map(p => (
          <button key={p.key} onClick={() => applyPreset(p)}
            className={cn('px-3 py-1.5 text-xs rounded font-medium transition-colors',
              preset.key === p.key ? 'bg-[#f59e0b] text-[#0b0f1a]' : 'bg-[#060b14] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]'
            )}>
            {p.label}
          </button>
        ))}
      </div>

      {/* Sliders */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {sliders.map(s => (
          <div key={s.key} className="bg-[#060b14] border border-[#1e2d40] rounded p-3">
            <div className="flex justify-between items-center mb-1.5">
              <span className="text-[10px] text-[#64748b]">{s.label}</span>
              <span className="text-xs font-mono font-bold text-[#e2e8f0]">{s.fmt(shocks[s.key])}</span>
            </div>
            <input type="range" min={s.min} max={s.max} step={s.step} value={shocks[s.key]}
              onChange={e => setShocks(prev => ({ ...prev, [s.key]: +e.target.value }))}
              className="w-full accent-[#f59e0b]" />
          </div>
        ))}
      </div>

      {/* Simulations + Run */}
      <div className="flex items-center gap-4">
        <div>
          <div className="text-[10px] text-[#4a5568] mb-1">SIMULATIONS</div>
          <select value={sims} onChange={e => setSims(+e.target.value)} className="bg-[#060b14] border border-[#1e2d40] text-xs text-[#e2e8f0] rounded px-2 py-1.5">
            <option value={500}>500</option><option value={1000}>1,000</option>
            <option value={3000}>3,000</option><option value={5000}>5,000</option>
          </select>
        </div>
        <button onClick={() => mut.mutate()} disabled={mut.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-[#f59e0b] hover:bg-[#d97706] disabled:opacity-50 text-[#0b0f1a] text-xs font-bold rounded transition-colors mt-4">
          <Dice5 className="w-3.5 h-3.5" />
          {mut.isPending ? '시뮬레이션 중...' : 'RUN SIMULATION'}
        </button>
      </div>

      {mut.isPending && <LoadingSpinner className="h-24" text="Jump-Diffusion 시뮬레이션 실행 중..." />}
      {mut.isError && <ErrorMessage message="시뮬레이션 실패" retry={() => mut.mutate()} />}

      {result && !mut.isPending && (
        <div className="space-y-4">
          {/* Key Metrics */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: '상승 확률', value: `${(result.probability_above_zero * 100).toFixed(1)}%`, color: result.probability_above_zero >= 0.5 ? '#10b981' : '#ef4444' },
              { label: 'VaR 95%', value: `${(result.var_95 * 100).toFixed(2)}%`, color: '#ef4444' },
              { label: 'CVaR 95%', value: `${(result.cvar_95 * 100).toFixed(2)}%`, color: '#ef4444' },
              { label: '기대 수익', value: `${(result.mean_return * 100).toFixed(2)}%`, color: result.mean_return >= 0 ? '#10b981' : '#ef4444' },
            ].map(item => (
              <div key={item.label} className="bg-[#060b14] border border-[#1e2d40] rounded p-3 text-center">
                <div className="text-[10px] text-[#4a5568] mb-1">{item.label}</div>
                <div className="text-xl font-mono font-bold" style={{ color: item.color }}>{item.value}</div>
              </div>
            ))}
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-2">RETURN DISTRIBUTION</div>
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={histData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#0f172a" vertical={false} />
                  <XAxis dataKey="bin" hide />
                  <YAxis hide />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Bar dataKey="count" name="빈도" radius={[1, 1, 0, 0]}>
                    {histData.map((_, i) => <Cell key={i} fill={i < histData.length / 2 ? '#ef4444' : '#10b981'} fillOpacity={0.7} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            {pathData.length > 0 && (
              <div>
                <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-2">SAMPLE PATHS (10)</div>
                <ResponsiveContainer width="100%" height={160}>
                  <LineChart>
                    <CartesianGrid strokeDasharray="3 3" stroke="#0f172a" vertical={false} />
                    <XAxis hide />
                    <YAxis tick={{ fill: '#374151', fontSize: 9 }} tickFormatter={v => `${(v * 100).toFixed(0)}%`} width={35} />
                    <ReferenceLine y={0} stroke="#374151" strokeDasharray="4 4" />
                    {pathData.map((path, i) => (
                      <Line key={i} data={path.map((v: number, t: number) => ({ t, v }))} dataKey="v" dot={false} stroke={i < 5 ? '#10b981' : '#ef4444'} strokeWidth={1} strokeOpacity={0.5} />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
          {result.message && <p className="text-[11px] text-[#64748b] bg-[#060b14] border border-[#1e2d40] rounded p-2">{result.message}</p>}
        </div>
      )}
    </div>
  )
}

// ── Tab 2: Portfolio Target Return ────────────────────────────────────────────
function PortfolioTab() {
  const [target, setTarget] = useState(0.1)
  const [sims, setSims] = useState(1000)
  const [result, setResult] = useState<MonteCarloPortfolioResult | null>(null)
  const mut = useMutation({ mutationFn: () => runMonteCarloPortfolio({ target_return: target, n_simulations: sims }), onSuccess: setResult })

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-[#060b14] border border-[#1e2d40] rounded p-3">
          <div className="text-[10px] text-[#4a5568] mb-1.5">TARGET RETURN: {(target * 100).toFixed(0)}%</div>
          <input type="range" min="-0.5" max="2" step="0.01" value={target} onChange={e => setTarget(+e.target.value)} className="w-full accent-[#f59e0b]" />
        </div>
        <div className="bg-[#060b14] border border-[#1e2d40] rounded p-3">
          <div className="text-[10px] text-[#4a5568] mb-1.5">SIMULATIONS</div>
          <input type="number" value={sims} onChange={e => setSims(+e.target.value)} min={100} max={10000}
            className="w-full bg-[#0b0f1a] border border-[#1e2d40] rounded px-2 py-1.5 text-xs font-mono text-[#e2e8f0]" />
        </div>
      </div>
      <button onClick={() => mut.mutate()} disabled={mut.isPending}
        className="flex items-center gap-2 px-4 py-2 bg-[#f59e0b] hover:bg-[#d97706] disabled:opacity-50 text-[#0b0f1a] text-xs font-bold rounded">
        <Dice5 className="w-3.5 h-3.5" />
        {mut.isPending ? '시뮬레이션 중...' : 'RUN SIMULATION'}
      </button>
      {mut.isPending && <LoadingSpinner className="h-24" text="포트폴리오 시뮬레이션 중..." />}
      {mut.isError && <ErrorMessage message="시뮬레이션 실패" retry={() => mut.mutate()} />}
      {result && !mut.isPending && (
        <div className="flex flex-col md:flex-row items-center gap-6">
          <ProbabilityGauge probability={result.probability} />
          <div className="grid grid-cols-3 gap-3 flex-1">
            {[
              { label: '기대 수익률', value: `${(result.mean_return * 100).toFixed(2)}%`, color: result.mean_return >= 0 ? '#10b981' : '#ef4444' },
              { label: '중앙값 수익률', value: `${(result.median_return * 100).toFixed(2)}%`, color: result.median_return >= 0 ? '#10b981' : '#ef4444' },
              { label: 'VaR 95%', value: `${(result.var_95 * 100).toFixed(2)}%`, color: '#ef4444' },
            ].map(item => (
              <div key={item.label} className="bg-[#060b14] border border-[#1e2d40] rounded p-3 text-center">
                <div className="text-[10px] text-[#4a5568] mb-1">{item.label}</div>
                <div className="text-lg font-mono font-bold" style={{ color: item.color }}>{item.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      {result?.message && <p className="text-[11px] text-[#64748b] bg-[#060b14] border border-[#1e2d40] rounded p-2">{result.message}</p>}
    </div>
  )
}

// ── Tab 3: Individual Stock ───────────────────────────────────────────────────
function StockTab() {
  const [ticker, setTicker] = useState('')
  const [target, setTarget] = useState(0)
  const [result, setResult] = useState<MonteCarloStockResult | null>(null)
  const mut = useMutation({ mutationFn: () => runMonteCarloStock({ ticker, target_price: target }), onSuccess: setResult })

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <div>
          <div className="text-[10px] text-[#4a5568] mb-1.5">TICKER</div>
          <input type="text" placeholder="AAPL" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
            className="w-28 bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#f59e0b]" />
        </div>
        <div>
          <div className="text-[10px] text-[#4a5568] mb-1.5">TARGET PRICE ($)</div>
          <input type="number" step="0.01" placeholder="200.00" value={target || ''} onChange={e => setTarget(+e.target.value)}
            className="w-32 bg-[#060b14] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#f59e0b]" />
        </div>
        <button onClick={() => mut.mutate()} disabled={mut.isPending || !ticker || !target}
          className="mt-5 flex items-center gap-2 px-4 py-2 bg-[#f59e0b] hover:bg-[#d97706] disabled:opacity-50 text-[#0b0f1a] text-xs font-bold rounded">
          <Dice5 className="w-3.5 h-3.5" />
          {mut.isPending ? '시뮬레이션 중...' : 'RUN SIMULATION'}
        </button>
      </div>
      {mut.isPending && <LoadingSpinner className="h-24" text={`${ticker} 시뮬레이션 중...`} />}
      {mut.isError && <ErrorMessage message="시뮬레이션 실패" retry={() => mut.mutate()} />}
      {result && !mut.isPending && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-xs text-[#64748b]">
            <span>현재가:</span>
            <span className="font-mono text-[#e2e8f0] font-bold">{formatCurrency(result.current_price)}</span>
            <span>→</span>
            <span className="font-mono text-[#f59e0b] font-bold">{formatCurrency(target)} 목표</span>
          </div>
          <div className="flex flex-col md:flex-row items-center gap-6">
            <div className="flex flex-col items-center gap-1">
              <ProbabilityGauge probability={result.probability_final} />
              <div className="text-[10px] text-[#4a5568] text-center">기간말 도달 확률</div>
            </div>
            <div className="flex flex-col items-center gap-1">
              <ProbabilityGauge probability={result.probability_touch} />
              <div className="text-[10px] text-[#4a5568] text-center">기간 중 터치 확률</div>
            </div>
          </div>
          {result.message && <p className="text-[11px] text-[#64748b] bg-[#060b14] border border-[#1e2d40] rounded p-2">{result.message}</p>}
        </div>
      )}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
const TABS = ['Macro Scenario', 'Portfolio Target', 'Stock Target']

export default function MonteCarlo() {
  const [tab, setTab] = useState(0)
  return (
    <div className="p-5 space-y-4">
      <div className="flex items-center gap-2">
        <Dice5 className="w-4 h-4 text-[#f59e0b]" />
        <div>
          <h1 className="text-base font-bold text-[#e2e8f0]">MONTE CARLO SIMULATOR</h1>
          <p className="text-[11px] text-[#4a5568]">Jump-Diffusion · 10,000회 시뮬레이션 · 매크로 충격 모델링</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-[#060b14] border border-[#1e2d40] rounded-lg p-1">
        {TABS.map((t, i) => (
          <button key={t} onClick={() => setTab(i)}
            className={cn('flex-1 py-2 text-xs font-bold rounded transition-colors',
              tab === i ? 'bg-[#f59e0b] text-[#0b0f1a]' : 'text-[#64748b] hover:text-[#e2e8f0]'
            )}>
            {t}
          </button>
        ))}
      </div>

      {tab === 0 && <MacroTab />}
      {tab === 1 && <PortfolioTab />}
      {tab === 2 && <StockTab />}
    </div>
  )
}
