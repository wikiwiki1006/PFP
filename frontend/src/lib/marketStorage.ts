/**
 * lib/marketStorage.ts
 * ────────────────────
 * 시장별로 분리된 sessionStorage.
 *
 * 리포트·최적화 진행 상태는 sessionStorage 에 남겨 새로고침해도 이어지게 해
 * 두었는데, 키가 시장과 무관해서 **한국에서 돌리던 리포트가 미국 화면에도
 * 그대로 떠 있었다.** 진행률과 결과까지 같이 넘어와 어느 시장의 것인지
 * 알 수 없게 된다.
 *
 * 키 뒤에 시장을 붙여 두 시장이 서로의 상태를 볼 수 없게 한다. 미국에서
 * 저장한 값은 한국 화면에서 아예 존재하지 않는 키가 된다.
 */
import { getMarket, type Market } from './market'

function scoped(key: string, market?: Market): string {
  return `${key}::${market ?? getMarket()}`
}

export const marketSession = {
  get(key: string): string | null {
    try { return sessionStorage.getItem(scoped(key)) } catch { return null }
  },
  set(key: string, value: string): void {
    try { sessionStorage.setItem(scoped(key), value) } catch { /* 저장 실패는 무시 */ }
  },
  remove(key: string): void {
    try { sessionStorage.removeItem(scoped(key)) } catch { /* 무시 */ }
  },
}
