/**
 * components/timing/TechLwcChart.tsx
 * ──────────────────────────────────
 * 트레이딩 신호 › 매매신호 창의 종목 차트 — TradingView lightweight-charts (v5).
 *
 * 예전에는 recharts 로 그렸다(캔들을 Bar 의 shape 로 직접 그리는 방식). 컨트롤과
 * 상태(볼린저밴드·중앙선·저항선·이평선 켜고 끄기, TP/SL 선, 일봉/주봉, 우측 여백)는
 * BollingerChart.tsx 에 그대로 있고, 여기는 그리는 일만 한다.
 *
 *   · 볼린저밴드 채움은 없다 — lightweight-charts 에 두 선 사이 채우기가 없다.
 *     상·하단을 같은 색 선으로 그린다.
 *   · '우측 여백' 은 빈 날짜 행을 만드는 대신 timeScale 의 rightOffset(봉 수)로 준다.
 *   · 크로스는 캔들 위 원 표시(series markers), TP/SL 은 price line 이다.
 *   · 확대·이동은 차트가 스스로 한다(휠·드래그·핀치). 예전 모바일의 가로 스크롤
 *     래퍼(chart-hscroll)는 필요 없다.
 *
 * 라이선스(Apache-2.0)의 TradingView 링크 요건은 attributionLogo(기본 true)로 채운다.
 */
import { useEffect, useRef, useState } from 'react'
import {
  createChart, createSeriesMarkers, CandlestickSeries, LineSeries, LineStyle, ColorType,
  type IChartApi, type ISeriesApi, type IPriceLine, type ISeriesMarkersPluginApi,
  type MouseEventParams, type Time,
} from 'lightweight-charts'
import type { TechnicalChartPoint } from '@/types'
import { formatAxisPrice, formatPrice, type Market } from '@/lib/market'

export type TechOverlays = {
  bands: boolean
  mid: boolean
  resist: boolean
  mas: Record<'ma5' | 'ma30' | 'ma60' | 'ma120', boolean>
}

type Cross = { date: string; price: number; type: 'golden' | 'dead' }

type Props = {
  data: TechnicalChartPoint[]           // 실제 캔들만 (price != null)
  market: Market
  height: number
  rightOffset: number                   // 우측 여백 (봉 수)
  overlays: TechOverlays
  maColors: Record<string, string>
  maLabels: Record<string, string>
  crosses: Cross[]
  tp: number | null                     // 켜져 있고 > 0 일 때만 값
  sl: number | null
  colors: { bg: string; text: string; grid: string; border: string; up: string; down: string }
}

const MA_KEYS = ['ma5', 'ma30', 'ma60', 'ma120'] as const

const pts = (rows: TechnicalChartPoint[], key: keyof TechnicalChartPoint, gaps = false) =>
  rows.flatMap(r => {
    const v = r[key]
    if (typeof v === 'number' && Number.isFinite(v)) return [{ time: r.date as Time, value: v }]
    // 저항선은 값이 없는 구간을 끊어 그린다 (예전 connectNulls={false}). 빈 점(whitespace)이 선을 끊는다.
    return gaps ? [{ time: r.date as Time }] : []
  })

export default function TechLwcChart({
  data, market, height, rightOffset, overlays, maColors, maLabels, crosses, tp, sl, colors: K,
}: Props) {
  const hostRef   = useRef<HTMLDivElement>(null)
  const legendRef = useRef<HTMLDivElement>(null)
  const chartRef  = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const lineRef   = useRef<Record<string, ISeriesApi<'Line'>>>({})
  const markerRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])
  const overlaysRef = useRef(overlays)
  const repaint = useRef<(() => void) | null>(null)
  // 차트를 새로 만들 때마다 오른다. 테마 전환처럼 데이터는 그대로인데 차트만 다시 만들어질
  // 때, 지표 표시·크로스 마커 effect 가 이 값으로 다시 돈다 — 안 그러면 끈 이평선이 다시
  // 보이고 크로스가 사라졌다.
  const [gen, setGen] = useState(0)

  // ── 차트 생성 (데이터·여백·색이 바뀔 때만) ─────────────────────────────
  useEffect(() => {
    const host = hostRef.current
    if (!host || !data.length) return
    const priceFormat = {
      type: 'custom' as const,
      formatter: (v: number) => formatAxisPrice(v, market),
      minMove: market === 'KR' ? 1 : 0.01,
    }
    const chart = createChart(host, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: K.bg }, textColor: K.text, fontSize: 10 },
      localization: { locale: 'ko-KR' },
      grid: { vertLines: { visible: false }, horzLines: { color: K.grid } },
      rightPriceScale: { borderColor: K.border },
      timeScale: { borderColor: K.border, rightOffset },
      crosshair: { mode: 0 },
      // 세로 터치 드래그는 페이지 스크롤로 넘긴다. 기본값(true)이면 차트가 세로 스와이프를
      // 가로채(preventDefault) 휴대폰에서 차트가 화면을 채우는 순간 페이지가 안 내려갔다.
      handleScroll: { vertTouchDrag: false },
    })
    chartRef.current = chart

    const line = (color: string, style: LineStyle, width: 1 | 2 = 1) =>
      chart.addSeries(LineSeries, {
        color, lineWidth: width, lineStyle: style, priceFormat,
        lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
      })
    const L: Record<string, ISeriesApi<'Line'>> = {}
    L.upper = line('#3b82f6', LineStyle.Solid); L.upper.setData(pts(data, 'upper'))
    L.lower = line('#3b82f6', LineStyle.Solid); L.lower.setData(pts(data, 'lower'))
    L.mid   = line('#64748b', LineStyle.Dashed); L.mid.setData(pts(data, 'mid'))
    L.resistance = line('#ef4444', LineStyle.Dotted); L.resistance.setData(pts(data, 'resistance', true))
    for (const k of MA_KEYS) { L[k] = line(maColors[k], LineStyle.Solid, 2); L[k].setData(pts(data, k)) }
    lineRef.current = L
    setGen(g => g + 1)   // 아래 effect 들이 새 차트에 지표·마커를 다시 적용하게

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: K.up, downColor: K.down, borderUpColor: K.up, borderDownColor: K.down,
      wickUpColor: K.up, wickDownColor: K.down, priceFormat,
    })
    candle.setData(data.map(r => {
      const close = r.price
      const open = r.open ?? close
      return { time: r.date as Time, open, high: r.high ?? Math.max(open, close),
               low: r.low ?? Math.min(open, close), close }
    }))
    candleRef.current = candle
    markerRef.current = createSeriesMarkers(candle, [])
    chart.timeScale().fitContent()

    // ── 범례: 커서 위치 값 (React 상태로 두면 마우스 이동마다 패널 전체가 다시 그려진다) ──
    const byDate = new Map(data.map((r, i) => [r.date, i]))
    const paint = (i: number) => {
      const el = legendRef.current, r = data[i]
      if (!el || !r) return
      const f = (v: number | null) => (v == null ? '—' : formatPrice(v, market))
      const c = r.price >= (r.open ?? r.price) ? K.up : K.down
      const o = overlaysRef.current
      const parts = [
        `<span style="color:${K.text}">${r.date}</span>`,
        `<span style="color:${c}">시 ${f(r.open)} 고 ${f(r.high)} 저 ${f(r.low)} 종 ${f(r.price)}</span>`,
      ]
      if (o.bands && r.upper != null) parts.push(`<span style="color:#3b82f6">BB ${f(r.upper)} / ${f(r.lower)}</span>`)
      if (o.resist && r.resistance != null) parts.push(`<span style="color:#ef4444">저항 ${f(r.resistance)}</span>`)
      for (const k of MA_KEYS) {
        if (o.mas[k] && r[k] != null) parts.push(`<span style="color:${maColors[k]}">MA${maLabels[k].replace('일', '')} ${f(r[k] as number)}</span>`)
      }
      if (r.zscore != null) parts.push(`<span style="color:${K.text}">Z ${r.zscore.toFixed(2)}</span>`)
      el.innerHTML = parts.join(' &nbsp;')
    }
    let last = data.length - 1
    const onMove = (p: MouseEventParams<Time>) => {
      const i = p.time != null ? byDate.get(String(p.time)) : undefined
      last = i ?? data.length - 1
      paint(last)
    }
    repaint.current = () => paint(last)
    paint(last)
    chart.subscribeCrosshairMove(onMove)

    return () => {
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
      chartRef.current = null; candleRef.current = null; markerRef.current = null
      lineRef.current = {}; priceLinesRef.current = []; repaint.current = null
    }
    // overlays·crosses·tp/sl 은 아래 effect 들이 다룬다 — 차트를 새로 만들면 확대가 풀린다.
  }, [data, market, rightOffset, K, maColors, maLabels])

  // ── 지표 켜고 끄기 ──────────────────────────────────────────────────────
  useEffect(() => {
    overlaysRef.current = overlays
    const L = lineRef.current
    L.upper?.applyOptions({ visible: overlays.bands })
    L.lower?.applyOptions({ visible: overlays.bands })
    L.mid?.applyOptions({ visible: overlays.mid })
    L.resistance?.applyOptions({ visible: overlays.resist })
    for (const k of MA_KEYS) L[k]?.applyOptions({ visible: overlays.mas[k] })
    repaint.current?.()
  }, [overlays, data, gen])

  // ── 골든·데드크로스 표시 ────────────────────────────────────────────────
  useEffect(() => {
    // 시간순이어야 한다 — 라이브러리가 보이는 범위를 이분 탐색으로 고른다.
    markerRef.current?.setMarkers([...crosses].sort((a, b) => a.date.localeCompare(b.date)).map(c => ({
      time: c.date as Time, position: 'inBar' as const, shape: 'circle' as const,
      color: c.type === 'golden' ? '#fbbf24' : '#a855f7', size: 1,
    })))
  }, [crosses, data, gen])

  // ── TP / SL 선 ──────────────────────────────────────────────────────────
  useEffect(() => {
    const s = candleRef.current
    if (!s) return
    priceLinesRef.current.forEach(l => s.removePriceLine(l))
    priceLinesRef.current = []
    if (tp != null) priceLinesRef.current.push(s.createPriceLine({
      price: tp, color: K.up, lineWidth: 2, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'TP' }))
    if (sl != null) priceLinesRef.current.push(s.createPriceLine({
      price: sl, color: K.down, lineWidth: 2, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'SL' }))
  }, [tp, sl, data, K, gen])

  return (
    <div style={{ position: 'relative', width: '100%', height }}>
      <div ref={hostRef} style={{ position: 'absolute', inset: 0 }} />
      <div ref={legendRef} style={{
        position: 'absolute', top: 4, left: 8, right: 70, zIndex: 3, pointerEvents: 'none',
        fontSize: 10, lineHeight: '15px', fontVariantNumeric: 'tabular-nums',
      }} />
    </div>
  )
}
