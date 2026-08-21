/**
 * components/auth/ConfirmDialog.tsx
 * ─────────────────────────────────
 * 확인 팝업.
 *
 * 브라우저 기본 confirm() 은 앱과 생김새가 따로 놀고 문구도 꾸밀 수 없어
 * 직접 만든다. 되돌릴 수 없는 동작(탈퇴)에는 tone="danger" 를 준다.
 */
import { useEffect } from 'react'
import { AlertTriangle, CheckCircle2, LogOut } from 'lucide-react'

type Tone = 'default' | 'danger' | 'success'

interface Props {
  open: boolean
  title: string
  message?: string
  confirmText?: string
  cancelText?: string
  tone?: Tone
  /** 확인만 필요한 알림이면 true — 취소 버튼을 숨긴다. */
  alert?: boolean
  onConfirm: () => void
  onCancel: () => void
}

const ICONS: Record<Tone, typeof AlertTriangle> = {
  default: LogOut,
  danger:  AlertTriangle,
  success: CheckCircle2,
}

const ACCENT: Record<Tone, string> = {
  default: 'text-[#3b82f6]',
  danger:  'text-[#ef4444]',
  success: 'text-[#10b981]',
}

const CONFIRM_BG: Record<Tone, string> = {
  default: 'bg-[#3b82f6] hover:bg-[#2f6fe0]',
  danger:  'bg-[#dc2626] hover:bg-[#b91c1c]',
  success: 'bg-[#10b981] hover:bg-[#059669]',
}

export default function ConfirmDialog({
  open, title, message, confirmText = '확인', cancelText = '취소',
  tone = 'default', alert = false, onConfirm, onCancel,
}: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
      if (e.key === 'Enter') onConfirm()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel, onConfirm])

  if (!open) return null

  const Icon = ICONS[tone]

  return (
    <div
      className="pointer-events-auto fixed inset-0 z-[110] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onCancel}
      role="alertdialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="w-full max-w-sm rounded-2xl border border-[#1e2d40] bg-[#060b14] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-col items-center text-center">
          <Icon size={26} className={`mb-3 ${ACCENT[tone]}`} />
          <h3 className="text-base font-semibold text-[#e2e8f0]">{title}</h3>
          {message && (
            <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-[#7d8ca3]">
              {message}
            </p>
          )}
        </div>

        <div className="mt-6 flex gap-2">
          {!alert && (
            <button
              onClick={onCancel}
              className="flex-1 rounded-lg border border-[#2d3f56] px-4 py-2.5 text-sm font-medium text-[#94a3b8] transition hover:border-[#3d5270] hover:bg-[#0d1526]"
            >
              {cancelText}
            </button>
          )}
          <button
            onClick={onConfirm}
            autoFocus
            className={`flex-1 rounded-lg px-4 py-2.5 text-sm font-semibold text-white transition ${CONFIRM_BG[tone]}`}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}
