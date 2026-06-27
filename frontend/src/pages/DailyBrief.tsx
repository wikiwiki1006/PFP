import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { generateDailyBrief, getDailyBriefHistory, getDailyBriefFile } from '@/api'
import { FileText, Play, ChevronRight, AlertCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'

export default function DailyBrief() {
  const [activeFile, setActiveFile] = useState<string | null>(null)
  const [viewContent, setViewContent] = useState<string | null>(null)
  const [logs, setLogs] = useState<string[]>([])

  const historyQ = useQuery({ queryKey: ['daily-brief-history'], queryFn: getDailyBriefHistory, staleTime: 60_000 })

  const fileMut = useMutation({
    mutationFn: getDailyBriefFile,
    onSuccess: (d) => setViewContent(d.content),
  })

  const genMut = useMutation({
    mutationFn: generateDailyBrief,
    onMutate: () => { setLogs(['[1/3] 포트폴리오 가격 데이터 수집 중...']); setViewContent(null) },
    onSuccess: (d) => {
      setViewContent(d.report)
      setLogs(d.logs || ['완료'])
      historyQ.refetch()
    },
    onError: (e: any) => setLogs(prev => [...prev, `오류: ${e.message}`]),
  })

  return (
    <div className="flex h-full bg-[#0b0f1a]">
      {/* Left: History sidebar */}
      <div className="w-56 border-r border-[#1e2d40] bg-[#060b14] flex flex-col flex-shrink-0">
        <div className="p-3 border-b border-[#1e2d40]">
          <div className="text-[10px] font-bold text-[#4a5568] tracking-wider mb-2">BRIEF HISTORY</div>
          <button
            onClick={() => genMut.mutate()}
            disabled={genMut.isPending}
            className="w-full flex items-center justify-center gap-1.5 px-3 py-2 bg-[#10b981]/10 border border-[#10b981]/30 text-[#10b981] text-xs rounded hover:bg-[#10b981]/20 disabled:opacity-50"
          >
            <Play className="w-3 h-3" />
            {genMut.isPending ? 'GENERATING...' : 'GENERATE BRIEF'}
          </button>
        </div>
        <div className="flex-1 overflow-y-auto py-1">
          {(historyQ.data || []).map((f) => (
            <button
              key={f.name}
              onClick={() => { setActiveFile(f.name); fileMut.mutate(f.name) }}
              className={`w-full text-left px-3 py-2 text-xs border-b border-[#0f172a] flex items-center gap-1.5 transition-colors ${activeFile === f.name ? 'bg-[#111827] text-[#10b981]' : 'text-[#4a5568] hover:text-[#94a3b8] hover:bg-[#0a0f1a]'}`}
            >
              <FileText className="w-3 h-3 flex-shrink-0" />
              <span className="truncate font-mono">{f.name}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-h-0">
        {/* Header */}
        <div className="flex-shrink-0 px-6 py-4 border-b border-[#1e2d40] bg-[#060b14]">
          <div className="flex items-center gap-2">
            <FileText className="w-4 h-4 text-[#10b981]" />
            <span className="text-[10px] font-bold text-[#4a5568] tracking-[3px]">DAILY PORTFOLIO BRIEF</span>
          </div>
          <p className="text-xs text-[#374151] mt-1">Claude Sonnet 웹서치 기반 AI 포트폴리오 브리프 · Bloomberg-style 한국어 리포트</p>
        </div>

        {/* Logs area when generating */}
        {genMut.isPending && (
          <div className="flex-shrink-0 mx-6 my-3 bg-[#060b14] border border-[#1e2d40] rounded p-3">
            <div className="text-[10px] font-bold text-[#4a5568] mb-2 tracking-wider">PIPELINE LOG</div>
            {logs.map((l, i) => (
              <div key={i} className="text-xs text-[#10b981] font-mono py-0.5 flex items-center gap-2">
                <ChevronRight className="w-3 h-3 flex-shrink-0" />
                {l}
              </div>
            ))}
            <div className="flex items-center gap-2 mt-2">
              <div className="w-1.5 h-1.5 rounded-full bg-[#10b981] animate-pulse" />
              <span className="text-[10px] text-[#4a5568]">AI 분석 중... (약 1-2분 소요)</span>
            </div>
          </div>
        )}

        {genMut.isError && (
          <div className="flex-shrink-0 mx-6 my-3 bg-[#ef4444]/10 border border-[#ef4444]/30 rounded p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#ef4444]" />
            <span className="text-xs text-[#ef4444]">{(genMut.error as any)?.message || '생성 실패'}</span>
          </div>
        )}

        {/* Report display */}
        <div className="flex-1 overflow-y-auto p-6">
          {!viewContent && !genMut.isPending && (
            <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
              <div className="w-16 h-16 rounded-full bg-[#10b981]/10 flex items-center justify-center">
                <FileText className="w-8 h-8 text-[#10b981]/50" />
              </div>
              <div>
                <p className="text-[#4a5568] text-sm">아직 생성된 브리프가 없습니다.</p>
                <p className="text-[#374151] text-xs mt-1">GENERATE BRIEF 버튼을 눌러 AI 리포트를 생성하세요.</p>
              </div>
            </div>
          )}
          {viewContent && (
            <div className="max-w-4xl mx-auto">
              <div className="prose prose-sm prose-invert max-w-none markdown-body">
                <ReactMarkdown>{viewContent}</ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      </div>

      <style>{`
        .markdown-body h1, .markdown-body h2, .markdown-body h3 { color: #e2e8f0; margin-top: 1.5rem; }
        .markdown-body h1 { font-size: 1.2rem; border-bottom: 1px solid #1e2d40; padding-bottom: 0.5rem; }
        .markdown-body h2 { font-size: 1rem; color: #10b981; }
        .markdown-body h3 { font-size: 0.9rem; color: #64748b; }
        .markdown-body p { color: #94a3b8; line-height: 1.7; font-size: 0.8rem; }
        .markdown-body strong { color: #e2e8f0; }
        .markdown-body ul, .markdown-body ol { color: #94a3b8; font-size: 0.8rem; }
        .markdown-body li { margin-bottom: 0.25rem; }
        .markdown-body hr { border-color: #1e2d40; }
        .markdown-body code { background: #111827; color: #10b981; padding: 2px 4px; border-radius: 3px; font-size: 0.75rem; }
        .markdown-body blockquote { border-left: 3px solid #3b82f6; padding-left: 1rem; color: #64748b; }
        .markdown-body table { font-size: 0.75rem; width: 100%; }
        .markdown-body th { color: #4a5568; border-bottom: 1px solid #1e2d40; padding: 0.5rem; text-align: left; }
        .markdown-body td { color: #94a3b8; padding: 0.5rem; border-bottom: 1px solid #0f172a; }
      `}</style>
    </div>
  )
}
