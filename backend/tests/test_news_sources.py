"""뉴스 수집은 시장별 출처를 따라야 한다.

관점은 프롬프트가 아니라 출처에서 갈린다. 로이터·CNBC 기사만 모아 놓고
"한국 관점으로 쓰라"고 지시해도, 모델이 가진 근거가 미국뿐이면 한국 이야기가
나올 수 없다. 뉴스를 긁는 지점이 네 군데(매크로·종목·산업·데일리)로 흩어져
있어 한 곳만 고치면 나머지가 조용히 미국 기사를 계속 쓴다.
"""
import pytest

from backend.services import ai_analysis as ai
from backend.services import daily_report as dr
from backend.services import report_writer as rw
from backend.services.news_sources import (
    KR_NEWS_DOMAINS, focus_block, perplexity_extra, search_domains,
)


def test_korean_search_is_restricted_to_domestic_press():
    assert search_domains("KR") == KR_NEWS_DOMAINS
    assert "hankyung.com" in search_domains("KR")
    assert perplexity_extra("KR")["search_domain_filter"] == KR_NEWS_DOMAINS


def test_us_search_is_not_restricted():
    """미국은 좁히지 않는다 — 좁히면 오히려 재료가 줄어든다."""
    assert search_domains("US") == []
    assert perplexity_extra("US") == {}


def test_domain_filter_stays_within_provider_limit():
    """Perplexity 도메인 필터는 10개까지만 받는다."""
    assert len(KR_NEWS_DOMAINS) <= 10


def test_focus_block_switches_perspective():
    kr = focus_block("KR", "삼성전자")
    us = focus_block("US")
    assert "SOUTH KOREA" in kr and "KOSPI" in kr
    assert "UNITED STATES" in us
    assert "KRW" in kr and "Do not convert to USD" in kr


@pytest.mark.parametrize("fn_name, call", [
    ("_call_perplexity", lambda: ai._call_perplexity("q", market="KR")),
    ("_perplexity_search", lambda: dr._perplexity_search("q", market="KR")),
])
def test_search_helpers_accept_market(fn_name, call, monkeypatch):
    """시장 인자를 받지 않으면 호출부가 조용히 미국 기본값으로 돈다."""
    import inspect
    fn = getattr(ai if fn_name == "_call_perplexity" else dr, fn_name)
    assert "market" in inspect.signature(fn).parameters


@pytest.mark.parametrize("fn", [
    rw.gather_equity_perplexity,
    rw.gather_industry_perplexity,
    dr.generate_daily_report,
    dr._build_prompt,
    ai.gather_perplexity_context,
])
def test_news_entrypoints_take_market(fn):
    import inspect
    assert "market" in inspect.signature(fn).parameters, (
        f"{fn.__module__}.{fn.__name__} 가 market 을 받지 않는다 — "
        "이 경로는 항상 미국 기사로 리포트를 쓴다"
    )
