import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Globe, Play, ChevronDown, ChevronRight } from 'lucide-react'
import LoadingSpinner, { ErrorMessage } from '@/components/LoadingSpinner'
import { getMacroModes, runMacroAnalysis } from '@/api'
import type { MacroAnalysisResult, MacroAgent, VerdictCard } from '@/types'
import { cn } from '@/lib/utils'

// 백엔드의 color 값("danger","warning","success","info")을 CSS 색상으로 변환
function verdictColor(card: VerdictCard): string {
  const c = (card.color ?? '').toLowerCase()
  if (c === 'danger')  return '#ef4444'
  if (c === 'warning') return '#f59e0b'
  if (c === 'success') return '#10b981'
  if (c === 'info')    return '#3b82f6'
  // 구버전 rating 필드 fallback
  const r = (card.rating ?? '').toLowerCase()
  if (r.includes('bull') || r.includes('positive') || r.includes('buy')) return '#10b981'
  if (r.includes('bear') || r.includes('negative') || r.includes('sell')) return '#ef4444'
  if (r.includes('neutral') || r.includes('hold')) return '#f59e0b'
  return '#64748b'
}

const PRESETS = [
  { label: 'Fed Shock', icon: '🏦', event: 'Federal Reserve raises interest rates by 75bp, signals further hikes ahead amid persistent inflation. 10Y Treasury yield surges past 5%, mortgage rates hit 8%.' },
  { label: 'Taiwan Blockade', icon: '🚢', event: "China imposes naval blockade on Taiwan Strait, halting 40% of global container shipping. TSMC semiconductor supply disrupted, tech supply chains at risk." },
  { label: 'Bank Crisis', icon: '🏧', event: '3 major US regional banks collapse amid commercial real estate losses. FDIC intervenes, interbank lending freezes, credit spreads spike 400bps.' },
  { label: 'OPEC+ Cut', icon: '🛢️', event: 'OPEC+ announces surprise 2M barrels/day production cut. WTI crude surges to $120/barrel. Energy inflation reignites, stagflation fears return.' },
  { label: 'Trade War 2.0', icon: '⚔️', event: 'US imposes 60% blanket tariffs on all Chinese imports. China retaliates with rare earth export ban. Global trade volumes expected to fall 15%.' },
  { label: 'AI Bubble Burst', icon: '💥', event: 'Major AI company reports hyperscaler capex cuts; NVIDIA warns of demand slowdown. AI-related stocks drop 40% in one week. Margin calls sweep hedge funds.' },
  { label: 'Debt Ceiling Crisis', icon: '💰', event: 'US Congress fails to raise debt ceiling, technical default on T-bills triggers global dollar selloff. DXY falls 12%, gold surges to $3,000/oz.' },
  { label: 'Soft Landing', icon: '🛬', event: 'Fed pivots to 50bp rate cuts as CPI hits 2.1%, unemployment stays at 4.2%. GDP growth accelerates to 2.8%. Risk-on rally across all asset classes.' },
]

function AgentCard({ agent, index }: { agent: MacroAgent; index: number }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="border border-[#1e2d40] rounded overflow-hidden">
      <button onClick={() => setExpanded(e => !e)} className="w-full flex items-center justify-between px-3 py-2.5 hover:bg-[#0a1628] transition-colors">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded-full bg-[#9b59b6]/20 border border-[#9b59b6]/30 flex items-center justify-center text-[10px] font-mono text-[#9b59b6]">{index + 1}</div>
          <span className="text-xs font-medium text-[#e2e8f0]">{agent.name}</span>
        </div>
        <div className="flex items-center gap-2">
          {agent.elapsed != null && <span className="text-[10px] text-[#374151] font-mono">{agent.elapsed.toFixed(1)}s</span>}
          {expanded ? <ChevronDown className="w-3 h-3 text-[#4a5568]" /> : <ChevronRight className="w-3 h-3 text-[#4a5568]" />}
        </div>
      </button>
      {expanded && (
        <div className="px-3 pb-3 border-t border-[#1e2d40]">
          <p className="text-xs text-[#94a3b8] leading-relaxed whitespace-pre-wrap mt-2">{agent.text}</p>
        </div>
      )}
    </div>
  )
}

export default function MacroScenario() {
  const [event, setEvent] = useState('')
  const [model, setModel] = useState('haiku')
  const [mode, setMode] = useState('standard')
  const [result, setResult] = useState<MacroAnalysisResult | null>(null)

  const modesQ = useQuery({ queryKey: ['macro-modes'], queryFn: getMacroModes })
  const analyzeMut = useMutation({ mutationFn: () => runMacroAnalysis({ event, model, mode }), onSuccess: setResult })

  const models = modesQ.data?.models ?? ['sonnet', 'haiku']
  const modes = Object.keys(modesQ.data?.modes ?? { fast: [], standard: [], full: [] })

  return (
    <div className="p-5 space-y-4 max-w-full">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Globe className="w-4 h-4 text-[#9b59b6]" />
        <div>
          <h1 className="text-base font-bold text-[#e2e8f0]">MACRO SCENARIO ANALYSIS</h1>
          <p className="text-[11px] text-[#4a5568]">9-에이전트 병렬 멀티 파이프라인 · 이벤트 충격 → 포트폴리오 액션 플랜</p>
        </div>
      </div>

      {/* Preset Scenarios */}
      <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3">
        <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-2">PRESET SCENARIOS</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {PRESETS.map(p => (
            <button key={p.label} onClick={() => setEvent(p.event)}
              className={cn(
                'text-left p-2 rounded border transition-all text-xs',
                event === p.event ? 'border-[#9b59b6]/50 bg-[#9b59b6]/10 text-[#c084fc]' : 'border-[#1e2d40] text-[#64748b] hover:border-[#1e2d40]/80 hover:text-[#94a3b8] hover:bg-[#0a1628]'
              )}>
              <span className="mr-1">{p.icon}</span>
              <span className="font-medium">{p.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Event Input */}
      <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3 space-y-3">
        <textarea value={event} onChange={e => setEvent(e.target.value)} rows={3}
          placeholder="매크로 이벤트를 직접 입력하거나 위 프리셋을 선택하세요..."
          className="w-full bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-xs text-[#e2e8f0] focus:outline-none focus:border-[#9b59b6] resize-none placeholder-[#374151]" />

        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-1.5">MODEL</div>
            <div className="flex gap-1.5">
              {models.map(m => (
                <button key={m} onClick={() => setModel(m)}
                  className={cn('px-2.5 py-1 text-[10px] rounded capitalize font-medium transition-colors',
                    model === m ? 'bg-[#9b59b6] text-white' : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]'
                  )}>{m}</button>
              ))}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-1.5">ANALYSIS MODE</div>
            <div className="flex gap-1.5">
              {modes.map(m => (
                <button key={m} onClick={() => setMode(m)}
                  className={cn('px-2.5 py-1 text-[10px] rounded capitalize font-medium transition-colors',
                    mode === m ? 'bg-[#3b82f6] text-white' : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]'
                  )}>{m}</button>
              ))}
            </div>
          </div>
          <button onClick={() => analyzeMut.mutate()} disabled={analyzeMut.isPending || !event.trim()}
            className="flex items-center gap-1.5 px-4 py-2 bg-[#9b59b6] hover:bg-[#7c3aed] disabled:opacity-50 text-white text-xs font-bold rounded transition-colors ml-auto">
            <Play className="w-3 h-3" />
            {analyzeMut.isPending ? 'ANALYZING...' : 'RUN ANALYSIS'}
          </button>
        </div>
        {analyzeMut.isError && <ErrorMessage message="분석 실패. 다시 시도해주세요." retry={() => analyzeMut.mutate()} />}
      </div>

      {/* Loading */}
      {analyzeMut.isPending && (
        <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-6 flex flex-col items-center gap-3">
          <div className="relative">
            <div className="w-12 h-12 rounded-full border-2 border-[#9b59b6]/20 border-t-[#9b59b6] animate-spin" />
            <Globe className="absolute inset-0 m-auto w-4 h-4 text-[#9b59b6]" />
          </div>
          <p className="text-xs text-[#64748b]">9-에이전트 병렬 분석 중... ({mode} mode, {model} model)</p>
        </div>
      )}

      {/* Results */}
      {result && !analyzeMut.isPending && (
        <div className="space-y-3">
          {/* Event Summary */}
          <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3">
            <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-1">ANALYZED EVENT</div>
            <p className="text-xs text-[#94a3b8] leading-relaxed">{result.event}</p>
          </div>

          {/* Verdict Cards Grid */}
          {result.verdict_cards && result.verdict_cards.length > 0 && (
            <div>
              <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-2">VERDICT CARDS</div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                {result.verdict_cards.map((card, i) => {
                  const color = verdictColor(card)
                  const label = card.title ?? card.category ?? '—'
                  const sub   = card.headline ?? card.rating ?? ''
                  const body  = card.summary ?? card.rationale ?? ''
                  const detail = card.details ?? ''
                  return (
                    <div key={i} className="bg-[#060b14] border rounded-lg p-3 flex flex-col gap-1.5" style={{ borderColor: `${color}30` }}>
                      <div className="flex items-center gap-1.5">
                        {card.icon && <span className="text-base leading-none">{card.icon}</span>}
                        <span className="text-xs font-bold text-[#e2e8f0] leading-tight">{label}</span>
                      </div>
                      {sub && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded font-semibold self-start" style={{ backgroundColor: `${color}20`, color }}>
                          {sub}
                        </span>
                      )}
                      {body && <p className="text-[10px] text-[#94a3b8] leading-relaxed">{body}</p>}
                      {detail && <p className="text-[9px] text-[#64748b] leading-relaxed border-t border-[#1e2d40] pt-1.5 mt-0.5">{detail}</p>}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Agent Results */}
          {result.agents && result.agents.length > 0 && (
            <div>
              <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-2">AGENT ANALYSES ({result.agents.length})</div>
              <div className="space-y-1">
                {result.agents.map((agent, i) => <AgentCard key={agent.id ?? i} agent={agent} index={i} />)}
              </div>
            </div>
          )}

          {/* Portfolio Actions */}
          {result.portfolio_actions && result.portfolio_actions.length > 0 && (
            <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg overflow-hidden">
              <div className="px-3 py-2 border-b border-[#1e2d40]">
                <div className="text-[10px] text-[#4a5568] font-bold tracking-wider">PORTFOLIO ACTION PLAN</div>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[#1e2d40]">
                    {['ACTION', 'TICKER', 'REASON'].map(h => <th key={h} className="py-2 px-3 text-left text-[10px] font-bold text-[#4a5568] tracking-wider">{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {result.portfolio_actions.map((a, i) => {
                    const act = String(a.action).toUpperCase()
                    const isBuy = act.includes('BUY') || act.includes('INCREASE')
                    const isSell = act.includes('SELL') || act.includes('REDUCE')
                    return (
                      <tr key={i} className="border-b border-[#0f172a] hover:bg-[#0a1628]">
                        <td className="py-2 px-3">
                          <span className={cn('text-[10px] px-2 py-0.5 rounded font-bold',
                            isBuy ? 'bg-[#10b981]/20 text-[#10b981]' : isSell ? 'bg-[#ef4444]/20 text-[#ef4444]' : 'bg-[#64748b]/20 text-[#64748b]'
                          )}>{String(a.action)}</span>
                        </td>
                        <td className="py-2 px-3 font-mono font-bold text-[#e2e8f0]">{String(a.ticker ?? '—')}</td>
                        <td className="py-2 px-3 text-[#64748b]">{String(a.reason ?? '—')}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
