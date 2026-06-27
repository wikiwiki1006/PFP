import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Brain, ChevronDown, ChevronRight, Play } from 'lucide-react'
import LoadingSpinner, { ErrorMessage } from '@/components/LoadingSpinner'
import { getMacroModes, runMacroAnalysis } from '@/api'
import type { MacroAnalysisResult, MacroAgent } from '@/types'
import { cn, ratingColor } from '@/lib/utils'

function AgentCard({ agent, index }: { agent: MacroAgent; index: number }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="border border-[#1e2d40] rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-[#1a2540] transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="w-6 h-6 rounded-full bg-[#3b82f6]/20 border border-[#3b82f6]/30 flex items-center justify-center text-xs font-mono text-[#3b82f6]">
            {index + 1}
          </div>
          <span className="text-sm font-medium text-[#e2e8f0]">{agent.name}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-[#64748b] font-mono">{agent.elapsed?.toFixed(1)}s</span>
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-[#64748b]" />
          ) : (
            <ChevronRight className="w-4 h-4 text-[#64748b]" />
          )}
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4 border-t border-[#1e2d40]">
          <p className="text-sm text-[#e2e8f0] leading-relaxed whitespace-pre-wrap mt-3">
            {agent.text}
          </p>
        </div>
      )}
    </div>
  )
}

function VerdictCardItem({
  category,
  rating,
  rationale,
  risk_level,
}: {
  category: string
  rating: string
  rationale: string
  risk_level: string
}) {
  const color = ratingColor(rating)
  const riskColors: { [k: string]: string } = {
    low: '#10b981',
    medium: '#f59e0b',
    high: '#ef4444',
    critical: '#ef4444',
  }
  const riskColor = riskColors[risk_level?.toLowerCase()] ?? '#64748b'

  return (
    <div
      className="bg-[#0b0f1a] border rounded-lg p-4"
      style={{ borderColor: `${color}30` }}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <h3 className="text-sm font-semibold text-[#e2e8f0]">{category}</h3>
        <span
          className="text-xs px-2 py-0.5 rounded font-medium flex-shrink-0"
          style={{ backgroundColor: `${color}20`, color }}
        >
          {rating}
        </span>
      </div>
      <p className="text-xs text-[#64748b] leading-relaxed">{rationale}</p>
      {risk_level && (
        <div className="mt-2 flex items-center gap-1">
          <span className="text-xs text-[#64748b]">Risk:</span>
          <span
            className="text-xs font-medium capitalize"
            style={{ color: riskColor }}
          >
            {risk_level}
          </span>
        </div>
      )}
    </div>
  )
}

const EXAMPLE_EVENTS = [
  'Federal Reserve raises interest rates by 50 basis points',
  'US CPI comes in at 4.2% YoY, above expectations',
  'Banking sector faces liquidity crisis, 3 regional banks fail',
  'China GDP growth slows to 3.5% annual rate',
  'Oil prices spike 20% due to Middle East tensions',
]

export default function MacroAnalysis() {
  const [event, setEvent] = useState('')
  const [model, setModel] = useState('haiku')
  const [mode, setMode] = useState('standard')
  const [result, setResult] = useState<MacroAnalysisResult | null>(null)

  const modesQ = useQuery({ queryKey: ['macro-modes'], queryFn: getMacroModes })

  const analyzeMutation = useMutation({
    mutationFn: () => runMacroAnalysis({ event, model, mode }),
    onSuccess: setResult,
  })

  const models = modesQ.data?.models ?? ['sonnet', 'haiku']
  const modes = Object.keys(modesQ.data?.modes ?? { fast: [], standard: [], full: [] })

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Brain className="w-5 h-5 text-[#3b82f6]" />
        <div>
          <h1 className="text-xl font-bold text-[#e2e8f0]">AI Macro Analysis</h1>
          <p className="text-sm text-[#64748b] mt-0.5">
            Multi-agent analysis of macroeconomic events
          </p>
        </div>
      </div>

      {/* Input Panel */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-4">Event Input</h2>

        <div className="space-y-4">
          <div>
            <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
              Macroeconomic Event
            </label>
            <textarea
              value={event}
              onChange={(e) => setEvent(e.target.value)}
              placeholder="Describe a macroeconomic event to analyze (e.g., 'Federal Reserve raises rates by 75bp, signaling continued hawkish stance')"
              rows={4}
              className="w-full bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6] resize-none placeholder-[#64748b]/50"
            />
          </div>

          <div>
            <div className="text-xs text-[#64748b] mb-2">Quick examples:</div>
            <div className="flex flex-wrap gap-2">
              {EXAMPLE_EVENTS.map((ex) => (
                <button
                  key={ex}
                  onClick={() => setEvent(ex)}
                  className="text-xs px-2 py-1 bg-[#0b0f1a] border border-[#1e2d40] rounded text-[#64748b] hover:text-[#e2e8f0] hover:border-[#3b82f6]/30 transition-colors"
                >
                  {ex.length > 40 ? ex.slice(0, 40) + '...' : ex}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap gap-4">
            <div>
              <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
                Model
              </label>
              <div className="flex gap-2">
                {models.map((m) => (
                  <button
                    key={m}
                    onClick={() => setModel(m)}
                    className={cn(
                      'px-3 py-1.5 text-xs rounded capitalize transition-colors',
                      model === m
                        ? 'bg-[#3b82f6] text-white'
                        : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]'
                    )}
                  >
                    {m}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="text-xs text-[#64748b] uppercase tracking-wider block mb-2">
                Analysis Mode
              </label>
              <div className="flex gap-2">
                {modes.map((m) => (
                  <button
                    key={m}
                    onClick={() => setMode(m)}
                    className={cn(
                      'px-3 py-1.5 text-xs rounded capitalize transition-colors',
                      mode === m
                        ? 'bg-[#8b5cf6] text-white'
                        : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]'
                    )}
                  >
                    {m}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <button
            onClick={() => analyzeMutation.mutate()}
            disabled={analyzeMutation.isPending || !event.trim()}
            className="flex items-center gap-2 px-5 py-2.5 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm font-medium rounded transition-colors"
          >
            {analyzeMutation.isPending ? (
              <>
                <LoadingSpinner size="sm" />
                Analyzing...
              </>
            ) : (
              <>
                <Play className="w-4 h-4" />
                Run Analysis
              </>
            )}
          </button>

          {analyzeMutation.isError && (
            <ErrorMessage
              message="Analysis failed. Please try again."
              retry={() => analyzeMutation.mutate()}
            />
          )}
        </div>
      </div>

      {/* Loading State */}
      {analyzeMutation.isPending && (
        <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-8">
          <div className="flex flex-col items-center gap-4">
            <div className="relative">
              <div className="w-16 h-16 rounded-full border-2 border-[#3b82f6]/20 border-t-[#3b82f6] animate-spin" />
              <Brain className="absolute inset-0 m-auto w-6 h-6 text-[#3b82f6]" />
            </div>
            <div className="text-center">
              <p className="text-sm font-medium text-[#e2e8f0]">Multi-agent analysis in progress</p>
              <p className="text-xs text-[#64748b] mt-1">
                Running {mode} mode with {model} model...
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Results */}
      {result && !analyzeMutation.isPending && (
        <>
          {/* Event */}
          <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
            <div className="text-xs text-[#64748b] uppercase tracking-wider mb-2">Analyzed Event</div>
            <p className="text-sm text-[#e2e8f0]">{result.event}</p>
          </div>

          {/* Agent Results */}
          {result.agents && result.agents.length > 0 && (
            <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
              <h2 className="text-sm font-semibold text-[#e2e8f0] mb-3">
                Agent Analyses ({result.agents.length} agents)
              </h2>
              <div className="space-y-2">
                {result.agents.map((agent, i) => (
                  <AgentCard key={agent.id ?? i} agent={agent} index={i} />
                ))}
              </div>
            </div>
          )}

          {/* Verdict Cards */}
          {result.verdict_cards && result.verdict_cards.length > 0 && (
            <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
              <h2 className="text-sm font-semibold text-[#e2e8f0] mb-3">
                Verdict Cards
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {result.verdict_cards.map((card, i) => (
                  <VerdictCardItem
                    key={i}
                    category={card.category}
                    rating={card.rating}
                    rationale={card.rationale}
                    risk_level={card.risk_level}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Portfolio Actions */}
          {result.portfolio_actions && result.portfolio_actions.length > 0 && (
            <div className="bg-[#111827] border border-[#1e2d40] rounded-lg">
              <div className="px-4 py-3 border-b border-[#1e2d40]">
                <h2 className="text-sm font-semibold text-[#e2e8f0]">Suggested Portfolio Actions</h2>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[#1e2d40]">
                      <th className="py-2.5 px-4 text-left text-xs font-medium text-[#64748b] uppercase tracking-wider">Action</th>
                      <th className="py-2.5 px-4 text-left text-xs font-medium text-[#64748b] uppercase tracking-wider">Ticker</th>
                      <th className="py-2.5 px-4 text-left text-xs font-medium text-[#64748b] uppercase tracking-wider">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.portfolio_actions.map((action, i) => (
                      <tr
                        key={i}
                        className={cn(
                          'border-b border-[#1e2d40]/30 hover:bg-[#1a2540] transition-colors',
                          i % 2 === 1 ? 'bg-[#0f1724]' : ''
                        )}
                      >
                        <td className="py-2.5 px-4">
                          <span
                            className={cn(
                              'text-xs px-2 py-0.5 rounded font-medium',
                              String(action.action).toUpperCase().includes('BUY') ||
                              String(action.action).toUpperCase().includes('INCREASE')
                                ? 'bg-[#10b981]/10 text-[#10b981]'
                                : String(action.action).toUpperCase().includes('SELL') ||
                                  String(action.action).toUpperCase().includes('REDUCE')
                                ? 'bg-[#ef4444]/10 text-[#ef4444]'
                                : 'bg-[#64748b]/10 text-[#64748b]'
                            )}
                          >
                            {String(action.action)}
                          </span>
                        </td>
                        <td className="py-2.5 px-4 font-mono font-semibold text-[#e2e8f0]">
                          {String(action.ticker ?? '—')}
                        </td>
                        <td className="py-2.5 px-4 text-[#64748b] text-xs">
                          {String(action.reason ?? '—')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
