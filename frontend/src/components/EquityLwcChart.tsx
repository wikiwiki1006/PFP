/**
 * components/EquityLwcChart.tsx
 * ─────────────────────────────
 * 포트폴리오 화면의 '포트폴리오 수익률' 그래프 — TradingView lightweight-charts (v5).
 *
 * 예전 recharts 그래프와 **같은 정보**를 그린다 (2026-10 변경, 사용자 요청):
 *   · 포트폴리오 / 벤치마크 / 나스닥 수익률(%) 선 — 표시 지수 선택은 부모가 정한다
 *   · 매매일 표시 (매수 초록 · 매도 빨강 · 같은 날 둘 다 주황), 입출금일 표시
 *     (입금 호박색 · 출금 장미색 — 예전 마름모는 이 라이브러리에 없어 네모로 그린다)
 *   · 툴팁: 날짜·총자산·각 선 수익률·입출금액·그날 종목별 수익률·그날 매매 내역
 *
 * 다른 점: 확대가 '드래그로 사각형 고르기' 에서 '휠·핀치 확대 + 드래그 이동' 으로
 * 바뀌었고 세로 범위는 자동이다. 더블클릭은 전체 보기로 돌아간다.
 *
 * 되돌리려면 `VITE_EQUITY_CHART=legacy` — 예전 그래프 코드는 AlphaTerminal.tsx 에 그대로 있다.
 */
import { useEffect, useRef } from 'react'
import {
  createChart, createSeriesMarkers, AreaSeries, LineStyle, ColorType,
  type IChartApi, type MouseEventParams, type Time, type SeriesMarker,
} from 'lightweight-charts'
import { formatMoney, formatPrice } from '@/lib/market'

export const EQUITY_LWC_ENABLED = import.meta.env.VITE_EQUITY_CHART !== 'legacy'

export type EquityPoint = {
  date: string
  port?: number
  benchmark_pct?: number
  nasdaq?: number
  total_equity?: number
  cash_flow?: number
  trades: { ticker: string; type: string; q: number; price: number }[]
  holdings: { ticker: string; return_pct: number | null; price: number | null }[]
}

type Props = {
  data: EquityPoint[]
  showBenchmark: boolean
  showNasdaq: boolean
  benchLabel: string
  secondaryLabel: string
  /** 티커 → 화면 이름 (한국은 종목명) */
  label: (ticker: string) => string
  height?: number
  bg: string
  grid: string
}

const C_PORT = '#00e6ff', C_BENCH = '#dc143c', C_NQ = '#a78bfa'
const isBuy = (t: { type: string }) => t.type === 'ADD' || t.type === 'BUY'
const isSell = (t: { type: string }) => t.type === 'SOLD' || t.type === 'SELL'
const esc = (s: string) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!))
const pct = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
// 값이 없으면 회색 — null 을 0 과 비교하면 '계산 불가' 가 빨강(손실)으로 칠해진다.
const pctColor = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? '#64748b' : v > 0 ? '#10b981' : v < 0 ? '#ef4444' : '#94a3b8'

const line = (rows: EquityPoint[], key: 'port' | 'benchmark_pct' | 'nasdaq') =>
  rows.map(r => {
    const v = r[key]
    // 값이 없는 날은 빈 점 — 0 으로 채우면 정체한 것처럼 그려진다 (선을 끊는다).
    return typeof v === 'number' && Number.isFinite(v) ? { time: r.date as Time, value: v } : { time: r.date as Time }
  })

export default function EquityLwcChart({
  data, showBenchmark, showNasdaq, benchLabel, secondaryLabel, label, height = 300, bg, grid,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host || !data.length) return
    const fmt = { type: 'custom' as const, formatter: (v: number) => `${v.toFixed(1)}%`, minMove: 0.01 }
    const chart = createChart(host, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: bg }, textColor: '#64748b', fontSize: 11 },
      localization: { locale: 'ko-KR' },
      grid: { vertLines: { visible: false }, horzLines: { color: grid, style: LineStyle.Dotted } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: 0 },
      // 세로 스와이프는 페이지 스크롤로 — 휴대폰에서 그래프 위에서도 화면이 내려가야 한다.
      handleScroll: { vertTouchDrag: false },
    })
    chartRef.current = chart

    const area = (color: string, top: string, dashed: boolean, width: 1 | 2 | 3) => chart.addSeries(AreaSeries, {
      lineColor: color, topColor: top, bottomColor: 'rgba(0,0,0,0)', lineWidth: width,
      lineStyle: dashed ? LineStyle.Dashed : LineStyle.Solid, priceFormat: fmt,
      lastValueVisible: false, priceLineVisible: false,
    })
    if (showBenchmark) area(C_BENCH, 'rgba(220,20,60,0.12)', true, 1).setData(line(data, 'benchmark_pct'))
    if (showNasdaq) area(C_NQ, 'rgba(167,139,250,0.15)', true, 1).setData(line(data, 'nasdaq'))
    const port = area(C_PORT, 'rgba(0,230,255,0.25)', false, 2)
    port.setData(line(data, 'port'))

    // 매매·입출금일 표시 — 포트폴리오 선 위
    const markers: SeriesMarker<Time>[] = []
    for (const d of data) {
      if (d.cash_flow != null) {
        markers.push({ time: d.date as Time, position: 'inBar', shape: 'square', size: 1,
                       color: d.cash_flow > 0 ? '#f59e0b' : '#f43f5e' })
      } else if (d.trades?.length) {
        const b = d.trades.some(isBuy), s = d.trades.some(isSell)
        markers.push({ time: d.date as Time, position: 'inBar', shape: 'circle', size: 1,
                       color: b && s ? '#f59e0b' : b ? '#10b981' : '#ef4444' })
      }
    }
    createSeriesMarkers(port, markers)
    chart.timeScale().fitContent()

    // ── 툴팁 — 예전 그래프와 같은 내용 ──────────────────────────────────
    const byDate = new Map(data.map(d => [d.date, d]))
    const onMove = (p: MouseEventParams<Time>) => {
      const el = tipRef.current
      if (!el) return
      const d = p.time != null ? byDate.get(String(p.time)) : undefined
      if (!d || !p.point) { el.style.display = 'none'; return }
      const row = (dot: string, name: string, v: number | null | undefined, nameColor = '#94a3b8') =>
        `<div style="display:flex;justify-content:space-between;gap:12px;margin-top:2px">` +
        `<span><span style="color:${dot}">●</span> <span style="color:${nameColor};font-size:10px">${esc(name)}</span></span>` +
        `<span style="font-family:monospace;font-weight:700;color:${pctColor(v)}">${pct(v)}</span></div>`
      let h = `<div style="display:flex;justify-content:space-between;gap:12px;margin-bottom:6px">` +
        `<span style="color:#cbd5e1;font-size:11px">${esc(d.date)}</span>` +
        (d.total_equity != null && d.total_equity > 0
          ? `<span style="font-family:monospace;font-size:11px;font-weight:700;color:#e2e8f0">${esc(formatMoney(d.total_equity))}</span>` : '') +
        `</div>`
      h += row(C_PORT, '포트폴리오', d.port)
      if (showBenchmark && d.benchmark_pct != null) h += row(C_BENCH, benchLabel, d.benchmark_pct, C_BENCH)
      if (showNasdaq && d.nasdaq != null) h += row(C_NQ, secondaryLabel, d.nasdaq)
      if (d.cash_flow != null) {
        const c = d.cash_flow > 0 ? '#f59e0b' : '#f43f5e'
        h += `<div style="margin-top:6px;padding-top:6px;border-top:1px solid #1e2d40;display:flex;justify-content:space-between;color:${c}">` +
          `<span style="font-size:10px">◆ ${d.cash_flow > 0 ? '입금' : '출금'}</span>` +
          `<span style="font-family:monospace;font-size:11px;font-weight:700">${d.cash_flow > 0 ? '+' : '-'}${esc(formatMoney(Math.abs(d.cash_flow)))}</span></div>`
      }
      if (d.holdings?.length) {
        h += `<div style="margin-top:6px;padding-top:6px;border-top:1px solid #1e2d40">` +
          d.holdings.map(x =>
            `<div style="display:flex;justify-content:space-between;gap:12px;margin-top:2px">` +
            `<span style="color:#cbd5e1;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(label(x.ticker))}</span>` +
            `<span style="font-family:monospace;font-size:11px;color:${pctColor(x.return_pct)}">${pct(x.return_pct)}</span></div>`).join('') +
          `</div>`
      }
      if (d.trades?.length) {
        h += `<div style="margin-top:6px;padding-top:6px;border-top:1px solid #1e2d40">` +
          d.trades.map(t => {
            const buy = isBuy(t), c = buy ? '#10b981' : '#ef4444'
            return `<div style="display:flex;justify-content:space-between;gap:12px;margin-top:2px">` +
              `<span style="color:${c}"><span style="font-size:9px">${buy ? '▲' : '▼'}</span> ` +
              `<b style="font-size:11px">${esc(label(t.ticker))}</b> <span style="font-size:10px;opacity:.7">${buy ? '매수' : '매도'}</span></span>` +
              `<span style="color:#cbd5e1;font-size:10px;font-family:monospace">${t.q}주 @${esc(formatPrice(t.price))}</span></div>`
          }).join('') + `</div>`
      }
      el.innerHTML = h
      el.style.display = 'block'
      // 커서 옆에 두되 상자 밖으로 나가지 않게
      const W = host.clientWidth, H = host.clientHeight
      const tw = el.offsetWidth, th = el.offsetHeight
      let x = p.point.x + 14, y = p.point.y + 14
      if (x + tw > W) x = Math.max(0, p.point.x - tw - 14)
      if (y + th > H) y = Math.max(0, H - th)
      el.style.left = `${x}px`; el.style.top = `${y}px`
    }
    chart.subscribeCrosshairMove(onMove)
    const onDbl = () => chart.timeScale().fitContent()
    host.addEventListener('dblclick', onDbl)

    return () => {
      host.removeEventListener('dblclick', onDbl)
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
      chartRef.current = null
    }
  }, [data, showBenchmark, showNasdaq, benchLabel, secondaryLabel, label, bg, grid])

  return (
    <div style={{ position: 'relative', width: '100%', height }}>
      <div ref={hostRef} style={{ position: 'absolute', inset: 0 }} />
      <div ref={tipRef} style={{
        position: 'absolute', display: 'none', zIndex: 5, pointerEvents: 'none',
        background: '#0b1220', border: '1px solid #1e2d40', borderRadius: 4,
        padding: '8px 12px', fontSize: 12, minWidth: 200, maxWidth: 280,
      }} />
    </div>
  )
}
