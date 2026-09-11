import { clsx, type ClassValue } from 'clsx'

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs)
}

// 통화 포맷은 여기 두지 않는다.
//
// formatCurrency 는 currency:'USD' 고정, formatLargeNumber 는 '$' 리터럴
// 고정이었다. 시장을 받는 lib/market.ts 의 formatMoney·formatPrice·
// formatCompact·formatAxisPrice 와 나란히 있으면, 다음 사람이 둘 중 아무거나
// 고른다 — 오늘 BollingerChart 툴팁·SectorBarChart·현금 부족 메시지가 전부
// 그렇게 원화를 달러로 표시했다. 같은 일을 하는 함수를 둘 두지 않는다.
// 통화가 붙는 값은 항상 lib/market.ts 를 쓴다.

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
