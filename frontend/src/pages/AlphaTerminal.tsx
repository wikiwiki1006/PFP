import { useState, useCallback, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AreaChart, Area, PieChart, Pie, Cell, Sector,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import ReactMarkdown from 'react-markdown'
import {
  Shield, Zap, MessageSquare, RefreshCw,
  Plus, Trash2, Edit3, Check, X, Play, FileText, ChevronRight,
  PanelRightClose, PanelRightOpen, Download,
} from 'lucide-react'
import {
  getPortfolioMetrics, getEquityCurve, getHoldingsDetail, getSectorWeights,
  getMarketSnapshot, getMarketNews, getMacroData, getEarnings, getCorrelation,
  getSignalsDoomRadar, getAnalystFeedback, getCachedScan, runSignalScan,
  postTrade, updateHolding, deleteHolding, getHoldings,
  generateDailyBrief, getDailyBriefHistory, getDailyBriefFile,
  getIndexPrices,
} from '@/api'
import { cn } from '@/lib/utils'

const SECTOR_COLORS = [
  '#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6',
  '#06b6d4','#84cc16','#f97316','#ec4899','#94a3b8',
  '#a78bfa','#34d399','#fbbf24','#f87171',
]

// ── Marquee ───────────────────────────────────────────────────────────────────
function Marquee({ snapshot }: { snapshot: any }) {
  if (!snapshot?.prices) return null
  const pairs = Object.entries(snapshot.prices as Record<string, any>)
  const seg = pairs.map(([t, v]) => {
    const p: number = v.change_1d_pct
    return `${t}  $${v.price.toFixed(2)}  ${p >= 0 ? '+' : ''}${p.toFixed(2)}%`
  }).join('     ·     ')
  return (
    <div className="overflow-hidden bg-[#070d18] border-b border-[#1e2d40] py-1.5 select-none">
      <div className="whitespace-nowrap text-[12px] font-mono animate-marquee inline-block text-[#64748b]">
        {seg + '     ·     ' + seg}
      </div>
    </div>
  )
}

// ── Metric Pill ───────────────────────────────────────────────────────────────
function Pill({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center px-4 py-2 border-r border-[#1e2d40] last:border-r-0 flex-shrink-0">
      <div className="text-[10px] text-[#64748b] font-bold tracking-widest uppercase">{label}</div>
      <div className="text-base font-mono font-bold mt-0.5 tabular-nums" style={{ color: color || '#e2e8f0' }}>{value}</div>
    </div>
  )
}

// ── Equity Curve ──────────────────────────────────────────────────────────────
type BenchmarkMode = 'sp500' | 'nasdaq' | 'both'

function EquityCurve({ curveQ }: { curveQ: any }) {
  const [range, setRange] = useState<'1M' | '3M' | '1Y' | 'ALL'>('1Y')
  const [bm,    setBm]    = useState<BenchmarkMode>('sp500')

  const rangeToPeriod = { '1M': '3mo', '3M': '6mo', '1Y': '2y', 'ALL': '5y' } as const

  const nasdaqQ = useQuery({
    queryKey: ['nasdaq-prices', rangeToPeriod[range]],
    queryFn:  () => getIndexPrices('^IXIC', rangeToPeriod[range]),
    enabled:  bm !== 'sp500',
    staleTime: 300_000,
  })

  const data = useCallback((): { date: string; port: number; sp: number; nasdaq?: number }[] => {
    const all: any[] = curveQ.data || []
    const days = range === '1M' ? 21 : range === '3M' ? 63 : range === '1Y' ? 252 : all.length
    const sliced = all.slice(-days)
    if (sliced.length < 2) return []
    const ip = sliced[0]?.value, ib = sliced[0]?.benchmark_value
    if (!ip || !ib) return []

    const nasdaqMap  = new Map((nasdaqQ.data || []).map(d => [d.date, d.close]))
    const nasdaqBase = nasdaqMap.get(sliced[0].date) ?? [...nasdaqMap.values()][0]

    return sliced.map(d => {
      const nc = nasdaqMap.get(d.date)
      return {
        date:   d.date,
        port:   +((d.value / ip - 1) * 100).toFixed(2),
        sp:     +((d.benchmark_value / ib - 1) * 100).toFixed(2),
        nasdaq: nc && nasdaqBase ? +((nc / nasdaqBase - 1) * 100).toFixed(2) : undefined,
      }
    })
  }, [curveQ.data, range, nasdaqQ.data])()

  // "2025-10-19" → "25.10.19"
  const fmtDate = (v: string) => {
    const p = v?.split('-')
    return p?.length === 3 ? `${p[0].slice(2)}.${p[1]}.${p[2]}` : v
  }

  const last    = data[data.length - 1]
  const portPct = last?.port   ?? 0
  const spPct   = last?.sp     ?? 0
  const nqPct   = last?.nasdaq ?? 0
  const alpha   = portPct - (bm === 'nasdaq' ? nqPct : spPct)

  const TIP = { backgroundColor: '#0b1220', border: '1px solid #1e2d40', borderRadius: 4, fontSize: 12 }

  const BM_BTNS: { key: BenchmarkMode; label: string; color: string }[] = [
    { key: 'sp500',  label: 'S&P',  color: '#64748b' },
    { key: 'nasdaq', label: 'NQ',   color: '#a78bfa' },
    { key: 'both',   label: 'BOTH', color: '#f59e0b' },
  ]

  return (
    <div className="px-4 pt-3 pb-2 border-b border-[#1e2d40]">
      {/* Header */}
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-4 flex-wrap">
          <span className="text-[11px] text-[#94a3b8] font-bold tracking-[3px] uppercase">Equity Curve</span>

          <div className="flex items-center gap-2">
            <svg width="20" height="5"><line x1="0" y1="2.5" x2="20" y2="2.5" stroke="#00e6ff" strokeWidth="2.5" /></svg>
            <span className="text-sm font-mono font-bold tabular-nums"
              style={{ color: portPct >= 0 ? '#10b981' : '#ef4444' }}>
              Portfolio {portPct >= 0 ? '+' : ''}{portPct.toFixed(2)}%
            </span>
          </div>

          {(bm === 'sp500' || bm === 'both') && (
            <div className="flex items-center gap-2">
              <svg width="20" height="5">
                <line x1="0" y1="2.5" x2="5" y2="2.5" stroke="#64748b" strokeWidth="1.5" />
                <line x1="7" y1="2.5" x2="12" y2="2.5" stroke="#64748b" strokeWidth="1.5" />
                <line x1="14" y1="2.5" x2="20" y2="2.5" stroke="#64748b" strokeWidth="1.5" />
              </svg>
              <span className="text-sm font-mono text-[#64748b] tabular-nums">
                S&P {spPct >= 0 ? '+' : ''}{spPct.toFixed(2)}%
              </span>
            </div>
          )}

          {(bm === 'nasdaq' || bm === 'both') && (
            <div className="flex items-center gap-2">
              <svg width="20" height="5">
                <line x1="0" y1="2.5" x2="5" y2="2.5" stroke="#a78bfa" strokeWidth="1.5" />
                <line x1="7" y1="2.5" x2="12" y2="2.5" stroke="#a78bfa" strokeWidth="1.5" />
                <line x1="14" y1="2.5" x2="20" y2="2.5" stroke="#a78bfa" strokeWidth="1.5" />
              </svg>
              <span className="text-sm font-mono text-[#a78bfa] tabular-nums">
                NASDAQ {nqPct >= 0 ? '+' : ''}{nqPct.toFixed(2)}%
              </span>
            </div>
          )}

          <span className="text-sm font-mono font-bold tabular-nums px-2 py-0.5 rounded"
            style={{
              color: alpha >= 0 ? '#10b981' : '#ef4444',
              backgroundColor: alpha >= 0 ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
            }}>
            α {alpha >= 0 ? '+' : ''}{alpha.toFixed(2)}%
          </span>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-2">
          {/* Benchmark selector */}
          <div className="flex gap-0.5 bg-[#070d18] border border-[#1e2d40] rounded p-0.5">
            {BM_BTNS.map(b => (
              <button key={b.key} onClick={() => setBm(b.key)}
                className={cn('text-[11px] px-2.5 py-1 rounded font-bold transition-colors',
                  bm === b.key ? '' : 'text-[#4a5568] hover:text-[#64748b]'
                )}
                style={bm === b.key ? { backgroundColor: b.color + '28', color: b.color } : {}}>
                {b.label}
              </button>
            ))}
          </div>
          {/* Range selector */}
          <div className="flex gap-0.5 bg-[#070d18] border border-[#1e2d40] rounded p-0.5">
            {(['1M', '3M', '1Y', 'ALL'] as const).map(r => (
              <button key={r} onClick={() => setRange(r)}
                className={cn('text-[11px] px-2.5 py-1 rounded font-bold transition-colors',
                  range === r ? 'bg-[#00e6ff]/15 text-[#00e6ff]' : 'text-[#4a5568] hover:text-[#64748b]'
                )}>
                {r}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={230}>
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="gPort" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#00e6ff" stopOpacity={0.25} />
              <stop offset="100%" stopColor="#00e6ff" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="gSP" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#64748b" stopOpacity={0.15} />
              <stop offset="100%" stopColor="#64748b" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="gNQ" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#a78bfa" stopOpacity={0.15} />
              <stop offset="100%" stopColor="#a78bfa" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="1 8" stroke="#111827" vertical={false} />
          <XAxis dataKey="date"
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false} axisLine={false}
            interval="preserveStartEnd"
            tickFormatter={fmtDate}
          />
          <YAxis
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false} axisLine={false}
            tickFormatter={v => `${v >= 0 ? '+' : ''}${v.toFixed(0)}%`}
            width={40}
          />
          <ReferenceLine y={0} stroke="#1e2d40" strokeDasharray="3 4" strokeWidth={1} />
          <Tooltip
            contentStyle={TIP}
            formatter={(v: number, key: string) => [
              `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`,
              key === 'port' ? 'Portfolio' : key === 'sp' ? 'S&P 500' : 'NASDAQ',
            ]}
            labelFormatter={fmtDate}
            labelStyle={{ color: '#94a3b8', fontSize: 11 }}
          />
          {(bm === 'sp500' || bm === 'both') && (
            <Area type="monotone" dataKey="sp" stroke="#64748b" strokeWidth={1.5}
              strokeDasharray="4 3" fill="url(#gSP)" dot={false} />
          )}
          {(bm === 'nasdaq' || bm === 'both') && (
            <Area type="monotone" dataKey="nasdaq" stroke="#a78bfa" strokeWidth={1.5}
              strokeDasharray="4 3" fill="url(#gNQ)" dot={false} />
          )}
          <Area type="monotone" dataKey="port" stroke="#00e6ff" strokeWidth={2.5}
            fill="url(#gPort)" dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Holdings ──────────────────────────────────────────────────────────────────
function HoldingsPanel({ holdQ }: { holdQ: any }) {
  const qc = useQueryClient()
  const [editTicker, setEditTicker] = useState<string | null>(null)
  const [editVals,   setEditVals]   = useState({ q: 0, avg: 0 })
  const [form, setForm] = useState({ ticker: '', type: 'BUY', q: 0, price: 0 })

  const updateMut = useMutation({
    mutationFn: ({ ticker, q, avg }: any) => updateHolding(ticker, { q, avg }),
    onSuccess: () => { setEditTicker(null); qc.invalidateQueries({ queryKey: ['holdings-detail'] }) },
  })
  const deleteMut = useMutation({
    mutationFn: (ticker: string) => deleteHolding(ticker),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['holdings-detail'] }),
  })
  const tradeMut = useMutation({
    mutationFn: (f: typeof form) => postTrade({ ticker: f.ticker, type: f.type as any, q: f.q, price: f.price }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['holdings-detail'] })
      qc.invalidateQueries({ queryKey: ['holdings-raw'] })
      setForm(f => ({ ...f, ticker: '', q: 0, price: 0 }))
    },
  })

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="text-[11px] text-[#94a3b8] font-bold tracking-[3px] uppercase px-3 py-2.5 border-b border-[#1e2d40] flex-shrink-0 bg-[#070d18]">
        Holdings
      </div>
      <div className="flex-1 overflow-y-auto min-h-0">
        <table className="w-full">
          <thead className="sticky top-0 bg-[#07101c] z-10">
            <tr className="text-[#64748b] border-b border-[#1e2d40]">
              {['Ticker', 'Avg', 'Qty', 'Price', '1D%', 'P&L', 'Wt', ''].map(h => (
                <th key={h} className="text-left py-2.5 px-2.5 font-semibold text-[11px] tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(holdQ.data || []).map((h: any) => (
              <tr key={h.ticker} className="border-b border-[#0f172a] hover:bg-[#0a1525] group transition-colors">
                {editTicker === h.ticker ? (
                  <>
                    <td className="py-2 px-2.5 font-mono font-bold text-sm text-[#e2e8f0]">{h.ticker}</td>
                    <td className="py-2 px-2.5">
                      <input type="number" value={editVals.avg}
                        onChange={e => setEditVals(v => ({ ...v, avg: +e.target.value }))}
                        className="w-18 bg-[#1e2d40] border border-[#334155] text-sm text-[#e2e8f0] rounded px-2 py-1" />
                    </td>
                    <td className="py-2 px-2.5">
                      <input type="number" value={editVals.q}
                        onChange={e => setEditVals(v => ({ ...v, q: +e.target.value }))}
                        className="w-14 bg-[#1e2d40] border border-[#334155] text-sm text-[#e2e8f0] rounded px-2 py-1" />
                    </td>
                    <td colSpan={4} />
                    <td className="py-2 px-2.5">
                      <div className="flex gap-1.5">
                        <button onClick={() => updateMut.mutate({ ticker: h.ticker, q: editVals.q, avg: editVals.avg })}
                          className="text-[#10b981] hover:text-[#34d399]"><Check className="w-4 h-4" /></button>
                        <button onClick={() => setEditTicker(null)}
                          className="text-[#ef4444] hover:text-[#f87171]"><X className="w-4 h-4" /></button>
                      </div>
                    </td>
                  </>
                ) : (
                  <>
                    <td className="py-2 px-2.5 font-mono font-bold text-[15px] text-[#e2e8f0]">{h.ticker}</td>
                    <td className="py-2 px-2.5 font-mono text-[12px] text-[#94a3b8]">${h.avg_cost.toFixed(0)}</td>
                    <td className="py-2 px-2.5 font-mono text-[12px] text-[#94a3b8]">{h.qty}</td>
                    <td className="py-2 px-2.5 font-mono text-[12px] text-[#cbd5e1]">${h.current_price.toFixed(1)}</td>
                    <td className="py-2 px-2.5 font-mono text-[13px] font-bold" style={{ color: h.pnl_pct >= 0 ? '#10b981' : '#ef4444' }}>
                      {h.pnl_pct >= 0 ? '+' : ''}{h.pnl_pct.toFixed(1)}%
                    </td>
                    <td className="py-2 px-2.5 font-mono text-[13px] font-bold" style={{ color: h.pnl >= 0 ? '#10b981' : '#ef4444' }}>
                      {h.pnl >= 0 ? '+' : ''}{Math.abs(h.pnl).toFixed(0)}
                    </td>
                    <td className="py-2 px-2.5 font-mono text-[12px] text-[#94a3b8]">{(h.weight * 100).toFixed(0)}%</td>
                    <td className="py-2 px-2.5 opacity-0 group-hover:opacity-100 transition-opacity">
                      <div className="flex gap-1">
                        <button onClick={() => { setEditTicker(h.ticker); setEditVals({ q: h.qty, avg: h.avg_cost }) }}
                          className="text-[#374151] hover:text-[#3b82f6] transition-colors"><Edit3 className="w-3.5 h-3.5" /></button>
                        <button onClick={() => deleteMut.mutate(h.ticker)}
                          className="text-[#374151] hover:text-[#ef4444] transition-colors"><Trash2 className="w-3.5 h-3.5" /></button>
                      </div>
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex-shrink-0 border-t border-[#1e2d40] px-3 py-2 bg-[#060b14] flex items-center gap-2 flex-wrap">
        <input value={form.ticker} onChange={e => setForm(f => ({ ...f, ticker: e.target.value.toUpperCase() }))}
          placeholder="Ticker"
          className="w-16 bg-[#0b1220] border border-[#1e2d40] text-sm font-mono text-[#e2e8f0] rounded px-2 py-1.5 placeholder-[#334155] focus:outline-none focus:border-[#3b82f6]" />
        <select value={form.type} onChange={e => setForm(f => ({ ...f, type: e.target.value }))}
          className="bg-[#0b1220] border border-[#1e2d40] text-sm text-[#94a3b8] rounded px-2 py-1.5 focus:outline-none focus:border-[#3b82f6]">
          <option value="BUY">BUY</option><option value="SELL">SELL</option>
        </select>
        <input type="number" value={form.q || ''} onChange={e => setForm(f => ({ ...f, q: +e.target.value }))}
          placeholder="Qty"
          className="w-14 bg-[#0b1220] border border-[#1e2d40] text-sm font-mono text-[#e2e8f0] rounded px-2 py-1.5 placeholder-[#334155] focus:outline-none focus:border-[#3b82f6]" />
        <input type="number" value={form.price || ''} onChange={e => setForm(f => ({ ...f, price: +e.target.value }))}
          placeholder="Price"
          className="w-20 bg-[#0b1220] border border-[#1e2d40] text-sm font-mono text-[#e2e8f0] rounded px-2 py-1.5 placeholder-[#334155] focus:outline-none focus:border-[#3b82f6]" />
        <button onClick={() => tradeMut.mutate(form)} disabled={!form.ticker || !form.q || !form.price}
          className="bg-[#1d4ed8] hover:bg-[#2563eb] disabled:opacity-40 text-white rounded px-3 py-1.5 transition-colors text-sm font-bold">
          <Plus className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

// ── Sectors (animated donut) ──────────────────────────────────────────────────
function SectorsPanel({ sectorData }: { sectorData: Record<string, number> }) {
  const [active, setActive] = useState(0)

  const data = Object.entries(sectorData)
    .sort(([, a], [, b]) => b - a)
    .map(([name, v], i) => ({
      name,
      value: +(v * 100).toFixed(1),
      fill: SECTOR_COLORS[i % SECTOR_COLORS.length],
    }))

  const renderActiveShape = (props: any) => {
    const { cx, cy, innerRadius, outerRadius, startAngle, endAngle, fill, payload, percent } = props
    return (
      <g>
        <Sector cx={cx} cy={cy} innerRadius={outerRadius + 5} outerRadius={outerRadius + 9}
          startAngle={startAngle} endAngle={endAngle} fill={fill} opacity={0.4} />
        <Sector cx={cx} cy={cy} innerRadius={innerRadius} outerRadius={outerRadius + 4}
          startAngle={startAngle} endAngle={endAngle} fill={fill} />
        <text x={cx} y={cy - 10} textAnchor="middle" fill="#94a3b8" fontSize={11} fontFamily="ui-monospace,monospace">
          {payload.name.length > 10 ? payload.name.slice(0, 10) + '…' : payload.name}
        </text>
        <text x={cx} y={cy + 12} textAnchor="middle" fill={fill} fontSize={18} fontWeight="700" fontFamily="ui-monospace,monospace">
          {`${(percent * 100).toFixed(1)}%`}
        </text>
      </g>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="text-[11px] text-[#94a3b8] font-bold tracking-[3px] uppercase px-3 py-2.5 border-b border-[#1e2d40] flex-shrink-0 bg-[#070d18]">
        Sectors
      </div>
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <div className="flex items-center justify-center" style={{ width: '46%' }}>
          <PieChart width={150} height={150}>
            <Pie
              activeIndex={active}
              activeShape={renderActiveShape}
              data={data}
              cx={75} cy={75}
              innerRadius={36} outerRadius={56}
              dataKey="value"
              onMouseEnter={(_, i) => setActive(i)}
              strokeWidth={0}
            >
              {data.map((e, i) => (
                <Cell key={i} fill={e.fill} opacity={active === i ? 1 : 0.6} />
              ))}
            </Pie>
          </PieChart>
        </div>
        <div className="flex-1 overflow-y-auto py-2 pr-3 space-y-1.5">
          {data.map((s, i) => (
            <div key={s.name} onMouseEnter={() => setActive(i)}
              className={cn('flex items-center gap-2 px-1.5 py-1 rounded cursor-pointer transition-all',
                active === i ? 'bg-[#0f172a]' : 'hover:bg-[#0a1020]'
              )}>
              <div className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                style={{ backgroundColor: s.fill, opacity: active === i ? 1 : 0.65 }} />
              <span className="text-[12px] truncate flex-1 transition-colors"
                style={{ color: active === i ? '#cbd5e1' : '#64748b' }}>
                {s.name}
              </span>
              <div className="w-12 h-2 bg-[#1e2d40] rounded-full overflow-hidden flex-shrink-0">
                <div className="h-full rounded-full transition-all duration-300"
                  style={{
                    width: `${Math.min(100, (s.value / (data[0]?.value || 1)) * 100)}%`,
                    backgroundColor: s.fill,
                    opacity: active === i ? 1 : 0.5,
                  }} />
              </div>
              <span className="text-[12px] font-mono font-bold w-9 text-right flex-shrink-0 tabular-nums"
                style={{ color: active === i ? s.fill : '#475569' }}>
                {s.value}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Correlation Heatmap ───────────────────────────────────────────────────────
function CorrelationHeatmap({ data }: { data: { tickers: string[]; matrix: number[][] } }) {
  const [hovered, setHovered] = useState<{ i: number; j: number } | null>(null)

  const short = (t: string) => t.replace('^', '').replace('-USD', '').slice(0, 6)

  // Smooth two-tone interpolation: blue (neg) → neutral → red (pos)
  const cellColors = (v: number, isDiag: boolean) => {
    if (isDiag) return { bg: 'rgba(255,255,255,0.04)', text: 'rgba(255,255,255,0.25)', border: 'rgba(255,255,255,0.06)' }
    const c = Math.max(-1, Math.min(1, v))
    if (c >= 0) {
      const a = 0.12 + c * 0.62
      const textBrightness = c > 0.6 ? '#fca5a5' : c > 0.3 ? '#f87171' : '#94a3b8'
      return { bg: `rgba(239,68,68,${a.toFixed(2)})`, text: textBrightness, border: `rgba(239,68,68,${(a * 0.6).toFixed(2)})` }
    } else {
      const a = 0.12 + (-c) * 0.62
      const textBrightness = -c > 0.6 ? '#93c5fd' : -c > 0.3 ? '#60a5fa' : '#94a3b8'
      return { bg: `rgba(59,130,246,${a.toFixed(2)})`, text: textBrightness, border: `rgba(59,130,246,${(a * 0.6).toFixed(2)})` }
    }
  }

  const n = data.tickers.length
  // Adapt cell size to number of tickers
  const sz = n <= 8 ? 36 : n <= 12 ? 30 : 25
  const gap = 3
  const hv = hovered

  const corrLabel = (v: number) => {
    const a = Math.abs(v)
    const dir = v > 0 ? '양의' : '음의'
    if (a > 0.7) return `강한 ${dir} 상관`
    if (a > 0.4) return `중간 ${dir} 상관`
    if (a > 0.15) return `약한 ${dir} 상관`
    return '무상관'
  }

  return (
    <div className="select-none space-y-3">
      <div className="overflow-auto">
        <div style={{ display: 'inline-flex', flexDirection: 'column', gap: `${gap}px` }}>
          {/* Column header row */}
          <div style={{ display: 'flex', gap: `${gap}px`, paddingLeft: `${sz + gap + 8}px` }}>
            {data.tickers.map((t, j) => (
              <div key={j} style={{ width: sz, textAlign: 'center' }}>
                <span style={{
                  fontSize: '9px', fontWeight: 700, fontFamily: 'monospace',
                  color: hv && (hv.i === j || hv.j === j) ? '#cbd5e1' : '#475569',
                  transition: 'color 0.15s',
                }}>
                  {short(t)}
                </span>
              </div>
            ))}
          </div>

          {/* Data rows */}
          {data.matrix.map((row, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: `${gap}px` }}>
              {/* Row label */}
              <div style={{ width: sz, paddingRight: 8, textAlign: 'right' }}>
                <span style={{
                  fontSize: '9px', fontWeight: 700, fontFamily: 'monospace',
                  color: hv && (hv.i === i || hv.j === i) ? '#cbd5e1' : '#475569',
                  transition: 'color 0.15s',
                }}>
                  {short(data.tickers[i])}
                </span>
              </div>

              {/* Cells */}
              {row.map((v, j) => {
                const isDiag = i === j
                const { bg, text, border } = cellColors(v, isDiag)
                const isHov = hv?.i === i && hv?.j === j

                return (
                  <div
                    key={j}
                    onMouseEnter={() => setHovered({ i, j })}
                    onMouseLeave={() => setHovered(null)}
                    style={{
                      width: sz, height: sz,
                      background: bg,
                      borderRadius: 5,
                      border: `1px solid ${isHov ? 'rgba(255,255,255,0.25)' : border}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: 'default',
                      transition: 'transform 0.12s ease, box-shadow 0.12s ease, border-color 0.12s ease',
                      transform: isHov ? 'scale(1.18)' : 'scale(1)',
                      boxShadow: isHov ? `0 0 10px ${bg}` : 'none',
                      zIndex: isHov ? 10 : 1,
                      position: 'relative',
                    }}
                  >
                    {isDiag ? (
                      <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.2)', fontWeight: 700 }}>●</span>
                    ) : (
                      <span style={{ fontSize: sz >= 32 ? 9 : 8, fontWeight: 700, color: text, fontFamily: 'monospace', lineHeight: 1 }}>
                        {v.toFixed(2)}
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Inline tooltip */}
      <div style={{ minHeight: 28 }}>
        {hv && hv.i !== hv.j && (
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#0f172a] border border-[#1e293b] text-[11px]">
            <span className="font-mono font-bold text-[#94a3b8]">{data.tickers[hv.i]}</span>
            <span className="text-[#334155]">↔</span>
            <span className="font-mono font-bold text-[#94a3b8]">{data.tickers[hv.j]}</span>
            <span className="text-[#1e293b] mx-1">|</span>
            <span className={`font-mono font-bold text-[13px] ${
              data.matrix[hv.i][hv.j] > 0 ? 'text-[#f87171]' : 'text-[#60a5fa]'
            }`}>
              {data.matrix[hv.i][hv.j] > 0 ? '+' : ''}{data.matrix[hv.i][hv.j].toFixed(4)}
            </span>
            <span className="text-[#374151] text-[10px]">{corrLabel(data.matrix[hv.i][hv.j])}</span>
          </div>
        )}
      </div>

      {/* Color legend */}
      <div className="flex items-center gap-2 px-1">
        <span className="text-[9px] text-[#3b82f6] font-bold">-1</span>
        <div style={{
          flex: 1, height: 5, borderRadius: 3,
          background: 'linear-gradient(to right, rgba(59,130,246,0.8) 0%, rgba(59,130,246,0.1) 45%, rgba(239,68,68,0.1) 55%, rgba(239,68,68,0.8) 100%)',
        }} />
        <span className="text-[9px] text-[#ef4444] font-bold">+1</span>
      </div>
      <div className="flex justify-between px-1">
        <span className="text-[8px] text-[#3b82f6]">음의 상관</span>
        <span className="text-[8px] text-[#374151]">무상관</span>
        <span className="text-[8px] text-[#ef4444]">양의 상관</span>
      </div>
    </div>
  )
}

// sessionStorage keys for Brief state persistence across navigation
const SK_CONTENT  = 'pfp_brief_content'
const SK_FILE     = 'pfp_brief_file'
const SK_LOGS     = 'pfp_brief_logs'
const SK_PENDING  = 'pfp_brief_pending'

// ── Daily Brief (right panel) ─────────────────────────────────────────────────
function DailyBriefPanel() {
  // Restore state from sessionStorage so navigation away doesn't wipe it
  const [file, setFile]         = useState<string | null>(() => sessionStorage.getItem(SK_FILE))
  const [content, setContent]   = useState<string | null>(() => sessionStorage.getItem(SK_CONTENT))
  const [logs, setLogs]         = useState<string[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(SK_LOGS) || '[]') } catch { return [] }
  })
  const [wasPending, setWasPending] = useState(() => sessionStorage.getItem(SK_PENDING) === '1')
  const [showHist,  setShowHist]    = useState(false)
  const [pdfBusy,   setPdfBusy]     = useState(false)
  const contentRef                  = useRef<HTMLDivElement>(null)

  const histQ   = useQuery({ queryKey: ['daily-brief-history'], queryFn: getDailyBriefHistory, staleTime: 60_000 })
  const fileMut = useMutation({
    mutationFn: getDailyBriefFile,
    onSuccess: d => {
      setContent(d.content)
      sessionStorage.setItem(SK_CONTENT, d.content)
    },
  })
  const genMut = useMutation({
    mutationFn: generateDailyBrief,
    onMutate: () => {
      setLogs(['[1/3] 가격 데이터 수집…'])
      setContent(null)
      setWasPending(false)
      sessionStorage.removeItem(SK_CONTENT)
      sessionStorage.setItem(SK_PENDING, '1')
      sessionStorage.setItem(SK_LOGS, JSON.stringify(['[1/3] 가격 데이터 수집…']))
    },
    onSuccess: d => {
      const newLogs = d.logs?.length ? d.logs : ['완료']
      setContent(d.report)
      setLogs(newLogs)
      sessionStorage.setItem(SK_CONTENT, d.report)
      sessionStorage.setItem(SK_LOGS, JSON.stringify(newLogs))
      sessionStorage.removeItem(SK_PENDING)
      histQ.refetch()
    },
    onError: (e: any) => {
      setLogs(prev => {
        const next = [...prev, `오류: ${e.message}`]
        sessionStorage.setItem(SK_LOGS, JSON.stringify(next))
        return next
      })
      sessionStorage.removeItem(SK_PENDING)
    },
  })

  // Fix 1: proper jsPDF named export + render element visibly (opacity:0) so
  // html2canvas can measure layout, then remove after capture.
  const downloadPDF = async () => {
    if (!content) return
    setPdfBusy(true)
    try {
      const [jspdfMod, h2cMod] = await Promise.all([
        import('jspdf'),
        import('html2canvas'),
      ])
      // jspdf v2 exports jsPDF as a named export; fall back to .default for CJS interop
      const JsPDF      = (jspdfMod as any).jsPDF ?? (jspdfMod as any).default
      const html2canvas = (h2cMod as any).default ?? h2cMod

      const dateStr  = new Date().toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
      const titleStr = file ? file.replace(/\.md$/, '') : `Daily Brief · ${dateStr}`

      // Position off-screen with z-index:-9999 (NOT opacity:0 — opacity:0 makes
      // html2canvas produce a blank transparent canvas).
      const wrap = document.createElement('div')
      wrap.style.cssText = [
        'position:fixed', 'top:0', 'left:0',
        'width:800px', 'background:#fff',
        'z-index:-9999', 'pointer-events:none',
      ].join(';')

      wrap.innerHTML = `
        <div style="background:#0f2044;padding:24px 40px 20px;">
          <div style="font-size:9px;letter-spacing:4px;color:#93c5fd;font-weight:700;margin-bottom:6px">PERSONAL FINANCIAL PLATFORM</div>
          <div style="font-size:22px;font-weight:900;color:#ffffff;line-height:1.2">${titleStr}</div>
          <div style="font-size:11px;color:#bfdbfe;margin-top:6px">${dateStr} · PFP Alpha Terminal</div>
        </div>
        <div id="pfp-pdf-body" style="padding:32px 40px 48px;color:#111827;font-family:'Helvetica Neue',Arial,sans-serif;font-size:13.5px;line-height:1.75;"></div>
      `
      document.body.appendChild(wrap)

      // Inject rendered markdown HTML + light-theme override styles
      const body = wrap.querySelector('#pfp-pdf-body')!
      body.innerHTML = contentRef.current?.innerHTML ?? content.replace(/\n/g, '<br/>')

      const overrideStyle = document.createElement('style')
      overrideStyle.id = 'pfp-pdf-override'
      overrideStyle.textContent = `
        #pfp-pdf-body *          { color:#111827!important; background:transparent!important; border-color:#d1d5db!important; }
        #pfp-pdf-body h1         { font-size:20px!important; font-weight:900!important; color:#0f2044!important;
                                   border-bottom:2px solid #0f2044!important; padding-bottom:8px!important; margin:0 0 16px!important; }
        #pfp-pdf-body h2         { font-size:16px!important; font-weight:700!important; color:#1e3a5f!important; margin:20px 0 8px!important; }
        #pfp-pdf-body h3         { font-size:14px!important; font-weight:700!important; color:#374151!important; margin:14px 0 6px!important; }
        #pfp-pdf-body p          { margin:0 0 10px!important; }
        #pfp-pdf-body ul,
        #pfp-pdf-body ol         { padding-left:20px!important; margin:0 0 10px!important; }
        #pfp-pdf-body li         { margin-bottom:3px!important; }
        #pfp-pdf-body strong     { color:#111827!important; font-weight:700!important; }
        #pfp-pdf-body code       { background:#f3f4f6!important; color:#1d4ed8!important; padding:1px 5px!important; border-radius:3px!important; font-size:12px!important; }
        #pfp-pdf-body blockquote { border-left:3px solid #1e3a5f!important; padding-left:12px!important; color:#6b7280!important; margin:8px 0!important; }
        #pfp-pdf-body hr         { border:none!important; border-top:1px solid #d1d5db!important; margin:12px 0!important; }
        #pfp-pdf-body table      { width:100%!important; border-collapse:collapse!important; font-size:12px!important; }
        #pfp-pdf-body th         { background:#f9fafb!important; color:#374151!important; border:1px solid #e5e7eb!important; padding:6px 8px!important; font-weight:600!important; }
        #pfp-pdf-body td         { color:#4b5563!important; border:1px solid #e5e7eb!important; padding:6px 8px!important; }
      `
      document.head.appendChild(overrideStyle)

      // Wait one frame so the browser fully computes layout
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))

      const canvas = await html2canvas(wrap, {
        scale: 2,
        backgroundColor: '#ffffff',
        useCORS: true,
        logging: false,
        windowWidth: 800,
      })

      document.body.removeChild(wrap)
      document.head.removeChild(overrideStyle)

      // Paginate into A4 (210 × 297 mm)
      const pdf     = new JsPDF('p', 'mm', 'a4')
      const pageW   = pdf.internal.pageSize.getWidth()
      const pageH   = pdf.internal.pageSize.getHeight()
      const pxPerMm = canvas.width / pageW
      const slicePx = pageH * pxPerMm

      let srcY = 0
      let page = 0
      while (srcY < canvas.height) {
        const rowH  = Math.min(slicePx, canvas.height - srcY)
        const slice = document.createElement('canvas')
        slice.width  = canvas.width
        slice.height = rowH
        const ctx = slice.getContext('2d')!
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, slice.width, slice.height)
        ctx.drawImage(canvas, 0, -srcY, canvas.width, canvas.height)

        if (page > 0) pdf.addPage()
        pdf.addImage(slice.toDataURL('image/jpeg', 0.92), 'JPEG', 0, 0, pageW, rowH / pxPerMm)

        srcY += slicePx
        page++
      }

      const fname = (file || `brief_${new Date().toISOString().slice(0, 10)}`).replace(/\.md$/, '')
      pdf.save(`${fname}.pdf`)
    } catch (err) {
      console.error('[PDF]', err)
      alert('PDF 생성 중 오류가 발생했습니다. 콘솔을 확인해주세요.')
    } finally {
      setPdfBusy(false)
    }
  }

  const isGenerating = genMut.isPending

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Action buttons */}
      <div className="flex-shrink-0 flex gap-2 p-3 border-b border-[#1e2d40]">
        <button onClick={() => genMut.mutate()} disabled={isGenerating}
          className="flex-1 flex items-center justify-center gap-2 py-2 bg-[#10b981]/12 border border-[#10b981]/30 text-[#10b981] text-[11px] font-bold rounded hover:bg-[#10b981]/20 disabled:opacity-50 transition-colors">
          <Play className="w-3.5 h-3.5" />
          {isGenerating ? 'GENERATING…' : 'GENERATE BRIEF'}
        </button>
        <button
          onClick={downloadPDF}
          disabled={!content || pdfBusy}
          title="PDF로 다운로드"
          className={cn(
            'px-3 py-2 rounded border text-[11px] font-bold transition-colors flex items-center gap-1.5',
            content && !pdfBusy
              ? 'border-[#3b82f6]/50 bg-[#3b82f6]/10 text-[#3b82f6] hover:bg-[#3b82f6]/20'
              : 'border-[#1e2d40] text-[#374151] cursor-not-allowed opacity-40'
          )}>
          {pdfBusy
            ? <span className="w-3.5 h-3.5 border-2 border-[#3b82f6] border-t-transparent rounded-full animate-spin" />
            : <Download className="w-3.5 h-3.5" />}
          <span>PDF</span>
        </button>
        <button onClick={() => setShowHist(h => !h)}
          className={cn('px-3 py-2 rounded border transition-colors',
            showHist ? 'bg-[#1e2d40] border-[#64748b]/50 text-[#94a3b8]' : 'border-[#1e2d40] text-[#64748b] hover:text-[#94a3b8]'
          )}>
          <FileText className="w-4 h-4" />
        </button>
      </div>

      {/* Resumed-from-navigation banner */}
      {wasPending && !isGenerating && !content && (
        <div className="flex-shrink-0 border-b border-[#f59e0b]/30 px-3 py-2 bg-[#f59e0b]/8 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-[#f59e0b] flex-shrink-0" />
          <span className="text-[11px] text-[#f59e0b] flex-1">생성이 진행 중이었습니다. 히스토리에서 완료된 보고서를 확인하세요.</span>
          <button onClick={() => { setWasPending(false); sessionStorage.removeItem(SK_PENDING); setShowHist(true); histQ.refetch() }}
            className="text-[10px] text-[#f59e0b] underline whitespace-nowrap">히스토리 열기</button>
        </div>
      )}

      {/* History dropdown */}
      {showHist && (
        <div className="flex-shrink-0 max-h-40 overflow-y-auto border-b border-[#1e2d40] bg-[#060b14]">
          {(histQ.data || []).map(f => (
            <button key={f.name}
              onClick={() => {
                setFile(f.name)
                sessionStorage.setItem(SK_FILE, f.name)
                fileMut.mutate(f.name)
                setShowHist(false)
                setWasPending(false)
                sessionStorage.removeItem(SK_PENDING)
              }}
              className={cn('w-full text-left px-3 py-2 text-[12px] border-b border-[#0f172a] font-mono truncate transition-colors',
                file === f.name ? 'text-[#10b981] bg-[#0f172a]' : 'text-[#64748b] hover:text-[#94a3b8] hover:bg-[#0a1020]'
              )}>
              {f.name}
            </button>
          ))}
        </div>
      )}

      {/* Pipeline logs while generating */}
      {isGenerating && (
        <div className="flex-shrink-0 border-b border-[#1e2d40] px-3 py-2.5 space-y-1.5 bg-[#060b14]">
          {logs.map((l, i) => (
            <div key={i} className="text-[12px] text-[#10b981] font-mono flex items-center gap-2">
              <ChevronRight className="w-3.5 h-3.5 flex-shrink-0" />{l}
            </div>
          ))}
          <div className="flex items-center gap-2 pt-0.5">
            <span className="w-2 h-2 rounded-full bg-[#10b981] animate-pulse" />
            <span className="text-[11px] text-[#64748b]">AI 분석 중… (~1-2분)</span>
          </div>
        </div>
      )}

      {/* Content area */}
      <div className="flex-1 overflow-y-auto">
        {!content && !isGenerating && !wasPending && (
          <div className="flex flex-col items-center justify-center h-full gap-3 p-5 text-center">
            <FileText className="w-12 h-12 text-[#10b981]/20" />
            <p className="text-sm text-[#64748b] leading-relaxed">GENERATE를 눌러<br/>AI 데일리 브리프를 생성하세요</p>
          </div>
        )}
        {content && !isGenerating && (
          <div ref={contentRef} className="p-4 brief-md">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
const BOT_TABS   = ['Correlation', 'Earnings', 'Macro']
const RIGHT_TABS = ['Brief', 'AI Feed', 'Signals', 'News']

export default function AlphaTerminal() {
  const qc = useQueryClient()
  const [botTab,    setBotTab]    = useState(0)
  const [rightTab,  setRightTab]  = useState(0)
  const [rightOpen, setRightOpen] = useState(true)

  const metricsQ  = useQuery({ queryKey: ['portfolio-metrics'], queryFn: getPortfolioMetrics,   refetchInterval: 30_000 })
  const curveQ    = useQuery({ queryKey: ['equity-curve'],      queryFn: getEquityCurve,         staleTime: 60_000 })
  const holdQ     = useQuery({ queryKey: ['holdings-detail'],   queryFn: getHoldingsDetail,      refetchInterval: 30_000 })
  const rawHoldQ  = useQuery({ queryKey: ['holdings-raw'],      queryFn: getHoldings })
  const sectorQ   = useQuery({ queryKey: ['sector-weights'],    queryFn: getSectorWeights,       staleTime: 60_000 })
  const snapQ     = useQuery({ queryKey: ['market-snapshot'],   queryFn: getMarketSnapshot,      refetchInterval: 30_000 })
  const macroQ    = useQuery({ queryKey: ['macro-data'],        queryFn: getMacroData,           staleTime: 300_000 })
  const doomQ     = useQuery({ queryKey: ['signals-doom'],      queryFn: getSignalsDoomRadar,    staleTime: 60_000 })
  const feedbackQ = useQuery({ queryKey: ['analyst-feedback'],  queryFn: getAnalystFeedback,     staleTime: 300_000 })
  const corrQ     = useQuery({ queryKey: ['correlation'],       queryFn: () => getCorrelation(), staleTime: 300_000 })
  const scanQ     = useQuery({ queryKey: ['scan'],              queryFn: getCachedScan,           retry: false })
  const scanMut   = useMutation({ mutationFn: () => runSignalScan(10), onSuccess: () => qc.invalidateQueries({ queryKey: ['scan'] }) })

  const holdTickers = Object.keys(rawHoldQ.data || {}).filter(t => t !== 'CASH').join(',')
  const newsQ = useQuery({
    queryKey: ['market-news', holdTickers],
    queryFn:  () => getMarketNews(holdTickers.split(',').filter(Boolean)),
    enabled: !!holdTickers,
    staleTime: 120_000,
  })
  const earningsQ = useQuery({
    queryKey: ['earnings', holdTickers],
    queryFn:  () => getEarnings(holdTickers.split(',').filter(Boolean)),
    enabled: !!holdTickers,
    staleTime: 3600_000,
  })

  const m    = metricsQ.data
  const doom = doomQ.data

  return (
    <div className="flex flex-col h-full bg-[#0b0f1a] overflow-hidden">

      {/* ── Metrics bar ── */}
      <div className="flex-shrink-0 bg-[#060b14] border-b border-[#1e2d40]">
        {m && (
          <div className="flex items-stretch overflow-x-auto">
            <Pill label="PORTFOLIO"  value={`$${(m.total_equity / 1000).toFixed(1)}K`} />
            <Pill label="TODAY"      value={`${m.today_change_pct >= 0 ? '+' : ''}${m.today_change_pct.toFixed(2)}%`}    color={m.today_change_pct >= 0 ? '#10b981' : '#ef4444'} />
            <Pill label="1W"         value={`${m.perf_1w >= 0 ? '+' : ''}${m.perf_1w.toFixed(2)}%`}                      color={m.perf_1w >= 0 ? '#10b981' : '#ef4444'} />
            <Pill label="1M"         value={`${m.perf_1m >= 0 ? '+' : ''}${m.perf_1m.toFixed(2)}%`}                      color={m.perf_1m >= 0 ? '#10b981' : '#ef4444'} />
            <Pill label="TOTAL RTN"  value={`${m.total_return_pct >= 0 ? '+' : ''}${m.total_return_pct.toFixed(2)}%`}    color={m.total_return_pct >= 0 ? '#10b981' : '#ef4444'} />
            <Pill label="BETA"       value={m.portfolio_beta.toFixed(2)} />
            <Pill label="VIX"        value={m.vix.toFixed(2)}                                                             color={m.vix > 25 ? '#ef4444' : m.vix > 18 ? '#f59e0b' : '#10b981'} />
            <Pill label="α vs S&P"   value={`${m.alpha_vs_sp500 >= 0 ? '+' : ''}${m.alpha_vs_sp500.toFixed(1)}%`}        color={m.alpha_vs_sp500 >= 0 ? '#10b981' : '#ef4444'} />
          </div>
        )}
      </div>

      {/* ── Marquee ── */}
      <Marquee snapshot={snapQ.data} />

      {/* ── Doom banner ── */}
      {doom?.is_doom && (
        <div className="flex-shrink-0 bg-[#ef4444]/10 border-b border-[#ef4444]/30 px-4 py-2 text-sm text-[#ef4444] flex items-center gap-2">
          <Shield className="w-4 h-4" />
          <span className="font-bold tracking-wider">DOOM  SEV {doom.severity}/5</span>
          <span className="opacity-80">{doom.comment}</span>
          <span className="ml-auto font-mono text-[12px]">Rate {doom.rate_spread > 0 ? '+' : ''}{doom.rate_spread.toFixed(2)}%p  ·  HY {doom.hy_spread.toFixed(0)} bps</span>
        </div>
      )}

      {/* ── Main layout ── */}
      <div className="flex flex-1 min-h-0">

        {/* ═══ LEFT PANEL — flex-1 expands into freed space when right panel closes ═══ */}
        <div className="overflow-y-auto border-r border-[#1e2d40] flex-1 min-w-0">

          {/* A: Equity Curve */}
          <EquityCurve curveQ={curveQ} />

          {/* B: Holdings + Sectors */}
          <div className="flex border-b border-[#1e2d40]" style={{ height: '330px' }}>
            <div className="border-r border-[#1e2d40] overflow-hidden" style={{ width: '55%' }}>
              <HoldingsPanel holdQ={holdQ} />
            </div>
            <div className="overflow-hidden" style={{ width: '45%' }}>
              <SectorsPanel sectorData={sectorQ.data || {}} />
            </div>
          </div>

          {/* C: Tabbed bottom */}
          <div style={{ minHeight: '300px' }}>
            <div className="flex bg-[#060b14] border-b border-[#1e2d40] sticky top-0 z-10">
              {BOT_TABS.map((t, i) => (
                <button key={t} onClick={() => setBotTab(i)}
                  className={cn('px-5 py-2.5 text-[11px] font-bold tracking-widest transition-colors uppercase',
                    botTab === i
                      ? 'text-[#3b82f6] border-b-2 border-[#3b82f6]'
                      : 'text-[#64748b] hover:text-[#94a3b8]'
                  )}>
                  {t}
                </button>
              ))}
            </div>
            <div className="p-4">
              {botTab === 0 && (corrQ.data
                ? <CorrelationHeatmap data={corrQ.data} />
                : <span className="text-sm text-[#64748b]">로드 중…</span>
              )}
              {botTab === 1 && earningsQ.data && (
                <table className="w-full">
                  <thead>
                    <tr className="text-[#64748b] border-b border-[#1e2d40]">
                      {['Ticker', '실적발표일', '배당락일', '배당수익률'].map(h => (
                        <th key={h} className="text-left py-2.5 px-3 font-semibold text-[12px]">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {earningsQ.data.map(e => (
                      <tr key={e.ticker} className="border-b border-[#0f172a] hover:bg-[#0a1020]">
                        <td className="py-2.5 px-3 font-mono font-bold text-base text-[#e2e8f0]">{e.ticker}</td>
                        <td className="py-2.5 px-3 text-sm text-[#94a3b8]">{e.earn_date}</td>
                        <td className="py-2.5 px-3 text-sm text-[#94a3b8]">{e.div_date}</td>
                        <td className="py-2.5 px-3 text-sm text-[#10b981] font-mono font-bold">{e.div_yield}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {botTab === 2 && macroQ.data && (
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: 'Fed Rate',      value: `${macroQ.data.fed_rate}%` },
                    { label: 'Unemployment',  value: `${macroQ.data.unemployment}%` },
                    { label: 'CPI (YoY)',     value: `${macroQ.data.cpi}%` },
                    { label: 'GDP Growth',    value: `${macroQ.data.gdp}%` },
                    { label: '10Y-2Y Spread', value: `${macroQ.data.t10y2y >= 0 ? '+' : ''}${macroQ.data.t10y2y.toFixed(2)}%p`, color: macroQ.data.t10y2y < 0 ? '#ef4444' : '#10b981' },
                    { label: 'HY Spread',     value: `${macroQ.data.bamlh0a0hym2.toFixed(0)} bps`, color: macroQ.data.bamlh0a0hym2 > 500 ? '#ef4444' : '#f59e0b' },
                  ].map(item => (
                    <div key={item.label} className="bg-[#060b14] border border-[#1e2d40] rounded p-3">
                      <div className="text-[11px] text-[#64748b] font-bold tracking-wider uppercase mb-1">{item.label}</div>
                      <div className="text-2xl font-mono font-bold" style={{ color: (item as any).color || '#e2e8f0' }}>{item.value}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ═══ RIGHT PANEL — collapsible ═══ */}
        {rightOpen ? (
          <div className="flex flex-col min-h-0 flex-shrink-0" style={{ width: '30%', minWidth: '260px' }}>
            {/* Tab bar + collapse button */}
            <div className="flex-shrink-0 bg-[#060b14] border-b border-[#1e2d40] flex items-center">
              <div className="flex flex-1">
                {RIGHT_TABS.map((t, i) => (
                  <button key={t} onClick={() => setRightTab(i)}
                    className={cn('flex-1 py-2.5 text-[10px] font-bold tracking-widest uppercase transition-colors',
                      rightTab === i
                        ? 'text-[#3b82f6] border-b-2 border-[#3b82f6] bg-[#3b82f6]/5'
                        : 'text-[#64748b] hover:text-[#94a3b8]'
                    )}>
                    {t}
                  </button>
                ))}
              </div>
              <button
                onClick={() => setRightOpen(false)}
                title="패널 닫기"
                className="flex-shrink-0 px-2.5 py-2.5 text-[#374151] hover:text-[#64748b] hover:bg-[#0f172a] transition-colors border-l border-[#1e2d40]"
              >
                <PanelRightClose className="w-4 h-4" />
              </button>
            </div>

            {/* Content */}
            <div className="flex-1 min-h-0 overflow-hidden">
              {rightTab === 0 && <DailyBriefPanel />}

              {rightTab === 1 && (
                <div className="h-full overflow-y-auto p-4">
                  {feedbackQ.isLoading && <div className="text-sm text-[#64748b] py-4 text-center">로드 중…</div>}
                  {feedbackQ.data && (
                    <div>
                      <div className="flex items-center gap-2 mb-3">
                        <MessageSquare className="w-4 h-4 text-[#3b82f6]" />
                        <span className="text-[11px] text-[#64748b] font-bold tracking-widest">AI ANALYST</span>
                      </div>
                      <p className="text-sm text-[#94a3b8] leading-relaxed whitespace-pre-wrap">{feedbackQ.data.feedback}</p>
                    </div>
                  )}
                </div>
              )}

              {rightTab === 2 && (
                <div className="h-full overflow-y-auto p-3 space-y-3">
                  <button onClick={() => scanMut.mutate()} disabled={scanMut.isPending}
                    className="w-full flex items-center justify-center gap-2 py-2.5 bg-[#3b82f6]/10 border border-[#3b82f6]/25 text-[#3b82f6] text-sm rounded hover:bg-[#3b82f6]/18 disabled:opacity-50 font-bold">
                    <Zap className="w-4 h-4" />
                    {scanMut.isPending ? '스캔 중…' : 'SCAN UNIVERSE'}
                  </button>
                  {scanQ.data && (
                    <>
                      <div className="text-[11px] text-[#10b981] font-bold tracking-widest mt-1">LONG TOP PICKS</div>
                      {(scanQ.data.long_picks || []).slice(0, 5).map(p => (
                        <div key={p.ticker} className="bg-[#060b14] border border-[#1e2d40] rounded p-3">
                          <div className="flex justify-between items-center">
                            <span className="font-mono font-bold text-base text-[#e2e8f0]">{p.ticker}</span>
                            <span className="text-sm text-[#10b981] font-mono font-bold">+{p.upside?.toFixed(1)}%</span>
                          </div>
                          <div className="text-[12px] text-[#64748b] mt-0.5">{p.method}</div>
                          <div className="text-[12px] text-[#4a5568] mt-0.5 truncate">
                            ${p.entry} → ${p.target} | stop ${p.stop}
                          </div>
                        </div>
                      ))}
                      <div className="text-[11px] text-[#ef4444] font-bold tracking-widest mt-1">SHORT TOP PICKS</div>
                      {(scanQ.data.short_picks || []).slice(0, 3).map(p => (
                        <div key={p.ticker} className="bg-[#060b14] border border-[#1e2d40] rounded p-3">
                          <div className="flex justify-between items-center">
                            <span className="font-mono font-bold text-base text-[#e2e8f0]">{p.ticker}</span>
                            <span className="text-sm text-[#ef4444] font-mono font-bold">{p.downside?.toFixed(1)}%</span>
                          </div>
                          <div className="text-[12px] text-[#64748b] mt-0.5">{p.method}</div>
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}

              {rightTab === 3 && (
                <div className="h-full overflow-y-auto divide-y divide-[#0f172a]">
                  {(newsQ.data || []).map((n, i) => (
                    <a key={i} href={n.url} target="_blank" rel="noreferrer"
                      className="block p-3.5 hover:bg-[#0a1525] transition-colors">
                      <div className="flex items-center gap-2 mb-2">
                        <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded',
                          n.ticker === 'MACRO' ? 'bg-[#9b59b6]/20 text-[#9b59b6]' : 'bg-[#3b82f6]/20 text-[#3b82f6]')}>
                          {n.ticker}
                        </span>
                        <span className="text-[11px] text-[#64748b]">
                          {new Date(n.datetime * 1000).toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric' })}
                        </span>
                      </div>
                      <p className="text-sm text-[#94a3b8] leading-relaxed line-clamp-2">{n.headline}</p>
                    </a>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          /* ── Collapsed rail ── */
          <div className="flex flex-col items-center bg-[#060b14] border-l border-[#1e2d40] flex-shrink-0 py-3 gap-3"
            style={{ width: '32px' }}>
            <button
              onClick={() => setRightOpen(true)}
              title="패널 열기"
              className="text-[#64748b] hover:text-[#3b82f6] transition-colors"
            >
              <PanelRightOpen className="w-4 h-4" />
            </button>
            {RIGHT_TABS.map((t, i) => (
              <button key={t}
                onClick={() => { setRightTab(i); setRightOpen(true) }}
                title={t}
                className={cn(
                  'text-[9px] font-bold tracking-widest uppercase transition-colors px-0.5',
                  rightTab === i ? 'text-[#3b82f6]' : 'text-[#374151] hover:text-[#64748b]'
                )}
                style={{ writingMode: 'vertical-rl', textOrientation: 'mixed', transform: 'rotate(180deg)' }}
              >
                {t}
              </button>
            ))}
            <button onClick={() => {
              qc.invalidateQueries({ queryKey: ['analyst-feedback'] })
              qc.invalidateQueries({ queryKey: ['market-snapshot'] })
            }} className="text-[#374151] hover:text-[#64748b] transition-colors mt-auto">
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>

      <style>{`
        @keyframes marquee { 0%{transform:translateX(0)} 100%{transform:translateX(-50%)} }
        .animate-marquee { animation: marquee 50s linear infinite; }

        .brief-md h1,.brief-md h2,.brief-md h3 { font-size:13px; color:#cbd5e1; font-weight:700; margin-top:12px; margin-bottom:4px; }
        .brief-md h1 { font-size:14px; color:#e2e8f0; border-bottom:1px solid #1e2d40; padding-bottom:5px; }
        .brief-md p  { font-size:13px; color:#94a3b8; line-height:1.65; margin-bottom:6px; }
        .brief-md strong { color:#e2e8f0; }
        .brief-md ul,.brief-md ol { font-size:12px; color:#94a3b8; padding-left:16px; margin-bottom:6px; }
        .brief-md li { margin-bottom:3px; }
        .brief-md hr { border-color:#1e2d40; margin:8px 0; }
        .brief-md code { background:#0f172a; color:#10b981; padding:2px 5px; border-radius:3px; font-size:12px; }
        .brief-md blockquote { border-left:2px solid #1d4ed8; padding-left:10px; color:#64748b; margin:6px 0; }
        .brief-md table { font-size:12px; width:100%; }
        .brief-md th { color:#64748b; border-bottom:1px solid #1e2d40; padding:4px 6px; text-align:left; font-weight:600; }
        .brief-md td { color:#94a3b8; padding:4px 6px; border-bottom:1px solid #0f172a; }
      `}</style>
    </div>
  )
}
