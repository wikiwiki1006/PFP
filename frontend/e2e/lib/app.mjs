/**
 * 브라우저 검사 공용 배선.
 *
 * 여기서 제일 중요한 것은 `requireApp()` 이 **skip 이 아니라 throw** 라는
 * 점이다. 프론트만 떠 있고 백엔드가 죽어 있으면 API 호출이 네트워크 오류로
 * 끝나는데, 그 상태에서 "401 응답이 없다"·"콘솔 오류가 없다" 는 단언은
 * **응답과 렌더가 애초에 없어서** 통과한다. 재려던 것을 안 재고 초록이 된다.
 * skip 은 그 구분을 로그에서 지운다.
 */
import { chromium } from 'playwright'

export const BASE_URL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:3003'
export const API_URL = process.env.E2E_API_URL ?? 'http://127.0.0.1:8003'

/** 설치된 Chrome 을 채널로 쓴다 — 브라우저 바이너리를 받지 않는다 (§9). */
const CHANNEL = process.env.E2E_CHANNEL ?? 'chrome'

async function reachable(url, { timeoutMs = 2000 } = {}) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
    return r.status
  } catch {
    return null
  }
}

/**
 * 프론트와 백엔드가 **둘 다** 떠 있어야 한다.
 *
 * 백엔드만 빠져도 화면은 뜨므로, 프론트만 확인하면 반쪽 상태를 정상으로
 * 읽는다. 그 반쪽에서는 이 폴더의 단언 대부분이 공허하게 통과한다.
 */
export async function requireApp() {
  const fe = await reachable(BASE_URL)
  const be = await reachable(`${API_URL}/api/health`) ?? await reachable(API_URL)
  if (fe === null || be === null) {
    throw new Error(
      `the app is not running (frontend ${BASE_URL}=${fe ?? 'down'}, ` +
      `backend ${API_URL}=${be ?? 'down'}) -- these checks are deliberately ` +
      `a failure rather than a skip: with the backend down the page still ` +
      `renders and every assertion here passes while measuring nothing.\n` +
      `  ./dev.sh --slot 3 --db-branch test --auth-emulator\n` +
      `  (E2E_BASE_URL / E2E_API_URL 로 주소를 바꿀 수 있다)`,
    )
  }
}

export const TEST_EMAIL = process.env.E2E_EMAIL ?? 'test@gmail.com'
export const TEST_PASSWORD = process.env.E2E_PASSWORD ?? '10october@'

/**
 * 앱의 **자기 로그인 폼**으로 들어간다.
 *
 * 토큰을 만들어 스토리지에 심는 방법도 있지만, 그러면 이 리포가 실제로 쓰는
 * 경로(커스텀 토큰 → `signInWithCustomToken`)를 건너뛴다. 그 경로가 깨져도
 * 검사는 계속 초록이고, 정작 사용자는 못 들어온다.
 *
 * 인증은 **에뮬레이터**에 붙어야 한다 (§7.8). 운영 Firebase 에 붙으면 이
 * 검사가 실계정을 건드린다. 그래서 붙은 곳이 에뮬레이터인지 확인하고,
 * 아니면 로그인을 시도하지 않고 실패한다.
 */
export async function login(page) {
  const emulatorCalls = []
  page.on('request', (r) => {
    if (r.url().includes('9099')) emulatorCalls.push(r.url())
  })

  await page.getByRole('button', { name: '로그인', exact: true }).first().click()
  // 이 폼의 입력칸에는 placeholder 가 없다 — type 으로 고른다.
  await page.locator('input[type="email"]').first().fill(TEST_EMAIL)
  await page.locator('input[type="password"]').first().fill(TEST_PASSWORD)
  await page.locator('input[type="password"]').first().press('Enter')

  await page.waitForFunction(
    () => ![...document.querySelectorAll('button')]
      .some(b => (b.innerText || '').trim() === '로그인'),
    null, { timeout: 20000 },
  ).catch(() => { /* 아래 단언이 더 나은 메시지를 낸다 */ })

  const stillAnonymous = await page.evaluate(() =>
    [...document.querySelectorAll('button')]
      .some(b => (b.innerText || '').trim() === '로그인'))
  if (stillAnonymous) {
    throw new Error(
      `login did not complete for ${TEST_EMAIL} -- the auth emulator must be ` +
      `up (127.0.0.1:9099) and seeded (./seed-test-user.sh), and both servers ` +
      `must have been started with the emulator env (§7.8).`)
  }
  if (emulatorCalls.length === 0) {
    throw new Error(
      'login succeeded without touching the auth emulator -- this ran against ' +
      'the production Firebase project. Restart with ' +
      'FIREBASE_AUTH_EMULATOR_HOST / VITE_USE_AUTH_EMULATOR set in the ' +
      'process environment (never in a file, §7.8).')
  }
}

/**
 * 페이지 하나를 열고, 그 동안의 **콘솔 기록과 응답 상태**를 모아 돌려준다.
 *
 * 두 가지를 한 번에 모으는 이유는 브라우저를 한 번만 띄우기 위해서다.
 * 검사마다 띄우면 한 번에 몇 초씩 붙는다.
 */
export async function visit(path, {
  market, settleMs = 3500, onPage, signIn = false, inject = [],
} = {}) {
  const browser = await chromium.launch({ channel: CHANNEL, headless: true })
  const context = await browser.newContext({ locale: 'ko-KR' })
  const page = await context.newPage()

  const console_ = []
  const pageErrors = []
  const responses = []

  page.on('console', (msg) => {
    const type = msg.type()
    if (type === 'error' || type === 'warning') {
      console_.push({ type, text: msg.text(), url: msg.location()?.url ?? '' })
    }
  })
  // `pageerror` 는 잡히지 않은 예외다. `console` 로는 안 오는 경우가 있다.
  page.on('pageerror', (err) => pageErrors.push(String(err)))
  page.on('response', (res) => {
    responses.push({ url: res.url(), status: res.status(), method: res.request().method() })
  })

  try {
    if (market) {
      // 시장 선택은 sessionStorage 에 산다 (lib/marketStorage.ts). 페이지가
      // 뜨기 **전에** 심어야 첫 렌더부터 그 시장으로 그려진다 — 열고 나서
      // 바꾸면 전환 새로고침이 끼어 무엇을 쟀는지 흐려진다.
      await context.addInitScript((m) => {
        try { window.sessionStorage.setItem('pfp_market', m) } catch { /* 무시 */ }
        try { window.localStorage.setItem('pfp_market', m) } catch { /* 무시 */ }
      }, market)
    }
    await page.goto(`${BASE_URL}${path}`, { waitUntil: 'domcontentloaded' })
    // 초기 쿼리가 돌 시간을 준다. `networkidle` 은 이 앱에서 안 온다 —
    // 마퀴·실시간 폴링이 계속 돌아서 idle 상태가 존재하지 않는다.
    await page.waitForTimeout(settleMs)

    if (signIn) {
      await login(page)
      await page.waitForTimeout(settleMs)
    }

    // 주입은 **로그인 뒤에** 건다. 앞에 걸면 인증 왕복까지 같이 가로채서
    // "로그인이 안 된 화면" 을 "실패를 그린 화면" 으로 착각하게 된다.
    const injected = []
    for (const rule of inject) {
      await page.route(rule.url, async (route) => {
        injected.push(route.request().url())
        await route.fulfill({
          status: rule.status,
          contentType: 'application/json',
          body: JSON.stringify(rule.body ?? { detail: 'injected' }),
        })
      })
    }
    if (inject.length) {
      // 주입한 규칙이 걸리려면 그 쿼리가 **다시** 나가야 한다. 화면을 다시
      // 그리는 대신 새로고침한다 — 로그인은 IndexedDB 에 남아 유지된다.
      await page.reload({ waitUntil: 'domcontentloaded' })
      await page.waitForTimeout(settleMs)
    }

    const extra = onPage ? await onPage(page) : undefined
    return { console: console_, pageErrors, responses, page, extra, injected,
             html: await page.content(),
             close: async () => { await browser.close() } }
  } catch (e) {
    await browser.close()
    throw e
  }
}

/** 상단 마퀴를 **뺀** 본문 텍스트.
 *
 * §1.4 는 마퀴를 명시적 예외로 둔다 — "상단 마퀴의 WTI·BTC 등 글로벌
 * 지표는 시장과 무관하게 `$` 가 맞다". 마퀴를 포함해 재면 **올바른 코드가
 * 영구히 빨간불**이 된다. denylist 를 아무리 잘 골라도 범위가 틀리면
 * 소용이 없다.
 */
export async function bodyTextExcludingMarquee(page) {
  return page.evaluate(() => {
    const root = document.body.cloneNode(true)
    for (const el of root.querySelectorAll('.animate-marquee')) {
      // 마퀴 컨테이너째로 들어낸다. 자식 텍스트만 지우면 래퍼에 남은
      // 문자열이 그대로 남는다.
      el.remove()
    }
    return root.innerText ?? root.textContent ?? ''
  })
}
