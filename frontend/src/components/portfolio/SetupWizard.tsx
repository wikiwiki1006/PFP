/**
 * components/portfolio/SetupWizard.tsx
 * ────────────────────────────────────
 * 포트폴리오 최초 등록 마법사.
 *
 * 사용자는 "지금 이만큼 갖고 있다"를 입력하지만, 내부 장부는 거래의 누적으로
 * 잔고를 만든다. 그 간극은 서버(/api/portfolio/setup)가 메운다 — 최초 매수일에
 * (매수금 합계 + 현금)을 입금한 것으로 기록해, 매수 시점마다 현금이 모자라
 * 잔고가 음수가 되는 일을 막는다.
 *
 * 그래서 이 화면은 순서를 강제한다: 종목을 먼저 받고, 그 다음 현금을 받는다.
 * 종목 입력 단계에서는 현금이 아직 0이지만 매수를 허용해야 하므로, 일반 매매
 * 경로(POST /trades)를 쓰지 않고 등록 전용 경로로 한 번에 보낸다.
 */
import { useRef, useState } from 'react'
import { X, Plus, Trash2, Loader2, AlertTriangle, ArrowRight, Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import { marketSymbol, MARKETS, getMarket, setMarket, moneyInputProps } from '@/lib/market'
import { useMarket } from '@/lib/useMarket'
import { setupPortfolio, getTickerPrice, searchTickers, type SetupHolding } from '@/api'
import { formatPrice } from '@/lib/market'
import SuggestionList from '@/components/SuggestionList'
import { pickOnEnter, moveHighlight, selectionLabel, type Suggestion } from '@/lib/suggestions'

interface Props {
  open: boolean
  /** 기존 포트폴리오를 지우고 새로 만드는 경우 — 경고를 먼저 보여준다. */
  replace?: boolean
  onClose: () => void
  onDone: () => void
}

interface Row extends SetupHolding {
  /** 입력칸에 보이는 글자. `ticker` 는 서버로 보내는 값이다 — 목록에서 한국
      종목을 고르면 칸에는 이름이, ticker 에는 '005930.KS' 가 들어간다. */
  label?: string
  /** 티커별 가격 자동조회 진행 표시 */
  loading?: boolean
  error?: string
  /** 참고용 현재 시장가 — 매수 단가(price) 입력에 자동으로 채우지 않는다.
      실제 매수한 단가는 오늘 시세와 다른 경우가 대부분이라, 자동 채움은
      사용자가 못 알아채고 잘못된 평단가를 그대로 등록하게 만든다. */
  currentPrice?: number
}

const today = () => new Date().toISOString().slice(0, 10)

const emptyRow = (): Row => ({ ticker: '', q: 0, price: 0, date: today() })

/** 목록이 닫힌 행에 넘기는 빈 목록 — 렌더마다 새 배열을 만들지 않는다. */
const NO_SUGGESTIONS: Suggestion[] = []

// 시장 선택 단계. 등록 절차의 일부가 아니라 그 앞에 오는 선택이라 음수로 둔다 —
// 이렇게 하면 진행 표시(1·2단계)와 하단 버튼의 `step > 0` 조건을 손대지 않아도 된다.
const MARKET_STEP = -1

/** 금액 표기 — 입력란 라벨에 통화를 명시해 원화로 오해하지 않게 한다. */
const money = (n: number) => formatPrice(n)

export default function SetupWizard({ open, replace = false, onClose, onDone }: Props) {
  // 통화 표기는 시장을 따른다. 한국 화면에 (USD) 라고 적혀 있으면
  // 사용자가 원화를 달러로 잘못 입력한다.
  const market = useMarket()
  const curLabel = market === 'KR' ? 'KRW' : 'USD'
  // replace(새로 등록)면 경고 단계(0)부터.
  // 최초 등록이면 시장 선택(-1)부터 — 어느 시장에 담는지부터 정해야 한다.
  // 종목을 다 넣은 뒤에 시장을 바꾸면 그 입력이 전부 다른 시장 것이 된다.
  const [step, setStep]   = useState(replace ? 0 : MARKET_STEP)
  const [rows, setRows]   = useState<Row[]>([emptyRow()])
  const [cash, setCash]   = useState('')
  const [busy, setBusy]   = useState(false)
  const [error, setError] = useState('')
  const [sug, setSug]     = useState<{ i: number; list: Suggestion[] }>({ i: -1, list: [] })
  const [sugIdx, setSugIdx] = useState(-1)
  // 제안 목록을 붙일 입력칸 — 행마다 칸이 있어서, 지금 포커스된 칸을 기억한다.
  const activeInputRef = useRef<HTMLInputElement | null>(null)

  if (!open) return null

  const invested = rows.reduce((s, r) => s + (r.q || 0) * (r.price || 0), 0)
  const cashNum  = parseFloat(cash) || 0
  const filled   = rows.filter(r => r.ticker.trim() && r.q > 0 && r.price > 0)

  const setRow = (i: number, patch: Partial<Row>) =>
    setRows(rs => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)))

  /** 티커를 확정하면 현재가를 받아 '현재가' 참고란에 표시한다.
      매수 단가(price)는 사용자가 직접 입력해야 한다 — 자동으로 채우지 않는다. */
  const fillPrice = async (i: number, ticker: string, label?: string) => {
    const t = ticker.trim().toUpperCase()
    if (!t) return
    setRow(i, { ticker: t, ...(label != null ? { label } : {}), loading: true, error: '', currentPrice: undefined })
    try {
      const r = await getTickerPrice(t)
      setRow(i, { loading: false, currentPrice: r.price })
    } catch {
      setRow(i, { loading: false, error: '확인할 수 없는 종목입니다' })
    }
  }

  const onTickerInput = async (i: number, v: string) => {
    const t = v.toUpperCase()
    // 직접 친 글자는 그대로 티커 후보다(미국 티커를 끝까지 친 경우). 목록에서
    // 고르면 pickSuggestion 이 티커로 바꾸고 칸에는 이름을 남긴다.
    setRow(i, { ticker: t, label: t, error: '' })
    setSugIdx(-1)
    if (t.length < 1) { setSug({ i: -1, list: [] }); return }
    try {
      setSug({ i, list: await searchTickers(t) })
    } catch { setSug({ i: -1, list: [] }) }
  }

  const pickSuggestion = (i: number, s: Suggestion) => {
    setSug({ i: -1, list: [] })
    setSugIdx(-1)
    fillPrice(i, s.ticker, selectionLabel(market, s.ticker, s.name))
  }

  const onTickerKeyDown = (i: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    const listOpen = sug.i === i && sug.list.length > 0
    if (!listOpen) return
    // 한글 조합 중의 키는 IME 몫이다 — 받으면 확정용 Enter 가 선택으로도 처리될 수 있다.
    if (e.nativeEvent.isComposing) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setSugIdx(k => moveHighlight(k, e.key === 'ArrowDown' ? 1 : -1, sug.list.length))
    } else if (e.key === 'Enter') {
      // 목록이 보이면 Enter 는 목록에서 고른다 (lib/suggestions.ts). 예전에는
      // Enter 를 받지 않아 "삼성" 이 그대로 남고, 칸을 떠나면 "확인할 수 없는 종목".
      const pick = pickOnEnter(rows[i]?.label ?? rows[i]?.ticker ?? '', sug.list, sugIdx)
      if (pick) { e.preventDefault(); pickSuggestion(i, pick) }
    } else if (e.key === 'Escape') {
      setSug({ i: -1, list: [] }); setSugIdx(-1)
    }
  }

  const submit = async () => {
    setBusy(true); setError('')
    try {
      const payload: SetupHolding[] = filled.map(r => ({
        ticker: r.ticker.trim().toUpperCase(),
        q: Number(r.q), price: Number(r.price), date: r.date,
      }))
      await setupPortfolio(payload, cashNum, replace)
      onDone()
    } catch (e) {
      const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setError(typeof d === 'string' ? d : '등록에 실패했습니다.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
         role="dialog" aria-modal="true" aria-label="포트폴리오 등록">
      <div className="flex max-h-[88vh] w-full max-w-3xl flex-col rounded-2xl border border-[#1e2d40] bg-[#060b14] shadow-2xl">
        {/* 헤더 */}
        <div className="flex flex-shrink-0 items-center justify-between border-b border-[#1e2d40] px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-[#e2e8f0]">
              {replace ? '포트폴리오 새로 등록' : '포트폴리오 등록'}
            </h2>
            <p className="mt-0.5 text-xs text-[#4a5568]">
              {step === MARKET_STEP ? '시장 선택'
                : step === 0 ? '기존 정보 삭제 확인'
                : step === 1 ? '1단계 · 보유 종목'
                : '2단계 · 현금 잔고'}
            </p>
          </div>
          <button onClick={onClose} aria-label="닫기"
                  className="rounded-lg p-1.5 text-[#4a5568] transition hover:bg-[#0d1526] hover:text-[#cbd5e1]">
            <X size={18} />
          </button>
        </div>

        {/* 진행 표시 — 몇 단계 중 어디인지 보이지 않으면 사용자는 끝이 안 보인다고 느낀다.
            0단계(삭제 확인)는 등록 절차가 아니라 경고라 세지 않는다. */}
        {step > 0 && (
          <div className="flex flex-shrink-0 items-center gap-2 border-b border-[#1e2d40] px-6 py-3">
            {([[1, '보유 종목'], [2, '현금 잔고']] as const).map(([n, label]) => {
              const done = step > n
              const now  = step === n
              return (
                <div key={n} className="flex flex-1 items-center gap-2">
                  <div className={cn(
                    'flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[11px] font-bold transition-colors',
                    done ? 'bg-[#10b981] text-white'
                      : now ? 'bg-[#10b981] text-white'
                      : 'border border-[#2d3f56] text-[#4a5568]',
                  )}>
                    {done ? <Check size={13} /> : n}
                  </div>
                  <span className={cn('text-[11px] font-medium whitespace-nowrap',
                    now ? 'text-[#e2e8f0]' : done ? 'text-[#10b981]' : 'text-[#4a5568]')}>
                    {label}
                  </span>
                  {n === 1 && (
                    <div className={cn('h-px flex-1 transition-colors',
                      step > 1 ? 'bg-[#10b981]' : 'bg-[#1e2d40]')} />
                  )}
                </div>
              )
            })}
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {/* ── 0단계: 덮어쓰기 경고 ── */}
          {/* ── 시장 선택 (최초 등록) ── */}
          {step === MARKET_STEP && (
            <div className="flex flex-col items-center py-4 text-center">
              <h3 className="text-base font-semibold text-[#e2e8f0]">
                어느 시장의 포트폴리오인가요?
              </h3>
              <p className="mt-2 max-w-md text-sm leading-relaxed text-[#7d8ca3]">
                미국과 한국은 <span className="text-[#e2e8f0]">완전히 분리된 포트폴리오</span>로
                관리됩니다. 나중에 상단 전환 버튼으로 다른 시장을 따로 등록할 수 있습니다.
              </p>
              <div className="mt-6 grid w-full max-w-md grid-cols-2 gap-3">
                {(['US', 'KR'] as const).map((m) => (
                  <button
                    key={m}
                    onClick={() => { setMarket(m); setStep(1) }}
                    className={cn(
                      'rounded-xl border-2 px-4 py-5 text-left transition',
                      'border-[#2d3f56] hover:border-[#10b981] hover:bg-[#10b981]/5',
                    )}
                  >
                    <div className="text-sm font-bold text-[#e2e8f0]">{MARKETS[m].label}</div>
                    <div className="mt-1 text-[11px] text-[#7d8ca3]">
                      {m === 'US' ? 'NYSE · NASDAQ · USD' : 'KOSPI · KOSDAQ · KRW'}
                    </div>
                  </button>
                ))}
              </div>
              <button onClick={onClose}
                      className="mt-5 rounded-lg border border-[#2d3f56] px-5 py-2 text-sm font-medium text-[#94a3b8] transition hover:bg-[#0d1526]">
                취소
              </button>
            </div>
          )}

          {step === 0 && (
            <div className="flex flex-col items-center py-6 text-center">
              <AlertTriangle size={30} className="mb-3 text-[#ef4444]" />
              <h3 className="text-base font-semibold text-[#e2e8f0]">
                기존 포트폴리오가 모두 삭제됩니다
              </h3>
              <p className="mt-3 max-w-md text-sm leading-relaxed text-[#7d8ca3]">
                지금까지 등록한 <span className="text-[#e2e8f0]">보유 종목과 거래 이력</span>이
                전부 지워지고 새 포트폴리오로 대체됩니다.
                <br />삭제된 기록은 되돌릴 수 없습니다.
              </p>
              <div className="mt-6 flex gap-2">
                <button onClick={onClose}
                        className="rounded-lg border border-[#2d3f56] px-5 py-2.5 text-sm font-medium text-[#94a3b8] transition hover:bg-[#0d1526]">
                  취소
                </button>
                <button onClick={() => setStep(1)}
                        className="rounded-lg bg-[#dc2626] px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-[#b91c1c]">
                  삭제하고 계속
                </button>
              </div>
            </div>
          )}

          {/* ── 1단계: 보유 종목 ── */}
          {step === 1 && (
            <div className="space-y-3">
              <p className="text-xs leading-relaxed text-[#7d8ca3]">
                현재 보유 중인 종목을 <span className="text-[#e2e8f0]">매수일</span>과
                <span className="text-[#e2e8f0]"> 매수 단가({curLabel})</span>와 함께 입력해 주세요.
                <br />없으면 비워 두고 다음으로 넘어가도 됩니다.
              </p>

              {/* 좁은 화면에서는 각 입력칸에 자리표시자가 라벨 노릇을 하므로 이 줄은 숨긴다 */}
              <div className="hidden sm:grid grid-cols-[1.4fr_0.8fr_1fr_1.1fr_auto] gap-2 px-1 text-[10px] font-bold uppercase tracking-wider text-[#4a5568]">
                <span>종목</span><span>수량</span><span>매수 단가 ({curLabel})</span><span>매수일</span><span />
              </div>

              {rows.map((r, i) => (
                <div key={i} className="relative rounded-xl border border-[#1e2d40] bg-[#0b1220]/40 p-2.5 sm:border-0 sm:bg-transparent sm:p-0">
                  {/* 종목 한 건 = 카드 하나.
                      모바일은 2×2(종목·수량 / 단가·매수일)로 놓아 좌우로 밀 일이 없고,
                      종목을 추가하면 카드가 아래로 쌓여 세로 스크롤만으로 등록된다.
                      넓은 화면에서는 예전처럼 한 줄 5칸을 유지한다. */}
                  <div className="grid grid-cols-2 sm:grid-cols-[1.4fr_0.8fr_1fr_1.1fr_auto] gap-2">

                    {/* 종목 — 조회한 현재가를 칸 안 오른쪽에 함께 보여준다.
                        아래 별도 줄에 두면 카드가 한 줄 더 길어지고, 어느 종목의
                        가격인지도 눈으로 이어 붙여야 한다. */}
                    <div className="relative col-span-2 sm:col-span-1">
                      <input
                        value={r.label ?? r.ticker}
                        onChange={e => onTickerInput(i, e.target.value)}
                        onFocus={e => { activeInputRef.current = e.currentTarget }}
                        onKeyDown={e => onTickerKeyDown(i, e)}
                        onBlur={() => { setTimeout(() => { setSug({ i: -1, list: [] }); setSugIdx(-1) }, 150); fillPrice(i, r.ticker) }}
                        placeholder={MARKETS[getMarket()].tickerExample}
                        autoComplete="off"
                        className={cn(
                          'w-full rounded-lg border border-[#1e2d40] bg-[#0d1526] py-2 pl-3 pr-20 text-sm text-[#e2e8f0] outline-none focus:border-[#10b981]',
                          // 이름(한글)에는 고정폭을 쓰지 않는다 — 글자 사이가 벌어진다.
                          market !== 'KR' && 'font-mono',
                        )}
                      />
                      <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 flex items-center gap-1">
                        {r.loading && <Loader2 size={12} className="animate-spin text-[#10b981]" />}
                        {!r.loading && !r.error && r.currentPrice != null && (
                          <span className="font-mono text-[11px] text-[#64748b]">{money(r.currentPrice)}</span>
                        )}
                      </span>
                    </div>

                    <input
                      type="number" min="0" step="any" value={r.q || ''}
                      onChange={e => setRow(i, { q: parseFloat(e.target.value) || 0 })}
                      placeholder="수량"
                      className="rounded-lg border border-[#1e2d40] bg-[#0d1526] px-3 py-2 text-sm text-[#e2e8f0] outline-none focus:border-[#10b981]"
                    />

                    <div className="relative">
                      <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-[#4a5568]">{marketSymbol()}</span>
                      <input
                        {...moneyInputProps(r.price || '', price => setRow(i, { price }))}
                        placeholder="매수 단가"
                        className="w-full rounded-lg border border-[#1e2d40] bg-[#0d1526] py-2 pl-6 pr-2 text-sm text-[#e2e8f0] outline-none focus:border-[#10b981]"
                      />
                    </div>

                    <input
                      type="date" value={r.date} max={today()}
                      onChange={e => setRow(i, { date: e.target.value })}
                      className="rounded-lg border border-[#1e2d40] bg-[#0d1526] px-2 py-2 text-sm text-[#e2e8f0] outline-none focus:border-[#10b981]"
                    />

                    <button
                      onClick={() => setRows(rs => (rs.length > 1 ? rs.filter((_, k) => k !== i) : [emptyRow()]))}
                      aria-label="행 삭제"
                      className="col-span-2 sm:col-span-1 flex items-center justify-center gap-1 rounded-lg border border-[#1e2d40] py-1.5 text-[11px] text-[#4a5568] transition hover:bg-[#0d1526] hover:text-[#ef4444] sm:border-0 sm:py-0 sm:text-0">
                      <Trash2 size={14} />
                      <span className="sm:hidden">이 종목 삭제</span>
                    </button>
                  </div>

                  {r.error && <p className="mt-1 pl-1 text-[11px] text-[#ef4444]">{r.error}</p>}

                  {/* 목록은 body 로 띄운다. 예전에는 이 카드 안에 absolute 로 붙여서
                      스크롤 영역에 갇혔고, 마지막 줄이 하단 '취소' 버튼 밑에 깔려
                      눌리지 않았다 (실측 5줄 중 4줄만 눌림). 한국은 이름만 보인다. */}
                  <SuggestionList
                    anchorRef={activeInputRef}
                    open={sug.i === i}
                    items={sug.i === i ? sug.list : NO_SUGGESTIONS}
                    highlighted={sugIdx}
                    onPick={s => pickSuggestion(i, s)}
                    minWidth={288}
                  />
                </div>
              ))}

              <button onClick={() => setRows(rs => [...rs, emptyRow()])}
                      className="flex items-center gap-1.5 rounded-lg border border-dashed border-[#1e2d40] px-3 py-2 text-xs text-[#7d8ca3] transition hover:border-[#10b981]/50 hover:text-[#cbd5e1]">
                <Plus size={13} /> 종목 추가
              </button>

              <div className="flex items-center justify-between rounded-lg border border-[#1e2d40] bg-[#0d1526] px-4 py-2.5 text-sm">
                <span className="text-[#7d8ca3]">매수금 합계 ({curLabel})</span>
                <span className="font-mono font-bold text-[#e2e8f0]">{money(invested)}</span>
              </div>
            </div>
          )}

          {/* ── 2단계: 현금 ── */}
          {step === 2 && (
            <div className="space-y-4">
              <p className="text-xs leading-relaxed text-[#7d8ca3]">
                현재 계좌에 남아 있는 <span className="text-[#e2e8f0]">현금 잔고({curLabel})</span>를 입력해 주세요.
              </p>

              <div>
                <label className="mb-1.5 block text-[10px] font-bold uppercase tracking-wider text-[#4a5568]">
                  현금 잔고 ({curLabel})
                </label>
                <div className="relative">
                  <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-[#4a5568]">{marketSymbol()}</span>
                  <input
                    {...moneyInputProps(cash, n => setCash(String(n)))} autoFocus
                    placeholder="0.00"
                    className="w-full rounded-lg border border-[#1e2d40] bg-[#0d1526] py-3 pl-7 pr-3 font-mono text-lg text-[#e2e8f0] outline-none focus:border-[#10b981]"
                  />
                </div>
              </div>

              {/* 등록 후 장부가 어떻게 기록되는지 미리 보여준다 —
                  "왜 입금 기록이 생기지?" 라는 의문을 남기지 않기 위해서다. */}
              <div className="space-y-1.5 rounded-lg border border-[#1e2d40] bg-[#0d1526] px-4 py-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-[#7d8ca3]">보유 종목 매수금</span>
                  <span className="font-mono text-[#cbd5e1]">{money(invested)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#7d8ca3]">현금 잔고</span>
                  <span className="font-mono text-[#cbd5e1]">{money(cashNum)}</span>
                </div>
                <div className="mt-1 flex justify-between border-t border-[#1e2d40] pt-2">
                  <span className="font-medium text-[#e2e8f0]">최초 입금액으로 기록</span>
                  <span className="font-mono font-bold text-[#10b981]">{money(invested + cashNum)}</span>
                </div>
                <p className="pt-1 text-[11px] leading-relaxed text-[#4a5568]">
                  가장 이른 매수일에 이 금액을 입금한 것으로 기록해, 그래프와 수익률이
                  그 시점부터 계산됩니다.
                </p>
              </div>

              {error && (
                <div className="rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-300">
                  {error}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 하단 버튼 */}
        {step > 0 && (
          <div className="flex flex-shrink-0 items-center justify-between border-t border-[#1e2d40] px-6 py-4">
            <button
              onClick={() => (step === 1 ? onClose() : setStep(1))}
              className="rounded-lg border border-[#2d3f56] px-4 py-2 text-sm font-medium text-[#94a3b8] transition hover:bg-[#0d1526]">
              {step === 1 ? '취소' : '이전'}
            </button>

            {step === 1 ? (
              <button
                onClick={() => { setError(''); setStep(2) }}
                className="flex items-center gap-1.5 rounded-lg bg-[#10b981] px-5 py-2 text-sm font-semibold text-white transition hover:bg-[#059669]">
                다음 <ArrowRight size={15} />
              </button>
            ) : (
              <button
                onClick={submit}
                disabled={busy || (filled.length === 0 && cashNum <= 0)}
                className="flex items-center gap-1.5 rounded-lg bg-[#10b981] px-5 py-2 text-sm font-semibold text-white transition hover:bg-[#059669] disabled:opacity-50">
                {busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                포트폴리오 등록 완료
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
