/**
 * components/LightweightDailyChart.tsx
 * ────────────────────────────────────
 * 일봉 차트 — TradingView lightweight-charts (v5).
 * https://github.com/tradingview/lightweight-charts
 *
 * 미국 종목은 TradingView 위젯(TradingViewChart.tsx)을 쓰지만 그 위젯은 KRX
 * 시세를 표시하지 않는다. 한국 종목은 **우리가 이미 받아 둔 일봉**
 * (`/api/ticker/{t}/detail` 의 ohlcv — 이평선·볼린저·스토캐스틱까지 서버가
 * 계산해 준다)을 같은 회사의 렌더링 엔진으로 그려 겉모습을 맞춘다.
 * 데이터는 그대로이고 그리는 쪽만 바뀐다.
 *
 * 위젯과 다른 점: 지표 메뉴·그리기 도구·분봉은 없다. lightweight-charts 는
 * 그리기 엔진이라 그런 UI 를 주지 않는다. 지표 켜고 끄기는 모달의 MA/BB 버튼이,
 * 기간은 모달의 기간 버튼이 맡는다.
 *
 * 라이선스(Apache-2.0)는 사용자에게 보이는 페이지에 https://www.tradingview.com/
 * 링크를 요구한다. `attributionLogo`(기본 true)가 차트 위에 그 링크를 그리므로
 * 끄지 않는다.
 */
import { useEffect, useRef } from 'react'
import {
  createChart, CandlestickSeries, HistogramSeries, LineSeries, LineStyle, ColorType,
  type IChartApi, type ISeriesApi, type MouseEventParams, type Time,
} from 'lightweight-charts'
import type { OHLCVPoint } from '@/types'
import { formatAxisPrice, formatPrice, type Market } from '@/lib/market'
import { MA_PERIODS, type ChartOverlays } from '@/lib/chartProvider'

export type DailyChartPalette = {
  bg: string; grid: string; text: string; muted: string; border: string
  up: string; down: string; volUp: string; volDn: string
  ma20: string; ma50: string; ma200: string; bb: string
  stochK: string; stochD: string
}

type Props = {
  ohlcv: OHLCVPoint[]
  /** 가격 표기 통화. 티커에서 정한다 — 화면의 시장 설정이 아니다. */
  market: Market
  palette: DailyChartPalette
  /** 겹쳐 그릴 지표 — 이평선은 기간마다 따로 켠다. 스토캐스틱은 항상 나온다. */
  overlays: ChartOverlays
  /** 마지막 봉이 장중 부분 봉이면 그 날짜 — 범례에 '장중' 을 붙인다. */
  liveDate?: string | null
  /** 최신 시세를 받지 못했으면 true — 범례에 그 사실을 적는다. */
  stale?: boolean
}

// 값이 없는 칸(이평선 초반 등)은 점을 찍지 않는다. 0 으로 채우면 선이
// 바닥으로 꺾여 '가격 0' 이라는 거짓 값이 그려진다 (CLAUDE.md §1.3(a)).
const line = (rows: OHLCVPoint[], key: keyof OHLCVPoint) =>
  rows.flatMap(r => {
    const v = r[key]
    return typeof v === 'number' && Number.isFinite(v) ? [{ time: r.date as Time, value: v }] : []
  })

export default function LightweightDailyChart({ ohlcv, market, palette: P, overlays, liveDate, stale }: Props) {
  const hostRef   = useRef<HTMLDivElement>(null)
  const legendRef = useRef<HTMLDivElement>(null)
  const chartRef  = useRef<IChartApi | null>(null)
  // MA/BB 를 켜고 끌 때 차트를 새로 만들지 않는다 — 새로 만들면 확대·이동해 둔
  // 범위가 처음으로 돌아간다. 선만 숨긴다 (아래 두 번째 effect).
  const maRef  = useRef<ISeriesApi<'Line'>[]>([])
  const bbRef  = useRef<ISeriesApi<'Line'>[]>([])
  const overlaysRef = useRef(overlays)
  const repaintRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    // 원화는 소수점이 없다 (호가 단위 1원, §1.4). 축 라벨은 사이트의 다른
    // 차트와 같은 formatAxisPrice 를 쓴다 — 100만원 이상만 '만' 으로 줄인다.
    const priceFormat = {
      type: 'custom' as const,
      formatter: (v: number) => formatAxisPrice(v, market),
      minMove: market === 'KR' ? 1 : 0.01,
    }

    const chart = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: P.bg },
        textColor: P.muted,
        fontSize: 11,
        panes: { separatorColor: P.border, separatorHoverColor: P.border, enableResize: false },
      },
      localization: { locale: 'ko-KR' },
      grid: { vertLines: { color: P.grid }, horzLines: { color: P.grid } },
      rightPriceScale: { borderColor: P.border },
      timeScale: { borderColor: P.border, rightOffset: 3 },
      crosshair: { mode: 0 },   // Normal — 위젯처럼 십자선이 커서를 그대로 따라간다
      // 세로 터치 드래그는 페이지(모달) 스크롤로 넘긴다 — 기본값이면 휴대폰에서 차트 위
      // 세로 스와이프가 막혀 아래 패널로 내려갈 수 없다.
      handleScroll: { vertTouchDrag: false },
    })
    chartRef.current = chart

    // ── 0번 패널: 캔들 + 볼린저 + 이평선 ─────────────────────────────────
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: P.up, downColor: P.down, borderUpColor: P.up, borderDownColor: P.down,
      wickUpColor: P.up, wickDownColor: P.down, priceFormat,
    })
    candle.setData(ohlcv.map(r => ({
      time: r.date as Time, open: r.open, high: r.high, low: r.low, close: r.close,
    })))

    const overlay = (color: string, width: 1 | 2, style: LineStyle = LineStyle.Solid) => ({
      color, lineWidth: width, lineStyle: style, priceFormat,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    })
    const add = (color: string, style: LineStyle, key: keyof OHLCVPoint) => {
      const sr = chart.addSeries(LineSeries, overlay(color, 1, style))
      sr.setData(line(ohlcv, key))
      return sr
    }
    bbRef.current = [
      add(P.bb, LineStyle.Dashed, 'bb_upper'),
      add(P.bb, LineStyle.Dotted, 'bb_mid'),
      add(P.bb, LineStyle.Dashed, 'bb_lower'),
    ]
    maRef.current = [
      add(P.ma20, LineStyle.Solid, 'ma20'),
      add(P.ma50, LineStyle.Solid, 'ma50'),
      add(P.ma200, LineStyle.Solid, 'ma200'),
    ]

    // ── 1번 패널: 거래량 ─────────────────────────────────────────────────
    const vol = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false,
    }, 1)
    vol.setData(ohlcv.map(r => ({
      time: r.date as Time, value: r.volume, color: r.close >= r.open ? P.volUp : P.volDn,
    })))

    // ── 2번 패널: 스토캐스틱(14,3,3) ──────────────────────────────────────
    const stochFmt = { type: 'price' as const, precision: 0, minMove: 1 }
    // 축을 0~100 으로 고정한다. 자동 맞춤에 맡기면 위아래 여백 때문에 120 까지 잡혀
    // 80/20 기준선의 위치가 종목마다 달라 보인다.
    const stochScale = () => ({ priceRange: { minValue: 0, maxValue: 100 } })
    const k = chart.addSeries(LineSeries, {
      color: P.stochK, lineWidth: 1, priceFormat: stochFmt, lastValueVisible: false, priceLineVisible: false,
      autoscaleInfoProvider: stochScale,
    }, 2)
    k.setData(line(ohlcv, 'stoch_k'))
    // 기본 여백(위 20%)이 남아 있으면 범위를 고정해도 120 눈금이 그려진다.
    k.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.05 } })
    chart.addSeries(LineSeries, {
      color: P.stochD, lineWidth: 1, lineStyle: LineStyle.Dashed, priceFormat: stochFmt,
      lastValueVisible: false, priceLineVisible: false, autoscaleInfoProvider: stochScale,
    }, 2).setData(line(ohlcv, 'stoch_d'))
    for (const [price, color] of [[80, P.down], [20, P.up]] as const) {
      k.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    }

    const panes = chart.panes()
    panes[0]?.setStretchFactor(0.62)
    panes[1]?.setStretchFactor(0.16)
    panes[2]?.setStretchFactor(0.22)

    // ── 범례 (위젯처럼 커서 위치의 시가·고가·저가·종가) ──────────────────
    // React 상태로 두면 마우스 이동마다 모달 전체가 다시 그려진다. DOM 을 직접 고친다.
    const byDate = new Map(ohlcv.map((r, i) => [r.date, i]))
    const paint = (i: number) => {
      const el = legendRef.current
      const r = ohlcv[i]
      if (!el || !r) return
      const prev = i > 0 ? ohlcv[i - 1].close : null
      const chg = prev ? (r.close - prev) / prev * 100 : null
      const c = r.close >= r.open ? P.up : P.down
      const f = (v: number) => formatPrice(v, market)
      const ma = (label: string, v: number | null, color: string) =>
        v == null ? '' : `<span style="color:${color};margin-right:10px">${label} ${f(v)}</span>`
      el.innerHTML =
        `<div><span style="color:${P.muted}">${r.date}</span>` +
        (r.date === liveDate ? ` <span style="color:${P.ma20}">장중</span>` : '') +
        ` <span style="color:${P.muted}">시</span><span style="color:${c}">${f(r.open)}</span>` +
        ` <span style="color:${P.muted}">고</span><span style="color:${c}">${f(r.high)}</span>` +
        ` <span style="color:${P.muted}">저</span><span style="color:${c}">${f(r.low)}</span>` +
        ` <span style="color:${P.muted}">종</span><span style="color:${c}">${f(r.close)}</span>` +
        (chg == null ? '' : ` <span style="color:${c}">(${chg > 0 ? '+' : ''}${chg.toFixed(2)}%)</span>`) +
        `</div>` +
        (stale && i === ohlcv.length - 1
          ? `<div style="color:${P.ma20}">최신 시세를 받지 못해 ${r.date} 까지 표시합니다</div>` : '') +
        (() => {
          const on = overlaysRef.current.ma
          const parts = (on[20] ? ma('MA20', r.ma20, P.ma20) : '') + (on[50] ? ma('MA50', r.ma50, P.ma50) : '')
                      + (on[200] ? ma('MA200', r.ma200, P.ma200) : '')
          return parts ? `<div>${parts}</div>` : ''
        })()
    }
    let lastIdx = ohlcv.length - 1
    const onMove = (p: MouseEventParams<Time>) => {
      const i = p.time != null ? byDate.get(String(p.time)) : undefined
      lastIdx = i ?? ohlcv.length - 1
      paint(lastIdx)
    }
    repaintRef.current = () => paint(lastIdx)
    paint(lastIdx)
    chart.subscribeCrosshairMove(onMove)
    chart.timeScale().fitContent()

    return () => {
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
      chartRef.current = null
      maRef.current = []; bbRef.current = []; repaintRef.current = null
    }
    // overlays 는 일부러 뺐다 — 아래 effect 가 선만 숨긴다.
  }, [ohlcv, market, P, liveDate, stale])

  useEffect(() => {
    overlaysRef.current = overlays
    // maRef 는 MA_PERIODS 순서(20·50·200)로 만들어진다.
    maRef.current.forEach((s, i) => s.applyOptions({ visible: overlays.ma[MA_PERIODS[i]] }))
    bbRef.current.forEach(s => s.applyOptions({ visible: overlays.bb }))
    repaintRef.current?.()
  }, [overlays, ohlcv, market, P])

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div ref={hostRef} style={{ position: 'absolute', inset: 0 }} />
      <div
        ref={legendRef}
        style={{
          // 오른쪽 가격 축(약 80px) 위로는 넘어가지 않게 — 좁은 화면에서 줄바꿈된다.
          position: 'absolute', top: 6, left: 10, right: 84, zIndex: 3, pointerEvents: 'none',
          fontSize: 11, lineHeight: '16px', color: P.text, fontVariantNumeric: 'tabular-nums',
        }}
      />
    </div>
  )
}
