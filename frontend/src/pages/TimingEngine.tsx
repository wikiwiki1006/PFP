import { useQuery } from '@tanstack/react-query'
import { Zap } from 'lucide-react'
import { getHoldings } from '@/api'
import TimingEngineTabs from '@/components/timing/TimingEngine'

export default function TimingEngine() {
  const holdQ = useQuery({ queryKey: ['holdings-raw'], queryFn: getHoldings })

  return (
    <div className="h-full flex flex-col">
      <div className="flex-shrink-0 flex items-center gap-2 p-5 pb-3">
        <Zap className="w-4 h-4 text-[#ef4444]" />
        <div>
          <h1 className="text-base font-bold text-[#e2e8f0]">TIMING ENGINE</h1>
          <p className="text-[11px] text-[#4a5568]">매크로 레이더 · K-means 시장 국면 · 볼린저 밴드 스캔 · 평균회귀/저항선 · 페어 트레이딩</p>
        </div>
      </div>
      <div className="flex-1 min-h-0 mx-5 mb-5 border border-[#1e2d40] rounded-lg overflow-hidden bg-[#0b0f1a]">
        <TimingEngineTabs holdings={holdQ.data ?? {}} />
      </div>
    </div>
  )
}
