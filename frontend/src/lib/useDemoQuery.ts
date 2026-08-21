/**
 * lib/useDemoQuery.ts
 * ───────────────────
 * 비로그인 상태에서 개인 데이터 쿼리를 예시 데이터로 대체하는 헬퍼.
 *
 * 로그인하지 않았으면 서버를 부르지 않고(어차피 401) 예시 값을 돌려준다.
 * 화면은 정상적으로 그려지고, LockedPreview 가 그 위에 흐림과 안내를 씌운다.
 *
 * 로그인 상태에서는 예시 데이터가 절대 섞이지 않는다 — enabled 로 실제 쿼리만
 * 돌고, 반환값도 쿼리 결과 그대로다.
 */
import { useQuery, type UseQueryOptions } from '@tanstack/react-query'
import { useAuth } from './AuthContext'

export function useDemoQuery<T>(
  key: unknown[],
  queryFn: () => Promise<T>,
  demo: T,
  options?: Omit<UseQueryOptions<T, Error, T, readonly unknown[]>, 'queryKey' | 'queryFn'>,
) {
  const { isAuthed, loading } = useAuth()
  const enabled = isAuthed && !loading && (options?.enabled ?? true)

  const q = useQuery<T, Error, T, readonly unknown[]>({
    ...options,
    queryKey: key,
    queryFn,
    enabled,
  })

  if (isAuthed) return q

  // 비로그인 — 예시 데이터를 성공 상태로 흉내 낸다. 로딩 스피너가 뜨면
  // 미리보기가 영영 비어 보이므로 isLoading 은 false 로 고정한다.
  return { ...q, data: demo, isLoading: false, isError: false, error: null } as typeof q
}
