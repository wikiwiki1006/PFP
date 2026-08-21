/**
 * components/auth/UserMenu.tsx
 * ────────────────────────────
 * 헤더의 계정 영역.
 * 비로그인이면 로그인·회원가입 버튼, 로그인 상태면 프로필 드롭다운.
 */
import { useEffect, useRef, useState } from 'react'
import { LogIn, UserPlus, LogOut, ChevronDown, Trash2, Settings } from 'lucide-react'
import { useAuth } from '@/lib/AuthContext'
import { api } from '@/api'
import AuthModal from './AuthModal'
import ConfirmDialog from './ConfirmDialog'
import AccountSettings from './AccountSettings'

export default function UserMenu() {
  const { isAuthed, loading, user, profile, logout } = useAuth()
  const [modal, setModal] = useState<'login' | 'signup' | null>(null)
  const [open, setOpen]   = useState(false)
  // 로그아웃·탈퇴는 되돌리기 어려우니 확인을 받는다.
  const [confirm, setConfirm] = useState<'logout' | 'withdraw' | null>(null)
  const [settings, setSettings] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // 바깥을 클릭하면 닫는다
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const withdraw = async () => {
    try {
      await api.delete('/api/auth/me')
    } finally {
      await logout()
    }
  }

  // 아이디가 있으면 그것을 우선 보여준다 — 사용자가 로그인할 때 쓰는 이름이다.
  const label = profile?.username || profile?.name || user?.displayName || '내 계정'
  const photo = profile?.photo_url || user?.photoURL
  const initial = (label[0] || '?').toUpperCase()

  // AuthModal 은 로그인 여부와 무관하게 **항상 같은 자리**에 렌더한다.
  // 분기마다 따로 그리면 로그인 상태가 바뀌는 순간 React 가 모달을 새로 마운트해
  // 내부 상태(오류 문구, 가입 완료 팝업)가 사라진다.
  const authModal = (
    <AuthModal open={modal !== null} initialMode={modal ?? 'login'} onClose={() => setModal(null)} />
  )

  if (loading) {
    return (
      <>
        <div className="h-8 w-20 animate-pulse rounded-lg bg-[#0d1526]" />
        {authModal}
      </>
    )
  }

  if (!isAuthed) {
    return (
      <>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setModal('login')}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-[#94a3b8] transition hover:bg-[#0d1526] hover:text-[#e2e8f0]"
          >
            <LogIn size={15} /> 로그인
          </button>
          <button
            onClick={() => setModal('signup')}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#3b82f6] px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-[#2f6fe0]"
          >
            <UserPlus size={15} /> 회원가입
          </button>
        </div>
        {authModal}
      </>
    )
  }

  return (
    <div className="relative" ref={ref}>
      {authModal}
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-lg px-2 py-1.5 transition hover:bg-[#0d1526]"
      >
        {photo ? (
          <img src={photo} alt="" className="h-7 w-7 rounded-full object-cover" />
        ) : (
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[#3b82f6] text-xs font-semibold text-white">
            {initial}
          </span>
        )}
        <span className="hidden max-w-[9rem] truncate text-sm text-[#94a3b8] sm:inline">{label}</span>
        <ChevronDown size={14} className={`text-[#4a5568] transition ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-64 overflow-hidden rounded-xl border border-[#1e2d40] bg-[#060b14] shadow-2xl">
          <div className="border-b border-[#1e2d40] px-4 py-3">
            <p className="truncate text-sm font-medium text-[#e2e8f0]">{label}</p>
            <p className="mt-0.5 truncate text-xs text-[#4a5568]">
              {profile?.email || user?.email}
            </p>
            {profile?.provider && (
              <span className="mt-2 inline-block rounded border border-[#1e2d40] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[#4a5568]">
                {providerLabel(profile.provider)}
              </span>
            )}
          </div>


          <button
            onClick={() => { setOpen(false); setSettings(true) }}
            className="flex w-full items-center gap-2 px-4 py-2.5 text-sm text-[#94a3b8] transition hover:bg-[#0d1526]"
          >
            <Settings size={15} /> 계정 설정
          </button>
          <button
            onClick={() => { setOpen(false); setConfirm('logout') }}
            className="flex w-full items-center gap-2 px-4 py-2.5 text-sm text-[#94a3b8] transition hover:bg-[#0d1526]"
          >
            <LogOut size={15} /> 로그아웃
          </button>
          <button
            onClick={() => { setOpen(false); setConfirm('withdraw') }}
            className="flex w-full items-center gap-2 border-t border-[#1e2d40] px-4 py-2.5 text-sm text-red-400 transition hover:bg-red-950/30"
          >
            <Trash2 size={15} /> 회원 탈퇴
          </button>
        </div>
      )}

      <AccountSettings open={settings} onClose={() => setSettings(false)} />

      <ConfirmDialog
        open={confirm === 'logout'}
        title="로그아웃 하시겠습니까?"
        confirmText="로그아웃"
        onConfirm={() => { setConfirm(null); logout() }}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === 'withdraw'}
        tone="danger"
        title="정말 탈퇴하시겠습니까?"
        message={'보유 종목, 거래 이력, 생성한 리포트가 모두 삭제됩니다.\n삭제된 데이터는 되돌릴 수 없습니다.'}
        confirmText="탈퇴하기"
        onConfirm={() => { setConfirm(null); withdraw() }}
        onCancel={() => setConfirm(null)}
      />
    </div>
  )
}

function providerLabel(p: string): string {
  return {
    'google.com': 'Google',
    password: '이메일',
    naver: '네이버',
    kakao: '카카오',
    custom: '연동 계정',
  }[p] ?? p
}
