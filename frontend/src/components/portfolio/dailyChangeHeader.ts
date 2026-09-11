/**
 * components/portfolio/dailyChangeHeader.ts
 * ─────────────────────────────────────────
 * 보유 표의 일변동률 컬럼 헤더 문자열.
 *
 * `AlphaTerminal` 안에 인라인으로 있던 것을 뽑았다. 응답 → 문자열인 순수
 * 함수인데 컴포넌트 안에 있으면 **브라우저를 띄워 화면의 글자를 읽는 것
 * 말고는 검증할 방법이 없다.** 실제로 `some`/`every` 를 틀린 채로 나간 적이
 * 있고 그건 사람이 화면을 보고서야 잡혔다. 여기서는 행 배열을 직접 넣어
 * 잰다.
 */

/** 이 함수가 실제로 읽는 필드만 받는다. `HoldingDetail` 전체를 요구하면
 *  테스트가 읽지도 않는 열 개 남짓한 필드를 매번 지어내야 하고, 그러다
 *  보면 지어낸 값이 판단에 영향을 준다고 착각하게 된다. */
export interface DailyChangeRow {
  ticker: string
  as_of?: string | null
  is_live?: boolean
}

export function dailyChangeHeader(holdings: readonly DailyChangeRow[] | null | undefined): string {
  // `?? []` 가 아니라 이 형태인 이유: 호출부가 react-query 의 `data` 를 그대로
  // 넘기므로 로딩 중에는 undefined 다. 로딩과 "보유 없음" 은 둘 다 기준일을
  // 말할 수 없어 같은 중립 문자열로 간다.
  const rows = (holdings ?? []).filter(h => h.ticker !== 'CASH')
  if (!rows.length) return '일변동률'

  // `some` 이 아니라 `every` 다. 한 종목만 실시간이어도 헤더가 'LIVE' 라고
  // 하면 **표 전체가 실시간이라는 주장**이 된다. fallback_df 로 채운 행은
  // 마지막 확정 종가이고(is_live=false, chg_pct=null), 현재가 칸은 실시간
  // 행과 똑같이 보인다 — 구별할 근거가 툴팁뿐이라 헤더가 유일하게 눈에
  // 띄는 신호다. 섞여 있으면 섞였다고 말한다.
  if (rows.every(r => r.is_live)) return '일변동률 · LIVE'
  if (rows.some(r => r.is_live)) return '일변동률 · 일부 실시간'

  const asOf = rows.find(r => r.as_of)?.as_of
  return asOf ? `일변동률 (${asOf.slice(5).replace('-', '/')} 종가)` : '일변동률'
}
