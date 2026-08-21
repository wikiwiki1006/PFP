/**
 * lib/credentials.ts
 * ──────────────────
 * 아이디·비밀번호 규칙 (backend/services/credentials.py 와 같은 규칙).
 *
 * 여기 검사는 입력 즉시 피드백을 주기 위한 것이고, **최종 판정은 서버가 한다**.
 * 두 곳의 규칙이 어긋나면 "여기선 되는데 가입은 안 되는" 상황이 생기므로
 * 한쪽을 바꾸면 다른 쪽도 함께 바꿔야 한다.
 */

const USERNAME_RE = /^[a-z][a-z0-9_-]{3,19}$/

const RESERVED = new Set([
  'admin', 'administrator', 'root', 'system', 'support', 'help', 'official',
  'pfp', 'api', 'auth', 'login', 'signup', 'user', 'users', 'me', 'null',
  'undefined', 'test', 'guest', 'anonymous', 'master', 'manager', 'staff',
])

export function normalizeUsername(raw: string): string {
  return (raw ?? '').normalize('NFKC').trim().toLowerCase()
}

/** 통과하면 빈 문자열, 아니면 사용자에게 보여줄 사유. */
export function checkUsername(raw: string): string {
  const u = normalizeUsername(raw)
  if (!u) return '아이디를 입력해 주세요.'
  if (u.length < 4 || u.length > 20) return '아이디는 4~20자여야 합니다.'
  if (!USERNAME_RE.test(u)) return '영문 소문자로 시작하고, 영문·숫자·밑줄(_)·하이픈(-)만 쓸 수 있습니다.'
  if (RESERVED.has(u)) return '사용할 수 없는 아이디입니다.'
  return ''
}

export interface PasswordCheck {
  length: boolean
  letter: boolean
  digit: boolean
  special: boolean
  /** 모두 충족 */
  ok: boolean
}

export function checkPassword(pw: string): PasswordCheck {
  const p = pw ?? ''
  const r = {
    length:  p.length >= 8 && p.length <= 72 && !p.includes(' '),
    letter:  /[A-Za-z]/.test(p),
    digit:   /\d/.test(p),
    special: /[^A-Za-z0-9]/.test(p),
  }
  return { ...r, ok: r.length && r.letter && r.digit && r.special }
}

/** 비밀번호 확인란 일치 여부. 통과하면 빈 문자열. */
export function checkPasswordConfirm(pw: string, confirm: string): string {
  if (!confirm) return ''
  return pw === confirm ? '' : '비밀번호가 일치하지 않습니다.'
}

export function checkEmail(email: string): string {
  const e = (email ?? '').trim()
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(e)) return '이메일 형식이 올바르지 않습니다.'
  if (e.length > 254) return '이메일이 너무 깁니다.'
  return ''
}
