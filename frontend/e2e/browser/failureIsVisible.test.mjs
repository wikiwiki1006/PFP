/**
 * **실패를 주입하면 화면이 실패라고 말하는가.**
 *
 * `backend/tests/test_empty_is_not_failed.py` 의 런타임판이다. 그 검사는
 * 소스를 훑어 "빈 상태를 그리면서 그 쿼리의 `isError` 를 안 읽는 자리" 를
 * 센다 — 정적이라 조건이 JSX 밖에 있거나 상위에서 처리되는 경우를 못 본다.
 * 여기서는 **실제로 서버를 실패시켜** 화면에 무엇이 뜨는지 읽는다.
 *
 * 두 경우가 같은 틀이다 — 상태코드와 화면은 다르지만 요구는 하나다:
 * **서버가 준 사유가 화면에 그대로 떠야 한다.** 한 화면에서는 "없음" 이
 * 정상 결과이기도 해서(스캔 후보 0건) 사유가 보이는지만 보고, 다른
 * 화면에서는 "없음" 이 거짓말이라 그 문구가 **없어야** 한다는 것까지 본다.
 *
 * ## 주입이 실제로 걸렸는지 먼저 본다
 *
 * 가로채기가 안 걸리면 화면은 **정상 응답**을 그리고, "없음 이 안 보인다"
 * 같은 단언이 그대로 통과한다. 아무 실패도 만들지 않은 채 초록이 된다.
 * 그래서 매 경우마다 가로챈 URL 을 세고, 0 이면 실패한다.
 *
 * ## 왜 상호작용이 필요한가
 *
 * 두 쿼리 다 화면을 열자마자 나가지 않는다 — 스캔은 탭을 눌러야, 이력은
 * "과거 레포트" 를 눌러야 나간다. 열기만 하고 재면 **요청이 없어서** 통과한다.
 */
import assert from 'node:assert/strict'
import test, { describe } from 'node:test'

import { requireApp, visit } from '../lib/app.mjs'

/** 이 문장이 화면에 그대로 떠야 한다. 앱 어디에도 없는 말이라야 한다 —
 *  흔한 문구를 쓰면 원래 있던 글자를 주입 결과로 착각한다. */
const SENTINEL = '주입한 사유 E2E-7Q4X 잠시 후 다시 시도해 주세요'

const CASES = [
  {
    id: '매매신호 스캔 503',
    route: '/timing',
    api: '**/api/signals/signal-scan*',
    status: 503,
    body: { detail: SENTINEL },
    open: async (page) => {
      await page.getByRole('button', { name: /Signal Scan/ }).first().click()
      await page.waitForTimeout(3000)
    },
    // 이 화면에서 "없음" 은 정상 결과이기도 하다("1차 필터를 통과한 매수
    // 후보가 없습니다"). 그래서 사유가 **보이는지**만 본다.
    mustNotSay: [],
  },
  {
    id: '주식 리포트 이력 500',
    route: '/lens',
    api: '**/api/reports/history*',
    status: 500,
    body: { detail: SENTINEL },
    open: async (page) => {
      // **탭이 아니라 탭 안의 버튼**을 누른다. '과거 레포트' 는 탭 이름이기도
      // 해서 이름으로 고르면 다른 화면(HistoryTab)으로 간다 — 처음에 그렇게
      // 눌렀고, 통과할 줄 알았던 검사가 엉뚱한 화면을 재고 있었다.
      await page.locator('button[title="저장된 리포트 보기"]').first().click()
      await page.waitForTimeout(3000)
    },
    // 여기서 "없음" 은 **거짓말**이다 — 사용자가 만든 리포트가 사라진 것이
    // 아니라 목록을 못 읽은 것이다.
    mustNotSay: ['저장된 주식 레포트 없음', '저장된 레포트 없음'],
  },
]

/**
 * 아직 실패라고 말하지 않는 화면. **지금 동작을 그대로 적어 둔다.**
 *
 * `backend/tests/test_empty_is_not_failed.py` 의 원장에도 같은 자리가 있다
 * (`pages/LensReport.tsx::HistoryTab::histQ`). 정적 가드가 소스에서 찾았고,
 * 여기서는 **실제로 500 을 줘서** 화면이 "저장된 레포트 없음" 을 그리는 것을
 * 확인한다. 고쳐지면 아래 검사가 빨개져서 이 항목을 위 `CASES` 로 옮기라고
 * 요구한다.
 *
 * 고칠 자리는 `frontend/src/pages/` 라 이 창 소유가 아니다. 같은 파일의
 * 주식·산업 탭은 이미 고쳐져 있다 — 그래서 위 대조군이 통과한다.
 */
const KNOWN_SILENT = [
  {
    id: '과거 레포트 탭 500 — 아직 "없음" 이라고 말한다',
    route: '/lens',
    api: '**/api/reports/history*',
    status: 500,
    body: { detail: SENTINEL },
    open: async (page) => {
      await page.getByRole('button', { name: /과거 레포트/ }).first().click()
      await page.waitForTimeout(3000)
    },
    saysInstead: '저장된 레포트 없음',
  },
]

describe('주입한 실패가 화면에 보인다', () => {
  for (const c of CASES) {
    test(c.id, async () => {
      await requireApp()
      const run = await visit(c.route, {
        signIn: true,
        inject: [{ url: c.api, status: c.status, body: c.body }],
        onPage: c.open,
      })
      try {
        assert.ok(
          run.injected.length > 0,
          `the interception never fired for ${c.api} -- nothing failed, so ` +
          `this test compared a healthy screen with itself. Either the route ` +
          `pattern is wrong or the query was not triggered by open().`,
        )

        const text = await run.page.evaluate(() => document.body.innerText || '')

        assert.ok(
          text.includes(SENTINEL),
          `the server said why it failed and the screen did not repeat it. ` +
          `The user is told nothing, or told the wrong thing. ` +
          `(서버가 사유를 줬는데 화면이 그걸 안 옮겼다.)\n` +
          `  injected: ${run.injected.length} call(s) → ${c.status}\n` +
          `  screen: ${text.replace(/\s+/g, ' ').slice(0, 300)}`,
        )

        for (const lie of c.mustNotSay) {
          assert.ok(
            !text.includes(lie),
            `the screen says "${lie}" after the request failed -- the user ` +
            `reads 'wait and retry' as 'your data is gone'.`,
          )
        }
      } finally {
        await run.close()
      }
    })
  }
})

describe('아직 실패를 숨기는 화면 — 고쳐지면 이 검사가 지우라고 한다', () => {
  for (const c of KNOWN_SILENT) {
    test(c.id, async () => {
      await requireApp()
      const run = await visit(c.route, {
        signIn: true,
        inject: [{ url: c.api, status: c.status, body: c.body }],
        onPage: c.open,
      })
      try {
        assert.ok(run.injected.length > 0, `interception never fired for ${c.api}`)
        const text = await run.page.evaluate(() => document.body.innerText || '')

        assert.ok(
          text.includes(c.saysInstead),
          `this screen no longer says "${c.saysInstead}" on a failed fetch. ` +
          `If it now reports the failure, move this entry into CASES and ` +
          `delete it from KNOWN_SILENT -- and drop the matching line from ` +
          `backend/tests/test_empty_is_not_failed.py. ` +
          `(고쳐졌으면 이 항목을 위로 옮기고 여기서 지운다.)\n` +
          `  screen: ${text.replace(/\s+/g, ' ').slice(0, 300)}`,
        )
        assert.ok(
          !text.includes(SENTINEL),
          `the reason IS shown now -- same as above: move this entry up.`,
        )
      } finally {
        await run.close()
      }
    })
  }
})

describe('대조군 — 주입하지 않으면 그 문장이 없다', () => {
  for (const c of CASES) {
    test(c.id, async () => {
      await requireApp()
      const run = await visit(c.route, { signIn: true, onPage: c.open })
      try {
        const text = await run.page.evaluate(() => document.body.innerText || '')
        // 없으면 위 검사는 "언제나 그 문장을 띄운다" 는 화면으로도 통과한다.
        assert.ok(
          !text.includes(SENTINEL),
          `the sentinel appears without any injection -- the check above ` +
          `proves nothing. (주입 안 했는데 그 문장이 있다.)`,
        )
      } finally {
        await run.close()
      }
    })
  }
})
