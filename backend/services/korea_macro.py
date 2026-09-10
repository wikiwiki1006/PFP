"""
services/korea_macro.py
───────────────────────
한국 거시지표 수집.

미국은 FRED 하나로 끝나지만 한국은 출처를 섞어야 한다.

  · 기준금리·소비자물가·국고채  → 한국은행 ECOS (최신치가 당월/전일까지 온다)
  · 실업률                    → FRED (ECOS 는 고용 통계 항목 코드가 복잡하고,
                                FRED 시리즈가 같은 값을 더 단순하게 준다)
  · 환율                      → yfinance USDKRW=X (실시간에 가깝다)

한 지표가 실패해도 나머지는 살린다. 거시지표는 리포트의 배경 설명이라,
일부가 비었다고 리포트 생성을 막을 이유가 없다. 대신 **빈 값을 지어내지는
않는다** — None 으로 두고 화면·프롬프트에서 생략한다.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

import requests
from dotenv import load_dotenv
from pathlib import Path

logger = logging.getLogger(__name__)

# 다른 서비스 모듈과 같은 방식으로 .env 를 직접 읽는다. 웹 서버가 아닌 경로
# (스크립트·스케줄러)에서 임포트될 때도 키가 필요하기 때문이다.
load_dotenv(Path(__file__).parent.parent / ".env")

ECOS_KEY = os.getenv("KOREA_BANK_API_KEY", "").strip()
_ECOS = "https://ecos.bok.or.kr/api/StatisticSearch"

# ECOS 통계표·항목 코드. 실제 응답을 확인해 고정한 값이다.
_BASE_RATE = ("722Y001", "M", "0101000")   # 한국은행 기준금리
_CPI       = ("901Y009", "M", "0")         # 소비자물가지수 총지수
_Y3        = ("817Y002", "D", "010200000") # 국고채 3년
_Y10       = ("817Y002", "D", "010210000") # 국고채 10년


def _ecos_series(stat: str, cycle: str, item: str, months_back: int = 26) -> list[tuple[str, float]]:
    """ECOS 시계열 조회 → [(시점, 값)] 오름차순. 실패하면 빈 리스트."""
    if not ECOS_KEY:
        return []
    now = datetime.now()
    if cycle == "D":
        start = (now - timedelta(days=90)).strftime("%Y%m%d")
        end   = now.strftime("%Y%m%d")
    else:
        start = (now - timedelta(days=31 * months_back)).strftime("%Y%m")
        end   = now.strftime("%Y%m")

    url = f"{_ECOS}/{ECOS_KEY}/json/kr/1/200/{stat}/{cycle}/{start}/{end}/{item}"
    try:
        data = requests.get(url, timeout=15).json()
    except Exception as e:
        logger.warning(f"ECOS 조회 실패 {stat}/{item}: {e}")
        return []

    rows = (data.get("StatisticSearch") or {}).get("row") or []
    out: list[tuple[str, float]] = []
    for r in rows:
        try:
            out.append((r["TIME"], float(r["DATA_VALUE"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _last(series: list[tuple[str, float]]) -> Optional[float]:
    return series[-1][1] if series else None


def _yoy_percent(series: list[tuple[str, float]]) -> Optional[float]:
    """지수 시계열의 전년 동월 대비 상승률(%). 13개월치가 있어야 계산된다."""
    if len(series) < 13:
        return None
    now, year_ago = series[-1][1], series[-13][1]
    if year_ago <= 0:
        return None
    return round((now / year_ago - 1) * 100, 2)


def _fred_unemployment() -> Optional[float]:
    """한국 실업률. ECOS 대신 FRED 를 쓰는 이유는 모듈 상단 설명 참고."""
    try:
        import pandas_datareader.data as web
        df = web.DataReader("LRHUTTTTKRM156S", "fred",
                            datetime.now() - timedelta(days=400)).dropna()
        return round(float(df.iloc[-1, 0]), 2) if len(df) else None
    except Exception as e:
        logger.warning(f"FRED 한국 실업률 조회 실패: {e}")
        return None


def _usdkrw() -> Optional[float]:
    """원/달러 환율. 월평균이 아니라 최근 종가라 리포트에 쓰기 적합하다."""
    try:
        import yfinance as yf
        h = yf.Ticker("USDKRW=X").history(period="5d")
        return round(float(h["Close"].iloc[-1]), 2) if len(h) else None
    except Exception as e:
        logger.warning(f"USDKRW 조회 실패: {e}")
        return None


def get_korea_macro(ttl: int = 3600) -> dict[str, Any]:
    """한국 거시지표 묶음. market_data 의 캐시를 그대로 쓴다."""
    from backend.services.market_data import _cached

    def _fetch() -> dict[str, Any]:
        cpi_series = _ecos_series(*_CPI)
        y3  = _last(_ecos_series(*_Y3))
        y10 = _last(_ecos_series(*_Y10))

        out: dict[str, Any] = {
            "base_rate":    _last(_ecos_series(*_BASE_RATE)),
            "cpi_yoy":      _yoy_percent(cpi_series),
            "cpi_index":    _last(cpi_series),
            "y3":           y3,
            "y10":          y10,
            # 장단기 금리차 — 경기 판단에 자주 쓰이므로 미리 계산해 둔다.
            "spread_10y3y": round(y10 - y3, 3) if (y10 is not None and y3 is not None) else None,
            "unemployment": _fred_unemployment(),
            "usdkrw":       _usdkrw(),
        }
        got = sum(1 for v in out.values() if v is not None)
        out["source"] = "ECOS+FRED" if got else "unavailable"
        return out

    return _cached("korea_macro", ttl, _fetch)


def format_for_prompt(macro: dict[str, Any]) -> str:
    """LLM 프롬프트에 넣을 문자열. 값이 없는 항목은 아예 넣지 않는다 —
    'N/A' 를 넣으면 모델이 그걸 수치처럼 인용하는 경우가 있다."""
    rows = [
        ("한국은행 기준금리", macro.get("base_rate"),    "%"),
        ("소비자물가 상승률(전년비)", macro.get("cpi_yoy"), "%"),
        ("실업률",           macro.get("unemployment"), "%"),
        ("국고채 3년",       macro.get("y3"),           "%"),
        ("국고채 10년",      macro.get("y10"),          "%"),
        ("장단기 금리차(10y-3y)", macro.get("spread_10y3y"), "%p"),
        ("원/달러 환율",     macro.get("usdkrw"),       "원"),
    ]
    lines = [f"  {label}: {value}{unit}" for label, value, unit in rows if value is not None]
    return "[한국 거시지표]\n" + "\n".join(lines) if lines else ""
