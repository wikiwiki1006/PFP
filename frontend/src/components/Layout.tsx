import { NavLink, Outlet } from 'react-router-dom'
import { Monitor, Globe, TrendingUp, Zap, BookOpen } from 'lucide-react'
import { cn } from '@/lib/utils'
import UserMenu from './auth/UserMenu'
import ThemeToggle from './ThemeToggle'

// short 는 모바일 하단 탭에서 쓴다. 긴 이름은 탭 다섯 개가 나란히 놓이면
// 줄바꿈되거나 잘려서 오히려 못 읽게 된다.
const navItems = [
  { to: '/terminal',    icon: Monitor,    label: '포트폴리오',       short: '포트폴리오' },
  { to: '/macro',       icon: Globe,      label: '시장 시나리오',     short: '시나리오' },
  { to: '/optimizer',   icon: TrendingUp, label: '포트폴리오 최적화', short: '최적화' },
  { to: '/timing',      icon: Zap,        label: '트레이딩 신호',     short: '신호' },
  { to: '/lens',        icon: BookOpen,   label: 'AI 리서치',        short: '리서치' },
]

export default function Layout() {
  return (
    // 모바일 브라우저는 주소창이 접히고 펴지면서 100vh 가 실제 보이는 높이와
    // 어긋난다. dvh 는 그 변화를 따라가므로 하단 탭이 화면 밖으로 밀리지 않는다.
    <div className="flex h-[100dvh] bg-[#0b0f1a] overflow-hidden">
      {/* 사이드바 — 태블릿 이상에서만. 모바일에서는 아래 탭 바가 대신한다. */}
      <aside className="hidden md:flex w-14 lg:w-52 flex-shrink-0 bg-[#060b14] border-r border-[#1e2d40] flex-col">
        <div className="h-12 flex items-center px-3 border-b border-[#1e2d40]">
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-6 h-6 rounded bg-[#3b82f6] flex items-center justify-center flex-shrink-0">
              <TrendingUp className="w-3.5 h-3.5 text-white" />
            </div>
            <div className="hidden lg:block min-w-0">
              <div className="text-[10px] font-bold text-[#3b82f6] tracking-widest">PERSONAL</div>
              <div className="text-[10px] font-bold text-[#e2e8f0] tracking-widest -mt-0.5">FINANCIAL PLATFORM</div>
            </div>
          </div>
        </div>

        <nav className="flex-1 py-3 space-y-0.5 px-1.5 overflow-y-auto">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2.5 px-2 py-2 rounded text-xs transition-all duration-150 group',
                  isActive
                    ? 'bg-[#3b82f6]/15 text-[#3b82f6] border border-[#3b82f6]/25'
                    : 'text-[#4a5568] hover:text-[#94a3b8] hover:bg-[#111827]/60'
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={cn('w-4 h-4 flex-shrink-0', isActive ? 'text-[#3b82f6]' : '')} />
                  <div className="hidden lg:block min-w-0 flex-1">
                    <div className={cn('font-bold tracking-wider text-[10px]', isActive ? 'text-[#3b82f6]' : 'text-[#94a3b8]')}>
                      {label}
                    </div>
                  </div>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="px-2 py-2 border-t border-[#1e2d40]">
          <div className="hidden lg:block text-[9px] text-[#1f2937] text-center tracking-widest">v2.0 · PFP SYSTEM</div>
        </div>
      </aside>

      {/* 본문 */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        <header className="h-12 flex-shrink-0 flex items-center gap-3 px-4 border-b border-[#1e2d40] bg-[#060b14]">
          {/* 모바일에는 사이드바가 없으므로 로고를 상단 바에 둔다 */}
          <div className="flex md:hidden items-center gap-2 min-w-0">
            <div className="w-6 h-6 rounded bg-[#3b82f6] flex items-center justify-center flex-shrink-0">
              <TrendingUp className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="text-[11px] font-bold text-[#e2e8f0] tracking-widest">PFP</span>
          </div>
          <div className="ml-auto flex items-center gap-3">
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>

        <main className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden pb-[env(safe-area-inset-bottom)] md:pb-0">
          <Outlet />
        </main>

        {/* 모바일 하단 탭 바.
            화면 아래에 두는 이유는 한 손으로 잡았을 때 엄지가 닿는 곳이라서다.
            홈 인디케이터가 있는 기기에서 가려지지 않도록 safe-area 만큼 띄운다. */}
        <nav className="md:hidden flex-shrink-0 flex items-stretch border-t border-[#1e2d40] bg-[#060b14] pb-[env(safe-area-inset-bottom)]">
          {navItems.map(({ to, icon: Icon, short }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cn(
                  // 탭 하나가 최소 48px 높이 — 손가락으로 정확히 누를 수 있는 크기다.
                  'flex-1 min-w-0 flex flex-col items-center justify-center gap-0.5 py-2 min-h-[52px] transition-colors',
                  isActive ? 'text-[#3b82f6]' : 'text-[#64748b]'
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={cn('w-5 h-5 flex-shrink-0', isActive ? 'text-[#3b82f6]' : '')} />
                  <span className="text-[10px] font-bold leading-none truncate max-w-full px-0.5">
                    {short}
                  </span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  )
}
