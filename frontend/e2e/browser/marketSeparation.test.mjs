/**
 * G3 · 한국 화면 본문에 미국 시장이 새어 들어오지 않는다.
 *
 * 하나가 여러 결함을 덮는다 — "S&P500 스캔 중" · 베타 툴팁 · 미국
 * 플레이스홀더는 셋이 아니라 **같은 것 하나**였다. 문자열 하나하나를
 * 지목하는 대신 "한국 화면 본문에 미국 시장 이름이 있는가" 를 묻는다.
 *
 * ## 범위가 denylist 보다 중요하다
 *
 * 두 가지를 **일부러** 뺀다. 안 빼면 **올바른 코드가 영구히 빨간불**이 된다.
 *
 *   상단 마퀴 — §1.4 가 명시한 예외다: "상단 마퀴의 WTI·BTC 등 글로벌
 *     지표는 시장과 무관하게 `$` 가 맞다". 마퀴는 스크롤 루프용으로 두 번
 *     렌더돼서 4종이 8건으로 잡히기까지 한다. denylist 를 아무리 잘 골라도
 *     범위에 마퀴가 들어 있으면 소용이 없다.
 *
 *   VIX — 타일 라벨이 **"변동성 (미국 VIX)"** 다. 미국 것임을 화면에서
 *     밝히고 있고, 대체재인 VKOSPI 를 야후가 주지 않아 의도적으로 남긴
 *     것이다 (AlphaTerminal.tsx 의 그 주석). 이름만 보고 거르면 이 타일이
 *     영원히 위반으로 잡힌다.
 *
 * ## 대조군이 없으면 "아무것도 안 그리는" 구현이 통과한다
 *
 * 미국 화면에서 같은 문자열이 **나오는지** 같이 잰다. 한쪽만 재면 화면이
 * 통째로 비어도 초록이다.
 */
import assert from 'node:assert/strict'
import test, { after, before, describe } from 'node:test'

import { bodyTextExcludingMarquee, requireApp, visit } from '../lib/app.mjs'

/** 미국 시장을 가리키는 이름. 한국 화면 본문에 있으면 안 된다. */
const US_MARKET_NAMES = ['S&P 500', 'S&P500', 'NASDAQ', 'Dow Jones', '^GSPC', '^IXIC']

/** 미국 종목·ETF 티커. 한국 보유 표에 섞이면 §1.1 그대로다. */
const US_TICKERS = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'MSFT', 'TSLA', 'JEPQ']

/**
 * 지금 한국 화면에 남아 있는 티커와 그 뿌리.
 *
 * 전부 한 곳에서 온다 — `lib/demoData.ts` 의 `DEMO_EARNINGS` 는 시장별
 * 접근자가 없다. `demoMetrics()`·`demoHoldingsDetail()`·`demoHoldingsRaw()`
 * ·`demoSectorWeights()` 는 `getMarket()` 으로 갈리는데 `DEMO_EARNINGS` ·
 * `DEMO_EQUITY_CURVE` · `DEMO_NEWS` 셋은 상수 그대로 넘어간다. 그래서
 * 한국 화면의 실적/배당 표에 미국 종목이 그대로 뜬다.
 *
 * 고치면 여섯 줄이 한꺼번에 사라지고, 아래 두 번째 검사가 지우라고 한다.
 */
const KNOWN_TICKER_LEAK = {
  SPY: 'demoData DEMO_EARNINGS — 시장별 접근자 없음',
  AAPL: 'demoData DEMO_EARNINGS — 시장별 접근자 없음',
  NVDA: 'demoData DEMO_EARNINGS — 시장별 접근자 없음',
  MSFT: 'demoData DEMO_EARNINGS — 시장별 접근자 없음',
  TSLA: 'demoData DEMO_EARNINGS — 시장별 접근자 없음',
  JEPQ: 'demoData DEMO_EARNINGS — 시장별 접근자 없음',
}

const seen = {}

function present(text, needles) {
  return needles.filter(n => text.includes(n))
}

before(async () => {
  await requireApp()
  for (const market of ['KR', 'US']) {
    const run = await visit('/terminal', { market })
    seen[market] = {
      run,
      body: await bodyTextExcludingMarquee(run.page),
      full: await run.page.evaluate(() => document.body.innerText || ''),
      tickers: await run.page.evaluate(() =>
        [...document.querySelectorAll('table tbody tr')]
          .map(tr => (tr.innerText || '').trim()).filter(Boolean)),
    }
  }
})

after(async () => {
  for (const s of Object.values(seen)) await s.run.close()
})


describe('전제 — 시장이 실제로 바뀌었다', () => {
  test('한국 화면의 보유 표에 한국 종목이 있다', () => {
    // 이게 없으면 아래 전부가 **미국 화면을 한국 화면이라 믿고** 재는 것이
    // 된다. 전환이 조용히 안 되면 위반 0건이 나오고 그건 깨끗한 것과
    // 구별되지 않는다.
    const rows = seen.KR.tickers.join('\n')
    assert.match(
      rows, /\.KS|\.KQ/,
      `no Korean ticker in the holdings table -- the market switch did not ` +
      `take effect, so everything below measured the US screen.`,
    )
  })

  test('미국 화면에는 한국 종목이 없다', () => {
    assert.doesNotMatch(seen.US.tickers.join('\n'), /\.KS|\.KQ/)
  })
})


describe('G3 · 한국 본문에 미국 시장 이름이 없다', () => {
  test('지수 이름', () => {
    const leaked = present(seen.KR.body, US_MARKET_NAMES)
    assert.deepEqual(
      leaked, [],
      `the Korean screen names a US index in its body: ${leaked.join(', ')} -- ` +
      `a won-denominated portfolio compared against a dollar index is not a ` +
      `comparison. (마퀴는 §1.4 예외라 이미 제외했다.)`,
    )
  })

  test('달러 기호', () => {
    assert.ok(
      !seen.KR.body.includes('$'),
      `the Korean screen shows '$' outside the marquee -- 원화 화면의 금액은 ` +
      `₩ 다 (§1.4). 마퀴의 WTI·BTC 는 제외했다.`,
    )
  })

  test('대조군 — 미국 화면에는 그 이름들이 있다', () => {
    // 없으면 위 둘은 "본문을 아예 안 그린다" 는 상태로도 통과한다.
    const found = present(seen.US.body, US_MARKET_NAMES)
    assert.ok(
      found.length > 0 && seen.US.body.includes('$'),
      `the US screen names no US index and shows no '$' either -- the checks ` +
      `above then prove nothing. body=${seen.US.body.length} chars`,
    )
  })
})


describe('G3 · 한국 본문의 미국 티커는 알려진 것뿐이다', () => {
  test('원장에 없는 티커가 새면 실패한다', () => {
    const unexpected = present(seen.KR.body, US_TICKERS)
      .filter(t => !(t in KNOWN_TICKER_LEAK))
    assert.deepEqual(
      unexpected, [],
      `a US ticker appears on the Korean screen: ${unexpected.join(', ')} -- ` +
      `holdings, earnings and signals must all be filtered by market (§1.1).`,
    )
  })

  test('원장이 문제보다 오래 남지 않는다', () => {
    const gone = Object.keys(KNOWN_TICKER_LEAK).filter(t => !seen.KR.body.includes(t))
    assert.deepEqual(
      gone, [],
      `these no longer leak but are still listed in KNOWN_TICKER_LEAK -- ` +
      `delete the lines (they share one root cause, so they go together):\n` +
      gone.map(t => `  ${t}  (${KNOWN_TICKER_LEAK[t]})`).join('\n'),
    )
  })

  test('대조군 — 미국 화면에는 그 티커들이 있다', () => {
    const found = present(seen.US.body, Object.keys(KNOWN_TICKER_LEAK))
    assert.ok(
      found.length >= 4,
      `the US screen shows almost none of them (${found.join(',')}) -- the ` +
      `ledger above may be recording an empty screen rather than a leak.`,
    )
  })
})


describe('마퀴는 범위 밖이다 — 그게 의도임을 고정한다', () => {
  test('마퀴를 포함하면 한국 화면에도 미국 이름이 있다', () => {
    // 이 검사가 빨개지면 둘 중 하나다: 마퀴가 사라졌거나, 마퀴 제외가
    // 동작을 멈췄거나. 후자면 위 검사들이 **아무것도 안 재고** 통과한다.
    const inFull = present(seen.KR.full, US_MARKET_NAMES)
    assert.ok(
      inFull.length > 0,
      `even with the marquee included the Korean page names no US index -- ` +
      `either the marquee is gone (then drop this test and the exclusion) or ` +
      `the exclusion selector stopped matching (then G3 above is measuring ` +
      `nothing). (제외가 멈추면 위 검사가 공허해진다.)`,
    )
    assert.deepEqual(
      present(seen.KR.body, inFull), [],
      `the marquee exclusion did not remove ${inFull.join(',')} from the body`,
    )
  })
})
