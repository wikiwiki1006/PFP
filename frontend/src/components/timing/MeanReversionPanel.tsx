import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getTechnicalChart } from '@/api'
import BollingerChart from './BollingerChart'
import type { HoldingsMap } from '@/types'

interface MeanReversionPanelProps {
  holdings: HoldingsMap
}

const TOGGLES: { key: 'bands' | 'mid' | 'resistance'; label: string }[] = [
  { key: 'bands',      label: '볼린저 밴드' },
  { key: 'mid',        label: '평균 회귀선' },
  { key: 'resistance', label: '저항선' },
]

export default function MeanReversionPanel({ holdings }: MeanReversionPanelProps) {
  const tickers = Object.keys(holdings).filter(t => t !== 'CASH')
  const [selected, setSelected] = useState<string | null>(tickers[0] ?? null)
  const [overlays, setOverlays] = useState<Record<string, boolean>>({ bands: true, mid: true, resistance: true })

  const chartQ = useQuery({
    queryKey: ['timing-technical-chart', selected],
    queryFn: () => getTechnicalChart(selected as string),
    enabled: !!selected,
    staleTime: 600_000,
  })

  function toggle(key: string) {
    setOverlays(prev => ({ ...prev, [key]: !prev[key] }))
  }

  if (tickers.length === 0) {
    return <div className="p-4 text-sm text-[#64748b]">보유 종목이 없습니다. 포트폴리오에 종목을 추가하면 여기서 분석할 수 있습니다.</div>
  }

  return (
    <div className="h-full flex">
      <div className="w-[240px] flex-shrink-0 border-r border-[#1e2d40] overflow-y-auto p-3 space-y-1.5">
        <div className="text-[11px] text-[#64748b] font-bold tracking-widest uppercase mb-2">보유 종목</div>
        {tickers.map(t => (
          <button
            key={t}
            onClick={() => setSelected(t)}
            className={`w-full text-left px-3 py-2 rounded border font-mono font-bold text-sm transition-colors ${selected === t ? 'border-[#3b82f6] bg-[#3b82f6]/10 text-[#3b82f6]' : 'border-[#1e2d40] text-[#e2e8f0] hover:bg-[#0a1525]'}`}
          >
            {t}
            <span className="text-[11px] text-[#64748b] font-normal ml-2">{holdings[t]?.q ?? 0}주</span>
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {!selected && (
          <div className="text-sm text-[#64748b] flex items-center justify-center h-full">왼쪽에서 종목을 선택하세요.</div>
        )}
        {selected && (
          <>
            <div className="flex items-center justify-between mb-3">
              <div className="text-lg font-mono font-bold text-[#e2e8f0]">{selected}</div>
              <div className="flex items-center gap-3">
                {TOGGLES.map(t => (
                  <label key={t.key} className="flex items-center gap-1.5 text-[11px] text-[#94a3b8] cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={overlays[t.key]}
                      onChange={() => toggle(t.key)}
                      className="accent-[#3b82f6] w-3.5 h-3.5"
                    />
                    {t.label}
                  </label>
                ))}
              </div>
            </div>

            {chartQ.isLoading && <div className="text-sm text-[#64748b]">{selected} 분석 중…</div>}
            {chartQ.isError && <div className="text-sm text-[#ef4444]">{selected} 데이터를 찾을 수 없습니다.</div>}
            {chartQ.data && (
              <BollingerChart
                key={selected}
                series={chartQ.data.series}
                keyPoints={chartQ.data.key_points}
                bias={chartQ.data.bias}
                currentZ={chartQ.data.current_z}
                overlays={overlays as any}
                height={420}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}
