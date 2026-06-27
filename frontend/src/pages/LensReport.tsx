import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  BookOpen, Play, Send, History, FileText, Loader2,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import {
  listIndustries, generateEquityReport, generateIndustryReport,
  getReportHistory, getReportFile, getTelegramStatus,
} from '@/api'
import { cn } from '@/lib/utils'

const TABS = ['Equity Report', 'Industry Report', 'History', 'Macro Link']

// ── Equity Report Tab ────────────────────────────────────────────────────────
function EquityTab() {
  const [ticker, setTicker] = useState('')
  const [company, setCompany] = useState('')
  const [telegram, setTelegram] = useState(false)
  const [sections, setSections] = useState<Record<string, string> | null>(null)
  const [rawContent, setRawContent] = useState('')
  const [activeSection, setActiveSection] = useState<string | null>(null)

  const telegramQ = useQuery({ queryKey: ['telegram-status'], queryFn: getTelegramStatus })

  const mut = useMutation({
    mutationFn: () => generateEquityReport(ticker, company, telegram),
    onSuccess: (d) => {
      setSections(d.sections)
      setRawContent(d.raw || '')
      setActiveSection('_raw')
    },
  })

  const sectionKeys = sections ? ['_raw', ...Object.keys(sections).filter(k => k !== '_raw')] : []

  return (
    <div className="flex h-full min-h-0">
      {/* Left: Input + Sections */}
      <div className="w-56 flex-shrink-0 border-r border-[#1e2d40] flex flex-col">
        <div className="p-3 border-b border-[#1e2d40] space-y-2">
          <div className="text-[10px] text-[#4a5568] font-bold tracking-wider">EQUITY RESEARCH</div>
          <input value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} placeholder="TICKER"
            className="w-full bg-[#0b0f1a] border border-[#1e2d40] rounded px-2 py-1.5 text-xs font-mono text-[#e2e8f0] focus:outline-none focus:border-[#2e75b6]" />
          <input value={company} onChange={e => setCompany(e.target.value)} placeholder="Company name"
            className="w-full bg-[#0b0f1a] border border-[#1e2d40] rounded px-2 py-1.5 text-xs text-[#e2e8f0] focus:outline-none focus:border-[#2e75b6]" />
          {telegramQ.data?.configured && (
            <label className="flex items-center gap-2 text-[10px] text-[#64748b] cursor-pointer">
              <input type="checkbox" checked={telegram} onChange={e => setTelegram(e.target.checked)} className="accent-[#2e75b6]" />
              텔레그램 전송
            </label>
          )}
          <button onClick={() => mut.mutate()} disabled={mut.isPending || !ticker || !company}
            className="w-full flex items-center justify-center gap-1.5 py-2 bg-[#2e75b6] text-white text-xs rounded disabled:opacity-50 hover:bg-[#1d5fa0]">
            {mut.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            {mut.isPending ? 'GENERATING...' : 'GENERATE'}
          </button>
          {mut.isError && <div className="text-[10px] text-[#ef4444]">오류 발생: {(mut.error as any)?.message}</div>}
        </div>
        <div className="flex-1 overflow-y-auto py-1">
          {sectionKeys.map(k => (
            <button key={k} onClick={() => setActiveSection(k)}
              className={cn('w-full text-left px-3 py-1.5 text-[11px] border-b border-[#0f172a] truncate',
                activeSection === k ? 'bg-[#111827] text-[#2e75b6]' : 'text-[#4a5568] hover:text-[#94a3b8] hover:bg-[#0a0f1a]'
              )}>
              {k === '_raw' ? '전체 리포트' : k}
            </button>
          ))}
        </div>
      </div>

      {/* Right: Content */}
      <div className="flex-1 overflow-y-auto p-5">
        {!sections && !mut.isPending && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
            <FileText className="w-12 h-12 text-[#2e75b6]/30" />
            <p className="text-xs text-[#4a5568]">티커와 회사명을 입력하고 GENERATE를 눌러주세요.</p>
          </div>
        )}
        {mut.isPending && (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <Loader2 className="w-8 h-8 text-[#2e75b6] animate-spin" />
            <p className="text-xs text-[#64748b]">Claude AI 리포트 생성 중... (약 2-3분 소요)</p>
          </div>
        )}
        {sections && activeSection && !mut.isPending && (
          <div className="max-w-4xl">
            <div className="prose prose-sm prose-invert max-w-none markdown-body">
              <ReactMarkdown>{activeSection === '_raw' ? rawContent : (sections[activeSection] || '')}</ReactMarkdown>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Industry Report Tab ────────────────────────────────────────────────────────
function IndustryTab() {
  const [selectedIndustry, setSelectedIndustry] = useState<string | null>(null)
  const [telegram, setTelegram] = useState(false)
  const [rawContent, setRawContent] = useState('')
  const [done, setDone] = useState(false)

  const industriesQ = useQuery({ queryKey: ['industries'], queryFn: listIndustries, staleTime: 3600_000 })
  const telegramQ   = useQuery({ queryKey: ['telegram-status'], queryFn: getTelegramStatus })

  const mut = useMutation({
    mutationFn: () => generateIndustryReport(selectedIndustry!, telegram),
    onSuccess: (d) => { setRawContent(d.raw || ''); setDone(true) },
  })

  return (
    <div className="flex h-full min-h-0">
      {/* Left: Industry Grid */}
      <div className="w-64 flex-shrink-0 border-r border-[#1e2d40] flex flex-col">
        <div className="p-3 border-b border-[#1e2d40]">
          <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-2">INDUSTRY SECTORS</div>
          {telegramQ.data?.configured && (
            <label className="flex items-center gap-2 text-[10px] text-[#64748b] cursor-pointer mb-2">
              <input type="checkbox" checked={telegram} onChange={e => setTelegram(e.target.checked)} className="accent-[#2e75b6]" />
              텔레그램 전송
            </label>
          )}
          <button onClick={() => mut.mutate()} disabled={mut.isPending || !selectedIndustry}
            className="w-full flex items-center justify-center gap-1.5 py-2 bg-[#2e75b6] text-white text-xs rounded disabled:opacity-50 hover:bg-[#1d5fa0]">
            {mut.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            {mut.isPending ? 'GENERATING...' : 'GENERATE REPORT'}
          </button>
        </div>
        <div className="flex-1 overflow-y-auto py-1">
          {industriesQ.isLoading && <div className="text-xs text-[#4a5568] p-3">로드 중...</div>}
          {(industriesQ.data || []).map(ind => (
            <button key={ind.id} onClick={() => { setSelectedIndustry(ind.id); setDone(false) }}
              className={cn('w-full text-left px-3 py-2 border-b border-[#0f172a] transition-colors',
                selectedIndustry === ind.id ? 'bg-[#111827] border-l-2 border-l-[#2e75b6]' : 'hover:bg-[#0a0f1a]'
              )}>
              <div className="flex items-center gap-1.5">
                <span className="text-base">{ind.icon}</span>
                <div className="min-w-0">
                  <div className={cn('text-xs font-medium truncate', selectedIndustry === ind.id ? 'text-[#2e75b6]' : 'text-[#94a3b8]')}>{ind.name_kr}</div>
                  <div className="text-[10px] text-[#374151] truncate">{ind.name_en}</div>
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Right: Content */}
      <div className="flex-1 overflow-y-auto p-5">
        {!selectedIndustry && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
            <BookOpen className="w-12 h-12 text-[#2e75b6]/30" />
            <p className="text-xs text-[#4a5568]">좌측에서 산업을 선택하고 GENERATE REPORT를 눌러주세요.</p>
          </div>
        )}
        {mut.isPending && (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <Loader2 className="w-8 h-8 text-[#2e75b6] animate-spin" />
            <p className="text-xs text-[#64748b]">산업 리포트 생성 중... (약 3-5분 소요)</p>
          </div>
        )}
        {done && rawContent && !mut.isPending && (
          <div className="max-w-4xl">
            <div className="prose prose-sm prose-invert max-w-none markdown-body">
              <ReactMarkdown>{rawContent}</ReactMarkdown>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── History Tab ───────────────────────────────────────────────────────────────
function HistoryTab() {
  const [selected, setSelected] = useState<string | null>(null)
  const [content, setContent] = useState('')

  const historyQ = useQuery({ queryKey: ['report-history'], queryFn: getReportHistory, staleTime: 60_000 })
  const fileMut = useMutation({ mutationFn: getReportFile, onSuccess: d => setContent(d.content) })

  return (
    <div className="flex h-full min-h-0">
      <div className="w-64 flex-shrink-0 border-r border-[#1e2d40] overflow-y-auto">
        <div className="p-3 border-b border-[#1e2d40]">
          <div className="text-[10px] text-[#4a5568] font-bold tracking-wider">REPORT HISTORY</div>
        </div>
        {(historyQ.data || []).map(f => (
          <button key={f.name} onClick={() => { setSelected(f.name); fileMut.mutate(f.name) }}
            className={cn('w-full text-left px-3 py-2 border-b border-[#0f172a] transition-colors',
              selected === f.name ? 'bg-[#111827] text-[#2e75b6]' : 'text-[#4a5568] hover:text-[#94a3b8] hover:bg-[#0a0f1a]'
            )}>
            <div className="flex items-center gap-1.5 mb-0.5">
              <span className={cn('text-[9px] px-1.5 py-0.5 rounded font-bold',
                f.type === 'daily' ? 'bg-[#10b981]/20 text-[#10b981]' :
                f.type === 'equity' ? 'bg-[#2e75b6]/20 text-[#2e75b6]' : 'bg-[#9b59b6]/20 text-[#9b59b6]'
              )}>{f.type?.toUpperCase()}</span>
              <span className="text-[10px] text-[#374151]">{f.size_kb?.toFixed(1)} KB</span>
            </div>
            <div className="text-[11px] truncate">{f.name}</div>
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-5">
        {!selected && <div className="flex items-center justify-center h-full text-xs text-[#4a5568]">리포트를 선택하세요.</div>}
        {fileMut.isPending && <div className="flex items-center justify-center h-full"><Loader2 className="w-5 h-5 text-[#2e75b6] animate-spin" /></div>}
        {content && !fileMut.isPending && (
          <div className="max-w-4xl">
            <div className="prose prose-sm prose-invert max-w-none markdown-body">
              <ReactMarkdown>{content}</ReactMarkdown>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Macro Link Tab ────────────────────────────────────────────────────────────
function MacroLinkTab() {
  const LINKS = [
    { category: 'Central Banks', items: [
      { label: 'Federal Reserve', desc: 'FOMC, 금리 결정, 경제 전망', href: 'https://www.federalreserve.gov' },
      { label: 'ECB', desc: '유럽중앙은행 정책 결정', href: 'https://www.ecb.europa.eu' },
      { label: 'Bank of Japan', desc: 'YCC 정책, 엔화', href: 'https://www.boj.or.jp/en' },
    ]},
    { category: 'Economic Data', items: [
      { label: 'FRED Economic Data', desc: '미국 매크로 경제 데이터베이스', href: 'https://fred.stlouisfed.org' },
      { label: 'BLS', desc: '미국 노동통계 (CPI, 고용)', href: 'https://www.bls.gov' },
      { label: 'BEA', desc: '미국 GDP, PCE 데이터', href: 'https://www.bea.gov' },
    ]},
    { category: 'Markets & Research', items: [
      { label: 'CME FedWatch', desc: 'Fed 금리 확률 시장', href: 'https://www.cmegroup.com/trading/interest-rates/countdown-to-fomc.html' },
      { label: 'CBOE VIX', desc: '변동성 지수 현황', href: 'https://www.cboe.com/tradable_products/vix' },
      { label: 'US Treasury Yields', desc: '국채 수익률 커브', href: 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates' },
    ]},
    { category: 'Research & Analysis', items: [
      { label: 'IMF World Economic Outlook', desc: '글로벌 경제 전망', href: 'https://www.imf.org/en/Publications/WEO' },
      { label: 'BIS', desc: '국제결제은행 연구', href: 'https://www.bis.org' },
      { label: 'Research Affiliates', desc: '자산배분, 요인투자 연구', href: 'https://www.researchaffiliates.com' },
    ]},
  ]

  return (
    <div className="p-5 overflow-y-auto space-y-6">
      {LINKS.map(cat => (
        <div key={cat.category}>
          <div className="text-[10px] text-[#4a5568] font-bold tracking-[2px] mb-3">{cat.category.toUpperCase()}</div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {cat.items.map(item => (
              <a key={item.label} href={item.href} target="_blank" rel="noreferrer"
                className="block bg-[#060b14] border border-[#1e2d40] rounded p-3 hover:border-[#2e75b6]/40 hover:bg-[#0a1628] transition-all group">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="text-xs font-medium text-[#e2e8f0] group-hover:text-[#2e75b6] transition-colors">{item.label}</span>
                  <span className="text-[#1e2d40] group-hover:text-[#2e75b6] transition-colors">↗</span>
                </div>
                <p className="text-[10px] text-[#4a5568]">{item.desc}</p>
              </a>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function LensReport() {
  const [tab, setTab] = useState(0)

  return (
    <div className="flex flex-col h-full bg-[#0b0f1a]">
      {/* Header */}
      <div className="flex-shrink-0 px-5 py-3 border-b border-[#1e2d40] bg-[#060b14] flex items-center gap-2">
        <BookOpen className="w-4 h-4 text-[#2e75b6]" />
        <div>
          <span className="text-[10px] font-bold text-[#4a5568] tracking-[3px]">LENS REPORT</span>
          <p className="text-[10px] text-[#374151]">Claude 웹서치 AI 리서치 · 종목/산업 딥 리포트 · 텔레그램 자동 발송</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex-shrink-0 flex border-b border-[#1e2d40] bg-[#060b14]">
        {TABS.map((t, i) => (
          <button key={t} onClick={() => setTab(i)}
            className={cn('px-4 py-2.5 text-[10px] font-bold tracking-wider transition-colors',
              tab === i ? 'text-[#2e75b6] border-b-2 border-[#2e75b6]' : 'text-[#4a5568] hover:text-[#64748b]'
            )}>
            {t}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {tab === 0 && <EquityTab />}
        {tab === 1 && <IndustryTab />}
        {tab === 2 && <HistoryTab />}
        {tab === 3 && <MacroLinkTab />}
      </div>

      <style>{`
        .markdown-body h1, .markdown-body h2, .markdown-body h3 { color: #e2e8f0; margin-top: 1.5rem; }
        .markdown-body h1 { font-size: 1.2rem; border-bottom: 1px solid #1e2d40; padding-bottom: 0.5rem; }
        .markdown-body h2 { font-size: 1rem; color: #2e75b6; }
        .markdown-body h3 { font-size: 0.9rem; color: #64748b; }
        .markdown-body p { color: #94a3b8; line-height: 1.7; font-size: 0.8rem; }
        .markdown-body strong { color: #e2e8f0; }
        .markdown-body ul, .markdown-body ol { color: #94a3b8; font-size: 0.8rem; }
        .markdown-body hr { border-color: #1e2d40; }
        .markdown-body code { background: #111827; color: #2e75b6; padding: 2px 4px; border-radius: 3px; font-size: 0.75rem; }
        .markdown-body blockquote { border-left: 3px solid #2e75b6; padding-left: 1rem; color: #64748b; }
        .markdown-body table { font-size: 0.75rem; width: 100%; }
        .markdown-body th { color: #4a5568; border-bottom: 1px solid #1e2d40; padding: 0.5rem; text-align: left; }
        .markdown-body td { color: #94a3b8; padding: 0.5rem; border-bottom: 1px solid #0f172a; }
      `}</style>
    </div>
  )
}
