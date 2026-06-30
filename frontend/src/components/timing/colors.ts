// Timing Engine 공용 색상 규칙: 상승/매수=초록, 하락/매도=빨강, 중립/횡보=노랑
export type Direction = 'up' | 'down' | 'neutral'

export const COLOR_UP      = '#10b981'
export const COLOR_DOWN    = '#ef4444'
export const COLOR_NEUTRAL = '#f59e0b'

export function directionColor(dir: Direction): string {
  if (dir === 'up') return COLOR_UP
  if (dir === 'down') return COLOR_DOWN
  return COLOR_NEUTRAL
}

// z-score: 음수(과매도)=매수 신호=초록, 양수(과매수)=매도 신호=빨강
export function zDirection(z: number, threshold = 1.0): Direction {
  if (z <= -threshold) return 'up'
  if (z >= threshold) return 'down'
  return 'neutral'
}

export function biasColor(bias: 'LONG' | 'SHORT' | 'NEUTRAL'): string {
  if (bias === 'LONG') return COLOR_UP
  if (bias === 'SHORT') return COLOR_DOWN
  return COLOR_NEUTRAL
}

export function biasLabel(bias: 'LONG' | 'SHORT' | 'NEUTRAL'): string {
  if (bias === 'LONG') return '롱 우세'
  if (bias === 'SHORT') return '숏 우세'
  return '중립'
}

export function pctDirection(pct: number, threshold = 0.05): Direction {
  if (pct > threshold) return 'up'
  if (pct < -threshold) return 'down'
  return 'neutral'
}

export function regimeColor(regime: string): string {
  if (regime === 'Bull') return COLOR_UP
  if (regime === 'Bear') return COLOR_DOWN
  return COLOR_NEUTRAL
}
