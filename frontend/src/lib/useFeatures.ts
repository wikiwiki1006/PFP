/**
 * lib/useFeatures.ts
 * ──────────────────
 * 관리자가 켜고 끄는 기능 스위치 상태를 읽는다.
 *
 * 화면을 잠그는 것은 안내일 뿐이고, 실제 차단은 서버가 한다
 * (ai_feature_user / resolve_model_tier). 다만 눌러봐야 실패하는 버튼을
 * 보여주지 않으려면 프론트도 상태를 알아야 한다.
 *
 * 비로그인 상태에서는 조회하지 않는다 — 401 만 받을 뿐이고, 어차피 AI 기능은
 * 로그인해야 쓸 수 있다. 그때는 전부 허용으로 두어 로그인 안내가 먼저 뜨게 한다.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api'
import { useAuth } from '@/lib/AuthContext'

export interface Features {
  is_admin: boolean
  ai_enabled: boolean
  deep_analysis_enabled: boolean
  /** true 면 심층 분석이 계정당 24시간 1회로 제한된다. */
  deep_analysis_daily_limit: boolean
}

const ALLOW_ALL: Features = {
  is_admin: false,
  ai_enabled: true,
  deep_analysis_enabled: true,
  deep_analysis_daily_limit: false,
}

export function useFeatures(): Features {
  const { isAuthed } = useAuth()
  const q = useQuery({
    queryKey: ['features'],
    queryFn: async (): Promise<Features> => (await api.get('/api/features')).data,
    enabled: isAuthed,
    // 관리자가 스위치를 내리면 오래 지나지 않아 화면에도 반영돼야 한다.
    staleTime: 30_000,
    retry: false,
  })
  return q.data ?? ALLOW_ALL
}
