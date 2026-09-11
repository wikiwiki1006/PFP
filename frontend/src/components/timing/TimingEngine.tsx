import { useState } from 'react'
import RegimePanel from './RegimePanel'
import TradeSignalsPanel from './TradeSignalsPanel'
import PairsTradingPanel from './PairsTradingPanel'
import MarketSituationPanel from './MarketSituationPanel'
import type { HoldingsMap } from '@/types'

interface TimingEngineProps {
  holdings: HoldingsMap
}

const TABS = [
  { label: 'Market Regime', sub: '종목별 시장 상황' },
  { label: 'Signal Scan', sub: '매매신호'},
  { label: 'Pairs Trading', sub: '페어 트레이딩' },
  // 'Market Situation' 이라고 쓰지 않는다. 엔드포인트 이름은 그쪽이지만,
  // 0번 탭의 부제가 이미 '종목별 시장 상황' 이라 탭 줄에 '시장 상황' 이
  // 둘 생긴다 — 같은 화면에서 이름이 겹치면 사용자는 어느 쪽이 무엇인지
  // 눌러 봐야 안다. 여기서는 내용으로 이름 짓는다.
  { label: 'Macro Spreads', sub: '금리차·신용 스프레드' },
]

export default function TimingEngine({ holdings }: TimingEngineProps) {
  const [tab, setTab] = useState(0)

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="tab-row flex items-center gap-1 px-2 pt-2 border-b border-[#1e2d40] flex-shrink-0 overflow-x-auto">
        {TABS.map((t, i) => (
          <button
            key={t.label}
            onClick={() => setTab(i)}
            className={`px-3 py-2 text-left rounded-t border-b-2 transition-colors flex-shrink-0 ${
              tab === i
                ? 'border-[#10b981] text-[#e2e8f0] bg-[#0a1525]'
                : 'border-transparent text-[#64748b] hover:text-[#94a3b8]'
            }`}
          >
            <div className="text-[12px] font-bold leading-tight">{t.label}</div>
            <div className="text-[10px] leading-tight">{t.sub}</div>
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === 0 && <RegimePanel holdings={holdings} />}
        {tab === 1 && <TradeSignalsPanel holdings={holdings} />}
        {tab === 2 && <PairsTradingPanel holdings={holdings} />}
        {/* 보유와 무관한 시장 지표라 holdings 를 받지 않는다. */}
        {tab === 3 && <MarketSituationPanel />}
      </div>
    </div>
  )
}
