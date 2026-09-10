"""
backend/db/schema.py
──────────────────────
테이블 DDL 정의. 기동할 때마다 실행되며 멱등이다.

예전에는 여기서 `pfp/data/*.json` 과 `outputs/*.md` 를 DB 로 옮기는 1회성
마이그레이션(`_migrate_json`)도 돌렸다. 인증이 붙기 전, 사용자가 한 명이고
데이터가 파일에 있던 시절의 코드다. 제거한 이유는 세 가지가 동시에 막고
있었기 때문이다:

  1. 그 코드는 `ON CONFLICT(user_id, ticker)` 로 upsert 했는데, 거기 맞는
     유니크 제약이 더는 없다. 아래 DO 블록이 `holdings` 기본키를
     `(user_id, market, ticker)` 3컬럼으로 바꾼다. 실제로 실행하면
     `no unique or exclusion constraint matching the ON CONFLICT
     specification` 로 죽는다.
  2. 전부 `user_id='default'` 로 넣는데 `fk_holdings_user` 가 `users` 를
     참조한다. 그 행이 없는 DB 에서는 FK 위반으로 죽고, 있는 DB 에서는
     스킵 가드(`holdings WHERE user_id='default'`)에 걸려 애초에 들어오지
     않는다. 성공하는 경로가 없다.
  3. 설령 들어갔어도 조회 경로가 전부 토큰의 uid 로 거르므로(CLAUDE.md §1.2)
     `'default'` 행은 어떤 사용자에게도 보이지 않는다.

원본도 남아 있지 않다 — `pfp/data/` 는 리포에 존재하지 않는다.
"""
from __future__ import annotations

import logging

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)

_DDL = """
-- 사용자 테이블
--   id = Firebase UID. 비밀번호는 저장하지 않는다 (Firebase Auth 가 관리).
--   최소 식별 정보만 보관: 이메일 + 표시이름. 그 외 개인정보는 수집하지 않는다.
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    email       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS provider      TEXT;   -- google | password | naver | kakao
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS photo_url     TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS disabled      BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS username      TEXT;
-- 닉네임·나이는 가입 후 선택 입력이다. 필수로 두면 가입 단계가 길어지고,
-- 나이는 없어도 서비스가 동작하므로 NULL 을 허용한다.
ALTER TABLE users ADD COLUMN IF NOT EXISTS age           SMALLINT;
-- 관리자 권한. 일반 가입 경로로는 절대 설정되지 않고, 시드 스크립트로만 부여한다.
-- 권한을 토큰(Firebase custom claims)이 아니라 DB 에 두는 이유는, 토큰은 갱신 전까지
-- 옛 값을 들고 있어 권한 회수가 즉시 반영되지 않기 때문이다.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin      BOOLEAN NOT NULL DEFAULT FALSE;
-- 로그인 후 처음 열릴 시장. 사용자가 프로필에서 고른다.
-- 기본값을 US 로 둔 이유는 기존 사용자의 보유 종목이 전부 미국 종목이라,
-- 값이 없던 계정이 갑자기 빈 한국 화면으로 열리면 데이터가 사라진 것처럼 보인다.
ALTER TABLE users ADD COLUMN IF NOT EXISTS default_market TEXT NOT NULL DEFAULT 'US';
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(LOWER(email)) WHERE email IS NOT NULL;
-- 아이디는 로그인 식별자다. 대소문자를 구분하면 'Foo' 와 'foo' 가 다른 계정이 되어
-- 사용자가 혼란스럽고 사칭에도 쓰일 수 있으므로, 소문자 기준으로 유일성을 건다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(LOWER(username)) WHERE username IS NOT NULL;

-- 사이트 전역 설정 (관리자만 변경).
--   AI 기능은 호출마다 외부 API 비용이 나간다. 비용이 급증하거나 키를 갈아끼우는
--   동안 서비스를 통째로 내리지 않고 기능만 끌 수 있어야 해서 스위치를 둔다.
--   키-값 한 줄짜리 테이블이라 설정이 늘어도 마이그레이션이 필요 없다.
CREATE TABLE IF NOT EXISTS site_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT
);

-- 심층 분석 사용 기록.
--   관리자가 '24시간 1회' 제한을 켰을 때 판단 근거가 된다. 잡 저장소는 메모리라
--   서버가 재시작되면 사라지고, reports 테이블은 취소·실패한 시도를 남기지 않아
--   횟수 계산에 쓸 수 없다. 그래서 시도 자체를 따로 기록한다.
--   used_at 에 인덱스를 두는 이유는 조회가 늘 "최근 N시간" 범위이기 때문이다.
CREATE TABLE IF NOT EXISTS deep_analysis_usage (
    id       BIGSERIAL PRIMARY KEY,
    user_id  TEXT NOT NULL,
    kind     TEXT NOT NULL,           -- equity_research | industry_research | macro_scenario
    used_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_deep_usage_user_time
    ON deep_analysis_usage(user_id, used_at DESC);

-- 백그라운드 잡 상태.
--   전에는 프로세스 메모리에만 있었다. Cloud Run 은 인스턴스를 여러 개 띄우고
--   세션 고정도 없어서, 생성은 A 인스턴스에서 도는데 폴링이 B 로 가면
--   "잡을 찾을 수 없습니다" 가 떴다 — 사용자에게는 리포트가 증발한 것으로 보인다.
--   DB 에 두면 어느 인스턴스가 받아도 같은 상태를 본다. 재시작도 견딘다.
--
--   cancelled 를 status 와 별도 컬럼으로 두는 이유: 작업 스레드는 이 값만
--   짧은 주기로 확인하면 되고, 결과(result)까지 매번 읽어 올 필요가 없다.
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,              -- reports | macro | optimizer
    user_id    TEXT,
    status     TEXT NOT NULL,              -- pending | done | error | cancelled
    result     JSONB,
    message    TEXT,
    cancelled  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 오래된 잡 청소용
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

-- ── 시장 구분 (US / KR) ──────────────────────────────────────────────────────
--   미국과 한국 자산을 완전히 분리해 다룬다. 한 포트폴리오에 원화·달러 종목이
--   섞이면 평가액이 `800,000 + 2,300` 처럼 단위 없이 더해져 수익률·비중·최적화가
--   전부 무의미해진다. 화면도 시장별로 갈리므로 조회는 항상 market 을 건다.
--
--   테이블을 복제하지 않고 컬럼으로 나눈 이유: 복제하면 스키마가 두 벌이 되어
--   이후 모든 변경을 두 번 해야 한다. 사용자에게는 완전히 별개로 보이면서
--   유지보수는 한 벌로 끝난다.
--
--   기존 데이터는 전부 미국 종목이므로 US 로 채운다(DEFAULT 'US').
ALTER TABLE holdings  ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'US';
ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'US';
ALTER TABLE reports   ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'US';

-- 같은 사용자가 두 시장에서 같은 티커를 가질 수 있어야 한다.
-- (예: 미국 계좌의 005930 ADR 과 한국 계좌의 005930.KS 는 별개 자산)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'holdings_pkey') THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'holdings'::regclass AND i.indisprimary AND a.attname = 'market'
        ) THEN
            ALTER TABLE holdings DROP CONSTRAINT holdings_pkey;
            ALTER TABLE holdings ADD PRIMARY KEY (user_id, market, ticker);
        END IF;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_trade_log_user_market_date
    ON trade_log(user_id, market, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_reports_user_market
    ON reports(user_id, market, created_at DESC);

-- 탈퇴한 사용자가 남긴 공용 리포트의 작성자 자리.
--   공용 리서치(scope='shared')는 종목·산업 분석이라 특정 개인의 것이 아니고
--   다른 사용자들이 캐시로 재사용한다. 그래서 탈퇴 시 지우지 않고 작성자만
--   이 행으로 옮긴다.
--   reports.user_id 는 NOT NULL 이고 users 를 참조하므로, 이 행이 실제로
--   있어야 한다. 없으면 익명화가 FK 위반으로 실패하고 **탈퇴 전체가 롤백된다** —
--   실제로 그래서 탈퇴가 계속 실패하고 DB 행만 남는 계정이 쌓였다.
--   이메일이 없으므로 로그인·가입 경로에 걸리지 않는다.
INSERT INTO users (id, name, email)
VALUES ('__deleted__', '(탈퇴한 사용자)', NULL)
ON CONFLICT (id) DO NOTHING;

-- 개인 데이터는 사용자 삭제 시 함께 지워져야 한다 (탈퇴 요구 대응).
-- 기존 테이블에 FK 가 없으므로 소급 적용한다.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_holdings_user') THEN
        ALTER TABLE holdings  ADD CONSTRAINT fk_holdings_user
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_trade_log_user') THEN
        ALTER TABLE trade_log ADD CONSTRAINT fk_trade_log_user
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_reports_user') THEN
        ALTER TABLE reports   ADD CONSTRAINT fk_reports_user
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
    END IF;
END $$;

-- 보유 종목 (사용자별)
CREATE TABLE IF NOT EXISTS holdings (
    user_id     TEXT NOT NULL DEFAULT 'default',
    ticker      TEXT NOT NULL,
    qty         DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_cost    DOUBLE PRECISION NOT NULL DEFAULT 0,
    sector      TEXT NOT NULL DEFAULT 'Other',
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, ticker)
);

-- 거래 이력 (사용자별)
CREATE TABLE IF NOT EXISTS trade_log (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    trade_date  DATE NOT NULL,
    ticker      TEXT NOT NULL,
    trade_type  TEXT NOT NULL,
    qty         DOUBLE PRECISION NOT NULL,
    price       DOUBLE PRECISION,
    memo        TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trade_log_user_date
    ON trade_log(user_id, trade_date DESC);

-- 시장 가격 캐시 (일별 종가) — 공통 데이터
CREATE TABLE IF NOT EXISTS market_prices (
    ticker      TEXT NOT NULL,
    price_date  DATE NOT NULL,
    close_price DOUBLE PRECISION NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, price_date)
);
CREATE INDEX IF NOT EXISTS idx_market_prices_ticker
    ON market_prices(ticker, price_date DESC);
-- 일별 거래량. 트레이딩 신호 1차 필터(당일 거래량 vs 20일 평균)에 쓴다.
-- 종가와 같은 (ticker, price_date) 행에 붙이며, 없으면 NULL — 기존 종가 소비자는 영향 없다.
ALTER TABLE market_prices ADD COLUMN IF NOT EXISTS volume DOUBLE PRECISION;

-- 실시간 시장 스냅샷 (1분마다 갱신) — 공통 데이터
CREATE TABLE IF NOT EXISTS market_snapshot (
    ticker          TEXT PRIMARY KEY,
    price           DOUBLE PRECISION,
    change_1d       DOUBLE PRECISION,
    change_1d_pct   DOUBLE PRECISION,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 공통 데이터 캐시 (macro, doom_radar, sector 등 유형별 TTL)
CREATE TABLE IF NOT EXISTS common_cache (
    cache_type  TEXT PRIMARY KEY,
    data        JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at  TIMESTAMPTZ
);

-- 생성된 레포트 (사용자별)
CREATE TABLE IF NOT EXISTS reports (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    report_type TEXT NOT NULL,
    filename    TEXT NOT NULL UNIQUE,
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reports_user_type
    ON reports(user_id, report_type, created_at DESC);

-- 공용/개인 구분 및 재사용 키
--   scope='shared'  : 종목·산업 리서치. 분석 대상이 같으면 누가 만들었든 동일한 결과이므로
--                     최초 1인이 생성한 것을 전체 사용자가 공유한다 (중복 생성·비용 방지).
--   scope='private' : 매크로 시나리오처럼 사용자 프롬프트에 종속된 결과. 공유 불가.
--   subject_key     : 재사용 판정 키. 종목=티커, 산업=industry_id. 그 외 NULL.
ALTER TABLE reports ADD COLUMN IF NOT EXISTS scope       TEXT DEFAULT 'private';
ALTER TABLE reports ADD COLUMN IF NOT EXISTS subject_key TEXT;
CREATE INDEX IF NOT EXISTS idx_reports_shared_lookup
    ON reports(report_type, subject_key, created_at DESC)
    WHERE scope = 'shared';

-- AI 분석 결과 캐시 (사용자별)
CREATE TABLE IF NOT EXISTS analysis_cache (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    analysis_type   TEXT NOT NULL,
    cache_key       TEXT NOT NULL,
    result          JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    UNIQUE(user_id, analysis_type, cache_key)
);
CREATE INDEX IF NOT EXISTS idx_analysis_cache_lookup
    ON analysis_cache(user_id, analysis_type, cache_key);

-- 상장 티커 유니버스 (NASDAQ + NYSE 계열 전 종목)
-- 출처: NASDAQ Trader SymDir (nasdaqlisted.txt / otherlisted.txt) — 공개·무제한
CREATE TABLE IF NOT EXISTS ticker_universe (
    ticker       TEXT PRIMARY KEY,
    name         TEXT,
    exchange     TEXT,           -- NASDAQ | NYSE | NYSE MKT | NYSE ARCA | BATS | IEX
    is_etf       BOOLEAN DEFAULT FALSE,
    sector       TEXT,
    industry     TEXT,
    tier         SMALLINT DEFAULT 3,   -- 1=실시간 스트리밍, 2=자주, 3=순환
    active       BOOLEAN DEFAULT TRUE,
    listed_seen  DATE DEFAULT CURRENT_DATE,
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ticker_universe_tier
    ON ticker_universe(tier, active);
CREATE INDEX IF NOT EXISTS idx_ticker_universe_exchange
    ON ticker_universe(exchange) WHERE active;

-- 종목 분석 결과 캐시 (하루 1회 계산 → 전 사용자 공유)
--   퀀트 스코어·패닉 점수·국면·VaR·성과 등은 일봉 기반이라 하루 단위로만 의미가 바뀐다.
--   최초 호출자가 계산해 저장하면 그날 나머지 호출은 DB 에서 즉시 읽는다.
--   optimizer(포트폴리오 맥락)는 사용자마다 다르므로 여기 넣지 않고 매 요청 계산한다.
CREATE TABLE IF NOT EXISTS ticker_analytics (
    ticker      TEXT NOT NULL,
    period      TEXT NOT NULL,          -- '1y' 등 조회 기간
    payload     JSONB NOT NULL,         -- 공용 응답 본문
    computed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, period)
);
CREATE INDEX IF NOT EXISTS idx_ticker_analytics_fresh
    ON ticker_analytics(ticker, period, computed_at DESC);

-- market_snapshot 확장: 거래량·고저·세션 구분·수집 경로
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS volume       DOUBLE PRECISION;
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS day_high     DOUBLE PRECISION;
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS day_low      DOUBLE PRECISION;
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS prev_close   DOUBLE PRECISION;
-- regular | pre | post | closed24h : 이 값이 어느 세션의 가격인지
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS session      TEXT;
-- ws(스트리밍) | poll(배치) | close(종가)
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS source       TEXT;
CREATE INDEX IF NOT EXISTS idx_market_snapshot_updated
    ON market_snapshot(updated_at DESC);
"""


def init_schema():
    """DDL 실행 (멱등). 기동할 때마다 돌아도 안전하다."""
    if not is_available():
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
        logger.info("DB 스키마 초기화 완료")
    except Exception as e:
        logger.error(f"스키마 초기화 오류: {e}")
