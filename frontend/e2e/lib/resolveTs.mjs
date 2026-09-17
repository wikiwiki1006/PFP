/**
 * 확장자 없는 상대 import 를 `.ts` 로 한 번 더 찾는 resolve 훅 — 단위 검사 전용.
 *
 * node 는 타입을 벗겨 `.ts` 를 바로 읽지만 **확장자를 추측하지 않는다.**
 * `import { getMarket } from './market'` 은 Vite·tsc 에서는 되고 node 에서는
 * `ERR_MODULE_NOT_FOUND` 다. 그 한 줄 때문에 `lib/demoData.ts` 같은 모듈을
 * 진짜 소스로 잴 수 없었다. 생산 코드에 `.ts` 를 붙이게 하는 대신 여기서
 * Vite 가 하는 일 하나만 흉내 낸다:
 *
 *   상대 경로(`./` `../`) + 확장자 없음 + 못 찾음  →  같은 경로에 `.ts` 를 붙여 다시
 *
 * 그 밖은 건드리지 않는다. `@/` 별칭 값 import, `.tsx`, enum, `import.meta.env`
 * 는 여전히 안 된다 (e2e/README.md). 못 찾은 파일은 원래 오류 그대로 올린다 —
 * 훅이 오타를 삼키면 검사가 엉뚱한 모듈을 재는 줄 모른다.
 *
 * **동적 import 로만 효과가 있다.** 정적 `import` 는 이 파일의 코드가 돌기
 * 전에 연결되므로, 훅을 건 뒤 `await import('../../src/…')` 로 불러야 한다.
 */
import { registerHooks } from 'node:module'

const RELATIVE = /^\.\.?\//
const HAS_EXTENSION = /\.[cm]?[jt]sx?$/

let registered = false

export function resolveExtensionlessTs() {
  if (registered) return
  registered = true
  registerHooks({
    resolve(specifier, context, nextResolve) {
      try {
        return nextResolve(specifier, context)
      } catch (err) {
        if (err?.code === 'ERR_MODULE_NOT_FOUND' && RELATIVE.test(specifier)
            && !HAS_EXTENSION.test(specifier)) {
          return nextResolve(`${specifier}.ts`, context)
        }
        throw err
      }
    },
  })
}
