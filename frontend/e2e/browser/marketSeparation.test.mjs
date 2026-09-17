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
 * (예전에는 `VIX` 도 뺐다 — 타일 라벨이 "변동성 (미국 VIX)" 였다. 지금 라벨은
 * '변동성' 이고 한국 값은 VKOSPI 다(ba60ceb). 2026-09-17 슬롯 3 에서 재 보니 두
 * 화면 본문 어디에도 `VIX` 가 없어서, 뺄 이유가 사라진 예외를 거두고 목록에 넣었다.
 * 한국 본문에 `VIX` 가 다시 보이면 그건 미국 지표가 한국 화면에 실린 것이다.)
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
const US_MARKET_NAMES = ['S&P 500', 'S&P500', 'NASDAQ', 'Dow Jones', '^GSPC', '^IXIC', 'VIX']

/** 미국 종목·ETF 티커. 한국 보유 표에 섞이면 §1.1 그대로다. */
const US_TICKERS = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'MSFT', 'TSLA', 'JEPQ']

/**
 * 지금 한국 화면에 남아 있는 티커와 그 뿌리.
 *
 * 전부 한 곳에서 온다. `lib/demoData.ts` 에는 이제 시장별 접근자
 * `demoEarnings()`·`demoNews()`·`demoEquityCurve()` 가 **있다**(6cc0751) —
 * 그런데 `pages/AlphaTerminal.tsx` 가 여전히 상수 `DEMO_EARNINGS`·`DEMO_NEWS`
 * ·`DEMO_EQUITY_CURVE` 를 넘긴다 (2026-09-17 확인). 그래서 한국 화면의
 * 실적/배당 표에 미국 종목이 그대로 뜬다.
 *
 * 고치면 여섯 줄이 한꺼번에 사라지고, 아래 두 번째 검사가 지우라고 한다.
 */
const LEAK_ROOT = 'AlphaTerminal 이 DEMO_EARNINGS 상수를 넘긴다 — demoEarnings() 를 안 쓴다'
const KNOWN_TICKER_LEAK = {
  SPY: LEAK_ROOT,
  AAPL: LEAK_ROOT,
  NVDA: LEAK_ROOT,
  MSFT: LEAK_ROOT,
  TSLA: LEAK_ROOT,
  JEPQ: LEAK_ROOT,
}

const seen = {}

function present(text, needles) {
  return needles.filter(n => text.includes(n))
}

/** 페이지가 보낸 `/api/` 요청들이 실은 `market` 값. 인터셉터가 `getMarket()` 을 붙인다. */
function apiMarkets(run) {
  const out = new Set()
  for (const r of run.responses) {
    const url = new URL(r.url)
    if (!url.pathname.startsWith('/api/')) continue
    const market = url.searchParams.get('market')
    if (market != null) out.add(market)
  }
  return out
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
  // 이게 없으면 아래 전부가 **미국 화면을 한국 화면이라 믿고** 재는 것이 된다.
  // 전환이 조용히 안 되면 위반 0건이 나오고 그건 깨끗한 것과 구별되지 않는다.
  //
  // 예전에는 "보유 표에 `.KS` 가 있다" 로 봤다. 요청 1번 이후 한국 표는 종목을
  // **이름으로만** 보여 줘서 그 신호가 사라졌고, 전환이 됐는데도 전제가 빨갰다.
  // 종목 이름(삼성전자…)은 예시 데이터에 묶여 있어 쓰지 않는다. 대신 두 신호를
  // 본다 — 둘 다 같은 `getMarket()` 에서 나오지만 서로 다른 층이다:
  //   요청  페이지의 `/api/` 요청이 그 시장을 싣는다 (axios 인터셉터)
  //   화면  보유 표의 금액이 그 시장 통화로 그려진다 (formatPrice)
  // '$' 는 쓰지 않는다 — 아래 G3 가 재는 바로 그 신호라, 전제로 쓰면 G3 가
  // 빨개질 수 없다.
  test('한국 화면은 한국 시장으로 요청하고 원화로 그린다', () => {
    const markets = apiMarkets(seen.KR.run)
    assert.deepEqual(
      [...markets], ['KR'],
      `the Korean page sent API requests for ${[...markets].join(',') || 'no market'} -- ` +
      `the market switch did not take effect, so everything below measured another screen.`,
    )
    assert.ok(
      seen.KR.tickers.some(row => row.includes('₩')),
      `no won amount in the Korean holdings table -- either the switch did not reach ` +
      `the formatters or the table did not render:\n${seen.KR.tickers.join('\n')}`,
    )
  })

  test('미국 화면은 미국 시장으로 요청하고 원화가 없다', () => {
    assert.deepEqual([...apiMarkets(seen.US.run)], ['US'])
    const rows = seen.US.tickers.join('\n')
    assert.ok(rows.length > 0, 'the US holdings table did not render')
    assert.ok(!rows.includes('₩'), `won amounts on the US screen:\n${rows}`)
    assert.doesNotMatch(rows, /\.KS|\.KQ/)
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
