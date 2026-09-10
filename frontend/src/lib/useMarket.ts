/**
 * lib/useMarket.ts
 * ────────────────
 * 시장 전환에 반응해 다시 그리게 하는 훅.
 *
 * 대부분의 금액 표기는 조회 응답과 함께 오므로 전환 시 재조회가 걸리면서
 * 자연히 다시 그려진다. 하지만 사용자가 입력한 값을 그 자리에서 포맷하는 곳
 * (매수 수량 × 단가 미리보기 등)은 조회와 무관해 다시 그려지지 않는다.
 * 그런 화면에서 기호가 이전 시장 것으로 남는 것을 막는다.
 */
import { useEffect, useState } from 'react'
import { getMarket, subscribeMarket, type Market } from './market'

export function useMarket(): Market {
  const [m, setM] = useState<Market>(getMarket)
  useEffect(() => subscribeMarket(setM), [])
  return m
}
