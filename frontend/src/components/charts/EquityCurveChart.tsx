import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import type { EquityCurvePoint } from '@/types'
import { formatCurrency } from '@/lib/utils'

interface EquityCurveChartProps {
  data: EquityCurvePoint[]
}

const CustomTooltip = ({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: string
}) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-[#1a2035] border border-[#1e2d40] rounded-lg p-3 text-xs shadow-xl">
        <p className="text-[#64748b] mb-2">{label}</p>
        {payload.map((p) => (
          <div key={p.name} className="flex items-center gap-2 mb-1">
            <div className="w-2 h-2 rounded-full" style={{ backgroundColor: p.color }} />
            <span className="text-[#e2e8f0] font-mono">{formatCurrency(p.value)}</span>
            <span className="text-[#64748b]">{p.name}</span>
          </div>
        ))}
      </div>
    )
  }
  return null
}

export default function EquityCurveChart({ data }: EquityCurveChartProps) {
  const formatted = data.map((d) => ({
    ...d,
    date: new Date(d.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
  }))

  return (
    <ResponsiveContainer width="100%" height={300}>
      <AreaChart data={formatted} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
        <defs>
          <linearGradient id="portfolioGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="benchmarkGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#64748b" stopOpacity={0.2} />
            <stop offset="95%" stopColor="#64748b" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fill: '#64748b', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fill: '#64748b', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `$${(v / 1000).toFixed(0)}K`}
          width={55}
        />
        <Tooltip content={<CustomTooltip />} />
        <Legend
          wrapperStyle={{ fontSize: '12px', color: '#64748b', paddingTop: '12px' }}
        />
        <Area
          type="monotone"
          dataKey="benchmark_value"
          name="S&P 500"
          stroke="#64748b"
          strokeWidth={1.5}
          fill="url(#benchmarkGrad)"
          dot={false}
        />
        <Area
          type="monotone"
          dataKey="value"
          name="Portfolio"
          stroke="#3b82f6"
          strokeWidth={2}
          fill="url(#portfolioGrad)"
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
