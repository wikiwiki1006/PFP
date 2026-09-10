"""
backend/tests/helpers/prompt_corpus.py
──────────────────────────────────────
프롬프트 빌더들이 실제로 만들어 내는 **문자열**을 한자리에 모은다.

규칙 검사가 빌더 하나만 보면 의미가 없다. 오늘까지 나온 프롬프트 결함이 전부
"한 빌더는 규칙을 알고 사본은 모르는" 형태였다 — 통화 표기, 거시지표 시장 분기,
금액 계산, 도구 없는 웹서치 지시. 그래서 **빌더 전부**를 같은 규칙에 건다.

빌더 목록 (프롬프트 변형이 여럿인 것은 각각 담는다):

    ai_analysis.py          _format_portfolio · build_macro_block
                            _build_agents (에이전트 9개) · generate_daily_brief
    daily_report.py         _build_prompt
    report_writer.py        _equity_prompt(+part1/part2) · _industry_prompt
    portfolio_optimizer.py  _build_ticker_section

**빌더를 셀 때 사본을 세면 안 된다.** 목록이 처음엔 셋이었고,
`portfolio_optimizer._build_ticker_section` 은 어디에도 없었다 — 그리고 거기에
§1.4 사고가 그대로 살아 있었다. 사본을 세면 "내가 아는 것과 같은 코드" 만
세게 되고, 모르는 자리는 구조적으로 안 세어진다.

기준은 코드 모양이 아니라 역할이다: **숫자나 사실을 문자열로 만들어 모델에게
넘기는 자리는 전부 빌더다.** 여기 없는 빌더는 어떤 규칙도 받지 않는다.

대부분 순수 함수다. 바깥을 보는 둘만 주입한다 — 거시지표 수집, 그리고
`generate_daily_brief` 의 모델 호출(그 반환을 가로채 프롬프트를 얻는다).
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
    """`_build_ticker_section` 은 `market` 을 **필수 인자로** 받는다.

    예전에는 아예 받지 않아서, 시장을 모르니 통화도 몰랐고 한국 종목이 들어와도
    달러로 적혔다. 기본값을 두지 않는 이유는 빠뜨린 호출부가 조용히 달러가 되지
    않게 하려는 것이다 — 빠뜨리면 여기서 TypeError 가 난다.

    값이 채워진 경우와 비어 있는 경우를 둘 다 넣는 이유는, 없는 값을 `'?'` 로
    적어 넣는 문제가 후자에서만 보이기 때문이다.
    """
    from backend.services.portfolio_optimizer import _build_ticker_section

    kr_fund = {"market_cap_b": 500_000.0, "sector": "Technology",
               "industry": "Semiconductors", "free_cashflow_b": 12_345.6, "roe": 9.1}
    us_fund = {"market_cap_b": 3_400.0, "sector": "Technology",
               "industry": "Consumer Electronics", "free_cashflow_b": 108.8, "roe": 147.0}
    return [
        Prompt("portfolio_optimizer._build_ticker_section", "KR",
               _build_ticker_section("005930.KS", {}, kr_fund, "KR")),
        Prompt("portfolio_optimizer._build_ticker_section", "US",
               _build_ticker_section("AAPL", {}, us_fund, "US")),
        Prompt("portfolio_optimizer._build_ticker_section(빈값)", "KR",
               _build_ticker_section("005930.KS", {}, {}, "KR")),
        Prompt("portfolio_optimizer._build_ticker_section(빈값)", "US",
               _build_ticker_section("AAPL", {}, {}, "US")),
    ]


def _agent_prompts() -> list[Prompt]:
    """`_build_agents` 는 에이전트마다 프롬프트를 하나씩 만든다.

    합쳐서 한 항목으로 담지 않는다 — 합치면 어느 에이전트가 규칙을 어겼는지
    실패 메시지에서 안 보인다. 아홉 중 하나만 시장 분기를 빠뜨리는 것이 바로
    이 리포에서 반복된 형태다.
    """
    from backend.services import ai_analysis

    out: list[Prompt] = []
    for market, holdings in (("KR", KR_HOLDINGS), ("US", US_HOLDINGS)):
        portfolio_str = ai_analysis._format_portfolio(holdings, market)
        for agent in ai_analysis._build_agents("관세 인상", portfolio_str, market=market):
            out.append(Prompt(
                f"ai_analysis._build_agents#{agent['id']}({agent['label']})",
                market, agent["prompt"],
            ))
    return out


def _daily_brief_prompt() -> list[Prompt]:
    """`generate_daily_brief` 는 프롬프트를 만들자마자 모델에 넘긴다.

    마지막 줄이 `return call_claude(prompt, ...)` 라, 그 함수를 가로채면
    프롬프트 문자열을 그대로 받을 수 있다. 별도 진입점을 만들지 않고 실제
    경로가 만드는 문자열을 그대로 잰다.
    """
    from backend.services import ai_analysis

    kr_prices = {"005930.KS": {"price": 71900, "chg_pct": 1.2, "pnl_pct": 0.0,
                               "pos_val": 719_000, "day_pnl": 10_239}}
    us_prices = {"AAPL": {"price": 231.45, "chg_pct": 1.2, "pnl_pct": 0.0,
                          "pos_val": 2314.5, "day_pnl": 25.5}}
    news = [{"ticker": "AAPL", "title": "테스트 헤드라인"}]

    out = []
    for market, holdings, prices, macro in (
        ("KR", KR_HOLDINGS, kr_prices, "[한국 거시지표]\n  한국은행 기준금리: 2.50%"),
        ("US", US_HOLDINGS, us_prices, "[US macro indicators]\n  Fed funds: 4.25%"),
    ):
        with mock.patch.object(ai_analysis, "call_claude", lambda prompt, *a, **kw: prompt), \
             mock.patch.object(ai_analysis, "build_macro_block", lambda m, _b=macro: _b):
            text = ai_analysis.generate_daily_brief(holdings, prices, news, market)
        out.append(Prompt("ai_analysis.generate_daily_brief", market, text))
    return out


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
    out += _agent_prompts()
    out += _daily_brief_prompt()
    out += _daily_prompts()
    out += _report_prompts()
    out += _optimizer_prompts()
    return out
