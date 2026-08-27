/**
 * components/ThemeToggle.tsx
 * ─────────────────────────
 * 라이트/다크 전환 버튼. 상단 바에 놓는다.
 */
import { Sun, Moon } from 'lucide-react'
import { useTheme } from '@/lib/ThemeContext'

export default function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const next = theme === 'dark' ? '라이트' : '다크'

  return (
    <button
      onClick={toggle}
      title={`${next} 모드로 전환`}
      aria-label={`${next} 모드로 전환`}
      className="flex h-8 w-8 items-center justify-center rounded-lg text-[#94a3b8] transition hover:bg-[#0d1526] hover:text-[#e2e8f0]"
    >
      {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  )
}
