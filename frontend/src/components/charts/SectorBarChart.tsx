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
            {/* null 을 0 으로 채우지 않는다. 이 차트가 CLAUDE.md 1.3 에 적힌
                사고의 화면 쪽이다 — 데이터가 없는 섹터들이 전부 '+0.00%' 초록으로
                떠서 사용자에게 "모든 섹터가 보합" 으로 보였다. 보합과 데이터
                없음은 다르다. */}
            <span
              className="font-mono font-medium"
              style={{ color: p.value == null ? '#64748b' : p.value >= 0 ? '#10b981' : '#ef4444' }}
            >
              {p.value == null ? '—' : `${p.value >= 0 ? '+' : ''}${p.value.toFixed(2)}%`}
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

  // 데이터 없는 섹터를 0 으로 취급하면 등락률 0 근처의 실제 섹터들 사이에
  // 섞여 순위가 왜곡된다. '모름' 은 순위가 없으므로 끝으로 보낸다.
  const sorted = [...data].sort((a, b) => {
    const av = a[dataKey], bv = b[dataKey]
    if (av == null && bv == null) return 0
    if (av == null) return 1
    if (bv == null) return -1
    return bv - av
  })

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
              fill={entry[dataKey] == null ? '#64748b'
                    : entry[dataKey]! >= 0 ? '#10b981' : '#ef4444'}
              fillOpacity={0.8}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
