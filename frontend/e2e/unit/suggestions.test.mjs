/**
 * 종목 제안 목록의 Enter 판정 — `lib/suggestions.ts` (요청 4번).
 *
 * 입력칸 다섯 곳이 이 규칙 하나를 쓴다. 한 곳에서만 틀려도 사용자는 "엔터를 쳤더니
 * 엉뚱한 종목" 을 본다 — 상단 검색이 미완성 입력("삼성")을 그대로 티커로 열어
 * "데이터 없음" 을 띄운 것이 그 형태였다.
 *
 *   1. 화살표로 고른 항목
 *   2. 입력과 **정확히 같은** 티커 · 한국 코드 6자리 · 이름 (목록 순서상 첫 번째)
 *   3. 목록의 첫 줄
 *
 * 2 가 3 보다 앞서는 이유: 서버 정렬은 관련도다. `LG` 를 쳤는데 첫 줄이 `LG화학`
 * 이면 첫 줄 규칙은 사용자가 정확히 친 종목을 버린다.
 *
 * **여기서 못 재는 것** — 한글 조합 중(`isComposing`) Enter 와 "목록이 안 보일 때는
 * 판정하지 않는다" 는 호출부(.tsx 여섯 곳)에 인라인으로 있다. node 는 .tsx 를 못
 * 읽는다. 순수 함수로 빠지면 여기서 잰다.
 */
import assert from 'node:assert/strict'
import test, { describe } from 'node:test'

import {
  isExactMatch, moveHighlight, pickOnEnter, selectionLabel, tickerByExactName,
} from '../../src/lib/suggestions.ts'

const s = (ticker, name) => ({ ticker, name })
const LG = [s('051910.KS', 'LG화학'), s('373220.KS', 'LG에너지솔루션'), s('003550.KS', 'LG')]
const SAMSUNG = [s('005935.KS', '삼성전자우'), s('005930.KS', '삼성전자'), s('028260.KS', '삼성물산')]

describe('pickOnEnter — 우선순위', () => {
  test('화살표로 고른 항목이 정확히 같은 항목보다 먼저다', () => {
    assert.equal(pickOnEnter('LG', LG, 1).ticker, '373220.KS')
  })

  test('고른 항목이 없으면 정확히 같은 항목 — 첫 줄이 아니라', () => {
    const pick = pickOnEnter('LG', LG)
    assert.equal(pick.ticker, '003550.KS',
      `picked ${pick.name} -- the user typed exactly 'LG' and the first-row rule threw it away`)
  })

  test('정확히 같은 것이 없으면 첫 줄', () => {
    assert.equal(pickOnEnter('LG에너', LG).ticker, '051910.KS')
  })

  test('빈 목록은 null — 호출자가 기존 Enter 동작을 한다', () => {
    assert.equal(pickOnEnter('LG', []), null)
    assert.equal(pickOnEnter('LG', [], 0), null)
  })

  for (const bad of [-1, 3, 99, 1.5, Number.NaN]) {
    test(`목록 밖의 강조 위치(${bad})는 없는 것으로 본다`, () => {
      assert.equal(pickOnEnter('LG', LG, bad).ticker, '003550.KS')
    })
  }

  test('같은 입력이 두 줄에 맞으면 목록 순서상 첫 번째', () => {
    const list = [s('A1', '가'), s('005930.KS', '삼성전자'), s('005930.KQ', '삼성전자')]
    assert.equal(pickOnEnter('삼성전자', list).ticker, '005930.KS')
  })
})

describe('isExactMatch — "정확히 같다" 의 범위', () => {
  test('이름 · 티커는 대소문자와 앞뒤 공백을 무시한다', () => {
    assert.ok(isExactMatch('  lg ', s('003550.KS', 'LG')))
    assert.ok(isExactMatch('aapl', s('AAPL', 'Apple Inc.')))
    assert.ok(isExactMatch('Apple Inc.', s('AAPL', 'apple inc.')))
  })

  test('한국 코드는 6자리만 쳐도 같다 — 사용자는 .KS 를 모른다', () => {
    assert.ok(isExactMatch('005930', s('005930.KS', '삼성전자')))
    assert.ok(isExactMatch('035720', s('035720.KQ', '카카오')))
    assert.equal(pickOnEnter('005930', SAMSUNG).ticker, '005930.KS')
  })

  test('점 앞만 떼는 규칙은 한국 코드 모양에만 — BRK 는 BRK.B 가 아니다', () => {
    assert.equal(isExactMatch('BRK', s('BRK.B', 'Berkshire Hathaway')), false)
  })

  test('포함·접두는 같은 것이 아니다', () => {
    assert.equal(isExactMatch('삼성', s('005930.KS', '삼성전자')), false)
    assert.equal(isExactMatch('삼성전자', s('005935.KS', '삼성전자우')), false)
  })

  test('조합형과 완성형 한글은 같은 이름이다', () => {
    const decomposed = '삼성전자'.normalize('NFD')
    assert.notEqual(decomposed, '삼성전자', 'test setup: the input must really be decomposed')
    assert.ok(isExactMatch(decomposed, s('005930.KS', '삼성전자')))
  })

  test('빈 입력은 무엇과도 같지 않다', () => {
    assert.equal(isExactMatch('   ', s('', '')), false)
    assert.equal(pickOnEnter('', LG).ticker, '051910.KS')
  })
})

describe('moveHighlight', () => {
  test('아직 고른 것이 없으면 ↓ 도 ↑ 도 첫 줄', () => {
    assert.equal(moveHighlight(-1, 1, 5), 0)
    assert.equal(moveHighlight(-1, -1, 5), 0)
  })

  test('목록 밖으로 나가지 않는다', () => {
    assert.equal(moveHighlight(4, 1, 5), 4)
    assert.equal(moveHighlight(0, -1, 5), 0)
    assert.equal(moveHighlight(2, 1, 5), 3)
  })

  test('빈 목록이면 -1', () => {
    assert.equal(moveHighlight(0, 1, 0), -1)
  })
})

describe('selectionLabel · tickerByExactName', () => {
  test('한국은 이름을, 미국은 티커를 칸에 남긴다', () => {
    assert.equal(selectionLabel('KR', '005930.KS', '삼성전자'), '삼성전자')
    assert.equal(selectionLabel('US', 'AAPL', 'Apple Inc.'), 'AAPL')
  })

  test('이름을 못 받았으면 티커 — 빈 칸은 무엇을 골랐는지 말하지 않는다', () => {
    assert.equal(selectionLabel('KR', '005930.KS', '  '), '005930.KS')
    assert.equal(selectionLabel('KR', '005930.KS', null), '005930.KS')
  })

  test('목록 없는 칸의 이름 해석은 정확히 같을 때만', () => {
    const names = { '005930.KS': '삼성전자', '000830.KS': '삼성화재' }
    assert.equal(tickerByExactName(' 삼성전자 ', names), '005930.KS')
    assert.equal(tickerByExactName('삼성', names), null)
    assert.equal(tickerByExactName('', names), null)
  })
})
