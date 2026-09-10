/**
 * components/admin/AdminPanel.tsx
 * ───────────────────────────────
 * 관리자 전용 — 사이트 기능 스위치.
 *
 * 여기서 끄는 것은 **일반 사용자**에게만 적용된다. 관리자 본인은 계속 쓸 수 있다 —
 * 스위치를 내린 뒤 상태를 확인할 방법이 없으면 곤란하기 때문이다.
 */
import { useEffect, useState } from 'react'
import { X, Loader2, ShieldCheck, AlertCircle } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api'
import { cn } from '@/lib/utils'

interface Props { open: boolean; onClose: () => void }

interface Settings {
  ai_enabled: boolean
  deep_analysis_enabled: boolean
  deep_analysis_daily_limit: boolean
}

const ITEMS: { key: keyof Settings; label: string; help: string;
               onLabel?: string; offLabel?: string }[] = [
  {
    key: 'ai_enabled',
    label: 'AI 분석 기능',
    help: '끄면 일반 사용자는 리포트·시나리오를 생성할 수 없습니다. API 비용이 나가는 모든 기능이 차단됩니다.',
  },
  {
    key: 'deep_analysis_enabled',
    label: '심층 분석',
    help: '끄면 일반 사용자는 기본 분석만 쓸 수 있습니다. 심층 분석은 토큰을 훨씬 많이 소비합니다.',
  },
  {
    key: 'deep_analysis_daily_limit',
    label: '심층 분석 하루 1회 제한',
    help: '켜면 일반 사용자는 심층 분석을 계정당 24시간에 한 번만 쓸 수 있습니다. 기능을 아예 막지 않고 빈도만 조일 때 씁니다.',
    onLabel: '제한 중', offLabel: '무제한',
  },
]

export default function AdminPanel({ open, onClose }: Props) {
  const qc = useQueryClient()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [busy, setBusy]   = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setError('')
    api.get('/api/admin/settings')
      .then(r => setSettings(r.data))
      .catch(() => setError('설정을 불러오지 못했습니다.'))
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const toggle = async (key: keyof Settings) => {
    if (!settings) return
    const next = !settings[key]
    setBusy(key); setError('')
    try {
      const r = await api.patch('/api/admin/settings', { [key]: next })
      setSettings(r.data)
      // 다른 화면의 잠금 상태도 곧바로 맞춘다.
      qc.invalidateQueries({ queryKey: ['features'] })
    } catch {
      setError('설정을 저장하지 못했습니다.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div
      className="pointer-events-auto fixed inset-0 z-[110] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onClose} role="dialog" aria-modal="true" aria-label="관리자 설정"
    >
      <div className="w-full max-w-md rounded-2xl border border-[#1e2d40] bg-[#060b14] shadow-2xl"
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-[#1e2d40] px-6 py-4">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-[#e2e8f0]">
            <ShieldCheck size={18} className="text-[#10b981]" />
            관리자 설정
          </h2>
          <button onClick={onClose} aria-label="닫기"
                  className="rounded-lg p-1.5 text-[#4a5568] transition hover:bg-[#0d1526] hover:text-[#cbd5e1]">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-3 px-6 py-5">
          <p className="text-[11px] leading-relaxed text-[#4a5568]">
            아래 설정은 <span className="text-[#94a3b8]">일반 사용자에게만</span> 적용됩니다.
            관리자 계정은 영향을 받지 않습니다.
          </p>

          {!settings && !error && (
            <div className="flex items-center gap-2 py-6 text-sm text-[#4a5568]">
              <Loader2 size={15} className="animate-spin" /> 불러오는 중…
            </div>
          )}

          {settings && ITEMS.map(({ key, label, help, onLabel, offLabel }) => (
            <div key={key} className="rounded-lg border border-[#1e2d40] bg-[#0b1220] px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-[#e2e8f0]">{label}</div>
                  <div className="mt-0.5 text-[11px] leading-relaxed text-[#4a5568]">{help}</div>
                </div>
                <button
                  onClick={() => toggle(key)}
                  disabled={busy !== null}
                  role="switch" aria-checked={settings[key]} aria-label={label}
                  className={cn(
                    'relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50',
                    settings[key] ? 'bg-[#10b981]' : 'bg-[#2d3f56]'
                  )}
                >
                  <span className={cn(
                    'absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform',
                    settings[key] ? 'translate-x-[22px]' : 'translate-x-0.5'
                  )} />
                </button>
              </div>
              {/* 이 항목만 '켜짐 = 제한'이라 색 의미가 반대다 */}
              <div className={cn('mt-2 text-[10px] font-bold tracking-wider',
                                 (key === 'deep_analysis_daily_limit' ? !settings[key] : settings[key])
                                   ? 'text-emerald-400' : 'text-amber-400')}>
                {settings[key] ? (onLabel ?? '사용 가능') : (offLabel ?? '차단됨')}
              </div>
            </div>
          ))}

          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-300">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button onClick={onClose}
                  className="w-full rounded-lg border border-[#2d3f56] px-4 py-2.5 text-sm font-medium text-[#94a3b8] transition hover:bg-[#0d1526]">
            닫기
          </button>
        </div>
      </div>
    </div>
  )
}
