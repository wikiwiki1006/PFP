"""
services/news_sources.py
────────────────────────
시장별 뉴스 수집 설정.

리포트·시나리오의 재료가 되는 웹 검색을 어디에서, 어떤 관점으로 할지 정한다.
한국 종목을 다루면서 로이터·CNBC 기사만 모으면, 프롬프트에 "한국 관점으로
쓰라"고 적어 봐야 소용이 없다 — **모델이 가진 근거가 미국뿐이면 한국 이야기가
나올 수 없다.** 관점은 프롬프트가 아니라 출처에서 갈린다.

한 곳에 모아 두는 이유는 뉴스를 긁는 지점이 네 군데(매크로 시나리오, 종목
리포트, 산업 리포트, 데일리 브리프)로 흩어져 있어서다. 예전에는 그중 하나만
한국 초점을 갖고 있었다.
"""
from __future__ import annotations

# 국내 주요 경제·증권 매체. Perplexity 의 도메인 필터는 최대 10개까지 받는다.
# 종합지 대신 경제지 위주로 골랐다 — 종목·업황 기사가 실제로 실리는 곳이다.
KR_NEWS_DOMAINS = [
    "hankyung.com",      # 한국경제
    "mk.co.kr",          # 매일경제
    "yna.co.kr",         # 연합뉴스
    "einfomax.co.kr",    # 연합인포맥스 (채권·외환)
    "edaily.co.kr",      # 이데일리
    "mt.co.kr",          # 머니투데이
    "sedaily.com",       # 서울경제
    "biz.chosun.com",    # 조선비즈
    "fnnews.com",        # 파이낸셜뉴스
    "asiae.co.kr",       # 아시아경제
]


def search_domains(market: str) -> list[str]:
    """이 시장의 검색 대상 도메인. 미국은 제한하지 않는다(빈 목록)."""
    return list(KR_NEWS_DOMAINS) if (market or "US").upper() == "KR" else []


def perplexity_extra(market: str) -> dict:
    """Perplexity 요청에 얹을 시장별 옵션.

    한국이면 국내 매체로 검색 범위를 좁힌다. 도메인 필터를 지원하지 않는
    모델·플랜이면 서버가 이 필드를 무시하므로, 넣어도 요청이 깨지지 않는다.
    """
    domains = search_domains(market)
    return {"search_domain_filter": domains} if domains else {}


def focus_block(market: str, subject: str = "") -> str:
    """프롬프트에 넣을 관점 지시문. 영어로 둔다 — 수집 응답도 영어라 토큰이 적다."""
    if (market or "US").upper() != "KR":
        return (
            "\n[Perspective: UNITED STATES]\n"
            "- Prioritise US market impact and US-listed companies\n"
        )
    tail = f" for {subject}" if subject else ""
    return (
        "\n[Perspective: SOUTH KOREA]\n"
        f"- Write from the viewpoint of a Korean investor{tail}\n"
        "- Use **Korean press and local brokerage research** as the primary sources\n"
        "  (한국경제, 매일경제, 연합인포맥스, 이데일리, 조선비즈 등)\n"
        "- Anchor on KOSPI/KOSDAQ, KRW/USD, exports and foreign-investor flows\n"
        "- Name Korea-listed companies and their KRX codes where relevant\n"
        "- Mention US/global events only where they transmit into the Korean market\n"
        "- Amounts in KRW (조/억). Do not convert to USD\n"
    )
