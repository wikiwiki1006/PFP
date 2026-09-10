/**
 * lib/markdown.ts
 * ───────────────
 * 리포트 본문을 그릴 때 쓰는 마크다운 설정.
 *
 * remark-gfm 은 기본적으로 **홑물결표 하나짜리 취소선**(`~취소~`)을 지원한다.
 * GFM 명세에는 없지만 github.com 이 받아 주기 때문에 기본값이 켜져 있다.
 *
 * 한국어 리포트에서는 이게 심각한 오작동을 만든다. 물결표가 범위 기호로
 * 흔히 쓰이기 때문이다 — "USD 14.3B~USD 35B", "향후 5~10년". 한 문단에
 * 물결표가 두 번 나오면 그 사이가 통째로 취소선이 되고, 물결표 자체도
 * 사라진다:
 *
 *   USD 14.3B~USD 35B ... 향후 5~10년
 *   → USD 14.3B<del>USD 35B ... 향후 5</del>10년
 *
 * 화면에서는 "14.3BUSD 35B", "510년" 처럼 **숫자가 붙어 버려** 잘못된 수치로
 * 읽히고, 가로줄까지 그어져 "틀린 내용"으로 오해된다. 실제 값은 멀쩡하다.
 *
 * 취소선이 필요하면 `~~두 개~~` 를 쓰면 되므로 잃는 것은 없다.
 */
import remarkGfm from 'remark-gfm'
import type { PluggableList } from 'unified'

export const MARKDOWN_PLUGINS: PluggableList = [[remarkGfm, { singleTilde: false }]]
