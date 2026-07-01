import { useQuery } from '@tanstack/react-query'
import { TrendingUp, DollarSign, Activity, BarChart2, MessageSquare } from 'lucide-react'
import MetricCard from '@/components/MetricCard'
import EquityCurveChart from '@/components/charts/EquityCurveChart'
import LoadingSpinner, { SkeletonCard, ErrorMessage } from '@/components/LoadingSpinner'
import {
  getPortfolioMetrics,
  getEquityCurve,
  getHoldingsDetail,
  getAnalystFeedback,
} from '@/api'
import { formatCurrency, formatLargeNumber, formatPct, colorForValue, bgColorForValue } from '@/lib/utils'
import { cn } from '@/lib/utils'

export default function Dashboard() {
  const metricsQ = useQuery({ queryKey: ['portfolio-metrics'], queryFn: getPortfolioMetrics })
  const curveQ = useQuery({ queryKey: ['equity-curve'], queryFn: getEquityCurve })
  const holdingsQ = useQuery({ queryKey: ['holdings-detail'], queryFn: getHoldingsDetail })
  const feedbackQ = useQuery({ queryKey: ['analyst-feedback'], queryFn: getAnalystFeedback })

  const m = metricsQ.data

  return (
    <div className="p-6 space-y-6 min-h-full">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[#e2e8f0]">Dashboard</h1>
          <p className="text-sm text-[#64748b] mt-0.5">Portfolio overview &amp; performance</p>
        </div>
        <div className="text-xs text-[#64748b] font-mono">
          {new Date().toLocaleDateString('en-US', {
            weekday: 'short',
            month: 'long',
            day: 'numeric',
            year: 'numeric',
          })}
        </div>
      </div>

      {/* Metric Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        {metricsQ.isLoading ? (
          Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)
        ) : metricsQ.isError ? (
          <div className="col-span-5">
            <ErrorMessage message="Failed to load portfolio metrics" retry={metricsQ.refetch} />
          </div>
        ) : m ? (
          <>
            <MetricCard
              title="Total Value"
              value={formatLargeNumber(m.total_equity)}
              icon={<DollarSign className="w-4 h-4" />}
              change={m.today_change_pct}
              changeLabel="today"
            />
            <MetricCard
              title="Daily P&amp;L"
              value={formatCurrency(m.today_change_val)}
              icon={<TrendingUp className="w-4 h-4" />}
              valueColor={colorForValue(m.today_change_val)}
              change={m.today_change_pct}
            />
            <MetricCard
              title="Total Return"
              value={formatPct(m.total_return_pct)}
              icon={<BarChart2 className="w-4 h-4" />}
              valueColor={colorForValue(m.total_return_pct)}
              subtitle={`Cost: ${formatLargeNumber(m.total_cost)}`}
            />
            <MetricCard
              title="Portfolio Beta"
              value={m.portfolio_beta.toFixed(2)}
              icon={<Activity className="w-4 h-4" />}
              subtitle={`Alpha vs S&P: ${m.alpha_vs_sp500 != null ? m.alpha_vs_sp500.toFixed(2) : 'N/A'}%`}
            />
            <MetricCard
              title="VIX"
              value={m.vix.toFixed(2)}
              icon={<Shield className="w-4 h-4" />}
              valueColor={m.vix > 25 ? 'text-[#ef4444]' : m.vix > 18 ? 'text-[#f59e0b]' : 'text-[#10b981]'}
              subtitle={`1W: ${formatPct(m.perf_1w)} | 1M: ${formatPct(m.perf_1m)}`}
            />
          </>
        ) : null}
      </div>

      {/* AI Analyst Feedback */}
      {feedbackQ.data && (
        <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <MessageSquare className="w-4 h-4 text-[#3b82f6]" />
            <h2 className="text-sm font-semibold text-[#e2e8f0]">AI Analyst Feedback</h2>
          </div>
          <p className="text-sm text-[#e2e8f0] leading-relaxed whitespace-pre-wrap">
            {feedbackQ.data.feedback}
          </p>
        </div>
      )}
      {feedbackQ.isLoading && (
        <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <MessageSquare className="w-4 h-4 text-[#3b82f6]" />
            <h2 className="text-sm font-semibold text-[#e2e8f0]">AI Analyst Feedback</h2>
          </div>
          <LoadingSpinner size="sm" text="Loading analyst feedback..." />
        </div>
      )}

      {/* Equity Curve */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-[#e2e8f0]">Equity Curve</h2>
          <span className="text-xs text-[#64748b]">vs S&amp;P 500 Benchmark</span>
        </div>
        {curveQ.isLoading && <LoadingSpinner className="h-[300px]" text="Loading chart..." />}
        {curveQ.isError && (
          <ErrorMessage message="Failed to load equity curve" retry={curveQ.refetch} />
        )}
        {curveQ.data && curveQ.data.length > 0 && <EquityCurveChart data={curveQ.data} />}
      </div>

      {/* Holdings Summary Table */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg">
        <div className="px-4 py-3 border-b border-[#1e2d40] flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[#e2e8f0]">Holdings Summary</h2>
          <span className="text-xs text-[#64748b]">
            {holdingsQ.data?.length ?? 0} positions
          </span>
        </div>
        {holdingsQ.isLoading && <LoadingSpinner className="p-8" text="Loading holdings..." />}
        {holdingsQ.isError && (
          <ErrorMessage message="Failed to load holdings" retry={holdingsQ.refetch} />
        )}
        {holdingsQ.data && holdingsQ.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#1e2d40]">
                  <th className="text-left px-4 py-2.5 text-xs font-medium text-[#64748b] uppercase tracking-wider">
                    Ticker
                  </th>
                  <th className="text-right px-4 py-2.5 text-xs font-medium text-[#64748b] uppercase tracking-wider">
                    Price
                  </th>
                  <th className="text-right px-4 py-2.5 text-xs font-medium text-[#64748b] uppercase tracking-wider">
                    P&amp;L%
                  </th>
                  <th className="text-right px-4 py-2.5 text-xs font-medium text-[#64748b] uppercase tracking-wider">
                    Weight
                  </th>
                </tr>
              </thead>
              <tbody>
                {holdingsQ.data.slice(0, 10).map((h, i) => (
                  <tr key={h.ticker} className={cn('table-row-stripe', i % 2 === 0 ? '' : '')}>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-semibold text-[#e2e8f0]">{h.ticker}</span>
                        <span className="text-xs text-[#64748b]">{h.sector}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-[#e2e8f0]">
                      {formatCurrency(h.current_price)}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <span className={cn('font-mono text-xs px-2 py-0.5 rounded', bgColorForValue(h.pnl_pct))}>
                        {formatPct(h.pnl_pct)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-[#64748b]">
                      {(h.weight * 100).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
