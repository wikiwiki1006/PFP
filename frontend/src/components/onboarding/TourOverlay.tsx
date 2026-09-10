/**
 * components/onboarding/TourOverlay.tsx
 * ─────────────────────────────────────
 * 투어 말풍선. 가리킬 요소를 찾아 그 옆에 붙이고 화살표로 연결한다.
 *
 * 요소를 못 찾으면(아직 안 그려졌거나, 그 화면에 없거나) 화면 가운데에 띄운다 —
 * 안내가 사라지는 것보다 위치가 덜 정확한 편이 낫다.
 *
 * 대상 주변만 밝게 남기는 대신 화면 전체에 옅은 막을 덮고 대상에 테두리를 준다.
 * 구멍을 뚫는 방식(clip-path)은 스크롤·리사이즈마다 좌표를 다시 계산해야 하고
 * 조금만 어긋나도 엉뚱한 곳이 뚫려 더 어수선해진다.
 */
import { useEffect, useLayoutEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import { useTour } from '@/lib/TourContext'

interface Box { top: number; left: number; width: number; height: number }

const GAP = 12          // 대상과 말풍선 사이 간격
const CARD_W = 300      // 말풍선 폭 (좁은 화면에서는 아래에서 줄어든다)

export default function TourOverlay() {
  const { active, current, step, total, next, prev, skip } = useTour()
  const [box, setBox] = useState<Box | null>(null)
  const [vw, setVw] = useState(() => window.innerWidth)

  // 대상 위치를 따라간다. 스크롤·리사이즈·레이아웃 변화에 모두 반응해야 해서
  // 이벤트와 함께 짧은 주기 폴링도 둔다 — 차트가 늦게 그려지며 위치가 밀린다.
  useLayoutEffect(() => {
    if (!active || !current) { setBox(null); return }
    if (!current.anchor) { setBox(null); return }

    let raf = 0
    const measure = () => {
      const el = document.querySelector<HTMLElement>(`[data-tour="${current.anchor}"]`)
      if (!el) { setBox(null); return }
      const r = el.getBoundingClientRect()
      if (r.width === 0 && r.height === 0) { setBox(null); return }
      setBox({ top: r.top, left: r.left, width: r.width, height: r.height })
    }
    const loop = () => { measure(); raf = window.setTimeout(loop, 300) }

    // 대상이 화면 밖이면 먼저 보이게 옮긴다.
    const el = document.querySelector<HTMLElement>(`[data-tour="${current.anchor}"]`)
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' })

    loop()
    const onResize = () => { setVw(window.innerWidth); measure() }
    window.addEventListener('resize', onResize)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.clearTimeout(raf)
      window.removeEventListener('resize', onResize)
      window.removeEventListener('scroll', measure, true)
    }
  }, [active, current])

  // 투어 중 Esc 로 건너뛰기
  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') skip() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, skip])

  if (!active || !current) return null

  const narrow = vw < 640
  const cardW = narrow ? Math.min(vw - 24, CARD_W) : CARD_W

  // 말풍선 위치 — 대상 아래에 두되, 아래 공간이 모자라면 위로 올린다.
  let cardStyle: React.CSSProperties
  let arrow: 'up' | 'down' | null = null
  if (box) {
    const below = window.innerHeight - (box.top + box.height)
    const putBelow = below > 190 || below > box.top
    const top = putBelow ? box.top + box.height + GAP : box.top - GAP
    let left = box.left + box.width / 2 - cardW / 2
    left = Math.max(12, Math.min(left, vw - cardW - 12))
    cardStyle = putBelow
      ? { top, left, width: cardW }
      : { top, left, width: cardW, transform: 'translateY(-100%)' }
    arrow = putBelow ? 'up' : 'down'
  } else {
    cardStyle = {
      top: '50%', left: '50%', width: cardW,
      transform: 'translate(-50%, -50%)',
    }
  }

  const isLast = step === total - 1

  return createPortal(
    <div className="fixed inset-0 z-[200]" role="dialog" aria-modal="true" aria-label="기능 안내">
      {/* 배경 막 — 클릭해도 넘어가지 않게 한다. 실수로 눌러 안내가 사라지면
          어디까지 봤는지 알 수 없다. 넘기려면 버튼을 쓰게 한다. */}
      <div className="absolute inset-0 bg-black/55" />

      {/* 가리키는 대상 테두리 */}
      {box && (
        <div
          className="absolute rounded-lg ring-2 ring-[#10b981] pointer-events-none transition-all duration-200"
          style={{
            top: box.top - 4, left: box.left - 4,
            width: box.width + 8, height: box.height + 8,
            boxShadow: '0 0 0 9999px rgba(0,0,0,0.55)',
          }}
        />
      )}

      {/* 말풍선 */}
      <div
        className="absolute rounded-xl border border-[#2d3f56] bg-[#0b1220] p-4 shadow-2xl"
        style={cardStyle}
      >
        {/* 화살표 */}
        {arrow && (
          <div
            className={
              'absolute left-1/2 -translate-x-1/2 h-3 w-3 rotate-45 border-[#2d3f56] bg-[#0b1220] ' +
              (arrow === 'up'
                ? '-top-[7px] border-l border-t'
                : '-bottom-[7px] border-b border-r')
            }
          />
        )}

        <div className="mb-1 flex items-start justify-between gap-2">
          <div className="text-[11px] font-bold tracking-wider text-[#10b981]">
            {step + 1} / {total}
          </div>
          <button onClick={skip} aria-label="안내 건너뛰기"
                  className="-mt-1 rounded p-1 text-[#64748b] transition hover:bg-[#111827] hover:text-[#cbd5e1]">
            <X size={15} />
          </button>
        </div>

        <h3 className="text-sm font-bold text-[#e2e8f0]">{current.title}</h3>
        <p className="mt-1.5 text-[12px] leading-relaxed text-[#94a3b8]">{current.body}</p>

        <div className="mt-4 flex items-center gap-2">
          {step > 0 && (
            <button onClick={prev}
                    className="flex items-center gap-1 rounded-lg border border-[#2d3f56] px-3 py-2 text-xs text-[#94a3b8] transition hover:bg-[#111827]">
              <ChevronLeft size={14} /> 이전
            </button>
          )}
          <button onClick={skip}
                  className="rounded-lg px-3 py-2 text-xs text-[#64748b] transition hover:text-[#94a3b8]">
            건너뛰기
          </button>
          <button onClick={next}
                  className="ml-auto flex items-center gap-1 rounded-lg bg-[#10b981] px-4 py-2 text-xs font-bold text-white transition hover:bg-[#059669]">
            {isLast ? '등록 시작' : '다음'}
            {!isLast && <ChevronRight size={14} />}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
