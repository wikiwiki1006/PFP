/**
 * 단위 검사용 resolve 훅(`e2e/lib/resolveTs.mjs`) 자기검사 — 답을 아는 모듈로.
 *
 * 훅이 너무 넓으면 **없는 파일을 있는 것처럼** 만들거나 다른 파일을 불러와,
 * 그 훅을 쓰는 검사가 엉뚱한 소스를 잰다. 너무 좁으면 `demoData.ts` 같은 모듈을
 * 못 부른다. 양쪽을 잰다.
 */
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import test, { before, describe } from 'node:test'

import { resolveExtensionlessTs } from '../lib/resolveTs.mjs'

let dir

before(() => {
  resolveExtensionlessTs()
  dir = mkdtempSync(join(tmpdir(), 'resolve-ts-'))
  writeFileSync(join(dir, 'b.ts'), 'export const b: number = 2\n')
  writeFileSync(join(dir, 'a.ts'), "import { b } from './b'\nexport const a = b + 1\n")
  writeFileSync(join(dir, 'missing.ts'), "import { nope } from './nope'\nexport const x = nope\n")
  writeFileSync(join(dir, 'bare.ts'), "import { y } from 'no-such-package-xyz'\nexport const z = y\n")
})

const load = (name) => import(pathToFileURL(join(dir, name)).href)

describe('확장자 없는 상대 import', () => {
  test('같은 이름의 .ts 를 찾는다', async () => {
    const mod = await load('a.ts')
    assert.equal(mod.a, 3)
  })

  test('그런 .ts 도 없으면 원래 오류 그대로다', async () => {
    await assert.rejects(load('missing.ts'), (err) => err.code === 'ERR_MODULE_NOT_FOUND')
  })

  test('상대 경로가 아닌 import 는 건드리지 않는다', async () => {
    await assert.rejects(load('bare.ts'), (err) => err.code === 'ERR_MODULE_NOT_FOUND')
  })
})
