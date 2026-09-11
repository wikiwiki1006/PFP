import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, ChevronLeft, ChevronRight } from 'lucide-react'
import { getSignalScan, getSignalScore } from '@/api'
import { getMarket } from '@/lib/market'
import BollingerChart from './BollingerChart'
import { COLOR_UP, COLOR_DOWN } from './colors'
import type { SignalScanPick, HoldingsMap } from '@/types'
import TickerLabel from '@/components/TickerLabel'
import { useTickerNames, displayTicker } from '@/lib/useTickerNames'

/** 서버가 준 실패 사유. 없으면 null (호출부가 기본 문구를 쓴다).
 *
 *  FastAPI 는 HTTPException 의 detail 을 `{ "detail": "..." }` 로 보낸다.
 *  그 문장이 "무엇을 기다려야 하는지" 를 담고 있으므로 버리지 않는다. */
function scanErrorMessage(err: unknown): string | null {
  const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof d === 'string' && d.trim() ? d : null
}

interface TradeSignalsPanelProps {
  holdings?: HoldingsMap
}

function ScoreBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100))
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[9px] text-[#64748b] w-8 shrink-0">{label}</span>
      <div className="flex-1 h-1 rounded bg-[#1e2d40] overflow-hidden">
        <div className="h-full rounded" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  )
}

function PickRow({
  p, kind, selected, onClick,
}: { p: SignalScanPick; kind: 'long' | 'short'; selected: boolean; onClick: () => void }) {
  const color = kind === 'long' ? COLOR_UP : COLOR_DOWN
  const macdUp = p.macd_hist >= p.macd_hist_prev
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2 rounded border transition-colors ${selected ? 'border-[#10b981] bg-[#10b981]/10' : 'border-[#1e2d40] hover:bg-[#0a1525]'}`}
    >
      <div className="flex justify-between items-center">
        {/* 한국은 종목명이 주, 코드가 보조. 미국은 반대 (TickerLabel 참고) */}
        <TickerLabel ticker={p.ticker} name={p.name} />
        <span className="font-mono font-bold tabular-nums" style={{ color }}>
          <span className="text-[15px]">{p.score}</span><span className="text-[10px] text-[#64748b]">/100</span>
        </span>
      </div>
      <div className="text-[10px] text-[#64748b] mt-0.5 flex items-center gap-2">
        <span>RSI {p.rsi.toFixed(0)}</span>
        <span>Vol×{p.volume_ratio.toFixed(1)}</span>
        <span style={{ color: macdUp ? COLOR_UP : COLOR_DOWN }}>MACD {macdUp ? '▲' : '▼'}</span>
      </div>
      <div className="mt-1.5 space-y-1">
        <ScoreBar label="수급"   value={p.components.volume}   max={40} color={color} />
        <ScoreBar label="모멘텀" value={p.components.momentum} max={30} color={color} />
        <ScoreBar label="RSI"    value={p.components.rsi}      max={30} color={color} />
      </div>
    </button>
  )
}

/** 종목 하나의 매수/매도 참고 점수 — Signal Scan 상위 10개 리스트에 없어도(순위 밖,
    보유 종목, 검색한 임의 티커 등) 볼 수 있게 한다. 상세 차트(BollingerChart) 위에 표시. */
function TickerScoreCard({ ticker }: { ticker: string }) {
  const q = useQuery({
    queryKey: ['signal-score', ticker],
    queryFn:  () => getSignalScore(ticker),
    staleTime: 300_000,
    retry: false,
  })

  if (q.isLoading) return <div className="text-xs text-[#64748b] mb-3">매매신호 점수 계산 중…</div>
  if (q.isError || !q.data) return null

  const { long, long_filter_pass, short, short_filter_pass } = q.data
  if (!long && !short) {
    return (
      <div className="text-xs text-[#64748b] mb-3">
        거래량 데이터가 부족해 매매신호 점수를 계산할 수 없습니다.
      </div>
    )
  }

  const sides: { key: string; side: SignalScanPick | null; pass: boolean; label: string; color: string }[] = [
    { key: 'long',  side: long,  pass: long_filter_pass,  label: '매수', color: COLOR_UP },
    { key: 'short', side: short, pass: short_filter_pass, label: '매도', color: COLOR_DOWN },
  ]

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
      {sides.map(({ key, side, pass, label, color }) => {
        if (!side) {
          return (
            <div key={key} className="border border-[#1e2d40] rounded p-2.5 flex items-center justify-center text-[11px] text-[#374151]">
              {label} 점수 계산 불가
            </div>
          )
        }
        const macdUp = side.macd_hist >= side.macd_hist_prev
        return (
          <div key={key} className="border border-[#1e2d40] rounded p-2.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] font-bold" style={{ color }}>{label} 점수</span>
              {!pass && <span className="text-[9px] text-[#64748b]">1차 필터 미통과 · 참고용</span>}
            </div>
            <div className="font-mono font-bold" style={{ color }}>
              <span className="text-[18px]">{side.score}</span><span className="text-[10px] text-[#64748b]">/100</span>
            </div>
            <div className="text-[10px] text-[#64748b] mt-0.5 mb-1.5">
              RSI {side.rsi.toFixed(0)} · Vol×{side.volume_ratio.toFixed(1)} · MACD {macdUp ? '▲' : '▼'}
            </div>
            <div className="space-y-1">
              <ScoreBar label="수급"   value={side.components.volume}   max={40} color={color} />
              <ScoreBar label="모멘텀" value={side.components.momentum} max={30} color={color} />
              <ScoreBar label="RSI"    value={side.components.rsi}      max={30} color={color} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function HoldingRow({
  ticker, selected, onClick,
}: { ticker: string; selected: boolean; onClick: () => void }) {
  const names = useTickerNames()
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2 rounded border transition-colors font-bold text-sm ${
        selected ? 'border-[#f59e0b] bg-[#f59e0b]/10 text-[#f59e0b]' : 'border-[#1e2d40] text-[#e2e8f0] hover:bg-[#0a1525]'
      }`}
    >
      {displayTicker(ticker, names)}
    </button>
  )
}

export default function TradeSignalsPanel({ holdings = {} }: TradeSignalsPanelProps) {
  const [selected,     setSelected]     = useState<string | null>(null)
  const [search,       setSearch]       = useState('')
  const [sidebarOpen,  setSidebarOpen]  = useState(true)

  const holdTickers = Object.keys(holdings).filter(t => t !== 'CASH')

  // 키에 시장이 들어가야 한다 (§1.1). 요청 자체는 axios 인터셉터가 market 을
  // 붙여 주지만, **캐시는 키로만 갈린다** — 키가 같으면 한 시장의 스캔 결과가
  // 다른 시장에 그대로 나간다.
  //
  // 지금 사고가 안 나는 이유는 시장 전환이 전체 새로고침이라 캐시가 통째로
  // 비워지기 때문이다. 그건 **다른 컴포넌트의 동작에 기댄 안전**이고, 실제로
  // 새로고침 없이 setMarket 을 부르는 경로가 둘 있다 (AuthContext 의
  // syncProfile, SetupWizard 의 시장 선택). 지금은 그 전환 시점에 이 패널이
  // 마운트돼 있지 않아 닿지 않을 뿐이다 — 마운트 순서는 아무도 고정하고 있지
  // 않다.
  const scanQ = useQuery({
    queryKey: ['timing-signal-scan', getMarket()],
    queryFn:  () => getSignalScan(10),
    staleTime: 1800_000,
  })

  function submitSearch() {
    const t = search.trim().toUpperCase()
    if (t) setSelected(t)
    setSearch('')
  }

  // 모바일에서는 목록과 차트를 좌우로 나눌 폭이 없다.
  // 위아래로 쌓아 차트가 화면 폭을 온전히 쓰게 한다.
  return (
    <div className="md:h-full flex flex-col md:flex-row relative">
      {/* sidebar toggle tab */}
      <button
        onClick={() => setSidebarOpen(v => !v)}
        title={sidebarOpen ? '패널 닫기' : '패널 열기'}
        className="hidden md:flex absolute z-20 top-1/2 -translate-y-1/2 items-center justify-center w-5 h-12 bg-[#1a2035] border border-[#2d3f56] rounded-r text-[#64748b] hover:text-[#e2e8f0] transition-colors"
        style={{ left: sidebarOpen ? '300px' : '0px' }}
      >
        {sidebarOpen ? <ChevronLeft className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
      </button>

      {/* left list panel */}
      {sidebarOpen && (
        <div className="w-full md:w-[300px] flex-shrink-0 max-h-[42vh] md:max-h-none overflow-y-auto border-b md:border-b-0 md:border-r border-[#1e2d40] p-3 space-y-3">
          {/* search */}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#374151]" />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && submitSearch()}
              placeholder="티커 검색…"
              className="w-full bg-[#060b14] border border-[#1e2d40] rounded pl-8 pr-3 py-2 text-sm text-[#e2e8f0] placeholder:text-[#374151] focus:outline-none focus:border-[#10b981]"
            />
          </div>

          {/* holdings signals */}
          {holdTickers.length > 0 && (
            <div>
              <div className="text-[11px] font-bold tracking-widest text-[#f59e0b] mb-1.5">
                보유종목 <span className="text-[#374151]">{holdTickers.length}</span>
              </div>
              <div className="space-y-1.5">
                {holdTickers.map(t => (
                  <HoldingRow key={t} ticker={t} selected={selected === t} onClick={() => setSelected(t)} />
                ))}
              </div>
            </div>
          )}

          {/* S&P500 scan results */}
          {scanQ.isLoading && <div className="text-sm text-[#64748b]">스캔 중… (최초 1회)</div>}
          {/* 서버가 왜 안 되는지 말해 주면 그대로 보여 준다.
              "불러올 수 없습니다" 로 뭉개면 **"데이터가 아직 없다" 와 "신호가
              없다" 가 같은 문장**이 된다 — 둘은 다른 사실이고, 사용자가 기다려야
              하는지 아닌지가 갈린다. 실제로 서버는 503 에 "종목 목록을 준비하는
              중입니다. 잠시 후 다시 시도하세요." 를 담아 보내는데 화면이 그걸
              버리고 있었다. */}
          {scanQ.isError && (
            <div className="text-sm text-[#ef4444]">
              {scanErrorMessage(scanQ.error) ?? '스캔 데이터를 불러올 수 없습니다.'}
            </div>
          )}

          {scanQ.data && (
            <>
              <div>
                <div className="text-[11px] font-bold tracking-widest mb-1.5 flex items-center gap-1.5 flex-wrap" style={{ color: COLOR_UP }}>
                  매수 신호 <span className="text-[#374151]">{scanQ.data.long_picks.length}</span>
                  {!!scanQ.data.long_filter_level && (
                    <span className="text-[9px] font-normal text-[#f59e0b] normal-case tracking-normal"
                      title="원래 기준으로 top 10이 안 채워져 조건을 완화했습니다.">
                      ⚠ 완화됨: {scanQ.data.long_filter_note}
                    </span>
                  )}
                </div>
                <div className="space-y-1.5">
                  {scanQ.data.long_picks.map(p => (
                    <PickRow key={p.ticker} p={p} kind="long" selected={selected === p.ticker} onClick={() => setSelected(p.ticker)} />
                  ))}
                  {scanQ.data.long_picks.length === 0 && (
                    <div className="text-[11px] text-[#64748b]">1차 필터를 통과한 매수 후보가 없습니다.</div>
                  )}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-bold tracking-widest mb-1.5 flex items-center gap-1.5 flex-wrap" style={{ color: COLOR_DOWN }}>
                  매도 신호 <span className="text-[#374151]">{scanQ.data.short_picks.length}</span>
                  {!!scanQ.data.short_filter_level && (
                    <span className="text-[9px] font-normal text-[#f59e0b] normal-case tracking-normal"
                      title="원래 기준으로 top 10이 안 채워져 조건을 완화했습니다.">
                      ⚠ 완화됨: {scanQ.data.short_filter_note}
                    </span>
                  )}
                </div>
                <div className="space-y-1.5">
                  {scanQ.data.short_picks.map(p => (
                    <PickRow key={p.ticker} p={p} kind="short" selected={selected === p.ticker} onClick={() => setSelected(p.ticker)} />
                  ))}
                  {scanQ.data.short_picks.length === 0 && (
                    <div className="text-[11px] text-[#64748b]">1차 필터를 통과한 매도 후보가 없습니다.</div>
                  )}
                </div>
              </div>
              <div className="text-[10px] text-[#374151] pt-1">
                S&P500 {scanQ.data.scanned}개 종목 · SMA 1차 필터 + MACD/RSI 스코어링
                {scanQ.data.as_of ? ` · ${scanQ.data.as_of} 기준` : ''}
              </div>
            </>
          )}
        </div>
      )}

      {/* right chart panel */}
      <div className="flex-1 md:overflow-y-auto p-2 md:p-4 min-w-0">
        {!selected ? (
          <div className="text-sm text-[#64748b] flex items-center justify-center h-full">
            왼쪽에서 티커를 선택하거나 검색하세요.
          </div>
        ) : (
          <>
            <div className="text-lg font-mono font-bold text-[#e2e8f0] mb-3">{selected}</div>
            <TickerScoreCard key={`score-${selected}`} ticker={selected} />
            <BollingerChart key={selected} ticker={selected} height={460} />
          </>
        )}
      </div>
    </div>
  )
}
