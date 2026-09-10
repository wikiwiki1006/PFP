/**
 * components/TickerLabel.tsx
 * ──────────────────────────
 * 종목을 한 줄로 표시한다. 무엇을 크게 보여 줄지는 시장에 따라 다르다.
 *
 * 미국은 티커가 곧 이름 역할을 한다 — AAPL, NVDA 를 보면 바로 안다.
 * 한국은 그렇지 않다. '044490.KQ' 는 아무것도 알려 주지 않고, 코드를 외우는
 * 사람도 없다. 그래서 한국에서는 **종목명을 크게, 코드를 작게** 뒤집는다.
 *
 * 이름이 없을 때는(수집 실패 등) 티커를 크게 보여 준다 — 큰 자리를 비워 두면
 * 줄이 무너지고, 무엇보다 무슨 종목인지 전혀 알 수 없게 된다.
 */
import { cn } from '@/lib/utils'
import { useMarket } from '@/lib/useMarket'

interface TickerLabelProps {
  ticker: string
  name?: string | null
  /** 주(主) 텍스트 크기. 쓰는 자리마다 달라 밖에서 준다. */
  primaryClass?: string
  secondaryClass?: string
  className?: string
  /** 세로로 쌓기 (표의 좁은 칸 등) */
  stacked?: boolean
}

export default function TickerLabel({
  ticker,
  name,
  primaryClass = 'text-sm font-bold',
  secondaryClass = 'text-[11px]',
  className,
  stacked = false,
}: TickerLabelProps) {
  const market = useMarket()
  const hasName = !!(name && name.trim() && name.trim() !== ticker)
  const nameFirst = market === 'KR' && hasName

  const primary = nameFirst ? name!.trim() : ticker
  const secondary = nameFirst ? ticker : (hasName ? name!.trim() : null)

  return (
    <span
      className={cn(
        'min-w-0',
        stacked ? 'flex flex-col leading-tight' : 'flex items-baseline gap-1.5',
        className,
      )}
    >
      <span
        className={cn(
          'truncate text-[#e2e8f0]',
          // 코드가 주 텍스트일 때만 고정폭 — 이름에 고정폭을 쓰면 한글이 벌어져 읽기 나쁘다.
          nameFirst ? 'font-semibold' : 'font-mono',
          primaryClass,
        )}
        title={primary}
      >
        {primary}
      </span>
      {secondary && (
        <span className={cn('truncate font-mono text-[#94a3b8]', secondaryClass)}>
          {secondary}
        </span>
      )}
    </span>
  )
}
