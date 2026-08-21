/**
 * components/auth/AuthModal.tsx
 * ─────────────────────────────
 * 로그인 / 회원가입 모달.
 *
 * 로그인·가입 모두 **이메일 + 비밀번호**를 쓴다. 서버가 비밀번호를 대조한 뒤
 * Firebase Custom Token 을 내려주고, 프론트는 그걸로 세션을 연다.
 *
 * 소셜(구글·카카오)은 인증에 성공해도 그것만으로는 가입이 아니다. 서버에
 * 가입 여부를 물어, 로그인인데 미가입이면 세션을 닫고 안내한다. 가입이면
 * 그 자리에서 계정을 만들고 바로 로그인시킨다.
 *
 * 저장하는 정보는 이메일뿐이다. 비밀번호는 Firebase 가 관리하며 우리 DB 에
 * 남지 않는다.
 */
import { useEffect, useRef, useState } from 'react'
import { X, Mail, Lock, Loader2, AlertCircle, CheckCircle2, Check } from 'lucide-react'
import {
  loginWithGoogle, loginWithCustomToken, authErrorMessage, setRememberMe, abortSignIn,
  discardSignIn,
} from '@/lib/firebase'
import { checkPassword, checkEmail, checkPasswordConfirm } from '@/lib/credentials'
import ConfirmDialog from './ConfirmDialog'
import { useAuth } from '@/lib/AuthContext'
import { api } from '@/api'

type Mode = 'login' | 'signup' | 'reset'

interface Props {
  open: boolean
  onClose: () => void
  initialMode?: Mode
}

interface Providers {
  google: boolean; password: boolean; naver: boolean; kakao: boolean; apple: boolean
  kakao_js_key?: string
  naver_client_id?: string
  /** 카카오가 꺼져 있는 구체적 사유 (키 설정 오류 등). */
  kakao_error?: string
}

/** 서버 오류 응답에서 사용자에게 보여줄 문구를 뽑는다. */
function apiError(e: unknown, fallback: string): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof d === 'string' ? d : fallback
}

export default function AuthModal({ open, onClose, initialMode = 'login' }: Props) {
  const { refreshProfile } = useAuth()
  const [mode, setMode]         = useState<Mode>(initialMode)
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm]   = useState('')
  const [busy, setBusy]         = useState<string | null>(null)
  const [error, setError]       = useState('')
  const [notice, setNotice]     = useState('')
  const [providers, setProviders] = useState<Providers | null>(null)
  const [mailState, setMailState] = useState<'idle' | 'checking' | 'ok' | 'taken'>('idle')
  const [mailReason, setMailReason] = useState('')
  const [signedUpAs, setSignedUpAs] = useState<string | null>(null)
  // 로그인 상태 유지 — 끄면 탭을 닫는 순간 로그아웃된다(공용 PC 대비).
  const [remember, setRemember] = useState(true)
  const firstFieldRef = useRef<HTMLInputElement>(null)

  const pw       = checkPassword(password)
  const pwMatch  = checkPasswordConfirm(password, confirm)

  useEffect(() => { if (open) setMode(initialMode) }, [open, initialMode])

  useEffect(() => {
    if (!open) return
    setError(''); setNotice('')
    firstFieldRef.current?.focus()
    api.get<Providers>('/api/auth/providers')
      .then((r) => setProviders(r.data))
      .catch(() => setProviders(null))
  }, [open])

  // 이메일 중복 확인 — 입력이 멈춘 뒤에만 물어본다(타이핑마다 호출하지 않는다).
  useEffect(() => {
    if (mode !== 'signup') { setMailState('idle'); return }
    if (checkEmail(email)) { setMailState('idle'); return }
    setMailState('checking')
    const id = setTimeout(async () => {
      try {
        const { data } = await api.get<{ available: boolean; reason: string }>(
          '/api/auth/email-available', { params: { email: email.trim() } })
        setMailState(data.available ? 'ok' : 'taken')
        setMailReason(data.reason)
      } catch { setMailState('idle') }
    }, 400)
    return () => clearTimeout(id)
  }, [email, mode])

  // ESC 로 닫기
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 가입 완료 안내 — 이 시점에 이미 로그인된 상태다.
  //
  // **!open 가드보다 먼저** 검사한다. 가입에 성공하면 로그인 상태가 되면서
  // 부모가 모달을 닫을 수 있는데, 그때 아래 가드에 걸리면 완료 팝업이
  // 뜨지도 못하고 사라진다.
  // 또 `signedUpAs !== null` 로 비교한다 — 이메일을 주지 않는 카카오 가입은
  // 빈 문자열이라 truthy 검사로는 걸러진다.
  if (signedUpAs !== null) {
    return (
      <ConfirmDialog
        open
        alert
        tone="success"
        title="회원가입이 완료되었습니다"
        message={signedUpAs ? `${signedUpAs} 으로 로그인되었습니다.` : '로그인되었습니다.'}
        confirmText="시작하기"
        onConfirm={() => { setSignedUpAs(null); onClose() }}
        onCancel={() => { setSignedUpAs(null); onClose() }}
      />
    )
  }

  if (!open) return null

  const run = async (label: string, fn: () => Promise<unknown>, closeOnDone = true) => {
    setBusy(label); setError(''); setNotice('')
    try {
      await fn()
      if (closeOnDone) onClose()
    } catch (e) {
      setError(authErrorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault()

    const badEmail = checkEmail(email)
    if (badEmail) { setError(badEmail); return }

    if (mode === 'reset') {
      // 서버를 거친다 — Firebase 는 미가입 이메일에도 성공을 돌려주므로,
      // 클라이언트에서 바로 보내면 안 갔는데도 "보냈다"고 안내하게 된다.
      return run('reset', async () => {
        try {
          await api.post('/api/auth/password-reset', { email: email.trim() })
          setNotice('비밀번호 재설정 메일을 보냈습니다. 메일함(스팸함 포함)을 확인해 주세요.')
        } catch (e) {
          throw new Error(apiError(e, '메일을 보내지 못했습니다.'))
        }
      }, false)
    }

    if (mode === 'signup') {
      if (mailState === 'taken') { setError(mailReason || '이미 가입된 이메일입니다.'); return }
      if (!pw.ok) { setError('비밀번호 조건을 모두 충족해 주세요.'); return }
      if (password !== confirm) { setError('비밀번호가 일치하지 않습니다.'); return }

      // 가입에 성공하면 서버가 Custom Token 을 주므로 그대로 로그인된다.
      return run('submit', async () => {
        try {
          const { data } = await api.post<{ custom_token: string; email: string }>(
            '/api/auth/signup', { email: email.trim(), password })
          await setRememberMe(remember)
          await loginWithCustomToken(data.custom_token)
          await refreshProfile()
          setSignedUpAs(data.email)
        } catch (e) {
          throw new Error(apiError(e, '가입에 실패했습니다.'))
        }
      }, false)   // 완료 안내를 확인한 뒤에 닫는다
    }

    if (!password) { setError('비밀번호를 입력해 주세요.'); return }
    return run('submit', async () => {
      try {
        const { data } = await api.post<{ custom_token: string }>('/api/auth/login', {
          email: email.trim(), password,
        })
        // 세션이 만들어지기 전에 지속성을 정해야 적용된다.
        await setRememberMe(remember)
        await loginWithCustomToken(data.custom_token)
      } catch (e) {
        throw new Error(apiError(e, '이메일 또는 비밀번호가 올바르지 않습니다.'))
      }
    })
  }

  /**
   * 소셜 인증 직후의 분기.
   *
   * Firebase 인증에 성공했다고 가입한 것은 아니다 — 구글 팝업은 처음 누르는
   * 순간 Firebase 계정을 만들어 준다. 서버에 가입 여부를 물어,
   *   · 이미 가입 → 로그인 완료
   *   · 가입 화면 → 그 자리에서 계정 생성 후 로그인
   *   · 로그인 화면인데 미가입 → 세션을 닫고 "가입되지 않은 계정" 안내
   */
  const afterSocialAuth = async () => {
    // 가입 여부를 **먼저** 확인한다. 프로필을 받아오는 경로(/auth/me)로 판단하면
    // 그 사이에 세션이 살아 있어 화면이 잠깐 로그인 상태로 보인다.
    const { data } = await api.get<{ registered: boolean }>('/api/auth/status')

    if (data.registered) {
      if (mode === 'signup') {
        // 가입 화면인데 이미 계정이 있다 — 그냥 로그인시키면 "가입됐다"고
        // 오해한다. 이미 있음을 알리고 로그인으로 유도한다.
        await abortSignIn()
        setMode('login')
        throw new Error('이미 가입된 계정입니다. 로그인해 주세요.')
      }
      await refreshProfile()      // isAuthed 를 올려 준 뒤 닫는다
      onClose()
      return
    }

    if (mode !== 'signup') {
      // 미가입 로그인 시도 — 방금 만들어진 계정이면 지운다. 남겨 두면 같은
      // 이메일에 계정이 둘처럼 보인다.
      await discardSignIn()
      setMode('login')
      throw new Error('가입되지 않은 계정입니다. 회원가입을 먼저 진행해 주세요.')
    }

    {
      try {
        const { data: reg } = await api.post<{ email: string | null }>('/api/auth/social/register')
        // 가입 전 /auth/me 가 403 이라 프로필이 비어 있다 — 지금 다시 받는다.
        await refreshProfile()
        setSignedUpAs(reg.email || '')
      } catch (e) {
        // 이미 다른 방법으로 쓰는 이메일 등 — 가입이 끝나지 않았다.
        // 방금 만들어진 계정이면 지워 이메일 중복 계정이 쌓이지 않게 한다.
        await discardSignIn()
        throw new Error(apiError(e, '가입에 실패했습니다.'))
      }
      return
    }
  }

  const googleAuth = () =>
    // 닫기는 afterSocialAuth 가 판단한다 (가입 완료 안내가 남을 수 있다).
    run('google', async () => {
      await setRememberMe(remember)
      await loginWithGoogle()
      await afterSocialAuth()
    }, false)

  // Naver·Kakao: 서버가 액세스 토큰을 검증한 뒤 Custom Token 을 내려준다.
  const socialViaServer = (path: 'naver' | 'kakao', accessToken: string) =>
    run(path, async () => {
      try {
        const { data } = await api.post<{ custom_token: string }>(`/api/auth/${path}`, {
          access_token: accessToken, signup: mode === 'signup',
        })
        await setRememberMe(remember)
        await loginWithCustomToken(data.custom_token)
      } catch (e) {
        throw new Error(apiError(e, '소셜 로그인에 실패했습니다.'))
      }
      await afterSocialAuth()
    }, false)

  const kakaoLogin = () => {
    if (!providers?.kakao) {
      setError(providers?.kakao_error || '카카오 로그인이 아직 설정되지 않았습니다.')
      return
    }
    setBusy('kakao'); setError('')

    // JavaScript 키가 있으면 SDK 가 간단하다. REST 키만 있으면 표준 OAuth
    // 인가 코드 흐름을 쓴다 — Kakao.init() 은 JS 키만 받기 때문이다.
    const flow = providers.kakao_js_key
      ? startKakaoLogin(providers.kakao_js_key).then((t) => ({ kind: 'token' as const, value: t }))
      : startKakaoOAuth().then((c) => ({ kind: 'code' as const, value: c }))

    flow
      .then(async (r) => {
        setBusy(null)
        if (r.kind === 'token') return socialViaServer('kakao', r.value)
        await run('kakao', async () => {
          try {
            const { data } = await api.post<{ custom_token: string }>('/api/auth/kakao/callback', {
              code: r.value, redirect_uri: kakaoRedirectUri(), signup: mode === 'signup',
            })
            await setRememberMe(remember)
            await loginWithCustomToken(data.custom_token)
          } catch (e) {
            throw new Error(apiError(e, '카카오 로그인에 실패했습니다.'))
          }
          await afterSocialAuth()
        }, false)
      })
      .catch((e) => { setBusy(null); setError(e?.message || '카카오 로그인에 실패했습니다.') })
  }

  const title = mode === 'signup' ? '회원가입' : mode === 'reset' ? '비밀번호 재설정' : '로그인'

  return (
    <div
      // pointer-events-auto 필수: AuthOverlay 처럼 pointer-events-none 컨테이너
      // 안에서 열리면 그 값이 상속돼 모달 내부 클릭이 전부 무시된다.
      className="pointer-events-auto fixed inset-0 z-[100] flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-[#1e2d40] bg-[#060b14] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between border-b border-[#1e2d40] px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-[#e2e8f0]">{title}</h2>
            {mode === 'reset' && (
              <p className="mt-0.5 text-xs text-[#4a5568]">가입한 이메일로 재설정 링크를 보냅니다.</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-[#4a5568] transition hover:bg-[#0d1526] hover:text-[#cbd5e1]"
            aria-label="닫기"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-6 py-5">
          {/* ── 로그인: 이메일·비밀번호를 위에, 소셜을 아래에 ── */}
          {mode === 'login' && (
            <form onSubmit={submit} className="space-y-3">
              <div className="flex gap-2.5">
                <div className="flex-1 space-y-2">
                  <LabeledInput
                    label="이메일"
                    inputRef={firstFieldRef}
                    type="email"
                    value={email}
                    onChange={setEmail}
                    autoComplete="email"
                  />
                  <LabeledInput
                    label="비밀번호"
                    type="password"
                    value={password}
                    onChange={setPassword}
                    autoComplete="current-password"
                  />
                </div>
                {/* 두 줄 높이를 채우는 세로형 로그인 버튼 */}
                <button
                  type="submit"
                  disabled={!!busy}
                  className="flex w-[86px] shrink-0 flex-col items-center justify-center rounded-lg bg-[#3b82f6] text-sm font-bold text-white transition hover:bg-[#2f6fe0] disabled:opacity-50"
                >
                  {busy === 'submit'
                    ? <Loader2 size={18} className="animate-spin" />
                    : '로그인'}
                </button>
              </div>

              <div className="flex items-center justify-between text-xs">
                <label className="flex cursor-pointer select-none items-center gap-1.5 text-[#7d8ca3]">
                  <input
                    type="checkbox"
                    checked={remember}
                    onChange={(e) => setRemember(e.target.checked)}
                    className="h-3.5 w-3.5 accent-[#3b82f6]"
                  />
                  로그인 상태 유지
                </label>
                <div className="flex items-center gap-2 text-[#7d8ca3]">
                  <button type="button"
                          onClick={() => { setMode('signup'); setError(''); setNotice('') }}
                          className="transition hover:text-[#e2e8f0]">
                    회원가입
                  </button>
                  <span className="text-[#1e2d40]">|</span>
                  <button type="button"
                          onClick={() => { setMode('reset'); setError(''); setNotice('') }}
                          className="transition hover:text-[#e2e8f0]">
                    비밀번호 찾기
                  </button>
                </div>
              </div>

              {error && <ErrorBox text={error} />}
            </form>
          )}

          {/* ── 회원가입 / 비밀번호 재설정 ── */}
          {mode !== 'login' && (
            <form onSubmit={submit} className="space-y-3">
              <div>
                <Field
                  icon={<Mail size={15} />}
                  inputRef={firstFieldRef}
                  type="email"
                  placeholder="이메일"
                  value={email}
                  onChange={setEmail}
                  autoComplete="email"
                />
                {mode === 'signup' && email && (
                  <p className={`mt-1 text-[11px] ${
                    mailState === 'ok' ? 'text-[#10b981]'
                    : mailState === 'taken' || checkEmail(email) ? 'text-[#ef4444]'
                    : 'text-[#4a5568]'
                  }`}>
                    {checkEmail(email)
                      || (mailState === 'checking' ? '확인 중…'
                        : mailState === 'ok' ? '사용할 수 있는 이메일입니다.'
                        : mailState === 'taken' ? (mailReason || '이미 가입된 이메일입니다.')
                        : '')}
                  </p>
                )}
              </div>

              {mode === 'signup' && (
                <>
                  <Field
                    icon={<Lock size={15} />}
                    type="password"
                    placeholder="비밀번호"
                    value={password}
                    onChange={setPassword}
                    autoComplete="new-password"
                  />
                  <div>
                    <Field
                      icon={<Lock size={15} />}
                      type="password"
                      placeholder="비밀번호 확인"
                      value={confirm}
                      onChange={setConfirm}
                      autoComplete="new-password"
                    />
                    {confirm && (
                      <p className={`mt-1 text-[11px] ${pwMatch ? 'text-[#ef4444]' : 'text-[#10b981]'}`}>
                        {pwMatch || '비밀번호가 일치합니다.'}
                      </p>
                    )}
                  </div>

                  {password && (
                    <div className="grid grid-cols-2 gap-x-3 gap-y-1 rounded-lg border border-[#1e2d40] bg-[#0d1526] px-3 py-2">
                      <Rule ok={pw.length}  text="8자 이상" />
                      <Rule ok={pw.letter}  text="영문 포함" />
                      <Rule ok={pw.digit}   text="숫자 포함" />
                      <Rule ok={pw.special} text="특수문자 포함" />
                    </div>
                  )}
                </>
              )}

              {error && <ErrorBox text={error} />}
              {notice && (
                <div className="flex items-start gap-2 rounded-lg border border-emerald-900/60 bg-emerald-950/40 px-3 py-2 text-xs text-emerald-300">
                  <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
                  <span>{notice}</span>
                </div>
              )}

              <button
                type="submit"
                disabled={!!busy}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-[#3b82f6] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#2f6fe0] disabled:opacity-50"
              >
                {(busy === 'submit' || busy === 'reset') && <Loader2 size={15} className="animate-spin" />}
                {mode === 'signup' ? '가입하기' : '재설정 메일 보내기'}
              </button>
            </form>
          )}

          {/* ── 소셜 로그인 ── */}
          {mode !== 'reset' && (
            <>
              <div className="my-4 h-px bg-[#1e2d40]" />
              <div className="grid grid-cols-2 gap-2">
                <SocialButton
                  label={mode === 'signup' ? '네이버로 가입' : '네이버 로그인'}
                  icon={<NaverIcon />}
                  enabled={!!providers?.naver}
                  busy={busy === 'naver'}
                  disabled={!!busy}
                  onClick={() => setError('네이버 로그인이 아직 설정되지 않았습니다.')}
                />
                <SocialButton
                  label={mode === 'signup' ? '카카오로 가입' : '카카오 로그인'}
                  icon={<KakaoIcon />}
                  enabled={!!providers?.kakao}
                  busy={busy === 'kakao'}
                  disabled={!!busy}
                  onClick={kakaoLogin}
                />
              </div>
              <button
                onClick={() => providers && !providers.google
                  ? setError('Google 로그인이 아직 설정되지 않았습니다.')
                  : googleAuth()}
                disabled={!!busy}
                className={`mt-2 flex w-full items-center justify-center gap-2 rounded-lg border px-4 py-2.5 text-sm font-medium transition disabled:opacity-50 ${
                  providers && !providers.google
                    ? 'border-[#1e2d40] bg-[#0d1526] text-[#374151]'
                    : 'border-[#1e2d40] bg-[#0d1526] text-[#cbd5e1] hover:bg-[#111c2e]'
                }`}
              >
                {busy === 'google'
                  ? <Loader2 size={15} className="animate-spin" />
                  : <GoogleIcon />}
                {mode === 'signup' ? 'Google로 가입' : 'Google 로그인'}
                {providers && !providers.google &&
                  <span className="text-[10px] text-[#2a3444]">준비중</span>}
              </button>
            </>
          )}

          {/* 모드 전환 — 로그인 화면은 폼 안에 링크가 있어 여기서는 생략한다 */}
          {mode !== 'login' && (
            <div className="mt-4 text-xs">
              <button onClick={() => { setMode('login'); setError(''); setNotice('') }}
                      className="text-[#60a5fa] transition hover:text-[#93c5fd]">
                ← 로그인으로 돌아가기
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── 하위 컴포넌트 ──────────────────────────────────────────────────────────────

function Rule({ ok, text }: { ok: boolean; text: string }) {
  return (
    <span className={`flex items-center gap-1.5 text-[11px] ${ok ? 'text-[#10b981]' : 'text-[#4a5568]'}`}>
      <Check size={11} className={ok ? '' : 'opacity-30'} />
      {text}
    </span>
  )
}

function Field({
  icon, type, placeholder, value, onChange, autoComplete, inputRef,
}: {
  icon: React.ReactNode
  type: string
  placeholder: string
  value: string
  onChange: (v: string) => void
  autoComplete?: string
  inputRef?: React.RefObject<HTMLInputElement>
}) {
  return (
    <div className="relative">
      <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#374151]">
        {icon}
      </span>
      <input
        ref={inputRef}
        type={type}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        className="w-full rounded-lg border border-[#1e2d40] bg-[#0d1526] py-2.5 pl-9 pr-3 text-sm text-[#e2e8f0] placeholder-[#374151] outline-none transition focus:border-[#3b82f6] focus:ring-1 focus:ring-[#3b82f6]/40"
      />
    </div>
  )
}

function ErrorBox({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-300">
      <AlertCircle size={14} className="mt-0.5 shrink-0" />
      <span>{text}</span>
    </div>
  )
}

/** 라벨이 왼쪽에 붙는 입력 — 로그인 폼 전용 배치. */
function LabeledInput({
  label, type, value, onChange, autoComplete, inputRef,
}: {
  label: string
  type: string
  value: string
  onChange: (v: string) => void
  autoComplete?: string
  inputRef?: React.RefObject<HTMLInputElement>
}) {
  return (
    <label className="flex items-center gap-2.5">
      <span className="w-14 shrink-0 text-right text-xs text-[#7d8ca3]">{label}</span>
      <input
        ref={inputRef}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        className="min-w-0 flex-1 rounded-lg border border-[#1e2d40] bg-[#0d1526] px-3 py-2.5 text-sm text-[#e2e8f0] outline-none transition focus:border-[#3b82f6] focus:ring-1 focus:ring-[#3b82f6]/40"
      />
    </label>
  )
}

function SocialButton({
  label, icon, enabled, busy, disabled, onClick,
}: {
  label: string; icon: React.ReactNode; enabled: boolean; busy: boolean
  disabled: boolean; onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={enabled ? label : `${label}은 아직 설정되지 않았습니다`}
      className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium transition disabled:opacity-50 ${
        enabled
          ? 'border-[#1e2d40] bg-[#0d1526] text-[#e2e8f0] hover:bg-[#111c2e]'
          : 'border-[#1e2d40] bg-[#0d1526] text-[#374151]'
      }`}
    >
      {busy ? <Loader2 size={15} className="animate-spin" /> : <span className={enabled ? '' : 'opacity-40'}>{icon}</span>}
      <span className="truncate">{label}</span>
      {!enabled && <span className="text-[10px] text-[#2a3444]">준비중</span>}
    </button>
  )
}

function NaverIcon() {
  return (
    <span className="flex h-[18px] w-[18px] items-center justify-center rounded-sm bg-[#03C75A]">
      <svg width="9" height="9" viewBox="0 0 20 20" aria-hidden="true">
        <path fill="#fff" d="M12.2 10.7 7.5 4H3.5v12h4.3V9.3l4.7 6.7h4V4h-4.3z"/>
      </svg>
    </span>
  )
}

function KakaoIcon() {
  return (
    <span className="flex h-[18px] w-[18px] items-center justify-center rounded-sm bg-[#FEE500]">
      <svg width="11" height="11" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="#191600" d="M12 4C7 4 3 7.1 3 10.9c0 2.4 1.6 4.5 4.1 5.7l-.9 3.3c-.1.3.2.5.5.4l3.9-2.6c.5.1.9.1 1.4.1 5 0 9-3.1 9-6.9S17 4 12 4z"/>
      </svg>
    </span>
  )
}

function GoogleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.76h3.57c2.08-1.92 3.27-4.74 3.27-8.09z"/>
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.76c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z"/>
      <path fill="#FBBC05" d="M5.84 14.11a6.6 6.6 0 0 1 0-4.22V7.05H2.18a11 11 0 0 0 0 9.9l3.66-2.84z"/>
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1a11 11 0 0 0-9.82 6.05l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z"/>
    </svg>
  )
}

// ── 카카오 로그인 ──────────────────────────────────────────────────────────────
// 카카오는 Firebase 가 지원하지 않으므로 JS SDK 로 액세스 토큰을 받아 서버에 넘긴다.
// 서버가 카카오 API 로 그 토큰을 검증한 뒤 Firebase Custom Token 을 발급한다.
// (클라이언트가 보낸 프로필을 그대로 믿으면 남의 계정을 사칭할 수 있다.)

declare global {
  interface Window {
    Kakao?: {
      isInitialized: () => boolean
      init: (key: string) => void
      Auth: {
        login: (opts: {
          scope?: string
          success: (r: { access_token: string }) => void
          fail: (e: unknown) => void
        }) => void
      }
    }
  }
}

const KAKAO_SDK_SRC = 'https://t1.kakaocdn.net/kakao_js_sdk/2.7.4/kakao.min.js'

function loadKakaoSdk(): Promise<void> {
  if (window.Kakao) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${KAKAO_SDK_SRC}"]`)
    if (existing) {
      existing.addEventListener('load', () => resolve())
      existing.addEventListener('error', () => reject(new Error('카카오 SDK를 불러오지 못했습니다.')))
      return
    }
    const el = document.createElement('script')
    el.src = KAKAO_SDK_SRC
    el.crossOrigin = 'anonymous'
    el.onload = () => resolve()
    el.onerror = () => reject(new Error('카카오 SDK를 불러오지 못했습니다.'))
    document.head.appendChild(el)
  })
}

/** 카카오 로그인 창을 띄우고 액세스 토큰을 돌려준다. */
async function startKakaoLogin(jsKey: string): Promise<string> {
  await loadKakaoSdk()
  const Kakao = window.Kakao
  if (!Kakao) throw new Error('카카오 SDK를 사용할 수 없습니다.')
  if (!Kakao.isInitialized()) Kakao.init(jsKey)

  return new Promise<string>((resolve, reject) => {
    Kakao.Auth.login({
      // scope 를 지정하지 않는다 — 콘솔에 설정된 동의항목이 그대로 적용된다.
      // account_email 을 강제로 요청하면 권한이 없는 앱에서는 로그인이 막힌다.
      success: (r) => resolve(r.access_token),
      fail: () => reject(new Error('카카오 로그인이 취소되었습니다.')),
    })
  })
}


// ── 카카오 REST OAuth (JavaScript 키 없이 REST 키만으로) ──────────────────────
// 인가 페이지를 팝업으로 열고, 리다이렉트된 콜백 페이지가 postMessage 로
// 인가 코드를 돌려준다. 코드를 토큰으로 바꾸는 건 서버가 한다.

export function kakaoRedirectUri(): string {
  return `${window.location.origin}/auth/kakao/callback`
}

interface KakaoOAuthMessage {
  source: 'pfp-kakao-oauth'
  code?: string | null
  error?: string | null
}

async function startKakaoOAuth(): Promise<string> {
  const redirectUri = kakaoRedirectUri()
  const { data } = await api.get<{ url: string }>('/api/auth/kakao/authorize-url', {
    params: { redirect_uri: redirectUri },
  })

  const w = 480, h = 640
  const left = window.screenX + (window.outerWidth - w) / 2
  const top  = window.screenY + (window.outerHeight - h) / 2
  const popup = window.open(data.url, 'kakao-login',
    `width=${w},height=${h},left=${left},top=${top}`)
  if (!popup) throw new Error('팝업이 차단됐습니다. 브라우저 설정을 확인해 주세요.')

  return new Promise<string>((resolve, reject) => {
    let done = false

    const cleanup = () => {
      window.removeEventListener('message', onMessage)
      clearInterval(closedTimer)
    }

    const onMessage = (e: MessageEvent<KakaoOAuthMessage>) => {
      // 우리 콜백 페이지가 보낸 메시지만 받는다 — 다른 출처의 위조를 막는다.
      if (e.origin !== window.location.origin) return
      if (e.data?.source !== 'pfp-kakao-oauth') return
      done = true; cleanup(); popup.close()
      if (e.data.code) resolve(e.data.code)
      else reject(new Error(e.data.error || '카카오 로그인이 취소되었습니다.'))
    }
    window.addEventListener('message', onMessage)

    // 사용자가 팝업을 그냥 닫으면 메시지가 오지 않는다 — 그 경우도 마무리한다.
    const closedTimer = setInterval(() => {
      if (popup.closed && !done) {
        cleanup()
        reject(new Error('카카오 로그인이 취소되었습니다.'))
      }
    }, 500)
  })
}
