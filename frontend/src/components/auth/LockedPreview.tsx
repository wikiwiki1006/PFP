/**
 * components/auth/LockedPreview.tsx
 * ─────────────────────────────────
 * 비로그인 사용자에게 "이런 기능이 있다"를 보여주는 미리보기 래퍼.
 *
 * 내용을 숨기는 게 아니라 **읽을 수 없을 정도로만** 흐리게 처리한다.
 * 레이아웃과 차트 모양은 그대로 보이므로 어떤 기능인지 알 수 있고,
 * 숫자는 읽히지 않아 예시 데이터를 실제 값으로 오해할 일이 없다.
 *
 * 클릭·선택·포커스를 모두 막아 안쪽 컨트롤이 동작하지 않게 한다
 * (inert 대신 pointer-events + tabIndex 조합 — 브라우저 지원 폭이 넓다).
 */
import { useState, type ReactNode } from 'react'
import { Lock, LogIn, UserPlus } from 'lucide-react'
import { useAuth } from '@/lib/AuthContext'

interface Props {
  children: ReactNode
  /** 무엇이 잠겨 있는지 — 예: "보유 종목", "AI 분석" */
  label?: string
  /** 흐림 강도 — 글자 크기가 작은 패널은 'light' 로 낮춘다 */
  blur?: 'light' | 'normal'
  /** 배지 크기. 좁은 패널은 'sm', 높이 40px 안팎의 가로 바는 'bar' */
  size?: 'sm' | 'md' | 'bar'
  /**
   * 흐림만 걸고 안내 배지는 띄우지 않는다.
   * 한 화면에 잠긴 패널이 여러 개일 때 배지가 그만큼 반복되면 화면이 시끄럽다.
   * 그럴 때는 패널을 전부 silent 로 두고 AuthNotice 를 한 번만 배치한다.
   */
  silent?: boolean
}

export default function LockedPreview({
  children, label, blur = 'normal', size = 'md', silent = false,
}: Props) {
  const { isAuthed, loading, openAuth } = useAuth()

  // 세션 복원 중에는 잠금을 씌우지 않는다 — 로그인 상태인데 잠깐 깜빡인다.
  if (loading || isAuthed) return <>{children}</>

  const blurPx = blur === 'light' ? '2.5px' : '4px'

  // 배지 없이 흐림만 — 안내는 화면당 한 번, AuthNotice 가 맡는다.
  if (silent) {
    return (
      <div
        aria-hidden="true"
        className="pointer-events-none h-full w-full select-none"
        style={{ filter: `blur(${blurPx})`, opacity: 0.5 }}
      >
        {children}
      </div>
    )
  }

  // 지표 바처럼 높이가 낮은 영역은 세로로 쌓인 배지가 넘친다.
  // 가로 한 줄짜리 컴팩트 배지를 따로 쓴다.
  if (size === 'bar') {
    return (
      <div className="relative flex h-full w-full items-stretch overflow-hidden">
        <div
          aria-hidden="true"
          className="pointer-events-none flex select-none items-stretch"
          style={{ filter: `blur(${blurPx})`, opacity: 0.5 }}
        >
          {children}
        </div>
        <div className="absolute inset-0 z-10 flex items-center gap-2 bg-[#0b0f1a]/40 px-3">
          <Lock size={11} className="shrink-0 text-[#3b82f6]" />
          <span className="whitespace-nowrap text-[10px] font-bold text-[#e2e8f0]">
            로그인 후 사용 가능
          </span>
          <button
            onClick={() => openAuth('login')}
            className="rounded bg-[#3b82f6] px-2 py-0.5 text-[10px] font-semibold text-white transition hover:bg-[#2f6fe0]"
          >
            로그인
          </button>
          <button
            onClick={() => openAuth('signup')}
            className="rounded border border-[#2d3f56] px-2 py-0.5 text-[10px] font-medium text-[#94a3b8] transition hover:bg-[#0d1526]"
          >
            회원가입
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="relative h-full w-full overflow-hidden">
      {/* 예시 콘텐츠 — 읽히지 않을 만큼만 흐리게, 상호작용은 차단 */}
      <div
        aria-hidden="true"
        className="pointer-events-none h-full w-full select-none"
        style={{ filter: `blur(${blurPx})`, opacity: 0.55 }}
      >
        {children}
      </div>

      {/* 잠금 안내 */}
      <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#0b0f1a]/45 p-3">
        <div
          className={`pointer-events-auto flex flex-col items-center rounded-xl border border-[#1e2d40] bg-[#060b14]/95 text-center shadow-xl backdrop-blur-sm ${
            size === 'sm' ? 'gap-2 px-4 py-3' : 'gap-2.5 px-6 py-5'
          }`}
        >
          <div className="flex items-center gap-1.5">
            <Lock size={size === 'sm' ? 12 : 14} className="text-[#3b82f6]" />
            <span className={`font-bold text-[#e2e8f0] ${size === 'sm' ? 'text-[11px]' : 'text-xs'}`}>
              로그인 후 사용 가능
            </span>
          </div>

          <div className="mt-0.5 flex items-center gap-1.5">
            <button
              onClick={() => openAuth('login')}
              className={`inline-flex items-center gap-1 rounded-md bg-[#3b82f6] font-semibold text-white transition hover:bg-[#2f6fe0] ${
                size === 'sm' ? 'px-2.5 py-1 text-[10px]' : 'px-3 py-1.5 text-[11px]'
              }`}
            >
              <LogIn size={size === 'sm' ? 11 : 12} /> 로그인
            </button>
            <button
              onClick={() => openAuth('signup')}
              className={`inline-flex items-center gap-1 rounded-md border border-[#2d3f56] font-medium text-[#94a3b8] transition hover:border-[#3d5270] hover:bg-[#0d1526] ${
                size === 'sm' ? 'px-2.5 py-1 text-[10px]' : 'px-3 py-1.5 text-[11px]'
              }`}
            >
              <UserPlus size={size === 'sm' ? 11 : 12} /> 회원가입
            </button>
          </div>
        </div>
      </div>

    </div>
  )
}

/**
 * 버튼 하나만 잠그고 싶을 때 쓰는 훅.
 * 최적화 화면의 "포트폴리오 불러오기"처럼, 화면 전체는 쓸 수 있고
 * 특정 동작만 로그인이 필요한 경우에 맞다.
 */
export function useLoginPrompt() {
  const { isAuthed, openAuth } = useAuth()

  /** 로그인했으면 action 실행, 아니면 로그인 모달을 띄우고 false 반환. */
  const requireLogin = (action?: () => void): boolean => {
    if (isAuthed) { action?.(); return true }
    openAuth('login')
    return false
  }

  // 모달은 앱 루트(AuthProvider)에 하나만 있다. 호출부가 렌더할 것이 없으므로
  // modalEl 은 빈 자리표시자로 남긴다 — 기존 호출부를 고치지 않아도 되게.
  return { isAuthed, requireLogin, modalEl: null }
}


/**
 * 흐림 처리된 영역 한가운데에 띄우는 로그인 안내.
 *
 * 감싼 영역 전체를 덮되 카드 바깥은 클릭이 통과하므로(pointer-events-none),
 * 같은 영역에 있는 공개 패널은 그대로 쓸 수 있다.
 * 화면당 한 번만 배치한다 — 잠긴 패널마다 배지를 띄우면 화면이 시끄럽다.
 */
export function AuthOverlay() {
  const { isAuthed, loading, openAuth } = useAuth()

  // 안내 카드는 비로그인일 때만 보여주지만, **모달은 항상 같은 자리에 렌더한다.**
  //
  // 예전에는 로그인 상태에 따라 서로 다른 분기에서 AuthModal 을 그렸다. 구글
  // 팝업이 성공하면 loading·isAuthed 가 잠깐 바뀌는데, 그때 분기가 갈리면서
  // React 가 모달을 언마운트하고 새로 마운트했다. 그 과정에서 error 와
  // signedUpAs 같은 내부 상태가 초기화돼, "가입되지 않은 계정입니다" 안내도
  // "회원가입이 완료되었습니다" 팝업도 뜨지 못하고 사라졌다.
  const hideCard = loading || isAuthed

  return (
    <>
      {!hideCard && (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center">
          <div className="pointer-events-auto flex flex-col items-center gap-3 rounded-xl border border-[#1e2d40] bg-[#060b14]/95 px-7 py-5 shadow-2xl backdrop-blur-sm">
            <div className="flex items-center gap-2">
              <Lock size={15} className="text-[#3b82f6]" />
              <span className="text-sm font-bold text-[#e2e8f0]">로그인 후 사용 가능</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => openAuth('login')}
                className="inline-flex items-center gap-1.5 rounded-md bg-[#3b82f6] px-4 py-1.5 text-xs font-semibold text-white transition hover:bg-[#2f6fe0]"
              >
                <LogIn size={13} /> 로그인
              </button>
              <button
                onClick={() => openAuth('signup')}
                className="inline-flex items-center gap-1.5 rounded-md border border-[#2d3f56] px-4 py-1.5 text-xs font-medium text-[#94a3b8] transition hover:border-[#3d5270] hover:bg-[#0d1526]"
              >
                <UserPlus size={13} /> 회원가입
              </button>
            </div>
          </div>
        </div>
      )}

    </>
  )
}
