"""
backend/db/ai_view_cache.py
───────────────────────────
포트폴리오 최적화의 **종목별 AI 뷰** 공용 캐시.

최적화는 바구니의 종목마다 뷰 하나를 AI 에게 받는다:

    {"expected_return": float, "confidence": float,
     "sentiment": "Bullish" | "Neutral" | "Bearish", "key_driver": str}

사용자 요구는 "다른 사용자가 이미 분석한 종목이면 AI 가 다시 분석하지 않게
재사용하라, 갱신은 하루 한 번" 이다. 그래서 먼저 만든 사람의 뷰를 종목 단위로
저장해 두고, 같은 주기에 다른 바구니가 그 종목을 담으면 꺼내 쓴다. (뷰 요청은
바구니 단위로 한 번 나가 다른 종목·뉴스가 함께 실리지만, 종목 뷰로 나눠 쓰기로
한 것이 요구다.) **뷰만 캐시한다.** Black-Litterman 사후수익은 바구니 전체의
공분산으로 계산하는 값이라 종목 단위로 나눠 쓸 수 없다.

## 키 — (시장, 투자 기간, 티커)

- **시장** (§1.1): `"US"` | `"KR"`. 같은 티커 문자열이 두 시장에 있을 수 있다고
  보고 언제나 키에 넣는다. 모르는 시장 코드는 미국으로 바꾸지 않고 **캐시를 쓰지
  않는다** — `markets.normalize()` 처럼 미국으로 떨어뜨리면 다른 시장의 뷰가 섞인다.
- **투자 기간**: 뷰 프롬프트가 "향후 {기간}" 을 묻기 때문에 기간이 다르면 다른
  뷰다. 부동소수 비교가 흔들리지 않게 **정수 개월**로 바꿔 키에 넣는다.
  규칙은 `_horizon_months()` 에 있다 — `horizon_years × 12` 가 정수(오차 1e-6
  개월 이내)이고 1~1200 이면 그 정수, 아니면 캐시를 쓰지 않는다.
  0.25→3 · 0.5→6 · 1→12 · 2→24. 0.3년(3.6개월)은 반올림하지 않는다 —
  반올림하면 다른 기간으로 만든 뷰를 나눠 쓰게 된다.
- **티커**: 앞뒤 공백을 떼고 대문자. 돌려주는 dict 의 키도 이 형태다.

실제 저장 키는 `ai_view::v1::{시장}::{개월}m::{티커}` 이다. 시장·개월 자리에는
`::` 가 들어갈 수 없어 서로 다른 (시장, 개월, 티커) 가 같은 키가 되지 않는다.
`v1` 은 뷰의 의미가 바뀌는 배포(프롬프트·스키마 변경)에서 올린다 — 올리면 옛 뷰는
읽히지 않고, 안 올리면 다음 초기화까지 옛 뷰가 모든 사용자에게 나간다.

## 만료 — 다음 '장 마감 + 30분'

저장할 때 `market_calendar.next_close_reset(market)` 을 행의 `expires_at` 으로
그대로 넣는다. 미국은 거래일 16:30 ET, 한국은 평일 16:00 KST. 기준 시각을 여기서
다시 계산하지 않는다 — DB·최적화·테스트가 같은 경계를 봐야 한다.

**한 주기에 한 번만 쓴다.** 같은 주기의 유효한 뷰가 이미 있으면 덮지 않는다
(`save_ai_views` 참고). 사용자 요구가 "하루 한 번 갱신" 이다.

## 저장소 — common_cache 를 쓰되 get_common/save_common 은 쓰지 않는다

테이블은 새로 만들지 않았다. `common_cache(cache_type PK, data JSONB, updated_at,
expires_at)` 가 필요한 열을 전부 갖고 있고, 새 DDL 은 창마다 따로 적용해야 한다
(§7.4). 두 헬퍼를 쓰지 않은 이유는 실측으로 확인했다 (pfp_dbmanage, 2026-09-17):

- `get_common` 은 `expires_at` 을 지킨다(SQL 로 과거로 밀면 None). 그 조건은
  아래 조회에 그대로 옮겼다. 다만 **키 하나에 한 번**이라 20종목이면 커넥션
  체크아웃 20번 — 체크아웃마다 `get_conn` 의 `SELECT 1` 이 붙어 문장 40개다.
  여기서는 `cache_type = ANY(%s)` 한 문장으로 읽는다.
- `save_common` 의 만료는 `앱 시각 + ttl_seconds` 라 `next_close_reset` 과 같아질
  수 없다 (int ttl −61~−81ms, float ttl +0~+15ms 어긋남). 그리고 성공·실패 모두
  None 을 돌려줘 **실제로 몇 행을 썼는지 알 수 없다** (NaN 이 든 값은 JSONB 가
  거부했는데 반환은 똑같이 None 이었다).
- `get_common` 은 `expires_at IS NULL` 을 영구 유효로 읽는다. 여기서는 만료 없는
  행을 내보내지 않는다 — 여기 쓰는 행은 전부 만료가 있고, 없는 행은 영원히 퍼진다.

만료된 행은 지우지 않는다. 읽기에서 보이지 않고, 다음 주기의 저장이 같은 키를
덮는다. 그래서 행 수의 상한은 한 번이라도 분석된 (시장, 개월, 티커) 조합 수다.

## 실패 (§1.3)

이 캐시는 비용 절감용이지 안전장치가 아니다. 실패해도 최적화는 AI 를 새로 부르면
되므로 **예외를 올리지 않는다.** 대신 조용하지 않다:

- 읽기 실패 · DB 미연결 · 키로 쓸 수 없는 입력 → `{}` + `logger.warning`
- 쓰기 실패 · DB 미연결 · 키로 쓸 수 없는 입력 → `0` + `logger.warning`
- 깨진 뷰(필수 키 없음, 숫자가 유한하지 않음 등) → 그 종목만 저장하지 않고
  `logger.warning`. 캐시는 다음 초기화까지 **모든 사용자에게** 나가므로 깨진 값이
  퍼진다. 읽을 때도 같은 검사를 거쳐, 통과하지 못한 행은 내보내지 않는다.

## 호출자가 지킬 것

- **AI 가 실제로 돌려준 뷰만 저장한다.** 최적화가 AI 실패 때 만드는 과거수익
  대체 뷰(`key_driver="AI 분석 불가 — ..."`)는 필수 키·유한값·비지 않은
  key_driver 를 전부 갖춰 여기 검사를 통과한다. 저장하면 같은 주기 안에서는
  덮이지도 않아(위 '한 주기에 한 번'), AI 가 한 번 실패한 종목이 다음 초기화까지
  모든 사용자에게 "AI 분석 불가" 로 나간다. 그건 이 모듈이 구별할 수 없다.
- 두 함수 모두 예외를 올리지 않는다. `try` 로 감쌀 필요가 없다.
- 돌려받는 키는 대문자 티커다. 조회할 때도 `t.strip().upper()` 로 찾는다.
"""
from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterable
from datetime import timezone
from numbers import Real
from typing import Any, Optional

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)

_KEY_PREFIX = "ai_view::v1"
_REQUIRED_KEYS = ("expected_return", "confidence", "sentiment", "key_driver")
_SENTIMENTS = frozenset({"Bullish", "Neutral", "Bearish"})

# 정수 개월로 볼 허용 오차. 1e-6 개월은 약 2.6초다 — 부동소수 흔들림은 흡수하고
# 3.6개월 같은 실제로 다른 기간은 걸러낸다.
_MONTH_TOLERANCE = 1e-6
_MAX_MONTHS = 1200


# ── 키 ────────────────────────────────────────────────────────────────────────

def _market_code(market: Any) -> Optional[str]:
    """캐시 키에 쓸 시장 코드. 모르는 시장이면 None (미국으로 바꾸지 않는다)."""
    from backend.services.markets import MARKETS

    code = str(market or "").strip().upper()
    return code if code in MARKETS else None


def _horizon_months(horizon_years: Any) -> Optional[int]:
    """투자 기간(년) → 캐시 키의 개월 수. 키로 쓸 수 없는 값이면 None.

    `horizon_years × 12` 가 가장 가까운 정수와 1e-6 개월 이내로 같고, 그 정수가
    1 이상 1200 이하일 때만 그 정수를 돌려준다.

    반올림해서 키를 만들지 않는 이유: 0.3년(3.6개월)을 4개월로 반올림하면 다른
    기간을 물은 프롬프트의 뷰를 4개월 요청에 내준다. 정수 개월이 아니면 캐시를
    쓰지 않는 편이 싸다 — 잃는 것은 AI 호출 한 번이다. 화면이 보내는 값
    (0.25·0.5·1·2년)은 이진 부동소수로 정확해 언제나 키가 된다.
    """
    if isinstance(horizon_years, bool):
        return None
    try:
        years = float(horizon_years)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(years):
        return None
    months = years * 12.0
    nearest = round(months)
    if abs(months - nearest) > _MONTH_TOLERANCE:
        return None
    if not 1 <= nearest <= _MAX_MONTHS:
        return None
    return int(nearest)


def _norm_ticker(ticker: Any) -> Optional[str]:
    """앞뒤 공백을 떼고 대문자. 문자열이 아니거나 비면 None."""
    if not isinstance(ticker, str):
        return None
    return ticker.strip().upper() or None


def _cache_key(market: str, months: int, ticker: str) -> str:
    return f"{_KEY_PREFIX}::{market}::{months}m::{ticker}"


# ── 뷰 검사 ───────────────────────────────────────────────────────────────────

def _clean_view(view: Any) -> tuple[Optional[dict], Optional[str]]:
    """내보내도 되는 뷰면 `(필수 키 넷만 담은 dict, None)`, 아니면 `(None, 이유)`.

    저장과 조회가 같은 검사를 쓴다. 필수 키 넷만 남기므로 호출자가 넘긴 다른 키
    (예: 캐시에서 꺼낸 뷰의 `cached_at`)는 저장되지 않는다.

    - `expected_return` · `confidence`: 숫자(불리언 제외)이고 유한해야 한다.
      `confidence` 는 Black-Litterman(Idzorek) 의 신뢰도라 0~1 이어야 한다.
    - `sentiment`: "Bullish" | "Neutral" | "Bearish".
    - `key_driver`: 비지 않은 문자열. **빈 문자열을 거부하는 이유** —
      `_generate_ai_views` 는 AI 응답에 없는 종목을 `expected_return=0.0 ·
      confidence=0.5 · "Neutral" · ""` 로 채운다. 숫자는 멀쩡해 보이지만 AI 가
      답하지 않은 자리다. 그걸 저장하면 "모름" 이 "보합 전망" 으로 위장한 채
      하루 동안 모든 사용자에게 나간다 (§1.3 (a)).
    """
    if not isinstance(view, dict):
        return None, f"뷰가 dict 가 아니다 ({type(view).__name__})"
    missing = [k for k in _REQUIRED_KEYS if k not in view]
    if missing:
        return None, f"필수 키 없음 {missing}"

    clean: dict = {}
    for k in ("expected_return", "confidence"):
        v = view[k]
        if isinstance(v, bool) or not isinstance(v, Real):
            return None, f"{k} 가 숫자가 아니다 ({v!r})"
        try:
            f = float(v)
        except (TypeError, ValueError, OverflowError):
            return None, f"{k} 를 float 로 바꿀 수 없다"
        if not math.isfinite(f):
            return None, f"{k} 가 유한하지 않다 ({f})"
        clean[k] = f
    if not 0.0 <= clean["confidence"] <= 1.0:
        return None, f"confidence 가 0~1 밖이다 ({clean['confidence']})"

    sentiment = view["sentiment"]
    if not isinstance(sentiment, str) or sentiment not in _SENTIMENTS:
        return None, f"sentiment 가 허용값이 아니다 ({sentiment!r})"
    clean["sentiment"] = sentiment

    driver = view["key_driver"]
    if not isinstance(driver, str) or not driver.strip():
        return None, "key_driver 가 비었다 (AI 가 답하지 않은 종목의 기본값 모양)"
    clean["key_driver"] = driver
    return clean, None


# ── 조회 ──────────────────────────────────────────────────────────────────────

def get_ai_views(market: str, horizon_years: float, tickers: list[str]) -> dict[str, dict]:
    """유효한(만료 전) 캐시 뷰만. 키는 대문자 티커. 각 뷰 dict 에 "cached_at"(ISO 문자열) 을 더해 돌려준다.

    반환: `{"AAPL": {"expected_return": float, "confidence": float,
    "sentiment": str, "key_driver": str, "cached_at": "2026-09-17T13:05:12.345678+00:00"}}`

    - 캐시에 없거나 만료된 종목은 **키가 빠진다.** 일부만 있으면 있는 것만 온다.
    - `cached_at` 은 그 뷰를 저장한 DB 시각(UTC)이다.
    - 종목 수와 무관하게 쿼리 한 번이다 (커넥션 체크아웃 1회).
    - 만료 판정은 DB 시각(`expires_at > NOW()`)이다. 인스턴스마다 시계가 조금씩
      달라도 모든 인스턴스가 같은 순간에 만료를 본다.

    실패는 `{}` + `logger.warning` 이다 — 캐시가 없으면 AI 를 새로 부르면 된다.
    반환값만으로는 "캐시 미스" 와 "조회 실패" 가 같다. 호출자의 다음 행동이
    같기 때문이고, 구별은 로그가 한다.
    """
    m = _market_code(market)
    months = _horizon_months(horizon_years)
    if m is None or months is None:
        logger.warning(
            "AI 뷰 캐시를 조회하지 않는다 — 키로 쓸 수 없는 입력 "
            "(market=%r, horizon_years=%r). 이 요청의 뷰는 전부 AI 를 새로 부른다.",
            market, horizon_years)
        return {}
    if isinstance(tickers, (str, bytes)) or not isinstance(tickers, Iterable):
        # 문자열을 그대로 돌면 글자 하나하나가 티커가 된다 ("A" 는 실제 종목이다).
        logger.warning("AI 뷰 캐시 조회: tickers 가 티커 목록이 아니다 (%r) — 조회하지 않는다",
                       tickers)
        return {}

    wanted: dict[str, str] = {}              # 캐시 키 → 대문자 티커
    for t in tickers:
        nt = _norm_ticker(t)
        if nt is None:
            logger.warning("AI 뷰 캐시 조회: 티커로 쓸 수 없는 값을 건너뛴다 (%r)", t)
            continue
        wanted[_cache_key(m, months, nt)] = nt
    if not wanted:
        return {}

    if not is_available():
        logger.warning(
            "AI 뷰 캐시를 조회하지 못했다 — DB 미연결. %s·%d개월 %d종목이 전부 AI 를 새로 부른다.",
            m, months, len(wanted))
        return {}

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT cache_type, data, updated_at
                         FROM common_cache
                        WHERE cache_type = ANY(%s)
                          AND expires_at > NOW()""",
                    (list(wanted),),
                )
                rows = cur.fetchall()
    except Exception as e:
        logger.warning(
            "AI 뷰 캐시 조회 실패 (%s·%d개월, %d종목) — 캐시 없이 진행해 AI 를 새로 부른다: %s",
            m, months, len(wanted), e)
        return {}

    views: dict[str, dict] = {}
    unusable: list[str] = []
    for cache_type, data, updated_at in rows:
        ticker = wanted.get(cache_type)
        if ticker is None:
            continue
        view, why = _clean_view(data)
        if view is None or updated_at is None:
            unusable.append(f"{ticker}: {why or 'updated_at 없음'}")
            continue
        view["cached_at"] = updated_at.astimezone(timezone.utc).isoformat()
        views[ticker] = view
    if unusable:
        logger.warning(
            "AI 뷰 캐시에 내보낼 수 없는 행 %d건 (%s·%d개월) — 없는 것으로 보고 AI 를 새로 부른다: %s",
            len(unusable), m, months, "; ".join(unusable))
    return views


# ── 저장 ──────────────────────────────────────────────────────────────────────

def save_ai_views(market: str, horizon_years: float, views: dict[str, dict]) -> int:
    """뷰를 저장하고 실제로 쓴 행 수를 돌려준다. 만료 = 다음 '장 마감 + 30분'.

    - 만료 시각은 저장 시점의 `next_close_reset(market)` 이다.
    - **같은 주기의 유효한 뷰가 이미 있으면 덮지 않고 세지도 않는다.** 두 사용자가
      동시에 미스를 내 같은 종목을 각자 AI 에 물으면 먼저 저장한 뷰가 그 주기의
      뷰로 남는다 — 하루 동안 모든 사용자가 같은 뷰를 본다. 주기가 바뀐 뒤의
      저장은 만료 시각이 더 늦으므로 덮는다. 캐시에서 꺼낸 뷰를 다시 넘겨도
      `cached_at` 이 새로 찍히지 않는다.
    - 깨진 뷰는 그 종목만 빼고 경고한다 (`_clean_view`). 나머지는 저장한다.
    - 쓰기는 한 문장이다. 키 순서로 정렬해 넣어, 겹치는 종목을 동시에 저장하는
      두 요청이 서로 반대 순서로 행을 잠가 교착되지 않게 한다. `ON CONFLICT DO
      UPDATE` 는 WHERE 가 거짓이어도 충돌 행을 잠근다. 실측(스레드 8개가 같은
      60종목을 저장, 절반은 역순): 같은 문장을 정렬 없이 보내면 90초 동안 113건 중
      99건이 `deadlock detected`, 이 함수로는 320건 중 0건이었다.

    실패는 `0` + `logger.warning` 이다. 반환값이 넘긴 개수보다 작으면 거부됐거나
    (경고가 남는다) 이미 이번 주기 뷰가 있었던 것이다 (debug 로그).
    """
    m = _market_code(market)
    months = _horizon_months(horizon_years)
    if m is None or months is None:
        logger.warning(
            "AI 뷰 캐시에 저장하지 않는다 — 키로 쓸 수 없는 입력 "
            "(market=%r, horizon_years=%r). 이 뷰는 다른 사용자에게 공유되지 않는다.",
            market, horizon_years)
        return 0
    if not isinstance(views, dict):
        logger.warning("AI 뷰 캐시 저장: views 가 dict 가 아니다 (%s) — 저장하지 않는다",
                       type(views).__name__)
        return 0
    if not views:
        return 0

    clean: dict[str, dict] = {}
    rejected: list[str] = []
    conflicted: set[str] = set()
    for raw_ticker, raw_view in views.items():
        ticker = _norm_ticker(raw_ticker)
        if ticker is None:
            rejected.append(f"{raw_ticker!r}: 티커로 쓸 수 없는 값")
            continue
        view, why = _clean_view(raw_view)
        if view is None:
            rejected.append(f"{ticker}: {why}")
            continue
        if ticker in clean and clean[ticker] != view:
            conflicted.add(ticker)      # 'aapl' 과 'AAPL' 에 서로 다른 뷰 — 고르지 않는다
        clean[ticker] = view
    for ticker in sorted(conflicted):
        del clean[ticker]
        rejected.append(f"{ticker}: 대소문자만 다른 키에 서로 다른 뷰가 왔다")
    if rejected:
        logger.warning(
            "AI 뷰 캐시 저장 거부 %d건 (%s·%d개월) — 캐시는 모든 사용자에게 나가므로 "
            "깨진 뷰는 넣지 않는다: %s",
            len(rejected), m, months, "; ".join(rejected))
    if not clean:
        return 0

    if not is_available():
        logger.warning(
            "AI 뷰 캐시에 저장하지 못했다 — DB 미연결 (%s·%d개월, %d종목). "
            "다음 요청도 AI 를 새로 부른다.",
            m, months, len(clean))
        return 0

    try:
        from psycopg2.extras import execute_values
        from backend.services.market_calendar import next_close_reset

        expires_at = next_close_reset(m)
        rows = sorted(
            (_cache_key(m, months, t), json.dumps(v, ensure_ascii=False, allow_nan=False), expires_at)
            for t, v in clean.items()
        )
        with get_conn() as conn:
            with conn.cursor() as cur:
                written = execute_values(
                    cur,
                    """INSERT INTO common_cache (cache_type, data, updated_at, expires_at)
                       VALUES %s
                       ON CONFLICT (cache_type) DO UPDATE
                          SET data       = EXCLUDED.data,
                              updated_at = NOW(),
                              expires_at = EXCLUDED.expires_at
                        WHERE common_cache.expires_at IS NULL
                           OR common_cache.expires_at < EXCLUDED.expires_at
                       RETURNING cache_type""",
                    rows,
                    template="(%s, %s::jsonb, NOW(), %s)",
                    page_size=len(rows),
                    fetch=True,
                )
    except Exception as e:
        logger.warning(
            "AI 뷰 캐시 저장 실패 (%s·%d개월, %d종목) — 이번 뷰는 공유되지 않고 "
            "다음 요청도 AI 를 새로 부른다: %s",
            m, months, len(clean), e)
        return 0

    count = len(written)
    if count < len(rows):
        logger.debug(
            "AI 뷰 캐시: %s·%d개월 %d종목 중 %d종목은 이번 주기 뷰가 이미 있어 그대로 뒀다",
            m, months, len(rows), len(rows) - count)
    return count
