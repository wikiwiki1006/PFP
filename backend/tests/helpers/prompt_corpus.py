"""
backend/tests/helpers/prompt_corpus.py
──────────────────────────────────────
프롬프트 빌더들이 실제로 만들어 내는 **문자열**을 한자리에 모은다.

규칙 검사가 빌더 하나만 보면 의미가 없다. 오늘까지 나온 프롬프트 결함이 전부
"한 빌더는 규칙을 알고 사본은 모르는" 형태였다 — 통화 표기, 거시지표 시장 분기,
금액 계산, 도구 없는 웹서치 지시. 그래서 **빌더 전부**를 같은 규칙에 건다.

빌더는 넷이다 (`report_writer` 는 프롬프트 변형이 여럿이라 각각 담는다):

    ai_analysis.py          _format_portfolio · build_macro_block
    daily_report.py         _build_prompt
    report_writer.py        _equity_prompt(+part1/part2) · _industry_prompt
    portfolio_optimizer.py  _build_ticker_section

**네 번째는 어느 목록에도 없었다.** 프롬프트를 만드는 곳은 `report_*`·`ai_*`
라는 이름 안에만 있지 않다. 새 빌더가 생기면 여기에 추가한다 — 여기 없는
빌더는 어떤 규칙도 받지 않는다.

전부 네트워크를 타지 않는 순수 함수다. 거시지표만 바깥을 보므로 값을 주입한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest import mock

KR_HOLDINGS = {
    "005930.KS": {"q": 10, "avg": 71900, "sector": "Tech"},
    "CASH": {"q": 1_234_567},
}
US_HOLDINGS = {
    "AAPL": {"q": 10, "avg": 231.45, "sector": "Tech"},
    "CASH": {"q": 5000.25},
}


@dataclass(frozen=True)
class Prompt:
    builder: str          # "모듈.함수"
    market: str           # "KR" | "US"
    text: str

    @property
    def id(self) -> str:
        return f"{self.builder}[{self.market}]"


def _macro_prompts() -> list[Prompt]:
    from backend.services import ai_analysis

    kr_block = (
        "[한국 거시지표]\n  한국은행 기준금리: 2.50%\n  국고채 3년: 2.61%\n  CPI YoY: 2.0%"
    )
    with mock.patch("backend.services.korea_macro.get_korea_macro", return_value={}), \
         mock.patch("backend.services.korea_macro.format_for_prompt", return_value=kr_block):
        kr = ai_analysis.build_macro_block("KR")

    with mock.patch("backend.services.market_data.get_fred_macro", return_value={
        "source": "fred", "fed_rate": 4.25, "unemployment": 4.1,
        "cpi": 2.9, "gdp": 2.1, "t10y2y": 0.35, "bamlh0a0hym2": 310,
    }):
        us = ai_analysis.build_macro_block("US")

    return [Prompt("ai_analysis.build_macro_block", "KR", kr),
            Prompt("ai_analysis.build_macro_block", "US", us)]


def _daily_prompts() -> list[Prompt]:
    from backend.services import daily_report

    kr_prices = {
        "005930.KS": {"close": 71900, "chg_pct": 1.2, "day_pnl": 10239,
                      "pos_val": 719_000, "sector": "Tech"},
        "__KOSPI":  {"close": 2600, "chg_pct": 0.4},
        "__KOSDAQ": {"close": 780, "chg_pct": -0.2},
        "__USDKRW": {"close": 1380, "chg_pct": 0.1},
    }
    us_prices = {
        "AAPL":   {"close": 231.45, "chg_pct": 1.2, "day_pnl": 25.5,
                   "pos_val": 2314.5, "sector": "Tech"},
        "__SPY":  {"close": 560.0, "chg_pct": 0.3},
        "__VIX":  {"close": 18.0, "chg_pct": -1.0},
        "__TNX":  {"close": 4.2, "chg_pct": 0.5},
    }
    news = {"005930.KS": [], "AAPL": []}
    return [
        Prompt("daily_report._build_prompt", "KR",
               daily_report._build_prompt(KR_HOLDINGS, kr_prices, news, "KR")),
        Prompt("daily_report._build_prompt", "US",
               daily_report._build_prompt(US_HOLDINGS, us_prices, news, "US")),
    ]


def _report_prompts() -> list[Prompt]:
    from backend.services import report_writer

    out: list[Prompt] = []
    for name, fn in (
        ("_equity_prompt", report_writer._equity_prompt),
        ("_equity_prompt_part1", report_writer._equity_prompt_part1),
        ("_equity_prompt_part2", report_writer._equity_prompt_part2),
    ):
        out.append(Prompt(f"report_writer.{name}", "KR", fn("005930.KS", "삼성전자", "KR")))
        out.append(Prompt(f"report_writer.{name}", "US", fn("AAPL", "Apple", "US")))

    for market in ("KR", "US"):
        industries = report_writer.industries_for(market)
        meta = industries[next(iter(industries))]
        out.append(Prompt("report_writer._industry_prompt", market,
                          report_writer._industry_prompt(meta, market)))
    return out


def _optimizer_prompts() -> list[Prompt]:
    """`_build_ticker_section` 은 **market 인자를 받지 않는다.**

    시장을 모르니 통화도 모른다 — 그래서 한국 종목이 들어와도 달러로 적힌다.
    여기서는 티커로 시장을 나눠 담는다. 값이 채워진 경우와 비어 있는 경우를
    둘 다 넣는 이유는, 없는 값을 `'?'` 로 적어 넣는 문제가 후자에서만 보이기
    때문이다.
    """
    from backend.services.portfolio_optimizer import _build_ticker_section

    kr_fund = {"market_cap_b": 500_000.0, "sector": "Technology",
               "industry": "Semiconductors", "free_cashflow_b": 12_345.6, "roe": 9.1}
    us_fund = {"market_cap_b": 3_400.0, "sector": "Technology",
               "industry": "Consumer Electronics", "free_cashflow_b": 108.8, "roe": 147.0}
    return [
        Prompt("portfolio_optimizer._build_ticker_section", "KR",
               _build_ticker_section("005930.KS", {}, kr_fund)),
        Prompt("portfolio_optimizer._build_ticker_section", "US",
               _build_ticker_section("AAPL", {}, us_fund)),
        Prompt("portfolio_optimizer._build_ticker_section(빈값)", "KR",
               _build_ticker_section("005930.KS", {}, {})),
        Prompt("portfolio_optimizer._build_ticker_section(빈값)", "US",
               _build_ticker_section("AAPL", {}, {})),
    ]


def build_corpus() -> list[Prompt]:
    """빌더 전부의 프롬프트. 새 빌더가 생기면 여기에 더한다."""
    from backend.services import ai_analysis

    out = [
        Prompt("ai_analysis._format_portfolio", "KR",
               ai_analysis._format_portfolio(KR_HOLDINGS, "KR")),
        Prompt("ai_analysis._format_portfolio", "US",
               ai_analysis._format_portfolio(US_HOLDINGS, "US")),
    ]
    out += _macro_prompts()
    out += _daily_prompts()
    out += _report_prompts()
    out += _optimizer_prompts()
    return out
