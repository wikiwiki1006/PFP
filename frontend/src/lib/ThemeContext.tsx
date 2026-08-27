/**
 * lib/ThemeContext.tsx
 * ────────────────────
 * 라이트/다크 테마 전환.
 *
 * 실제 색상 전환은 CSS 가 한다 — `<html data-theme="light">` 가 붙으면
 * src/styles/light-theme.css 의 규칙이 활성화된다. 여기서는 그 속성을
 * 켜고 끄고, 사용자의 선택을 기억하는 일만 한다.
 *
 * 기본값은 라이트다. 저장된 선택이 없으면 라이트로 시작한다 —
 * 시스템 설정을 따라가지 않는 이유는, 처음 방문한 사용자에게 일관된
 * 첫인상을 주기 위해서다. 한 번 고르면 그 선택이 계속 유지된다.
 */
import {
  createContext, useContext, useEffect, useMemo, useState, useCallback,
  type ReactNode,
} from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'pfp_theme'

interface ThemeState {
  theme: Theme
  setTheme: (t: Theme) => void
  toggle: () => void
}

const Ctx = createContext<ThemeState | null>(null)

/** 저장된 선택을 읽는다. 없거나 이상한 값이면 라이트. */
function initialTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(initialTheme)

  useEffect(() => {
    const root = document.documentElement
    root.setAttribute('data-theme', theme)
    // 스크롤바·폼 컨트롤 등 브라우저 기본 UI 도 함께 맞춘다.
    root.style.colorScheme = theme
    try { localStorage.setItem(STORAGE_KEY, theme) } catch { /* 사파리 프라이빗 모드 등 */ }
  }, [theme])

  const setTheme = useCallback((t: Theme) => setThemeState(t), [])
  const toggle = useCallback(() => setThemeState(t => (t === 'dark' ? 'light' : 'dark')), [])

  const value = useMemo(() => ({ theme, setTheme, toggle }), [theme, setTheme, toggle])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useTheme(): ThemeState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useTheme 은 ThemeProvider 안에서만 쓸 수 있습니다.')
  return v
}
