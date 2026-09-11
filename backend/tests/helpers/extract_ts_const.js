// 실제 .tsx 파일에서 최상위 `const <NAME> ... = { ... }` 객체 리터럴을 꺼내
// JSON 으로 출력한다.
//
// 왜 node 인가: 이 값을 파이썬으로 **옮겨 적고** 비교하면, 옮기면서 잘못 읽은
// 것은 양쪽에 똑같이 들어가 검사가 볼 수 없다. 그건 "내가 일관되게 읽었는가"
// 만 검증하는 것이다 (CLAUDE.md §6). 그래서 리터럴을 실제 파일에서 잘라내
// **자바스크립트 엔진에게 직접 평가시킨다** — 주석·후행 쉼표·따옴표 규칙을
// 내가 다시 구현하지 않는다.
//
//   node extract_ts_const.js <파일> <상수이름>
//
// 표준 출력: JSON 객체. 못 찾으면 종료 코드 1 + 표준 오류에 이유.

const fs = require('fs')
const vm = require('vm')

const [file, name] = process.argv.slice(2)
if (!file || !name) {
  console.error('usage: extract_ts_const.js <file> <constName>')
  process.exit(2)
}

const src = fs.readFileSync(file, 'utf8')

// `const NAME` 뒤의 첫 `=` 와 그 뒤의 첫 `{` 를 찾는다. 타입 주석
// (`: Record<string, string>`)이 사이에 끼는 것을 그대로 건너뛴다.
const decl = new RegExp(`\\bconst\\s+${name}\\b`)
const at = src.search(decl)
if (at < 0) {
  console.error(`const ${name} not found in ${file}`)
  process.exit(1)
}
const open = src.indexOf('{', at)
if (open < 0) {
  console.error(`no object literal after const ${name}`)
  process.exit(1)
}

// 중괄호 짝을 세어 끝을 찾는다. 문자열 안의 중괄호를 세지 않도록 따옴표를
// 추적한다 — 정규식으로 끝을 찾으면 값에 `}` 가 들어간 순간 틀린다.
let depth = 0
let quote = null
let end = -1
for (let i = open; i < src.length; i++) {
  const c = src[i]
  if (quote) {
    if (c === '\\') { i++; continue }
    if (c === quote) quote = null
    continue
  }
  if (c === '"' || c === "'" || c === '`') { quote = c; continue }
  if (c === '{') depth++
  else if (c === '}') {
    depth--
    if (depth === 0) { end = i; break }
  }
}
if (end < 0) {
  console.error(`unbalanced braces after const ${name}`)
  process.exit(1)
}

const literal = src.slice(open, end + 1)
const value = vm.runInNewContext(`(${literal})`)
process.stdout.write(JSON.stringify(value))
