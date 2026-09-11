"""
backend/services/perplexity.py
──────────────────────────────
Perplexity sonar 호출을 한 곳으로 모은다.

이 함수는 리포 안에 **여섯 벌**이 있었다. 전부 같은 엔드포인트에 같은 모양의
본문을 보내는데, 하나가 고쳐져도 나머지는 그대로였다. 실제로 실패를 조용히
삼키는 결함을 `ai_analysis` 에서 고친 뒤 `report_writer` 에 둘, `daily_report`
에 하나가 같은 상태로 남아 있었고, 각각 따로 찾아 고쳐야 했다.

증상을 여러 벌 고치는 것으로는 끝나지 않는다 — 사본이 남아 있는 한 다음
변경에서 다시 갈린다. 호출부마다 다른 것(프롬프트·응답 길이·모델)만 인자로
받고 나머지는 여기서 한 번만 정한다.

## 실패 처리

실패하면 `""` 를 돌려주고 **이유를 로그에 남긴다.** 조용히 "" 를 돌려주면
뉴스로 쓴 리포트와 뉴스 없이 쓴 리포트가 겉보기에 같아진다 (§1.3).

호출자는 그 사실을 **프롬프트에도 적어야 한다.** 블록을 그냥 빼면 모델에게는
"없음" 이 아니라 아무 정보도 아니고, 그래서 사전지식으로 채운다.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.perplexity.ai/chat/completions"

# 뉴스 수집용 기본 모델. 예측용으로 `sonar-pro` 를 쓰는 호출부가 있어
# 모델은 인자로 받는다 (portfolio_optimizer 의 PERPLEXITY_MODEL).
DEFAULT_MODEL = "sonar"

# 뉴스·사실 수집은 매번 같은 답이 나오는 편이 낫다. 예측처럼 다른 값이
# 필요한 호출부는 인자로 올린다.
DEFAULT_TEMPERATURE = 0.0


def window_notice(window: str) -> str:
    """수집 **요청** 범위를 적는 문구.

    **이건 검증이 아니라 표시다.** 프롬프트가 "최근 48시간" 을 요구해도 응답이
    그 범위라는 보장은 없다 — 6개월 전 기사가 섞여도 우리는 모른다. 그래서
    "48시간 이내로 확인됨" 같은 말을 만들면 안 된다. 하지 않은 검증을 한 것처럼
    보이게 하는 것이 침묵보다 나쁘다.

    적는 사실은 둘이다: 무엇을 **요청했는가**, 그리고 **확인하지 않았다**.
    """
    return (f"(수집 요청 범위: 최근 {window}. 실제 기사 시점은 확인되지 않았습니다 — "
            f"기사 날짜를 말해야 한다면 본문에 적힌 날짜만 쓰고, 없으면 쓰지 마세요.)")


def _api_key() -> str:
    # 모듈 로드 시점에 읽어 두면 테스트나 키 교체 후 재로딩이 안 먹는다.
    return os.getenv("PERPLEXITY_API_KEY", "")


def search(
    prompt: str,
    *,
    market: str = "US",
    max_tokens: int = 1500,
    label: str = "",
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    system: Optional[str] = None,
    timeout: int = 30,
    scope_by_market: bool = True,
) -> str:
    """Perplexity 검색 결과 텍스트. 실패하면 `""` 이고, 이유는 로그에 남는다.

    `label` 은 로그에만 쓴다 — 어느 리포트의 뉴스가 빠졌는지 알아야
    나중에 그 리포트가 왜 얇은지 설명할 수 있다.

    `scope_by_market` 은 검색 출처를 그 시장 쪽으로 좁힌다(한국이면 국내
    경제지). 프롬프트로 관점만 바꾸고 출처가 미국 매체뿐이면 한국 이야기가
    나오지 않는다. 끌 이유가 있는 호출부만 끈다.
    """
    who = f"[{label}] " if label else ""

    key = _api_key()
    if not key:
        logger.warning("%sPerplexity 검색 건너뜀 — PERPLEXITY_API_KEY 미설정", who)
        return ""

    body: dict = {
        "model": model,
        "messages": (
            ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}]
        ),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if scope_by_market:
        from backend.services.news_sources import perplexity_extra
        body.update(perplexity_extra(market))

    try:
        resp = requests.post(
            _ENDPOINT,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
        # raise_for_status 를 쓴다. `if resp.ok:` 로 두고 else 를 안 쓰면
        # 4xx·5xx 가 아무 흔적 없이 "" 가 된다 — 실제로 그런 사본이 있었다.
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        logger.warning("%sPerplexity 검색 실패 (market=%s) — 뉴스 없이 진행",
                       who, market, exc_info=True)
        return ""
