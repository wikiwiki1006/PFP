import { Link, NavLink, Outlet } from 'react-router-dom'
import { Monitor, Globe, TrendingUp, Zap, BookOpen } from 'lucide-react'
import { cn } from '@/lib/utils'
import UserMenu from './auth/UserMenu'
import ThemeToggle from './ThemeToggle'
import MarketSwitch from './MarketSwitch'
import logo from '@/assets/image_logo.png'

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
          {/* 로고는 홈(포트폴리오)으로 가는 링크다. 대부분의 서비스가 그렇게
              동작해서 사용자가 먼저 눌러 본다. */}
          <Link to="/terminal" aria-label="포트폴리오로 이동"
                className="flex items-center gap-2 min-w-0 rounded transition-opacity hover:opacity-80">
            <img src={logo} alt="ZOOPZOOP" className="w-9 h-9 object-contain flex-shrink-0" />
            <div className="hidden lg:block min-w-0">
              <div className="text-xs font-bold text-[#e2e8f0] tracking-widest">ZOOPZOOP</div>
              <div className="text-[9px] font-bold text-[#10b981] tracking-widest -mt-0.5">투자의 기회를 줍다</div>
            </div>
          </Link>
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
                    ? 'bg-[#10b981]/15 text-[#10b981] border border-[#10b981]/25'
                    : 'text-[#4a5568] hover:text-[#94a3b8] hover:bg-[#111827]/60'
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={cn('w-4 h-4 flex-shrink-0', isActive ? 'text-[#10b981]' : '')} />
                  <div className="hidden lg:block min-w-0 flex-1">
                    <div className={cn('font-bold tracking-wider text-[10px]', isActive ? 'text-[#10b981]' : 'text-[#94a3b8]')}>
                      {label}
                    </div>
                  </div>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="px-2 py-2 border-t border-[#1e2d40]">
          <div className="hidden lg:block text-[9px] text-[#1f2937] text-center tracking-widest">v2.0 · ZOOPZOOP SYSTEM</div>
        </div>
      </aside>

      {/* 본문 */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* 상단 바는 좌·중·우 세 자리로 고정한다.
            로고를 절대 위치로 가운데 두는 이유: 양옆 폭이 로그인 여부와 시장 이름
            길이에 따라 변하는데, 흐름 배치로는 그때마다 로고가 좌우로 밀린다. */}
        <header className="relative h-12 flex-shrink-0 flex items-center gap-2 px-3 sm:gap-3 sm:px-4 border-b border-[#1e2d40] bg-[#060b14]">
          <div className="flex-shrink-0">
            <MarketSwitch />
          </div>

          {/* 모바일에는 사이드바가 없으므로 로고를 상단 바 가운데 둔다.
              로고를 위, 이름을 그 아래 작게 쌓는다 — 가로로 늘어놓으면 좌우
              버튼과 폭을 다투다가 좁은 기기에서 이름이 잘린다.

              바깥 상자는 pointer-events-none 을 유지한다. 이 상자는 헤더 전체에
              걸쳐 가운데 정렬돼 있어, 여기서 탭을 받으면 뒤에 있는 시장 전환·
              메뉴 버튼이 눌리지 않는다. 링크에만 pointer-events-auto 를 줘서
              **로고와 글자 위에서만** 탭이 잡히게 한다. */}
          <div className="pointer-events-none absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center leading-none md:hidden">
            <Link to="/terminal" aria-label="포트폴리오로 이동"
                  className="pointer-events-auto flex flex-col items-center active:opacity-70">
              <img src={logo} alt="ZOOPZOOP" className="h-[22px] w-[22px] flex-shrink-0 object-contain" />
              <span className="mt-[2px] text-[8px] font-bold tracking-[0.15em] text-[#94a3b8]">ZOOPZOOP</span>
            </Link>
          </div>

          <div className="ml-auto flex flex-shrink-0 items-center gap-1.5 sm:gap-3">
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
                  isActive ? 'text-[#10b981]' : 'text-[#64748b]'
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={cn('w-5 h-5 flex-shrink-0', isActive ? 'text-[#10b981]' : '')} />
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
