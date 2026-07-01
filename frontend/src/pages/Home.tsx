import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Monitor, Globe, Dice5, TrendingUp, Zap, BookOpen, Activity } from 'lucide-react'
import { getPortfolioMetrics } from '@/api'
import { formatCurrency } from '@/lib/utils'

const MODULES = [
  {
    to: '/terminal',
    icon: Monitor,
    color: '#00e6ff',
    label: 'ALPHA TERMINAL',
    desc: ['yfinance 실시간 포트폴리오', '에쿼티 커브(정규화) + 섹터 도넛', '상관관계 히트맵 · AI 피드 · 뉴스', 'Daily Brief (Claude 웹서치) 내장'],
    cta: 'OPEN TERMINAL →',
  },
  {
    to: '/macro',
    icon: Globe,
    color: '#9b59b6',
    label: 'MACRO SCENARIO',
    desc: ['9-에이전트 멀티 파이프라인', '이벤트 분석 → 투자 전략', '포트폴리오 액션 플랜 (JSON)', 'Claude Sonnet / Haiku 선택'],
    cta: 'RUN SCENARIO →',
  },
  {
    to: '/monte-carlo',
    icon: Dice5,
    color: '#f59e0b',
    label: 'MONTE CARLO',
    desc: ['10,000회 시뮬레이션', '매크로 외생변수 충격 6개', '목표 달성 확률 수치화', 'Jump-Diffusion 패닉 클러스터링'],
    cta: 'RUN SIMULATION →',
  },
  {
    to: '/optimizer',
    icon: TrendingUp,
    color: '#3b82f6',
    label: 'OPTIMIZER',
    desc: ['Max Sharpe 황금비중 최적화', 'Black-Litterman 베이지안 모델', 'Fama-French 4-Factor 분석', '효율적 투자선 시각화'],
    cta: 'OPTIMIZE →',
  },
  {
    to: '/timing',
    icon: Zap,
    color: '#ef4444',
    label: 'TIMING ENGINE',
    desc: ['매크로 저승사자 레이더', '페어 트레이딩 Z-score 신호', '볼린저 밴드 평균 회귀', 'K-means 시장 국면 감지'],
    cta: 'OPEN ENGINE →',
  },
  {
    to: '/lens',
    icon: BookOpen,
    color: '#2e75b6',
    label: 'LENS REPORT',
    desc: ['종목별 AI 리서치 레포트', '산업 리서치 레포트', 'Claude 웹서치 + AI 집필', '텔레그램 자동 발송'],
    cta: 'GENERATE REPORT →',
  },
]

function Clock() {
  const now = new Date()
  const ny = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const kr = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Seoul' }))
  const fmt = (d: Date) => d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
  return (
    <div className="flex items-center gap-4 text-xs font-mono text-[#64748b]">
      <span>🇺🇸 NY {fmt(ny)} EST</span>
      <span className="text-[#1e2d40]">|</span>
      <span>🇰🇷 KR {fmt(kr)} KST</span>
      <span className="w-1.5 h-1.5 rounded-full bg-[#10b981] animate-pulse" />
    </div>
  )
}

export default function Home() {
  const navigate = useNavigate()
  const metricsQ = useQuery({ queryKey: ['portfolio-metrics'], queryFn: getPortfolioMetrics, staleTime: 60_000 })
  const m = metricsQ.data

  return (
    <div className="p-6 min-h-full space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="text-[10px] text-[#3b82f6] font-bold tracking-[3px] mb-1">PERSONAL FINANCIAL PLATFORM</div>
          <h1 className="text-2xl font-bold text-[#00e6ff] tracking-wide">PFP COMMAND CENTER</h1>
          <p className="text-xs text-[#4a5568] mt-1">Investment Operating System · 8 Modules</p>
        </div>
        <Clock />
      </div>

      {/* Pipeline Status */}
      <div className="bg-[#060b14] border border-[#1e2d40] rounded-lg p-4">
        <div className="text-[10px] text-[#4a5568] font-bold tracking-[2px] mb-3">PIPELINE STATUS</div>
        <div className="grid grid-cols-3 gap-4">
          <StatusCard
            label="포트폴리오"
            value={m ? `${Object.keys(metricsQ.data || {}).length > 0 ? formatCurrency(m.total_equity) : '로드 중'}` : '미로드'}
            sub={m ? `총 ${m.total_return_pct >= 0 ? '+' : ''}${m.total_return_pct.toFixed(2)}%` : ''}
            ok={!!m}
          />
          <StatusCard
            label="VIX 추세"
            value={m ? (m.vix > 25 ? '발작 구간' : m.vix > 18 ? '주의 구간' : '정상') : '로드 중'}
            sub={m ? `VIX ${m.vix.toFixed(2)}` : ''}
            ok={!!m}
            warn={m ? m.vix > 25 : false}
          />
          <StatusCard
            label="VIX 공포지수"
            value={m ? m.vix.toFixed(2) : '—'}
            sub={m ? (m.vix > 25 ? '발작 구간' : m.vix > 18 ? '주의 구간' : '정상 구간') : ''}
            ok={!!m}
            warn={m ? m.vix > 25 : false}
          />
        </div>
      </div>

      {/* Module Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {/* Alpha Terminal - featured */}
        <div
          onClick={() => navigate('/terminal')}
          className="cursor-pointer md:col-span-2 xl:col-span-1 bg-[#060b14] border border-[#1e2d40] hover:border-[#00e6ff]/40 rounded-lg p-5 transition-all hover:bg-[#0a1628] group"
          style={{ borderLeft: '3px solid #00e6ff' }}
        >
          <div className="flex items-center gap-2 mb-3">
            <Monitor className="w-4 h-4 text-[#00e6ff]" />
            <span className="text-[10px] font-bold tracking-[2px] text-[#64748b]">📊 ALPHA TERMINAL</span>
          </div>
          <div className="space-y-1 mb-4">
            {MODULES[0].desc.map(d => (
              <div key={d} className="text-xs text-[#4a5568] flex items-center gap-1.5">
                <span className="w-1 h-1 rounded-full bg-[#00e6ff]/50" />
                {d}
              </div>
            ))}
          </div>
          <div className="text-xs font-bold text-[#00e6ff] group-hover:underline">{MODULES[0].cta}</div>
        </div>

        {MODULES.slice(1).map(mod => (
          <ModuleCard key={mod.to} mod={mod} onClick={() => navigate(mod.to)} />
        ))}
      </div>
    </div>
  )
}

function StatusCard({ label, value, sub, ok, warn }: {
  label: string; value: string; sub: string; ok: boolean; warn?: boolean
}) {
  const color = warn ? '#ef4444' : ok ? '#10b981' : '#64748b'
  return (
    <div className="flex items-start gap-3">
      <div className="mt-1 w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: color, boxShadow: `0 0 6px ${color}` }} />
      <div>
        <div className="text-[10px] text-[#4a5568] font-bold tracking-wider">{label}</div>
        <div className="text-sm font-mono font-bold" style={{ color: warn ? '#ef4444' : ok ? '#e2e8f0' : '#4a5568' }}>{value}</div>
        {sub && <div className="text-[10px] text-[#374151]">{sub}</div>}
      </div>
    </div>
  )
}

function ModuleCard({ mod, onClick }: { mod: typeof MODULES[0]; onClick: () => void }) {
  const Icon = mod.icon
  return (
    <div
      onClick={onClick}
      className="cursor-pointer bg-[#060b14] border border-[#1e2d40] rounded-lg p-4 transition-all hover:bg-[#0a1628] group"
      style={{ borderLeft: `3px solid ${mod.color}` }}
    >
      <div className="flex items-center gap-2 mb-2">
        <Icon className="w-4 h-4" style={{ color: mod.color }} />
        <span className="text-[9px] font-bold tracking-[2px] text-[#64748b]">{mod.label}</span>
      </div>
      <div className="space-y-0.5 mb-3">
        {mod.desc.map(d => (
          <div key={d} className="text-[11px] text-[#374151] flex items-center gap-1.5">
            <span className="w-1 h-1 rounded-full flex-shrink-0" style={{ backgroundColor: mod.color + '60' }} />
            {d}
          </div>
        ))}
      </div>
      <div className="text-[11px] font-bold group-hover:underline" style={{ color: mod.color }}>{mod.cta}</div>
    </div>
  )
}
