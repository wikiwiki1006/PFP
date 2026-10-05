/**
 * components/Disclaimer.tsx
 * ─────────────────────────
 * 면책 고지와 AI 생성물 표기 — **문구는 여기 한 곳에만 있다.**
 *
 * 화면마다 따로 적으면 한 곳만 고쳐지고 나머지는 옛 문구로 남는다. 그리고
 * 이 문구들은 법에 걸린 문장이라 어긋나면 안 된다:
 *
 *  · 자본시장법 제101조의3 이 유사투자자문업자의 표시·광고에 넣으라고 정한
 *    사항(개별 상담·자금운용 불가 / 원금 손실 가능·투자자 귀속 / 유사투자자문업자라는
 *    사실)이 기준이다. 근거와 신고 후 바꿀 문구는 백엔드 사본의 주석에 있다.
 *  · 인공지능기본법 제31조 제2항은 생성형 AI 결과물에 그 사실을 표시하라고
 *    한다. 표기는 산출물의 **맨 끝**에 둔다.
 *  · 같은 문구가 PDF·마크다운 리포트에도 들어가야 한다. 그쪽은 React 가
 *    아니라 문자열이므로 상수를 쓴다. 백엔드 사본은
 *    `backend/services/disclaimer.py` — 두 곳은 **글자 단위로** 같아야 한다.
 */

/** 화면 하단·리포트 말미 — **모든 자리에 이 한 문구**를 쓴다.
 *
 *  예전에는 모바일용 짧은 문구(DISCLAIMER_SHORT)가 따로 있었는데, 거기에는 법정
 *  사항 두 가지(개별 상담·자금운용 불가, 원금 손실 가능)가 모두 빠져 있었다 —
 *  모바일 이용자는 필수 사항을 한 번도 보지 못했다. 지금 문구는 짧아서 나눌
 *  이유가 없다.
 *
 *  문장 경계에서만 나눠 이어 붙인다 — 예전에 '투자목적·' 에서 끊어 이어 붙였다가
 *  백엔드 문구에 없는 공백이 끼었다. */
export const DISCLAIMER_TEXT =
  '본 서비스 운영자는 금융투자업자가 아니며 개별적인 투자 상담과 자금운용이 불가능합니다. ' +
  '제공 정보는 매매 권유가 아닌 투자 참고자료이며, 투자 시 원금 손실이 발생할 수 있고 ' +
  '그 손실은 투자자에게 귀속됩니다.'

/** 백엔드가 리포트 본문 끝에 붙이는 면책 블록의 표지 (disclaimer.py DISCLAIMER_MARKER). */
export const DISCLAIMER_MARKER = '[투자 유의사항]'

// ── AI 생성물 표기 ───────────────────────────────────────────────────────────
// 모든 문장이 '※' 로 시작하고 AI_LABEL_MARKER 를 담는다 — '이미 붙었는가' 판정은
// **마지막 줄**이 그 모양인지로 한다 (`endsWithAiLabel`). 본문 어딘가에 같은 단어가
// 있는지로 판정하면 안 된다: 본문에는 LLM 이 쓴 요약이 있고, 인공지능기본법 기사를
// 요약하면 'AI 생성물' 이 나온다. 그러면 맨 끝 표기가 빠진 채 나갔다.
export const AI_LABEL_MARKER = 'AI 생성물'
export const AI_LABEL_REPORT = '※ 이 리포트는 생성형 AI가 작성한 AI 생성물입니다.'
export const AI_LABEL_SCENARIO = '※ 이 시나리오 분석은 생성형 AI가 작성한 AI 생성물입니다.'
// 전날 브리핑은 본문 전체를 모델이 쓴다 — backend/services/disclaimer.py 와 같은 문장.
export const AI_LABEL_BRIEF = '※ 이 브리핑은 생성형 AI가 작성한 AI 생성물입니다.'
export const AI_LABEL_OPTIMIZER_VIEW =
  '※ AI 뷰(종목별 기대수익률·신뢰도·근거)는 생성형 AI가 작성한 AI 생성물입니다.'

/** 백엔드가 붙인 면책·AI 표기 블록을 본문에서 걷어낸다.
 *
 *  화면은 고지를 **자기 자리**(항상 보이는 하단)에 그린다. 백엔드 블록은 리포트의
 *  마지막 섹션 안으로 들어가는데, 섹션은 접혀 있을 수 있어 그 자리에 의존하면
 *  고지가 안 보일 수 있다. 블록을 남겨 두면 펼쳤을 때 같은 고지가 두 번 나온다. */
export function stripAppendix(md: string): string {
  if (!md) return md
  let out = md.trimEnd()
  // 끝 줄이 AI 생성물 표기면 걷는다 (블록 뒤에 붙는다).
  const lines = out.split('\n')
  if (endsWithAiLabel(out)) out = lines.slice(0, -1).join('\n').trimEnd()
  // 면책 블록은 **그것이 끝 줄일 때만** 걷는다. 예전에는 표지 문자열이 어디 있든 그
  // 줄부터 끝까지 잘랐다 — 본문이 '[투자 유의사항]' 을 인용하면 그 아래 본문이 통째로
  // 사라졌다(실제 node 대조에서 확인). 백엔드 `_strip_tail_appendix` 와 같은 규칙이다.
  const head = '\n> **' + DISCLAIMER_MARKER + '**'
  const i = out.lastIndexOf(head)
  if (i >= 0 && !out.slice(i + 1).includes('\n')) {
    out = out.slice(0, i).trimEnd()
    if (out.endsWith('---')) out = out.slice(0, -3).trimEnd()
  }
  return out
}

/** 마지막 줄이 AI 생성물 표기인가. 본문 중간의 같은 단어는 세지 않는다. */
export function endsWithAiLabel(md: string): boolean {
  const lines = (md || '').trimEnd().split('\n')
  const last = (lines[lines.length - 1] || '').trim()
  return last.startsWith('※') && last.includes(AI_LABEL_MARKER)
}

type Props = {
  className?: string
}

export default function Disclaimer({ className }: Props) {
  return (
    <div
      role="note"
      className={
        'text-[10px] leading-relaxed text-[#475569] ' + (className ?? '')
      }
    >
      {DISCLAIMER_TEXT}
    </div>
  )
}

/** AI 생성물 표기 한 줄. 산출물의 **맨 끝**에 둔다. */
export function AiGeneratedNote({ text, className }: { text: string; className?: string }) {
  return (
    <div role="note" className={'text-[10px] leading-relaxed text-[#64748b] ' + (className ?? '')}>
      {text}
    </div>
  )
}
