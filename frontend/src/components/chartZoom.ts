/**
 * components/chartZoom.ts
 * ───────────────────────
 * 캔들 차트의 확대/축소 — **기준점이 화면에서 움직이지 않게** 보이는 구간을 정한다.
 *
 * 예전에는 항상 보이는 구간의 가운데를 기준으로 줄였다. 그래서 차트 왼쪽 끝의
 * 캔들을 보려고 그 위에서 휠을 굴리면 그 캔들이 화면 밖으로 밀려났다 (실측:
 * 1년 차트의 10% 지점에서 네 번 굴리면 커서 아래가 2025-10-29 → 2026-01-05).
 *
 * 규칙은 하나다 — 확대 전에 기준점 아래 있던 캔들이 확대 후에도 기준점 아래
 * 있다. 기준점은 휠이면 커서, 핀치면 두 손가락의 중점, 버튼이면 가운데(0.5).
 * 데이터 시작/끝에 닿으면 그 규칙보다 "구간이 데이터 밖으로 나가지 않는다" 가
 * 우선한다 (클램프).
 *
 * React·경로 별칭을 import 하지 않는다 — node 가 이 파일을 그대로 읽어 테스트할
 * 수 있어야 한다 (CLAUDE.md §6: 옮겨 적은 사본이 아니라 진짜 소스를 부른다).
 */

/** 보이는 구간 — 캔들 인덱스 [start, end). end 는 포함하지 않는다. */
export interface ViewRange {
  start: number
  end: number
}

/**
 * 화면 x 좌표가 플롯 폭에서 차지하는 비율 (0 = 플롯 왼쪽 끝, 1 = 오른쪽 끝).
 *
 * 플롯은 차트 요소 전체가 아니다 — 왼쪽에 Y축(`axisLeft`), 오른쪽에 여백
 * (`marginRight`)이 있다. 요소 폭으로 나누면 Y축 폭만큼 기준점이 어긋난다.
 * 플롯 밖(축 위)에서 굴리면 가까운 끝으로 붙인다.
 */
export function plotRatio(
  clientX: number,
  rectLeft: number,
  rectWidth: number,
  axisLeft: number,
  marginRight: number,
): number {
  const width = rectWidth - axisLeft - marginRight
  if (!(width > 0)) return 0.5
  const r = (clientX - (rectLeft + axisLeft)) / width
  return Math.max(0, Math.min(1, r))
}

export interface ZoomOptions {
  /** 이보다 적게는 확대하지 않는다 (데이터가 이보다 적으면 전체). */
  minSpan?: number
  /**
   * 기준 캔들이 확대 후에 놓일 비율. 생략하면 `anchorRatio` 그대로 —
   * 휠·버튼은 기준점이 움직이지 않는다. 핀치는 손가락이 함께 움직이므로
   * 지금 중점 위치를 넘긴다 (처음 중점 아래 캔들이 지금 중점을 따라간다).
   */
  toRatio?: number
}

/**
 * `view` 를 `factor` 배로 확대/축소한 새 구간.
 *
 * factor < 1 은 확대(보이는 캔들이 줄어든다), > 1 은 축소.
 * anchorRatio — 기준점이 플롯에서 차지하는 비율 (`plotRatio`).
 *
 * 반환은 항상 정수 구간이고 `0 <= start < end <= total` 이다.
 *
 * 정수로 맞추는 방법이 요점이다. 연속 좌표로 계산한 뒤 반올림하면 기준
 * 캔들이 한 칸씩 흔들린다. 기준 캔들 i 가 새 구간에서 같은 비율 r 에 오려면
 * `floor(start + r·n) = i`, 즉 `i - r·n <= start < i + 1 - r·n` 이어야 하고,
 * 이 반열린 구간은 폭이 정확히 1 이라 정수가 하나뿐이다 — `ceil(i - r·n)`.
 */
export function zoomAround(
  view: ViewRange,
  total: number,
  factor: number,
  anchorRatio: number,
  opts: ZoomOptions = {},
): ViewRange {
  const tot = Math.max(0, Math.floor(total))
  if (tot === 0) return { start: 0, end: 0 }

  // 들어온 구간부터 데이터 안으로 정리한다 — 호출자가 낡은 구간을 넘겨도
  // (기간을 바꿔 데이터가 줄어든 직후 등) 결과가 데이터 밖을 가리키지 않게.
  let start = Math.max(0, Math.min(tot - 1, Math.floor(view.start)))
  let end = Math.max(start + 1, Math.min(tot, Math.ceil(view.end)))
  const span = end - start

  const clamp01 = (v: number) => (Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0.5)
  const r0 = clamp01(anchorRatio)
  const r1 = opts.toRatio == null ? r0 : clamp01(opts.toRatio)
  const f = Number.isFinite(factor) && factor > 0 ? factor : 1

  const minSpan = Math.max(1, Math.min(tot, Math.floor(opts.minSpan ?? 10)))
  const n = Math.max(minSpan, Math.min(tot, Math.round(span * f)))

  // 기준 캔들 — 기준점 비율이 가리키는 캔들. 오른쪽 끝(r=1)은 마지막 캔들이다.
  const anchor = Math.min(end - 1, Math.floor(start + r0 * span))

  start = Math.ceil(anchor - r1 * n)
  // 부동소수 오차로 경계값이 한 칸 넘어가는 것을 막는다: floor(start + r1·n)
  // 가 anchor 가 되도록 한 번 더 맞춘다.
  if (Math.floor(start + r1 * n) > anchor) start -= 1
  if (Math.floor(start + r1 * n) < anchor) start += 1
  if (r1 === 1) start = anchor + 1 - n       // 오른쪽 끝은 마지막 칸이 기준 캔들

  // 데이터 밖으로 나가지 않게 — 여기서만 기준 캔들이 움직일 수 있다.
  start = Math.max(0, Math.min(tot - n, start))
  return { start, end: start + n }
}
