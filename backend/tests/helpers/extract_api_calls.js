// 프론트 소스에서 API 호출 지점을 **TypeScript 파서로** 꺼낸다.
//
//   node extract_api_calls.js <파일|디렉터리> [...]
//
// 표준 출력 (JSON):
//   {
//     "calls": [{ "file", "line", "method", "parts": [...], "spans": [null | [...]] }],
//     "stray": [{ "file", "line", "text" }]
//   }
//
// 왜 정규식이 아닌가: 주석 속 호출, 문자열 속 예시, 여러 줄에 걸친 인자,
// 템플릿 리터럴의 `${}` 를 줄 단위로 가르면 반드시 어딘가 틀린다. 파서는
// 주석을 노드로 만들지 않고, 인자의 경계를 안다.
//
// calls — `<무엇이든>.<get|post|put|patch|delete|head|options>('/api/...', ...)`.
//   parts 는 템플릿의 고정 조각, spans 는 조각 사이 `${…}` 자리마다 가능한 값.
//   `(path: 'naver' | 'kakao') => api.post(`/api/auth/${path}`)` 처럼 그 자리가
//   **매개변수이고 타입이 문자열 리터럴 유니언**이면 그 값들을 준다. 아니면
//   null — 아무 한 조각이라는 뜻이다.
//
// stray — '/api/' 로 시작하는 리터럴인데 위 모양의 첫 인자가 **아닌** 것.
//   경로를 변수에 담아 넘기거나 `fetch` 로 부르면 여기로 온다. 추출기가 모르는
//   모양을 조용히 빼먹으면 "전부 살아 있다" 가 **못 본 것은 셈하지 않아서** 나온다.
//   그걸 막는 칸이다.

const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..', '..', '..')

let ts
try {
  ts = require(path.join(ROOT, 'frontend', 'node_modules', 'typescript'))
} catch (e) {
  console.error(`cannot load typescript from frontend/node_modules: ${e.message}`)
  process.exit(2)
}

const METHODS = new Set(['get', 'post', 'put', 'patch', 'delete', 'head', 'options'])

function headText(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text
  if (ts.isTemplateExpression(node)) return node.head.text
  return null
}

function isApiLiteral(node) {
  const head = headText(node)
  return head !== null && head.startsWith('/api/')
}

function literalUnion(typeNode) {
  if (!typeNode) return null
  const members = ts.isUnionTypeNode(typeNode) ? typeNode.types : [typeNode]
  const out = []
  for (const m of members) {
    if (ts.isLiteralTypeNode(m) && ts.isStringLiteral(m.literal)) out.push(m.literal.text)
    else return null
  }
  return out.length ? out : null
}

// `${ident}` 의 ident 가 둘러싼 함수의 매개변수면 그 타입에서 값들을 읽는다.
function spanValues(expr) {
  if (!ts.isIdentifier(expr)) return null
  for (let n = expr.parent; n; n = n.parent) {
    if (ts.isFunctionLike(n) && n.parameters) {
      for (const p of n.parameters) {
        if (ts.isIdentifier(p.name) && p.name.text === expr.text) return literalUnion(p.type)
      }
    }
  }
  return null
}

function scanFile(file, out) {
  const src = fs.readFileSync(file, 'utf8')
  const kind = file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, kind)
  const rel = path.relative(ROOT, file).split(path.sep).join('/')
  const lineOf = (node) => sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
  const claimed = new Set()

  const visit = (node) => {
    if (ts.isCallExpression(node)
        && ts.isPropertyAccessExpression(node.expression)
        && METHODS.has(node.expression.name.text)
        && node.arguments.length > 0
        && isApiLiteral(node.arguments[0])) {
      const arg = node.arguments[0]
      claimed.add(arg)
      const parts = []
      const spans = []
      if (ts.isTemplateExpression(arg)) {
        parts.push(arg.head.text)
        for (const s of arg.templateSpans) {
          spans.push(spanValues(s.expression))
          parts.push(s.literal.text)
        }
      } else {
        parts.push(arg.text)
      }
      out.calls.push({
        file: rel, line: lineOf(node), method: node.expression.name.text.toUpperCase(), parts, spans,
      })
    } else if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)
                || ts.isTemplateExpression(node))
               && isApiLiteral(node) && !claimed.has(node)) {
      out.stray.push({ file: rel, line: lineOf(node), text: node.getText(sf).slice(0, 120) })
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
}

function walk(target, out) {
  const st = fs.statSync(target)
  if (st.isFile()) {
    scanFile(target, out)
    return
  }
  for (const entry of fs.readdirSync(target, { withFileTypes: true })) {
    if (entry.name === 'node_modules') continue
    const p = path.join(target, entry.name)
    if (entry.isDirectory()) walk(p, out)
    else if (/\.(ts|tsx)$/.test(entry.name)) scanFile(p, out)
  }
}

const targets = process.argv.slice(2)
if (!targets.length) {
  console.error('usage: extract_api_calls.js <file-or-dir> [...]')
  process.exit(2)
}
const out = { calls: [], stray: [] }
for (const t of targets) walk(path.resolve(t), out)
process.stdout.write(JSON.stringify(out))
