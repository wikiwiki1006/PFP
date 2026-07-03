import { AlertTriangle, ShieldCheck } from 'lucide-react'
import { cn } from '@/lib/utils'

interface DoomRadarLike {
  is_doom?: boolean
  severity?: number
  comment?: string
  rate_spread?: number
  hy_spread?: number
}

interface DoomBannerProps {
  data: DoomRadarLike
  className?: string
}

export default function DoomBanner({ data, className }: DoomBannerProps) {
  if (!data) return null

  const isDoom = data.is_doom
  const severity = data.severity ?? 0

  return (
    <div
      className={cn(
        'flex items-center gap-3 px-4 py-3 rounded-lg border text-sm',
        isDoom
          ? 'bg-[#ef4444]/10 border-[#ef4444]/30 text-[#ef4444]'
          : 'bg-[#10b981]/10 border-[#10b981]/30 text-[#10b981]',
        className
      )}
    >
      {isDoom ? (
        <AlertTriangle className="w-5 h-5 flex-shrink-0" />
      ) : (
        <ShieldCheck className="w-5 h-5 flex-shrink-0" />
      )}

      <div className="flex-1 min-w-0">
        <span className="font-semibold mr-2">
          {isDoom ? `DOOM RADAR — SEVERITY ${severity}` : 'MARKET CONDITIONS — NORMAL'}
        </span>
        <span className="text-inherit opacity-80">{data.comment}</span>
      </div>

      <div className="flex items-center gap-4 text-xs font-mono flex-shrink-0">
        {data.rate_spread !== undefined && (
          <span>
            Rate Spread: <span className="font-semibold">{data.rate_spread.toFixed(2)}</span>
          </span>
        )}
        {data.hy_spread !== undefined && (
          <span>
            HY Spread: <span className="font-semibold">{data.hy_spread.toFixed(0)}</span>
          </span>
        )}
      </div>
    </div>
  )
}
