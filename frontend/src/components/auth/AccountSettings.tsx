/**
 * components/auth/AccountSettings.tsx
 * ───────────────────────────────────
 * 계정 설정 — 화면에 표시되는 이름을 바꾼다.
 *
 * 이메일은 로그인 식별자라 여기서 바꾸지 않는다. 바꾸려면 Firebase 인증 정보와
 * 우리 DB 를 함께 옮겨야 하고, 그 사이 한쪽만 성공하면 로그인이 막힌다.
 */
import { useEffect, useState } from 'react'
import { X, Loader2, Check, AlertCircle } from 'lucide-react'
import { useAuth } from '@/lib/AuthContext'
import { api } from '@/api'

interface Props {
  open: boolean
  onClose: () => void
}

const MAX = 20

export default function AccountSettings({ open, onClose }: Props) {
  const { profile, refreshProfile } = useAuth()
  const [name, setName]   = useState('')
  const [busy, setBusy]   = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (!open) return
    setName(profile?.name || profile?.username || '')
    setError(''); setSaved(false)
  }, [open, profile])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const trimmed = name.trim()
  const invalid =
    !trimmed ? '표시 이름을 입력해 주세요.'
    : trimmed.length > MAX ? `${MAX}자 이하로 입력해 주세요.`
    : ''

  const save = async () => {
    if (invalid) { setError(invalid); return }
    setBusy(true); setError(''); setSaved(false)
    try {
      await api.patch('/api/auth/me', { name: trimmed })
      await refreshProfile()
      setSaved(true)
    } catch (e) {
      const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setError(typeof d === 'string' ? d : '저장하지 못했습니다.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="pointer-events-auto fixed inset-0 z-[110] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="dialog" aria-modal="true" aria-label="계정 설정"
    >
      <div className="w-full max-w-md rounded-2xl border border-[#1e2d40] bg-[#060b14] shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-[#1e2d40] px-6 py-4">
          <h2 className="text-lg font-semibold text-[#e2e8f0]">계정 설정</h2>
          <button onClick={onClose} aria-label="닫기"
                  className="rounded-lg p-1.5 text-[#4a5568] transition hover:bg-[#0d1526] hover:text-[#cbd5e1]">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-4 px-6 py-5">
          <div>
            <label className="mb-1.5 block text-[10px] font-bold uppercase tracking-wider text-[#4a5568]">
              표시 이름
            </label>
            <input
              value={name}
              onChange={(e) => { setName(e.target.value); setSaved(false) }}
              onKeyDown={(e) => { if (e.key === 'Enter') save() }}
              maxLength={MAX}
              autoFocus
              placeholder="화면에 표시할 이름"
              className="w-full rounded-lg border border-[#1e2d40] bg-[#0d1526] px-3 py-2.5 text-sm text-[#e2e8f0] placeholder-[#374151] outline-none transition focus:border-[#3b82f6] focus:ring-1 focus:ring-[#3b82f6]/40"
            />
            <div className="mt-1 flex items-center justify-between">
              <span className="text-[11px] text-[#4a5568]">사이트 곳곳에 표시되는 이름입니다</span>
              <span className="text-[11px] text-[#374151]">{trimmed.length}/{MAX}</span>
            </div>
          </div>

          {/* 이메일은 로그인 식별자라 읽기 전용으로만 보여준다 */}
          <div>
            <label className="mb-1.5 block text-[10px] font-bold uppercase tracking-wider text-[#4a5568]">
              이메일
            </label>
            <div className="rounded-lg border border-[#1e2d40] bg-[#0b1220] px-3 py-2.5 text-sm text-[#4a5568]">
              {profile?.email || '—'}
            </div>
            <p className="mt-1 text-[11px] text-[#374151]">로그인에 사용되며 변경할 수 없습니다</p>
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-300">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}
          {saved && (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-900/60 bg-emerald-950/40 px-3 py-2 text-xs text-emerald-300">
              <Check size={14} /> 저장되었습니다.
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <button onClick={onClose}
                    className="flex-1 rounded-lg border border-[#2d3f56] px-4 py-2.5 text-sm font-medium text-[#94a3b8] transition hover:bg-[#0d1526]">
              닫기
            </button>
            <button onClick={save} disabled={busy || !!invalid}
                    className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-[#3b82f6] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#2f6fe0] disabled:opacity-50">
              {busy && <Loader2 size={15} className="animate-spin" />}
              저장
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
