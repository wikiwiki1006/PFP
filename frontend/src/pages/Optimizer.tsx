import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts'
import { Brain, Plus, X, Download, ChevronRight, TrendingUp, TrendingDown, Minus, Square, Info, Lock } from 'lucide-react'
import { useLoginPrompt } from '@/components/auth/LockedPreview'
import LoadingSpinner from '@/components/LoadingSpinner'
import { startAIOptimizeJob, getAIOptimizeJob, cancelAIOptimizeJob, getHoldings, checkTickerExists } from '@/api'
import type { AIOptimizationResult, OptimizationMode } from '@/types'
import { cn } from '@/lib/utils'
import { formatPrice } from '@/lib/market'
import { marketSession } from '@/lib/marketStorage'
import { useTickerNames, displayTicker } from '@/lib/useTickerNames'
import TickerLabel from '@/components/TickerLabel'

// ─── Module-level cache (SPA 내 페이지 이동 시에도 유지) ─────────────────────

let _cachedResult: AIOptimizationResult | null = null
try {
  const s = marketSession.get('opt_result')
  if (s) _cachedResult = JSON.parse(s)
} catch {}

// ─── Constants ────────────────────────────────────────────────────────────────

const TOOLTIP_STYLE = {
  backgroundColor: '#111827',
  border: '1px solid #1e2d40',
  borderRadius: '6px',
  color: '#e2e8f0',
  fontSize: '11px',
}

// basis: 이 카드의 '기대수익'이 어느 기대수익률 벡터(mu)로 계산됐는지.
// 카드마다 mu 가 다르므로(BL 사후 vs 과거 실적) 서로 다른 잣대의 수치를
// 그대로 비교하면 안 된다 — 화면에 기준을 표시해 오해를 막는다.
const OPT_CARDS = [
  {
    key: 'black_litterman' as const,
    title: 'AI 추천 비중',
    subtitle: 'Black-Litterman + AI 뷰',
    color: '#8b5cf6',
    basis: 'BL 사후',
    desc: 'AI가 생성한 전망을 Black-Litterman 모델에 베이즈 방식으로 반영한 최적 비중',
  },
  {
    key: 'max_sharpe_hist' as const,
    title: '위험대비 수익 최대',
    subtitle: '과거 데이터 기반 Max Sharpe',
    color: '#3b82f6',
    basis: '과거 실적',
    desc: '과거 수익률 데이터로 샤프비율을 최대화하는 비중 (AI 뷰 미반영).',
  },
  {
    key: 'hrp' as const,
    title: '헤지 방어',
    subtitle: 'HRP · 계층적 리스크 패리티',
    color: '#10b981',
    basis: 'BL 사후',
    desc: '상관관계로 자산을 군집화한 뒤 클러스터 간·내부로 리스크를 나눠 배분합니다. 비슷한 성격의 종목에 비중이 쏠리지 않아 자연스러운 헤지가 됩니다.',
  },
  {
    key: 'target_return' as const,
    title: '목표 수익률 달성',
    subtitle: 'Efficient Return (최소 위험)',
    color: '#f59e0b',
    basis: 'BL 사후',
    desc: '목표 수익률을 달성하는 최소 위험 포트폴리오. 아래 비중으로 투자 시 예상 변동성·샤프비율을 확인하세요.',
  },
]

const GLOSSARY: Record<string, string> = {
  '샤프비율': '(기대수익률 - 무위험이자율) / 변동성. 위험 한 단위당 초과수익입니다. 높을수록 효율적입니다.',
  '변동성': '수익률의 표준편차를 연간화한 수치입니다. 낮을수록 안정적이지만 기대수익도 제한됩니다.',
  '효율적 프론티어': '동일 위험에서 최대 수익, 동일 수익에서 최소 위험을 실현하는 포트폴리오 집합입니다. 이 선 위의 포트폴리오만 최적입니다.',
  'BL 사후수익률': 'AI 전망을 반영한 Black-Litterman 사후(posterior) 기대수익률입니다. 순수 과거 수익률보다 펀더멘털을 반영합니다.',
  '소르티노': '(기대수익 - 무위험이율) / 하방편차. 샤프비율과 달리 손실 방향의 변동만 위험으로 봅니다. 1 이상이면 양호합니다.',
  '최대낙폭': 'MDD (Max Drawdown). 분석 기간 중 최고점 대비 최대 손실폭입니다. 과거 최악 시나리오의 하락 크기를 보여줍니다.',
  '칼마비율': '연환산 수익률 / |MDD|. 낙폭 위험 대비 수익을 측정합니다. 0.5 이상이면 양호, 1 이상이면 우수합니다.',
  '베타': 'S&P 500 대비 시장 민감도. 1이면 시장과 동일하게 움직이며, 1 초과면 더 크게 반응합니다.',
  'HRP': '계층적 리스크 패리티(Hierarchical Risk Parity). 상관계수 거리로 자산을 계층 군집화한 뒤, 클러스터 간에는 분산에 반비례하게, 클러스터 내부에서는 다시 같은 방식으로 비중을 나눕니다. 공분산 역행렬을 쓰지 않아 종목이 많아도 안정적이며, 상관 높은 자산군에 비중이 쏠리는 현상을 막습니다.',
  'CVaR 95%': '조건부 VaR. 수익률 하위 5% 시나리오에서 기대되는 평균 연손실입니다. 극단적 하락 위험의 크기를 나타냅니다.',
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function GlossaryTip({ term }: { term: string }) {
  const [open, setOpen] = useState(false)
  const def = GLOSSARY[term]
  if (!def) return <span>{term}</span>
  return (
    <span className="relative inline-flex items-center gap-0.5">
      <span
        className="underline decoration-dotted decoration-[#4a5568] cursor-help"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        {term}
      </span>
      <Info className="w-2.5 h-2.5 text-[#4a5568]" />
      {open && (
        <div className="absolute bottom-full left-0 z-50 w-64 bg-[#1e293b] border border-[#334155] rounded-lg px-3 py-2.5 text-[10px] text-[#cbd5e1] shadow-xl leading-relaxed"
          style={{ minWidth: 220 }}>
          <strong className="text-[#94a3b8] block mb-1">{term}</strong>
          {def}
        </div>
      )}
    </span>
  )
}

function SentimentBadge({ s }: { s: string }) {
  if (s === 'Bullish') return (
    <span className="flex items-center gap-0.5 text-[#10b981] text-xs font-medium">
      <TrendingUp className="w-3 h-3" /> Bullish
    </span>
  )
  if (s === 'Bearish') return (
    <span className="flex items-center gap-0.5 text-[#ef4444] text-xs font-medium">
      <TrendingDown className="w-3 h-3" /> Bearish
    </span>
  )
  return (
    <span className="flex items-center gap-0.5 text-[#64748b] text-xs font-medium">
      <Minus className="w-3 h-3" /> Neutral
    </span>
  )
}

function WeightsBar({ weights, color }: { weights: { [t: string]: number }; color?: string }) {
  const names = useTickerNames()
  const data = Object.entries(weights)
    .sort(([, a], [, b]) => b - a)
    .map(([ticker, w]) => ({ ticker, w: +(w * 100).toFixed(1) }))
  const COLORS = ['#8b5cf6', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#06b6d4', '#ec4899', '#a78bfa']
  return (
    <ResponsiveContainer width="100%" height={150}>
      <BarChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
        <XAxis dataKey="ticker" tick={{ fill: '#94a3b8', fontSize: 10 }} tickLine={false} axisLine={false}
               tickFormatter={(t: string) => displayTicker(t, names)} />
        <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false}
          tickFormatter={(v) => `${v}%`} />
        <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [`${v.toFixed(1)}%`, '비중']}
          labelStyle={{ color: '#e2e8f0' }} />
        <Bar dataKey="w" radius={[2, 2, 0, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={color ?? COLORS[i % COLORS.length]} fillOpacity={0.85} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function OptCard({
  card, mode, targetReturn, effectiveTarget,
}: {
  card: typeof OPT_CARDS[number]
  mode: OptimizationMode | null
  targetReturn: number
  effectiveTarget?: number
}) {
  const names = useTickerNames()
  const [expanded, setExpanded] = useState(false)
  const showAdjusted = card.key === 'target_return' && effectiveTarget != null
    && Math.abs(effectiveTarget - targetReturn) > 0.002

  return (
    <div className="bg-[#111827] border border-[#1e2d40] rounded-lg overflow-hidden">
      <div className="px-4 pt-4 pb-3 border-b border-[#1e2d40]"
        style={{ borderTop: `2px solid ${card.color}` }}>
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1">
            <p className="text-[10px] text-[#64748b]">
              {card.key === 'hrp' ? <><GlossaryTip term="HRP" /> · 계층적 리스크 패리티</> : card.subtitle}
            </p>
            <h3 className="text-sm font-bold text-[#e2e8f0] mt-0.5">{card.title}</h3>
          </div>
          {card.key === 'target_return' && (
            <div className="text-right shrink-0">
              <span className="text-xs font-mono font-bold px-2 py-1 rounded"
                style={{ background: `${card.color}25`, color: card.color }}>
                목표 {(targetReturn * 100).toFixed(0)}%
              </span>
              {showAdjusted && effectiveTarget != null && (
                <p className="text-[9px] text-[#64748b] mt-0.5">
                  실제 적용 {(effectiveTarget * 100).toFixed(1)}%
                </p>
              )}
            </div>
          )}
        </div>
        <p className="text-[10px] text-[#4a5568] mt-1.5 leading-relaxed">{card.desc}</p>
      </div>

      {mode ? (
        <div className="p-4 space-y-3">
          {/* 핵심 지표 */}
          <div className="grid grid-cols-3 gap-2">
            {[
              { label: `기대수익 · ${card.basis}`, value: `${(mode.expected_return * 100).toFixed(1)}%`, color: '#10b981' },
              { label: '변동성',   value: `${(mode.volatility * 100).toFixed(1)}%`,      color: '#ef4444' },
              { label: '샤프비율', value: mode.sharpe_ratio.toFixed(2),                   color: card.color },
            ].map(({ label, value, color }) => (
              <div key={label} className="bg-[#0b0f1a] rounded p-2 text-center">
                <p className="text-[9px] text-[#64748b] mb-0.5 whitespace-nowrap">{label}</p>
                <p className="text-sm font-mono font-bold" style={{ color }}>{value}</p>
              </div>
            ))}
          </div>
          <WeightsBar weights={mode.weights} color={card.color} />
          <button onClick={() => setExpanded(e => !e)}
            className="w-full flex items-center justify-between text-xs text-[#64748b] hover:text-[#94a3b8] transition-colors">
            <span>종목별 비중 상세</span>
            <ChevronRight className={cn('w-3 h-3 transition-transform', expanded && 'rotate-90')} />
          </button>
          {expanded && (
            <div className="space-y-1.5">
              {Object.entries(mode.weights).sort(([, a], [, b]) => b - a).map(([ticker, w]) => (
                <div key={ticker} className="flex items-center gap-2">
                  <span className="text-xs text-[#e2e8f0] w-20 truncate" title={ticker}>{displayTicker(ticker, names)}</span>
                  <div className="flex-1 h-1.5 bg-[#1e2d40] rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${(w * 100).toFixed(1)}%`, backgroundColor: card.color }} />
                  </div>
                  <span className="text-xs font-mono text-[#94a3b8] w-10 text-right">{(w * 100).toFixed(1)}%</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="p-4 flex items-center justify-center h-28 text-xs text-[#4a5568]">
          {card.key === 'target_return'
            ? '목표 수익률이 달성 불가능하거나 최적화 실패'
            : '최적화 실패'}
        </div>
      )}
    </div>
  )
}

// ─── Risk Metrics Comparison Grid ─────────────────────────────────────────────

function RiskMetricsGrid({ modes }: { modes: AIOptimizationResult['optimizations'] }) {
  const cols = OPT_CARDS.map(c => ({ ...c, mode: modes[c.key] }))

  const METRICS = [
    {
      key: 'sharpe_ratio'  as const,
      tip: '샤프비율',
      fmt: (v: number) => v.toFixed(2),
      norm: (v: number) => Math.max(0, Math.min(1, v / 3)),
      good: 'high' as const,
    },
    {
      key: 'sortino_ratio' as const,
      tip: '소르티노',
      fmt: (v: number) => v.toFixed(2),
      norm: (v: number) => Math.max(0, Math.min(1, v / 3)),
      good: 'high' as const,
    },
    {
      key: 'calmar_ratio'  as const,
      tip: '칼마비율',
      fmt: (v: number) => v.toFixed(2),
      norm: (v: number) => Math.max(0, Math.min(1, v / 2)),
      good: 'high' as const,
    },
    {
      key: 'max_drawdown'  as const,
      tip: '최대낙폭',
      fmt: (v: number) => `${(v * 100).toFixed(1)}%`,
      norm: (v: number) => Math.max(0, Math.min(1, Math.abs(v) / 0.5)),
      good: 'low'  as const,
    },
    {
      key: 'beta'          as const,
      tip: '베타',
      fmt: (v: number) => v.toFixed(2),
      norm: (v: number) => Math.max(0, Math.min(1, Math.abs(v) / 2)),
      good: 'low'  as const,
    },
    {
      key: 'cvar_95'       as const,
      tip: 'CVaR 95%',
      fmt: (v: number) => `${(v * 100).toFixed(1)}%`,
      norm: (v: number) => Math.max(0, Math.min(1, Math.abs(v) / 0.5)),
      good: 'low'  as const,
    },
  ]

  const barColor = (good: 'high' | 'low', norm: number) => {
    const t = good === 'high' ? norm : 1 - norm
    return t > 0.6 ? '#10b981' : t > 0.3 ? '#f59e0b' : '#ef4444'
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs min-w-[540px]">
        <thead>
          <tr className="border-b border-[#1e2d40]">
            <th className="py-2 px-3 text-left text-[#64748b] w-24">지표</th>
            {cols.map(c => (
              <th key={c.key} className="py-2 px-3 text-center font-semibold" style={{ color: c.color }}>
                {c.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {METRICS.map(m => (
            <tr key={m.key} className="border-b border-[#1e2d40]/30 hover:bg-[#1a2540]">
              <td className="py-2 px-3 text-[#64748b]"><GlossaryTip term={m.tip} /></td>
              {cols.map(c => {
                const raw = c.mode?.[m.key]
                const val = typeof raw === 'number' ? raw : null
                if (val == null) return <td key={c.key} className="py-2 px-3 text-center text-[#4a5568]">—</td>
                const n = m.norm(val)
                const color = barColor(m.good, n)
                return (
                  <td key={c.key} className="py-2 px-3">
                    <div className="flex flex-col items-center gap-1">
                      <span className="font-mono font-bold" style={{ color }}>{m.fmt(val)}</span>
                      <div className="w-full h-1 bg-[#1e2d40] rounded-full overflow-hidden">
                        <div className="h-full rounded-full" style={{ width: `${n * 100}%`, backgroundColor: color }} />
                      </div>
                    </div>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ─── Frontier Chart (Custom SVG) ──────────────────────────────────────────────

function FrontierChart({
  frontier, modes, userTargetReturn,
}: {
  frontier: AIOptimizationResult['frontier']
  modes: AIOptimizationResult['optimizations']
  userTargetReturn: number
}) {
  if (!frontier.length) return null

  const RISK_FREE = 0.04
  const W = 580, H = 270
  const M = { top: 24, right: 110, bottom: 48, left: 52 }
  const iW = W - M.left - M.right
  const iH = H - M.top - M.bottom

  const curve = [...frontier]
    .sort((a, b) => a.volatility - b.volatility)
    .map(p => ({
      vol: p.volatility * 100,
      ret: p.return * 100,
      sharpe: p.volatility > 0 ? (p.return - RISK_FREE) / p.volatility : 0,
    }))

  const strategies = ([
    { key: 'black_litterman', label: 'AI 추천',                                     color: '#8b5cf6' },
    { key: 'max_sharpe_hist', label: 'Max Sharpe',                                  color: '#3b82f6' },
    { key: 'hrp',             label: 'HRP 헤지',                                    color: '#10b981' },
    { key: 'target_return',   label: `목표 ${(userTargetReturn * 100).toFixed(0)}%`, color: '#f59e0b' },
  ] as const).flatMap(({ key, label, color }) => {
    const m = modes[key]
    if (!m) return []
    return [{ vol: m.volatility * 100, ret: m.expected_return * 100, sharpe: m.sharpe_ratio, label, color }]
  })

  const allVols = [...curve.map(p => p.vol), ...strategies.map(s => s.vol)]
  const allRets = [...curve.map(p => p.ret), ...strategies.map(s => s.ret)]
  const vSpan = Math.max(...allVols) - Math.min(...allVols)
  const rSpan = Math.max(...allRets) - Math.min(...allRets)
  const vMin = Math.min(...allVols) - vSpan * 0.12
  const vMax = Math.max(...allVols) + vSpan * 0.12
  const rMin = Math.min(...allRets) - rSpan * 0.15
  const rMax = Math.max(...allRets) + rSpan * 0.20

  const xS = (v: number) => ((v - vMin) / (vMax - vMin)) * iW
  const yS = (r: number) => iH - ((r - rMin) / (rMax - rMin)) * iH

  const sharpes = curve.map(p => p.sharpe)
  const sMin = Math.min(...sharpes), sMax = Math.max(...sharpes)
  const segColor = (s: number) => {
    const t = sMax > sMin ? (s - sMin) / (sMax - sMin) : 0.5
    if (t > 0.66) return '#10b981'
    if (t > 0.33) return '#3b82f6'
    return '#ef4444'
  }

  const N_TICKS = 5
  const xTicks = Array.from({ length: N_TICKS }, (_, i) => vMin + (i / (N_TICKS - 1)) * (vMax - vMin))
  const yTicks = Array.from({ length: N_TICKS }, (_, i) => rMin + (i / (N_TICKS - 1)) * (rMax - rMin))

  // Compute label offsets to avoid overlapping strategy labels
  const labeledPts = strategies.map(s => ({ ...s, cx: xS(s.vol), cy: yS(s.ret) }))

  return (
    <div>
      <div className="flex items-start justify-between mb-3 gap-4">
        <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider">
          <GlossaryTip term="효율적 프론티어" /> (BL 기반)
        </h3>
        <div className="flex items-center gap-3 text-[10px] text-[#64748b] shrink-0">
          <span className="flex items-center gap-1">
            <span className="w-4 h-0.5 rounded bg-[#ef4444] inline-block" /> 낮은 <GlossaryTip term="샤프비율" />
          </span>
          <span className="flex items-center gap-1">
            <span className="w-4 h-0.5 rounded bg-[#3b82f6] inline-block" /> 중간
          </span>
          <span className="flex items-center gap-1">
            <span className="w-4 h-0.5 rounded bg-[#10b981] inline-block" /> 높음
          </span>
        </div>
      </div>

      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ overflow: 'visible', display: 'block' }}>
        <g transform={`translate(${M.left},${M.top})`}>
          {/* Grid */}
          {xTicks.map((v, i) => (
            <line key={`gx${i}`} x1={xS(v)} y1={0} x2={xS(v)} y2={iH}
              stroke="#1e2d40" strokeDasharray="3 3" />
          ))}
          {yTicks.map((r, i) => (
            <line key={`gy${i}`} x1={0} y1={yS(r)} x2={iW} y2={yS(r)}
              stroke="#1e2d40" strokeDasharray="3 3" />
          ))}

          {/* Axis labels */}
          {xTicks.map((v, i) => (
            <text key={`lx${i}`} x={xS(v)} y={iH + 16} textAnchor="middle"
              fill="#64748b" fontSize={9.5}>{v.toFixed(1)}%</text>
          ))}
          <text x={iW / 2} y={iH + 34} textAnchor="middle" fill="#64748b" fontSize={9.5}>
            연간 변동성 (리스크) →
          </text>
          {yTicks.map((r, i) => (
            <text key={`ly${i}`} x={-8} y={yS(r) + 3.5} textAnchor="end"
              fill="#64748b" fontSize={9.5}>{r.toFixed(1)}%</text>
          ))}
          <text
            x={-36} y={iH / 2} textAnchor="middle"
            fill="#64748b" fontSize={9.5}
            transform={`rotate(-90,${-36},${iH / 2})`}
          >기대수익률 ↑</text>

          {/* Frontier curve colored by sharpe */}
          {curve.slice(0, -1).map((p, i) => {
            const q = curve[i + 1]
            return (
              <line key={i}
                x1={xS(p.vol).toFixed(2)} y1={yS(p.ret).toFixed(2)}
                x2={xS(q.vol).toFixed(2)} y2={yS(q.ret).toFixed(2)}
                stroke={segColor(p.sharpe)} strokeWidth={3.5} strokeLinecap="round" />
            )
          })}

          {/* Strategy points */}
          {labeledPts.map(({ vol, ret, sharpe, label, color, cx, cy }) => (
            <g key={label}>
              <circle cx={cx} cy={cy} r={14} fill={color} fillOpacity={0.12} />
              <circle cx={cx} cy={cy} r={7}  fill={color} stroke="#0b0f1a" strokeWidth={2} />
              <text x={cx} y={cy - 14} textAnchor="middle"
                fill={color} fontSize={10} fontWeight="700">{label}</text>
              <text x={cx} y={cy - 25} textAnchor="middle"
                fill="#64748b" fontSize={8.5}>Sharpe {sharpe.toFixed(2)}</text>
            </g>
          ))}
        </g>
      </svg>

      {/* Strategy legend */}
      <div className="flex flex-wrap gap-4 mt-1 justify-center">
        {strategies.map(s => (
          <div key={s.label} className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: s.color }} />
            <span className="text-[10px] text-[#64748b]">{s.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Correlation Heatmap ──────────────────────────────────────────────────────

function CorrelationHeatmap({ corr }: { corr: AIOptimizationResult['correlation'] }) {
  const names = useTickerNames()
  const { tickers, matrix } = corr
  if (!tickers.length) return null

  const cellBg = (v: number, diag: boolean) => {
    if (diag) return 'rgba(139, 92, 246, 0.18)'
    if (v >= 0) return `rgba(59, 130, 246, ${Math.min(v * 0.82, 0.82)})`
    return `rgba(239, 68, 68, ${Math.min(-v * 0.82, 0.82)})`
  }
  const cellFg = (v: number, diag: boolean) => {
    if (diag) return '#a78bfa'
    return Math.abs(v) > 0.5 ? '#f1f5f9' : '#94a3b8'
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider flex-1">
          상관관계 매트릭스
        </h3>
      </div>

      {/* Color scale legend */}
      <div className="flex items-center gap-2 mb-4">
        <span className="text-[10px] font-mono text-[#ef4444]">-1</span>
        <div
          className="flex-1 h-2 rounded-full"
          style={{ background: 'linear-gradient(to right, rgba(239,68,68,0.82), rgba(30,45,64,0.4), rgba(59,130,246,0.82))' }}
        />
        <span className="text-[10px] font-mono text-[#3b82f6]">+1</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-center border-separate" style={{ borderSpacing: '3px' }}>
          <thead>
            <tr>
              <th className="w-16" />
              {tickers.map(t => (
                <th key={t} className="pb-1 px-1 text-[10px] text-[#64748b] font-semibold">{displayTicker(t, names)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row, i) => (
              <tr key={tickers[i]}>
                <td className="pr-2 py-0.5 text-[11px] font-mono font-semibold text-[#94a3b8] text-right whitespace-nowrap">
                  {tickers[i]}
                </td>
                {row.map((v, j) => {
                  const diag = i === j
                  return (
                    <td key={j} title={diag ? '자기상관 = 1.00' : `상관계수: ${v.toFixed(3)}`}>
                      <div
                        className="rounded text-[11px] font-mono py-1.5 px-1 min-w-[42px] transition-all"
                        style={{
                          backgroundColor: cellBg(v, diag),
                          color: cellFg(v, diag),
                          fontWeight: diag ? 700 : 400,
                        }}>
                        {diag ? '1.00' : v.toFixed(2)}
                      </div>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-[#64748b]">
        <span><span className="text-[#3b82f6] font-semibold">0.7 이상</span> 강한 양상관 (분산 효과 낮음)</span>
        <span><span className="text-[#ef4444] font-semibold">음수</span> 분산 효과 높음 (이상적)</span>
        <span><span className="text-[#a78bfa] font-semibold">■</span> 자기상관 (항상 1)</span>
      </div>
    </div>
  )
}

// ─── AI Views Table ───────────────────────────────────────────────────────────

function RetCell({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-[#374151]">—</span>
  return (
    <span className={cn('font-mono', v >= 0 ? 'text-[#10b981]' : 'text-[#ef4444]')}>
      {v >= 0 ? '+' : ''}{v.toFixed(1)}%
    </span>
  )
}

function AIViewsTable({ result }: { result: AIOptimizationResult }) {
  const names = useTickerNames()
  const { tickers, ai_views, price_stats, posterior_returns } = result
  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider">
          AI 분석 뷰 (밸류에이션·애널리스트·모멘텀 → <GlossaryTip term="BL 사후수익률" />)
        </h3>
        <p className="text-[10px] text-[#4a5568] mt-1 leading-relaxed">
          포트폴리오 기대수익은 종목별 기대수익의 <b className="text-[#64748b]">비중가중평균</b>이므로
          개별 종목 최댓값을 넘을 수 없습니다. 단, ‘위험대비 수익 최대’ 카드만 과거 실적 기준이라
          아래 BL 사후수익과 잣대가 다릅니다.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[#1e2d40]">
              {['종목', '현재가', '1M / 3M / 1Y', '변동성', 'AI 심리', 'AI 예상수익', '신뢰도', 'BL 사후수익'].map(h => (
                <th key={h} className="py-2 px-3 text-left text-[#64748b] font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tickers.flatMap(t => {
              const view  = ai_views[t]
              const stats = price_stats[t]
              const post  = posterior_returns[t]
              const confPct = view ? Math.round(view.confidence * 100) : 0
              const rows = [
                <tr key={t} className="border-b border-[#1e2d40]/20 hover:bg-[#1a2540] transition-colors">
                  <td className="py-2 px-3"><TickerLabel ticker={t} name={names[t]} primaryClass="text-sm font-bold" /></td>
                  <td className="py-2 px-3 font-mono text-[#94a3b8]">
                    {formatPrice(stats?.current_price)}
                  </td>
                  <td className="py-2 px-3">
                    {stats ? (
                      <div className="flex items-center gap-1 text-[10px]">
                        <RetCell v={stats.ret_1m} />
                        <span className="text-[#2d3748]">/</span>
                        <RetCell v={stats.ret_3m} />
                        <span className="text-[#2d3748]">/</span>
                        <RetCell v={stats.ret_1y} />
                      </div>
                    ) : '—'}
                  </td>
                  <td className="py-2 px-3 font-mono text-[#64748b]">
                    {stats?.annual_vol_1y != null ? `${stats.annual_vol_1y.toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-2 px-3">
                    {view ? <SentimentBadge s={view.sentiment} /> : '—'}
                  </td>
                  <td className={cn('py-2 px-3 font-mono font-semibold',
                    (view?.expected_return ?? 0) >= 0 ? 'text-[#10b981]' : 'text-[#ef4444]')}>
                    {view ? `${view.expected_return >= 0 ? '+' : ''}${(view.expected_return * 100).toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-2 px-3">
                    {view ? (
                      <div className="flex items-center gap-1.5">
                        <div className="w-14 h-1.5 bg-[#1e2d40] rounded-full overflow-hidden">
                          <div className="h-full bg-[#10b981] rounded-full" style={{ width: `${confPct}%` }} />
                        </div>
                        <span className="font-mono text-[#64748b]">{confPct}%</span>
                      </div>
                    ) : '—'}
                  </td>
                  <td className={cn('py-2 px-3 font-mono', (post ?? 0) >= 0 ? 'text-[#8b5cf6]' : 'text-[#ef4444]')}>
                    {post != null ? `${post >= 0 ? '+' : ''}${(post * 100).toFixed(1)}%` : '—'}
                  </td>
                </tr>,
              ]
              if (view?.key_driver) {
                rows.push(
                  <tr key={`${t}-d`} className="border-b border-[#1e2d40]/30">
                    <td colSpan={8} className="px-3 pb-2.5 pt-0">
                      <p className="text-[10px] text-[#4a5568] leading-relaxed">
                        <span className="text-[#374151] font-medium mr-1">근거</span>{view.key_driver}
                      </p>
                    </td>
                  </tr>
                )
              }
              return rows
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Progress Bar ─────────────────────────────────────────────────────────────

function ProgressBar({ stage, stageText, elapsed }: { stage: number; stageText: string; elapsed: number }) {
  const pct = stage === 0 ? 8 : stage === 1 ? 33 : stage === 2 ? 67 : 92
  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-[#94a3b8]">{stageText}</span>
        <span className="text-[#4a5568] font-mono tabular-nums">{elapsed}s</span>
      </div>
      <div className="w-full h-2 bg-[#1e2d40] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{
            width: `${pct}%`,
            background: 'linear-gradient(90deg, #10b981, #059669)',
          }}
        />
      </div>
      <div className="grid grid-cols-3 text-[10px] text-[#374151]">
        <span className={cn('text-left', stage >= 1 && 'text-[#64748b]')}>① 데이터 수집</span>
        <span className={cn('text-center', stage >= 2 && 'text-[#64748b]')}>② AI 분석</span>
        <span className={cn('text-right', stage >= 3 && 'text-[#64748b]')}>③ 최적화</span>
      </div>
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function Optimizer() {
  const names = useTickerNames()
  const { isAuthed, requireLogin, modalEl } = useLoginPrompt()
  const [tickers, setTickers]             = useState<string[]>([])
  const [tickerInput, setTickerInput]     = useState('')
  const [targetReturn, setTargetReturn]   = useState(10)
  const [holdingYears, setHoldingYears]   = useState(1.0)
  const [loadingPortfolio, setLoadingPortfolio] = useState(false)
  const [result, setResult]               = useState<AIOptimizationResult | null>(_cachedResult)
  const [jobId, setJobId]                 = useState<string | null>(() => marketSession.get('opt_job_id'))
  const [jobError, setJobError]           = useState<string | null>(null)
  const [elapsed, setElapsed]             = useState(0)
  const startTimeRef                      = useRef<number>(0)
  const timerRef                          = useRef<ReturnType<typeof setInterval> | null>(null)
  const inputRef                          = useRef<HTMLInputElement>(null)

  // ── Job polling ──────────────────────────────────────────────────────────────
  const { data: jobData } = useQuery({
    queryKey: ['opt-job', jobId],
    queryFn: () => getAIOptimizeJob(jobId!),
    enabled: !!jobId,
    refetchInterval: 2000,
  })

  useEffect(() => {
    if (!jobData) return
    if (jobData.status === 'done' && jobData.result) {
      const r = jobData.result as AIOptimizationResult
      _cachedResult = r
      try { marketSession.set('opt_result', JSON.stringify(r)) } catch {}
      setResult(r)
      _clearJob()
    } else if (jobData.status === 'error') {
      setJobError(jobData.detail ?? '최적화 실패')
      _clearJob()
    } else if (jobData.status === 'cancelled') {
      _clearJob()
    }
  }, [jobData])

  // ── Elapsed timer ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (jobId) {
      const saved = marketSession.get('opt_job_start')
      startTimeRef.current = saved ? parseInt(saved) : Date.now()
      setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000))
      timerRef.current = setInterval(() => {
        setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000))
      }, 1000)
    } else {
      if (timerRef.current) clearInterval(timerRef.current)
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [jobId])

  const _clearJob = () => {
    marketSession.remove('opt_job_id')
    marketSession.remove('opt_job_start')
    setJobId(null)
  }

  // ── Start / Stop ─────────────────────────────────────────────────────────────
  const startOptimize = async () => {
    setJobError(null)
    try {
      const { job_id } = await startAIOptimizeJob({
        tickers: tickers.length ? tickers : undefined,
        // period 는 보내지 않는다 — 백엔드가 holding_period_years 로부터 자동 결정한다
        target_return: targetReturn / 100,
        risk_free_rate: 0.04,
        holding_period_years: holdingYears,
        weight_bounds: [0.0, 1.0],
      })
      marketSession.set('opt_job_id', job_id)
      marketSession.set('opt_job_start', Date.now().toString())
      setJobId(job_id)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      setJobError(e?.response?.data?.detail ?? '최적화 시작 실패')
    }
  }

  const stopOptimize = async () => {
    if (!jobId) return
    try { await cancelAIOptimizeJob(jobId) } catch {}
    _clearJob()
  }

  // ── Ticker helpers ────────────────────────────────────────────────────────────
  const [tickerError,    setTickerError]    = useState<string | null>(null)
  const [checkingTicker, setCheckingTicker] = useState(false)
  // 검증 도중 다른 티커가 추가/삭제되며 응답이 뒤늦게 와도 그 결과가 최신 요청인지
  // 확인하기 위한 카운터 — 없으면 빠르게 여러 개 입력할 때 오래된 응답이 화면을
  // 덮어써 엉뚱한 에러가 표시될 수 있다.
  const tickerCheckSeq = useRef(0)

  const addTicker = async (raw: string) => {
    const t = raw.trim().toUpperCase().replace(/[^A-Z0-9.-]/g, '')
    setTickerInput('')
    if (!t || tickers.includes(t)) return

    const seq = ++tickerCheckSeq.current
    setTickerError(null)
    setCheckingTicker(true)
    try {
      const res = await checkTickerExists(t)
      if (seq !== tickerCheckSeq.current) return   // 그 사이 더 최신 요청이 나감 — 무시
      if (res.exists) {
        setTickers(prev => (prev.includes(t) ? prev : [...prev, t]))
      } else {
        setTickerError(`${t}: 시장에 존재하지 않는 티커입니다.`)
      }
    } catch {
      // 검증 자체가 실패(네트워크 등)하면 막지 않는다 — 최적화 실행 단계에서 다시 걸러진다.
      if (seq === tickerCheckSeq.current) {
        setTickers(prev => (prev.includes(t) ? prev : [...prev, t]))
      }
    } finally {
      if (seq === tickerCheckSeq.current) setCheckingTicker(false)
    }
  }
  const removeTicker = (t: string) => setTickers(prev => prev.filter(x => x !== t))
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ' ' || e.key === ',') { e.preventDefault(); void addTicker(tickerInput) }
    if (e.key === 'Backspace' && !tickerInput && tickers.length) setTickers(prev => prev.slice(0, -1))
  }
  // 이 화면은 로그인 없이도 쓸 수 있다 — 종목을 직접 입력하면 공개 시세만으로
  // 최적화가 돌아간다. "내 포트폴리오 불러오기"만 개인 데이터가 필요하므로
  // 그 버튼에서만 로그인을 요구한다.
  const loadFromPortfolio = () => {
    if (!requireLogin()) return
    void (async () => {
      setLoadingPortfolio(true)
      try {
        const holdings = await getHoldings()
        setTickers(Object.keys(holdings).filter(t => t !== 'CASH'))
      } catch {}
      finally { setLoadingPortfolio(false) }
    })()
  }

  const isRunning = !!jobId
  const stage     = jobData?.stage ?? 0
  const stageText = jobData?.stage_text ?? '준비 중...'

  // ── Render ────────────────────────────────────────────────────────────────────
  return (
    <div className="p-6 space-y-6 min-h-full bg-[#0b0f1a]">
      {modalEl}

      {/* Header */}
      <div className="flex items-center gap-3">
        <Brain className="w-5 h-5 text-[#10b981]" />
        <div>
          <h1 className="text-xl font-bold text-[#e2e8f0]">포트폴리오 최적화</h1>
          <p className="text-xs text-[#64748b] mt-0.5">
            종목들을 입력하면(2개 이상) 선호도에 따른 최적화 포트폴리오를 추천합니다. AI 분석 뷰를 통해 종목별 기대수익과 신뢰도를 확인할 수 있습니다.
          </p>
        </div>
      </div>

      {/* Input Panel */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-5 space-y-5">

        {/* Ticker input */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs text-[#64748b] uppercase tracking-wider">종목 입력</label>
            <button onClick={loadFromPortfolio} disabled={loadingPortfolio || isRunning}
              title={isAuthed ? '내 보유 종목을 불러옵니다' : '로그인 후 사용 가능합니다'}
              className="text-xs text-[#10b981] hover:text-[#34d399] flex items-center gap-1 disabled:opacity-50">
              {loadingPortfolio
                ? <LoadingSpinner size="sm" />
                : isAuthed ? <Download className="w-3 h-3" /> : <Lock className="w-3 h-3" />}
              포트폴리오에서 불러오기
              {!isAuthed && <span className="text-[10px] text-[#4a5568]">· 로그인 필요</span>}
            </button>
          </div>
          <div
            className="min-h-[44px] bg-[#0b0f1a] border border-[#1e2d40] rounded-lg px-3 py-2 flex flex-wrap gap-1.5 items-center cursor-text focus-within:border-[#10b981] transition-colors"
            onClick={() => inputRef.current?.focus()}
          >
            {tickers.map(t => (
              <span key={t} className="flex items-center gap-1 bg-[#1e2d40] text-[#e2e8f0] text-xs px-2 py-0.5 rounded">
                {displayTicker(t, names)}
                <button onClick={(e) => { e.stopPropagation(); removeTicker(t) }}
                  disabled={isRunning}
                  className="text-[#64748b] hover:text-[#ef4444] transition-colors disabled:opacity-40">
                  <X className="w-2.5 h-2.5" />
                </button>
              </span>
            ))}
            <input
              ref={inputRef}
              value={tickerInput}
              onChange={e => setTickerInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onBlur={() => { if (tickerInput) void addTicker(tickerInput) }}
              disabled={isRunning}
              placeholder={tickers.length ? '' : 'AAPL MSFT NVDA... (Enter로 추가, 비우면 보유 종목 자동 사용)'}
              className="flex-1 bg-transparent text-xs text-[#e2e8f0] outline-none placeholder-[#374151] min-w-[180px] disabled:opacity-50"
            />
            {checkingTicker
              ? <LoadingSpinner size="sm" />
              : tickerInput && (
                <button onClick={() => void addTicker(tickerInput)} className="text-[#10b981] hover:text-[#34d399]">
                  <Plus className="w-3.5 h-3.5" />
                </button>
              )}
          </div>
          {tickerError ? (
            <p className="text-[10px] text-[#ef4444] mt-1">{tickerError}</p>
          ) : (
            <p className="text-[10px] text-[#374151] mt-1">Enter로 추가. 미기입 시 보유 종목 자동 사용.</p>
          )}
        </div>

        {/* Options — 데이터 기간은 투자기간에 따라 백엔드가 자동 결정하므로 입력받지 않는다 */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Target return */}
          <div>
            <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
              목표 수익률: <span className="text-[#10b981] font-mono font-bold">{targetReturn}%</span>
            </label>
            <input type="range" min={1} max={50} step={1} value={targetReturn}
              disabled={isRunning}
              onChange={e => setTargetReturn(+e.target.value)}
              className="w-full accent-[#10b981] disabled:opacity-50" />
          </div>

          {/* Holding period — 이 값이 참조 데이터 기간까지 결정한다 */}
          <div>
            <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
              투자 기간:{' '}
              <span className="text-[#10b981] font-mono">
                {holdingYears === 0.25 ? '3개월' : holdingYears === 0.5 ? '6개월' : holdingYears === 1 ? '1년' : '2년'}
              </span>
            </label>
            <div className="flex gap-1">
              {[{ v: 0.25, l: '3M', d: '2y' }, { v: 0.5, l: '6M', d: '2y' },
                { v: 1, l: '1Y', d: '3y' }, { v: 2, l: '2Y', d: '5y' }].map(o => (
                <button key={o.v} onClick={() => setHoldingYears(o.v)} disabled={isRunning}
                  className={cn('flex-1 py-1.5 text-xs rounded transition-colors disabled:opacity-50',
                    holdingYears === o.v
                      ? 'bg-[#10b981] text-white'
                      : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]')}>
                  {o.l}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-[#4a5568] mt-1.5">
              참조 데이터 기간은 이 값에 따라 자동 결정됩니다
              <span className="text-[#64748b] font-mono ml-1">
                → {holdingYears <= 0.5 ? '2y' : holdingYears <= 1 ? '3y' : '5y'}
              </span>
            </p>
          </div>
        </div>

        {/* Run / Stop + Progress */}
        <div className="space-y-3">
          <div className="flex items-center gap-4">
            {!isRunning ? (
              <button onClick={startOptimize}
                className="flex items-center gap-2 px-6 py-2.5 bg-[#10b981] hover:bg-[#059669] text-white text-sm font-semibold rounded-lg transition-colors">
                <Brain className="w-4 h-4" /> AI 최적화 실행
              </button>
            ) : (
              <button onClick={stopOptimize}
                className="flex items-center gap-2 px-5 py-2.5 bg-[#ef4444]/20 hover:bg-[#ef4444]/30 text-[#ef4444] text-sm font-semibold rounded-lg border border-[#ef4444]/40 transition-colors">
                <Square className="w-3.5 h-3.5 fill-current" /> 중단
              </button>
            )}
            {!isRunning && (
              <p className="text-[10px] text-[#4a5568] leading-relaxed">
                약 20–30초 소요
              </p>
            )}
          </div>

          {isRunning && (
            <ProgressBar stage={stage} stageText={stageText} elapsed={elapsed} />
          )}

          {jobError && (
            <p className="text-xs text-[#ef4444] bg-[#ef4444]/10 border border-[#ef4444]/30 rounded px-3 py-2">
              {jobError}
            </p>
          )}
        </div>
      </div>

      {/* Results */}
      {result && (
        <div className="space-y-6">
          {/* 분석 메타 */}
          {result.data_period && (
            <div className="flex items-center gap-2 text-[10px] text-[#4a5568]">
              <span className="px-2 py-0.5 rounded bg-[#1e2d40] text-[#64748b] font-mono">
                데이터 기간: {result.data_period}
              </span>
              <span>· 투자기간 기반 자동 선택 · 기하평균 수익률 · Ledoit-Wolf 공분산</span>
            </div>
          )}

          {/* AI Views */}
          <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-5">
            <AIViewsTable result={result} />
          </div>

          {/* 4 Opt Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            {OPT_CARDS.map(card => (
              <OptCard
                key={card.key}
                card={card}
                mode={result.optimizations[card.key]}
                targetReturn={targetReturn / 100}
                effectiveTarget={card.key === 'target_return' ? result.effective_target_return : undefined}
              />
            ))}
          </div>

          {/* 리스크 지표 비교 */}
          <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-5">
            <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider mb-4">
              최적화 방식별 리스크 지표 비교 (역사 데이터 기반)
            </h3>
            <RiskMetricsGrid modes={result.optimizations} />
          </div>

          {/* Frontier + Correlation */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-5">
              <FrontierChart
                frontier={result.frontier}
                modes={result.optimizations}
                userTargetReturn={targetReturn / 100}
              />
            </div>
            <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-5">
              <CorrelationHeatmap corr={result.correlation} />
            </div>
          </div>

          {/* Weights comparison table */}
          <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-5">
            <h3 className="text-xs font-semibold text-[#64748b] uppercase tracking-wider mb-4">
              최적화 방식별 비중 비교
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[#1e2d40]">
                    <th className="py-2 px-3 text-left text-[#64748b]">종목</th>
                    {OPT_CARDS.map(c => (
                      <th key={c.key} className="py-2 px-3 text-center font-medium" style={{ color: c.color }}>
                        {c.title}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.tickers.map(t => (
                    <tr key={t} className="border-b border-[#1e2d40]/30 hover:bg-[#1a2540]">
                      <td className="py-2 px-3"><TickerLabel ticker={t} name={names[t]} primaryClass="text-sm font-bold" /></td>
                      {OPT_CARDS.map(c => {
                        const w = result.optimizations[c.key]?.weights[t]
                        return (
                          <td key={c.key} className="py-2 px-3 text-center font-mono text-[#94a3b8]">
                            {w != null ? `${(w * 100).toFixed(1)}%` : '—'}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                  {/* 샤프·소르티노·최대낙폭은 위의 '리스크 지표 비교'가 단일 출처다.
                      여기서는 그 표에 없는 기대수익/변동성만 요약한다. */}
                  {([
                    { label: <><span>기대수익 / </span><GlossaryTip term="변동성" /></>, get: (m: OptimizationMode) => `${(m.expected_return * 100).toFixed(1)}% / ${(m.volatility * 100).toFixed(1)}%` },
                  ] as { label: React.ReactNode; get: (m: OptimizationMode) => string | undefined }[]).map((row, i) => (
                    <tr key={i} className={`${i === 0 ? 'border-t border-[#1e2d40]' : ''} bg-[#0b0f1a]`}>
                      <td className="py-2 px-3 text-[#64748b] text-xs">{row.label}</td>
                      {OPT_CARDS.map(c => {
                        const m = result.optimizations[c.key]
                        const val = m ? row.get(m) : undefined
                        return (
                          <td key={c.key} className="py-2 px-3 text-center font-mono text-[10px]" style={{ color: c.color }}>
                            {val ?? '—'}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <p className="text-[10px] text-[#374151] text-center">
            본 분석은 교육·참고 목적이며 투자 조언이 아닙니다. AI 예측 수익률과 실제 결과는 다를 수 있습니다.
          </p>
        </div>
      )}
    </div>
  )
}
