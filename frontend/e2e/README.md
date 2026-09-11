# frontend/e2e

브라우저와 순수 함수를 **실물로** 재는 자리.

```bash
cd frontend
node --test "e2e/unit/**/*.test.mjs"      # 브라우저 불필요, 항상 돌 수 있다
node --test "e2e/browser/**/*.test.mjs"   # dev 서버가 떠 있어야 한다 (아래 참고)
```

디렉터리를 그대로 넘기는 `node --test e2e/unit` 은 이 환경(Windows·node 24)
에서 그 경로를 **실행할 모듈**로 읽고 `MODULE_NOT_FOUND` 로 죽는다. 글로브를
따옴표로 감싸 넘긴다.

## 왜 `@playwright/test` 가 아닌가

`frontend/package.json` 의 devDependency 는 `playwright` (라이브러리) 하나다.
러너인 `@playwright/test` 도, `vitest` 도 없다. **venv 와 node_modules 는 창
다섯이 공유하므로 여기서 `npm install` 을 하지 않는다** (CLAUDE.md §7.6) —
한 창이 올린 패키지가 다섯 창에 즉시 반영되고, 다른 창의 테스트가 깨지면
원인을 자기 코드에서 찾게 된다.

그래서 **node 내장 러너**(`node --test`, `node:assert`)를 쓴다. 새 의존성이
0개고, 러너를 바꿔도 단언은 그대로 남는다.

## 왜 브라우저 바이너리를 안 받는가

`npx playwright install` 은 수백 MB 를 받고 공유 `node_modules` 를 건드린다.
대신 **설치된 Chrome 을 채널로** 쓴다 (CLAUDE.md §9):

```js
chromium.launch({ channel: 'chrome', headless: true })
```

Chrome 이 없는 머신이면 `channel: 'msedge'` 로 바꾼다.

## 왜 순수 함수 테스트가 e2e 폴더에 있는가

이 창이 소유한 프론트 경로가 `frontend/e2e/` 하나뿐이다 (§7.2).
`e2e/unit/` 은 브라우저를 안 띄우므로 **이름과 달리 e2e 가 아니다** —
경로 소유 때문에 여기 있다. 러너가 분리돼 있어 섞이지는 않는다.

## 핸드오프 게이트에는 `e2e/browser` 가 들어가지 않는다

게이트는 지금도 `pytest backend/tests -q` 와 `npm run build` 다 (§7.5).
`e2e/unit` 은 아무 데서나 돌므로 거기 붙여도 되지만, `e2e/browser` 는
**서버가 떠 있어야만** 뜻이 있고 안 떠 있으면 실패한다. 그 실패를 게이트에
넣으면 서버를 안 띄운 창이 자기 코드를 의심하게 된다.

브라우저 검사는 화면을 건드린 뒤에 직접 돌린다.

## 이 검사들이 **안** 덮는 것

가드가 있다는 사실이 "그 부류는 덮였다" 로 읽히면, 안 덮인 자리를 아무도
다시 안 본다. 그래서 경계를 여기 적어 둔다.

**G2(콘솔)는 익명 실행만 덮는다.** 로그인해야 데이터가 생기고, 데이터가
있어야 나는 경고(React key 등)는 이 검사에 **안 걸린다.** 실제로 로그인
실행에서 재 봤을 때 key 경고는 나지 않았다 — 테스트 계정에 보유가 없어서다.

로그인까지 넓히려면 포트폴리오 시드가 선행인데, 그러면 이 검사가 DB 상태에
의존하게 된다. 시드가 조용히 어긋나면 "경고 0건" 이 **데이터가 없어서**
나오고, 그건 이 폴더가 없애려는 형태 그대로다. 넓힐 때는 **시드가 실제로
들어갔는지를 재는 대조군**이 먼저 있어야 한다.

(지금은 보유 없는 계정이 `/metrics`·`/equity-curve` 400 을 받아 콘솔에
`Failed to load resource` 를 두 개 남긴다. 화면 동작은 맞다 —
`isEmptyPortfolioError` 가 처리한다.)

**G3(시장 분리)는 `/terminal` 만, 본문만 본다.** 상단 마퀴는 §1.4 가 명시한
예외라 제외하고, `VIX` 는 라벨이 "변동성 (미국 VIX)" 라 제외한다. 나머지 네
화면은 아직 안 잰다.

**주입 검사는 두 엔드포인트뿐이다.** 같은 틀을 다른 엔드포인트에 돌리면
부류 단언이 되지만, 지금은 스캔과 리포트 이력 둘만 덮는다.

## dev 서버

`e2e/browser` 는 **백엔드와 프론트가 같이 떠 있어야** 뜻이 있다. 프론트만
띄우면 API 호출이 네트워크 오류로 끝나는데, 그러면 "401 이 없다" 같은
단언이 **응답 자체가 없어서** 통과한다 — 재려는 것을 안 재고 초록이 된다.
그래서 `waitForApp()` 이 둘 다 확인하고, 아니면 **skip 이 아니라 실패**한다.

이 창(테스트)은 슬롯 3 이다:

```bash
./dev.sh --slot 3 --db-branch test --auth-emulator
# 프론트 :3003 · 백엔드 :8003
```

`E2E_BASE_URL` / `E2E_API_URL` 로 주소를 바꿀 수 있다.
