import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  Legend,
} from 'recharts'
import type { SectorData } from '@/types'

interface SectorBarChartProps {
  data: SectorData[]
  view?: '1d' | '1w' | '1m'
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
        <p className="text-[#e2e8f0] font-medium mb-2">{label}</p>
        {payload.map((p) => (
          <div key={p.name} className="flex items-center gap-2 mb-1">
            <div className="w-2 h-2 rounded-full" style={{ backgroundColor: p.color }} />
            <span
              className="font-mono font-medium"
              style={{ color: (p.value ?? 0) >= 0 ? '#10b981' : '#ef4444' }}
            >
              {(p.value ?? 0) >= 0 ? '+' : ''}
              {(p.value ?? 0).toFixed(2)}%
            </span>
            <span className="text-[#64748b]">{p.name}</span>
          </div>
        ))}
      </div>
    )
  }
  return null
}

export default function SectorBarChart({ data, view = '1d' }: SectorBarChartProps) {
  const keyMap = {
    '1d': 'change_1d_pct',
    '1w': 'change_1w_pct',
    '1m': 'change_1m_pct',
  } as const

  const dataKey = keyMap[view]

  const sorted = [...data].sort(
    (a, b) => (b[dataKey] ?? 0) - (a[dataKey] ?? 0)
  )

  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart
        data={sorted}
        layout="vertical"
        margin={{ top: 0, right: 20, left: 0, bottom: 0 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#1e2d40" horizontal={false} />
        <XAxis
          type="number"
          tick={{ fill: '#64748b', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%`}
        />
        <YAxis
          dataKey="sector"
          type="category"
          tick={{ fill: '#e2e8f0', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={110}
        />
        <Tooltip content={<CustomTooltip />} />
        <Legend wrapperStyle={{ fontSize: '12px', color: '#64748b' }} />
        <Bar dataKey={dataKey} name={`${view} Change`} radius={[0, 2, 2, 0]}>
          {sorted.map((entry, index) => (
            <Cell
              key={`cell-${index}`}
              fill={(entry[dataKey] ?? 0) >= 0 ? '#10b981' : '#ef4444'}
              fillOpacity={0.8}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
