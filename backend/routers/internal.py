"""
routers/internal.py
───────────────────
Cloud Scheduler 가 호출하는 내부 작업 엔드포인트.

Cloud Run 은 요청이 없으면 인스턴스를 0 으로 내린다. 그래서 프로세스 안에서
도는 백그라운드 스케줄러(ENABLE_SCHEDULER)는 실질적으로 동작하지 않는다 —
실제로 그 때문에 시세 거래량이 한 건도 수집되지 않아 매매신호 목록이 비어 있었다.
바깥에서 주기적으로 깨워 주는 편이 서버리스에 맞다.

이 서비스는 웹앱이 직접 호출해야 해서 공개(allow-unauthenticated)로 열려 있다.
따라서 "누가 불렀는지"를 앱이 직접 확인해야 한다. Cloud Scheduler 가 붙여 보내는
OIDC 토큰을 검증해, 지정한 서비스 계정이 지정한 audience 로 부른 요청만 받는다.
공유 비밀번호 방식보다 낫다 — 유출 시 회수가 IAM 으로 끝나고, 값을 코드나
환경변수에 둘 필요도 없다.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/internal", tags=["internal"])

# 이 서비스 계정이 보낸 요청만 받는다. 비어 있으면 엔드포인트를 열지 않는다.
SCHEDULER_SA = os.getenv("SCHEDULER_SERVICE_ACCOUNT", "").strip()
# OIDC 토큰의 audience. Cloud Scheduler 작업에 설정한 값과 같아야 한다.
SCHEDULER_AUDIENCE = os.getenv("SCHEDULER_AUDIENCE", "").strip()


def _verify_scheduler(authorization: Optional[str]) -> None:
    """Cloud Scheduler 의 OIDC 토큰 검증. 실패하면 404 로 막는다.

    401 이 아니라 404 를 쓰는 이유는, 인증 실패를 알려 주면 "여기에 내부
    엔드포인트가 있다"는 사실이 드러나기 때문이다. 존재 자체를 숨긴다.
    """
    if not SCHEDULER_SA:
        raise HTTPException(status_code=404, detail="찾을 수 없습니다.")

    token = ""
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=404, detail="찾을 수 없습니다.")

    try:
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(
            token, g_requests.Request(),
            audience=SCHEDULER_AUDIENCE or None,
        )
    except Exception as e:
        logger.warning(f"내부 엔드포인트 토큰 검증 실패: {e}")
        raise HTTPException(status_code=404, detail="찾을 수 없습니다.")

    if claims.get("email") != SCHEDULER_SA or not claims.get("email_verified"):
        logger.warning(f"내부 엔드포인트 호출자 불일치: {claims.get('email')}")
        raise HTTPException(status_code=404, detail="찾을 수 없습니다.")


@router.post("/collect-prices")
def collect_prices(
    max_tickers: int = Query(default=120, ge=1, le=500),
    market: str = Query(default="US", description="US 또는 KR"),
    authorization: Optional[str] = Header(default=None),
):
    """해당 시장의 종가·거래량 수집을 한 번 돌린다.

    미국은 S&P500, 한국은 시총 상위 350종목(KOSPI200 + KOSDAQ150)이 대상이다.
    두 시장은 마감 시각이 달라 Cloud Scheduler 작업도 따로 걸어야 한다
    (KRX 15:30 KST / NYSE 16:00 ET).

    max_tickers 로 한 번에 처리할 양을 제한한다. Cloud Run 은 요청이 끝나면
    CPU 를 회수하므로, 500 종목을 한 요청에서 받다가 타임아웃되면 아무것도
    남지 않는다. 나눠 받으면 매 호출이 진척을 남기고 남은 건 다음 호출이 잇는다.

    응답의 remaining 이 0 이 아니면 아직 받을 종목이 남았다는 뜻이다.
    """
    _verify_scheduler(authorization)

    from backend.db.scheduler import _update_daily_prices
    from backend.services.markets import normalize

    mkt = normalize(market)
    try:
        result = _update_daily_prices(max_tickers=max_tickers, market=mkt)
    except Exception as e:
        logger.error(f"[{mkt}] 가격 수집 실패: {e}")
        raise HTTPException(status_code=500, detail="수집 중 오류가 발생했습니다.")

    logger.info(f"[{mkt}] 가격 수집 결과: {result}")
    return {**result, "market": mkt}
