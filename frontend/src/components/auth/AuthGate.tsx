/**
 * components/auth/AuthGate.tsx
 * ────────────────────────────
 * 개인 데이터 영역을 감싸는 게이트.
 *
 * 로그인하지 않았으면 안내와 함께 로그인·회원가입 버튼을 보여주고,
 * 로그인했으면 children 을 그대로 렌더한다.
 *
 * 이건 편의 장치일 뿐 보안 경계가 아니다. 실제 차단은 서버가 한다 —
 * 개인 데이터 엔드포인트는 유효한 ID 토큰이 없으면 401 을 돌려준다.
 */
import { useState, type ReactNode } from 'react'
import { Lock, LogIn, UserPlus } from 'lucide-react'
import { useAuth } from '@/lib/AuthContext'

interface Props {
  /** 로그인 상태에서 보여줄 내용. 안내만 띄우고 싶으면 생략한다. */
  children?: ReactNode
  /** 무엇이 잠겨 있는지 — 예: "포트폴리오", "과거 리포트 이력" */
  feature?: string
  description?: string
  /** 카드 대신 작은 인라인 배너로 표시 */
  compact?: boolean
}

export default function AuthGate({ children, feature = '이 기능', description, compact }: Props) {
  const { isAuthed, loading, openAuth } = useAuth()

  // 세션 복원 중에는 안내를 띄우지 않는다 — 로그인 상태인데도 잠깐 깜빡인다.
  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[#2d3f56] border-t-[#10b981]" />
      </div>
    )
  }

  if (isAuthed) return <>{children ?? null}</>

  const buttons = (
    <div className="flex flex-wrap items-center gap-2">
      <button
        onClick={() => openAuth('login')}
        className="inline-flex items-center gap-1.5 rounded-lg bg-[#10b981] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[#059669]"
      >
        <LogIn size={15} /> 로그인
      </button>
      <button
        onClick={() => openAuth('signup')}
        className="inline-flex items-center gap-1.5 rounded-lg border border-[#2d3f56] px-4 py-2 text-sm font-medium text-[#94a3b8] transition hover:border-[#3d5270] hover:bg-[#0d1526]"
      >
        <UserPlus size={15} /> 회원가입
      </button>
    </div>
  )

  return (
    <>
      {compact ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#1e2d40] bg-[#0d1526]/60 px-4 py-3">
          <div className="flex items-center gap-2.5">
            <Lock size={16} className="shrink-0 text-[#4a5568]" />
            <p className="text-sm text-[#cbd5e1]">로그인 후 사용 가능</p>
          </div>
          {buttons}
        </div>
      ) : (
        <div className="m-6 flex flex-col items-center justify-center rounded-2xl border border-[#1e2d40] bg-[#0d1526]/50 px-6 py-14 text-center">
          <div className="mb-4 rounded-full border border-[#1e2d40] bg-[#0d1526] p-3.5">
            <Lock size={22} className="text-[#4a5568]" />
          </div>
          <h3 className="text-base font-semibold text-[#e2e8f0]">
            로그인 후 사용 가능한 기능입니다
          </h3>
          {description && (
            <p className="mt-2 max-w-md text-sm leading-relaxed text-[#4a5568]">{description}</p>
          )}
          <div className="mt-6">{buttons}</div>
        </div>
      )}

    </>
  )
}
