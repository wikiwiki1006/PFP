import { useCallback, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, ChevronLeft, ChevronRight, X } from 'lucide-react'
import { useIsMobile } from '@/lib/useIsMobile'
import { getSignalScan, getSignalScore } from '@/api'
import { getMarket } from '@/lib/market'
import BollingerChart from './BollingerChart'
import { COLOR_UP, COLOR_DOWN } from './colors'
import type { SignalScanPick, SignalScanResult } from '@/types'
import TickerLabel from '@/components/TickerLabel'
import { useTickerNames, displayTicker } from '@/lib/useTickerNames'
import { tickerByExactName } from '@/lib/suggestions'

/** 서버가 준 실패 사유. 없으면 null (호출부가 기본 문구를 쓴다).
 *
 *  FastAPI 는 HTTPException 의 detail 을 `{ "detail": "..." }` 로 보낸다.
 *  그 문장이 "무엇을 기다려야 하는지" 를 담고 있으므로 버리지 않는다. */
function scanErrorMessage(err: unknown): string | null {
  const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof d === 'string' && d.trim() ? d : null
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
        {/* 이 숫자는 상세 화면의 같은 방향 점수와 **같은 값**이다 — 둘 다 스캔이 같은
            프레임에서 계산한다 (routers/signals.py signal_score, basis="scan").
            예전에는 상세가 다른 날짜의 데이터로 다시 계산해 숫자가 자주 달랐다. */}
        <span className="font-mono font-bold tabular-nums" style={{ color }}>
          <span className="text-[9px] font-sans font-normal text-[#64748b] mr-1">{kind === 'long' ? '매수' : '매도'}</span>
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

/** 매수 또는 매도 목록 한 칸. 렌더 안에서 컴포넌트를 정의하면 렌더마다 새 타입이
    되어 React 가 목록 전체를 다시 마운트한다 — 모듈 수준에 둔다. */
function SideList({ title, color, picks, level, note, kind, selected, onSelect }: {
  title: string; color: string; picks: SignalScanPick[]
  level?: number; note?: string; kind: 'long' | 'short'
  selected: string | null; onSelect: (t: string) => void
}) {
  return (
    <div>
      <div className="text-[11px] font-bold tracking-widest mb-1.5 flex items-center gap-1.5 flex-wrap" style={{ color }}>
        {title} <span className="text-[#374151]">{picks.length}</span>
        {!!level && (
          <span className="text-[9px] font-normal text-[#f59e0b] normal-case tracking-normal"
            title="원래 기준으로 top 10이 안 채워져 조건을 완화했습니다.">
            ⚠ 완화됨: {note}
          </span>
        )}
      </div>
      <div className="space-y-1.5">
        {picks.map(p => (
          <PickRow key={p.ticker} p={p} kind={kind}
            selected={selected === p.ticker} onClick={() => onSelect(p.ticker)} />
        ))}
        {picks.length === 0 && (
          <div className="text-[11px] text-[#64748b]">1차 필터를 통과한 {kind === 'long' ? '매수' : '매도'} 후보가 없습니다.</div>
        )}
      </div>
    </div>
  )
}

/** 종목 하나의 매수/매도 참고 점수 — Signal Scan 상위 10개 리스트에 없어도(순위 밖,
    검색한 임의 티커 등) 볼 수 있게 한다. 상세 차트(BollingerChart) 위에 표시.

    `listAsOf` 는 왼쪽 목록의 기준일이다. 상세가 스캔 스냅샷(basis=scan)인데 날짜가
    목록과 다르면 — 목록을 받은 뒤 스캔이 새로 돌았다 — `onStale` 로 목록을 다시
    받는다. 그러지 않으면 같은 종목이 목록과 상세에서 다른 날의 점수로 나온다. */
function TickerScoreCard({ ticker, listAsOf, onStale }: {
  ticker: string; listAsOf?: string | null; onStale?: () => void
}) {
  const q = useQuery({
    // 시장을 키에 넣는다 (§1.1) — 같은 문자열 티커가 시장마다 다른 응답일 수 있다.
    queryKey: ['signal-score', getMarket(), ticker],
    queryFn:  () => getSignalScore(ticker),
    staleTime: 300_000,
    retry: false,
  })
  const scoreAsOf = q.data?.as_of ?? null
  const fromScan  = q.data?.basis === 'scan'
  const { refetch: refetchScore } = q
  useEffect(() => {
    if (!fromScan || !listAsOf || !scoreAsOf || scoreAsOf === listAsOf) return
    // YYYY-MM-DD 라 문자열 비교가 곧 날짜 비교다. 낡은 쪽을 다시 받는다.
    if (scoreAsOf > listAsOf) onStale?.()
    else void refetchScore()
  }, [fromScan, listAsOf, scoreAsOf, onStale, refetchScore])

  if (q.isLoading) return <div className="text-xs text-[#64748b] mb-3">매매신호 점수 계산 중…</div>
  if (q.isError || !q.data) return null

  const { long, long_filter_pass, short, short_filter_pass, as_of, basis } = q.data
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
    <div className="mb-3">
    {/* 점수의 기준일. 스캔 유니버스 종목은 왼쪽 목록과 같은 스냅샷(basis=scan)이라
        숫자가 같고, 유니버스 밖 종목은 같은 공식으로 따로 계산한다. */}
    <div className="text-[10px] text-[#475569] mb-1.5">
      {as_of ? `${as_of} 종가 기준` : '기준일 정보 없음'}
      {basis === 'on_demand' && ' · 스캔 대상 밖 종목이라 따로 계산'}
      {basis === 'scan_unavailable' && ' · 스캔 결과를 아직 만들 수 없어 따로 계산'}
    </div>
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
    </div>
  )
}

export default function TradeSignalsPanel() {
  const names = useTickerNames()
  const [selected,     setSelected]     = useState<string | null>(null)
  const [search,       setSearch]       = useState('')
  const [sidebarOpen,  setSidebarOpen]  = useState(true)
  // 모바일: 종목을 고르면 오른쪽에서 상세 서랍이 나온다. 예전에는 목록 아래에 상세가
  // 쌓여 차트를 보려면 목록을 끝까지 내려야 했고, 다른 종목을 보려면 다시 올라와야 했다.
  const isMobile = useIsMobile()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const pick = useCallback((t: string) => { setSelected(t); setDrawerOpen(true) }, [])

  // 보유 종목 목록을 없앴다. 예전에는 왼쪽에 '보유종목' 칸이 있어 내 종목만
  // 따로 신호를 보여 줬는데, 그러면 같은 화면이 사용자마다 다른 목록을 낸다.
  // 스캔 결과는 이제 누구에게나 같다 — 검색으로는 어떤 종목이든 볼 수 있다.

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
  // 서버가 스캔 대상 이름을 함께 준다 (routers/signals.py `_UNIVERSE_LABEL`).
  // 공용 타입(types/index.ts)에는 아직 없는 필드라 여기서만 넓혀 읽는다.
  const universeLabel = (scanQ.data as (SignalScanResult & { universe_label?: string | null }) | undefined)
    ?.universe_label ?? null
  // 상세 점수가 더 새 스냅샷이면 목록을 다시 받는다 (TickerScoreCard 참고).
  const { refetch: refetchScanQuery } = scanQ
  const refetchScan = useCallback(() => { void refetchScanQuery() }, [refetchScanQuery])

  function submitSearch() {
    // 한국은 이름으로 찾는다 — 코드를 화면 어디에도 안 보여 주므로 사용자가 코드를
    // 알 방법이 없다. 이름이 사전과 정확히 같으면 그 티커, 아니면 입력을 코드로 읽는다.
    const typed = search.trim()
    const t = tickerByExactName(typed, names) ?? typed.toUpperCase()
    if (t) pick(t)
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
        <div className="w-full md:w-[300px] flex-shrink-0 md:overflow-y-auto border-b md:border-b-0 md:border-r border-[#1e2d40] p-3 space-y-3">
          {/* search */}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#374151]" />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && !e.nativeEvent.isComposing && submitSearch()}
              placeholder={getMarket() === 'KR' ? '종목명 검색…' : '티커 검색…'}
              className="w-full bg-[#060b14] border border-[#1e2d40] rounded pl-8 pr-3 py-2 text-sm text-[#e2e8f0] placeholder:text-[#374151] focus:outline-none focus:border-[#10b981]"
            />
          </div>

          {/* 시장별 스캔 결과 (미국 S&P500 · 한국 KOSPI200·KOSDAQ150) */}
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

          {scanQ.data && (() => {
            const d = scanQ.data
            return (
              <>
                <SideList title="매수 신호" color={COLOR_UP} kind="long" picks={d.long_picks}
                  level={d.long_filter_level} note={d.long_filter_note}
                  selected={selected} onSelect={pick} />
                <SideList title="매도 신호" color={COLOR_DOWN} kind="short" picks={d.short_picks}
                  level={d.short_filter_level} note={d.short_filter_note}
                  selected={selected} onSelect={pick} />
                {/* 스캔 대상 이름은 서버가 유니버스를 고르는 자리에서 같이 준다
                    (`universe_label`). 화면이 "S&P500" 을 박아 두면 한국 화면이 미국
                    유니버스를 스캔했다고 말한다(§1.1). 이름이 없으면 지어내지 않는다. */}
                <div className="text-[10px] text-[#374151] pt-1">
                  {universeLabel ? `${universeLabel} ` : ''}
                  {d.scanned}개 종목 · SMA 1차 필터 + MACD/RSI 스코어링
                  {d.as_of ? ` · ${d.as_of} 종가 기준` : ''}
                </div>
                <div className="text-[10px] text-[#475569] leading-relaxed border-t border-[#1e2d40] pt-2 mt-1">
                  기술적 지표로 계산한 참고 정보입니다. 특정 종목의 매매를 권유하지 않으며,
                  투자 결과에 대한 책임은 투자자 본인에게 있습니다.
                </div>
              </>
            )
          })()}
        </div>
      )}

      {/* right chart panel — 데스크톱 */}
      {!isMobile && (
      <div className="flex-1 md:overflow-y-auto p-2 md:p-4 min-w-0">
        {!selected ? (
          <div className="text-sm text-[#64748b] flex items-center justify-center h-full">
            왼쪽에서 티커를 선택하거나 검색하세요.
          </div>
        ) : (
          <>
            {/* 한국은 종목명 (사전에 없으면 티커). 이름에는 고정폭을 쓰지 않는다. */}
            <div className={`text-lg font-bold text-[#e2e8f0] mb-3${names[selected] ? '' : ' font-mono'}`}>
              {displayTicker(selected, names)}
            </div>
            <TickerScoreCard key={`score-${selected}`} ticker={selected}
              listAsOf={scanQ.data?.as_of ?? null} onStale={refetchScan} />
            <BollingerChart key={selected} ticker={selected} height={460} />
          </>
        )}
      </div>
      )}

      {/* 모바일 상세 서랍 — 오른쪽에서 밀려 나온다. 바깥(어두운 막)이나 닫기를 누르면 접힌다. */}
      {isMobile && selected && (
        <>
          <div
            onClick={() => setDrawerOpen(false)}
            className={`fixed inset-0 z-[60] bg-black/50 transition-opacity ${drawerOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
          />
          <div
            role="dialog" aria-modal="true" aria-label={`${displayTicker(selected, names)} 상세`}
            className={`fixed top-0 bottom-0 right-0 z-[61] w-[94vw] max-w-[520px] bg-[#0b0f1a] border-l border-[#1e2d40] shadow-2xl flex flex-col transition-transform duration-200 ${drawerOpen ? 'translate-x-0' : 'translate-x-full'}`}
          >
            <div className="flex-shrink-0 flex items-center gap-2 px-3 py-2.5 border-b border-[#1e2d40] bg-[#060b14] pt-[max(0.625rem,env(safe-area-inset-top))]">
              <button onClick={() => setDrawerOpen(false)} aria-label="상세 닫기"
                className="min-w-[44px] min-h-[44px] -ml-2 flex items-center justify-center text-[#94a3b8] active:text-[#e2e8f0]">
                <ChevronRight className="w-5 h-5" />
              </button>
              <div className={`flex-1 min-w-0 truncate text-base font-bold text-[#e2e8f0]${names[selected] ? '' : ' font-mono'}`}>
                {displayTicker(selected, names)}
              </div>
              <button onClick={() => setDrawerOpen(false)} aria-label="닫기"
                className="min-w-[44px] min-h-[44px] flex items-center justify-center text-[#94a3b8] active:text-[#e2e8f0]">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto overscroll-contain p-2 pb-[max(1rem,env(safe-area-inset-bottom))]">
              {drawerOpen && (
                <>
                  <TickerScoreCard key={`score-${selected}`} ticker={selected}
                    listAsOf={scanQ.data?.as_of ?? null} onStale={refetchScan} />
                  <BollingerChart key={selected} ticker={selected} height={400} />
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
