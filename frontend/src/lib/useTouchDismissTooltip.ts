/**
 * lib/useTouchDismissTooltip.ts
 * ──────────────────────────────
 * recharts <Tooltip> 은 마우스 hover 는 mouseleave 로 스스로 지우지만, 터치엔
 * 그에 대응하는 이벤트가 없어 손가락을 떼도 마지막 값 팝업이 화면에 남는다
 * (좁은 모바일 화면에서는 이게 그래프를 그대로 가려버린다).
 *
 * 반환값을 그대로 <Tooltip active={tooltipActive} ...> 와, 차트를 감싸는
 * 컨테이너의 onPointerDown/onPointerUp 에 꽂으면 된다. 마우스는 pointerType
 * 으로 걸러 손대지 않는다 — 기존 hover 동작 그대로.
 */
import { useCallback, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'

export function useTouchDismissTooltip() {
  const [hidden, setHidden] = useState(false)

  const onPointerDown = useCallback((e: ReactPointerEvent) => {
    if (e.pointerType === 'touch') setHidden(false)
  }, [])

  const onPointerUp = useCallback((e: ReactPointerEvent) => {
    if (e.pointerType === 'touch') setHidden(true)
  }, [])

  return { tooltipActive: hidden ? false : undefined, onPointerDown, onPointerUp }
}
