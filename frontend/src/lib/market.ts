/**
 * lib/market.ts
 * ─────────────
 * 지금 보고 있는 시장(미국/한국).
 *
 * 미국과 한국 자산은 완전히 분리해 다룬다. 한 화면에 섞이면 평가액이
 * `800,000원 + 2,300달러` 처럼 단위 없이 더해져 수익률·비중이 무의미해진다.
 *
 * 값을 여기 모듈 수준에 두는 이유: API 호출 62곳을 하나씩 고치는 대신
 * axios 인터셉터가 이 값을 읽어 모든 요청에 자동으로 실어 보낸다.
 * React 컨텍스트만 쓰면 인터셉터에서는 읽을 수 없다.
 */
export type Market = 'US' | 'KR'

const STORAGE_KEY = 'pfp_market'

export interface MarketSpec {
  code: Market
  label: string
  currency: string
  symbol: string
  /** 금액 소수 자리. 원화는 소수점을 쓰지 않는다. */
  fractionDigits: number
  locale: string
  /** 종목 입력칸의 예시. 미국은 티커, 한국은 회사 이름으로 검색한다. */
  tickerExample: string
}

export const MARKETS: Record<Market, MarketSpec> = {
  US: { code: 'US', label: '미국', currency: 'USD', symbol: '$', fractionDigits: 2, locale: 'en-US', tickerExample: 'AAPL' },
  KR: { code: 'KR', label: '한국', currency: 'KRW', symbol: '₩', fractionDigits: 0, locale: 'ko-KR', tickerExample: '삼성전자' },
}

function readStored(): Market {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'KR' ? 'KR' : 'US'
  } catch {
    return 'US'
  }
}

let current: Market = readStored()

/** 인터셉터·포맷터가 읽는 현재 시장. */
export function getMarket(): Market {
  return current
}

/**
 * 이 기기에서 시장을 한 번이라도 고른 적이 있는지.
 *
 * 계정의 기본 시장을 언제 적용할지 가르는 기준이다. 저장된 값이 있으면
 * 사용자가 직접 고른 것이므로 로그인 응답이 덮어쓰지 않는다.
 */
export function hasStoredMarket(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) != null
  } catch {
    return false
  }
}

export function getMarketSpec(): MarketSpec {
  return MARKETS[current]
}

const listeners = new Set<(m: Market) => void>()

export function setMarket(m: Market): void {
  if (m === current) return
  current = m
  try { localStorage.setItem(STORAGE_KEY, m) } catch { /* 사파리 프라이빗 모드 등 */ }
  listeners.forEach(fn => fn(m))
}

export function subscribeMarket(fn: (m: Market) => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/**
 * 금액 표기. 시장에 따라 기호와 소수 자리가 달라진다.
 * 원화에 소수점 두 자리를 붙이면(`₩80,000.00`) 어색하고 자릿수만 늘어난다.
 *
 * 부호는 **기호 앞**에 붙인다. `toLocaleString` 이 음수에 '-' 를 붙이므로
 * `기호 + 값` 으로 이으면 `$-300.00` 처럼 부호가 기호 뒤로 들어간다.
 * 같은 파일의 `formatCompact` 는 처음부터 이렇게 하고 있었다 — 한 파일에
 * 맞는 예와 틀린 예가 같이 있으면, 읽는 사람은 옆 줄을 보고 "이 파일은
 * 이미 처리한다" 고 판단한다. 실제로 그렇게 걸렸다.
 */
export function formatMoney(value: number | null | undefined, market?: Market): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const spec = MARKETS[market ?? current]
  const sign = value < 0 ? '-' : ''
  return sign + spec.symbol + Math.abs(value).toLocaleString(spec.locale, {
    minimumFractionDigits: spec.fractionDigits,
    maximumFractionDigits: spec.fractionDigits,
  })
}

/**
 * 주가 표기. 소수 자리가 시장마다 다르다.
 *
 * 한국 주식은 호가 단위가 1원이라 소수점이 없다. 달러와 같은 규칙으로 찍으면
 * `₩71,900.00` 처럼 실제로 존재하지 않는 정밀도가 표시된다.
 *
 * 부호는 **기호 앞**에 붙인다. `toLocaleString` 이 음수에 '-' 를 붙이므로
 * `기호 + 값` 으로 이으면 `$-300.00` 처럼 부호가 기호 뒤로 들어간다.
 * 같은 파일의 `formatCompact` 는 처음부터 이렇게 하고 있었다 — 한 파일에
 * 맞는 예와 틀린 예가 같이 있으면, 읽는 사람은 옆 줄을 보고 "이 파일은
 * 이미 처리한다" 고 판단한다. 실제로 그렇게 걸렸다.
 */
export function formatPrice(value: number | null | undefined, market?: Market): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const spec = MARKETS[market ?? current]
  const sign = value < 0 ? '-' : ''
  return sign + spec.symbol + Math.abs(value).toLocaleString(spec.locale, {
    minimumFractionDigits: spec.fractionDigits,
    maximumFractionDigits: spec.fractionDigits,
  })
}

/** 차트 축처럼 자리가 좁은 곳: 기호 + 정수. */
export function formatAxisPrice(value: number | null | undefined, market?: Market): string {
  if (value == null || !Number.isFinite(value)) return ''
  const spec = MARKETS[market ?? current]
  // 원화 축약은 100만 이상에서만 한다.
  //
  // 1만 기준으로 줄이면 71,900 이 '7만'이 되어, 68,000~75,000 범위의 주가
  // 차트에서 축 라벨이 전부 '7만'으로 같아진다. 한국 주식은 대부분 100만원
  // 아래라 그대로 찍어도 라벨이 길지 않다.
  // 부호는 기호 앞에 (formatMoney·formatPrice·formatCompact 와 같은 규칙).
  const sign = value < 0 ? '-' : ''
  const v = Math.abs(value)
  if (spec.code === 'KR' && v >= 1_000_000) {
    return `${sign}${spec.symbol}${Math.round(v / 10_000).toLocaleString(spec.locale)}만`
  }
  return sign + spec.symbol + Math.round(v).toLocaleString(spec.locale)
}

/**
 * 큰 금액을 짧게. 단위 체계가 시장마다 다르다.
 *
 * 영어권은 천/백만(K·M)으로 끊지만 한국은 만·억·조로 끊는다. `₩1.2M` 은
 * 한국 사용자에게 읽히지 않는 표기다(120만원인지 바로 안 온다).
 */
export function formatCompact(value: number | null | undefined, market?: Market): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const spec = MARKETS[market ?? current]
  const sign = value < 0 ? '-' : ''
  const v = Math.abs(value)

  if (spec.code === 'KR') {
    if (v >= 1e12) return `${sign}${spec.symbol}${(v / 1e12).toFixed(2)}조`
    if (v >= 1e8)  return `${sign}${spec.symbol}${(v / 1e8).toFixed(2)}억`
    // 만 구간은 소수 1자리까지 쓰되 필요할 때만 붙인다. 정수로 반올림하면
    // 12,340 이 ₩1만 이 되어 19% 어긋나고, 14,999 는 33% 어긋난다. 달러 쪽
    // 같은 자릿수 구간(K)이 이미 소수 1자리라 정수 반올림은 비대칭이었다.
    // maximumFractionDigits 라 850 은 ₩850만 그대로고 1.234 만 ₩1.2만 이 된다.
    if (v >= 1e4)  return `${sign}${spec.symbol}${(v / 1e4).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}만`
    return `${sign}${spec.symbol}${Math.round(v).toLocaleString('ko-KR')}`
  }
  if (v >= 1e12) return `${sign}${spec.symbol}${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9)  return `${sign}${spec.symbol}${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `${sign}${spec.symbol}${(v / 1e6).toFixed(2)}M`
  if (v >= 1e3) return `${sign}${spec.symbol}${(v / 1e3).toFixed(1)}K`
  return `${sign}${spec.symbol}${v.toFixed(2)}`
}

/** 지금 시장의 통화 기호. 문자열을 직접 조립해야 하는 곳에서만 쓴다. */
export function marketSymbol(market?: Market): string {
  return MARKETS[market ?? current].symbol
}


/**
 * 숫자 입력칸 표시용 — 정수부에 세 자리마다 쉼표를 넣는다.
 *
 * `<input type="number">` 는 쉼표를 넣을 수 없다(브라우저가 값을 무효로 본다).
 * 그래서 금액 칸은 text 입력으로 두고, 화면에 보이는 문자열만 여기서 만든다.
 * 원화는 250000 처럼 자릿수가 길어 쉼표 없이는 눈으로 자릿수를 세야 한다.
 *
 * 입력 도중 상태를 망가뜨리지 않는 것이 요건이다. 사용자가 "1200." 까지 쳤을 때
 * 소수점을 지워버리면 그 뒤를 이어 칠 수 없으므로, 꼬리의 점과 0 은 그대로 둔다.
 */
export function formatNumberInput(raw: string | number | null | undefined): string {
  if (raw == null || raw === '') return ''
  const str = String(raw)
  const neg = str.startsWith('-')
  const body = neg ? str.slice(1) : str
  const [intPart = '', ...rest] = body.split('.')
  const digits = intPart.replace(/\D/g, '')
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const frac = rest.length ? '.' + rest.join('').replace(/\D/g, '') : ''
  return (neg ? '-' : '') + grouped + frac
}

/** 쉼표가 섞인 입력 문자열을 숫자로. 비었거나 숫자가 아니면 0. */
export function parseNumberInput(raw: string): number {
  const cleaned = raw.replace(/,/g, '')
  const n = Number(cleaned)
  return Number.isFinite(n) ? n : 0
}

/**
 * 금액 입력칸에 그대로 펼치는 속성 묶음.
 *
 * 한국만 쉼표를 넣는다. 원화 가격은 정수라 "250,000" 을 숫자로 되돌려도
 * 잃는 것이 없다. 반면 달러는 소수점을 쓰는데, 상태가 number 인 칸에 쉼표
 * 서식을 씌우면 "12.5" 를 치는 도중 "12." 가 12 로 접혀 소수점을 이어 칠 수
 * 없다. 그래서 미국은 브라우저 숫자 입력을 그대로 둔다.
 */
export function moneyInputProps(
  value: number | string | null | undefined,
  onValue: (n: number) => void,
  market?: Market,
) {
  const raw = value == null || value === '' ? '' : String(value)
  if ((market ?? current) === 'KR') {
    return {
      type: 'text' as const,
      inputMode: 'decimal' as const,
      value: formatNumberInput(raw),
      onChange: (e: { target: { value: string } }) => onValue(parseNumberInput(e.target.value)),
    }
  }
  return {
    type: 'number' as const,
    step: 'any',
    value: raw,
    onChange: (e: { target: { value: string } }) =>
      onValue(e.target.value === '' ? 0 : Number(e.target.value)),
  }
}
