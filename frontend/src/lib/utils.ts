import { clsx, type ClassValue } from 'clsx'

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs)
}

export function formatCurrency(value: number, decimals = 2): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value)
}

export function formatLargeNumber(value: number): string {
  if (Math.abs(value) >= 1_000_000) {
    return `$${(value / 1_000_000).toFixed(2)}M`
  }
  if (Math.abs(value) >= 1_000) {
    return `$${(value / 1_000).toFixed(1)}K`
  }
  return formatCurrency(value)
}

export function formatPct(value: number | null | undefined, decimals = 2): string {
  // null/NaN 을 0%로 위장하지 않는다 — '계산 불가'와 '보합'은 다르다.
  // (가드가 없으면 value.toFixed 에서 TypeError 가 나 페이지가 통째로 빈 화면이 된다.)
  if (value == null || !Number.isFinite(value)) return '—'
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(decimals)}%`
}

export function formatNumber(value: number, decimals = 2): string {
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value)
}

export function colorForValue(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'text-[#64748b]'
  if (value > 0) return 'text-[#10b981]'
  if (value < 0) return 'text-[#ef4444]'
  return 'text-[#64748b]'
}

export function bgColorForValue(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'bg-[#64748b]/10 text-[#64748b]'
  if (value > 0) return 'bg-[#10b981]/10 text-[#10b981]'
  if (value < 0) return 'bg-[#ef4444]/10 text-[#ef4444]'
  return 'bg-[#64748b]/10 text-[#64748b]'
}

export function formatDateStr(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

export function severityColor(severity: number): string {
  if (severity >= 4) return '#ef4444'
  if (severity >= 3) return '#f59e0b'
  if (severity >= 2) return '#3b82f6'
  return '#10b981'
}

export function ratingColor(rating: string): string {
  const r = rating.toLowerCase()
  if (r.includes('bullish') || r.includes('positive') || r.includes('buy')) return '#10b981'
  if (r.includes('bearish') || r.includes('negative') || r.includes('sell')) return '#ef4444'
  if (r.includes('neutral') || r.includes('hold')) return '#f59e0b'
  return '#64748b'
}
