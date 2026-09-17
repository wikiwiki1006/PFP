/**
 * components/serverError.ts
 * ─────────────────────────
 * 요청 실패를 화면에 쓸 문장으로 바꾼다 — 서버가 준 사유(`detail`)가 먼저다.
 *
 * axios 의 `e.message` 는 "Request failed with status code 500" 이다. 서버는
 * FastAPI HTTPException 의 `detail` 에 사유를 싣는데(예: 데일리 브리핑의 "기준일
 * 종가가 아직 수집되지 않았습니다"), 인터셉터가 그 값을 옮기지 않아 화면이
 * 상태 코드만 말했다. 사유가 있으면 사용자가 할 수 있는 일(기다리기·다시 하기·
 * 포기하기)이 갈린다 (§1.3).
 *
 * **서버가 내부 예외 문자열을 detail 에 싣는 경로에는 쓰지 않는다** —
 * `HTTPException(500, detail=str(e))` 는 스택의 말을 그대로 사용자에게 보여 준다.
 * 이 함수는 그 구별을 못 한다. 부르는 쪽이 그 경로의 detail 이 사용자용 문장인지
 * 확인한 뒤에 쓴다.
 *
 * React·경로 별칭을 import 하지 않는다 — node 가 이 파일을 그대로 읽는다.
 */

function rawDetail(err: unknown): unknown {
  return (err as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
}

/** 서버가 준 사유. 없으면 null. 문자열이 아니면(FastAPI 검증 오류 배열 등) JSON 으로. */
export function serverDetail(err: unknown): string | null {
  const d = rawDetail(err)
  if (d == null) return null
  if (typeof d === 'string') return d.trim() || null
  try {
    return JSON.stringify(d)
  } catch {
    return String(d)
  }
}

/** 사유가 **문장**(문자열)일 때만. 검증 오류 배열처럼 사람이 읽을 문장이 아니면 null. */
export function stringDetail(err: unknown): string | null {
  const d = rawDetail(err)
  return typeof d === 'string' && d.trim() ? d.trim() : null
}

/** 사유 → 없으면 예외 메시지 → 그것도 없으면 fallback. */
export function errorText(err: unknown, fallback = '요청에 실패했습니다.'): string {
  const detail = serverDetail(err)
  if (detail) return detail
  const msg = (err as { message?: unknown } | null)?.message
  return typeof msg === 'string' && msg.trim() ? msg : fallback
}
