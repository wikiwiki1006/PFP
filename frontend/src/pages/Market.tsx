import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'
import SectorBarChart from '@/components/charts/SectorBarChart'
import LoadingSpinner, { ErrorMessage } from '@/components/LoadingSpinner'
import { getMarketSnapshot, getMarketSectors, getMacroData, getMarketNews, getHoldings } from '@/api'
import { formatCurrency, colorForValue, bgColorForValue } from '@/lib/utils'
import { cn } from '@/lib/utils'

const WATCH_TICKERS = ['SPY', 'QQQ', 'BTC-USD', '^VIX', 'NVDA', 'AAPL', 'MSFT']

const MACRO_ITEMS = [
  { key: 'fed_rate', label: 'Fed Rate', format: (v: number) => `${v.toFixed(2)}%`, threshold: 5 },
  { key: 'unemployment', label: 'Unemployment', format: (v: number) => `${v.toFixed(1)}%`, threshold: 5 },
  { key: 'cpi', label: 'CPI (YoY)', format: (v: number) => `${v.toFixed(1)}%`, threshold: 3 },
  { key: 'gdp', label: 'GDP Growth', format: (v: number) => `${v.toFixed(1)}%`, threshold: 0 },
  { key: 't10y2y', label: '10Y-2Y Spread', format: (v: number) => `${v.toFixed(2)}%`, threshold: 0 },
  { key: 'bamlh0a0hym2', label: 'HY Spread', format: (v: number) => `${v.toFixed(0)} bps`, threshold: 400 },
] as const

type ViewMode = '1d' | '1w' | '1m'

export default function Market() {
  const [sectorView, setSectorView] = useState<ViewMode>('1d')

  const snapshotQ = useQuery({ queryKey: ['market-snapshot'], queryFn: getMarketSnapshot })
  const sectorsQ = useQuery({ queryKey: ['market-sectors'], queryFn: getMarketSectors })
  const macroQ = useQuery({ queryKey: ['macro-data'], queryFn: getMacroData })
  const holdingsQ = useQuery({ queryKey: ['holdings'], queryFn: getHoldings })

  const newsTickers = holdingsQ.data ? Object.keys(holdingsQ.data).slice(0, 8) : ['SPY', 'QQQ']
  const newsQ = useQuery({
    queryKey: ['market-news', newsTickers],
    queryFn: () => getMarketNews(newsTickers),
    enabled: newsTickers.length > 0,
  })

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-[#e2e8f0]">Market</h1>
        <p className="text-sm text-[#64748b] mt-0.5">Live market data, sectors &amp; macro indicators</p>
      </div>

      {/* Market Snapshot */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-4">Market Snapshot</h2>
        {snapshotQ.isLoading && <LoadingSpinner className="h-24" text="Loading prices..." />}
        {snapshotQ.isError && (
          <ErrorMessage message="Failed to load market snapshot" retry={snapshotQ.refetch} />
        )}
        {snapshotQ.data && (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            {WATCH_TICKERS.map((ticker) => {
              const snap = snapshotQ.data.prices[ticker]
              if (!snap) return null
              return (
                <div
                  key={ticker}
                  className="bg-[#0b0f1a] border border-[#1e2d40] rounded-lg p-3"
                >
                  <div className="text-xs font-mono font-semibold text-[#e2e8f0] mb-2">{ticker}</div>
                  <div className="text-base font-mono font-bold text-[#e2e8f0]">
                    {formatCurrency(snap.price)}
                  </div>
                  <div
                    className={cn(
                      'text-xs font-mono mt-1',
                      colorForValue(snap.change_1d_pct)
                    )}
                  >
                    {snap.change_1d_pct >= 0 ? '+' : ''}
                    {snap.change_1d_pct.toFixed(2)}%
                  </div>
                  <div className={cn('text-xs font-mono', colorForValue(snap.change_1d))}>
                    {snap.change_1d >= 0 ? '+' : ''}
                    {formatCurrency(snap.change_1d)}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Sector Performance */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-[#e2e8f0]">Sector Performance</h2>
          <div className="flex gap-1">
            {(['1d', '1w', '1m'] as ViewMode[]).map((v) => (
              <button
                key={v}
                onClick={() => setSectorView(v)}
                className={cn(
                  'px-3 py-1 text-xs rounded transition-colors',
                  sectorView === v
                    ? 'bg-[#3b82f6] text-white'
                    : 'bg-[#0b0f1a] text-[#64748b] hover:text-[#e2e8f0] border border-[#1e2d40]'
                )}
              >
                {v.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        {sectorsQ.isLoading && <LoadingSpinner className="h-48" text="Loading sectors..." />}
        {sectorsQ.isError && (
          <ErrorMessage message="Failed to load sector data" retry={sectorsQ.refetch} />
        )}
        {sectorsQ.data && sectorsQ.data.length > 0 && (
          <SectorBarChart data={sectorsQ.data} view={sectorView} />
        )}
      </div>

      {/* Macro Panel */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-4">FRED Macro Indicators</h2>
        {macroQ.isLoading && <LoadingSpinner className="h-24" text="Loading macro data..." />}
        {macroQ.isError && (
          <ErrorMessage message="Failed to load macro data" retry={macroQ.refetch} />
        )}
        {macroQ.data && (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            {MACRO_ITEMS.map(({ key, label, format, threshold }) => {
              const value = macroQ.data[key]
              const isWarning =
                key === 'gdp' || key === 't10y2y'
                  ? value < threshold
                  : value > threshold
              return (
                <div
                  key={key}
                  className={cn(
                    'bg-[#0b0f1a] border rounded-lg p-3',
                    isWarning ? 'border-[#f59e0b]/30' : 'border-[#1e2d40]'
                  )}
                >
                  <div className="text-xs text-[#64748b] mb-2">{label}</div>
                  <div
                    className={cn(
                      'text-xl font-mono font-bold',
                      isWarning ? 'text-[#f59e0b]' : 'text-[#e2e8f0]'
                    )}
                  >
                    {format(value)}
                  </div>
                  {isWarning && (
                    <div className="text-xs text-[#f59e0b] mt-1">⚠ Watch</div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* News Feed */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg">
        <div className="px-4 py-3 border-b border-[#1e2d40]">
          <h2 className="text-sm font-semibold text-[#e2e8f0]">Portfolio News</h2>
          <p className="text-xs text-[#64748b] mt-0.5">Recent headlines for your holdings</p>
        </div>
        {newsQ.isLoading && <LoadingSpinner className="p-10" text="Loading news..." />}
        {newsQ.isError && (
          <div className="p-4">
            <ErrorMessage message="Failed to load news" retry={newsQ.refetch} />
          </div>
        )}
        {newsQ.data && newsQ.data.length === 0 && (
          <p className="p-6 text-sm text-[#64748b] text-center">No recent news available</p>
        )}
        {newsQ.data && newsQ.data.length > 0 && (
          <div className="divide-y divide-[#1e2d40]">
            {newsQ.data.slice(0, 15).map((item, i) => (
              <div key={i} className="px-4 py-3 hover:bg-[#1a2540] transition-colors">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span
                        className={cn(
                          'text-xs px-1.5 py-0.5 rounded font-mono font-semibold',
                          bgColorForValue(0)
                        )}
                        style={{ backgroundColor: '#3b82f6/10', color: '#3b82f6' }}
                      >
                        {item.ticker}
                      </span>
                      <span className="text-xs text-[#64748b]">
                        {new Date(Number(item.datetime) * 1000).toLocaleDateString('en-US', {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    </div>
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm text-[#e2e8f0] hover:text-[#3b82f6] line-clamp-2 transition-colors"
                    >
                      {item.headline}
                    </a>
                  </div>
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[#64748b] hover:text-[#3b82f6] flex-shrink-0 mt-1"
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
