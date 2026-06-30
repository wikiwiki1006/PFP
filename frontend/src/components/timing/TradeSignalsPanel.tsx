import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { getBBScanFull, getTechnicalChart } from '@/api'
import BollingerChart from './BollingerChart'
import { COLOR_UP, COLOR_DOWN } from './colors'
import type { BBScanPick } from '@/types'

function PickRow({
  p, kind, selected, onClick,
}: { p: BBScanPick; kind: 'long' | 'short'; selected: boolean; onClick: () => void }) {
  const color = kind === 'long' ? COLOR_UP : COLOR_DOWN
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2 rounded border transition-colors ${selected ? 'border-[#3b82f6] bg-[#3b82f6]/10' : 'border-[#1e2d40] hover:bg-[#0a1525]'}`}
    >
      <div className="flex justify-between items-center">
        <span className="font-mono font-bold text-sm text-[#e2e8f0]">{p.ticker}</span>
        <span className="text-[12px] font-mono font-bold" style={{ color }}>
          {p.move_pct >= 0 ? '+' : ''}{p.move_pct.toFixed(1)}%
        </span>
      </div>
      <div className="text-[10px] text-[#64748b] mt-0.5">Z {p.z.toFixed(2)} · ${p.entry} → ${p.target}</div>
    </button>
  )
}

export default function TradeSignalsPanel() {
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  const scanQ = useQuery({
    queryKey: ['timing-bb-scan-full'],
    queryFn: () => getBBScanFull(10),
    staleTime: 1800_000,
  })

  const chartQ = useQuery({
    queryKey: ['timing-technical-chart', selected],
    queryFn: () => getTechnicalChart(selected as string),
    enabled: !!selected,
    staleTime: 600_000,
  })

  function submitSearch() {
    const t = search.trim().toUpperCase()
    if (t) setSelected(t)
    setSearch('')
  }

  return (
    <div className="h-full flex">
      <div className="w-[300px] flex-shrink-0 border-r border-[#1e2d40] overflow-y-auto p-3 space-y-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#374151]" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submitSearch()}
            placeholder="티커 검색…"
            className="w-full bg-[#060b14] border border-[#1e2d40] rounded pl-8 pr-3 py-2 text-sm text-[#e2e8f0] placeholder:text-[#374151] focus:outline-none focus:border-[#3b82f6]"
          />
        </div>

        {scanQ.isLoading && <div className="text-sm text-[#64748b]">S&P500 전수 스캔 중… (최초 1회, 최대 1분)</div>}
        {scanQ.isError && <div className="text-sm text-[#ef4444]">스캔 데이터를 불러올 수 없습니다.</div>}

        {scanQ.data && (
          <>
            <div className="text-[11px] font-bold tracking-widest" style={{ color: COLOR_UP }}>
              매수 신호 TOP {scanQ.data.long_picks.length}
            </div>
            <div className="space-y-1.5">
              {scanQ.data.long_picks.map(p => (
                <PickRow key={p.ticker} p={p} kind="long" selected={selected === p.ticker} onClick={() => setSelected(p.ticker)} />
              ))}
            </div>
            <div className="text-[11px] font-bold tracking-widest mt-3" style={{ color: COLOR_DOWN }}>
              매도 신호 TOP {scanQ.data.short_picks.length}
            </div>
            <div className="space-y-1.5">
              {scanQ.data.short_picks.map(p => (
                <PickRow key={p.ticker} p={p} kind="short" selected={selected === p.ticker} onClick={() => setSelected(p.ticker)} />
              ))}
            </div>
            <div className="text-[10px] text-[#374151] pt-1">S&P500 {scanQ.data.scanned}개 종목 · 최대 3년 볼린저 밴드 분석</div>
          </>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {!selected && (
          <div className="text-sm text-[#64748b] flex items-center justify-center h-full">
            왼쪽에서 티커를 선택하거나 검색하세요.
          </div>
        )}
        {selected && chartQ.isLoading && <div className="text-sm text-[#64748b]">{selected} 분석 중…</div>}
        {selected && chartQ.isError && <div className="text-sm text-[#ef4444]">{selected} 데이터를 찾을 수 없습니다.</div>}
        {selected && chartQ.data && (
          <>
            <div className="text-lg font-mono font-bold text-[#e2e8f0] mb-2">{selected}</div>
            <BollingerChart
              key={selected}
              series={chartQ.data.series}
              keyPoints={chartQ.data.key_points}
              bias={chartQ.data.bias}
              currentZ={chartQ.data.current_z}
              height={420}
            />
          </>
        )}
      </div>
    </div>
  )
}
