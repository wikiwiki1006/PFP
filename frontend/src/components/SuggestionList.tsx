/**
 * components/SuggestionList.tsx
 * ─────────────────────────────
 * 종목 입력칸 아래에 뜨는 제안 목록. **`document.body` 에 포털로 띄운다.**
 *
 * 예전에는 각 입력칸 옆에 `absolute` 로 붙였다. z-index 를 아무리 올려도
 * 조상 중 하나가 `overflow: hidden/auto` 면 그 경계에서 잘린다 — 포트폴리오의
 * 종목 추가 칸은 보유 패널(`overflow-hidden`) 안에 있어서 다섯 줄 중 첫 줄만
 * 보이고 나머지는 '섹터 비중' 패널 뒤로 사라졌다 (실측: elementFromPoint 가
 * 2~5번째 줄 자리에서 '섹터 비중' 머리글과 도넛을 돌려줬다). 설정 마법사도
 * 마지막 줄이 하단 '취소' 버튼 밑에 깔렸다. z-index 는 같은 쌓임 맥락 안에서만
 * 순서를 정할 뿐, 잘림은 못 푼다.
 *
 * 그래서 목록을 잘라낼 수 있는 조상에서 아예 빼내고, 입력칸의 화면 좌표로
 * `position: fixed` 배치한다. 열려 있는 동안 스크롤(어느 조상이든 — capture)과
 * 창 크기 변화에 따라 다시 잰다.
 *
 * z-index 는 종목 상세 모달(9999) 보다 위다. 그 모달 안의 검색칸도 이 목록을
 * 쓰고, 모바일 매수/매도 팝업(110)·설정 마법사(100) 도 그보다 아래다.
 *
 * 키보드 판정(↑↓·Enter)은 여기서 하지 않는다 — 입력칸을 가진 쪽이
 * `lib/suggestions.ts` 의 순수 함수로 한다. 이 컴포넌트는 그리기만 한다.
 */
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/utils'
import { useMarket } from '@/lib/useMarket'
import type { Suggestion } from '@/lib/suggestions'

/** 인라인 색을 쓰는 화면(종목 상세 모달)용. 주면 클래스 대신 이 색으로 그린다. */
export interface SuggestionPalette {
  bg: string
  border: string
  text: string
  muted: string
  hover: string
}

interface Props<T extends Suggestion> {
  /** 목록을 붙일 기준 요소 — 보통 입력칸. */
  anchorRef: React.RefObject<HTMLElement | null>
  open: boolean
  items: readonly T[]
  /** 키보드로 강조한 줄. 없으면 -1. */
  highlighted: number
  onPick: (item: T) => void
  minWidth?: number
  palette?: SuggestionPalette
}

export const SUGGESTION_Z = 10050
const GAP = 4
const MAX_H = 264
/** 아래 공간이 이보다 좁고 위가 더 넓으면 위로 편다. */
const MIN_BELOW = 150
const EDGE = 8

interface Placement {
  left: number
  width: number
  top?: number
  bottom?: number
  maxHeight: number
}

/**
 * 한 줄에 무엇을 쓸지 — 한국은 **이름만**, 미국은 티커 + 이름.
 *
 * 한국 코드는 아무것도 알려 주지 않는다. 이름을 못 받았으면(수집 실패 등)
 * 티커를 쓴다 — 빈 줄은 무슨 종목인지 전혀 알 수 없다.
 */
export function SuggestionRow({ item, market, palette }: {
  item: Suggestion
  market: string
  palette?: SuggestionPalette
}) {
  if (market === 'KR') {
    const label = item.name?.trim() || item.ticker
    return palette
      ? <span style={{ color: palette.text, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      : <span className="font-bold text-[13px] truncate">{label}</span>
  }
  return palette ? (
    <>
      <span style={{ color: palette.text, fontFamily: 'monospace', fontWeight: 700, flexShrink: 0 }}>{item.ticker}</span>
      <span style={{ color: palette.muted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</span>
    </>
  ) : (
    <>
      <span className="font-mono font-bold text-[13px] flex-shrink-0">{item.ticker}</span>
      <span className="text-[11px] text-[#94a3b8] truncate">{item.name}</span>
    </>
  )
}

export default function SuggestionList<T extends Suggestion>({
  anchorRef, open, items, highlighted, onPick, minWidth = 220, palette,
}: Props<T>) {
  const market = useMarket()
  const [place, setPlace] = useState<Placement | null>(null)
  const [hover, setHover] = useState(-1)
  const listRef = useRef<HTMLDivElement>(null)
  const visible = open && items.length > 0

  const measure = useCallback(() => {
    const el = anchorRef.current
    if (!el) { setPlace(null); return }
    const r = el.getBoundingClientRect()
    const vh = window.innerHeight
    const vw = window.innerWidth
    // 입력칸이 화면 밖으로 완전히 나갔으면 목록도 숨긴다 — 떠다니는 목록이
    // 머리글 같은 엉뚱한 자리를 덮는다.
    if (r.bottom < 0 || r.top > vh || r.width === 0) { setPlace(null); return }
    const width = Math.min(Math.max(r.width, minWidth), vw - EDGE * 2)
    const left = Math.max(EDGE, Math.min(r.left, vw - width - EDGE))
    const below = vh - r.bottom - GAP - EDGE
    const above = r.top - GAP - EDGE
    // 기본은 아래로 편다. 위에 두면 방금 친 글자와 겹쳐 보일 것 같지만, 입력칸
    // 바깥(위)에 붙이므로 글자를 가리지 않는다. 아래가 모자랄 때만 뒤집는다.
    if (below < Math.min(MAX_H, MIN_BELOW) && above > below) {
      setPlace({ left, width, bottom: vh - r.top + GAP, maxHeight: Math.min(MAX_H, above) })
    } else {
      setPlace({ left, width, top: r.bottom + GAP, maxHeight: Math.max(80, Math.min(MAX_H, below)) })
    }
  }, [anchorRef, minWidth])

  useLayoutEffect(() => {
    if (!visible) return
    measure()
  }, [visible, items.length, measure])

  useEffect(() => {
    if (!visible) return
    let frame = 0
    const onChange = () => {
      if (frame) return
      frame = requestAnimationFrame(() => { frame = 0; measure() })
    }
    // capture: 스크롤은 버블링하지 않는다. 입력칸을 감싼 어느 조상이 스크롤해도
    // 받으려면 capture 로 창에서 받아야 한다.
    window.addEventListener('scroll', onChange, true)
    window.addEventListener('resize', onChange)
    return () => {
      window.removeEventListener('scroll', onChange, true)
      window.removeEventListener('resize', onChange)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [visible, measure])

  useEffect(() => { setHover(-1) }, [items])

  // 키보드로 옮긴 줄이 목록 밖에 있으면 보이게 한다.
  useEffect(() => {
    if (!visible || highlighted < 0) return
    const row = listRef.current?.children[highlighted] as HTMLElement | undefined
    row?.scrollIntoView({ block: 'nearest' })
  }, [visible, highlighted])

  if (!visible || !place || typeof document === 'undefined') return null

  const style: React.CSSProperties = {
    position: 'fixed',
    left: place.left,
    width: place.width,
    top: place.top,
    bottom: place.bottom,
    maxHeight: place.maxHeight,
    zIndex: SUGGESTION_Z,
    overflowY: 'auto',
    ...(palette ? {
      background: palette.bg, border: `1px solid ${palette.border}`, borderRadius: 6,
      boxShadow: '0 10px 25px rgba(0,0,0,0.35)',
    } : {}),
  }

  return createPortal(
    <div
      ref={listRef}
      role="listbox"
      data-suggestion-list=""
      style={style}
      className={palette ? undefined : 'bg-[#0b1220] border border-[#1e2d40] rounded shadow-xl'}
    >
      {items.map((s, idx) => {
        const active = idx === highlighted
        return (
          <div
            key={s.ticker}
            role="option"
            aria-selected={active}
            // mousedown 에서 고르고 기본 동작을 막는다 — 입력칸의 포커스가 유지되어
            // blur 가 먼저 목록을 닫아 클릭이 허공에 떨어지는 일이 없다.
            onMouseDown={e => { e.preventDefault(); onPick(s) }}
            onMouseEnter={() => setHover(idx)}
            onMouseLeave={() => setHover(h => (h === idx ? -1 : h))}
            className={palette ? undefined : cn(
              'flex items-center gap-2 w-full text-left px-3 py-2 cursor-pointer transition-colors',
              active ? 'bg-[#1e2d40] text-[#e2e8f0]' : 'text-[#cbd5e1] hover:bg-[#0f1e30] hover:text-[#e2e8f0]',
            )}
            style={palette ? {
              display: 'flex', alignItems: 'center', gap: 8, padding: '7px 12px', fontSize: 12,
              cursor: 'pointer',
              background: active || hover === idx ? palette.hover : 'transparent',
            } : undefined}
          >
            <SuggestionRow item={s} market={market} palette={palette} />
          </div>
        )
      })}
    </div>,
    document.body,
  )
}
