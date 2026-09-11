/**
 * 보유 표 일변동률 헤더 — **표 전체에 대한 주장**이라 경계가 중요하다.
 *
 * 원래 `rows.some(r => r.is_live)` 였다. 한 종목만 실시간이어도 헤더가
 * 'LIVE' 라고 하면 **표 전체가 실시간이라는 주장**이 된다. `fallback_df` 로
 * 채운 행은 마지막 확정 종가인데(is_live=false), 현재가 칸은 실시간 행과
 * 똑같이 보인다. 구별할 근거가 툴팁뿐이라 헤더가 유일하게 눈에 띄는
 * 신호다. 그 한 글자가 틀린 채로 나갔고, 사람이 화면을 보고서야 잡혔다.
 *
 * 여기서는 **진짜 소스를 그대로 부른다** — node 가 타입을 벗겨 읽는다.
 * 옮겨 적은 사본과 비교하면 내가 일관되게 읽었다는 것만 검증된다
 * (CLAUDE.md §6).
 */
import assert from 'node:assert/strict'
import test, { describe } from 'node:test'

import { dailyChangeHeader } from '../../src/components/portfolio/dailyChangeHeader.ts'

const live = (ticker) => ({ ticker, is_live: true })
const closed = (ticker, as_of = '2026-09-10') => ({ ticker, as_of, is_live: false })

describe('기준일을 말할 수 없으면 말하지 않는다', () => {
  // 로딩 중(undefined)과 보유 없음([])은 **둘 다 기준일이 없다.** 다르게
  // 그리면 로딩 중에 "종가 기준" 같은 거짓 기준일이 잠깐 떴다 사라진다.
  for (const [label, input] of [
    ['undefined (로딩 중)', undefined],
    ['null', null],
    ['빈 배열 (보유 없음)', []],
  ]) {
    test(label, () => {
      assert.equal(dailyChangeHeader(input), '일변동률')
    })
  }

  test('CASH 만 있으면 종목이 없는 것과 같다', () => {
    // 현금에는 일변동이 없다. 세면 "보유 있음" 으로 읽혀 기준일을 지어낸다.
    assert.equal(dailyChangeHeader([{ ticker: 'CASH', is_live: true }]), '일변동률')
  })

  test('종가 행인데 as_of 가 하나도 없으면 날짜를 지어내지 않는다', () => {
    assert.equal(dailyChangeHeader([{ ticker: 'AAPL', is_live: false }]), '일변동률')
  })
})

describe('LIVE 는 표 전체에 대한 주장이다', () => {
  test('전부 실시간이어야 LIVE 다', () => {
    assert.equal(dailyChangeHeader([live('AAPL'), live('MSFT')]), '일변동률 · LIVE')
  })

  test('하나라도 아니면 LIVE 가 아니다 — 이게 틀렸던 자리', () => {
    const header = dailyChangeHeader([live('AAPL'), closed('MSFT')])
    assert.equal(
      header, '일변동률 · 일부 실시간',
      `header=${header} -- one stale row makes 'LIVE' a claim about rows it ` +
      `cannot support; the stale row looks identical in the price column.`,
    )
  })

  test('마지막 한 종목만 실시간이어도 섞였다고 말한다', () => {
    // `every` 와 `some` 의 경계를 양쪽에서 짚는다. 한쪽만 재면 둘 중 하나를
    // 뒤집어도 통과하는 조합이 남는다.
    assert.equal(
      dailyChangeHeader([closed('AAPL'), closed('MSFT'), live('NVDA')]),
      '일변동률 · 일부 실시간',
    )
  })

  test('CASH 는 LIVE 판정에서 빠진다', () => {
    // CASH 는 `is_live` 를 안 들고 온다. 세면 실시간 표가 영원히
    // '일부 실시간' 이 되어 진짜로 섞인 경우와 구별되지 않는다.
    assert.equal(
      dailyChangeHeader([live('AAPL'), { ticker: 'CASH', q: 1000 }]),
      '일변동률 · LIVE',
    )
  })
})

describe('종가 기준이면 어느 날 종가인지 말한다', () => {
  test('MM/DD 로 줄여 쓴다', () => {
    assert.equal(dailyChangeHeader([closed('AAPL', '2026-09-10')]),
      '일변동률 (09/10 종가)')
  })

  test('연도가 바뀌어도 월/일만 쓴다', () => {
    assert.equal(dailyChangeHeader([closed('AAPL', '2025-01-02')]),
      '일변동률 (01/02 종가)')
  })

  test('as_of 가 없는 행이 앞에 있어도 있는 값을 찾아 쓴다', () => {
    // `find(r => r.as_of)` 라 순서에 안 흔들린다. `rows[0].as_of` 였다면
    // 첫 행이 빈 것만으로 기준일이 통째로 사라진다.
    assert.equal(
      dailyChangeHeader([{ ticker: 'AAPL', is_live: false }, closed('MSFT', '2026-09-08')]),
      '일변동률 (09/08 종가)',
    )
  })
})

describe('지금 동작을 적어 둔다 — 고칠지는 소유 창 판단', () => {
  test('행마다 기준일이 달라도 헤더는 하나만 말한다', () => {
    // 한국·미국이 섞인 표나 수집이 밀린 종목이 있으면 실제로 갈린다.
    // 그때 헤더는 **첫 번째로 찾은 날짜**를 표 전체의 기준일처럼 말한다.
    // 지금 동작을 고정해 둔다 — 적어 두지 않으면 다음 사람이 이 헤더를
    // "표 전체가 이 날 종가" 로 읽는다.
    const header = dailyChangeHeader([
      closed('AAPL', '2026-09-10'),
      closed('005930.KS', '2026-09-08'),
    ])
    assert.equal(
      header, '일변동률 (09/10 종가)',
      `header=${header} -- if this changed, the note in this test is stale.`,
    )
  })
})
