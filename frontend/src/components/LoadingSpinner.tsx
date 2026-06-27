import { cn } from '@/lib/utils'

interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg'
  className?: string
  text?: string
}

export default function LoadingSpinner({ size = 'md', className, text }: LoadingSpinnerProps) {
  const sizes = {
    sm: 'w-4 h-4 border-2',
    md: 'w-8 h-8 border-2',
    lg: 'w-12 h-12 border-3',
  }

  return (
    <div className={cn('flex flex-col items-center justify-center gap-3', className)}>
      <div
        className={cn(
          'rounded-full border-[#1e2d40] border-t-[#3b82f6] animate-spin',
          sizes[size]
        )}
      />
      {text && <p className="text-sm text-[#64748b]">{text}</p>}
    </div>
  )
}

export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div className={cn('bg-[#111827] border border-[#1e2d40] rounded-lg p-4', className)}>
      <div className="space-y-3">
        <div className="skeleton h-3 w-24 rounded" />
        <div className="skeleton h-7 w-32 rounded" />
        <div className="skeleton h-3 w-16 rounded" />
      </div>
    </div>
  )
}

export function ErrorMessage({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
      <div className="w-10 h-10 rounded-full bg-[#ef4444]/10 flex items-center justify-center">
        <span className="text-[#ef4444] text-lg">!</span>
      </div>
      <p className="text-sm text-[#64748b]">{message}</p>
      {retry && (
        <button
          onClick={retry}
          className="text-xs text-[#3b82f6] hover:underline"
        >
          Try again
        </button>
      )}
    </div>
  )
}
