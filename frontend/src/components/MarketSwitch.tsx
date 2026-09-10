/**
 * components/MarketSwitch.tsx
 * ──────────────────────────
 * 미국 / 한국 전환 스위치. 상단 바에 놓는다.
 *
 * 좌측 메뉴(포트폴리오·시나리오·최적화·신호·리서치)는 그대로 두고 **내용만**
 * 시장에 따라 갈린다. 메뉴를 둘로 나누면 같은 기능이 두 벌 보여 화면이 복잡해진다.
 *
 * 전환하면 포트폴리오 화면으로 옮겨 가며 페이지를 새로 연다.
 *
 * 캐시만 비우는 방식은 충분하지 않았다. 지금 보고 있던 화면이 그 시장에서는
 * 의미가 없을 수 있고(예: 한국에서 보던 종목 상세), 무엇보다 진행 중이던
 * 리포트 생성 같은 메모리 상태가 그대로 남아 다른 시장 화면에 얹힌다.
 * 통째로 다시 여는 편이 "완전히 다른 창"이라는 약속을 지키는 확실한 방법이다.
 */
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { MARKETS, getMarket, setMarket, subscribeMarket, type Market } from '@/lib/market'

// 전환 후 항상 여기로 돌아온다.
const HOME_PATH = '/terminal'

export default function MarketSwitch() {
  const [market, setLocal] = useState<Market>(getMarket)

  // 다른 곳에서 시장을 바꿔도 이 버튼이 따라오도록 구독해 둔다.
  useEffect(() => subscribeMarket(setLocal), [])

  const pick = (m: Market) => {
    if (m === market) return
    setMarket(m)
    // 라우터 이동이 아니라 실제 페이지 로드를 건다. 라우터로만 옮기면
    // 조회 캐시와 진행 중이던 잡 폴링이 그대로 살아남는다.
    window.location.assign(HOME_PATH)
  }

  return (
    <div
      role="group"
      aria-label="시장 선택"
      className="flex flex-shrink-0 items-center rounded-lg border border-[#1e2d40] bg-[#0b1220] p-0.5"
    >
      {(Object.keys(MARKETS) as Market[]).map((m) => (
        <button
          key={m}
          onClick={() => pick(m)}
          aria-pressed={market === m}
          className={cn(
            'whitespace-nowrap rounded-md px-2 py-1 text-[10px] font-bold tracking-wide transition-colors sm:px-2.5 sm:text-[11px]',
            market === m
              ? 'bg-[#10b981] text-white'
              : 'text-[#64748b] hover:text-[#cbd5e1]',
          )}
        >
          {MARKETS[m].label}
        </button>
      ))}
    </div>
  )
}
