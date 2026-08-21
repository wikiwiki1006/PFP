/**
 * lib/AuthContext.tsx
 * ───────────────────
 * 앱 전역 로그인 상태.
 *
 * Firebase 가 세션의 원본이다. onAuthStateChanged 로 상태를 구독하고,
 * 로그인이 확인되면 /api/auth/me 로 서버 프로필을 동기화한다.
 */
import {
  createContext, useContext, useEffect, useMemo, useState, useCallback,
  type ReactNode,
} from 'react'
import { onAuthStateChanged } from 'firebase/auth'
import { useQueryClient } from '@tanstack/react-query'
import { auth, logout as fbLogout, type User } from './firebase'
import { api } from '@/api'

export interface Profile {
  uid: string
  /** 로그인 식별자. SNS 로 가입한 계정은 없을 수 있다. */
  username: string | null
  email: string | null
  name: string | null
  provider: string | null
  email_verified: boolean
  photo_url: string | null
  created_at: string | null
}

interface AuthState {
  user: User | null
  profile: Profile | null
  /**
   * 세션 복원과 가입 확인이 끝나기 전까지 true.
   * 이때 로그인 안내를 띄우면 로그인 상태인데도 잠깐 깜빡인다.
   */
  loading: boolean
  /**
   * **가입까지 마친** 사용자인지.
   *
   * Firebase 세션만 보고 판단하면 안 된다 — 구글 팝업은 성공하는 순간 세션을
   * 만들어 주므로, 가입한 적 없는 계정도 1초쯤 로그인한 것처럼 보이다가
   * 서버가 거부하면 되돌아간다. 그 깜빡임이 사용자에게는 "로그인됐다가 튕김"
   * 으로 보인다. 그래서 서버가 프로필을 내준 경우에만 true 로 둔다.
   */
  isAuthed: boolean
  /** 인증은 됐지만 가입하지 않은 상태 — 소셜 가입 진행 중이거나 미가입 로그인 시도. */
  unregistered: boolean
  logout: () => Promise<void>
  refreshProfile: () => Promise<'ok' | 'unregistered' | 'error'>
}

const Ctx = createContext<AuthState | null>(null)

/** 마지막으로 화면을 쓴 계정. 계정이 바뀌면 남은 캐시를 지운다. */
const LAST_UID_KEY = 'pfp_last_uid'

/**
 * 생성된 리포트·분석 결과는 sessionStorage 에 캐시된다(새로고침 대비).
 * 로그아웃하거나 다른 계정으로 로그인하면 이건 남의 데이터가 되므로 지운다.
 * 공용 브라우저에서 앞사람의 리포트가 뒷사람에게 보이는 것을 막는다.
 */
function clearPersonalCache() {
  const keep = new Set([LAST_UID_KEY])
  for (const k of Object.keys(sessionStorage)) {
    if (!keep.has(k)) sessionStorage.removeItem(k)
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)
  const [unregistered, setUnregistered] = useState(false)
  const qc = useQueryClient()

  const syncProfile = useCallback(async (): Promise<'ok' | 'unregistered' | 'error'> => {
    try {
      const { data } = await api.get<Profile>('/api/auth/me')
      setProfile(data)
      setUnregistered(false)
      return 'ok'
    } catch (e) {
      setProfile(null)
      // 403 = 토큰은 유효하지만 가입하지 않은 계정. 구글 팝업이 Firebase 계정을
      // 자동으로 만들어 준 경우가 여기 해당한다. 세션을 닫아 로그인 상태로
      // 보이지 않게 한다.
      const status = (e as { response?: { status?: number } })?.response?.status
      setUnregistered(status === 403)
      if (status === 403) return 'unregistered'
      return 'error'
    }
  }, [])

  useEffect(() => {
    return onAuthStateChanged(auth, async (u) => {
      // 계정이 바뀌었으면(로그아웃 포함) 앞 계정의 흔적을 모두 지운다.
      const prevUid = localStorage.getItem(LAST_UID_KEY)
      if (prevUid !== (u?.uid ?? null)) {
        qc.clear()
        clearPersonalCache()
        if (u) localStorage.setItem(LAST_UID_KEY, u.uid)
        else   localStorage.removeItem(LAST_UID_KEY)
      }

      setUser(u)
      if (u) {
        // 가입 여부를 확인할 때까지 loading 을 유지한다. 여기서 loading 을
        // 먼저 내리면 '세션은 있는데 미가입' 상태가 화면에 잠깐 드러난다.
        setLoading(true)
        // 소셜 가입 도중이면 아직 users 행이 없어 403 이 난다. 그때는 프로필
        // 없이 두고, 가입이 끝난 뒤 refreshProfile() 이 다시 채운다.
        await syncProfile()
      } else {
        setProfile(null)
        setUnregistered(false)
      }
      setLoading(false)
    })
  }, [syncProfile, qc])

  const logout = useCallback(async () => {
    await fbLogout()
    qc.clear()
    clearPersonalCache()
  }, [qc])

  const value = useMemo<AuthState>(() => ({
    user, profile, loading,
    // 서버가 프로필을 내준 경우에만 로그인으로 본다 (가입 확인 완료).
    isAuthed: !!user && !!profile,
    unregistered,
    logout,
    refreshProfile: syncProfile,
  }), [user, profile, loading, unregistered, logout, syncProfile])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth(): AuthState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth 는 AuthProvider 안에서만 쓸 수 있습니다.')
  return v
}
