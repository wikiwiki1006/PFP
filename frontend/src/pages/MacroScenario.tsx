import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Globe, Play, ChevronDown, ChevronRight, Download, History, X, Square, Lock } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ErrorMessage } from '@/components/LoadingSpinner'
import { FinancialTips } from '@/components/FinancialTips'
import { getMacroModes, startMacroAnalysis, getMacroJob, cancelMacroJob, getHoldings, getMacroReportHistory, getMacroReportFile } from '@/api'
import type { MacroAnalysisResult, MacroAgent } from '@/types'
import { cn } from '@/lib/utils'
import { useLoginPrompt } from '@/components/auth/LockedPreview'
import { useDemoQuery } from '@/lib/useDemoQuery'
import { DEMO_HOLDINGS_RAW } from '@/lib/demoData'

// ── sessionStorage 키 ──────────────────────────────────────────────────────────
const SK_PENDING   = 'macro_pending'
const SK_START     = 'macro_start'
const SK_RESULT    = 'macro_result'
const SK_EVENT     = 'macro_event'
const SK_MODEL     = 'macro_model'
const SK_MODE      = 'macro_mode'
const SK_JOB_ID    = 'macro_job_id'

// 모드별 예상 소요 시간 (ms)
const MODE_MAX_MS: Record<string, number> = {
  fast: 30_000, standard: 65_000, full: 130_000,
}

// 경과 시간별 진행 단계 메시지
function stageLabel(elapsedMs: number, mode: string): string {
  if (mode === 'fast') {
    return elapsedMs < 8_000  ? '이벤트 데이터 수집 중...'
         : elapsedMs < 20_000 ? '에이전트 분석 중...'
         : '최종 판정 생성 중...'
  }
  return elapsedMs < 10_000 ? '이벤트·시장 데이터 수집 중...'
       : elapsedMs < 40_000 ? '에이전트 병렬 분석 중...'
       : '결과 종합 및 최종 판정 생성 중...'
}

// ── 상수 ──────────────────────────────────────────────────────────────────────
const MODE_LABELS: Record<string, string> = {
  fast: '빠름', standard: '표준', full: '전체',
}

const MODE_DESCRIPTIONS: Record<string, string> = {
  fast:     '이벤트분석·시장반응·최종판정 3개 에이전트',
  standard: '5개 에이전트 · 포트폴리오 액션 포함',
  full:     '9개 에이전트 · 전체 심층 분석',
}

const PRESETS = [
  { label: 'Fed 긴축 충격',   icon: '🏦', event: '미국 연방준비제도(Fed)가 기준금리를 75bp 인상하고 지속적인 인플레이션에 대응해 추가 인상을 시사했습니다. 미국 10년물 국채 금리가 5%를 돌파하고 모기지 금리는 8%에 달했습니다.' },
  { label: '대만 해협 봉쇄',  icon: '🚢', event: '중국이 대만 해협에 해군 봉쇄를 단행해 전 세계 컨테이너 물동량의 40%가 차단됐습니다. TSMC 반도체 공급망이 위협받고 글로벌 기술 산업 전반에 걸쳐 공급망 리스크가 급부상했습니다.' },
  { label: '은행 위기',       icon: '🏧', event: '상업용 부동산 손실로 인해 미국 주요 지역 은행 3곳이 연쇄 붕괴했습니다. FDIC가 긴급 개입하고 은행 간 대출이 사실상 중단되었으며 신용 스프레드가 400bp 급등했습니다.' },
  { label: 'OPEC+ 감산',     icon: '🛢️', event: 'OPEC+가 기습적으로 하루 200만 배럴 감산을 발표했습니다. WTI 원유가 배럴당 120달러로 급등하고 에너지 인플레이션이 재점화되면서 스태그플레이션 우려가 다시 고개를 들었습니다.' },
  { label: '무역 전쟁 2.0',  icon: '⚔️', event: '미국이 중국산 전 품목에 60% 일괄 관세를 부과하자 중국이 희토류 수출 금지로 보복했습니다. 글로벌 교역량이 15% 급감할 것으로 예상됩니다.' },
  { label: 'AI 버블 붕괴',   icon: '💥', event: '주요 AI 기업이 하이퍼스케일러 설비투자(CAPEX) 축소를 발표하고 엔비디아가 수요 둔화를 경고했습니다. AI 관련 주가가 일주일 만에 40% 폭락하고 헤지펀드의 마진콜이 연쇄적으로 터졌습니다.' },
  { label: '부채 한도 위기', icon: '💰', event: '미국 의회가 부채 한도 상향에 실패해 단기 국채(T-bill)에 대한 기술적 채무불이행이 발생했습니다. 글로벌 달러 매도세가 확산되어 달러인덱스(DXY)가 12% 급락하고 금값이 온스당 3,000달러까지 폭등했습니다.' },
  { label: '연착륙',         icon: '🛬', event: '연준이 CPI가 2.1%까지 내려오고 실업률이 4.2%를 유지하는 가운데 기준금리를 50bp 인하하는 피벗을 단행했습니다. GDP 성장률이 2.8%로 가속화되면서 모든 자산군에 걸쳐 위험선호(Risk-on) 랠리가 펼쳐졌습니다.' },
]

// Agent 8 은 JSON 원문 대신 ActionTable 표로 렌더링
const ACTION_AGENT_ID  = 8
const VERDICT_AGENT_ID = 9

// ── 판정 카드 렌더러 (Agent 9 전용) ──────────────────────────────────────────

const VERDICT_COLOR_MAP: Record<string, { border: string; badge: string; text: string }> = {
  danger:  { border: '#ef4444', badge: 'bg-[#ef4444]/20 text-[#f87171]',  text: 'text-[#f87171]'  },
  warning: { border: '#f59e0b', badge: 'bg-[#f59e0b]/20 text-[#fbbf24]',  text: 'text-[#fbbf24]'  },
  success: { border: '#10b981', badge: 'bg-[#10b981]/20 text-[#34d399]',  text: 'text-[#34d399]'  },
  info:    { border: '#3b82f6', badge: 'bg-[#3b82f6]/20 text-[#60a5fa]',  text: 'text-[#60a5fa]'  },
}

function VerdictCardsDisplay({ cards }: { cards: import('@/types').VerdictCard[] }) {
  if (!cards || cards.length === 0) return null
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
      {cards.map((card, i) => {
        const colors = VERDICT_COLOR_MAP[card.color ?? 'info'] ?? VERDICT_COLOR_MAP.info
        return (
          <div
            key={i}
            className="rounded-lg p-4 bg-[#060b14]"
            style={{ border: `1px solid ${colors.border}40` }}
          >
            <div className="flex items-center gap-2 mb-2">
              <span className="text-lg">{card.icon}</span>
              <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${colors.badge}`}>
                {card.title}
              </span>
            </div>
            <p className={`text-sm font-bold mb-1 ${colors.text}`}>{card.headline}</p>
            <p className="text-xs text-[#94a3b8] mb-2">{card.summary}</p>
            {card.details && (
              <p className="text-xs text-[#64748b] leading-relaxed border-t border-[#1e2d40] pt-2 mt-2">
                {card.details}
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── 진행 바 ──────────────────────────────────────────────────────────────────

function ProgressBar({ progress, elapsedMs, mode }: { progress: number; elapsedMs: number; mode: string }) {
  const elapsed = elapsedMs < 1000
    ? `${elapsedMs}ms`
    : `${(elapsedMs / 1000).toFixed(0)}초`

  return (
    <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg px-4 py-3 space-y-2">
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-[#10b981] font-mono flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-[#10b981] animate-pulse inline-block" />
          {stageLabel(elapsedMs, mode)}
        </span>
        <span className="text-[#475569] font-mono">{elapsed} 경과</span>
      </div>
      <div className="w-full bg-[#0f172a] rounded-full h-2 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${progress}%`,
            background: 'linear-gradient(90deg, #7c3aed, #3b82f6)',
          }}
        />
      </div>
      <div className="text-right text-[10px] text-[#475569] font-mono">{Math.round(progress)}%</div>
    </div>
  )
}

// ── AgentCard ─────────────────────────────────────────────────────────────────

function AgentCard({
  agent, index, portfolioActions, verdictCards,
}: {
  agent: MacroAgent
  index: number
  portfolioActions?: Array<Record<string, unknown>>
  verdictCards?: import('@/types').VerdictCard[]
}) {
  const [expanded, setExpanded] = useState(false)
  const isActionAgent  = Number(agent.id) === ACTION_AGENT_ID
  const isVerdictAgent = Number(agent.id) === VERDICT_AGENT_ID

  return (
    <div className="border border-[#1e2d40] rounded overflow-hidden">
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-[#0a1628] transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <div className="w-5 h-5 rounded-full bg-[#9b59b6]/20 border border-[#9b59b6]/30 flex items-center justify-center text-[10px] font-mono text-[#9b59b6]">
            {index + 1}
          </div>
          <span className="text-sm font-semibold text-[#f1f5f9]">{agent.name}</span>
        </div>
        <div className="flex items-center gap-2">
          {agent.elapsed != null && (
            <span className="text-[11px] text-[#475569] font-mono">{agent.elapsed.toFixed(1)}s</span>
          )}
          {expanded
            ? <ChevronDown className="w-4 h-4 text-[#64748b]" />
            : <ChevronRight className="w-4 h-4 text-[#64748b]" />
          }
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-5 border-t border-[#1e2d40] pt-3">
          {isVerdictAgent && verdictCards && verdictCards.length > 0 ? (
            <VerdictCardsDisplay cards={verdictCards} />
          ) : isActionAgent && portfolioActions && portfolioActions.length > 0 ? (
            <ActionTable actions={portfolioActions} />
          ) : (
            <div className="macro-md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{agent.text}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── 포트폴리오 액션 테이블 ────────────────────────────────────────────────────

const URGENCY_COLOR: Record<string, string> = {
  '즉시': '#ef4444', '1개월 내': '#f59e0b', '3개월 내': '#10b981',
}

function ActionTable({ actions }: { actions: Array<Record<string, unknown>> }) {
  if (!actions.length) return null
  return (
    <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg overflow-hidden">
      <div className="px-4 py-2.5 border-b border-[#1e2d40]">
        <span className="text-[11px] text-[#64748b] font-bold tracking-wider">포트폴리오 액션 플랜</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#1e2d40]">
              {['티커', '액션', '시급도', '추천 이유'].map(h => (
                <th key={h} className="py-2.5 px-4 text-left text-[11px] font-bold text-[#64748b] tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {actions.map((a, i) => {
              const act    = String(a.action ?? '').toUpperCase()
              const isBuy  = /BUY|INCREASE|ADD|매수/.test(act)
              const isSell = /SELL|REDUCE|매도/.test(act)
              const urgency = String(a.urgency ?? '—')
              const urgColor = URGENCY_COLOR[urgency] ?? '#94a3b8'
              return (
                <tr key={i} className="border-b border-[#0f172a] hover:bg-[#0a1628]">
                  <td className="py-3 px-4 font-mono font-bold text-[#f1f5f9]">{String(a.ticker ?? '—')}</td>
                  <td className="py-3 px-4">
                    <span className={cn(
                      'text-xs px-2.5 py-1 rounded-full font-bold',
                      isBuy  ? 'bg-[#10b981]/20 text-[#10b981]'
                      : isSell ? 'bg-[#ef4444]/20 text-[#ef4444]'
                      : 'bg-[#64748b]/20 text-[#94a3b8]'
                    )}>
                      {String(a.action ?? '—')}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-xs font-semibold" style={{ color: urgColor }}>{urgency}</td>
                  <td className="py-3 px-4 text-sm text-[#cbd5e1] leading-relaxed">{String(a.reason ?? '—')}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── PDF 빌더 ─────────────────────────────────────────────────────────────────

function mdToHtml(raw: string): string {
  if (!raw) return ''
  let s = raw
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  // Tables
  s = s.replace(/(\|[^\n]+\|\r?\n\|[-:| ]+\|\r?\n(?:\|[^\n]+\|\r?\n)*)/g, (block) => {
    const lines = block.trim().split(/\r?\n/)
    if (lines.length < 3) return block
    const heads = lines[0].split('|').filter(c => c.trim()).map(h => `<th>${h.trim()}</th>`).join('')
    const rows = lines.slice(2).map(row =>
      `<tr>${row.split('|').filter(c => c.trim()).map(c => `<td>${c.trim()}</td>`).join('')}</tr>`
    ).join('')
    return `<table><thead><tr>${heads}</tr></thead><tbody>${rows}</tbody></table>`
  })
  s = s.replace(/^## (.+)$/gm, '<h2>$1</h2>')
  s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>')
  s = s.replace(/^# (.+)$/gm, '<h1>$1</h1>')
  s = s.replace(/^---+$/gm, '<hr>')
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  s = s.replace(/^[-*] (.+)$/gm, '<li>$1</li>')
  s = s.replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
  s = s.replace(/((?:<li>[^\n]*\n?)+)/g, '<ul>$1</ul>')
  s = s.replace(/\n{2,}/g, '</p><p class="p">')
  return `<p class="p">${s}</p>`
}

function buildPdfHtml(result: MacroAnalysisResult, dateStr: string): string {
  function actionTableHtml(actions: Array<Record<string, unknown>>): string {
    if (!actions?.length) return ''
    const rows = actions.map(a => {
      const act = String(a.action ?? '').toUpperCase()
      const isBuy  = /BUY|INCREASE|ADD|매수/.test(act)
      const isSell = /SELL|REDUCE|매도/.test(act)
      const color = isBuy ? '#16a34a' : isSell ? '#dc2626' : '#4b5563'
      const urg = String(a.urgency ?? '—')
      const urgColor = urg === '즉시' ? '#dc2626' : urg === '1개월 내' ? '#d97706' : '#16a34a'
      return `<tr>
        <td style="font-family:monospace;font-weight:700;white-space:nowrap;width:70px">${String(a.ticker ?? '—')}</td>
        <td style="width:80px;white-space:nowrap"><span style="color:${color};font-weight:700;border:1px solid ${color};padding:2px 6px;border-radius:4px;font-size:11px;display:inline-block">${String(a.action ?? '—')}</span></td>
        <td style="color:${urgColor};font-weight:600;white-space:nowrap;width:80px">${urg}</td>
        <td style="word-break:break-word;line-height:1.5">${String(a.reason ?? '—')}</td>
      </tr>`
    }).join('')
    return `<h3>포트폴리오 액션 플랜</h3>
      <table style="table-layout:fixed"><thead><tr>
        <th style="width:70px">티커</th>
        <th style="width:80px">액션</th>
        <th style="width:80px">시급도</th>
        <th>추천 이유</th>
      </tr></thead>
      <tbody>${rows}</tbody></table>`
  }

  function verdictCardsHtml(cards: import('@/types').VerdictCard[]): string {
    if (!cards?.length) return ''
    const colorMap: Record<string, { border: string; bg: string; label: string }> = {
      danger:  { border: '#ef4444', bg: '#fff5f5', label: '#dc2626' },
      warning: { border: '#f59e0b', bg: '#fffbeb', label: '#d97706' },
      success: { border: '#10b981', bg: '#f0fdf4', label: '#16a34a' },
      info:    { border: '#3b82f6', bg: '#eff6ff', label: '#2563eb' },
    }
    const cardItems = cards.map(card => {
      const c = colorMap[card.color ?? 'info'] ?? colorMap.info
      const esc = (s?: string) => (s ?? '').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      return `<div style="border:2px solid ${c.border};background:${c.bg};border-radius:8px;padding:14px;break-inside:avoid">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <span style="font-size:18px">${esc(card.icon)}</span>
          <span style="color:${c.label};font-weight:700;font-size:11px;background:${c.border}30;padding:3px 10px;border-radius:12px">${esc(card.title)}</span>
        </div>
        <p style="color:${c.label};font-weight:700;font-size:13px;margin:0 0 4px 0">${esc(card.headline)}</p>
        <p style="color:#374151;font-size:12px;margin:0 0 6px 0">${esc(card.summary)}</p>
        ${card.details ? `<p style="color:#6b7280;font-size:11px;border-top:1px solid ${c.border}50;padding-top:8px;margin:0;line-height:1.6">${esc(card.details)}</p>` : ''}
      </div>`
    }).join('')
    return `<h3>최종 판정 시나리오</h3>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px">${cardItems}</div>`
  }

  const agentSections = result.agents?.map((agent, i) => {
    const isVerdict = Number(agent.id) === 9
    const isAction  = Number(agent.id) === 8
    const body = isVerdict && result.verdict_cards?.length
      ? verdictCardsHtml(result.verdict_cards)
      : isAction && result.portfolio_actions?.length
      ? actionTableHtml(result.portfolio_actions)
      : mdToHtml(agent.text ?? '')
    return `<div class="agent-card">
      <div class="agent-header">
        <span class="agent-badge">${i + 1}</span>
        <span class="agent-name">${agent.name}</span>
        ${agent.elapsed != null ? `<span class="agent-elapsed">${agent.elapsed.toFixed(1)}s</span>` : ''}
      </div>
      <div class="agent-body">${body}</div>
    </div>`
  }).join('') ?? ''

  return `<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,'Segoe UI',sans-serif;font-size:13px;color:#1f2937;background:#fff;line-height:1.7}
    .hdr{background:linear-gradient(135deg,#1e1b4b,#312e81);padding:24px 40px 20px;color:#fff}
    .hdr .brand{font-size:9px;letter-spacing:4px;color:#c4b5fd;font-weight:700;margin-bottom:6px}
    .hdr .title{font-size:20px;font-weight:900;line-height:1.3}
    .hdr .ev{font-size:11px;color:#a5b4fc;margin-top:6px;line-height:1.5}
    .body{padding:24px 40px}
    .ev-box{background:#f9fafb;border:1px solid #e5e7eb;border-left:4px solid #7c3aed;padding:12px 16px;margin-bottom:20px;border-radius:4px}
    .ev-box .lbl{font-size:10px;font-weight:700;color:#6b7280;letter-spacing:.1em;margin-bottom:4px}
    .ev-box .txt{color:#374151;font-size:13px;line-height:1.6}
    .sec-title{font-size:10px;font-weight:700;color:#6b7280;letter-spacing:.1em;margin-bottom:12px}
    .agent-card{border:1px solid #e5e7eb;border-radius:8px;margin-bottom:14px;overflow:hidden;page-break-inside:avoid}
    .agent-header{display:flex;align-items:center;gap:10px;padding:9px 16px;background:#f3f4f6;border-bottom:1px solid #e5e7eb}
    .agent-badge{width:22px;height:22px;border-radius:50%;background:#7c3aed;color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0}
    .agent-name{font-weight:700;font-size:13px;color:#1f2937;flex:1}
    .agent-elapsed{font-size:11px;color:#9ca3af;font-family:monospace}
    .agent-body{padding:14px 18px;color:#374151}
    h1{font-size:15px;font-weight:700;color:#1e1b4b;margin:14px 0 5px;border-bottom:1px solid #e5e7eb;padding-bottom:3px}
    h2{font-size:14px;font-weight:700;color:#1e40af;margin:12px 0 5px;border-bottom:1px solid #f3f4f6;padding-bottom:2px}
    h3{font-size:13px;font-weight:700;color:#374151;margin:10px 0 4px}
    p.p{margin:5px 0;color:#374151}
    strong{color:#111827;font-weight:700}
    ul{padding-left:18px;margin:5px 0}
    li{margin:2px 0;color:#4b5563}
    hr{border:none;border-top:1px solid #e5e7eb;margin:10px 0}
    table{width:100%;border-collapse:collapse;font-size:12px;margin:10px 0;table-layout:fixed}
    th{background:#f3f4f6;color:#374151;border:1px solid #e5e7eb;padding:7px 10px;font-weight:600;text-align:left;word-break:keep-all}
    td{color:#4b5563;border:1px solid #e5e7eb;padding:6px 10px;word-break:break-word;line-height:1.5}
    tr:nth-child(even) td{background:#f9fafb}
    .footer{margin-top:20px;padding-top:10px;border-top:1px solid #e5e7eb;font-size:10px;color:#9ca3af;text-align:center}
  </style></head><body>
  <div class="hdr">
    <div class="brand">PERSONAL FINANCIAL PLATFORM</div>
    <div class="title">매크로 시나리오 분석 · ${dateStr}</div>
    <div class="ev">${(result.event ?? '').replace(/</g, '&lt;').replace(/>/g, '&gt;').slice(0, 200)}</div>
  </div>
  <div class="body">
    <div class="ev-box">
      <div class="lbl">분석 이벤트</div>
      <div class="txt">${(result.event ?? '').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
    </div>
    <div class="sec-title">에이전트 분석 (${result.agents?.length ?? 0}개)</div>
    ${agentSections}
    <div class="footer">본 레포트는 AI 자동 생성 참고용으로, 투자 조언이 아닙니다.</div>
  </div>
  </body></html>`
}

// ── 메인 페이지 ───────────────────────────────────────────────────────────────

export default function MacroScenario() {
  // 시나리오 분석·과거 이력은 로그인이 필요하다. 예시 분석 결과는 만들지 않는다.
  const { isAuthed, requireLogin, modalEl } = useLoginPrompt()
  // sessionStorage 에서 이전 상태 복원
  const [event,    setEvent]    = useState(() => sessionStorage.getItem(SK_EVENT)    || '')
  const [model,    setModel]    = useState(() => sessionStorage.getItem(SK_MODEL)    || 'sonnet')
  const [mode,     setMode]     = useState(() => sessionStorage.getItem(SK_MODE)     || 'standard')

  const [result, setResult] = useState<MacroAnalysisResult | null>(() => {
    try {
      const s = sessionStorage.getItem(SK_RESULT)
      return s ? JSON.parse(s) : null
    } catch { return null }
  })

  // 새로고침 후 재개할 잡 ID (sessionStorage에서 복원)
  const [jobId, setJobId] = useState<string | null>(
    () => sessionStorage.getItem(SK_JOB_ID)
  )

  // 진행 바 상태
  const [progress,  setProgress]  = useState(0)
  const [elapsedMs, setElapsedMs] = useState(0)

  // PDF / 히스토리 상태
  const [pdfBusy,  setPdfBusy]  = useState(false)
  const [showHist, setShowHist] = useState(false)

  const modesQ    = useQuery({ queryKey: ['macro-modes'], queryFn: getMacroModes })
  const holdingsQ = useDemoQuery(['holdings'], getHoldings, DEMO_HOLDINGS_RAW, { staleTime: 60_000 })
  const histQ     = useQuery({
    queryKey: ['macro-report-history'],
    queryFn:  getMacroReportHistory,
    staleTime: 60_000,
    enabled:  showHist && isAuthed,
  })

  const loadHistMut = useMutation({
    mutationFn: getMacroReportFile,
    onSuccess: (data) => {
      sessionStorage.setItem(SK_RESULT, JSON.stringify(data))
      setResult(data)
      setShowHist(false)
    },
  })

  // POST가 응답 오기 전에 사용자가 중단을 눌렀는지 추적
  const wantCancelRef = useRef(false)

  // 잡 시작 뮤테이션 — 즉시 job_id 반환, 분석은 서버에서 계속 실행
  const startMut = useMutation({
    mutationFn: () => startMacroAnalysis({
      event,
      model,
      mode,
      portfolio: holdingsQ.data as Record<string, unknown> | undefined,
    }),
    onSuccess: ({ job_id }) => {
      if (wantCancelRef.current) {
        wantCancelRef.current = false
        cancelMacroJob(job_id).catch(() => {})
        sessionStorage.removeItem(SK_PENDING)
        sessionStorage.removeItem(SK_JOB_ID)
        return
      }
      sessionStorage.setItem(SK_JOB_ID,  job_id)
      sessionStorage.setItem(SK_PENDING, '1')
      setJobId(job_id)
    },
    onError: () => {
      wantCancelRef.current = false
      sessionStorage.removeItem(SK_PENDING)
      setProgress(0)
    },
  })

  // 잡 상태 폴링 — jobId 있는 동안 3초마다 서버에 질의
  const pollQ = useQuery({
    queryKey: ['macro-job', jobId],
    queryFn:  () => getMacroJob(jobId!),
    enabled:  !!jobId,
    refetchInterval: 3_000,
    refetchIntervalInBackground: true,
    staleTime: 0,
    retry: false,
  })

  // 잡 취소 뮤테이션
  const cancelMut = useMutation({
    mutationFn: (id: string) => cancelMacroJob(id),
    onSettled: () => {
      sessionStorage.removeItem(SK_PENDING)
      sessionStorage.removeItem(SK_JOB_ID)
      setJobId(null)
      setProgress(0)
    },
  })

  // 분석 중단 핸들러
  const cancelAnalysis = () => {
    if (jobId) {
      cancelMut.mutate(jobId)
    } else if (startMut.isPending) {
      // POST 응답 오기 전: onSuccess에서 처리하도록 플래그 세팅
      wantCancelRef.current = true
      sessionStorage.removeItem(SK_PENDING)
      sessionStorage.removeItem(SK_JOB_ID)
      setProgress(0)
    }
  }

  // 폴링 결과 처리
  useEffect(() => {
    if (!pollQ.data) return
    if (pollQ.data.status === 'done' && pollQ.data.result) {
      const r = pollQ.data.result as MacroAnalysisResult
      sessionStorage.setItem(SK_RESULT, JSON.stringify(r))
      sessionStorage.removeItem(SK_PENDING)
      sessionStorage.removeItem(SK_JOB_ID)
      setResult(r)
      setProgress(100)
      setJobId(null)
      histQ.refetch()
    } else if (pollQ.data.status === 'error' || pollQ.data.status === 'cancelled') {
      sessionStorage.removeItem(SK_PENDING)
      sessionStorage.removeItem(SK_JOB_ID)
      setProgress(0)
      setJobId(null)
    }
  }, [pollQ.data]) // eslint-disable-line react-hooks/exhaustive-deps

  // 폴링 중 서버 재시작 등으로 404 → 잡 소실 처리
  useEffect(() => {
    if (!pollQ.isError) return
    sessionStorage.removeItem(SK_PENDING)
    sessionStorage.removeItem(SK_JOB_ID)
    setJobId(null)
    setProgress(0)
  }, [pollQ.isError])

  const displayResult: MacroAnalysisResult | null = result
  const isRunning = startMut.isPending || (!!jobId && !result)

  // 새로고침 후 복원: SK_PENDING=1이지만 SK_JOB_ID가 없으면 POST가 응답 전에 새로고침된 것
  // → 잡을 다시 시작해 폴링이 재개되도록 한다 (빠름 모드 새로고침 버그 수정)
  const autoStartedRef = useRef(false)
  useEffect(() => {
    if (autoStartedRef.current) return
    const hasPending = sessionStorage.getItem(SK_PENDING) === '1'
    const hasJobId   = !!sessionStorage.getItem(SK_JOB_ID)
    if (hasPending && !hasJobId && event.trim()) {
      autoStartedRef.current = true
      startMut.mutate()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 진행 바 타이머
  useEffect(() => {
    if (!isRunning) return
    const startMs = parseInt(sessionStorage.getItem(SK_START) || String(Date.now()), 10)
    const maxMs   = MODE_MAX_MS[mode] ?? 65_000
    const tick = () => {
      const ms = Date.now() - startMs
      setElapsedMs(ms)
      setProgress(Math.min(95, (ms / maxMs) * 100))
    }
    tick()
    const id = setInterval(tick, 400)
    return () => clearInterval(id)
  }, [isRunning, mode])

  // 분석 시작 핸들러
  const startAnalysis = () => {
    sessionStorage.setItem(SK_EVENT,    event)
    sessionStorage.setItem(SK_MODEL,    model)
    sessionStorage.setItem(SK_MODE,     mode)
    sessionStorage.setItem(SK_START,   String(Date.now()))
    sessionStorage.setItem(SK_PENDING, '1')   // POST 응답 전 새로고침 대비 — 즉시 세팅
    sessionStorage.removeItem(SK_RESULT)
    sessionStorage.removeItem(SK_JOB_ID)
    wantCancelRef.current = false
    setResult(null)
    setJobId(null)
    setProgress(0)
    setElapsedMs(0)
    startMut.mutate()
  }

  // PDF 다운로드 — 결과 데이터로 라이트모드 HTML을 직접 빌드 후 캡처
  const downloadPDF = async () => {
    if (!displayResult) return
    setPdfBusy(true)
    try {
      const [jspdfMod, h2cMod] = await Promise.all([import('jspdf'), import('html2canvas')])
      const JsPDF       = (jspdfMod as any).jsPDF ?? (jspdfMod as any).default
      const html2canvas = (h2cMod as any).default ?? h2cMod

      const dateStr = new Date().toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
      const htmlContent = buildPdfHtml(displayResult, dateStr)

      const container = document.createElement('div')
      container.style.cssText = 'position:fixed;top:0;left:-9999px;width:900px;background:#fff;z-index:-9999;pointer-events:none'
      container.innerHTML = htmlContent
      document.body.appendChild(container)

      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))

      const canvas = await html2canvas(container, {
        scale: 2, backgroundColor: '#ffffff', useCORS: true, logging: false, windowWidth: 900,
      })
      document.body.removeChild(container)

      const pdf     = new JsPDF('p', 'mm', 'a4')
      const pageW   = pdf.internal.pageSize.getWidth()
      const pageH   = pdf.internal.pageSize.getHeight()
      const pxPerMm = canvas.width / pageW
      const slicePx = pageH * pxPerMm

      let srcY = 0, page = 0
      while (srcY < canvas.height) {
        const rowH  = Math.min(slicePx, canvas.height - srcY)
        const slice = document.createElement('canvas')
        slice.width  = canvas.width
        slice.height = rowH
        slice.getContext('2d')!.drawImage(canvas, 0, srcY, canvas.width, rowH, 0, 0, canvas.width, rowH)
        if (page > 0) pdf.addPage()
        pdf.addImage(slice.toDataURL('image/jpeg', 0.92), 'JPEG', 0, 0, pageW, rowH / pxPerMm)
        srcY += rowH
        page++
      }
      pdf.save(`macro_scenario_${new Date().toISOString().slice(0, 10)}.pdf`)
    } catch (e) {
      console.error('PDF 생성 실패:', e)
    } finally {
      setPdfBusy(false)
    }
  }

  const modes  = Object.keys(modesQ.data?.modes ?? { fast: [], standard: [], full: [] })

  return (
    <div className="p-5 space-y-4 max-w-full">
      {modalEl}

      {/* 히스토리 모달 */}
      {showHist && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowHist(false)}>
          <div className="bg-[#0a1628] border border-[#1e2d40] rounded-xl w-full max-w-lg max-h-[70vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e2d40]">
              <span className="text-sm font-bold text-[#e2e8f0]">저장된 레포트</span>
              <button onClick={() => setShowHist(false)} className="text-[#64748b] hover:text-[#e2e8f0]">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="overflow-y-auto flex-1 divide-y divide-[#1e2d40]">
              {histQ.isLoading && <p className="text-center text-[#64748b] py-8 text-sm">로딩 중...</p>}
              {histQ.data?.length === 0 && <p className="text-center text-[#64748b] py-8 text-sm">저장된 레포트 없음</p>}
              {histQ.data?.map(r => (
                <button
                  key={r.name}
                  onClick={() => loadHistMut.mutate(r.name)}
                  disabled={loadHistMut.isPending}
                  className="w-full text-left px-4 py-3 hover:bg-[#0f172a] transition-colors"
                >
                  <div className="text-xs font-medium text-[#e2e8f0] truncate">{r.event || r.name}</div>
                  <div className="text-[10px] text-[#475569] mt-0.5">
                    {r.created_at ? new Date(r.created_at).toLocaleString('ko-KR') : ''} · {r.mode}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 헤더 */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Globe className="w-4 h-4 text-[#9b59b6]" />
          <div>
            <h1 className="text-base font-bold text-[#e2e8f0]">매크로 시나리오 분석</h1>
            <p className="text-[11px] text-[#4a5568]">9-에이전트 병렬 분석 · 이벤트 충격 → 보유 포트폴리오 대응 플랜</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => requireLogin(() => setShowHist(true))}
            disabled={isRunning}
            title={isAuthed ? '저장된 분석 보기' : '로그인 후 사용 가능합니다'}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0] hover:border-[#9b59b6]/40 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {isAuthed ? <History className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
            과거 레포트
          </button>
          {displayResult && !isRunning && (
            <button
              onClick={downloadPDF}
              disabled={pdfBusy}
              className={cn(
                'flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] rounded font-bold transition-colors',
                pdfBusy
                  ? 'border border-[#1e2d40] text-[#64748b] opacity-50 cursor-not-allowed'
                  : 'border border-[#7c3aed]/50 bg-[#7c3aed]/10 text-[#c084fc] hover:bg-[#7c3aed]/20'
              )}
            >
              {pdfBusy
                ? <span className="w-3.5 h-3.5 border-2 border-[#7c3aed] border-t-transparent rounded-full animate-spin" />
                : <Download className="w-3.5 h-3.5" />
              }
              PDF
            </button>
          )}
        </div>
      </div>

      {/* 프리셋 시나리오 */}
      <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3">
        <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-2">프리셋 시나리오</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {PRESETS.map(p => (
            <button key={p.label} onClick={() => setEvent(p.event)} disabled={isRunning}
              className={cn(
                'text-left p-2 rounded border transition-all text-xs disabled:opacity-40 disabled:cursor-not-allowed',
                event === p.event
                  ? 'border-[#9b59b6]/50 bg-[#9b59b6]/10 text-[#c084fc]'
                  : 'border-[#1e2d40] text-[#64748b] hover:border-[#9b59b6]/30 hover:text-[#94a3b8] hover:bg-[#0a1628]'
              )}>
              <span className="mr-1">{p.icon}</span>
              <span className="font-medium">{p.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* 이벤트 입력 */}
      <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3 space-y-3">
        <textarea
          value={event} onChange={e => setEvent(e.target.value)} rows={3}
          readOnly={isRunning}
          placeholder="매크로 이벤트를 직접 입력하거나 위 프리셋을 선택하세요..."
          className={cn(
            'w-full bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#9b59b6] resize-none placeholder-[#374151]',
            isRunning && 'opacity-50 cursor-not-allowed',
          )}
        />
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-1.5">분석 모드</div>
            <div className="flex gap-2">
              {modes.map(m => (
                <button key={m} onClick={() => setMode(m)} disabled={isRunning}
                  className={cn(
                    'px-3 py-1.5 text-left rounded font-medium transition-colors min-w-[90px] disabled:opacity-40 disabled:cursor-not-allowed',
                    mode === m ? 'bg-[#3b82f6] text-white' : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]'
                  )}>
                  <div className="text-[11px] font-bold">{MODE_LABELS[m] ?? m}</div>
                  <div className={cn('text-[9px] mt-0.5 leading-tight', mode === m ? 'text-blue-200' : 'text-[#475569]')}>
                    {MODE_DESCRIPTIONS[m]}
                  </div>
                </button>
              ))}
            </div>
          </div>
          {/* 분석 등급 */}
          <div>
            <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-1.5">분석 등급</div>
            <div className="flex gap-2">
              {([['haiku', '기본 분석', '빠른 분석'], ['sonnet', '심층 분석', '정밀 분석']] as const).map(([m, label, desc]) => (
                <button key={m} onClick={() => setModel(m)} disabled={isRunning}
                  className={cn(
                    'px-3 py-1.5 text-left rounded font-medium transition-colors min-w-[90px] disabled:opacity-40 disabled:cursor-not-allowed',
                    model === m ? 'bg-[#9b59b6] text-white' : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]'
                  )}>
                  <div className="text-[11px] font-bold">{label}</div>
                  <div className={cn('text-[9px] mt-0.5 leading-tight', model === m ? 'text-purple-200' : 'text-[#475569]')}>
                    {desc}
                  </div>
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2 ml-auto">
            {isRunning && (
              <button
                onClick={cancelAnalysis}
                disabled={cancelMut.isPending}
                className="flex items-center gap-1.5 px-3 py-2 bg-[#ef4444]/10 hover:bg-[#ef4444]/20 border border-[#ef4444]/40 text-[#f87171] text-sm font-bold rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Square className="w-3 h-3 fill-current" />
                중단
              </button>
            )}
            <button
              onClick={() => requireLogin(startAnalysis)}
              disabled={isRunning || !event.trim()}
              className="flex items-center gap-1.5 px-4 py-2 bg-[#9b59b6] hover:bg-[#7c3aed] disabled:opacity-50 text-white text-sm font-bold rounded transition-colors"
            >
              {isAuthed ? <Play className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
              {isRunning ? '분석 중...' : '분석 실행'}
            </button>
          </div>
        </div>
        {(startMut.isError || pollQ.data?.status === 'error') && (
          <ErrorMessage message="분석 실패. 다시 시도해주세요." retry={startAnalysis} />
        )}
      </div>

      {/* 진행 바 + 금융 용어 캐러셀 (분석 중이고 아직 결과 없을 때만) */}
      {isRunning && !displayResult && (
        <div className="space-y-2">
          <ProgressBar progress={progress} elapsedMs={elapsedMs} mode={mode} />
          <FinancialTips />
        </div>
      )}

      {/* 결과 — displayResult가 세팅되는 즉시 표시 */}
      {displayResult && (
        <div className="space-y-4">

          <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3">
            <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-1.5">분석 이벤트</div>
            <p className="text-sm text-[#cbd5e1] leading-relaxed">{displayResult.event}</p>
          </div>

          {displayResult.agents && displayResult.agents.length > 0 && (
            <div>
              <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-2">
                에이전트 분석 ({displayResult.agents.length}개)
              </div>
              <div className="space-y-1.5">
                {displayResult.agents.map((agent, i) => (
                  <AgentCard
                    key={agent.id ?? i}
                    agent={agent}
                    index={i}
                    portfolioActions={displayResult.portfolio_actions}
                    verdictCards={displayResult.verdict_cards}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <style>{`
        .macro-md { color: #cbd5e1; font-size: 14px; line-height: 1.75; }
        .macro-md h1, .macro-md h2 {
          color: #f1f5f9; font-size: 15px; font-weight: 700;
          margin: 14px 0 6px; border-bottom: 1px solid #1e2d40; padding-bottom: 4px;
        }
        .macro-md h3 { color: #e2e8f0; font-size: 14px; font-weight: 600; margin: 10px 0 4px; }
        .macro-md strong, .macro-md b { color: #f1f5f9; font-weight: 700; }
        .macro-md p { margin: 6px 0; }
        .macro-md ul, .macro-md ol { padding-left: 20px; margin: 6px 0; }
        .macro-md li { margin: 3px 0; color: #cbd5e1; }
        .macro-md table {
          width: 100%; border-collapse: collapse; font-size: 13px; margin: 10px 0;
          display: block; overflow-x: auto;
        }
        .macro-md thead { background: #0a1628; }
        .macro-md th {
          color: #94a3b8; border-bottom: 1px solid #1e2d40; padding: 8px 10px;
          text-align: left; font-weight: 700; white-space: nowrap;
        }
        .macro-md td { color: #e2e8f0; padding: 7px 10px; border-bottom: 1px solid #0f172a; font-size: 13px; }
        .macro-md tr:hover td { background: #0a1628; }
        .macro-md code { background: #0f172a; color: #10b981; padding: 2px 5px; border-radius: 3px; font-size: 12px; }
        .macro-md blockquote { border-left: 3px solid #9b59b6; padding-left: 10px; color: #94a3b8; margin: 6px 0; }
        .macro-md hr { border-color: #1e2d40; margin: 12px 0; }
      `}</style>
    </div>
  )
}
