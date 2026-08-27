/**
 * components/auth/UserMenu.tsx
 * ────────────────────────────
 * 헤더의 계정 영역.
 * 비로그인이면 로그인·회원가입 버튼, 로그인 상태면 프로필 드롭다운.
 */
import { useEffect, useRef, useState } from 'react'
import { LogIn, UserPlus, LogOut, ChevronDown, Trash2, Settings, ShieldCheck } from 'lucide-react'
import { useAuth } from '@/lib/AuthContext'
import { api } from '@/api'
import ConfirmDialog from './ConfirmDialog'
import AccountSettings from './AccountSettings'
import AdminPanel from '../admin/AdminPanel'
import { useFeatures } from '@/lib/useFeatures'

export default function UserMenu() {
  const { isAuthed, loading, user, profile, logout, openAuth } = useAuth()
  const [open, setOpen]   = useState(false)
  // 로그아웃·탈퇴는 되돌리기 어려우니 확인을 받는다.
  const [confirm, setConfirm] = useState<'logout' | 'withdraw' | null>(null)
  const [settings, setSettings] = useState(false)
  const [adminOpen, setAdminOpen] = useState(false)
  const features = useFeatures()
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

  // 사용자가 정한 닉네임(name)이 최우선이다. username 은 이메일에서 자동 생성한
  // 표시용 대체값이라, 닉네임을 입력했는데도 그게 계속 보이면 입력이 무시된 것처럼
  // 보인다. 로그인 식별자는 이메일이므로 username 을 우선할 이유도 없다.
  const label = profile?.name || profile?.username || user?.displayName || '내 계정'
  const photo = profile?.photo_url || user?.photoURL
  const initial = (label[0] || '?').toUpperCase()

  if (loading) {
    return (
      <>
        <div className="h-8 w-20 animate-pulse rounded-lg bg-[#0d1526]" />
      </>
    )
  }

  if (!isAuthed) {
    return (
      <>
        <div className="flex items-center gap-2">
          <button
            onClick={() => openAuth('login')}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-[#94a3b8] transition hover:bg-[#0d1526] hover:text-[#e2e8f0]"
          >
            <LogIn size={15} /> 로그인
          </button>
          <button
            onClick={() => openAuth('signup')}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#3b82f6] px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-[#2f6fe0]"
          >
            <UserPlus size={15} /> 회원가입
          </button>
        </div>
      </>
    )
  }

  return (
    <div className="relative" ref={ref}>
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


          {features.is_admin && (
            <button
              onClick={() => { setOpen(false); setAdminOpen(true) }}
              className="flex w-full items-center gap-2 px-4 py-2.5 text-sm text-[#3b82f6] transition hover:bg-[#0d1526]"
            >
              <ShieldCheck size={15} /> 관리자 설정
            </button>
          )}
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
      <AdminPanel open={adminOpen} onClose={() => setAdminOpen(false)} />

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
