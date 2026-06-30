import { useQuery } from '@tanstack/react-query'
import { getMarketSituation } from '@/api'
import InfoTooltip from './InfoTooltip'
import type { MarketSituationMetric } from '@/types'

const RATE_SPREAD_INFO = `10년물-2년물 미국채 금리차(T10Y2Y).
낮음(역전): 향후 침체 신호로 해석되는 위험 구간.
정상: 완만한 우상향 수익률 곡선, 안정적 국면.
높음: 곡선이 가팔라짐, 경기 회복 기대 또는 인플레 우려.`

const HY_SPREAD_INFO = `하이일드(투기등급) 채권과 국채의 스프레드(BAMLH0A0HYM2).
낮음: 신용 시장이 낙관적, 위험 선호 강함.
정상: 평년 수준의 신용 리스크 프리미엄.
높음: 신용 경색 우려 확대, 위험 회피 국면(주가에 부정적).`

function badge(metric: MarketSituationMetric, label: string) {
  return (
    <span
      className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider"
      style={{ color: metric.color, backgroundColor: `${metric.color}1a` }}
    >
      {label === 'Low' ? '낮음' : label === 'High' ? '높음' : '정상'}
    </span>
  )
}

function MetricCard({
  title, info, metric, unit, valueFmt,
}: {
  title: string
  info: string
  metric: MarketSituationMetric
  unit: string
  valueFmt: (v: number) => string
}) {
  return (
    <div className="bg-[#060b14] border border-[#1e2d40] rounded p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-[#64748b] font-bold tracking-wider uppercase">{title}</span>
          <InfoTooltip text={info} />
        </div>
        {badge(metric, metric.level)}
      </div>
      <div className="text-2xl font-mono font-bold" style={{ color: metric.color }}>
        {valueFmt(metric.value)}{unit}
      </div>
      <div className="mt-2 h-1.5 rounded-full bg-[#0f172a] overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${metric.percentile}%`, backgroundColor: metric.color }}
        />
      </div>
      <div className="text-[10px] text-[#374151] mt-1">과거 10년 대비 백분위 {metric.percentile.toFixed(0)}%</div>
    </div>
  )
}

export default function MarketSituationPanel() {
  const q = useQuery({
    queryKey: ['timing-market-situation'],
    queryFn: getMarketSituation,
    staleTime: 3600_000,
  })

  if (q.isLoading) {
    return <div className="p-4 text-sm text-[#64748b]">로드 중…</div>
  }
  if (q.isError || !q.data) {
    return <div className="p-4 text-sm text-[#ef4444]">시장 상황 데이터를 불러올 수 없습니다.</div>
  }

  const d = q.data

  return (
    <div className="p-4 space-y-4">
      <div className="text-[11px] text-[#64748b] font-bold tracking-widest uppercase">시장 상황 — 매크로 지표</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <MetricCard
          title="금리차 (10Y-2Y)"
          info={RATE_SPREAD_INFO}
          metric={d.rate_spread}
          unit="%p"
          valueFmt={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}`}
        />
        <MetricCard
          title="하이일드 스프레드"
          info={HY_SPREAD_INFO}
          metric={d.hy_spread}
          unit="%"
          valueFmt={(v) => v.toFixed(2)}
        />
      </div>
      <div className="text-[10px] text-[#374151]">데이터 출처: {d.source === 'FRED' ? 'FRED (St. Louis Fed)' : '대체 추정값'}</div>
    </div>
  )
}
