import { NavLink, Outlet } from 'react-router-dom'
import { Monitor, Globe, Dice5, TrendingUp, Zap, BookOpen } from 'lucide-react'
import { cn } from '@/lib/utils'

const navItems = [
  { to: '/terminal',    icon: Monitor,    label: '포토폴리오', sub: 'Portfolio HQ + Brief' },
  { to: '/macro',       icon: Globe,      label: '시장 시나리오',sub: '9-Agent Pipeline' },
  { to: '/monte-carlo', icon: Dice5,      label: 'MONTE CARLO',   sub: 'Simulations' },
  { to: '/optimizer',   icon: TrendingUp, label: '포토폴리오 최적화',     sub: 'Portfolio Opt' },
  { to: '/timing',      icon: Zap,        label: '트레이딩 신호', sub: 'Trade Signals' },
  { to: '/lens',        icon: BookOpen,   label: 'LENS REPORT',   sub: 'AI Research' },
]

export default function Layout() {
  return (
    <div className="flex h-screen bg-[#0b0f1a] overflow-hidden">
      {/* Sidebar */}
      <aside className="w-14 lg:w-52 flex-shrink-0 bg-[#060b14] border-r border-[#1e2d40] flex flex-col">
        {/* Logo */}
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

        {/* Navigation */}
        <nav className="flex-1 py-3 space-y-0.5 px-1.5 overflow-y-auto">
          {navItems.map(({ to, icon: Icon, label, sub }) => (
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
                    <div className="text-[9px] text-[#374151] truncate">{sub}</div>
                  </div>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Version */}
        <div className="px-2 py-2 border-t border-[#1e2d40]">
          <div className="hidden lg:block text-[9px] text-[#1f2937] text-center tracking-widest">v2.0 · PFP SYSTEM</div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
