import { cn, colorForValue } from '@/lib/utils'
import type { ReactNode } from 'react'

interface MetricCardProps {
  title: string
  value: string
  subtitle?: string
  change?: number
  changeLabel?: string
  icon?: ReactNode
  valueColor?: string
  className?: string
}

export default function MetricCard({
  title,
  value,
  subtitle,
  change,
  changeLabel,
  icon,
  valueColor,
  className,
}: MetricCardProps) {
  return (
    <div
      className={cn(
        'bg-[#111827] border border-[#1e2d40] rounded-lg p-4 flex flex-col gap-2',
        className
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-[#64748b] uppercase tracking-wider">
          {title}
        </span>
        {icon && <div className="text-[#64748b]">{icon}</div>}
      </div>

      <div
        className={cn(
          'text-2xl font-bold font-mono tracking-tight',
          valueColor ?? 'text-[#e2e8f0]'
        )}
      >
        {value}
      </div>

      {(subtitle || change !== undefined) && (
        <div className="flex items-center gap-2 text-xs">
          {change !== undefined && (
            <span className={cn('font-mono font-medium', colorForValue(change))}>
              {change >= 0 ? '+' : ''}
              {change.toFixed(2)}%
            </span>
          )}
          {changeLabel && <span className="text-[#64748b]">{changeLabel}</span>}
          {subtitle && <span className="text-[#64748b]">{subtitle}</span>}
        </div>
      )}
    </div>
  )
}
