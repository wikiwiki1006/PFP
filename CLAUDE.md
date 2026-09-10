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

새 머신에서 클론했으면 먼저 `./setup.sh` 를 한 번 돌린다. venv·node_modules·
`backend/.env`·`secrets/firebase-admin.json` 은 전부 추적되지 않아 클론에 딸려오지
않고, 없으면 백엔드가 기동조차 하지 않는다. 비밀값은 Secret Manager 에서 받으므로
`gcloud auth login` 이 먼저다. 멱등이라 여러 번 돌려도 된다.

venv 인터프리터 경로는 OS 마다 다르다. 아래 `$PY` 로 적은 자리에
macOS/Linux 는 `venv/bin/python`, Windows 는 `venv/Scripts/python.exe` 를 넣는다.
`dev.sh` 와 `worktree-setup.sh` 는 이걸 자동으로 고른다.

```bash
# 백엔드 (venv 사용, uvicorn 은 -m 으로 부른다)
PYTHONPATH=$PWD $PY -m uvicorn backend.main:app --port 8000

# 프론트
cd frontend && npm run dev          # :3000, /api 는 :8000 으로 프록시

# 테스트 — 커밋·배포 전 필수
$PY -m pytest backend/tests -q

# 타입체크는 tsc -b 로만 유효하다
cd frontend && npm run build
```

pytest 는 `requirements.txt` 가 아니라 `requirements-dev.txt` 에 있다. 없다고 나오면
`$PY -m pip install -r requirements-dev.txt` 를 한 번 돌린다.

**게이트 명령을 파이프에 물리지 마라.** `npm run build 2>&1 | tail -20` 의 종료코드는
`tail` 것이라 빌드가 깨져도 0 이 나온다. 실패를 통과로 읽는다. 종료코드가 필요하면
`npm run build > log 2>&1; echo $?` 처럼 파일로 받는다.

`npx tsc --noEmit -p tsconfig.json` 은 솔루션 스타일 config 라 **아무것도 검사하지
않는다.** 통과해도 빌드가 깨질 수 있다. 반드시 `npm run build` 로 확인한다.

`venv/bin/uvicorn` (Windows 는 `venv/Scripts/uvicorn.exe`) 의 셔뱅은 옛 경로를
가리켜 깨져 있다. 언제나 `$PY -m uvicorn` 으로 부른다.

---

## 7. 병렬 에이전트 협업

Claude Code 세션 여러 개가 역할을 나눠 이 리포를 **동시에** 개발할 때의 규약이다.
혼자 작업 중이면 이 절은 무시하고 `./dev.sh` 만 쓰면 된다.

병렬로 들어가는 순간 세 가지가 충돌한다. **워킹트리·포트·DB** 다. 셋 다 갈라야
병렬이지, 안 가르면 그냥 서로 덮어쓰기다.

### 7.1 워크트리를 만든다

```bash
./worktree-setup.sh <역할> <슬롯>     # 예: ./worktree-setup.sh develop 1
```

`../pfp-<역할>` 에 워크트리를, `agent/<역할>` 브랜치를 만들고 포트 슬롯을 준다.

`git worktree` 는 추적되는 파일만 준다. 그래서 이 스크립트가 추가로 해 주는 것:

- `venv` (784M) 와 `frontend/node_modules` (413M) 는 **심볼릭 링크**. 복사하면
  슬롯 5개에 6GB 다. 링크해도 되는 이유는 항상 `<venv>/python -m uvicorn` 으로
  부르고 `python -m` 이 cwd 를 `sys.path` 에 넣기 때문이다 — 인터프리터는 공유하되
  `backend` 패키지는 각 워크트리 것을 import 한다.
- `backend/.env` 와 `frontend/.env.local` 은 **복사**. 링크하면 한 에이전트가 키를
  바꿔 실험하다 메인 파일을 덮어써 나머지가 같이 죽는다.
- `ROLE.md` 생성 — 역할·슬롯·소유 경로가 파일로 남아야 컨텍스트가 밀려도 유지된다.

### 7.2 소유 경로 — 한 경로는 한 에이전트만 쓴다

충돌을 막는 유일하게 확실한 규칙이다. 이게 없으면 에이전트는 눈에 보이는 버그를
그냥 고치는데, 그 파일은 다른 창도 고치고 있다.

| 역할 | 워크트리 | 브랜치 | 슬롯 | 소유 경로 |
|---|---|---|---|---|
| 통합 (main) | `PFP/` | `main` | 0 | `CLAUDE.md`, `*.sh`, `requirements*.txt`, `frontend/package.json`, main 병합 전담 |
| 기능 개발 | `pfp-develop/` | `agent/develop` | 1 | `frontend/src/pages/` `frontend/src/components/` `backend/routers/` |
| DB 관리 | `pfp-dbmanage/` | `agent/dbmanage` | 2 | `backend/db/` `backend/services/cash_ledger.py` |
| 테스트 | `pfp-test/` | `agent/test` | 3 | `backend/tests/` `frontend/e2e/` |
| 리포트 품질 | `pfp-reportmanage/` | `agent/reportmanage` | 4 | `backend/services/report_writer.py` `backend/services/ai_analysis.py` `backend/services/daily_report.py` |
| 최적화 | `pfp-programoptimize/` | `agent/programoptimize` | 5 | `backend/services/quant_metrics.py` `backend/services/portfolio_*.py` |

워크트리는 리포와 **형제 디렉터리**로 만들어진다 (`vscode/PFP/` 옆에 `vscode/pfp-develop/`).

`frontend/src/lib/market.ts` 와 `backend/services/markets.py` 는 모두가 건드리고
싶어하는 파일이다. **통합 소유로 둔다.**

소유하지 않은 경로가 필요하면 직접 고치지 않는다. 통합 세션에 요청 → 통합이 담당
창에 전달 → 담당이 고치고 커밋 → 통합이 알림. 느려 보이지만 두 창이 같은 파일을
다르게 고쳐 병합에서 터지는 것보다 빠르다.

### 7.3 대화는 별 구조로

세션끼리는 `SendMessage({to: "<세션이름>", ...})` 로 직접 말할 수 있다. 하지만
역할끼리 자유롭게 말하면 채널이 10개가 되고 같은 결정이 창마다 다르게 내려진다.
**역할 창은 통합 세션하고만 말한다.**

세션 이름은 `ListAgents` 로 확인한다. **재시작하면 바뀐다** — 배치표에 적어둔
이름을 믿지 말고 보내기 전에 확인한다.

보고는 네 줄로 고정한다. 자유 서술로 주고받으면 통합이 매번 되물어야 한다.

```
브랜치: agent/develop @ <커밋해시>
건드린 경로: frontend/src/pages/AlphaTerminal.tsx
테스트: pytest 163 passed / npm run build OK
요청: (남의 경로가 필요하면 여기에)
```

### 7.4 DB 를 가른다

`holdings` 기본키가 `(user_id, market, ticker)` 다. 여러 창이 같은 테스트 계정으로
같은 DB 를 쓰면 같은 종목 행을 서로 덮는다. 테스트 창이 보유를 지우면 기능 창의
화면이 빈다.

**창마다 독립 데이터베이스가 있다.** 로컬 도커 postgres(5433) 안에 역할별로
하나씩 — `pfp_develop`, `pfp_dbmanage`, `pfp_test`, `pfp_reportmanage`,
`pfp_programoptimize`. 전부 개발 데이터 사본을 그대로 갖고 시작하므로
(시세 69만 행 포함) 빈 DB 로 시작하는 불편이 없다.

```bash
./dev.sh --slot 1 --db-branch develop
```

연결 문자열은 `db-targets.env` 에 있다 (gitignore, 워크트리마다 복사본).
`dev.sh` 가 그 파일을 읽어 `PFP_DB_<이름>` 을 찾는다. 이름을 틀리면 기동을
거부한다 — 조용히 공유 DB 로 떨어지면 격리했다고 믿는 채로 서로 덮어쓴다.

한 창이 `delete from holdings` 를 해도 다른 창은 그대로다. 실제로 그렇게
검증했다.

**`sslmode=disable` 을 빼지 마라.** `_dsn()` 은 `DATABASE_URL` 에 `sslmode` 가
없으면 `require` 를 붙인다 (Neon 용). 로컬 도커는 SSL 을 안 하므로 그대로 두면
연결이 거부된다.

#### Neon 은 실데이터다 — 역할 창은 붙지 않는다

`backend/.env` 의 `CONNECTION_STRING` 이 Neon 을 가리킨다. 그곳에는 실제
서비스 데이터가 있다:

| | Neon | 로컬 도커 |
|---|---|---|
| users | 12 | 3 |
| holdings | 30 | 4 |
| reports | 46 | 39 |

코드는 `DATABASE_URL` 을 읽고 `CONNECTION_STRING` 은 읽지 않는다. **그 상태를
유지한다** — 이름이 어긋나 있는 것이 안전장치다. 승격시키지 마라.

Neon 조회가 필요하면 통합 세션이 그 값을 명시적으로 넘겨 쓴다. 쓰기는 하지 않는다.
`--prod-db` 는 GCP Secret Manager 쪽 운영 DB 로, 이것과 또 다르다.

### 7.5 병합

- **`main` 에 직접 커밋하지 않는다.** 지금까지 모든 커밋이 main 에 직접 올라갔는데,
  병렬에서는 그 방식이 바로 충돌이다.
- 핸드오프 전 게이트: `pytest backend/tests -q` 와 `npm run build` 를 통과시킨다.
  통과 못 하면 통합에 보고하지 않는다. **파이프에 물려서 확인하지 마라** —
  `| tail` 은 종료코드를 삼켜서 깨진 빌드를 통과로 읽는다 (§6 참고).
- `CLAUDE.md` 는 통합만 고친다. 다섯 창이 전부 규칙을 추가하고 싶어하고, 매 병합마다
  충돌한다. 고칠 내용은 통합에 요청한다.
- 브랜치가 오래 갈라져 있을수록 병합 비용이 폭증한다. 작업 단위마다, 최소 하루
  두 번은 `main` 으로 합치고 각 창이 `git rebase main` 한다.
- 병합 후 `pytest backend/tests/test_market_isolation.py` 를 돌린다. `market` 을
  빼먹은 조회가 반복해서 사고를 냈다.

### 7.6 함정

- **venv 는 공유된다.** 한 창의 `pip install` 이 다섯 창 전부에 즉시 반영된다.
  버전을 올렸다 다른 창의 테스트가 깨지면 원인을 자기 코드에서 찾게 된다. 패키지를
  건드리는 작업은 통합 세션에서만 하고, `requirements.txt` 를 바꿨으면 전 창에 알린다.
- **`.env` 는 복사본이다.** 키를 회전하면 메인 + 워크트리 전부를 고쳐야 한다.
- **Firebase 프로젝트가 로컬과 운영이 같다.** 그래서 병렬 창은 인증 에뮬레이터를
  쓴다 — `./auth-emulator.sh` 를 한 번 띄우고 각 창이 `--auth-emulator` 로 붙는다.
  §7.8 참고. 에뮬레이터 없이 돌리는 창은 운영 인증에 붙으므로 새 계정을 만들면
  안 된다 (고정 계정 `test@gmail.com` 만).
- **서브에이전트는 서로 대화하지 못한다.** `Agent` 툴로 띄우는 서브에이전트는 부모에게만
  보고하고 끝난다. 역할끼리 주고받는 구조가 필요하면 독립 세션(창)이어야 한다.
  서브에이전트는 창 안에서 조사를 병렬로 돌릴 때 쓴다.
- **포트가 겹치면 조용히 넘어가지 않는다.** `strictPort` 라 즉시 실패한다. 일부러
  그렇게 뒀다 — 두 창이 자기도 모르게 같은 백엔드를 보고 있는 것보다 낫다.

### 7.7 수집은 로컬에서 꺼 둔다

`main.py` 의 `ENABLE_SCHEDULER` 기본값은 `true` 다. 그대로 두면 기동할 때마다 공통 티커
프리패치(2년치) + SP500 전 종목 수집 + 1분 주기 스케줄러가 돈다. 이걸 끄는 것은
`Dockerfile` 뿐이었고 로컬에는 아무도 없었다.

병렬에서는 이게 바로 사고다. 창이 다섯이면 같은 수집이 다섯 번 돈다.
**yfinance 는 IP 단위로 막으므로 한 창이 레이트리밋에 걸리면 다섯 창이 같이
죽는다.** 창마다 DB 가 다르니 캐시도 공유되지 않아 중복이 그대로 5배가 되고,
행 수가 저절로 늘어 벤치마크 기준값이 이동한다.

`dev.sh` 가 로컬 기본을 꺼 둔다. 수집 경로 자체를 시험해야 하면 `--scheduler`
로 켜되 **한 번에 한 창만** 켠다.

### 7.8 인증 에뮬레이터

Firebase 프로젝트는 로컬과 운영이 같다. 창이 다섯이면 오염이 다섯 배로 빨라진다.
에뮬레이터는 완전히 분리된 인증 저장소라 계정을 마음껏 만들어도 된다.

```bash
./auth-emulator.sh                      # 창 하나에서 한 번만 (다섯이 공유)
./seed-test-user.sh                     # 계정 시드 (에뮬레이터는 비어서 시작)
./dev.sh --slot 1 --auth-emulator       # 각 개발 창
```

에뮬레이터는 하나만 띄운다. 인증 저장소를 공유하는 편이 낫다 — 한 번 시드한
계정을 다섯 창이 전부 쓴다. 포트를 갈라야 하는 것은 백엔드·프론트지 인증이 아니다.
내리면 데이터가 사라지므로 다시 띄우면 시드도 다시 돌린다.

**`FIREBASE_AUTH_EMULATOR_HOST` 와 `VITE_USE_AUTH_EMULATOR` 를 파일에 쓰지 마라.**
`dev.sh` 가 프로세스 환경으로만 넘긴다. 이유는 하나다 — `firebase_admin` 은 이
변수가 보이면 ID 토큰의 **서명 검증을 건너뛴다** (`_token_gen.py`:
`if emulated: verified_claims = payload`, kid·alg 검사도 `not emulated` 조건이라
같이 빠진다). 남는 검사는 aud/iss/sub 뿐이고 셋 다 호출자가 스스로 적는 값이다.
그 값이 `backend/.env` 에 남아 있으면 `deploy.sh` 가 그 백엔드를 cloudflared 로
인터넷에 공개하는 순간 누구나 토큰을 위조해 임의 사용자로 로그인할 수 있다.
`deploy.sh` 에 가드가 있어 그때는 기동을 거부하지만, 애초에 파일에 남기지 않는다.

프론트가 `.env.local` 없이도 켜지는 이유: vite 의 `loadEnv` 가 `process.env` 에서
`VITE_` 접두사 키를 그대로 가져간다.

**백엔드와 프론트는 반드시 같이 켠다.** 한쪽만 켜면 "가입은 되는데 로그인은 안 되는"
상태가 된다 — 가입은 Admin SDK 가 에뮬레이터를 따라가지만 로그인·비밀번호 재설정은
`routers/auth.py` 의 `IDENTITY_TOOLKIT` REST 주소를 직접 부르기 때문이다.
`--auth-emulator` 플래그 하나가 둘 다 켠다.

`init_firebase()` 는 자격증명이 하나도 없어도 `True` 를 반환한다 —
`initialize_app` 이 지연 검증이라 실패는 나중에 `verify_id_token` 에서 난다.
"Firebase Admin 초기화 완료" 로그를 정상 신호로 읽지 마라.

체크리스트: https://claude.ai/code/artifact/8fe11e8f-d846-424b-ae28-9fad554f5d5c

---

## 8. 배포

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

## 9. 작업 방식

- **브라우저로 확인한다.** 코드만 봐서는 안 보이는 버그가 반복해서 나왔다
  (전환 시 재조회 0건, 5일 묵은 지수, 잘못된 기본 지수). Playwright 는
  npx 캐시에 있다: `NODE_PATH=$(find ~/.npm/_npx -maxdepth 4 -type d -name playwright | head -1 | xargs dirname)`
- **일괄 치환은 구문을 깨뜨린다.** 정규식으로 여러 파일을 고칠 때 다중 행
  import 안쪽에 줄이 끼거나 중첩 괄호가 어긋나는 사고가 반복됐다. 바꾼 뒤
  `python -c "import ast; ast.parse(open(f).read())"` 로 확인한다.
- BSD `sed` 는 `\b` 를 지원하지 않는다.
- 운영 DB 에 직접 붙지 않는다. 스키마 변경은 `schema.py` 에 넣어 기동 시 적용한다.

---

## 10. 미해결

- 비밀번호 로테이션: 관리자·개인 계정(`10october@`), Neon DB.
  옛 이미지에 `.env` 가 들어간 건도 있어 우선순위가 높다.
- `gcr.io` 의 옛 이미지 태그 정리 (비밀값 포함).
- `naver-site-verification` 미등록 (`index.html` 에 주석 처리됨).
- 프로세스 내 스케줄러의 tier1 게이트가 미국 전용 — 켤 계획이면 시장별로 분리 필요.
