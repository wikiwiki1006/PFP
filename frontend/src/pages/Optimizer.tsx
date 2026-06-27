import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ScatterChart,
  Scatter,
  ReferenceLine,
} from 'recharts'
import LoadingSpinner, { ErrorMessage } from '@/components/LoadingSpinner'
import {
  runMaxSharpe,
  runBlackLitterman,
  runFactorAnalysis,
  runMonteCarloPortfolio,
  runMonteCarloStock,
} from '@/api'
import type {
  OptimizationResult,
  FactorAnalysisResult,
  MonteCarloPortfolioResult,
  MonteCarloStockResult,
} from '@/types'
import { formatCurrency, cn } from '@/lib/utils'

type Tab = 'max-sharpe' | 'black-litterman' | 'factor' | 'mc-portfolio' | 'mc-stock'

const TABS: { id: Tab; label: string }[] = [
  { id: 'max-sharpe', label: 'Max Sharpe' },
  { id: 'black-litterman', label: 'Black-Litterman' },
  { id: 'factor', label: 'Factor Analysis' },
  { id: 'mc-portfolio', label: 'MC Portfolio' },
  { id: 'mc-stock', label: 'MC Stock' },
]

const TOOLTIP_STYLE = {
  backgroundColor: '#1a2035',
  border: '1px solid #1e2d40',
  borderRadius: '8px',
  color: '#e2e8f0',
  fontSize: '12px',
}

function WeightsBarChart({ weights }: { weights: { [ticker: string]: number } }) {
  const data = Object.entries(weights)
    .sort(([, a], [, b]) => b - a)
    .map(([ticker, weight]) => ({ ticker, weight: weight * 100 }))

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 0, right: 10, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
        <XAxis dataKey="ticker" tick={{ fill: '#e2e8f0', fontSize: 11 }} tickLine={false} axisLine={false} />
        <YAxis
          tick={{ fill: '#64748b', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `${v.toFixed(0)}%`}
        />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(v: number) => [`${v.toFixed(1)}%`, 'Weight']}
          labelStyle={{ color: '#e2e8f0' }}
        />
        <Bar dataKey="weight" radius={[2, 2, 0, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill="#3b82f6" fillOpacity={0.8} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function FrontierChart({ result }: { result: OptimizationResult }) {
  if (!result.frontier || result.frontier.length === 0) return null

  const frontierData = result.frontier.map((p) => ({
    x: p.volatility * 100,
    y: p.return * 100,
  }))

  const optimalPoint = [{
    x: result.volatility * 100,
    y: result.expected_return * 100,
    label: 'Optimal',
  }]

  return (
    <ResponsiveContainer width="100%" height={220}>
      <ScatterChart margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" />
        <XAxis
          dataKey="x"
          name="Volatility"
          tick={{ fill: '#64748b', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `${v.toFixed(1)}%`}
          label={{ value: 'Volatility', position: 'insideBottom', offset: -5, fill: '#64748b', fontSize: 11 }}
        />
        <YAxis
          dataKey="y"
          name="Return"
          tick={{ fill: '#64748b', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `${v.toFixed(1)}%`}
          label={{ value: 'Return', angle: -90, position: 'insideLeft', fill: '#64748b', fontSize: 11 }}
        />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(v: number) => [`${v.toFixed(2)}%`]}
          cursor={{ strokeDasharray: '3 3', stroke: '#1e2d40' }}
        />
        <Scatter data={frontierData} fill="#64748b" fillOpacity={0.5} r={2} />
        <Scatter data={optimalPoint} fill="#3b82f6" r={8} />
      </ScatterChart>
    </ResponsiveContainer>
  )
}

function FactorBarChart({ result }: { result: FactorAnalysisResult }) {
  const data = [
    { name: 'Market', value: result.betas.Market, color: '#3b82f6' },
    { name: 'SMB', value: result.betas.SMB, color: '#10b981' },
    { name: 'HML', value: result.betas.HML, color: '#f59e0b' },
    { name: 'MOM', value: result.betas.MOM, color: '#8b5cf6' },
  ]

  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={data} margin={{ top: 0, right: 10, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
        <XAxis dataKey="name" tick={{ fill: '#e2e8f0', fontSize: 11 }} tickLine={false} axisLine={false} />
        <YAxis tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} />
        <ReferenceLine y={0} stroke="#1e2d40" />
        <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={{ color: '#e2e8f0' }} />
        <Bar dataKey="value" name="Beta" radius={[2, 2, 0, 0]}>
          {data.map((entry, i) => (
            <Cell key={i} fill={entry.color} fillOpacity={0.8} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function ProbabilityGauge({ probability }: { probability: number }) {
  const pct = Math.max(0, Math.min(100, probability * 100))
  const color = pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#ef4444'

  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className="w-32 h-32 rounded-full border-8 flex items-center justify-center"
        style={{ borderColor: color, boxShadow: `0 0 20px ${color}30` }}
      >
        <div className="text-center">
          <div className="text-2xl font-mono font-bold" style={{ color }}>
            {pct.toFixed(0)}%
          </div>
          <div className="text-xs text-[#64748b]">Probability</div>
        </div>
      </div>
    </div>
  )
}

export default function Optimizer() {
  const [activeTab, setActiveTab] = useState<Tab>('max-sharpe')

  // Max Sharpe state
  const [msResult, setMsResult] = useState<OptimizationResult | null>(null)
  const msMutation = useMutation({ mutationFn: () => runMaxSharpe(), onSuccess: setMsResult })

  // Black-Litterman state
  const [blRegime, setBlRegime] = useState('Bull')
  const [blConfidence, setBlConfidence] = useState(0.5)
  const [blResult, setBlResult] = useState<OptimizationResult | null>(null)
  const blMutation = useMutation({
    mutationFn: () => runBlackLitterman({ regime: blRegime, view_confidence: blConfidence }),
    onSuccess: setBlResult,
  })

  // Factor Analysis state
  const [faResult, setFaResult] = useState<FactorAnalysisResult | null>(null)
  const faMutation = useMutation({ mutationFn: () => runFactorAnalysis(), onSuccess: setFaResult })

  // MC Portfolio state
  const [mcPortTarget, setMcPortTarget] = useState(0.1)
  const [mcPortSims, setMcPortSims] = useState(1000)
  const [mcPortResult, setMcPortResult] = useState<MonteCarloPortfolioResult | null>(null)
  const mcPortMutation = useMutation({
    mutationFn: () =>
      runMonteCarloPortfolio({ target_return: mcPortTarget, n_simulations: mcPortSims }),
    onSuccess: setMcPortResult,
  })

  // MC Stock state
  const [mcStockTicker, setMcStockTicker] = useState('')
  const [mcStockTarget, setMcStockTarget] = useState(0)
  const [mcStockResult, setMcStockResult] = useState<MonteCarloStockResult | null>(null)
  const mcStockMutation = useMutation({
    mutationFn: () =>
      runMonteCarloStock({ ticker: mcStockTicker, target_price: mcStockTarget }),
    onSuccess: setMcStockResult,
  })

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-[#e2e8f0]">Portfolio Optimizer</h1>
        <p className="text-sm text-[#64748b] mt-0.5">
          Quantitative optimization tools &amp; risk analysis
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-1">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              'flex-1 py-2 px-3 text-xs font-medium rounded transition-colors whitespace-nowrap',
              activeTab === tab.id
                ? 'bg-[#3b82f6] text-white'
                : 'text-[#64748b] hover:text-[#e2e8f0]'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}

      {/* Max Sharpe */}
      {activeTab === 'max-sharpe' && (
        <div className="space-y-4">
          <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-sm font-semibold text-[#e2e8f0]">Maximum Sharpe Ratio</h2>
                <p className="text-xs text-[#64748b] mt-0.5">
                  Optimize portfolio for maximum risk-adjusted return
                </p>
              </div>
              <button
                onClick={() => msMutation.mutate()}
                disabled={msMutation.isPending}
                className="flex items-center gap-2 px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm rounded transition-colors"
              >
                {msMutation.isPending ? <LoadingSpinner size="sm" /> : null}
                {msMutation.isPending ? 'Optimizing...' : 'Run Optimization'}
              </button>
            </div>

            {msMutation.isError && (
              <ErrorMessage message="Optimization failed" retry={() => msMutation.mutate()} />
            )}

            {msResult && (
              <div className="space-y-4">
                <div className="grid grid-cols-3 gap-4">
                  {[
                    { label: 'Expected Return', value: `${(msResult.expected_return * 100).toFixed(2)}%`, color: 'text-[#10b981]' },
                    { label: 'Volatility', value: `${(msResult.volatility * 100).toFixed(2)}%`, color: 'text-[#ef4444]' },
                    { label: 'Sharpe Ratio', value: msResult.sharpe_ratio.toFixed(3), color: 'text-[#3b82f6]' },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3 text-center">
                      <div className="text-xs text-[#64748b] mb-1">{label}</div>
                      <div className={cn('text-xl font-mono font-bold', color)}>{value}</div>
                    </div>
                  ))}
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider mb-2">
                      Optimal Weights
                    </h3>
                    <WeightsBarChart weights={msResult.weights} />
                  </div>
                  <div>
                    <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider mb-2">
                      Efficient Frontier
                    </h3>
                    <FrontierChart result={msResult} />
                  </div>
                </div>

                <div className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3">
                  <div className="text-xs text-[#64748b] mb-2 uppercase tracking-wider">vs Equal Weight</div>
                  <div className="grid grid-cols-3 gap-4 text-xs">
                    <div>
                      <span className="text-[#64748b]">Sharpe: </span>
                      <span className="font-mono text-[#e2e8f0]">{msResult.equal_weight_sharpe?.toFixed(3)}</span>
                    </div>
                    <div>
                      <span className="text-[#64748b]">Return: </span>
                      <span className="font-mono text-[#e2e8f0]">{(msResult.equal_weight_return * 100)?.toFixed(2)}%</span>
                    </div>
                    <div>
                      <span className="text-[#64748b]">Vol: </span>
                      <span className="font-mono text-[#e2e8f0]">{(msResult.equal_weight_volatility * 100)?.toFixed(2)}%</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Black-Litterman */}
      {activeTab === 'black-litterman' && (
        <div className="space-y-4">
          <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
            <h2 className="text-sm font-semibold text-[#e2e8f0] mb-4">Black-Litterman Model</h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <div>
                <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
                  Market Regime
                </label>
                <div className="flex gap-2">
                  {['Bull', 'Bear', 'Sideways'].map((regime) => (
                    <button
                      key={regime}
                      onClick={() => setBlRegime(regime)}
                      className={cn(
                        'px-3 py-1.5 text-xs rounded transition-colors',
                        blRegime === regime
                          ? regime === 'Bull'
                            ? 'bg-[#10b981] text-white'
                            : regime === 'Bear'
                            ? 'bg-[#ef4444] text-white'
                            : 'bg-[#f59e0b] text-white'
                          : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]'
                      )}
                    >
                      {regime}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
                  View Confidence: {(blConfidence * 100).toFixed(0)}%
                </label>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={blConfidence}
                  onChange={(e) => setBlConfidence(Number(e.target.value))}
                  className="w-full accent-[#3b82f6]"
                />
              </div>
            </div>

            <button
              onClick={() => blMutation.mutate()}
              disabled={blMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm rounded transition-colors"
            >
              {blMutation.isPending ? <LoadingSpinner size="sm" /> : null}
              {blMutation.isPending ? 'Running...' : 'Run Black-Litterman'}
            </button>

            {blMutation.isError && (
              <div className="mt-4">
                <ErrorMessage message="Optimization failed" retry={() => blMutation.mutate()} />
              </div>
            )}

            {blResult && (
              <div className="mt-4 space-y-4">
                <div className="grid grid-cols-3 gap-4">
                  {[
                    { label: 'Expected Return', value: `${(blResult.expected_return * 100).toFixed(2)}%`, color: 'text-[#10b981]' },
                    { label: 'Volatility', value: `${(blResult.volatility * 100).toFixed(2)}%`, color: 'text-[#ef4444]' },
                    { label: 'Sharpe Ratio', value: blResult.sharpe_ratio.toFixed(3), color: 'text-[#3b82f6]' },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3 text-center">
                      <div className="text-xs text-[#64748b] mb-1">{label}</div>
                      <div className={cn('text-xl font-mono font-bold', color)}>{value}</div>
                    </div>
                  ))}
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider mb-2">
                      Posterior Weights
                    </h3>
                    <WeightsBarChart weights={blResult.weights} />
                  </div>
                  {blResult.posterior_returns && (
                    <div>
                      <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider mb-2">
                        Posterior Returns
                      </h3>
                      <ResponsiveContainer width="100%" height={200}>
                        <BarChart
                          data={Object.entries(blResult.posterior_returns).map(([t, r]) => ({
                            ticker: t,
                            return: r * 100,
                          }))}
                          margin={{ top: 0, right: 10, left: 0, bottom: 0 }}
                        >
                          <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
                          <XAxis dataKey="ticker" tick={{ fill: '#e2e8f0', fontSize: 11 }} tickLine={false} axisLine={false} />
                          <YAxis tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v.toFixed(0)}%`} />
                          <ReferenceLine y={0} stroke="#1e2d40" />
                          <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [`${v.toFixed(2)}%`, 'Return']} labelStyle={{ color: '#e2e8f0' }} />
                          <Bar dataKey="return" radius={[2, 2, 0, 0]}>
                            {Object.values(blResult.posterior_returns).map((v, i) => (
                              <Cell key={i} fill={v >= 0 ? '#10b981' : '#ef4444'} fillOpacity={0.8} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Factor Analysis */}
      {activeTab === 'factor' && (
        <div className="space-y-4">
          <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-sm font-semibold text-[#e2e8f0]">Fama-French Factor Analysis</h2>
                <p className="text-xs text-[#64748b] mt-0.5">
                  Decompose portfolio returns into risk factors
                </p>
              </div>
              <button
                onClick={() => faMutation.mutate()}
                disabled={faMutation.isPending}
                className="flex items-center gap-2 px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm rounded transition-colors"
              >
                {faMutation.isPending ? <LoadingSpinner size="sm" /> : null}
                {faMutation.isPending ? 'Analyzing...' : 'Run Analysis'}
              </button>
            </div>

            {faMutation.isError && (
              <ErrorMessage message="Factor analysis failed" retry={() => faMutation.mutate()} />
            )}

            {faResult && (
              <div className="space-y-4">
                <div className="grid grid-cols-3 gap-4">
                  {[
                    { label: 'Alpha (Annual)', value: `${(faResult.alpha * 100).toFixed(2)}%`, color: faResult.alpha >= 0 ? 'text-[#10b981]' : 'text-[#ef4444]' },
                    { label: 'R-Squared', value: `${(faResult.r_squared * 100).toFixed(1)}%`, color: 'text-[#3b82f6]' },
                    { label: 'Residual Vol', value: `${(faResult.residual_vol * 100).toFixed(2)}%`, color: 'text-[#f59e0b]' },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3 text-center">
                      <div className="text-xs text-[#64748b] mb-1">{label}</div>
                      <div className={cn('text-xl font-mono font-bold', color)}>{value}</div>
                    </div>
                  ))}
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider mb-2">
                      Factor Betas
                    </h3>
                    <FactorBarChart result={faResult} />
                  </div>

                  {faResult.factor_contribution && (
                    <div>
                      <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider mb-2">
                        Factor Contributions
                      </h3>
                      <div className="space-y-2">
                        {Object.entries(faResult.factor_contribution).map(([factor, contrib]) => (
                          <div key={factor} className="flex items-center justify-between text-sm">
                            <span className="text-[#64748b]">{factor}</span>
                            <div className="flex items-center gap-2">
                              <div className="w-24 h-2 bg-[#1e2d40] rounded-full overflow-hidden">
                                <div
                                  className="h-full rounded-full"
                                  style={{
                                    width: `${Math.min(100, Math.abs(contrib * 100))}%`,
                                    backgroundColor: contrib >= 0 ? '#10b981' : '#ef4444',
                                  }}
                                />
                              </div>
                              <span className={cn('font-mono text-xs w-16 text-right', contrib >= 0 ? 'text-[#10b981]' : 'text-[#ef4444]')}>
                                {(contrib * 100).toFixed(2)}%
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* MC Portfolio */}
      {activeTab === 'mc-portfolio' && (
        <div className="space-y-4">
          <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
            <h2 className="text-sm font-semibold text-[#e2e8f0] mb-4">Monte Carlo — Portfolio</h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <div>
                <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
                  Target Return: {(mcPortTarget * 100).toFixed(0)}%
                </label>
                <input
                  type="range"
                  min="-0.5"
                  max="2"
                  step="0.01"
                  value={mcPortTarget}
                  onChange={(e) => setMcPortTarget(Number(e.target.value))}
                  className="w-full accent-[#3b82f6]"
                />
              </div>
              <div>
                <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
                  Simulations
                </label>
                <input
                  type="number"
                  value={mcPortSims}
                  onChange={(e) => setMcPortSims(Number(e.target.value))}
                  min={100}
                  max={10000}
                  className="w-full bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
                />
              </div>
            </div>

            <button
              onClick={() => mcPortMutation.mutate()}
              disabled={mcPortMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm rounded transition-colors"
            >
              {mcPortMutation.isPending ? <LoadingSpinner size="sm" /> : null}
              {mcPortMutation.isPending ? 'Simulating...' : 'Run Simulation'}
            </button>

            {mcPortMutation.isError && (
              <div className="mt-4">
                <ErrorMessage message="Simulation failed" retry={() => mcPortMutation.mutate()} />
              </div>
            )}

            {mcPortResult && (
              <div className="mt-4 space-y-4">
                <div className="flex flex-col md:flex-row items-center gap-6">
                  <ProbabilityGauge probability={mcPortResult.probability} />

                  <div className="flex-1 grid grid-cols-3 gap-4">
                    {[
                      { label: 'Mean Return', value: `${(mcPortResult.mean_return * 100).toFixed(2)}%`, color: mcPortResult.mean_return >= 0 ? 'text-[#10b981]' : 'text-[#ef4444]' },
                      { label: 'Median Return', value: `${(mcPortResult.median_return * 100).toFixed(2)}%`, color: mcPortResult.median_return >= 0 ? 'text-[#10b981]' : 'text-[#ef4444]' },
                      { label: 'VaR 95%', value: `${(mcPortResult.var_95 * 100).toFixed(2)}%`, color: 'text-[#ef4444]' },
                    ].map(({ label, value, color }) => (
                      <div key={label} className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3 text-center">
                        <div className="text-xs text-[#64748b] mb-1">{label}</div>
                        <div className={cn('text-lg font-mono font-bold', color)}>{value}</div>
                      </div>
                    ))}
                  </div>
                </div>

                <p className="text-xs text-[#64748b] bg-[#0b0f1a] border border-[#1e2d40] rounded p-3">
                  {mcPortResult.message}
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* MC Stock */}
      {activeTab === 'mc-stock' && (
        <div className="space-y-4">
          <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
            <h2 className="text-sm font-semibold text-[#e2e8f0] mb-4">Monte Carlo — Stock</h2>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
              <div>
                <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
                  Ticker
                </label>
                <input
                  type="text"
                  placeholder="e.g. AAPL"
                  value={mcStockTicker}
                  onChange={(e) => setMcStockTicker(e.target.value.toUpperCase())}
                  className="w-full bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6] uppercase"
                />
              </div>
              <div>
                <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
                  Target Price ($)
                </label>
                <input
                  type="number"
                  step="0.01"
                  placeholder="e.g. 200.00"
                  value={mcStockTarget || ''}
                  onChange={(e) => setMcStockTarget(Number(e.target.value))}
                  className="w-full bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
                />
              </div>
              <div className="flex items-end">
                <button
                  onClick={() => mcStockMutation.mutate()}
                  disabled={mcStockMutation.isPending || !mcStockTicker || !mcStockTarget}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm rounded transition-colors"
                >
                  {mcStockMutation.isPending ? <LoadingSpinner size="sm" /> : null}
                  {mcStockMutation.isPending ? 'Simulating...' : 'Run Simulation'}
                </button>
              </div>
            </div>

            {mcStockMutation.isError && (
              <ErrorMessage message="Simulation failed" retry={() => mcStockMutation.mutate()} />
            )}

            {mcStockResult && (
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-sm text-[#64748b]">
                  <span>Current Price:</span>
                  <span className="font-mono text-[#e2e8f0]">
                    {formatCurrency(mcStockResult.current_price)}
                  </span>
                  <span>→</span>
                  <span className="font-mono text-[#3b82f6]">
                    {formatCurrency(mcStockTarget)} target
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-6">
                  <div className="flex flex-col items-center gap-2">
                    <ProbabilityGauge probability={mcStockResult.probability_final} />
                    <div className="text-xs text-[#64748b] text-center">
                      Probability of reaching target<br />at end of period
                    </div>
                  </div>
                  <div className="flex flex-col items-center gap-2">
                    <ProbabilityGauge probability={mcStockResult.probability_touch} />
                    <div className="text-xs text-[#64748b] text-center">
                      Probability of touching target<br />at any point
                    </div>
                  </div>
                </div>

                <p className="text-xs text-[#64748b] bg-[#0b0f1a] border border-[#1e2d40] rounded p-3">
                  {mcStockResult.message}
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
