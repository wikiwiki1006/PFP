/**
 * 익명으로 다섯 화면을 열었을 때 — **버그를 몰라도 잡히는 것**들.
 *
 * 특정 쿼리를 지목하지 않고 **부류 전체**를 잡는 단언이다. 새 화면이나 새
 * 쿼리가 생겨도 그대로 적용되고, 그게 이런 단언을 두는 이유다.
 *
 *   G1  익명 실행 중 401/403 응답 0건
 *       비로그인 401 이 실제로 재발한 적이 있다. 로그인 유도가 아니라
 *       **요청이 나가서 거절당하는** 상태다.
 *
 *   G2  콘솔 error/warning 이 원장에 없는 것으로 늘지 않는다
 *       React key 경고 같은 것이 여기 걸린다. **어디인지는 말하지 않는다** —
 *       위치를 짚으려다 두 번 오진한 적이 있다. 가드는 "있다" 까지만 말한다.
 */
import assert from 'node:assert/strict'
import test, { after, before, describe } from 'node:test'

import { requireApp, visit } from '../lib/app.mjs'

const ROUTES = ['/terminal', '/macro', '/optimizer', '/timing', '/lens']

// 브라우저를 화면당 한 번만 띄운다. 단언마다 띄우면 몇 초씩 붙는다.
const runs = new Map()

before(async () => {
  await requireApp()
  for (const route of ROUTES) runs.set(route, await visit(route))
})

after(async () => {
  for (const r of runs.values()) await r.close()
})


describe('G1 · 익명 실행에 401/403 이 없다', () => {
  for (const route of ROUTES) {
    test(route, () => {
      const denied = runs.get(route).responses
        .filter(r => r.status === 401 || r.status === 403)
      assert.deepEqual(
        denied, [],
        `an anonymous visit to ${route} was denied by the server -- the page ` +
        `should ask for login instead of firing a request that gets refused:\n` +
        denied.map(d => `  ${d.status} ${d.method} ${d.url}`).join('\n'),
      )
    })
  }

  test('전제 — API 를 실제로 불렀다', () => {
    // 요청이 아예 안 나갔으면 "401 0건" 은 공허하다. 백엔드가 죽어 있거나
    // 프록시가 끊겼을 때가 그 상태인데, 위 다섯 건은 그래도 전부 통과한다.
    const apiCalls = [...runs.values()]
      .flatMap(r => r.responses.filter(x => x.url.includes('/api/')))
    assert.ok(
      apiCalls.length > 0,
      'no /api/ request was made at all -- "no 401" above measured nothing. ' +
      '(요청이 안 나갔으면 거절도 없다.)',
    )
    const served = apiCalls.filter(x => x.status < 500)
    assert.ok(
      served.length > 0,
      `every /api/ call failed (${apiCalls.map(x => x.status).join(',')}) -- ` +
      'the backend is not really answering.',
    )
  })
})


// 원장 — **지금 나는** 콘솔 메시지. 새로 생기면 실패하고, 사라지면 그것도
// 실패한다(줄을 지우게 만든다). 여기 적힌 둘은 이 리포의 결함이 아니라
// react-router 가 다음 메이저를 예고하는 알림이다. 옵트인 플래그를 켜거나
// v7 로 올리면 사라지고, 그때 이 줄을 지우면 된다.
const KNOWN_CONSOLE = [
  { pattern: /React Router Future Flag Warning: .*startTransition/,
    why: 'react-router v7 예고 — v7_startTransition 플래그를 켜면 사라진다' },
  { pattern: /React Router Future Flag Warning: .*relativeSplatPath/,
    why: 'react-router v7 예고 — v7_relativeSplatPath 플래그를 켜면 사라진다' },
]

describe('G2 · 콘솔이 조용하다', () => {
  for (const route of ROUTES) {
    test(route, () => {
      const r = runs.get(route)
      const unexpected = r.console
        .filter(c => !KNOWN_CONSOLE.some(k => k.pattern.test(c.text)))

      assert.deepEqual(
        unexpected.map(c => `[${c.type}] ${c.text.slice(0, 200)}`), [],
        `${route} logged console output that is not in the ledger. This guard ` +
        `does not say where it comes from -- pointing at a line was wrong ` +
        `twice. Open the page and read the stack.`,
      )
      assert.deepEqual(
        r.pageErrors, [],
        `${route} threw an uncaught exception -- React unmounts the subtree ` +
        `and the user sees a blank panel with no explanation.`,
      )
    })
  }

  test('원장이 문제보다 오래 남지 않는다', () => {
    // 한 방향만 검사하면 목록이 조용히 낡는다. 고친 사람은 초록을 보고
    // 지나가고, 다음 사람은 이 줄을 읽고 "아직 난다" 고 믿는다.
    const seen = [...runs.values()].flatMap(r => r.console).map(c => c.text)
    const gone = KNOWN_CONSOLE.filter(k => !seen.some(t => k.pattern.test(t)))
    assert.deepEqual(
      gone.map(k => k.why), [],
      'these console messages no longer appear but are still listed in ' +
      'KNOWN_CONSOLE -- delete the entries so the ledger keeps meaning what ' +
      'it says.',
    )
  })
})
