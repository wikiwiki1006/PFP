/**
 * lib/firebase.ts
 * ───────────────
 * Firebase 초기화 + 인증 헬퍼.
 *
 * 여기 있는 값들은 공개돼도 되는 식별자다. Firebase 웹 설정은 비밀이 아니며
 * (프론트 번들에 그대로 들어간다), 실제 접근 통제는 서버의 ID 토큰 검증과
 * Firebase 콘솔의 승인 도메인 설정이 담당한다.
 */
import { initializeApp } from 'firebase/app'
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signInWithCustomToken,
  signOut as fbSignOut,
  browserLocalPersistence,
  browserSessionPersistence,
  setPersistence,
  type User,
} from 'firebase/auth'

const firebaseConfig = {
  apiKey:            import.meta.env.VITE_FIREBASE_API_KEY            || 'AIzaSyCSjie4HV_Z8zEnlowZDW33qTRdpstTIVE',
  authDomain:        import.meta.env.VITE_FIREBASE_AUTH_DOMAIN        || 'personalfinancialplatform.firebaseapp.com',
  projectId:         import.meta.env.VITE_FIREBASE_PROJECT_ID         || 'personalfinancialplatform',
  storageBucket:     import.meta.env.VITE_FIREBASE_STORAGE_BUCKET     || 'personalfinancialplatform.firebasestorage.app',
  messagingSenderId: import.meta.env.VITE_FIREBASE_SENDER_ID          || '725974651480',
  appId:             import.meta.env.VITE_FIREBASE_APP_ID             || '1:725974651480:web:afd01bb05efd08c2aeb02d',
}

export const firebaseApp = initializeApp(firebaseConfig)
export const auth = getAuth(firebaseApp)

// Firebase 가 보내는 메일(비밀번호 재설정 등)을 한국어 템플릿으로 받는다.
// 지정하지 않으면 영문 기본 템플릿이 나간다.
auth.languageCode = 'ko'

// 기본은 '로그인 상태 유지' — 탭을 닫아도 세션이 남는다 (localStorage).
setPersistence(auth, browserLocalPersistence).catch(() => {})

/**
 * '로그인 상태 유지' 토글.
 *
 * 켜면 localStorage 에 세션이 남아 브라우저를 닫았다 열어도 로그인 상태가 유지된다.
 * 끄면 sessionStorage 를 써서 탭을 닫는 순간 로그아웃된다 — 공용 PC 에서 중요하다.
 * **로그인 직전에** 호출해야 새로 만들어지는 세션에 적용된다.
 */
export async function setRememberMe(remember: boolean): Promise<void> {
  await setPersistence(auth, remember ? browserLocalPersistence : browserSessionPersistence)
}

// ── 로그인 수단 ────────────────────────────────────────────────────────────────

export async function loginWithGoogle(): Promise<User> {
  const provider = new GoogleAuthProvider()
  provider.setCustomParameters({ prompt: 'select_account' })
  const { user } = await signInWithPopup(auth, provider)
  return user
}

/**
 * 서버가 발급한 Custom Token 으로 로그인한다.
 * 아이디 로그인·회원가입·Naver·Kakao 가 모두 이 경로를 쓴다 — 서버가 신원을
 * 확인한 뒤 토큰을 내주고, 프론트는 그걸로 Firebase 세션을 연다.
 */
export async function loginWithCustomToken(token: string): Promise<User> {
  const { user } = await signInWithCustomToken(auth, token)
  return user
}

export async function logout(): Promise<void> {
  await fbSignOut(auth)
}

/**
 * 가입하지 않은 소셜 계정으로 로그인이 시도된 경우의 뒷정리.
 *
 * 구글 팝업은 성공하는 순간 Firebase 세션을 열어 버린다. 서버가 "가입 안 됨"을
 * 돌려줘도 그 세션이 남아 있으면 앱은 로그인 상태처럼 보인다. 그래서 곧바로
 * 세션을 닫는다. Firebase 계정 자체는 지우지 않는다 — 나중에 정식으로 가입하면
 * 같은 계정을 그대로 쓰게 된다.
 */
export async function abortSignIn(): Promise<void> {
  await fbSignOut(auth).catch(() => {})
}

/**
 * 거절된 소셜 로그인의 뒷정리.
 *
 * 구글 팝업은 성공하는 순간 Firebase 계정을 만든다. 우리가 가입을 거절해도
 * 그 계정은 남아, 같은 이메일에 계정이 둘처럼 보이고 다음 시도마다 쌓인다.
 * **이번 로그인에서 방금 만들어진 계정만** 지운다 — 원래 있던 계정을 지우면
 * 멀쩡한 사용자의 계정을 없애는 셈이라 되돌릴 수 없다.
 */
export async function discardSignIn(): Promise<void> {
  const u = auth.currentUser
  if (!u) return
  const m = u.metadata
  const brandNew = !!m.creationTime && m.creationTime === m.lastSignInTime
  if (brandNew) {
    // 삭제에 실패해도(재인증 요구 등) 로그아웃은 반드시 한다.
    await u.delete().catch(() => fbSignOut(auth).catch(() => {}))
    return
  }
  await fbSignOut(auth).catch(() => {})
}

/** 현재 ID 토큰. 만료가 가까우면 SDK 가 알아서 갱신한다. */
export async function getIdToken(forceRefresh = false): Promise<string | null> {
  const u = auth.currentUser
  return u ? u.getIdToken(forceRefresh) : null
}

/** Firebase 오류 코드를 사용자에게 보여줄 한국어 문구로 바꾼다. */
export function authErrorMessage(err: unknown): string {
  const code = (err as { code?: string })?.code ?? ''
  const map: Record<string, string> = {
    'auth/invalid-email':                 '이메일 형식이 올바르지 않습니다.',
    'auth/user-disabled':                 '비활성화된 계정입니다.',
    'auth/user-not-found':                '등록되지 않은 이메일입니다.',
    'auth/wrong-password':                '비밀번호가 일치하지 않습니다.',
    'auth/invalid-credential':            '이메일 또는 비밀번호가 올바르지 않습니다.',
    'auth/email-already-in-use':          '이미 가입된 이메일입니다.',
    'auth/weak-password':                 '비밀번호가 너무 단순합니다.',
    'auth/invalid-custom-token':          '인증 정보가 올바르지 않습니다. 다시 시도해 주세요.',
    'auth/too-many-requests':             '시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.',
    'auth/popup-closed-by-user':          '로그인 창이 닫혔습니다.',
    'auth/cancelled-popup-request':       '로그인 창이 닫혔습니다.',
    'auth/popup-blocked':                 '팝업이 차단됐습니다. 브라우저 설정을 확인해 주세요.',
    'auth/unauthorized-domain':           '이 도메인은 로그인이 허용되지 않았습니다.',
    'auth/network-request-failed':        '네트워크 오류입니다. 연결을 확인해 주세요.',
    'auth/operation-not-allowed':         '이 로그인 방식이 아직 활성화되지 않았습니다.',
    'auth/account-exists-with-different-credential':
      '이미 다른 방법으로 가입된 이메일입니다. 기존 방식으로 로그인해 주세요.',
  }
  return map[code] || (err as Error)?.message || '로그인에 실패했습니다.'
}

export type { User }
