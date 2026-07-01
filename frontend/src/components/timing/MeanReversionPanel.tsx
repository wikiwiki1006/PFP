import { useState } from 'react'
import BollingerChart from './BollingerChart'
import type { HoldingsMap } from '@/types'

interface MeanReversionPanelProps {
  holdings: HoldingsMap
}

export default function MeanReversionPanel({ holdings }: MeanReversionPanelProps) {
  const tickers  = Object.keys(holdings).filter(t => t !== 'CASH')
  const [selected, setSelected] = useState<string | null>(tickers[0] ?? null)

  if (tickers.length === 0) {
    return (
      <div className="p-4 text-sm text-[#64748b]">
        보유 종목이 없습니다. 포트폴리오에 종목을 추가하면 여기서 분석할 수 있습니다.
      </div>
    )
  }

  return (
    <div className="h-full flex">
      <div className="w-[200px] flex-shrink-0 border-r border-[#1e2d40] overflow-y-auto p-3 space-y-1.5">
        <div className="text-[11px] text-[#64748b] font-bold tracking-widest uppercase mb-2">보유 종목</div>
        {tickers.map(t => (
          <button
            key={t}
            onClick={() => setSelected(t)}
            className={`w-full text-left px-3 py-2 rounded border font-mono font-bold text-sm transition-colors ${
              selected === t
                ? 'border-[#3b82f6] bg-[#3b82f6]/10 text-[#3b82f6]'
                : 'border-[#1e2d40] text-[#e2e8f0] hover:bg-[#0a1525]'
            }`}
          >
            {t}
            <span className="text-[11px] text-[#64748b] font-normal ml-2">{holdings[t]?.q ?? 0}주</span>
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-4 min-w-0">
        {!selected ? (
          <div className="text-sm text-[#64748b] flex items-center justify-center h-full">
            왼쪽에서 종목을 선택하세요.
          </div>
        ) : (
          <>
            <div className="text-lg font-mono font-bold text-[#e2e8f0] mb-3">{selected}</div>
            <BollingerChart key={selected} ticker={selected} height={420} />
          </>
        )}
      </div>
    </div>
  )
}
