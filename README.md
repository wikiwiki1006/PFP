# PFP — Personal Financial Platform

개인 투자 포트폴리오 분석 · 시장 분석 · AI 리포트를 통합한 풀스택 금융 플랫폼.

**Backend**: FastAPI (Python) · **Frontend**: React + Vite + TypeScript · **DB**: Supabase (PostgreSQL)

---

## 주요 기능

### 1. 포트폴리오 관리
- 보유 종목 추가 / 수정 / 삭제, 매매 내역 기록
- 수익률 · MDD · Sharpe Ratio · 섹터 비중 자동 계산
- S&P 500 / NASDAQ 벤치마크 대비 수익률 비교 (에쿼티 커브)
- 종목 간 상관관계 히트맵
- 섹터 자동 분류 (AI 기반 Other 섹터 자동 탐지)
- PDF 리포트 내보내기

### 2. 시장 현황
- 주요 지수 실시간 현황 (S&P 500, NASDAQ, 다우, 국채금리, 달러, 금유가)
- 시장 스냅샷 1분 주기 자동 갱신
- 섹터별 등락률 바차트
- 보유 종목 뉴스 피드
- 실적 / 배당 일정

### 3. 매크로 분석
- FRED 데이터 연동 — 금리차(T10Y2Y), HY 스프레드, M2, 소비자심리지수 등
- 거시 지표 AI 분석 (Anthropic Claude)
- 매크로 시나리오 작성 → MC 시뮬레이션 직접 연결

### 4. 몬테카를로 시뮬레이션
세 가지 독립적인 MC 모델 제공:

| 모델 | 알고리즘 | 주요 출력 |
|------|----------|-----------|
| **개별 종목 GBM** | 기하 브라운 운동 | 목표가 터치/만기 달성 확률 |
| **포트폴리오** | 다변량 정규분포 + Cholesky | 목표 수익률 달성 확률, VaR |
| **매크로 시나리오** | GBM + Hawkes 자기흥분 점프 | 금리/환율 충격 하 확률, CVaR |

- 섹터 베타 자동 계산 (종목 vs 섹터 ETF 회귀)
- 펀더멘탈 드리프트 보정 (PEG, Forward PE 반영)
- 2,000회 시뮬레이션 경로 시각화

### 5. 포트폴리오 최적화
- **최대 샤프(Max Sharpe)**: 공분산 기반 효율적 프론티어
- **블랙-리터만(Black-Litterman)**: 시장 균형 + 투자자 견해 통합
- **팩터 분석**: 모멘텀 · 가치 · 저변동성 팩터 점수

### 6. Timing Engine (매매 타이밍)
다섯 개 서브 패널로 구성된 종합 타이밍 분석:

| 패널 | 내용 |
|------|------|
| **시장 상황** | 금리차·HY스프레드 백분위 기반 Low/Normal/High 분류 |
| **종목별 시장 상황** | Bull/Sideways/Bear 레짐 구간을 가격 차트에 색상 오버레이 |
| **매매 신호** | S&P 500 전체 볼린저 밴드 스캔, 과매수/과매도 Top 10 |
| **평균 회귀·저항선** | BB 밴드 + 저항선 + 키포인트(돌파·이탈) 인터랙티브 줌 차트 |
| **페어 트레이딩** | 자동 최적 페어 탐색, 가격 스프레드 임계값 초과 구간 강조 |

### 7. AI 분석 (Anthropic Claude)
- **AI Feed**: 포트폴리오 기반 실시간 투자 피드백
- **Daily Brief**: 매일 시장 상황 + 포트폴리오 요약 자동 생성, 히스토리 관리
- **Equity Research**: 개별 종목 심층 리서치 리포트
- **Industry Report**: 산업별 분석 리포트 생성

### 8. S&P 500 가격 데이터 자동 수집
- 전 종목(~503개) 매일 **한국시간 오전 3시** 자동 수집
- 서버 재시작 시 누락 업데이트 자동 복구
- 수집 중에도 사용자 요청 실시간 병렬 처리 (Lock 충돌 없음)
- Pairs Trading 사전 계산 후 DB 캐시 (25h TTL)

---

## 기술 스택

### Backend
| 구분 | 기술 |
|------|------|
| 프레임워크 | FastAPI + Uvicorn |
| 데이터 | yfinance, pandas-datareader (FRED) |
| DB | Supabase (PostgreSQL) + psycopg2 |
| AI | Anthropic Claude API |
| 최적화 | scipy, scikit-learn, PyPortfolioOpt |
| 스케줄링 | 내장 백그라운드 스레드 |

### Frontend
| 구분 | 기술 |
|------|------|
| 프레임워크 | React 18 + TypeScript + Vite |
| 스타일 | Tailwind CSS |
| 차트 | Recharts |
| 상태관리 | TanStack React Query |
| 라우팅 | React Router v6 |
| 아이콘 | Lucide React |

### 인프라
- **DB**: Supabase (PostgreSQL) — `market_prices`, `market_snapshot`, `common_cache` 테이블
- **Backend**: `127.0.0.1:8000` (localhost only)
- **Frontend**: `0.0.0.0:3000` (LAN 공개), Vite `/api/*` → Backend 프록시

---

## 실행 방법

### 사전 준비

1. Python 3.11+ 및 Node.js 18+ 설치
2. `venv` 생성 및 패키지 설치
   ```bash
   python -m venv venv
   venv\Scripts\activate
   pip install -r backend/requirements.txt
   ```
3. 프론트엔드 패키지 설치
   ```bash
   cd frontend
   npm install
   ```
4. 환경 변수 설정 (`backend/.env`)
   ```env
   DB_HOST=your-supabase-host
   DB_PORT=5432
   DB_NAME=postgres
   DB_USER=postgres
   DB_PASSWORD=your-password
   ANTHROPIC_API_KEY=your-anthropic-key
   APP_ENV=development
   ```

### 서버 실행

```bash
# Windows — 백엔드 + 프론트엔드 동시 실행
start.bat
```

또는 개별 실행:
```bash
# Backend
venv\Scripts\uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload

# Frontend
cd frontend
npm run dev
```

접속 주소:
- 로컬: `http://localhost:3000`
- LAN: `http://[PC-IP]:3000`

---

## 프로젝트 구조

```
PFP/
├── backend/
│   ├── db/
│   │   ├── market_cache.py     # DB 캐시 (prices, snapshot, common_cache)
│   │   └── scheduler.py        # 백그라운드 스케줄러 (스냅샷 1분, S&P500 1일)
│   ├── routers/
│   │   ├── portfolio.py        # 포트폴리오 CRUD, 매매 내역
│   │   ├── market.py           # 시장 현황, 뉴스, 매크로
│   │   ├── macro.py            # 매크로 분석, AI 피드백, Daily Brief
│   │   ├── signals.py          # 매매 신호, Timing Engine
│   │   ├── optimizer.py        # 포트폴리오 최적화, MC 시뮬레이션
│   │   └── reports.py          # 리포트 생성 (Daily Brief, Equity Research)
│   ├── services/
│   │   ├── monte_carlo.py      # GBM / 포트폴리오 / 매크로 MC 알고리즘
│   │   ├── trading_signals.py  # BB, 모멘텀, 페어, 레짐, S&P500 스캔
│   │   ├── market_data.py      # yfinance 가격 데이터 로딩 (stale-while-revalidate)
│   │   ├── optimizer.py        # Max Sharpe, Black-Litterman, 팩터 분석
│   │   ├── ai_analysis.py      # Claude AI 분석 서비스
│   │   └── portfolio_calculator.py  # 수익률, MDD, Sharpe 계산
│   └── main.py                 # FastAPI 앱 진입점, CORS, 시작 훅
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── AlphaTerminal.tsx   # 메인 대시보드 (포트폴리오 + AI)
│       │   ├── TimingEngine.tsx    # 매매 타이밍 엔진
│       │   ├── MonteCarlo.tsx      # MC 시뮬레이션 UI
│       │   ├── Optimizer.tsx       # 포트폴리오 최적화 UI
│       │   ├── MacroAnalysis.tsx   # 매크로 분석 UI
│       │   └── ...
│       └── components/
│           └── timing/             # Timing Engine 서브 패널 컴포넌트
├── start.bat                   # 개발 서버 실행 스크립트
└── backend/requirements.txt
```

---

## API 엔드포인트 요약

| 경로 | 설명 |
|------|------|
| `GET /api/market/snapshot` | 주요 지수 현재가 |
| `GET /api/market/macro` | FRED 거시 지표 |
| `GET /api/market/news` | 보유 종목 뉴스 |
| `GET /api/portfolio/holdings` | 보유 종목 목록 |
| `GET /api/portfolio/metrics` | 수익률, MDD, Sharpe 등 |
| `POST /api/optimizer/max-sharpe` | 최대 샤프 최적화 |
| `POST /api/optimizer/black-litterman` | BL 최적화 |
| `POST /api/optimizer/montecarlo/stock` | 개별 종목 MC |
| `POST /api/optimizer/montecarlo/portfolio` | 포트폴리오 MC |
| `POST /api/optimizer/montecarlo/macro` | 매크로 시나리오 MC |
| `POST /api/signals/scan` | 유니버스 전수 스캔 |
| `GET /api/signals/timing-engine` | Timing Engine 통합 데이터 |
| `POST /api/macro/daily-brief` | AI Daily Brief 생성 |
| `POST /api/macro/analyst-feedback` | AI 포트폴리오 피드백 |
