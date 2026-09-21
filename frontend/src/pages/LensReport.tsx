import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  BookOpen, Play, ChevronDown, ChevronRight, Download, History, X, Square, Search, Lock,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { MARKDOWN_PLUGINS } from '@/lib/markdown'
import { FinancialTips } from '@/components/FinancialTips'
import {
  startEquityReport, startIndustryReport,
  getReportJob, cancelReportJob,
  getReportHistory, getReportFile,
  listIndustries, searchTickers,
} from '@/api'
import { cn } from '@/lib/utils'
import { useLoginPrompt } from '@/components/auth/LockedPreview'
import AuthGate from '@/components/auth/AuthGate'
import { useAuth } from '@/lib/AuthContext'
import { useFeatures } from '@/lib/useFeatures'
import { useMarket } from '@/lib/useMarket'
import { marketSession } from '@/lib/marketStorage'
import { useTickerNames, displayTicker } from '@/lib/useTickerNames'
import SuggestionList from '@/components/SuggestionList'
import { pickOnEnter, moveHighlight, selectionLabel } from '@/lib/suggestions'

// ── sessionStorage 키 ──────────────────────────────────────────────────────────
const EQ_JOB_ID  = 'lens_eq_job_id'
const EQ_PENDING = 'lens_eq_pending'
const EQ_TICKER  = 'lens_eq_ticker'
const EQ_START   = 'lens_eq_start'
const EQ_RESULT  = 'lens_eq_result'
const EQ_TIER    = 'lens_eq_tier'

const IND_JOB_ID   = 'lens_ind_job_id'
const IND_PENDING  = 'lens_ind_pending'
const IND_INDUSTRY = 'lens_ind_industry'
const IND_START    = 'lens_ind_start'
const IND_RESULT   = 'lens_ind_result'
const IND_TIER     = 'lens_ind_tier'

// ── 탭 목록 ────────────────────────────────────────────────────────────────────
const TABS = ['주식 리포트', '산업 리포트', '과거 레포트', '매크로 링크']

// ── 예상 소요 시간 (ms) ────────────────────────────────────────────────────────
const MODE_MAX_MS = { equity: 90_000, industry: 90_000 }

// ── 진행 단계 메시지 ────────────────────────────────────────────────────────────
function stageLabel(elapsedMs: number, type: 'equity' | 'industry'): string {
  if (type === 'equity') {
    return elapsedMs < 10_000 ? '데이터 수집 중...'
         : elapsedMs < 20_000 ? '시장 데이터 구조화 중...'
         : elapsedMs < 30_000 ? '최신 뉴스 수집 중...'
         : 'AI 분석 및 레포트 작성 중...'
  }
  return elapsedMs < 10_000 ? '데이터 수집 중...'
       : elapsedMs < 25_000 ? '산업 동향 수집 중...'
       : 'AI 분석 및 레포트 작성 중...'
}

// ── 타입 정의 ──────────────────────────────────────────────────────────────────
type EquityResult = {
  ticker: string
  company_name: string
  raw: string
  sections: Record<string, string>
  market_data?: Record<string, unknown>
  report_type?: string
  file_path?: string
  /** 공용 캐시 재사용 여부 — 다른 사용자가 이미 만든 리포트를 받아온 경우 true */
  from_cache?: boolean
  model_tier?: string
  cached_at?: string
  cache_age_hours?: number
  cache_ttl_hours?: number
}

type IndustryResult = {
  industry_id: string
  industry_name_kr: string
  industry_name_en: string
  raw: string
  sections: Record<string, string>
  market_data?: Record<string, unknown>
  report_type?: string
  file_path?: string
  /** 공용 캐시 재사용 여부 — 다른 사용자가 이미 만든 리포트를 받아온 경우 true */
  from_cache?: boolean
  model_tier?: string
  cached_at?: string
  cache_age_hours?: number
  cache_ttl_hours?: number
}

// ── 섹션 파서 (히스토리 레포트 클라이언트 파싱용) ──────────────────────────────
function parseSections(raw: string): Record<string, string> {
  const sections: Record<string, string> = {}
  let cur = 'header'
  const buf: string[] = []
  for (const line of raw.split('\n')) {
    if (line.startsWith('## ')) {
      if (buf.length) sections[cur] = buf.join('\n').trim()
      cur = line.slice(3).trim().toLowerCase().replace(/\s+/g, '_')
      buf.length = 0
    } else {
      buf.push(line)
    }
  }
  if (buf.length) sections[cur] = buf.join('\n').trim()
  return sections
}

// ── 섹션 표시 타이틀 ──────────────────────────────────────────────────────────
function sectionTitle(key: string): string {
  return key.replace(/_/g, ' ')
}

// ── Markdown → HTML (PDF용) ──────────────────────────────────────────────────
function mdToHtml(raw: string): string {
  if (!raw) return ''

  const esc = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const inline = (s: string) =>
    s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\*(.+?)\*/g, '<em>$1</em>')
  const processCell = (s: string) => inline(esc(s.trim()))

  // 행에서 파이프로 셀 분리 (앞뒤 | 제거 후 split)
  const splitRow = (line: string): string[] => {
    const t = line.trim()
    const body = t.startsWith('|') ? t.slice(1) : t
    const final = body.endsWith('|') ? body.slice(0, -1) : body
    return final.split('|')
  }

  const isSepRow = (line: string) => /^\s*\|?[\s\-:|]+\|/.test(line) &&
    /^[\s|:\-]+$/.test(line.replace(/[^|:\-\s]/g, ''))

  const lines = raw.split('\n')
  const out: string[] = []
  let i = 0

  while (i < lines.length) {
    const cur = lines[i]
    const curT = cur.trim()

    // 테이블 감지: 현재 행에 | 포함 + 다음 행이 구분선
    if (curT.includes('|') && i + 1 < lines.length && isSepRow(lines[i + 1])) {
      const tblRows: string[] = [cur]
      i += 2  // 헤더 + 구분선 건너뜀
      while (i < lines.length && lines[i].trim().includes('|')) {
        tblRows.push(lines[i])
        i++
      }
      const heads = splitRow(tblRows[0]).map(h => `<th>${processCell(h)}</th>`).join('')
      const rows  = tblRows.slice(1).map(row =>
        `<tr>${splitRow(row).map(c => `<td>${processCell(c)}</td>`).join('')}</tr>`
      ).join('')
      out.push(`<div class="tbl-wrap"><table><thead><tr>${heads}</tr></thead><tbody>${rows}</tbody></table></div>`)
      continue
    }

    // 일반 줄 처리
    let s = inline(esc(cur))
    if (/^## /.test(s))       s = s.replace(/^## (.+)$/, '<h2>$1</h2>')
    else if (/^### /.test(s)) s = s.replace(/^### (.+)$/, '<h3>$1</h3>')
    else if (/^# /.test(s))   s = s.replace(/^# (.+)$/, '<h1>$1</h1>')
    else if (/^---+$/.test(s.trim())) s = '<hr/>'
    else if (/^[-*•·]\s/.test(s))     s = s.replace(/^[-*•·]\s+(.+)$/, '<li>$1</li>')
    else if (/^\d+\.\s/.test(s))      s = s.replace(/^\d+\.\s+(.+)$/, '<li>$1</li>')

    out.push(s)
    i++
  }

  let html = out.join('\n')
  html = html.replace(/((?:<li>[^\n]*\n?)+)/g, '<ul>$1</ul>')
  html = html.replace(/\n{2,}/g, '</p><p>')
  return `<p>${html}</p>`
}

// ── PDF HTML 빌더 ─────────────────────────────────────────────────────────────
/** `title` 은 표지 제목 — 한국은 종목명만, 미국은 '티커 — 회사명' (호출자가 정한다). */
function buildEquityPdfHtml(result: EquityResult, dateStr: string, title: string): string {
  const entries = Object.entries(result.sections || {})
  const bodyHtml = entries.map(([key, content], i) => `
    <div class="sec-card">
      <div class="sec-hdr"><span class="sec-badge">${i + 1}</span><span>${sectionTitle(key)}</span></div>
      <div class="sec-body">${mdToHtml(content)}</div>
    </div>
  `).join('')
  return `<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,'Segoe UI',sans-serif;font-size:13px;color:#1f2937;background:#fff;line-height:1.7}
    .hdr{background:linear-gradient(135deg,#1e3a5f,#2e75b6);padding:24px 40px 20px;color:#fff}
    .hdr .brand{font-size:9px;letter-spacing:4px;color:#bfdbfe;font-weight:700;margin-bottom:6px}
    .hdr .title{font-size:20px;font-weight:900}
    .hdr .sub{font-size:11px;color:#93c5fd;margin-top:6px}
    .body{padding:24px 40px}
    .sec-card{border:1px solid #e5e7eb;border-radius:8px;margin-bottom:14px;overflow:hidden;page-break-inside:avoid}
    .sec-hdr{display:flex;align-items:center;gap:10px;padding:9px 16px;background:#f3f4f6;border-bottom:1px solid #e5e7eb;font-weight:700;font-size:13px;color:#1f2937}
    .sec-badge{width:22px;height:22px;border-radius:50%;background:#2e75b6;color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0}
    .sec-body{padding:14px 18px;color:#374151}
    h1{font-size:15px;font-weight:700;color:#1e3a5f;margin:12px 0 5px}
    h2{font-size:14px;font-weight:700;color:#1d4ed8;margin:10px 0 4px}
    h3{font-size:13px;font-weight:700;color:#374151;margin:8px 0 3px}
    p{margin:5px 0}
    ul{padding-left:18px;margin:5px 0}
    li{margin:2px 0;color:#4b5563}
    .tbl-wrap{width:100%;overflow-x:auto;margin:10px 0}
    table{width:100%;border-collapse:collapse;font-size:11px;table-layout:auto}
    th{background:#eef2f7;color:#1e3a5f;border:1px solid #c7d4e0;padding:6px 9px;font-weight:700;text-align:left;white-space:nowrap}
    td{color:#374151;border:1px solid #dde3ec;padding:5px 9px;word-break:break-word;vertical-align:top}
    tr:nth-child(even) td{background:#f8fafc}
    strong{color:#111827;font-weight:700}
    em{color:#374151;font-style:italic}
    .footer{margin-top:20px;padding-top:10px;border-top:1px solid #e5e7eb;font-size:10px;color:#9ca3af;text-align:center}
  </style></head><body>
  <div class="hdr">
    <div class="brand">LENS CAPITAL RESEARCH</div>
    <div class="title">${title.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
    <div class="sub">종목 리서치 레포트 · ${dateStr}</div>
  </div>
  <div class="body">${bodyHtml}<div class="footer">본 레포트는 AI 자동 생성 참고용으로, 투자 조언이 아닙니다.</div></div>
  </body></html>`
}

function buildIndustryPdfHtml(result: IndustryResult, dateStr: string): string {
  const entries = Object.entries(result.sections || {})
  const bodyHtml = entries.map(([key, content], i) => `
    <div class="sec-card">
      <div class="sec-hdr"><span class="sec-badge">${i + 1}</span><span>${sectionTitle(key)}</span></div>
      <div class="sec-body">${mdToHtml(content)}</div>
    </div>
  `).join('')
  return `<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,'Segoe UI',sans-serif;font-size:13px;color:#1f2937;background:#fff;line-height:1.7}
    .hdr{background:linear-gradient(135deg,#1e3a5f,#2e75b6);padding:24px 40px 20px;color:#fff}
    .hdr .brand{font-size:9px;letter-spacing:4px;color:#bfdbfe;font-weight:700;margin-bottom:6px}
    .hdr .title{font-size:20px;font-weight:900}
    .hdr .sub{font-size:11px;color:#93c5fd;margin-top:6px}
    .body{padding:24px 40px}
    .sec-card{border:1px solid #e5e7eb;border-radius:8px;margin-bottom:14px;overflow:hidden;page-break-inside:avoid}
    .sec-hdr{display:flex;align-items:center;gap:10px;padding:9px 16px;background:#f3f4f6;border-bottom:1px solid #e5e7eb;font-weight:700;font-size:13px;color:#1f2937}
    .sec-badge{width:22px;height:22px;border-radius:50%;background:#9b59b6;color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0}
    .sec-body{padding:14px 18px;color:#374151}
    h1{font-size:15px;font-weight:700;margin:12px 0 5px}
    h2{font-size:14px;font-weight:700;margin:10px 0 4px}
    h3{font-size:13px;font-weight:700;margin:8px 0 3px}
    p{margin:5px 0}
    ul{padding-left:18px;margin:5px 0}
    li{margin:2px 0}
    .tbl-wrap{width:100%;overflow-x:auto;margin:10px 0}
    table{width:100%;border-collapse:collapse;font-size:11px;table-layout:auto}
    th{background:#f0f4f8;border:1px solid #c7d4e0;padding:6px 9px;font-weight:700;text-align:left;white-space:nowrap}
    td{border:1px solid #dde3ec;padding:5px 9px;word-break:break-word;vertical-align:top}
    tr:nth-child(even) td{background:#f8fafc}
    strong{font-weight:700}
    em{font-style:italic}
    .footer{margin-top:20px;padding-top:10px;border-top:1px solid #e5e7eb;font-size:10px;color:#9ca3af;text-align:center}
  </style></head><body>
  <div class="hdr">
    <div class="brand">LENS CAPITAL RESEARCH</div>
    <div class="title">${result.industry_name_kr}</div>
    <div class="sub">산업 리서치 레포트 · ${dateStr}</div>
  </div>
  <div class="body">${bodyHtml}<div class="footer">본 레포트는 AI 자동 생성 참고용으로, 투자 조언이 아닙니다.</div></div>
  </body></html>`
}

// 과거 레포트 탭 전용 — 저장된 레포트는 EquityResult/IndustryResult 구조 없이
// {sections, raw, name} 뿐이라 위 두 빌더를 못 쓴다. 같은 톤으로 제목/부제만 주입.
function buildGenericPdfHtml(title: string, subtitle: string, sections: Record<string, string>, accent: string): string {
  const entries = Object.entries(sections).filter(([k]) => k !== 'header')
  const bodyHtml = entries.map(([key, content], i) => `
    <div class="sec-card">
      <div class="sec-hdr"><span class="sec-badge">${i + 1}</span><span>${sectionTitle(key)}</span></div>
      <div class="sec-body">${mdToHtml(content)}</div>
    </div>
  `).join('')
  return `<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,'Segoe UI',sans-serif;font-size:13px;color:#1f2937;background:#fff;line-height:1.7}
    .hdr{background:linear-gradient(135deg,#1e3a5f,${accent});padding:24px 40px 20px;color:#fff}
    .hdr .brand{font-size:9px;letter-spacing:4px;color:#bfdbfe;font-weight:700;margin-bottom:6px}
    .hdr .title{font-size:20px;font-weight:900}
    .hdr .sub{font-size:11px;color:#93c5fd;margin-top:6px}
    .body{padding:24px 40px}
    .sec-card{border:1px solid #e5e7eb;border-radius:8px;margin-bottom:14px;overflow:hidden;page-break-inside:avoid}
    .sec-hdr{display:flex;align-items:center;gap:10px;padding:9px 16px;background:#f3f4f6;border-bottom:1px solid #e5e7eb;font-weight:700;font-size:13px;color:#1f2937}
    .sec-badge{width:22px;height:22px;border-radius:50%;background:${accent};color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0}
    .sec-body{padding:14px 18px;color:#374151}
    h1{font-size:15px;font-weight:700;color:#1e3a5f;margin:12px 0 5px}
    h2{font-size:14px;font-weight:700;color:#1d4ed8;margin:10px 0 4px}
    h3{font-size:13px;font-weight:700;color:#374151;margin:8px 0 3px}
    p{margin:5px 0}
    ul{padding-left:18px;margin:5px 0}
    li{margin:2px 0;color:#4b5563}
    .tbl-wrap{width:100%;overflow-x:auto;margin:10px 0}
    table{width:100%;border-collapse:collapse;font-size:11px;table-layout:auto}
    th{background:#eef2f7;color:#1e3a5f;border:1px solid #c7d4e0;padding:6px 9px;font-weight:700;text-align:left;white-space:nowrap}
    td{color:#374151;border:1px solid #dde3ec;padding:5px 9px;word-break:break-word;vertical-align:top}
    tr:nth-child(even) td{background:#f8fafc}
    strong{color:#111827;font-weight:700}
    em{color:#374151;font-style:italic}
    .footer{margin-top:20px;padding-top:10px;border-top:1px solid #e5e7eb;font-size:10px;color:#9ca3af;text-align:center}
  </style></head><body>
  <div class="hdr">
    <div class="brand">LENS CAPITAL RESEARCH</div>
    <div class="title">${title.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
    <div class="sub">${subtitle}</div>
  </div>
  <div class="body">${bodyHtml}<div class="footer">본 레포트는 AI 자동 생성 참고용으로, 투자 조언이 아닙니다.</div></div>
  </body></html>`
}

/** 과거 레포트 파일명에서 표시용 제목을 뽑는다.
    (예: lens_AAPL_20260901_1030.md → AAPL, lens_industry_반도체_20260901_1030.md → 반도체) */
function reportDisplayTitle(name: string, isIndustry: boolean): string {
  const base = name
    .replace(/^lens_industry_/, '')
    .replace(/^lens_/, '')
    .replace(/_\d{8}_\d{4}\.md$/, '')
  return isIndustry ? base.replace(/_/g, ' ') : base.toUpperCase()
}

/** 과거 목록 한 줄. 서버가 종목 리포트면 `ticker`(저장 때 metadata)를 함께 준다. */
type HistoryRow = { name: string; type?: string; ticker?: string | null }

/**
 * 과거 종목 리포트의 티커. 서버가 준 값이 먼저다.
 *
 * 없으면 파일명에서 되짚는다 — 저장할 때 티커의 점을 뺐으므로(`lens_005930KS_…`,
 * routers/reports.py 의 `re.sub(r"[^\w]", "", ticker)`) 이름 사전의 키에서 같은
 * 규칙으로 점을 뺀 것과 맞춘다. 산업 리포트면 null.
 */
function historyTicker(row: HistoryRow, names: Record<string, string>): string | null {
  if (row.ticker) return row.ticker
  if (!row.name.startsWith('lens_') || row.name.startsWith('lens_industry_')) return null
  const slug = row.name.replace(/^lens_/, '').replace(/_\d{8}_\d{4}\.md$/, '').toUpperCase()
  if (!slug) return null
  return Object.keys(names).find(t => t.replace(/[^\w]/g, '').toUpperCase() === slug) ?? null
}

/**
 * 과거 목록에 보일 제목. **한국 종목 리포트는 종목명** — 파일명에는 코드
 * (`lens_005930KS_20260917_1234.md`)가 들어 있다. 날짜·시각은 같은 줄에 따로 보인다.
 * 미국·산업은 예전처럼 파일명 그대로.
 */
function historyTitle(row: HistoryRow, market: string, names: Record<string, string>): string {
  if (market !== 'KR') return row.name
  const t = historyTicker(row, names)
  return t ? displayTicker(t, names) : row.name
}

/** 내려받는 PDF 파일 이름에 넣어도 되는 글자만 (종목명의 공백·& 등). */
const pdfSafe = (s: string) => s.replace(/[\\/:*?"<>|&\s]+/g, '_')

// ── 진행 바 컴포넌트 ──────────────────────────────────────────────────────────
function ProgressBar({ progress, elapsedMs, type }: {
  progress: number
  elapsedMs: number
  type: 'equity' | 'industry'
}) {
  const elapsed = elapsedMs < 1000
    ? `${elapsedMs}ms`
    : `${(elapsedMs / 1000).toFixed(0)}초`

  return (
    <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg px-4 py-3 space-y-2">
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-[#10b981] font-mono flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-[#10b981] animate-pulse inline-block" />
          {stageLabel(elapsedMs, type)}
        </span>
        <span className="text-[#475569] font-mono">{elapsed} 경과</span>
      </div>
      <div className="w-full bg-[#0f172a] rounded-full h-2 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${progress}%`,
            background: 'linear-gradient(90deg, #10b981, #059669)',
          }}
        />
      </div>
      <div className="text-right text-[10px] text-[#475569] font-mono">{Math.round(progress)}%</div>
    </div>
  )
}

// ── 레포트 섹션 카드 ──────────────────────────────────────────────────────────
function ReportSection({ title, content, defaultExpanded = false, sectionIndex }: {
  title: string
  content: string
  defaultExpanded?: boolean
  sectionIndex: number
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  return (
    <div className="border border-[#1e2d40] rounded overflow-hidden">
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-[#0a1628] transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <div className="w-5 h-5 rounded-full bg-[#10b981]/20 border border-[#10b981]/30 flex items-center justify-center text-[10px] font-mono text-[#10b981]">
            {sectionIndex + 1}
          </div>
          <span className="text-sm font-semibold text-[#f1f5f9]">{sectionTitle(title)}</span>
        </div>
        {expanded
          ? <ChevronDown className="w-4 h-4 text-[#64748b]" />
          : <ChevronRight className="w-4 h-4 text-[#64748b]" />
        }
      </button>
      {expanded && (
        <div className="px-4 pb-5 border-t border-[#1e2d40] pt-3">
          <div className="lens-md">
            <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS}>{content}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  )
}

// ── 핵심 지표 헤더 카드 ───────────────────────────────────────────────────────
function ReportHeaderCard({ headerContent, type }: {
  headerContent: string
  type: 'equity' | 'industry'
}) {
  type Metric = { label: string; value: string; color: string }
  const metrics: Metric[] = []

  for (const line of headerContent.split('\n')) {
    const colonIdx = line.indexOf(':')
    if (colonIdx < 0) continue
    const label = line.slice(0, colonIdx).trim()
    const value = line.slice(colonIdx + 1).trim()
    if (!label || !value) continue
    // Filter out verbose keys
    if (label.startsWith('KEY_HIGHLIGHT') || label === '슬로건' || label === '벤치마크' || label === '커버리지') continue

    let color = '#94a3b8'
    const lbl = label.toLowerCase()
    if (lbl === '투자의견' || lbl === '의견') {
      if (/buy|overweight/i.test(value)) color = '#10b981'
      else if (/sell|underweight/i.test(value)) color = '#ef4444'
      else color = '#f59e0b'
    } else if (lbl.includes('bull') || lbl.includes('bull_목표')) {
      color = '#10b981'
    } else if (lbl.includes('bear') || lbl.includes('bear_목표')) {
      color = '#ef4444'
    } else if (lbl.includes('주가') || lbl.includes('목표') || lbl.includes('수익률')) {
      color = '#60a5fa'
    }

    metrics.push({ label, value, color })
  }

  if (!metrics.length) return null

  return (
    <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-4">
      <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-3">
        {type === 'equity' ? '종목 핵심 지표' : '산업 핵심 지표'}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
        {metrics.slice(0, 8).map((m, i) => (
          <div key={i} className="bg-[#0a1628] rounded-lg p-2.5 border border-[#1e2d40]">
            <div className="text-[9px] text-[#64748b] mb-1 font-medium">{m.label}</div>
            <div className="text-xs font-bold truncate" style={{ color: m.color }}>{m.value}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── PDF 다운로드 헬퍼 ──────────────────────────────────────────────────────────
async function downloadPdfFromHtml(htmlContent: string, filename: string) {
  const [jspdfMod, h2cMod] = await Promise.all([import('jspdf'), import('html2canvas')])
  const JsPDF       = (jspdfMod as any).jsPDF ?? (jspdfMod as any).default
  const html2canvas = (h2cMod as any).default ?? h2cMod

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
  pdf.save(filename)
}

// ── 주식 리포트 탭 ─────────────────────────────────────────────────────────────
function EquityTab() {
  const market = useMarket()
  const features = useFeatures()
  // 이전에 고른 'deep' 이 sessionStorage 에 남아 있을 수 있다.
  useEffect(() => {
    if (!features.deep_analysis_enabled) setModelTier(t => (t === 'deep' ? 'basic' : t))
  }, [features.deep_analysis_enabled])
  // 리포트 생성·과거 이력은 로그인이 필요하다. 예시 리포트는 만들지 않는다 —
  // 로그인 전에는 결과도 이력도 비어 있고, 버튼을 누르면 로그인을 요구한다.
  const { isAuthed, requireLogin, modalEl } = useLoginPrompt()
  const names = useTickerNames()
  // ticker 는 리포트를 요청할 티커, tickerText 는 입력칸에 **친** 글자다.
  // tickerText 가 null 이면 아직 안 쳤다는 뜻이고 칸에는 ticker 의 이름(한국)을
  // 그린다 — 목록에서 한국 종목을 고르면 칸에 '005930.KS' 가 남던 자리다.
  // 이름 사전은 비동기로 오므로 값으로 복사하지 않고 그릴 때 읽는다.
  const [ticker,    setTicker]    = useState(() => marketSession.get(EQ_TICKER) || '')
  const [tickerText, setTickerText] = useState<string | null>(null)
  const shownTicker = tickerText ?? selectionLabel(market, ticker, names[ticker])
  const [sugIdx,    setSugIdx]    = useState(-1)
  const tickerInputRef = useRef<HTMLInputElement>(null)
  const [modelTier, setModelTier] = useState(() => marketSession.get(EQ_TIER)   || 'basic')
  const [jobId,  setJobId]    = useState<string | null>(() => marketSession.get(EQ_JOB_ID))
  const [result, setResult]   = useState<EquityResult | null>(() => {
    try { const s = marketSession.get(EQ_RESULT); return s ? JSON.parse(s) : null }
    catch { return null }
  })

  const [progress,  setProgress]  = useState(0)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [pdfBusy,   setPdfBusy]   = useState(false)
  const [showDropdown, setShowDropdown] = useState(false)
  const [showHist,  setShowHist]  = useState(false)

  const wantCancelRef   = useRef(false)
  const autoStartedRef  = useRef(false)

  // 티커 자동완성 — 칸에 보이는 글자로 찾는다.
  const tickerSearchQ = useQuery({
    queryKey: ['ticker-search', shownTicker],
    queryFn:  () => searchTickers(shownTicker),
    enabled:  shownTicker.length >= 2 && showDropdown,
    staleTime: 30_000,
  })
  const sugList = tickerSearchQ.data ?? []
  const sugOpen = showDropdown && sugList.length > 0

  const selectTicker = (item: { ticker: string; name: string }) => {
    setTicker(item.ticker)
    setTickerText(null)
    marketSession.set(EQ_TICKER, item.ticker)
    setShowDropdown(false)
    setSugIdx(-1)
  }

  // 히스토리
  const histQ = useQuery({
    queryKey: ['report-history'],
    queryFn:  getReportHistory,
    staleTime: 60_000,
    enabled:  showHist && isAuthed,
  })
  const loadHistMut = useMutation({
    // 목록의 행을 받는다 — 파일명만 넘기면 티커를 파일명에서 추측해야 한다.
    mutationFn: (row: HistoryRow) => getReportFile(row.name),
    onSuccess: (data, row) => {
      const sections = parseSections(data.content)
      const t = historyTicker(row, names)
      const r: EquityResult = {
        ticker: t ?? data.name.replace(/lens_|_\d{8}_\d{4}\.md/g, '').toUpperCase(),
        // 한국은 종목명 — 예전에는 파일명을 넣어 결과 머리가 'lens_005930KS_…md' 였다.
        // 미국은 예전 그대로(파일명).
        company_name: market === 'KR' ? (t ? displayTicker(t, names) : '') : data.name,
        raw: data.content,
        sections,
        report_type: 'equity',
      }
      marketSession.set(EQ_RESULT, JSON.stringify(r))
      setResult(r)
      setShowHist(false)
    },
  })

  // 잡 시작 뮤테이션 — 티커를 **인자로** 받는다. 클로저의 ticker 를 읽으면,
  // Enter 로 목록에서 고른 직후 같은 핸들러에서 시작할 때 고르기 전 글자가 간다.
  const startMut = useMutation({
    mutationFn: (t: string) => startEquityReport({ ticker: t, model_tier: modelTier }),
    onSuccess: ({ job_id }) => {
      if (wantCancelRef.current) {
        wantCancelRef.current = false
        cancelReportJob(job_id).catch(() => {})
        marketSession.remove(EQ_PENDING)
        marketSession.remove(EQ_JOB_ID)
        return
      }
      marketSession.set(EQ_JOB_ID,  job_id)
      marketSession.set(EQ_PENDING, '1')
      setJobId(job_id)
    },
    onError: () => {
      wantCancelRef.current = false
      marketSession.remove(EQ_PENDING)
      setProgress(0)
    },
  })

  // 폴링 쿼리
  const pollQ = useQuery({
    queryKey: ['report-job-eq', jobId],
    queryFn:  () => getReportJob(jobId!),
    enabled:  !!jobId,
    refetchInterval: 3_000,
    refetchIntervalInBackground: true,
    staleTime: 0,
    retry: false,
  })

  // 취소 뮤테이션
  const cancelMut = useMutation({
    mutationFn: (id: string) => cancelReportJob(id),
    onSettled: () => {
      marketSession.remove(EQ_PENDING)
      marketSession.remove(EQ_JOB_ID)
      setJobId(null)
      setProgress(0)
    },
  })

  // 폴링 결과 처리
  useEffect(() => {
    if (!pollQ.data) return
    if (pollQ.data.status === 'done' && pollQ.data.result) {
      const r = pollQ.data.result as unknown as EquityResult
      marketSession.set(EQ_RESULT, JSON.stringify(r))
      marketSession.remove(EQ_PENDING)
      marketSession.remove(EQ_JOB_ID)
      setResult(r)
      setProgress(100)
      setJobId(null)
      histQ.refetch()
    } else if (pollQ.data.status === 'error' || pollQ.data.status === 'cancelled') {
      marketSession.remove(EQ_PENDING)
      marketSession.remove(EQ_JOB_ID)
      setProgress(0)
      setJobId(null)
    }
  }, [pollQ.data]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!pollQ.isError) return
    marketSession.remove(EQ_PENDING)
    marketSession.remove(EQ_JOB_ID)
    setJobId(null)
    setProgress(0)
  }, [pollQ.isError])

  const isRunning = startMut.isPending || (!!jobId && !result)

  // 새로고침 후 재개
  useEffect(() => {
    if (autoStartedRef.current) return
    const hasPending = marketSession.get(EQ_PENDING) === '1'
    const hasJobId   = !!marketSession.get(EQ_JOB_ID)
    if (hasPending && !hasJobId && ticker.trim()) {
      autoStartedRef.current = true
      startMut.mutate(ticker)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 진행 바 타이머
  useEffect(() => {
    if (!isRunning) return
    const startMs = parseInt(marketSession.get(EQ_START) || String(Date.now()), 10)
    const maxMs   = MODE_MAX_MS.equity
    const tick = () => {
      const ms = Date.now() - startMs
      setElapsedMs(ms)
      setProgress(Math.min(95, (ms / maxMs) * 100))
    }
    tick()
    const id = setInterval(tick, 400)
    return () => clearInterval(id)
  }, [isRunning])

  // 중단 핸들러
  const cancelAnalysis = () => {
    if (jobId) {
      cancelMut.mutate(jobId)
    } else if (startMut.isPending) {
      wantCancelRef.current = true
      marketSession.remove(EQ_PENDING)
      marketSession.remove(EQ_JOB_ID)
      setProgress(0)
    }
  }

  // 시작 핸들러
  const startAnalysis = (t: string = ticker) => {
    marketSession.set(EQ_TICKER,  t)
    marketSession.set(EQ_TIER,    modelTier)
    marketSession.set(EQ_START,   String(Date.now()))
    marketSession.set(EQ_PENDING, '1')
    marketSession.remove(EQ_RESULT)
    marketSession.remove(EQ_JOB_ID)
    wantCancelRef.current = false
    setResult(null)
    setJobId(null)
    setProgress(0)
    setElapsedMs(0)
    startMut.mutate(t)
  }

  // PDF 다운로드
  const downloadPDF = async () => {
    if (!result) return
    setPdfBusy(true)
    try {
      const dateStr = new Date().toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
      // 한국은 표지·파일 이름 모두 종목명 (화면의 '종목:' 머리와 같은 규칙).
      const krName = result.company_name && result.company_name !== result.ticker
        ? result.company_name : displayTicker(result.ticker, names)
      const title = market === 'KR' ? krName : `${result.ticker} — ${result.company_name}`
      await downloadPdfFromHtml(
        buildEquityPdfHtml(result, dateStr, title),
        `lens_${market === 'KR' ? pdfSafe(krName) : result.ticker}_${new Date().toISOString().slice(0, 10)}.pdf`,
      )
    } catch (e) {
      console.error('PDF 생성 실패:', e)
    } finally {
      setPdfBusy(false)
    }
  }

  const sections = result?.sections ? Object.entries(result.sections) : []
  const headerContent = result?.sections?.['header'] || ''
  const equityHistRows = (histQ.data || []).filter(r => String(r.type).includes('equity'))

  return (
    <div className="space-y-4">
      {modalEl}
      {/* 히스토리 모달 */}
      {showHist && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowHist(false)}>
          <div className="bg-[#0a1628] border border-[#1e2d40] rounded-xl w-full max-w-lg max-h-[70vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e2d40]">
              <span className="text-sm font-bold text-[#e2e8f0]">과거 주식 레포트</span>
              <button onClick={() => setShowHist(false)} className="text-[#64748b] hover:text-[#e2e8f0]">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="overflow-y-auto flex-1 divide-y divide-[#1e2d40]">
              {histQ.isLoading && <p className="text-center text-[#64748b] py-8 text-sm">로딩 중...</p>}
              {/* 조회 실패와 "저장된 게 없음" 을 구분한다. isError 를 안 보면
                  서버가 죽어도 "레포트 없음" 이 뜨고, 사용자는 자기가 만든 것이
                  사라진 줄 안다 — 기다려야 하는 상황을 빈 목록으로 읽는다. */}
              {histQ.isError && (
                <p className="text-center text-[#ef4444] py-8 text-sm">
                  {(histQ.error as any)?.response?.data?.detail ?? '레포트 목록을 불러오지 못했습니다.'}
                </p>
              )}
              {!histQ.isLoading && !histQ.isError && equityHistRows.length === 0 && (
                <p className="text-center text-[#64748b] py-8 text-sm">저장된 주식 레포트 없음</p>
              )}
              {equityHistRows.map(r => (
                <button
                  key={r.name}
                  onClick={() => loadHistMut.mutate(r as HistoryRow)}
                  disabled={loadHistMut.isPending}
                  className="w-full text-left px-4 py-3 hover:bg-[#0f172a] transition-colors"
                >
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className={cn('text-[9px] px-1 py-0.5 rounded font-bold', r.model_tier === 'deep' ? 'bg-[#9b59b6]/20 text-[#c084fc]' : 'bg-[#2e75b6]/20 text-[#60a5fa]')}>
                      {r.model_tier === 'deep' ? '심층' : '기본'}
                    </span>
                    {/* 한국은 종목명 — 파일명에는 코드가 들어 있다 (historyTitle). */}
                    <div className="text-xs font-medium text-[#e2e8f0] truncate">
                      {historyTitle(r as HistoryRow, market, names)}
                    </div>
                  </div>
                  <div className="text-[10px] text-[#475569]">
                    {r.created_at ? new Date(r.created_at).toLocaleString('ko-KR') : ''}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 헤더 컨트롤 */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-[#10b981]" />
          <div>
            <h1 className="text-base font-bold text-[#e2e8f0]">종목 리서치</h1>
          
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => requireLogin(() => setShowHist(true))}
            disabled={isRunning}
            title={isAuthed ? '저장된 리포트 보기' : '로그인 후 사용 가능합니다'}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0] hover:border-[#10b981]/40 rounded transition-colors disabled:opacity-40"
          >
            {isAuthed ? <History className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
            과거 레포트
          </button>
          {result && !isRunning && (
            <button
              onClick={downloadPDF}
              disabled={pdfBusy}
              className={cn(
                'flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] rounded font-bold transition-colors',
                pdfBusy
                  ? 'border border-[#1e2d40] text-[#64748b] opacity-50 cursor-not-allowed'
                  : 'border border-[#10b981]/50 bg-[#10b981]/10 text-[#34d399] hover:bg-[#10b981]/20'
              )}
            >
              {pdfBusy
                ? <span className="w-3.5 h-3.5 border-2 border-[#10b981] border-t-transparent rounded-full animate-spin" />
                : <Download className="w-3.5 h-3.5" />
              }
              PDF
            </button>
          )}
        </div>
      </div>

      {/* 입력 영역 */}
      <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3 space-y-3">
        {/* 티커 자동완성 */}
        <div className="relative">
          <div className={cn(
            'flex items-center gap-1.5 bg-[#0b0f1a] border rounded px-2 py-1.5 transition-colors',
            isRunning ? 'border-[#1e2d40] opacity-50' : 'border-[#1e2d40] focus-within:border-[#10b981]',
          )}>
            <Search className="w-3.5 h-3.5 text-[#475569] flex-shrink-0" />
            <input
              ref={tickerInputRef}
              value={shownTicker}
              onChange={e => {
                const v = e.target.value.toUpperCase()
                // 직접 친 글자는 그대로 요청 후보다 (미국 티커를 끝까지 친 경우).
                setTickerText(v)
                setTicker(v)
                setShowDropdown(true)
                setSugIdx(-1)
              }}
              onFocus={() => setShowDropdown(true)}
              onBlur={() => setTimeout(() => { setShowDropdown(false); setSugIdx(-1) }, 200)}
              onKeyDown={e => {
                // 한글 조합 중의 키는 IME 몫이다 — 받으면 확정용 Enter 가 시작으로도 처리될 수 있다.
                if (e.nativeEvent.isComposing) return
                if (sugOpen && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
                  e.preventDefault()
                  setSugIdx(i => moveHighlight(i, e.key === 'ArrowDown' ? 1 : -1, sugList.length))
                  return
                }
                if (sugOpen && e.key === 'Escape') { setShowDropdown(false); setSugIdx(-1); return }
                if (e.key !== 'Enter' || isRunning) return
                // 목록이 보이면 목록에서 고른 종목으로 시작한다. 예전에는 "삼성" 을
                // 그대로 티커로 보내 "종목 정보 없음" 으로 끝났다.
                const pick = sugOpen ? pickOnEnter(shownTicker, sugList, sugIdx) : null
                if (pick) {
                  e.preventDefault()
                  selectTicker(pick)
                  startAnalysis(pick.ticker)
                  return
                }
                if (ticker.trim()) startAnalysis()
              }}
              placeholder={market === 'KR'
                ? "종목 검색 (예: 삼성전자, SK하이닉스)"
                : "티커 검색 (예: AAPL, NVDA, TSLA)"}
              readOnly={isRunning}
              autoComplete="off"
              className={cn(
                'flex-1 bg-transparent text-sm text-[#e2e8f0] focus:outline-none placeholder-[#374151]',
                // 이름(한글)에는 고정폭을 쓰지 않는다 — 글자 사이가 벌어진다.
                market !== 'KR' && 'font-mono',
              )}
            />
          </div>
          {/* 목록은 body 로 띄운다 (SuggestionList 주석). 한국은 이름만 보인다. */}
          <SuggestionList
            anchorRef={tickerInputRef}
            open={showDropdown && !isRunning}
            items={sugList}
            highlighted={sugIdx}
            onPick={selectTicker}
            minWidth={260}
          />
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div>
            <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-1.5">분석 등급</div>
            <div className="option-row flex gap-1.5">
              {([['basic', '기본 분석'], ['deep', '심층 분석']] as const).map(([t, label]) => {
                // 관리자가 심층 분석을 잠그면 고를 수 없다. 서버도 같은 규칙으로
                // 막지만(resolve_model_tier), 눌러도 기본으로 처리되는 버튼을
                // 그냥 두면 사용자는 무시당했다고 느낀다.
                const locked = t === 'deep' && !features.deep_analysis_enabled
                const limited = t === 'deep' && !locked && features.deep_analysis_daily_limit
                return (
                <button key={t} onClick={() => setModelTier(t)} disabled={isRunning || locked}
                  title={locked ? '심층 분석은 현재 사용할 수 없습니다' : undefined}
                  className={cn(
                    'px-3 py-1.5 text-left rounded font-medium transition-colors min-w-[80px] disabled:opacity-40 disabled:cursor-not-allowed',
                    modelTier === t ? 'bg-[#10b981] text-white' : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]'
                  )}>
                  <div className="text-[11px] font-bold">{label}</div>
                  {locked && <div className="text-[9px] opacity-70">사용 불가</div>}
                  {limited && <div className="text-[9px] opacity-70">하루 1회</div>}
                </button>
              )})}
            </div>
          </div>
          <div className="flex items-center gap-2 ml-auto">
            {isRunning && (
              <button
                onClick={cancelAnalysis}
                disabled={cancelMut.isPending}
                className="flex items-center gap-1.5 px-3 py-2 bg-[#ef4444]/10 hover:bg-[#ef4444]/20 border border-[#ef4444]/40 text-[#f87171] text-sm font-bold rounded transition-colors disabled:opacity-50"
              >
                <Square className="w-3 h-3 fill-current" />
                중단
              </button>
            )}
            <button
              onClick={() => requireLogin(startAnalysis)}
              disabled={isRunning || !ticker.trim()}
              className="flex items-center gap-1.5 px-4 py-2 bg-[#10b981] hover:bg-[#059669] disabled:opacity-50 text-white text-sm font-bold rounded transition-colors"
            >
              {isAuthed ? <Play className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
              {isRunning ? '생성 중...' : '리포트 생성'}
            </button>
          </div>
        </div>

        {(startMut.isError || pollQ.data?.status === 'error') && (
          <div className="text-[10px] text-[#ef4444]">
            {/* 서버가 이유를 알려준 경우(AI 기능 중지 등) 그대로 보여준다.
                "다시 시도"만 안내하면 아무리 눌러도 안 되는 상황을 설명하지 못한다. */}
            {(startMut.error as { response?: { data?: { detail?: string } } } | null)
              ?.response?.data?.detail ?? '생성 실패. 다시 시도해주세요.'}
          </div>
        )}
      </div>

      {/* 진행 바 + 금융 팁 */}
      {isRunning && !result && (
        <div className="space-y-2">
          <ProgressBar progress={progress} elapsedMs={elapsedMs} type="equity" />
          <FinancialTips />
        </div>
      )}

      {/* 결과 */}
      {result && (
        <div className="space-y-3">
          <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3">
            <span className="text-[10px] text-[#4a5568] font-bold tracking-wider">종목: </span>
            {/* 한국은 종목명만 — 코드는 붙이지 않는다. 이름이 없으면 사전, 거기도
                없으면 티커. */}
            {market === 'KR' ? (
              <span className="text-sm font-bold text-[#10b981]">
                {result.company_name && result.company_name !== result.ticker
                  ? result.company_name
                  : (names[result.ticker] || result.ticker)}
              </span>
            ) : (
              <>
                <span className="text-sm font-mono font-bold text-[#10b981]">{result.ticker}</span>
                {result.company_name && result.company_name !== result.ticker && (
                  <span className="text-sm text-[#94a3b8] ml-2">— {result.company_name}</span>
                )}
              </>
            )}
          </div>
          {headerContent && <ReportHeaderCard headerContent={headerContent} type="equity" />}
          <div className="space-y-1.5">
            {sections.filter(([k]) => k !== 'header').map(([key, content], i) => (
              <ReportSection
                key={key}
                title={key}
                content={content}
                defaultExpanded={i === 0}
                sectionIndex={i}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── 산업 리포트 탭 ─────────────────────────────────────────────────────────────
function IndustryTab() {
  const features = useFeatures()
  // 이전에 고른 'deep' 이 sessionStorage 에 남아 있을 수 있다.
  useEffect(() => {
    if (!features.deep_analysis_enabled) setModelTier(t => (t === 'deep' ? 'basic' : t))
  }, [features.deep_analysis_enabled])
  const { isAuthed, requireLogin, modalEl } = useLoginPrompt()
  const [selectedId, setSelectedId] = useState<string>(() => marketSession.get(IND_INDUSTRY) || '')
  const [modelTier,  setModelTier]  = useState(() => marketSession.get(IND_TIER) || 'basic')
  const [jobId,      setJobId]      = useState<string | null>(() => marketSession.get(IND_JOB_ID))
  const [result,     setResult]     = useState<IndustryResult | null>(() => {
    try { const s = marketSession.get(IND_RESULT); return s ? JSON.parse(s) : null }
    catch { return null }
  })

  const [progress,  setProgress]  = useState(0)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [pdfBusy,   setPdfBusy]   = useState(false)
  const [showHist,  setShowHist]  = useState(false)

  const wantCancelRef  = useRef(false)
  const autoStartedRef = useRef(false)

  const industriesQ = useQuery({
    queryKey: ['industries'],
    queryFn:  listIndustries,
    staleTime: 3_600_000,
  })

  // 히스토리
  const histQ = useQuery({
    queryKey: ['report-history'],
    queryFn:  getReportHistory,
    staleTime: 60_000,
    enabled:  showHist && isAuthed,
  })
  const loadHistMut = useMutation({
    mutationFn: getReportFile,
    onSuccess: (data) => {
      const sections = parseSections(data.content)
      const r: IndustryResult = {
        industry_id:      data.name,
        industry_name_kr: data.name,
        industry_name_en: data.name,
        raw:      data.content,
        sections,
        report_type: 'industry',
      }
      marketSession.set(IND_RESULT, JSON.stringify(r))
      setResult(r)
      setShowHist(false)
    },
  })

  // 잡 시작 뮤테이션
  const startMut = useMutation({
    mutationFn: () => startIndustryReport({ industry_id: selectedId, model_tier: modelTier }),
    onSuccess: ({ job_id }) => {
      if (wantCancelRef.current) {
        wantCancelRef.current = false
        cancelReportJob(job_id).catch(() => {})
        marketSession.remove(IND_PENDING)
        marketSession.remove(IND_JOB_ID)
        return
      }
      marketSession.set(IND_JOB_ID,  job_id)
      marketSession.set(IND_PENDING, '1')
      setJobId(job_id)
    },
    onError: () => {
      wantCancelRef.current = false
      marketSession.remove(IND_PENDING)
      setProgress(0)
    },
  })

  // 폴링 쿼리
  const pollQ = useQuery({
    queryKey: ['report-job-ind', jobId],
    queryFn:  () => getReportJob(jobId!),
    enabled:  !!jobId,
    refetchInterval: 3_000,
    refetchIntervalInBackground: true,
    staleTime: 0,
    retry: false,
  })

  // 취소 뮤테이션
  const cancelMut = useMutation({
    mutationFn: (id: string) => cancelReportJob(id),
    onSettled: () => {
      marketSession.remove(IND_PENDING)
      marketSession.remove(IND_JOB_ID)
      setJobId(null)
      setProgress(0)
    },
  })

  // 폴링 결과 처리
  useEffect(() => {
    if (!pollQ.data) return
    if (pollQ.data.status === 'done' && pollQ.data.result) {
      const r = pollQ.data.result as unknown as IndustryResult
      marketSession.set(IND_RESULT, JSON.stringify(r))
      marketSession.remove(IND_PENDING)
      marketSession.remove(IND_JOB_ID)
      setResult(r)
      setProgress(100)
      setJobId(null)
      histQ.refetch()
    } else if (pollQ.data.status === 'error' || pollQ.data.status === 'cancelled') {
      marketSession.remove(IND_PENDING)
      marketSession.remove(IND_JOB_ID)
      setProgress(0)
      setJobId(null)
    }
  }, [pollQ.data]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!pollQ.isError) return
    marketSession.remove(IND_PENDING)
    marketSession.remove(IND_JOB_ID)
    setJobId(null)
    setProgress(0)
  }, [pollQ.isError])

  const isRunning = startMut.isPending || (!!jobId && !result)

  // 새로고침 후 재개
  useEffect(() => {
    if (autoStartedRef.current) return
    const hasPending = marketSession.get(IND_PENDING) === '1'
    const hasJobId   = !!marketSession.get(IND_JOB_ID)
    if (hasPending && !hasJobId && selectedId) {
      autoStartedRef.current = true
      startMut.mutate()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 진행 바 타이머
  useEffect(() => {
    if (!isRunning) return
    const startMs = parseInt(marketSession.get(IND_START) || String(Date.now()), 10)
    const maxMs   = MODE_MAX_MS.industry
    const tick = () => {
      const ms = Date.now() - startMs
      setElapsedMs(ms)
      setProgress(Math.min(95, (ms / maxMs) * 100))
    }
    tick()
    const id = setInterval(tick, 400)
    return () => clearInterval(id)
  }, [isRunning])

  const cancelAnalysis = () => {
    if (jobId) {
      cancelMut.mutate(jobId)
    } else if (startMut.isPending) {
      wantCancelRef.current = true
      marketSession.remove(IND_PENDING)
      marketSession.remove(IND_JOB_ID)
      setProgress(0)
    }
  }

  const startAnalysis = () => {
    marketSession.set(IND_INDUSTRY, selectedId)
    marketSession.set(IND_TIER,     modelTier)
    marketSession.set(IND_START,    String(Date.now()))
    marketSession.set(IND_PENDING,  '1')
    marketSession.remove(IND_RESULT)
    marketSession.remove(IND_JOB_ID)
    wantCancelRef.current = false
    setResult(null)
    setJobId(null)
    setProgress(0)
    setElapsedMs(0)
    startMut.mutate()
  }

  const downloadPDF = async () => {
    if (!result) return
    setPdfBusy(true)
    try {
      const dateStr = new Date().toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
      await downloadPdfFromHtml(
        buildIndustryPdfHtml(result, dateStr),
        `lens_industry_${result.industry_id}_${new Date().toISOString().slice(0, 10)}.pdf`,
      )
    } catch (e) {
      console.error('PDF 생성 실패:', e)
    } finally {
      setPdfBusy(false)
    }
  }

  const sections      = result?.sections ? Object.entries(result.sections) : []
  const headerContent = result?.sections?.['header'] || ''
  const indHistRows   = (histQ.data || []).filter(r => String(r.type).includes('industry'))

  return (
    <div className="space-y-4">
      {modalEl}
      {/* 히스토리 모달 */}
      {showHist && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowHist(false)}>
          <div className="bg-[#0a1628] border border-[#1e2d40] rounded-xl w-full max-w-lg max-h-[70vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e2d40]">
              <span className="text-sm font-bold text-[#e2e8f0]">과거 산업 레포트</span>
              <button onClick={() => setShowHist(false)} className="text-[#64748b] hover:text-[#e2e8f0]">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="overflow-y-auto flex-1 divide-y divide-[#1e2d40]">
              {histQ.isLoading && <p className="text-center text-[#64748b] py-8 text-sm">로딩 중...</p>}
              {histQ.isError && (
                <p className="text-center text-[#ef4444] py-8 text-sm">
                  {(histQ.error as any)?.response?.data?.detail ?? '레포트 목록을 불러오지 못했습니다.'}
                </p>
              )}
              {!histQ.isLoading && !histQ.isError && indHistRows.length === 0 && (
                <p className="text-center text-[#64748b] py-8 text-sm">저장된 산업 레포트 없음</p>
              )}
              {indHistRows.map(r => (
                <button
                  key={r.name}
                  onClick={() => loadHistMut.mutate(r.name)}
                  disabled={loadHistMut.isPending}
                  className="w-full text-left px-4 py-3 hover:bg-[#0f172a] transition-colors"
                >
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className={cn('text-[9px] px-1 py-0.5 rounded font-bold', r.model_tier === 'deep' ? 'bg-[#9b59b6]/20 text-[#c084fc]' : 'bg-[#2e75b6]/20 text-[#60a5fa]')}>
                      {r.model_tier === 'deep' ? '심층' : '기본'}
                    </span>
                    <div className="text-xs font-medium text-[#e2e8f0] truncate">{r.name}</div>
                  </div>
                  <div className="text-[10px] text-[#475569]">
                    {r.created_at ? new Date(r.created_at).toLocaleString('ko-KR') : ''}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 헤더 컨트롤 */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-[#10b981]" />
          <div>
            <h1 className="text-base font-bold text-[#e2e8f0]">산업 리서치</h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => requireLogin(() => setShowHist(true))}
            disabled={isRunning}
            title={isAuthed ? '저장된 리포트 보기' : '로그인 후 사용 가능합니다'}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0] hover:border-[#10b981]/40 rounded transition-colors disabled:opacity-40"
          >
            {isAuthed ? <History className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
            과거 레포트
          </button>
          {result && !isRunning && (
            <button
              onClick={downloadPDF}
              disabled={pdfBusy}
              className={cn(
                'flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] rounded font-bold transition-colors',
                pdfBusy
                  ? 'border border-[#1e2d40] text-[#64748b] opacity-50 cursor-not-allowed'
                  : 'border border-[#10b981]/50 bg-[#10b981]/10 text-[#34d399] hover:bg-[#10b981]/20'
              )}
            >
              {pdfBusy
                ? <span className="w-3.5 h-3.5 border-2 border-[#10b981] border-t-transparent rounded-full animate-spin" />
                : <Download className="w-3.5 h-3.5" />
              }
              PDF
            </button>
          )}
        </div>
      </div>

      {/* 산업 선택 그리드 */}
      <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3">
        <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-2">산업 선택</div>
        {industriesQ.isLoading && (
          <div className="text-xs text-[#64748b] py-4 text-center">산업 목록 로딩 중...</div>
        )}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {(industriesQ.data || []).map(ind => (
            <button
              key={ind.id}
              onClick={() => {
                setSelectedId(ind.id)
                marketSession.set(IND_INDUSTRY, ind.id)
              }}
              disabled={isRunning}
              className={cn(
                'text-left p-2.5 rounded border transition-all disabled:opacity-40 disabled:cursor-not-allowed',
                selectedId === ind.id
                  ? 'border-[#10b981]/50 bg-[#10b981]/10'
                  : 'border-[#1e2d40] hover:border-[#10b981]/30 hover:bg-[#0a1628]',
              )}
            >
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-sm">{ind.icon}</span>
                <span className={cn(
                  'text-[10px] font-bold truncate',
                  selectedId === ind.id ? 'text-[#34d399]' : 'text-[#94a3b8]',
                )}>{ind.name_kr}</span>
              </div>
              <div className="text-[9px] text-[#475569] truncate">{ind.name_en}</div>
            </button>
          ))}
        </div>
      </div>

      {/* 분석 등급 + 실행 버튼 */}
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mb-1.5">분석 등급</div>
          <div className="option-row flex gap-1.5">
            {([['basic', '기본 분석'], ['deep', '심층 분석']] as const).map(([t, label]) => {
              const locked = t === 'deep' && !features.deep_analysis_enabled
              const limited = t === 'deep' && !locked && features.deep_analysis_daily_limit
              return (
              <button key={t} onClick={() => setModelTier(t)} disabled={isRunning || locked}
                title={locked ? '심층 분석은 현재 사용할 수 없습니다' : undefined}
                className={cn(
                  'px-3 py-1.5 text-left rounded font-medium transition-colors min-w-[80px] disabled:opacity-40 disabled:cursor-not-allowed',
                  modelTier === t ? 'bg-[#10b981] text-white' : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]'
                )}>
                <div className="text-[11px] font-bold">{label}</div>
                {locked && <div className="text-[9px] opacity-70">사용 불가</div>}
                {limited && <div className="text-[9px] opacity-70">하루 1회</div>}
              </button>
            )})}
          </div>
        </div>
        <div className="flex items-center gap-2 ml-auto">
          {isRunning && (
            <button
              onClick={cancelAnalysis}
              disabled={cancelMut.isPending}
              className="flex items-center gap-1.5 px-3 py-2 bg-[#ef4444]/10 hover:bg-[#ef4444]/20 border border-[#ef4444]/40 text-[#f87171] text-sm font-bold rounded transition-colors disabled:opacity-50"
            >
              <Square className="w-3 h-3 fill-current" />
              중단
            </button>
          )}
          <button
            onClick={() => requireLogin(startAnalysis)}
            disabled={isRunning || !selectedId}
            className="flex items-center gap-1.5 px-4 py-2 bg-[#10b981] hover:bg-[#059669] disabled:opacity-50 text-white text-sm font-bold rounded transition-colors"
          >
            {isAuthed ? <Play className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
            {isRunning ? '생성 중...' : '산업 레포트 생성'}
          </button>
        {(startMut.isError || pollQ.data?.status === 'error') && (
          <span className="text-[10px] text-[#ef4444]">생성 실패. 다시 시도해주세요.</span>
        )}
        </div>
      </div>

      {/* 진행 바 + 금융 팁 */}
      {isRunning && !result && (
        <div className="space-y-2">
          <ProgressBar progress={progress} elapsedMs={elapsedMs} type="industry" />
          <FinancialTips />
        </div>
      )}

      {/* 결과 */}
      {result && (
        <div className="space-y-3">
          <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3">
            <span className="text-[10px] text-[#4a5568] font-bold tracking-wider">산업: </span>
            <span className="text-sm font-bold text-[#34d399]">
              {result.industry_name_kr || result.industry_id}
            </span>
            {result.industry_name_en && (
              <span className="text-sm text-[#64748b] ml-2">({result.industry_name_en})</span>
            )}
          </div>
          {headerContent && <ReportHeaderCard headerContent={headerContent} type="industry" />}
          <div className="space-y-1.5">
            {sections.filter(([k]) => k !== 'header').map(([key, content], i) => (
              <ReportSection
                key={key}
                title={key}
                content={content}
                defaultExpanded={i === 0}
                sectionIndex={i}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── 과거 레포트 탭 ─────────────────────────────────────────────────────────────
function HistoryTab() {
  const market = useMarket()
  const names = useTickerNames()
  const [filter,  setFilter]  = useState<'all' | 'equity' | 'industry'>('all')
  const [selected, setSelected] = useState<string | null>(null)
  const [viewResult, setViewResult] = useState<{ sections: Record<string, string>; raw: string; name: string } | null>(null)
  const [pdfBusy, setPdfBusy] = useState(false)

  const { isAuthed } = useAuth()
  const histQ = useQuery({
    queryKey: ['report-history'],
    queryFn:  getReportHistory,
    staleTime: 60_000,
    enabled:  isAuthed,
  })

  const fileMut = useMutation({
    mutationFn: getReportFile,
    onSuccess: (data) => {
      setViewResult({
        sections: parseSections(data.content),
        raw: data.content,
        name: data.name,
      })
    },
  })

  const rows = (histQ.data || []).filter(r => {
    const t = String(r.type)
    if (filter === 'equity')   return t.includes('equity')
    if (filter === 'industry') return t.includes('industry')
    return t.includes('equity') || t.includes('industry')
  })

  const viewSections = viewResult?.sections ? Object.entries(viewResult.sections) : []
  const viewHeader   = viewResult?.sections?.['header'] || ''
  const viewType     = selected?.includes('industry') ? 'industry' : 'equity'
  // 선택한 행 — 한국 종목 리포트의 이름을 찾는 데 쓴다 (파일명에는 코드가 들어 있다).
  const selectedRow  = (histQ.data || []).find(r => r.name === selected) as HistoryRow | undefined
  const selectedTitle = selected
    ? historyTitle(selectedRow ?? { name: selected }, market, names)
    : ''

  // PDF 다운로드 — 저장된 레포트는 EquityResult/IndustryResult 구조가 없어
  // buildGenericPdfHtml 을 쓴다(제목은 파일명에서 뽑는다 · 한국 종목은 종목명).
  const downloadPDF = async () => {
    if (!viewResult || !selected) return
    setPdfBusy(true)
    try {
      const isIndustry = viewType === 'industry'
      const krEquity = market === 'KR' && !isIndustry && selectedTitle !== selected
      const title    = krEquity ? selectedTitle : reportDisplayTitle(selected, isIndustry)
      const subtitle = `${isIndustry ? '산업 리서치' : '종목 리서치'} 레포트 · 과거 이력`
      const stamp    = selected.match(/_(\d{8}_\d{4})\.md$/)?.[1]
      await downloadPdfFromHtml(
        buildGenericPdfHtml(title, subtitle, viewResult.sections, isIndustry ? '#9b59b6' : '#2e75b6'),
        krEquity
          ? `lens_${pdfSafe(selectedTitle)}${stamp ? `_${stamp}` : ''}.pdf`
          : `${selected.replace(/\.md$/, '')}.pdf`,
      )
    } catch (e) {
      console.error('PDF 생성 실패:', e)
    } finally {
      setPdfBusy(false)
    }
  }

  // 과거 이력은 전부 개인 데이터다. 로그인 전에는 목록 자체를 만들지 않는다.
  if (!isAuthed) {
    return <AuthGate feature="과거 리포트 이력" />
  }

  return (
    <div className="space-y-4">
      {/* 필터 버튼 */}
      <div className="flex items-center gap-2">
        <div className="text-[10px] text-[#4a5568] font-bold tracking-wider mr-1">필터</div>
        {(['all', 'equity', 'industry'] as const).map(f => (
          <button
            key={f}
            onClick={() => { setFilter(f); setSelected(null); setViewResult(null) }}
            className={cn(
              'px-3 py-1 text-[11px] rounded font-bold transition-colors',
              filter === f
                ? 'bg-[#10b981] text-white'
                : 'bg-[#0b0f1a] border border-[#1e2d40] text-[#64748b] hover:text-[#e2e8f0]',
            )}
          >
            {f === 'all' ? '전체' : f === 'equity' ? '주식' : '산업'}
          </button>
        ))}
      </div>

      {histQ.isLoading && (
        <div className="text-center text-[#64748b] py-8 text-sm">로딩 중...</div>
      )}
      {/* 조회 실패와 "저장된 게 없음" 을 구분한다. isError 를 안 보면 서버가
          죽어도 "레포트 없음" 이 뜨고, 사용자는 자기가 만든 것이 사라진 줄
          안다 — 기다려야 하는 상황을 빈 목록으로 읽는다.
          같은 파일의 주식·산업 탭은 이미 이렇게 갈라져 있었다. 이 탭만
          빠져 있었고, 여기가 **세 목록을 다 보여주는 탭**이라 가장 넓다. */}
      {histQ.isError && (
        <div className="text-center text-[#ef4444] py-8 text-sm">
          {(histQ.error as any)?.response?.data?.detail ?? '레포트 목록을 불러오지 못했습니다.'}
        </div>
      )}
      {!histQ.isLoading && !histQ.isError && rows.length === 0 && (
        <div className="text-center text-[#64748b] py-8 text-sm">저장된 레포트 없음</div>
      )}

      {/* 레포트 목록 */}
      <div className="space-y-1.5">
        {rows.map(r => {
          const isEq  = String(r.type).includes('equity')
          const isInd = String(r.type).includes('industry')
          return (
            <button
              key={r.name}
              onClick={() => { setSelected(r.name); fileMut.mutate(r.name) }}
              disabled={fileMut.isPending}
              className={cn(
                'w-full text-left px-4 py-3 rounded border transition-all',
                selected === r.name
                  ? 'border-[#10b981]/50 bg-[#0a1628]'
                  : 'border-[#1e2d40] bg-[#060b14] hover:border-[#10b981]/30 hover:bg-[#0a1628]',
              )}
            >
              <div className="flex items-center gap-2 mb-1">
                <span className={cn(
                  'text-[9px] px-1.5 py-0.5 rounded font-bold',
                  isEq  ? 'bg-[#2e75b6]/20 text-[#60a5fa]'
                  : isInd ? 'bg-[#9b59b6]/20 text-[#c084fc]'
                  : 'bg-[#64748b]/20 text-[#94a3b8]',
                )}>
                  {isEq ? '주식' : isInd ? '산업' : r.type}
                </span>
                <span className={cn('text-[9px] px-1 py-0.5 rounded font-bold', r.model_tier === 'deep' ? 'bg-[#9b59b6]/20 text-[#c084fc]' : 'bg-[#475569]/20 text-[#94a3b8]')}>
                  {r.model_tier === 'deep' ? '심층' : '기본'}
                </span>
                <span className="text-[10px] text-[#475569]">
                  {r.created_at ? new Date(r.created_at).toLocaleString('ko-KR') : ''}
                </span>
              </div>
              {/* 한국 종목 리포트는 종목명 (historyTitle) — 파일명에는 코드가 들어 있다. */}
              <div className="text-xs text-[#94a3b8] truncate">{historyTitle(r as HistoryRow, market, names)}</div>
            </button>
          )
        })}
      </div>

      {/* 선택된 레포트 내용 */}
      {fileMut.isPending && (
        <div className="text-center text-[#64748b] py-4 text-sm">레포트 로딩 중...</div>
      )}
      {viewResult && !fileMut.isPending && (
        <div className="space-y-3 mt-4">
          <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-3 flex items-center justify-between gap-2">
            <div>
              <span className="text-[10px] text-[#4a5568] font-bold tracking-wider">레포트: </span>
              <span className="text-xs text-[#94a3b8]">{selectedTitle || viewResult.name}</span>
            </div>
            <button
              onClick={downloadPDF}
              disabled={pdfBusy}
              className={cn(
                'flex-shrink-0 flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] rounded font-bold transition-colors',
                pdfBusy
                  ? 'border border-[#1e2d40] text-[#64748b] opacity-50 cursor-not-allowed'
                  : 'border border-[#10b981]/50 bg-[#10b981]/10 text-[#10b981] hover:bg-[#10b981]/20'
              )}
            >
              {pdfBusy
                ? <span className="w-3.5 h-3.5 border-2 border-[#10b981] border-t-transparent rounded-full animate-spin" />
                : <Download className="w-3.5 h-3.5" />
              }
              PDF
            </button>
          </div>
          {viewHeader && <ReportHeaderCard headerContent={viewHeader} type={viewType} />}
          <div className="space-y-1.5">
            {viewSections.filter(([k]) => k !== 'header').map(([key, content], i) => (
              <ReportSection
                key={key}
                title={key}
                content={content}
                defaultExpanded={i === 0}
                sectionIndex={i}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── 매크로 링크 탭 ─────────────────────────────────────────────────────────────
function MacroLinkTab() {
  const LINKS = [
    { category: '중앙은행', items: [
      { label: 'Federal Reserve', desc: 'FOMC, 금리 결정, 경제 전망', href: 'https://www.federalreserve.gov' },
      { label: 'ECB', desc: '유럽중앙은행 정책 결정', href: 'https://www.ecb.europa.eu' },
      { label: 'Bank of Japan', desc: 'YCC 정책, 엔화', href: 'https://www.boj.or.jp/en' },
    ]},
    { category: '경제 데이터', items: [
      { label: 'FRED Economic Data', desc: '미국 매크로 경제 데이터베이스', href: 'https://fred.stlouisfed.org' },
      { label: 'BLS', desc: '미국 노동통계 (CPI, 고용)', href: 'https://www.bls.gov' },
      { label: 'BEA', desc: '미국 GDP, PCE 데이터', href: 'https://www.bea.gov' },
    ]},
    { category: '시장 & 리서치', items: [
      { label: 'CME FedWatch', desc: 'Fed 금리 확률 시장', href: 'https://www.cmegroup.com/trading/interest-rates/countdown-to-fomc.html' },
      { label: 'CBOE VIX', desc: '변동성 지수 현황', href: 'https://www.cboe.com/tradable_products/vix' },
      { label: 'US Treasury Yields', desc: '국채 수익률 커브', href: 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates' },
    ]},
    { category: '리서치 & 분석', items: [
      { label: 'IMF World Economic Outlook', desc: '글로벌 경제 전망', href: 'https://www.imf.org/en/Publications/WEO' },
      { label: 'BIS', desc: '국제결제은행 연구', href: 'https://www.bis.org' },
      { label: 'Research Affiliates', desc: '자산배분, 요인투자 연구', href: 'https://www.researchaffiliates.com' },
    ]},
  ]

  return (
    <div className="space-y-6">
      {LINKS.map(cat => (
        <div key={cat.category}>
          <div className="text-[10px] text-[#4a5568] font-bold tracking-[2px] mb-3">{cat.category.toUpperCase()}</div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {cat.items.map(item => (
              <a
                key={item.label}
                href={item.href}
                target="_blank"
                rel="noreferrer"
                className="block bg-[#060b14] border border-[#1e2d40] rounded p-3 hover:border-[#10b981]/40 hover:bg-[#0a1628] transition-all group"
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="text-xs font-medium text-[#e2e8f0] group-hover:text-[#10b981] transition-colors">{item.label}</span>
                  <span className="text-[#1e2d40] group-hover:text-[#10b981] transition-colors">↗</span>
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

// ── 메인 페이지 ───────────────────────────────────────────────────────────────
export default function LensReport() {
  const [tab, setTab] = useState(0)

  return (
    <div className="p-5 space-y-4 max-w-full">
      {/* 헤더 */}
      <div className="flex items-center gap-2">
        <BookOpen className="w-4 h-4 text-[#10b981]" />
        <div>
          <h1 className="text-base font-bold text-[#e2e8f0]">AI 리서치</h1>
        </div>
      </div>

      {/* 탭 바 */}
      <div className="tab-row flex border-b border-[#1e2d40]">
        {TABS.map((t, i) => (
          <button
            key={t}
            onClick={() => setTab(i)}
            className={cn(
              'px-4 py-2.5 text-[11px] font-bold tracking-wider transition-colors',
              tab === i
                ? 'text-[#10b981] border-b-2 border-[#10b981]'
                : 'text-[#4a5568] hover:text-[#64748b]',
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {/* 탭 콘텐츠 */}
      {tab === 0 && <EquityTab />}
      {tab === 1 && <IndustryTab />}
      {tab === 2 && <HistoryTab />}
      {tab === 3 && <MacroLinkTab />}

      <style>{`
        .lens-md { color: #cbd5e1; font-size: 14px; line-height: 1.75; }
        .lens-md h1, .lens-md h2 {
          color: #f1f5f9; font-size: 15px; font-weight: 700;
          margin: 14px 0 6px; border-bottom: 1px solid #1e2d40; padding-bottom: 4px;
        }
        .lens-md h3 { color: #e2e8f0; font-size: 14px; font-weight: 600; margin: 10px 0 4px; }
        .lens-md strong, .lens-md b { color: #f1f5f9; font-weight: 700; }
        .lens-md p { margin: 6px 0; }
        .lens-md ul, .lens-md ol { padding-left: 20px; margin: 6px 0; }
        .lens-md li { margin: 3px 0; color: #cbd5e1; }
        .lens-md table {
          width: 100%; border-collapse: collapse; font-size: 13px; margin: 10px 0;
          display: block; overflow-x: auto;
        }
        .lens-md thead { background: #0a1628; }
        .lens-md th {
          color: #94a3b8; border-bottom: 1px solid #1e2d40; padding: 8px 10px;
          text-align: left; font-weight: 700; white-space: nowrap;
        }
        .lens-md td { color: #e2e8f0; padding: 7px 10px; border-bottom: 1px solid #0f172a; font-size: 13px; }
        .lens-md tr:hover td { background: #0a1628; }
        .lens-md code { background: #0f172a; color: #10b981; padding: 2px 5px; border-radius: 3px; font-size: 12px; }
        .lens-md blockquote { border-left: 3px solid #10b981; padding-left: 10px; color: #94a3b8; margin: 6px 0; }
        .lens-md hr { border-color: #1e2d40; margin: 12px 0; }
        .lens-md a { color: #34d399; text-decoration: underline; }
      `}</style>
    </div>
  )
}
