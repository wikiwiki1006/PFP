/**
 * lib/suggestions.ts
 * ──────────────────
 * 종목 제안 목록을 키보드로 다루는 규칙. **판정은 여기 한 곳에서만 한다.**
 *
 * 종목을 고르는 입력칸이 다섯 곳이다(종목 추가 · 상단 검색 · 종목 상세 모달 ·
 * 설정 마법사 · AI 리서치). 각자 Enter 를 따로 판정하면 한 곳만 고쳐지고
 * 나머지는 옛 규칙으로 남는다 — 실제로 화살표 선택은 종목 추가에만 있었고,
 * 상단 검색은 미완성 입력("삼성")을 그대로 티커로 열어 "데이터 없음" 을 띄웠다.
 *
 * 이 파일은 React·경로 별칭(`@/`)을 import 하지 않는다. node 가 타입만 벗겨
 * 바로 읽을 수 있어야 테스트가 **진짜 소스**를 부른다 (CLAUDE.md §6).
 */

export interface Suggestion {
  ticker: string
  name: string
}

/** 비교용 정규화 — 앞뒤 공백·대소문자·유니코드 조합형 차이를 지운다. */
function norm(s: string | null | undefined): string {
  return (s ?? '').normalize('NFC').trim().toUpperCase()
}

/** 한국 종목코드(`005930.KS`)의 6자리 부분. 한국 코드가 아니면 null. */
function krCode(ticker: string): string | null {
  const m = /^(\d{6})\.K[SQ]$/i.exec(ticker.trim())
  return m ? m[1] : null
}

/**
 * 입력과 **정확히 같은** 항목인가 — 티커, 한국 코드 6자리, 또는 이름.
 *
 * 6자리를 따로 보는 이유: 사용자는 '.KS' 를 모른다. `005930` 을 친 사람은
 * `005930.KS` 를 정확히 입력한 것이다. 반대로 미국 `BRK.B` 에서 점 앞만
 * 떼어 `BRK` 와 같다고 보면 안 되므로, 한국 코드 모양일 때만 적용한다.
 */
export function isExactMatch(input: string, s: Suggestion): boolean {
  const q = norm(input)
  if (!q) return false
  if (norm(s.ticker) === q) return true
  const code = krCode(s.ticker)
  if (code != null && code === q) return true
  return norm(s.name) === q
}

/**
 * 제안 목록이 **보이는 상태**에서 Enter 를 눌렀을 때 고를 항목.
 *
 *   1. 화살표로 고른 항목이 있으면 그것
 *   2. 입력과 정확히 같은 티커/이름이 목록에 있으면 그것 (목록 순서상 첫 번째)
 *   3. 없으면 목록의 첫 번째
 *
 * 목록이 비었으면 `null` — 호출자는 기존 Enter 동작을 한다. 목록이 안 보이는
 * 상태를 여기에 넘기지 않는다: 보이지 않는 목록에서 고르면 사용자가 본 적
 * 없는 종목이 선택된다.
 *
 * 2 가 3 보다 앞서는 이유: 서버 정렬은 관련도지 일치가 아니다. `LG` 를 쳤는데
 * 시총이 큰 `LG화학` 이 첫 줄이면, 첫 줄을 고르는 규칙은 사용자가 정확히
 * 입력한 종목을 버린다.
 */
export function pickOnEnter<T extends Suggestion>(
  input: string,
  list: readonly T[],
  highlighted: number = -1,
): T | null {
  if (!list.length) return null
  if (Number.isInteger(highlighted) && highlighted >= 0 && highlighted < list.length) {
    return list[highlighted]
  }
  return list.find(s => isExactMatch(input, s)) ?? list[0]
}

/**
 * 화살표 키로 옮긴 강조 위치. 목록 밖으로 나가지 않는다.
 *
 * 아직 아무것도 강조하지 않은 상태(-1)에서 ↓ 는 첫 줄, ↑ 도 첫 줄이다 —
 * ↑ 로 마지막 줄에 가면 긴 목록에서 사용자가 위치를 잃는다.
 */
export function moveHighlight(current: number, delta: 1 | -1, length: number): number {
  if (length <= 0) return -1
  if (current < 0) return 0
  return Math.max(0, Math.min(length - 1, current + delta))
}

/**
 * 종목을 고른 뒤 **입력칸에 남길 글자.** 한국은 이름, 미국은 티커.
 *
 * 입력칸에 보이는 글자와 서버로 보내는 티커는 다른 값이다 — 한국 종목을 고르면
 * 칸에는 '삼성전자' 가 남고, 거래·조회에는 '005930.KS' 가 간다. 이름을 못
 * 받았으면 티커를 남긴다 — 빈 칸은 무엇을 골랐는지 알려 주지 못한다.
 */
export function selectionLabel(
  market: string,
  ticker: string,
  name?: string | null,
): string {
  if (market !== 'KR') return ticker
  const n = (name ?? '').trim()
  return n || ticker
}

/**
 * 제안 목록 없이 받는 입력칸에서 쓰는 이름 해석 — 입력이 사전의 이름과
 * **정확히** 같을 때만 그 티커를 준다. 없으면 null (호출자는 입력을 그대로 쓴다).
 *
 * 부분 일치로 고르지 않는다. 목록을 보여 주지 않는 자리에서 "삼성" 을
 * 삼성제약으로 바꾸면 사용자는 무엇이 골라졌는지 모른다.
 */
export function tickerByExactName(
  input: string,
  names: Readonly<Record<string, string>>,
): string | null {
  const q = norm(input)
  if (!q) return null
  for (const [ticker, name] of Object.entries(names)) {
    if (norm(name) === q) return ticker
  }
  return null
}
