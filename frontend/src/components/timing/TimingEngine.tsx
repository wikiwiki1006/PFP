import { useState } from 'react'
import RegimePanel from './RegimePanel'
import TradeSignalsPanel from './TradeSignalsPanel'
import PairsTradingPanel from './PairsTradingPanel'
import type { HoldingsMap } from '@/types'

interface TimingEngineProps {
  holdings: HoldingsMap
}

const TABS = [
  { label: 'Market Regime', sub: '종목별 시장 상황' },
  { label: 'Signal Scan', sub: '매매신호 · 볼린저 밴드' },
  { label: 'Pairs Trading', sub: '페어 트레이딩' },
]

export default function TimingEngine({ holdings }: TimingEngineProps) {
  const [tab, setTab] = useState(0)

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex items-center gap-1 px-2 pt-2 border-b border-[#1e2d40] flex-shrink-0 overflow-x-auto">
        {TABS.map((t, i) => (
          <button
            key={t.label}
            onClick={() => setTab(i)}
            className={`px-3 py-2 text-left rounded-t border-b-2 transition-colors flex-shrink-0 ${
              tab === i
                ? 'border-[#3b82f6] text-[#e2e8f0] bg-[#0a1525]'
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
        {tab === 2 && <PairsTradingPanel />}
      </div>
    </div>
  )
}
