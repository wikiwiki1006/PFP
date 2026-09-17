/**
 * 캔들 차트 확대·축소의 기준점 — `components/chartZoom.ts` (요청 2번).
 *
 * 예전에는 늘 가운데를 기준으로 줄여서, 왼쪽 끝 캔들 위에서 휠을 굴리면 그 캔들이
 * 화면 밖으로 밀려났다. 규칙은 하나다 — **기준점 아래 있던 캔들이 확대 후에도
 * 기준점 아래 있다.** 데이터 끝에 닿으면 "구간이 데이터 밖으로 안 나간다" 가 이긴다.
 *
 * ## 오라클은 구현을 옮겨 적지 않는다
 *
 * `zoomAround` 는 기준 캔들을 맞추는 시작점을 식(`ceil(i − r·n)`)으로 구한다. 여기서
 * 같은 식을 쓰면 식이 틀렸을 때 기대값도 같이 틀린다 (CLAUDE.md §6). 대신 **가능한
 * 시작점을 전부 훑어** "그 비율 아래 캔들이 기준 캔들인 시작점" 을 찾는다:
 *
 *   있으면   결과의 시작점이 그중 하나여야 한다 (정수 구간에서 하나뿐이다)
 *   없으면   데이터 끝에 닿은 것이다 — 결과는 0 또는 total−n 에 붙어야 한다
 *
 * 인자로 쓰는 배율은 화면의 것이다 — 휠 0.85/1.15, 버튼 0.7/1.3.
 */
import assert from 'node:assert/strict'
import test, { describe } from 'node:test'

import { plotRatio, zoomAround } from '../../src/components/chartZoom.ts'

/** 비율 r 이 가리키는 캔들 — 차트가 기준점 아래에 그리는 그 캔들. */
function candleAt(view, r) {
  const span = view.end - view.start
  return Math.min(view.end - 1, Math.floor(view.start + r * span))
}

function expectedSpan(view, total, factor, minSpan = 10) {
  const span = view.end - view.start
  const floorSpan = Math.max(1, Math.min(total, minSpan))
  return Math.max(floorSpan, Math.min(total, Math.round(span * factor)))
}

function checkZoom(view, total, factor, r, toRatio) {
  const out = zoomAround(view, total, factor, r, { minSpan: 10, toRatio })
  const n = out.end - out.start
  const where = `view [${view.start},${view.end}) total ${total} factor ${factor} r ${r}` +
    (toRatio == null ? '' : ` → ${toRatio}`) + ` gave [${out.start},${out.end})`

  assert.ok(Number.isInteger(out.start) && Number.isInteger(out.end), `${where}: not integers`)
  assert.ok(out.start >= 0 && out.start < out.end && out.end <= total, `${where}: outside the data`)
  assert.equal(n, expectedSpan(view, total, factor), `${where}: span`)

  const anchor = candleAt(view, r)
  const target = toRatio == null ? r : toRatio
  const fitting = []
  for (let s = 0; s + n <= total; s++) {
    if (candleAt({ start: s, end: s + n }, target) === anchor) fitting.push(s)
  }
  if (fitting.length) {
    assert.ok(fitting.includes(out.start),
      `${where}: candle ${anchor} moved from under the anchor (now ${candleAt(out, target)}), ` +
      `although start ${fitting} would have kept it`)
  } else {
    assert.ok(out.start === 0 || out.end === total,
      `${where}: no start keeps candle ${anchor} under the anchor, so the view must sit on a data edge`)
  }
  return out
}

describe('기준 캔들이 움직이지 않는다', () => {
  const RATIOS = [0, 0.1, 0.5, 0.9, 1]
  const FACTORS = [0.85, 1.15, 0.7, 1.3]
  const VIEWS = [
    [{ start: 0, end: 250 }, 250],
    [{ start: 100, end: 200 }, 500],
    [{ start: 0, end: 40 }, 250],
    [{ start: 210, end: 250 }, 250],
    [{ start: 37, end: 61 }, 1000],
  ]

  for (const [view, total] of VIEWS) {
    for (const factor of FACTORS) {
      test(`[${view.start},${view.end}) / ${total} × ${factor}`, () => {
        for (const r of RATIOS) checkZoom(view, total, factor, r)
      })
    }
  }

  test('핀치 — 처음 중점 아래 캔들이 지금 중점을 따라간다', () => {
    for (const [from, to] of [[0.5, 0.3], [0.1, 0.9], [0.9, 0.1], [0, 1], [1, 0]]) {
      checkZoom({ start: 100, end: 300 }, 1000, 0.8, from, to)
      checkZoom({ start: 100, end: 300 }, 1000, 1.25, from, to)
    }
  })
})

describe('30번 확대 뒤 30번 축소', () => {
  // 최소 폭(10)에도 데이터 끝에도 닿지 않는 크기로 고른다. 처음에 [300,700)/1000 으로
  // 썼더니 확대가 최소 폭에 붙고 축소가 전체 구간으로 넘쳐, 마지막 단언이 한 번도
  // 돌지 않았다 — 그래서 아래 전제를 먼저 확인한다.
  const total = 100_000
  const start = { start: 40_000, end: 60_000 }

  for (const r of [0, 0.1, 0.5, 0.9, 1]) {
    test(`비율 ${r} — 매 단계와 끝에서 기준 캔들이 그대로다`, () => {
      let view = start
      const anchor = candleAt(view, r)
      let narrowest = Infinity
      for (const factor of [...Array(30).fill(0.85), ...Array(30).fill(1 / 0.85)]) {
        view = checkZoom(view, total, factor, r)
        narrowest = Math.min(narrowest, view.end - view.start)
      }
      assert.ok(narrowest > 10 && view.start > 0 && view.end < total,
        `test setup: the sequence touched a limit (narrowest ${narrowest}, final ` +
        `[${view.start},${view.end})) -- the drift check below would not measure anything`)
      assert.equal(candleAt(view, r), anchor,
        `after 30 in and 30 out the candle under ratio ${r} drifted from ${anchor} to ` +
        `${candleAt(view, r)} (span ${start.end - start.start} → ${view.end - view.start})`)
    })
  }
})

describe('가장자리 클램프', () => {
  test('[0,40) 을 가운데 기준으로 두 배 축소하면 [0,80) — 데이터 밖으로 안 나간다', () => {
    assert.deepEqual(zoomAround({ start: 0, end: 40 }, 250, 2, 0.5), { start: 0, end: 80 })
  })

  test('끝에서도 같다 — [210,250) → [170,250)', () => {
    assert.deepEqual(zoomAround({ start: 210, end: 250 }, 250, 2, 0.5), { start: 170, end: 250 })
  })

  test('데이터보다 넓게는 안 된다', () => {
    assert.deepEqual(zoomAround({ start: 0, end: 200 }, 250, 3, 0.5), { start: 0, end: 250 })
  })

  test('최소 폭 아래로는 확대하지 않는다', () => {
    const out = zoomAround({ start: 100, end: 112 }, 500, 0.1, 0.5, { minSpan: 10 })
    assert.equal(out.end - out.start, 10)
  })

  test('낡은 구간(데이터가 줄어든 뒤)도 데이터 안으로 정리한다', () => {
    const out = zoomAround({ start: 400, end: 480 }, 120, 1, 0.5)
    assert.ok(out.start >= 0 && out.end <= 120 && out.start < out.end, JSON.stringify(out))
  })

  test('빈 데이터', () => {
    assert.deepEqual(zoomAround({ start: 0, end: 10 }, 0, 0.5, 0.5), { start: 0, end: 0 })
  })
})

describe('plotRatio — 플롯 폭 기준', () => {
  // 요소 [100, 600), 왼쪽 Y축 60, 오른쪽 여백 40 → 플롯 [160, 560), 폭 400
  test('Y축 폭을 빼고 잰다', () => {
    assert.equal(plotRatio(160, 100, 500, 60, 40), 0)
    assert.equal(plotRatio(360, 100, 500, 60, 40), 0.5)
    assert.equal(plotRatio(560, 100, 500, 60, 40), 1)
  })

  test('플롯 밖(축 위)은 가까운 끝에 붙는다', () => {
    assert.equal(plotRatio(120, 100, 500, 60, 40), 0)
    assert.equal(plotRatio(590, 100, 500, 60, 40), 1)
  })

  test('폭이 없으면 가운데', () => {
    assert.equal(plotRatio(300, 100, 90, 60, 40), 0.5)
  })
})
