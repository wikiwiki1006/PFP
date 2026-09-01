import { useQuery } from '@tanstack/react-query'
import { Zap } from 'lucide-react'
import { getHoldings } from '@/api'
import { useAuth } from '@/lib/AuthContext'
import TimingEngineTabs from '@/components/timing/TimingEngine'

export default function TimingEngine() {
  const { isAuthed } = useAuth()

  // 이 화면 자체는 공개 시장 데이터라 로그인 없이 볼 수 있다.
  // 보유 종목은 페어 트레이딩 프리셋에만 쓰이므로, 비로그인이면 조회하지 않는다
  // (요청해봐야 401 이고, 프리셋만 비는 것으로 충분하다).
  const holdQ = useQuery({
    queryKey: ['holdings-raw'],
    queryFn: getHoldings,
    enabled: isAuthed,
  })

  return (
    <div className="h-full flex flex-col">
      <div className="flex-shrink-0 flex items-center gap-2 p-5 pb-3">
        <Zap className="w-4 h-4 text-[#ef4444]" />
        <div>
          <h1 className="text-base font-bold text-[#e2e8f0]">트레이딩 신호</h1>
          <p className="text-[11px] text-[#4a5568]">종목 추세, 급등/급락 신호, 유사 종목 분석 정보를 제공합니다.</p>
        </div>
      </div>
      {/* 이 박스(테두리+좌우 여백)가 안의 차트들 최대 폭을 깎아먹는다 — 모바일에서는
          없애 화면을 꽉 채우고, 데스크탑은 여유가 있으니 그대로 둔다. */}
      <div className="flex-1 min-h-0 mb-5 md:mx-5 border-0 md:border md:border-[#1e2d40] rounded-none md:rounded-lg overflow-hidden bg-[#0b0f1a]">
        <TimingEngineTabs holdings={holdQ.data ?? {}} />
      </div>
    </div>
  )
}
