/**
 * lib/useIsMobile.ts
 * ───────────────────
 * 좁은 화면(휴대폰)인지 여부. 기준값 767px 은 styles/mobile.css 의
 * `@media (max-width: 767px)` 및 Tailwind 의 `md:` 와 동일하게 맞춰,
 * CSS 로 보이는 레이아웃과 JS 분기가 서로 어긋나지 않게 한다.
 *
 * CSS 미디어 쿼리로 처리할 수 없는 경우(인라인 style 기반 컴포넌트,
 * 터치 제스처를 아예 붙이지 않아야 하는 경우 등)에만 쓴다 — 그 외에는
 * mobile.css 의 클래스 기반 오버라이드를 우선한다.
 */
import { useEffect, useState } from 'react'

export function useIsMobile(): boolean {
  const [m, setM] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    const on = () => setM(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return m
}
