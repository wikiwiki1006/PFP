import { NavLink, Outlet } from 'react-router-dom'
import {
  LayoutDashboard,
  BriefcaseBusiness,
  BarChart3,
  Zap,
  Settings2,
  Brain,
  TrendingUp,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/portfolio', icon: BriefcaseBusiness, label: 'Portfolio' },
  { to: '/market', icon: BarChart3, label: 'Market' },
  { to: '/signals', icon: Zap, label: 'Signals' },
  { to: '/optimizer', icon: TrendingUp, label: 'Optimizer' },
  { to: '/macro', icon: Brain, label: 'AI Macro' },
]

export default function Layout() {
  return (
    <div className="flex h-screen bg-[#0b0f1a] overflow-hidden">
      {/* Sidebar */}
      <aside className="w-16 lg:w-56 flex-shrink-0 bg-[#080d16] border-r border-[#1e2d40] flex flex-col">
        {/* Logo */}
        <div className="h-14 flex items-center px-4 border-b border-[#1e2d40]">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-md bg-[#3b82f6] flex items-center justify-center">
              <TrendingUp className="w-4 h-4 text-white" />
            </div>
            <span className="hidden lg:block font-semibold text-[#e2e8f0] text-sm tracking-wide">
              PFP
            </span>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-4 space-y-1 px-2">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors',
                  isActive
                    ? 'bg-[#3b82f6]/15 text-[#3b82f6] border border-[#3b82f6]/20'
                    : 'text-[#64748b] hover:text-[#e2e8f0] hover:bg-[#1e2d40]/50'
                )
              }
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              <span className="hidden lg:block">{label}</span>
            </NavLink>
          ))}
        </nav>

        {/* Settings */}
        <div className="p-2 border-t border-[#1e2d40]">
          <button className="w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm text-[#64748b] hover:text-[#e2e8f0] hover:bg-[#1e2d40]/50 transition-colors">
            <Settings2 className="w-4 h-4 flex-shrink-0" />
            <span className="hidden lg:block">Settings</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
