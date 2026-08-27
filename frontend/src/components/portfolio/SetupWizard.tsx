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
import { useState } from 'react'
import { X, Plus, Trash2, Loader2, AlertTriangle, ArrowRight, Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import { setupPortfolio, getTickerPrice, searchTickers, type SetupHolding } from '@/api'

interface Props {
  open: boolean
  /** 기존 포트폴리오를 지우고 새로 만드는 경우 — 경고를 먼저 보여준다. */
  replace?: boolean
  onClose: () => void
  onDone: () => void
}

interface Row extends SetupHolding {
  /** 티커별 가격 자동조회 진행 표시 */
  loading?: boolean
  error?: string
}

const today = () => new Date().toISOString().slice(0, 10)

const emptyRow = (): Row => ({ ticker: '', q: 0, price: 0, date: today() })

/** 금액 표기 — 입력란 라벨에 통화를 명시해 원화로 오해하지 않게 한다. */
const usd = (n: number) => `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export default function SetupWizard({ open, replace = false, onClose, onDone }: Props) {
  // replace 면 경고 단계(0)부터, 아니면 종목 입력(1)부터 시작한다.
  const [step, setStep]   = useState(replace ? 0 : 1)
  const [rows, setRows]   = useState<Row[]>([emptyRow()])
  const [cash, setCash]   = useState('')
  const [busy, setBusy]   = useState(false)
  const [error, setError] = useState('')
  const [sug, setSug]     = useState<{ i: number; list: { ticker: string; name: string }[] }>({ i: -1, list: [] })

  if (!open) return null

  const invested = rows.reduce((s, r) => s + (r.q || 0) * (r.price || 0), 0)
  const cashNum  = parseFloat(cash) || 0
  const filled   = rows.filter(r => r.ticker.trim() && r.q > 0 && r.price > 0)

  const setRow = (i: number, patch: Partial<Row>) =>
    setRows(rs => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)))

  /** 티커를 확정하면 현재가를 받아 매수 단가의 기본값으로 채운다. */
  const fillPrice = async (i: number, ticker: string) => {
    const t = ticker.trim().toUpperCase()
    if (!t) return
    setRow(i, { ticker: t, loading: true, error: '' })
    try {
      const r = await getTickerPrice(t)
      // 이미 사용자가 단가를 적었으면 덮어쓰지 않는다.
      setRows(rs => rs.map((row, k) =>
        k === i ? { ...row, loading: false, price: row.price || r.price } : row))
    } catch {
      setRow(i, { loading: false, error: '확인할 수 없는 종목입니다' })
    }
  }

  const onTickerInput = async (i: number, v: string) => {
    const t = v.toUpperCase()
    setRow(i, { ticker: t, error: '' })
    if (t.length < 1) { setSug({ i: -1, list: [] }); return }
    try {
      setSug({ i, list: await searchTickers(t) })
    } catch { setSug({ i: -1, list: [] }) }
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
              {step === 0 ? '기존 정보 삭제 확인'
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
                      : now ? 'bg-[#3b82f6] text-white'
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
                <span className="text-[#e2e8f0]"> 매수 단가(USD)</span>와 함께 입력해 주세요.
                <br />없으면 비워 두고 다음으로 넘어가도 됩니다.
              </p>

              {/* 좁은 화면에서는 각 입력칸에 자리표시자가 라벨 노릇을 하므로 이 줄은 숨긴다 */}
              <div className="hidden sm:grid grid-cols-[1.4fr_0.8fr_1fr_1.1fr_auto] gap-2 px-1 text-[10px] font-bold uppercase tracking-wider text-[#4a5568]">
                <span>종목</span><span>수량</span><span>매수 단가 (USD)</span><span>매수일</span><span />
              </div>

              {rows.map((r, i) => (
                <div key={i} className="relative">
                  {/* 모바일: 5칸을 한 줄에 넣으면 칸마다 60px 남짓이라 숫자가 안 보인다.
                      두 줄로 접어 각 칸이 읽히는 폭을 갖게 한다 — 좌우로 밀 필요가 없다. */}
                  <div className="grid grid-cols-[1.3fr_0.7fr_auto] sm:grid-cols-[1.4fr_0.8fr_1fr_1.1fr_auto] gap-2">
                    <input
                      value={r.ticker}
                      onChange={e => onTickerInput(i, e.target.value)}
                      onBlur={() => { setTimeout(() => setSug({ i: -1, list: [] }), 150); fillPrice(i, r.ticker) }}
                      placeholder="AAPL"
                      className="rounded-lg border border-[#1e2d40] bg-[#0d1526] px-3 py-2 font-mono text-sm text-[#e2e8f0] outline-none focus:border-[#3b82f6]"
                    />
                    <input
                      type="number" min="0" step="any" value={r.q || ''}
                      onChange={e => setRow(i, { q: parseFloat(e.target.value) || 0 })}
                      placeholder="0"
                      className="rounded-lg border border-[#1e2d40] bg-[#0d1526] px-3 py-2 text-sm text-[#e2e8f0] outline-none focus:border-[#3b82f6]"
                    />
                    {/* 모바일에서는 이 두 칸이 둘째 줄을 통째로 쓴다 (col-span-3) */}
                    <div className="relative col-span-2 sm:col-span-1">
                      <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-[#4a5568]">$</span>
                      <input
                        type="number" min="0" step="any" value={r.price || ''}
                        onChange={e => setRow(i, { price: parseFloat(e.target.value) || 0 })}
                        placeholder="0.00"
                        className="w-full rounded-lg border border-[#1e2d40] bg-[#0d1526] py-2 pl-6 pr-2 text-sm text-[#e2e8f0] outline-none focus:border-[#3b82f6]"
                      />
                      {r.loading && (
                        <Loader2 size={13} className="absolute right-2 top-1/2 -translate-y-1/2 animate-spin text-[#3b82f6]" />
                      )}
                    </div>
                    <input
                      type="date" value={r.date} max={today()}
                      onChange={e => setRow(i, { date: e.target.value })}
                      className="col-span-1 rounded-lg border border-[#1e2d40] bg-[#0d1526] px-2 py-2 text-sm text-[#e2e8f0] outline-none focus:border-[#3b82f6]"
                    />
                    <button
                      onClick={() => setRows(rs => (rs.length > 1 ? rs.filter((_, k) => k !== i) : [emptyRow()]))}
                      aria-label="행 삭제"
                      className="rounded-lg px-2 text-[#4a5568] transition hover:bg-[#0d1526] hover:text-[#ef4444]">
                      <Trash2 size={15} />
                    </button>
                  </div>

                  {r.error && <p className="mt-1 pl-1 text-[11px] text-[#ef4444]">{r.error}</p>}

                  {sug.i === i && sug.list.length > 0 && (
                    <div className="absolute left-0 top-full z-20 mt-1 max-h-44 w-72 overflow-y-auto rounded-lg border border-[#1e2d40] bg-[#0b1220] shadow-xl">
                      {sug.list.map(s => (
                        <button key={s.ticker}
                          onMouseDown={() => { setSug({ i: -1, list: [] }); fillPrice(i, s.ticker) }}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] transition hover:bg-[#1e2d40]">
                          <span className="font-mono font-bold text-[#e2e8f0]">{s.ticker}</span>
                          <span className="truncate text-[#94a3b8]">{s.name}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}

              <button onClick={() => setRows(rs => [...rs, emptyRow()])}
                      className="flex items-center gap-1.5 rounded-lg border border-dashed border-[#1e2d40] px-3 py-2 text-xs text-[#7d8ca3] transition hover:border-[#3b82f6]/50 hover:text-[#cbd5e1]">
                <Plus size={13} /> 종목 추가
              </button>

              <div className="flex items-center justify-between rounded-lg border border-[#1e2d40] bg-[#0d1526] px-4 py-2.5 text-sm">
                <span className="text-[#7d8ca3]">매수금 합계 (USD)</span>
                <span className="font-mono font-bold text-[#e2e8f0]">{usd(invested)}</span>
              </div>
            </div>
          )}

          {/* ── 2단계: 현금 ── */}
          {step === 2 && (
            <div className="space-y-4">
              <p className="text-xs leading-relaxed text-[#7d8ca3]">
                현재 계좌에 남아 있는 <span className="text-[#e2e8f0]">현금 잔고(USD)</span>를 입력해 주세요.
              </p>

              <div>
                <label className="mb-1.5 block text-[10px] font-bold uppercase tracking-wider text-[#4a5568]">
                  현금 잔고 (USD)
                </label>
                <div className="relative">
                  <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-[#4a5568]">$</span>
                  <input
                    type="number" min="0" step="any" value={cash} autoFocus
                    onChange={e => setCash(e.target.value)}
                    placeholder="0.00"
                    className="w-full rounded-lg border border-[#1e2d40] bg-[#0d1526] py-3 pl-7 pr-3 font-mono text-lg text-[#e2e8f0] outline-none focus:border-[#3b82f6]"
                  />
                </div>
              </div>

              {/* 등록 후 장부가 어떻게 기록되는지 미리 보여준다 —
                  "왜 입금 기록이 생기지?" 라는 의문을 남기지 않기 위해서다. */}
              <div className="space-y-1.5 rounded-lg border border-[#1e2d40] bg-[#0d1526] px-4 py-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-[#7d8ca3]">보유 종목 매수금</span>
                  <span className="font-mono text-[#cbd5e1]">{usd(invested)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#7d8ca3]">현금 잔고</span>
                  <span className="font-mono text-[#cbd5e1]">{usd(cashNum)}</span>
                </div>
                <div className="mt-1 flex justify-between border-t border-[#1e2d40] pt-2">
                  <span className="font-medium text-[#e2e8f0]">최초 입금액으로 기록</span>
                  <span className="font-mono font-bold text-[#3b82f6]">{usd(invested + cashNum)}</span>
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
                className="flex items-center gap-1.5 rounded-lg bg-[#3b82f6] px-5 py-2 text-sm font-semibold text-white transition hover:bg-[#2f6fe0]">
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
