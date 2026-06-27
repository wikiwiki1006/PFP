import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw } from 'lucide-react'
import WeightsChart from '@/components/charts/WeightsChart'
import LoadingSpinner, { ErrorMessage } from '@/components/LoadingSpinner'
import { getHoldingsDetail, getSectorWeights, getTrades, postTrade } from '@/api'
import { formatCurrency, formatPct, colorForValue, bgColorForValue } from '@/lib/utils'
import { cn } from '@/lib/utils'
import type { TradeForm } from '@/types'

const SECTORS = [
  'Technology',
  'Healthcare',
  'Financials',
  'Consumer Discretionary',
  'Consumer Staples',
  'Energy',
  'Materials',
  'Industrials',
  'Utilities',
  'Real Estate',
  'Communication Services',
  'Other',
]

export default function Portfolio() {
  const qc = useQueryClient()
  const holdingsQ = useQuery({ queryKey: ['holdings-detail'], queryFn: getHoldingsDetail })
  const sectorQ = useQuery({ queryKey: ['sector-weights'], queryFn: getSectorWeights })
  const tradesQ = useQuery({ queryKey: ['trades'], queryFn: getTrades })

  const [form, setForm] = useState<TradeForm>({
    date: new Date().toISOString().split('T')[0],
    ticker: '',
    type: 'BUY',
    q: 0,
    price: 0,
    memo: '',
  })
  const [formError, setFormError] = useState('')

  const tradeMutation = useMutation({
    mutationFn: postTrade,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['trades'] })
      qc.invalidateQueries({ queryKey: ['holdings-detail'] })
      qc.invalidateQueries({ queryKey: ['portfolio-metrics'] })
      setForm({
        date: new Date().toISOString().split('T')[0],
        ticker: '',
        type: 'BUY',
        q: 0,
        price: 0,
        memo: '',
      })
      setFormError('')
    },
    onError: () => setFormError('Failed to submit trade. Please try again.'),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.ticker.trim()) return setFormError('Ticker is required')
    if (form.q <= 0) return setFormError('Quantity must be positive')
    if (form.price <= 0) return setFormError('Price must be positive')
    tradeMutation.mutate(form)
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[#e2e8f0]">Portfolio</h1>
          <p className="text-sm text-[#64748b] mt-0.5">Holdings, allocation &amp; trade history</p>
        </div>
        <button
          onClick={() => {
            qc.invalidateQueries({ queryKey: ['holdings-detail'] })
            qc.invalidateQueries({ queryKey: ['sector-weights'] })
          }}
          className="flex items-center gap-2 text-xs text-[#64748b] hover:text-[#e2e8f0] transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>

      {/* Holdings Table + Sector Chart */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Holdings Table */}
        <div className="xl:col-span-2 bg-[#111827] border border-[#1e2d40] rounded-lg">
          <div className="px-4 py-3 border-b border-[#1e2d40] flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[#e2e8f0]">Holdings</h2>
            <span className="text-xs text-[#64748b]">
              {holdingsQ.data?.length ?? 0} positions
            </span>
          </div>

          {holdingsQ.isLoading && <LoadingSpinner className="p-10" text="Loading holdings..." />}
          {holdingsQ.isError && (
            <ErrorMessage message="Failed to load holdings" retry={holdingsQ.refetch} />
          )}
          {holdingsQ.data && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#1e2d40]">
                    {['Ticker', 'Sector', 'Qty', 'Avg Cost', 'Current', 'Mkt Value', 'P&L', 'P&L%', 'Weight'].map(
                      (col) => (
                        <th
                          key={col}
                          className={cn(
                            'py-2.5 px-3 text-xs font-medium text-[#64748b] uppercase tracking-wider whitespace-nowrap',
                            col === 'Ticker' || col === 'Sector' ? 'text-left' : 'text-right'
                          )}
                        >
                          {col}
                        </th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody>
                  {holdingsQ.data.map((h, i) => (
                    <tr
                      key={h.ticker}
                      className={cn(
                        'border-b border-[#1e2d40]/30 hover:bg-[#1a2540] transition-colors',
                        i % 2 === 1 ? 'bg-[#0f1724]' : ''
                      )}
                    >
                      <td className="py-2.5 px-3 font-mono font-semibold text-[#e2e8f0]">
                        {h.ticker}
                      </td>
                      <td className="py-2.5 px-3 text-xs text-[#64748b] whitespace-nowrap">
                        {h.sector}
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono text-[#e2e8f0]">
                        {h.qty}
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono text-[#e2e8f0]">
                        {formatCurrency(h.avg_cost)}
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono text-[#e2e8f0]">
                        {formatCurrency(h.current_price)}
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono text-[#e2e8f0]">
                        {formatCurrency(h.market_value)}
                      </td>
                      <td className={cn('py-2.5 px-3 text-right font-mono', colorForValue(h.pnl))}>
                        {formatCurrency(h.pnl)}
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <span
                          className={cn(
                            'font-mono text-xs px-1.5 py-0.5 rounded',
                            bgColorForValue(h.pnl_pct)
                          )}
                        >
                          {formatPct(h.pnl_pct)}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono text-[#64748b]">
                        {(h.weight * 100).toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Sector Weights */}
        <div className="bg-[#111827] border border-[#1e2d40] rounded-lg">
          <div className="px-4 py-3 border-b border-[#1e2d40]">
            <h2 className="text-sm font-semibold text-[#e2e8f0]">Sector Allocation</h2>
          </div>
          {sectorQ.isLoading && <LoadingSpinner className="p-10" text="Loading sectors..." />}
          {sectorQ.isError && (
            <ErrorMessage message="Failed to load sector weights" retry={sectorQ.refetch} />
          )}
          {sectorQ.data && (
            <div className="p-4">
              <WeightsChart data={sectorQ.data} />
              <div className="mt-4 space-y-1.5">
                {Object.entries(sectorQ.data)
                  .sort(([, a], [, b]) => b - a)
                  .slice(0, 8)
                  .map(([sector, weight]) => (
                    <div key={sector} className="flex items-center justify-between text-xs">
                      <span className="text-[#64748b] truncate">{sector}</span>
                      <div className="flex items-center gap-2">
                        <div className="w-16 h-1.5 bg-[#1e2d40] rounded-full overflow-hidden">
                          <div
                            className="h-full bg-[#3b82f6] rounded-full"
                            style={{ width: `${(weight * 100).toFixed(0)}%` }}
                          />
                        </div>
                        <span className="font-mono text-[#e2e8f0] w-10 text-right">
                          {(weight * 100).toFixed(1)}%
                        </span>
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Add Trade Form */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg p-4">
        <div className="flex items-center gap-2 mb-4">
          <Plus className="w-4 h-4 text-[#3b82f6]" />
          <h2 className="text-sm font-semibold text-[#e2e8f0]">Add Trade</h2>
        </div>

        <form onSubmit={handleSubmit} className="grid grid-cols-2 md:grid-cols-6 gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-[#64748b] uppercase tracking-wider">Date</label>
            <input
              type="date"
              value={form.date}
              onChange={(e) => setForm((f) => ({ ...f, date: e.target.value }))}
              className="bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-[#64748b] uppercase tracking-wider">Ticker</label>
            <input
              type="text"
              placeholder="AAPL"
              value={form.ticker}
              onChange={(e) => setForm((f) => ({ ...f, ticker: e.target.value.toUpperCase() }))}
              className="bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6] uppercase"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-[#64748b] uppercase tracking-wider">Type</label>
            <select
              value={form.type}
              onChange={(e) => setForm((f) => ({ ...f, type: e.target.value as 'BUY' | 'SELL' }))}
              className="bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
            >
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-[#64748b] uppercase tracking-wider">Qty</label>
            <input
              type="number"
              placeholder="100"
              value={form.q || ''}
              onChange={(e) => setForm((f) => ({ ...f, q: Number(e.target.value) }))}
              className="bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-[#64748b] uppercase tracking-wider">Price</label>
            <input
              type="number"
              step="0.01"
              placeholder="150.00"
              value={form.price || ''}
              onChange={(e) => setForm((f) => ({ ...f, price: Number(e.target.value) }))}
              className="bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm font-mono text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-[#64748b] uppercase tracking-wider">Memo</label>
            <input
              type="text"
              placeholder="optional"
              value={form.memo}
              onChange={(e) => setForm((f) => ({ ...f, memo: e.target.value }))}
              className="bg-[#0b0f1a] border border-[#1e2d40] rounded px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
            />
          </div>

          <div className="col-span-2 md:col-span-6 flex items-center gap-3">
            <button
              type="submit"
              disabled={tradeMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm rounded transition-colors"
            >
              {tradeMutation.isPending ? (
                <LoadingSpinner size="sm" />
              ) : (
                <Plus className="w-4 h-4" />
              )}
              Submit Trade
            </button>
            {tradeMutation.isSuccess && (
              <span className="text-xs text-[#10b981]">Trade submitted successfully</span>
            )}
            {formError && <span className="text-xs text-[#ef4444]">{formError}</span>}
          </div>
        </form>
      </div>

      {/* Recent Trades */}
      <div className="bg-[#111827] border border-[#1e2d40] rounded-lg">
        <div className="px-4 py-3 border-b border-[#1e2d40]">
          <h2 className="text-sm font-semibold text-[#e2e8f0]">Recent Trades</h2>
        </div>

        {tradesQ.isLoading && <LoadingSpinner className="p-10" text="Loading trades..." />}
        {tradesQ.isError && (
          <ErrorMessage message="Failed to load trades" retry={tradesQ.refetch} />
        )}
        {tradesQ.data && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#1e2d40]">
                  {['Date', 'Ticker', 'Type', 'Qty', 'Price', 'Total', 'Memo'].map((col) => (
                    <th
                      key={col}
                      className={cn(
                        'py-2.5 px-4 text-xs font-medium text-[#64748b] uppercase tracking-wider',
                        col === 'Date' || col === 'Ticker' || col === 'Memo' ? 'text-left' : 'text-right'
                      )}
                    >
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...tradesQ.data].reverse().map((t, i) => (
                  <tr
                    key={i}
                    className={cn(
                      'border-b border-[#1e2d40]/30 hover:bg-[#1a2540] transition-colors',
                      i % 2 === 1 ? 'bg-[#0f1724]' : ''
                    )}
                  >
                    <td className="py-2.5 px-4 text-[#64748b] font-mono text-xs whitespace-nowrap">
                      {t.date}
                    </td>
                    <td className="py-2.5 px-4 font-mono font-semibold text-[#e2e8f0]">
                      {t.ticker}
                    </td>
                    <td className="py-2.5 px-4">
                      <span
                        className={cn(
                          'text-xs px-2 py-0.5 rounded font-medium',
                          t.type === 'BUY'
                            ? 'bg-[#10b981]/10 text-[#10b981]'
                            : 'bg-[#ef4444]/10 text-[#ef4444]'
                        )}
                      >
                        {t.type}
                      </span>
                    </td>
                    <td className="py-2.5 px-4 text-right font-mono text-[#e2e8f0]">{t.q}</td>
                    <td className="py-2.5 px-4 text-right font-mono text-[#e2e8f0]">
                      {formatCurrency(t.price)}
                    </td>
                    <td className="py-2.5 px-4 text-right font-mono text-[#64748b]">
                      {formatCurrency(t.q * t.price)}
                    </td>
                    <td className="py-2.5 px-4 text-[#64748b] text-xs">{t.memo ?? '—'}</td>
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
