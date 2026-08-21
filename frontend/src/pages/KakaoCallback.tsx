/**
 * pages/KakaoCallback.tsx
 * ───────────────────────
 * 카카오 OAuth 리다이렉트 착지점.
 *
 * 카카오 인가 페이지는 팝업으로 열리고, 동의가 끝나면 이 경로로 `?code=…` 를
 * 붙여 돌아온다. 이 페이지는 그 코드를 부모 창(로그인 모달)에 넘기고 닫힌다.
 * 코드를 액세스 토큰으로 바꾸는 일은 서버가 한다 — REST 키가 브라우저에
 * 노출되지 않도록.
 */
import { useEffect } from 'react'

export default function KakaoCallback() {
  useEffect(() => {
    const p = new URLSearchParams(window.location.search)
    const payload = {
      source: 'pfp-kakao-oauth' as const,
      code:  p.get('code'),
      error: p.get('error_description') || p.get('error'),
    }

    // 부모 창에만 전달한다. origin 을 현재 출처로 제한해 다른 사이트가
    // 이 메시지를 가로채지 못하게 한다.
    if (window.opener) {
      window.opener.postMessage(payload, window.location.origin)
      window.close()
    }
  }, [])

  return (
    <div className="flex h-screen items-center justify-center bg-[#0b0f1a]">
      <p className="text-sm text-[#94a3b8]">카카오 로그인 처리 중…</p>
    </div>
  )
}
