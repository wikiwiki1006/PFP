/**
 * components/onboarding/TourContext.tsx
 * ─────────────────────────────────────
 * 처음 가입한 사용자에게 화면별 기능을 순서대로 짚어 주는 투어.
 *
 * 왜 컨텍스트인가: 투어는 화면(라우트)을 넘나든다. 포트폴리오에서 시작해
 * 시나리오·최적화·신호·리서치까지 안내한 뒤 마지막에 포트폴리오 등록으로
 * 데려가야 한다. 각 페이지가 제 단계를 들고 있으면 순서를 맞출 수 없다.
 *
 * 진행 상태는 localStorage 에 남긴다. 투어 도중 새로고침하거나 나갔다 와도
 * 처음부터 다시 시작하지 않는다. 한 번 끝냈으면 다시 뜨지 않는다.
 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from 'react'
import { useNavigate } from 'react-router-dom'

/** 투어 한 단계. anchor 는 화면에서 가리킬 요소의 data-tour 값. */
export interface TourStep {
  /** 이 단계를 보여줄 라우트 */
  route: string
  /** 가리킬 요소 — data-tour="..." 로 표시해 둔다. 없으면 화면 가운데에 띄운다. */
  anchor?: string
  title: string
  body: string
}

export const TOUR_STEPS: TourStep[] = [
  {
    route: '/terminal',
    title: '포트폴리오 화면입니다',
    body: '보유 종목의 수익률과 자산 변화를 한곳에서 봅니다. 먼저 화면을 훑어볼게요.',
  },
  {
    route: '/terminal', anchor: 'metrics',
    title: '핵심 지표',
    body: '총 자산과 기간별 수익률입니다. 옆으로 밀면 더 많은 지표를 볼 수 있습니다.',
  },
  {
    route: '/terminal', anchor: 'search',
    title: '종목 검색',
    body: '티커나 회사 이름을 넣으면 차트·재무·리스크 지표를 한 창에서 확인할 수 있습니다.',
  },
  {
    route: '/terminal', anchor: 'equity',
    title: '자산 추이',
    body: '내 포트폴리오와 S&P 500·나스닥을 같은 기간으로 비교합니다.',
  },
  {
    route: '/terminal', anchor: 'holdings',
    title: '보유 종목',
    body: '종목별 평단가와 손익입니다. 여기서 매수·매도 기록을 추가할 수 있습니다.',
  },
  {
    route: '/terminal', anchor: 'brief',
    title: 'AI 브리핑과 피드백',
    body: '전날 시장 요약과 내 포트폴리오에 대한 AI 의견을 받아볼 수 있습니다.',
  },
  {
    route: '/macro',
    title: '시장 시나리오',
    body: '"금리가 급등하면?" 같은 상황을 가정해 내 포트폴리오가 어떻게 움직일지 분석합니다. 프리셋을 고르거나 직접 써도 됩니다.',
  },
  {
    route: '/optimizer',
    title: '포트폴리오 최적화',
    body: '종목을 넣고 목표 수익률을 정하면 위험 대비 효율이 좋은 비중을 계산해 줍니다.',
  },
  {
    route: '/timing',
    title: '트레이딩 신호',
    body: '종목의 추세와 과열·침체 구간, 유사 종목 쌍을 찾아 매매 시점 판단을 돕습니다.',
  },
  {
    route: '/lens',
    title: 'AI 리서치',
    body: '종목이나 산업을 고르면 AI가 리서치 리포트를 만들어 줍니다. 만든 리포트는 과거 레포트 탭에 쌓입니다.',
  },
  {
    route: '/terminal', anchor: 'setup',
    title: '이제 포트폴리오를 등록해 볼까요',
    body: '보유 종목과 현금을 넣으면 위 기능들이 내 자산 기준으로 계산됩니다. 한 단계씩 안내해 드릴게요.',
  },
]

const KEY_STEP = 'pfp_tour_step'
const KEY_DONE = 'pfp_tour_done'

interface TourValue {
  active: boolean
  step: number
  total: number
  current: TourStep | null
  next: () => void
  prev: () => void
  skip: () => void
  start: () => void
  /** 마지막 단계에서 '등록 시작'을 눌렀는지 — 포트폴리오 화면이 마법사를 연다. */
  wantsSetup: boolean
  clearWantsSetup: () => void
}

const Ctx = createContext<TourValue | null>(null)

export function useTour(): TourValue {
  const v = useContext(Ctx)
  if (!v) throw new Error('useTour 는 TourProvider 안에서만 쓸 수 있습니다.')
  return v
}

function read(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}
function write(key: string, val: string | null) {
  try { val === null ? localStorage.removeItem(key) : localStorage.setItem(key, val) } catch { /* 저장 못 해도 투어는 돈다 */ }
}

export function TourProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const [step, setStep] = useState<number>(() => {
    if (read(KEY_DONE)) return -1
    // 저장값이 없으면 투어를 시작하지 않는다. Number(null) 이 0 이라서
    // 그냥 Number() 로 변환하면 '저장 안 됨'이 '0단계'가 되어, 가입한 적도
    // 없는 첫 방문자에게 투어가 떠 화면을 막는다.
    const raw = read(KEY_STEP)
    if (raw === null) return -1
    const s = Number(raw)
    return Number.isInteger(s) && s >= 0 && s < TOUR_STEPS.length ? s : -1
  })
  const [wantsSetup, setWantsSetup] = useState(false)

  const active  = step >= 0 && step < TOUR_STEPS.length
  const current = active ? TOUR_STEPS[step] : null

  // 단계가 바뀌면 그 단계의 화면으로 옮긴다.
  useEffect(() => {
    if (!current) return
    write(KEY_STEP, String(step))
    if (window.location.pathname !== current.route) navigate(current.route)
  }, [step, current, navigate])

  const finish = useCallback(() => {
    setStep(-1)
    write(KEY_STEP, null)
    write(KEY_DONE, '1')
  }, [])

  const next = useCallback(() => {
    setStep(s => {
      if (s + 1 >= TOUR_STEPS.length) {
        // 마지막 단계를 넘기면 곧바로 포트폴리오 등록으로 이어 준다.
        setWantsSetup(true)
        write(KEY_STEP, null)
        write(KEY_DONE, '1')
        return -1
      }
      return s + 1
    })
  }, [])

  const prev  = useCallback(() => setStep(s => (s > 0 ? s - 1 : s)), [])
  const skip  = useCallback(() => finish(), [finish])
  const start = useCallback(() => {
    write(KEY_DONE, null)
    setStep(0)
  }, [])

  const value = useMemo<TourValue>(() => ({
    active, step, total: TOUR_STEPS.length, current,
    next, prev, skip, start,
    wantsSetup, clearWantsSetup: () => setWantsSetup(false),
  }), [active, step, current, next, prev, skip, start, wantsSetup])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
