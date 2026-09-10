# ZOOPZOOP (PFP) — 프로젝트 안내

개인 투자 관리 플랫폼. 미국·한국 두 시장을 **완전히 분리해서** 다룬다.

- 운영: https://pfpstudio.io · https://personalfinancialplatform.web.app
- 백엔드: FastAPI → Cloud Run (`pfp-backend`, asia-southeast1)
- 프론트: React + TypeScript + Vite → Firebase Hosting
- DB: PostgreSQL (Neon)
- 인증: Firebase Auth (이메일/비밀번호 · 카카오 · 네이버)

---

## 1. 반드시 지킬 규칙

아래는 전부 **실제로 사고가 났던 것**들이다. 코드를 고치기 전에 읽는다.

### 1.1 시장 분리 — 조회에 `market` 을 빼먹지 않는다

미국은 달러, 한국은 원. 한 포트폴리오에 섞이면 평가액이 `800,000 + 2,300` 처럼
단위 없이 더해져 수익률·비중·최적화가 전부 무의미해진다.

- `holdings` / `trade_log` / `reports` 는 `market` 컬럼(`'US'` | `'KR'`)으로 나뉜다.
- `holdings` 기본키는 `(user_id, market, ticker)` 다.
- 라우터는 `market: str = Depends(market_param)` 로 받아 아래로 그대로 넘긴다.
- 캐시 키에도 시장을 넣는다 (`signal_scan:{market}`, `sector_etf_1mo:{market}`).
  안 넣으면 한국 요청이 미국 결과를 받는다.

새 함수가 시장을 받는지 `backend/tests/test_market_isolation.py` 가 검사한다.

### 1.2 개인 데이터 격리

- `"default"` 같은 상수 사용자 ID 를 절대 쓰지 않는다.
- 모든 개인 데이터 조회는 토큰에서 얻은 `uid` 로만 건다. 경로/쿼리의 사용자
  식별자를 신뢰하지 않는다 (IDOR).
- 잡 조회는 소유자가 다르면 403 이 아니라 **404** 를 준다 — 403 은 "그 잡이
  존재한다"는 사실을 흘린다.

### 1.3 계산 불가를 0 으로 위장하지 않는다

`0%` 는 "보합"이라는 뜻이지 "모름"이 아니다. 값이 없으면 `None` / `null` 을
돌려주고 화면에는 `—` 를 그린다. 정렬에서도 값 없는 항목은 뒤로 보낸다.

실제 사고: 섹터 1M/3M/6M 이 전부 `0.0` 으로 나왔는데, 데이터가 없어서였다.
사용자에게는 "모든 섹터가 보합"으로 보였다.

프론트의 `formatPct()` 는 이미 `null → '—'` 로 처리한다. `?? 0` 을 붙이지 않는다.

### 1.4 통화 표기

| | 미국 | 한국 |
|---|---|---|
| 주가 | `$231.45` (소수 2자리) | `₩71,900` (**소수점 없음**, 호가 단위 1원) |
| 큰 금액 | `$4.20T` / `$850.00M` / `$12.3K` | `₩4.20조` / `₩3.40억` / `₩850만` |
| 차트 축 | `$231` | `₩71,900` (100만 이상만 `만` 축약) |

- 프론트: `frontend/src/lib/market.ts` 의 `formatPrice` / `formatCompact` /
  `formatAxisPrice` 를 쓴다. `$` 를 직접 쓰지 않는다.
- 백엔드(리포트): `backend/services/report_writer.py` 의 `_fmt_amount` /
  `_fmt_price`. 통화는 시장이 아니라 **yfinance 응답**(`currency`,
  `financialCurrency`)에서 읽는다 — 해외 상장·ADR 은 둘이 다르다.
- **원화에 `B`(10억)를 쓰지 않는다.** LLM 이 달러로 읽는다. 실제로 삼성전자
  매출 333조원이 `$333605.94B` 로 프롬프트에 실려 기업 규모가 1,300배로 서술됐다.
- 예외: 상단 마퀴의 WTI·BTC 등 글로벌 지표는 시장과 무관하게 `$` 가 맞다.

### 1.5 시장 캘린더 — 함정이 있다

`backend/services/market_calendar.py`

- 미국 휴장일은 규칙으로 계산한다(NYSE 캘린더).
- **한국은 설·추석이 음력이라 규칙으로 못 구한다.** 그래서 `^KS11` 지수에서
  **실제 거래가 있었던 날**을 읽어 캘린더를 만든다(`_krx_trading_days`, DB 1일 캐시).
- 그 캘린더는 **종가가 확정된 날만** 담는다. 야후는 장 마감 후에도 종가 확정
  전까지 거래량만 있고 종가가 `NaN` 인 행을 준다. 그 행을 개장일로 세면
  `last_completed_kr_session()` 이 도달 불가능한 날짜를 가리켜 **같은 종목을
  무한히 다시 수집**한다. (실제로 그랬다.)
- **따라서 오늘은 항상 캘린더에서 빠져 있다.** 장중 판단에 `is_kr_trading_day()`
  를 쓰면 장이 열려 있는데 "휴장"으로 나와 실시간이 멈춘다.
  → 장중 게이트는 `is_kr_extended_hours()` 를 쓴다(평일 + 시간대만 본다).

### 1.6 시세 저장은 한 곳으로만

`market_cache.save_prices_to_db()` 가 `market_prices` 의 **유일한 SQL 기록 지점**이다.
여기서 캘린더 가드를 건다.

- 비거래일 행 저장 금지
- 종가 미확정 당일 행 저장 금지 (미국 16:00 ET / 한국 15:30 KST 기준)

호출자가 `ffill` 된 프레임을 넘겨도 위조 종가가 영구 저장되지 않게 하려는 구조다.
다른 곳에서 `INSERT INTO market_prices` 를 추가하지 않는다.

### 1.7 실시간 수집 시간대는 시장마다 다르다

`live_quotes._can_move(ticker)` 가 "지금 이 종목 가격이 변할 수 있는가"를 판단한다.

| 대상 | 재수집 시간대 |
|---|---|
| 미국 주식 | 04:00–20:00 ET (시간외 포함) |
| 한국 주식 (`.KS` / `.KQ` / `^KS11`) | 08:30–18:00 KST 평일 |
| 암호화폐·환율·선물 | 항상 |

두 장은 **겹치지 않는다**. KRX 09:00 KST = 20:00 ET 로, 미국 시간외가 끝나는
시각에 한국장이 열린다. "미국이냐 아니냐"로만 갈랐다가 한국 종목이 24시간
자산 취급을 받아 밤새 60초마다 yfinance 를 호출하던 버그가 있었다.

### 1.8 비동기 함수에서 블로킹 I/O 금지

FastAPI 는 `def` 의존성을 스레드풀에서 돌리지만 `async def` 는 이벤트 루프에서
돈다. `async def` 안에서 블로킹 호출(예: `verify_id_token`)을 하면 **전체 서버가
멈춘다.** 실측으로 동시 100명에서 10.6초 → 1.3초 차이가 났다.

인증 의존성은 전부 동기 `def` 다. 되돌리지 않는다.

---

## 2. 구조

```
backend/
  main.py            앱 조립, startup(DB 풀 → 스키마 → 스케줄러)
  routers/           HTTP 경계. 인증·검증만 하고 로직은 services 로
    auth(16) portfolio(18) reports(12) signals(12) macro(8) market(8)
    optimizer(7) admin(2) ticker(1) internal(1)
  services/          도메인 로직
    markets.py           ★ 시장 정의의 단일 출처 (US/KR 스펙, 티커 판별)
    market_calendar.py   ★ 거래일·장중 판단 (1.5 참고)
    live_quotes.py       실시간 시세 (온디맨드, 60초 캐시)
    market_data.py       지수·섹터·매크로
    korea_macro.py       한국은행 ECOS (기준금리·CPI·국고채)
    korea_universe.py    KOSPI200+KOSDAQ150 (네이버 시총순) + 종목명
    trading_signals.py   SMA/MACD/RSI 스캔, 페어 트레이딩
    report_writer.py     AI 리포트 (종목·산업)
    ai_analysis.py       매크로 시나리오 에이전트
    portfolio_calculator.py  자산곡선·수익률·베타
    cash_ledger.py       현금 원장, 보유 재계산
    job_store.py         잡 상태 (DB, 취소 지원)
  db/
    schema.py          DDL 전부. 기동 시 자동 적용 (멱등)
    market_cache.py    시세 캐시 + ★ 유일한 market_prices 기록 지점
    portfolio_repo.py  보유·거래 (시장별)
    scheduler.py       수집 로직 (Cloud Scheduler 가 호출)
  tests/             13개 파일. 대부분 회귀 방지용

frontend/src/
  lib/market.ts        ★ 시장 상태 + 통화 포맷터
  lib/marketStorage.ts 시장별 sessionStorage (잡 상태 격리)
  lib/useMarket.ts     시장 변경 구독 훅
  api/index.ts         axios — 인터셉터가 모든 요청에 market 자동 첨부
  components/MarketSwitch.tsx   전환 시 /terminal 로 전체 새로고침
  pages/  AlphaTerminal(포트폴리오) MacroScenario Optimizer TimingEngine LensReport
```

### 라우팅되는 화면은 5개뿐

`/terminal` `/macro` `/optimizer` `/timing` `/lens`.
`Dashboard.tsx` `Portfolio.tsx` `Market.tsx` `Home.tsx` `Signals.tsx` 는
**라우팅되지 않는 죽은 파일**이다. 고쳐도 화면에 반영되지 않는다.

### DB 테이블

`users` `holdings` `trade_log` `reports` `jobs` `market_prices` `market_snapshot`
`common_cache` `analysis_cache` `ticker_universe` `ticker_analytics`
`site_settings` `deep_analysis_usage`

---

## 3. 시장을 추가·수정할 때

1. `backend/services/markets.py` 의 `MarketSpec` 에 정의를 넣는다
   (통화·지수·섹터 ETF·FRED 시리즈·티커 접미사).
2. 라우터에 `market: str = Depends(market_param)` 를 추가한다.
3. 캐시 키에 시장을 넣는다.
4. 프론트는 `lib/market.ts` 의 `MARKETS` 에 추가한다. API 호출은
   인터셉터가 자동으로 붙이므로 개별 호출부를 고치지 않는다.
5. `pytest backend/tests/test_market_isolation.py` 로 누락을 확인한다.

---

## 4. 데이터 출처

| 데이터 | 출처 | 비고 |
|---|---|---|
| 시세·재무 | yfinance | 한국은 `financialCurrency=KRW` 로 정확히 옴 |
| 미국 매크로 | FRED (키 불필요) | 금리·실업률·CPI·국채 |
| 한국 매크로 | 한국은행 ECOS | `KOREA_BANK_API_KEY` 필요 |
| 한국 종목 목록·시총·종목명 | 네이버 금융 시가총액 | 이미 시총 내림차순 → 7요청 2초 |
| 뉴스·리서치 | Perplexity | |
| AI 리포트 | Anthropic | 스트리밍(중단 지원) |

**막힌 경로 (쓰지 말 것)**

- KRX MDC (`data.krx.co.kr`) — 브라우저 세션 필요, `LOGOUT` 만 돌아온다.
- KRX KIND 상장목록 — 해외 IP 에서 **403**. Cloud Run(싱가포르)에서 안 된다.
  종목명은 네이버 시총 페이지에서 유니버스와 함께 받아 캐시한다(`kr_name_map`).

---

## 5. 수집 스케줄

Cloud Run 은 `ENABLE_SCHEDULER=false` 다. **프로세스 내 스케줄러는 돌지 않는다.**

수집은 Cloud Scheduler 가 바깥에서 깨운다 (OIDC 토큰 검증).

| 작업 | 주기 | 대상 |
|---|---|---|
| `pfp-collect-prices` | 20분 (KST) | 미국 S&P500 ~503종목 |
| `pfp-collect-prices-kr` | 20분 (KST) | 한국 350종목 |

각 호출이 하는 일: 일봉 종가·거래량 수집 → 매매신호 스캔 → 페어 사전계산.
`max_tickers=120` 으로 나눠 받는다 — 한 번에 다 받다 타임아웃되면 아무것도 안 남는다.

**장중 실시간은 요청 시점에 처리된다.** 종목 상세·포트폴리오 현재가·지수 마퀴는
`live_quotes` 가 yfinance 에서 받아 60초 캐시한다. 일봉 수집은 캘린더 가드 때문에
장 마감 후에 들어온다(의도된 동작 — 장중 부분 봉을 종가로 저장하면 안 된다).

`ENABLE_SCHEDULER=true` 로 바꾸는 것은 권장하지 않는다:
- CPU 스로틀링(기본값)이라 유휴 중 백그라운드 스레드가 CPU 를 못 받는다
- `maxScale=5` 인데 인스턴스 간 잠금이 없어 수집이 최대 5중으로 돈다
- tier1 게이트가 미국 시간 전용이라 한국 종목은 어차피 갱신되지 않는다

---

## 6. 개발

```bash
# 백엔드 (venv 사용, uvicorn 은 -m 으로 부른다)
PYTHONPATH=$PWD venv/bin/python -m uvicorn backend.main:app --port 8000

# 프론트
cd frontend && npm run dev          # :3000, /api 는 :8000 으로 프록시

# 테스트 — 커밋·배포 전 필수
venv/bin/python -m pytest backend/tests -q

# 타입체크는 tsc -b 로만 유효하다
cd frontend && npm run build
```

`npx tsc --noEmit -p tsconfig.json` 은 솔루션 스타일 config 라 **아무것도 검사하지
않는다.** 통과해도 빌드가 깨질 수 있다. 반드시 `npm run build` 로 확인한다.

`venv/bin/uvicorn` 의 셔뱅은 옛 경로를 가리켜 깨져 있다. `venv/bin/python -m uvicorn`
으로 부른다.

---

## 7. 배포

프론트만 바뀌었으면 2번만 한다.

```bash
# 1) 백엔드
TAG="rel-$(date +%Y%m%d-%H%M)"
gcloud builds submit --tag "gcr.io/personalfinancialplatform/pfp-backend:$TAG" .
gcloud run deploy pfp-backend --region asia-southeast1 \
  --image "gcr.io/personalfinancialplatform/pfp-backend:$TAG" --quiet

# 2) 프론트
cd frontend && npm run build && cd ..
npx firebase-tools deploy --only hosting
```

- 스키마 마이그레이션은 기동 시 `init_schema()` 가 자동 적용한다(멱등).
  로그에 `DB 스키마 초기화 완료` 가 찍히는지 확인한다.
- 환경변수는 Secret Manager 에 있다. `gcloud run deploy` 에 `--set-env-vars` 를
  쓰면 **기존 값이 전부 날아간다.** 추가는 `--update-secrets` 로 한다.
- `.dockerignore` 의 비밀값 패턴은 `**/.env` 여야 한다. `.env` 한 줄은 최상위만
  걸러내서, 실제 키가 든 `backend/.env` 가 이미지에 통째로 들어간다.
  (한동안 그렇게 배포되고 있었다. 옛 이미지 태그에는 아직 남아 있다.)

---

## 8. 작업 방식

- **브라우저로 확인한다.** 코드만 봐서는 안 보이는 버그가 반복해서 나왔다
  (전환 시 재조회 0건, 5일 묵은 지수, 잘못된 기본 지수). Playwright 는
  npx 캐시에 있다: `NODE_PATH=$(find ~/.npm/_npx -maxdepth 4 -type d -name playwright | head -1 | xargs dirname)`
- **일괄 치환은 구문을 깨뜨린다.** 정규식으로 여러 파일을 고칠 때 다중 행
  import 안쪽에 줄이 끼거나 중첩 괄호가 어긋나는 사고가 반복됐다. 바꾼 뒤
  `python -c "import ast; ast.parse(open(f).read())"` 로 확인한다.
- BSD `sed` 는 `\b` 를 지원하지 않는다.
- 운영 DB 에 직접 붙지 않는다. 스키마 변경은 `schema.py` 에 넣어 기동 시 적용한다.

---

## 9. 미해결

- 비밀번호 로테이션: 관리자·개인 계정(`10october@`), Neon DB.
  옛 이미지에 `.env` 가 들어간 건도 있어 우선순위가 높다.
- `gcr.io` 의 옛 이미지 태그 정리 (비밀값 포함).
- `naver-site-verification` 미등록 (`index.html` 에 주석 처리됨).
- 프로세스 내 스케줄러의 tier1 게이트가 미국 전용 — 켤 계획이면 시장별로 분리 필요.
