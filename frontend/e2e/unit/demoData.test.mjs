/**
 * 비로그인 미리보기 데이터의 **단위**와 **집계** — `lib/demoData.ts` (f2dcdb2).
 *
 * 실제 API 는 비중을 비율로 주고(holdings-detail `value/total_equity`, sector-weights
 * `round(v/total, 4)`) 화면이 100 을 곱한다. 예시가 퍼센트(30.5)로 적혀 있어 비로그인
 * 화면에 미국 2000%·한국 3050%·섹터 5170% 가 나갔다 — 방문자가 처음 보는 화면이다.
 * 흐림 처리 뒤라 신호가 없었다.
 *
 * 그래서 두 가지를 시장마다 잰다:
 *
 *   단위   보유·섹터 비중이 0~1 이고 합이 1 (반올림 허용)
 *   정합   비중 == 평가액 / 총자산, 섹터 비중 == 그 섹터 보유 비중의 합, 그리고
 *          집계가 자기 표와 맞는다 (이 파일의 주석이 적어 둔 불변식 — 한국·미국
 *          데모가 차례로 표와 안 맞는 집계를 들고 있었다)
 *
 * 허용 오차는 **표기 자릿수에서** 나온다. 비중은 소수 셋째 자리로 적혀 있어 한 줄에
 * 최대 0.0005 가 어긋나고, n 줄의 합은 n × 0.0005 까지다.
 *
 * `demoData.ts` 는 `./market` 을 확장자 없이 import 해서 node 가 그냥은 못 읽는다.
 * `e2e/lib/resolveTs.mjs` 훅으로 `.ts` 를 찾게 한 뒤 **동적으로** 불러온다.
 */
import assert from 'node:assert/strict'
import test, { before, describe } from 'node:test'

import { resolveExtensionlessTs } from '../lib/resolveTs.mjs'

const ROUNDING = 0.0005 + 1e-9
let market
let demo

before(async () => {
  resolveExtensionlessTs()
  market = await import('../../src/lib/market.ts')
  demo = await import('../../src/lib/demoData.ts')
})

function snapshot(m) {
  market.setMarket(m)
  assert.equal(market.getMarket(), m, `test setup: could not switch the demo to ${m}`)
  return {
    metrics: demo.demoMetrics(),
    rows: demo.demoHoldingsDetail(),
    raw: demo.demoHoldingsRaw(),
    sectors: demo.demoSectorWeights(),
  }
}

for (const m of ['US', 'KR']) {
  describe(`${m} 예시`, () => {
    test('보유 비중은 0~1 비율이고 합이 1 이다', () => {
      const { rows } = snapshot(m)
      const outside = rows.filter(r => !(r.weight >= 0 && r.weight <= 1))
      assert.deepEqual(outside.map(r => `${r.ticker} ${r.weight}`), [],
        'a demo weight is outside 0..1 -- the screen multiplies by 100, so 30.5 shows as 3050%')
      const sum = rows.reduce((a, r) => a + r.weight, 0)
      assert.ok(Math.abs(sum - 1) <= rows.length * ROUNDING, `weights sum to ${sum}`)
    })

    test('섹터 비중도 0~1 이고 합이 1 이다', () => {
      const { sectors } = snapshot(m)
      const values = Object.entries(sectors)
      assert.deepEqual(values.filter(([, v]) => !(v >= 0 && v <= 1)), [],
        'a demo sector weight is outside 0..1')
      const sum = values.reduce((a, [, v]) => a + v, 0)
      assert.ok(Math.abs(sum - 1) <= values.length * ROUNDING, `sector weights sum to ${sum}`)
    })

    test('비중은 평가액 / 총자산이다', () => {
      const { rows, metrics } = snapshot(m)
      const off = rows.filter(r => Math.abs(r.weight - r.market_value / metrics.total_equity) > ROUNDING)
      assert.deepEqual(off.map(r => `${r.ticker} ${r.weight} vs ${r.market_value / metrics.total_equity}`), [])
    })

    test('섹터 비중은 그 섹터 보유 비중의 합이다', () => {
      const { rows, sectors } = snapshot(m)
      const bySector = {}
      for (const r of rows) {
        bySector[r.sector] = bySector[r.sector] ?? { sum: 0, n: 0 }
        bySector[r.sector].sum += r.weight
        bySector[r.sector].n += 1
      }
      assert.deepEqual(Object.keys(bySector).sort(), Object.keys(sectors).sort(),
        'the sector table and the holdings table name different sectors')
      for (const [sector, { sum, n }] of Object.entries(bySector)) {
        assert.ok(Math.abs(sectors[sector] - sum) <= n * ROUNDING,
          `${sector}: sector weight ${sectors[sector]} vs holdings ${sum}`)
      }
    })

    test('집계가 자기 표와 맞는다', () => {
      const { rows, raw, metrics } = snapshot(m)
      const stocks = rows.filter(r => r.ticker !== 'CASH')
      const cash = rows.find(r => r.ticker === 'CASH')
      const stockValue = stocks.reduce((a, r) => a + r.market_value, 0)
      const cost = stocks.reduce((a, r) => a + r.qty * r.avg_cost, 0)

      assert.equal(metrics.stock_value, stockValue, 'stock_value vs the table')
      assert.equal(metrics.cash_value, cash.market_value, 'cash_value vs the CASH row')
      assert.equal(metrics.total_equity, stockValue + cash.market_value, 'total_equity')
      assert.ok(Math.abs(metrics.total_cost - cost) <= 0.5, `total_cost ${metrics.total_cost} vs ${cost}`)
      assert.ok(Math.abs(metrics.total_return_pct - (stockValue / cost - 1) * 100) <= 0.005 + 1e-9,
        `total_return_pct ${metrics.total_return_pct}`)
      const weighted = rows.reduce((a, r) => a + r.weight * r.chg_pct, 0)
      assert.ok(Math.abs(metrics.today_change_pct - weighted) <= 0.005 + rows.length * ROUNDING * 3,
        `today_change_pct ${metrics.today_change_pct} vs weight-weighted ${weighted}`)
      assert.equal(metrics.change_counted, stocks.length)
      assert.equal(metrics.change_holdings, stocks.length)
      for (const r of stocks) {
        assert.ok(Math.abs(r.market_value - r.current_price * r.qty) <= 1, `${r.ticker} market_value`)
        assert.ok(Math.abs(r.pnl - (r.current_price - r.avg_cost) * r.qty) <= 1, `${r.ticker} pnl`)
        assert.deepEqual([raw[r.ticker]?.q, raw[r.ticker]?.avg], [r.qty, r.avg_cost], `${r.ticker} raw holdings`)
      }
    })
  })
}

test('대조군 — 두 시장의 예시가 실제로 다르다', () => {
  const us = snapshot('US')
  const kr = snapshot('KR')
  assert.notDeepEqual(us.rows.map(r => r.ticker), kr.rows.map(r => r.ticker),
    'both markets returned the same demo -- every check above measured one market twice')
  assert.ok(kr.rows.some(r => /\.K[SQ]$/.test(r.ticker)))
})
