import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'

interface WeightsChartProps {
  data: { [key: string]: number }
  title?: string
}

const COLORS = [
  '#3b82f6',
  '#10b981',
  '#f59e0b',
  '#8b5cf6',
  '#ef4444',
  '#06b6d4',
  '#84cc16',
  '#f97316',
  '#ec4899',
  '#14b8a6',
  '#a855f7',
  '#eab308',
]

const CustomTooltip = ({
  active,
  payload,
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; payload: { fill: string } }>
}) => {
  if (active && payload && payload.length) {
    const p = payload[0]
    return (
      <div className="bg-[#1a2035] border border-[#1e2d40] rounded-lg p-3 text-xs shadow-xl">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: p.payload.fill }} />
          <span className="text-[#e2e8f0] font-medium">{p.name}</span>
        </div>
        <p className="text-[#3b82f6] font-mono font-semibold mt-1">
          {(p.value * 100).toFixed(1)}%
        </p>
      </div>
    )
  }
  return null
}

export default function WeightsChart({ data }: WeightsChartProps) {
  const chartData = Object.entries(data)
    .sort(([, a], [, b]) => b - a)
    .map(([name, value], i) => ({
      name,
      value,
      fill: COLORS[i % COLORS.length],
    }))

  return (
    <ResponsiveContainer width="100%" height={280}>
      <PieChart>
        <Pie
          data={chartData}
          cx="50%"
          cy="45%"
          innerRadius={55}
          outerRadius={90}
          paddingAngle={2}
          dataKey="value"
        >
          {chartData.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={entry.fill} stroke="transparent" />
          ))}
        </Pie>
        <Tooltip content={<CustomTooltip />} />
        <Legend
          formatter={(value) => (
            <span style={{ color: '#e2e8f0', fontSize: '11px' }}>{value}</span>
          )}
          iconSize={8}
          iconType="circle"
        />
      </PieChart>
    </ResponsiveContainer>
  )
}
