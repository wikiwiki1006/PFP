import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Layout from './components/Layout'
import { AuthProvider } from './lib/AuthContext'
import { ThemeProvider } from './lib/ThemeContext'
import { TourProvider } from './lib/TourContext'
import TourOverlay from './components/onboarding/TourOverlay'
import AlphaTerminal from './pages/AlphaTerminal'
import MacroScenario from './pages/MacroScenario'
import Optimizer from './pages/Optimizer'
import TimingEngine from './pages/TimingEngine'
import LensReport from './pages/LensReport'
import KakaoCallback from './pages/KakaoCallback'
import NaverCallback from './pages/NaverCallback'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
})

export default function App() {
  return (
    <ThemeProvider>
    <QueryClientProvider client={queryClient}>
      {/* 중첩 순서에 이유가 있다:
          · AuthProvider 는 QueryClient 안쪽 — 로그아웃 시 캐시를 비운다.
          · TourProvider 는 BrowserRouter 안쪽 — 단계마다 화면을 옮기려면
            useNavigate 가 필요하다.
          · AuthProvider 는 TourProvider 안쪽 — AuthProvider 가 렌더하는
            가입 완료 모달이 가입 직후 투어를 시작한다. 반대로 두면
            useTour 가 프로바이더를 못 찾아 앱 전체가 백지가 된다. */}
      <BrowserRouter>
        <TourProvider>
        <AuthProvider>
          <Routes>
            {/* 카카오 OAuth 착지점 — 팝업 안에서만 열리므로 레이아웃 밖에 둔다 */}
            <Route path="/auth/kakao/callback" element={<KakaoCallback />} />
            <Route path="/auth/naver/callback" element={<NaverCallback />} />

            <Route path="/" element={<Layout />}>
              <Route index element={<Navigate to="/terminal" replace />} />

              {/* 개인 데이터가 섞인 화면 — 페이지 전체를 막지 않고, 개인 패널만
                  예시 데이터 + 흐림 처리로 미리보기를 제공한다 (LockedPreview). */}
              <Route path="terminal" element={<AlphaTerminal />} />
              <Route path="macro" element={<MacroScenario />} />
              <Route path="optimizer" element={<Optimizer />} />
              <Route path="lens" element={<LensReport />} />

              {/* 공개 시장 데이터 */}
              <Route path="timing" element={<TimingEngine />} />
            </Route>
          </Routes>
        <TourOverlay />
        </AuthProvider>
        </TourProvider>
      </BrowserRouter>
    </QueryClientProvider>
    </ThemeProvider>
  )
}
