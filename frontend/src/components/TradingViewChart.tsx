/**
 * components/TradingViewChart.tsx
 * ───────────────────────────────
 * TradingView "Advanced Real-Time Chart" 위젯.
 * https://www.tradingview.com/widget-docs/widgets/charts/advanced-chart/
 *
 * 공식 임베드 코드는 `<script>` 태그 안에 JSON 설정을 넣는 형태다. React 는
 * JSX 의 `<script>` 를 실행하지 않으므로 스크립트 요소를 직접 만들어 붙인다.
 * 설정이 바뀌면(종목·테마·지표) 컨테이너를 비우고 다시 붙인다 — 위젯에는
 * 설정을 바꾸는 API 가 없다.
 *
 * 지표는 호출자가 고른 것(MA 20/50/200 · BB)에 스토캐스틱을 더해 넘긴다 —
 * 형식에 관한 함정은 `studiesFor` 주석 참고.
 *
 * 하단의 TradingView 링크는 위젯 이용 조건이라 지우지 않는다.
 */
import { useEffect, useRef } from 'react'
import { MA_PERIODS, type ChartOverlays } from '@/lib/chartProvider'

/** 켜진 지표 → 위젯 studies 배열.
 *
 *  **전부 객체형(`{ id, inputs }`)으로 적는다.** 문자열형('BB@tv-basicstudies')과
 *  섞으면 위젯이 첫 형식과 다른 항목을 말없이 버린다 — MA(객체) 뒤의 BB·스토캐스틱
 *  (문자열)이 사라졌고, 거꾸로 두면 MA 가 사라졌다. 그래서 '무료 위젯은 지표 3개까지'
 *  처럼 보였다. 객체형으로 통일하면 MA 셋 + BB + 스토캐스틱 다섯이 함께 그려진다
 *  (2026-10-05 실제 위젯으로 확인).
 *
 *  이평선 길이는 옛 ID(`MASimple@tv-basicstudies`)로만 지정된다 — `STD;SMA` 에
 *  `length` 를 주면 무시되고 9일선이 된다. 선 색은 위젯이 받지 않는다(`styles`·
 *  `overrides`·`studies_overrides` 모두 무시되거나 지표가 통째로 빠졌다) — 세 이평선은
 *  같은 색이고 범례 이름(MA 20/50/200)으로 구분된다. */
function studiesFor(o: ChartOverlays) {
  return [
    ...MA_PERIODS.filter(n => o.ma[n]).map(n => ({ id: 'MASimple@tv-basicstudies', inputs: { length: n } })),
    ...(o.bb ? [{ id: 'BB@tv-basicstudies' }] : []),
    { id: 'Stochastic@tv-basicstudies' },     // 기본 화면 — 항상 나온다
  ]
}

type Props = {
  symbol: string
  theme: 'dark' | 'light'
  background: string
  grid: string
  overlays: ChartOverlays
}

export default function TradingViewChart({ symbol, theme, background, grid, overlays }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  // 객체는 렌더마다 새로 만들어진다 — 내용으로 비교해야 위젯이 쓸데없이 다시 뜨지 않는다.
  const studiesKey = JSON.stringify(studiesFor(overlays))

  useEffect(() => {
    const host = ref.current
    if (!host) return
    host.innerHTML = ''

    const widget = document.createElement('div')
    widget.className = 'tradingview-widget-container__widget'
    widget.style.height = 'calc(100% - 24px)'
    widget.style.width = '100%'
    host.appendChild(widget)

    const credit = document.createElement('div')
    credit.className = 'tradingview-widget-copyright'
    credit.style.cssText = 'height:24px;line-height:24px;font-size:10px;text-align:right;padding-right:8px'
    credit.innerHTML =
      '<a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank" ' +
      'style="color:#64748b;text-decoration:none">Chart by TradingView</a>'
    host.appendChild(credit)

    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'
    script.type = 'text/javascript'
    script.async = true
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol,
      interval: 'D',
      // 1Y 범위. 3M 이하로 주면 위젯이 일봉 대신 1시간봉으로 바꾼다.
      range: '12M',
      timezone: 'America/New_York',
      theme,
      style: '1',
      locale: 'kr',
      backgroundColor: background,
      gridColor: grid,
      allow_symbol_change: false,
      withdateranges: true,
      hide_side_toolbar: true,
      save_image: false,
      calendar: false,
      studies: JSON.parse(studiesKey),
      support_host: 'https://www.tradingview.com',
    })
    // 한 틱 미뤄 붙인다. 개발 모드(StrictMode)는 마운트→언마운트→마운트를
    // 동기로 돌리는데, 첫 마운트의 스크립트가 비동기로 실행될 때는 이미
    // 컨테이너에서 떨어져 있어 위젯 스크립트가 `null.querySelector` 로 죽는다.
    const timer = window.setTimeout(() => host.appendChild(script), 0)

    return () => { window.clearTimeout(timer); host.innerHTML = '' }
  }, [symbol, theme, background, grid, studiesKey])

  return (
    <div
      ref={ref}
      className="tradingview-widget-container"
      style={{ height: '100%', width: '100%' }}
    />
  )
}
