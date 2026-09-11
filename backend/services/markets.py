"""
services/markets.py
───────────────────
시장별(미국/한국) 설정을 한 곳에 모은다.

미국과 한국을 완전히 분리해 다루기로 했다. 분리 자체는 DB 의 market 컬럼이
담당하고, 이 모듈은 "그 시장이 무엇을 쓰는가"를 정의한다 — 지수, 통화,
거래 캘린더, 섹터 대표 ETF, 거시지표 시리즈.

값을 코드 곳곳에 흩어 두지 않고 여기 모으는 이유는, 시장을 하나 더 붙일 때
고칠 곳이 한 군데로 끝나게 하기 위해서다. 지금은 US·KR 둘뿐이지만 구조는
같다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

MarketCode = Literal["US", "KR"]
DEFAULT_MARKET: MarketCode = "US"


@dataclass(frozen=True)
class MarketSpec:
    code: str
    label: str
    currency: str
    currency_symbol: str
    # 시세 조회용 대표 지수 (첫 항목이 그 시장의 '기준 지수')
    indices: dict[str, str]
    # 섹터 등락률을 대표하는 ETF. 미국은 SPDR 섹터, 한국은 KODEX 업종.
    sector_etfs: list[tuple[str, str]]
    # FRED 거시지표 시리즈 — 키가 필요 없고 미국·한국 모두 제공된다.
    fred_series: dict[str, str]
    # 티커가 이 시장에 속하는지 판별할 접미사. 미국은 접미사가 없어 빈 튜플.
    suffixes: tuple[str, ...] = ()
    # 화면·리포트에 쓰는 자연어 (LLM 프롬프트에도 들어간다)
    lang_note: str = ""
    extra: dict = field(default_factory=dict)


US = MarketSpec(
    code="US",
    label="미국",
    currency="USD",
    currency_symbol="$",
    indices={
        "^GSPC": "S&P 500",
        "^IXIC": "NASDAQ",
        "^DJI":  "다우존스",
        "^VIX":  "VIX",
        "^TNX":  "미 10년물",
    },
    sector_etfs=[
        ("Technology", "XLK"), ("Financials", "XLF"), ("Health Care", "XLV"),
        ("Consumer Discretionary", "XLY"), ("Communication Services", "XLC"),
        ("Industrials", "XLI"), ("Consumer Staples", "XLP"), ("Energy", "XLE"),
        ("Utilities", "XLU"), ("Real Estate", "XLRE"), ("Materials", "XLB"),
    ],
    fred_series={
        "policy_rate":  "FEDFUNDS",
        "unemployment": "UNRATE",
        "y10":          "DGS10",
        "y2":           "DGS2",
        "cpi_index":    "CPIAUCSL",
    },
    lang_note="미국 증시(NYSE·NASDAQ), 통화 USD",
)

KR = MarketSpec(
    code="KR",
    label="한국",
    currency="KRW",
    currency_symbol="₩",
    indices={
        "^KS11":    "코스피",
        "^KQ11":    "코스닥",
        "USDKRW=X": "원/달러",
        "^KS200":   "코스피200",
    },
    # 한국에는 미국 SPDR 같은 표준 11 섹터 ETF 세트가 없다. 거래대금이 충분한
    # KODEX·TIGER 업종 ETF 로 대체하되, 대응되지 않는 섹터는 비워 둔다 —
    # 없는 것을 억지로 끼워 맞추면 섹터 등락률이 사실과 달라진다.
    # 섹터 키는 미국(GICS_SECTOR_ETFS)과 **같은 형식**이어야 한다.
    # 프론트의 한글 라벨 표가 이 키로 찾는다 — 'Technology' 처럼 다른 표기를
    # 쓰면 표에서 못 찾아 영어 원문이 그대로 화면에 나온다.
    sector_etfs=[
        ("TECHNOLOGY",        "091160.KS"),   # KODEX 반도체
        ("FINANCIALS",        "091170.KS"),   # KODEX 은행
        ("CONSUMER_DISC",     "266390.KS"),   # KODEX 경기소비재
        ("HEALTHCARE",        "266420.KS"),   # KODEX 헬스케어
        # 아래 두 개는 실물을 확인해 이름에 맞게 바로잡았다.
        # 102960 은 '기계장비'가 아니라 조선(Shipbuilding),
        # 266370 은 '미디어&엔터'가 아니라 IT하드웨어다.
        ("SHIPBUILDING",      "102960.KS"),   # KODEX 조선
        ("CONSUMER_STAPLES",  "266410.KS"),   # KODEX 필수소비재
        ("ENERGY_CHEM",       "117460.KS"),   # KODEX 에너지화학
        ("STEEL",             "117680.KS"),   # KODEX 철강
        ("IT_HARDWARE",       "266370.KS"),   # KODEX IT하드웨어
    ],
    # FRED 에 한국 시리즈가 그대로 있어 한국은행 ECOS 키 없이 쓸 수 있다.
    # CPI 는 FRED 커버리지가 약해(최신치가 오래됨) 넣지 않는다 — 없는 값을
    # 넣어 두면 리포트가 낡은 수치를 사실처럼 인용한다.
    fred_series={
        "policy_rate":  "IR3TIB01KRM156N",   # 3개월 시장금리
        "unemployment": "LRHUTTTTKRM156S",   # 실업률
        "y10":          "IRLTLT01KRM156N",   # 장기국채 수익률
        "fx":           "EXKOUS",            # 원/달러(월평균)
    },
    suffixes=(".KS", ".KQ"),
    lang_note="한국 증시(KOSPI·KOSDAQ), 통화 KRW",
)

MARKETS: dict[str, MarketSpec] = {"US": US, "KR": KR}


def get_market(code: str | None) -> MarketSpec:
    """시장 코드 → 설정. 모르는 값이면 기본 시장(미국)."""
    return MARKETS.get((code or "").upper(), MARKETS[DEFAULT_MARKET])


def benchmark_for(market: str | None) -> str:
    """그 시장의 기준 지수 티커. 베타·상대수익률의 비교 대상이다.

    `indices` 의 첫 항목이라는 것을 아는 코드는 여기 하나여야 한다. 예전에는
    `calculate_portfolio_beta` 가 `^GSPC` 를 기본값으로 들고 있어서, 한국
    포트폴리오의 베타가 "한국 주식이 S&P 를 얼마나 안 따라가는가" 를
    쟀다 — 계산은 맞고 질문이 틀렸는데 맞는 답처럼 보였다.

    호출부마다 `next(iter(get_market(m).indices))` 를 쓰면 그 지식이 사본으로
    퍼진다. 이 리포는 같은 규칙이 여러 곳에 구현돼 한쪽만 고쳐지는 사고를
    반복해서 냈다 (§1.4 통화 표기가 그 예다).
    """
    return next(iter(get_market(market).indices))


def normalize(code: str | None) -> str:
    """저장·조회에 쓸 정규화된 시장 코드."""
    return get_market(code).code


def market_of_ticker(ticker: str) -> str:
    """티커가 어느 시장 종목인지. 접미사로 판별한다.

    한국 종목은 `005930.KS` 처럼 접미사가 붙고 미국 종목은 붙지 않는다.
    사용자가 시장을 고른 뒤 티커를 넣으므로 보통은 이 판별이 필요 없지만,
    잘못된 시장에 저장되는 것을 막는 마지막 확인용으로 둔다.
    """
    t = (ticker or "").upper().strip()
    for spec in MARKETS.values():
        if spec.suffixes and t.endswith(spec.suffixes):
            return spec.code
        # 지수·환율에는 접미사가 없다(^KS11, USDKRW=X). 시장이 자기 지수로
        # 등록해 둔 심볼이면 그 시장 것으로 본다.
        if t in {k.upper() for k in spec.indices} and spec.code != DEFAULT_MARKET:
            return spec.code
    return DEFAULT_MARKET


def belongs_to(ticker: str, market: str) -> bool:
    """그 티커를 해당 시장에 담아도 되는지."""
    return market_of_ticker(ticker) == normalize(market)


# ── FastAPI 의존성 ───────────────────────────────────────────────────────────

def market_param(market: str = "US") -> str:
    """요청의 시장 코드를 정규화해 돌려주는 의존성.

    쿼리스트링 `?market=KR` 로 받는다. 값이 없거나 이상하면 미국이다 —
    시장을 보내지 않는 옛 프론트도 기존과 똑같이 동작해야 하기 때문이다.
    라우터마다 검증을 되풀이하지 않도록 한 곳에 모았다.
    """
    return normalize(market)


def universe_lookup(ticker: str, market: str | None = None) -> Optional[dict]:
    """시장에 맞는 상장 목록에서 티커를 찾는다. 없으면 None.

    미국과 한국은 상장 목록을 서로 다른 곳에 담고 있다. 예전에는 미국 목록만
    조회해서 '005930.KS' 를 찾지 못했고, 시세 조회 전 존재 확인에서 걸러
    한국 종목 현재가가 전부 404 였다.

    market 을 주지 않으면 티커 모양(.KS/.KQ 접미사)으로 판단한다.
    """
    m = normalize(market) if market else market_of_ticker(ticker)
    if m == "KR":
        from backend.services.korea_universe import lookup as _kr_lookup
        return _kr_lookup(ticker)
    from backend.services.ticker_universe import lookup as _us_lookup
    return _us_lookup(ticker)


def display_name(ticker: str, market: str | None = None) -> str:
    """화면에 보여줄 종목 이름. 못 찾으면 티커 그대로.

    한국 종목은 '034020.KS' 처럼 숫자 코드라 사람이 보고 어느 회사인지 알 수 없다.
    미국은 티커가 곧 이름 역할을 하지만(AAPL), 한국은 그렇지 않아 이름을 함께
    보여줘야 한다.
    """
    row = universe_lookup(ticker, market)
    name = (row or {}).get("name") or ""
    return name.strip() or ticker


def name_map_for(tickers, market: str | None = None) -> dict[str, str]:
    """티커 목록 → 이름 사전. 한 번에 조회해 목록 화면에서 N 번 왕복하지 않는다."""
    out: dict[str, str] = {}
    kr = [t for t in tickers if market_of_ticker(t) == "KR"]
    us = [t for t in tickers if t not in kr]

    if kr:
        try:
            from backend.services.korea_universe import name_map as _kr_names
            names = _kr_names()
            for t in kr:
                n = names.get(t) or names.get(t.upper())
                if n:
                    out[t] = n
        except Exception:
            pass
        # 이름 사전에 없으면 상장 목록에서 하나씩 채운다.
        for t in kr:
            if t not in out:
                out[t] = display_name(t, "KR")

    for t in us:
        out[t] = display_name(t, "US")
    return out
