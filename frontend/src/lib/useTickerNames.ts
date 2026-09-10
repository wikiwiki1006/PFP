/**
 * lib/useTickerNames.ts
 * ─────────────────────
 * 티커 → 표시용 이름 사전.
 *
 * 한국 종목은 코드(005930.KS)만 봐서는 어느 회사인지 알 수 없다. 화면 곳곳
 * (거래이력·수익률 목록·실적표·최적화 결과)에서 이름이 필요한데, 종목마다
 * 따로 물으면 목록 하나에 수십 번 왕복이 생긴다. 시장당 한 번만 받아 캐시한다.
 *
 * 미국은 빈 사전이 온다 — 티커가 곧 이름 역할을 해서 바꿀 필요가 없다.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api'
import { useMarket } from './useMarket'

export function useTickerNames(): Record<string, string> {
  const market = useMarket()
  const q = useQuery({
    queryKey: ['ticker-names', market],
    queryFn: async () =>
      (await api.get('/api/market/ticker-names')).data as Record<string, string>,
    // 상장사 이름은 거의 변하지 않는다. 하루 캐시로 충분하다.
    staleTime: 86_400_000,
    gcTime: 86_400_000,
    enabled: market === 'KR',
  })
  return q.data ?? {}
}

/**
 * 목록에서 종목을 부를 이름. 한국은 이름, 미국은 티커.
 * 이름을 못 찾으면 티커를 그대로 쓴다 — 빈 칸으로 두면 무슨 종목인지 알 수 없다.
 */
export function displayTicker(
  ticker: string | null | undefined,
  names: Record<string, string>,
): string {
  if (!ticker) return ''
  const n = names[ticker] || names[ticker.toUpperCase()]
  return n && n !== ticker ? n : ticker
}
