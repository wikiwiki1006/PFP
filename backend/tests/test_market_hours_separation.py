"""포트폴리오 계산의 시장 시간 판단은 시장마다 갈려야 한다.

미국 캘린더로 고정돼 있어 두 가지가 깨져 있었다.
  ① 한국 포트폴리오의 가장 최근 거래일이 자산곡선에서 잘려 나갔다
     (KST 오전은 미국 기준 전날 밤이고, 미국 공휴일에 한국이 열린 날도 잘렸다)
  ② 'LIVE' 배지가 한국장 중에 꺼지고 미국장 중에 켜졌다
"""
from datetime import datetime, timedelta
from unittest.mock import patch
import zoneinfo

import pandas as pd
import pytest

from backend.services import market_calendar as mc
from backend.services.portfolio_calculator import _trim_to_session, _market_open_flag

KST = zoneinfo.ZoneInfo("Asia/Seoul")
ET = zoneinfo.ZoneInfo("America/New_York")

# 2026-09-07(월)은 미국 노동절 휴장, 한국은 정상 개장.
KR_SESSION = datetime(2026, 9, 8, 11, 0, tzinfo=KST)   # 한국 정규장
KR_AFTER = datetime(2026, 9, 8, 6, 0, tzinfo=KST)      # 한국 개장 전 새벽
US_SESSION = datetime(2026, 9, 8, 23, 30, tzinfo=KST)  # 미국 정규장

FRAME = pd.DataFrame(
    {"005930.KS": [255000, 258000, 269500]},
    index=pd.to_datetime(["2026-09-03", "2026-09-04", "2026-09-07"]),
)


def _at(when: datetime):
    return patch.multiple(
        mc,
        now_et=lambda *a, **k: when.astimezone(ET),
        now_kst=lambda *a, **k: when.astimezone(KST),
    )


@pytest.mark.parametrize("when", [KR_SESSION, KR_AFTER, US_SESSION])
def test_korean_frame_keeps_its_last_session(when):
    """한국 기준으로 자르면 09-07(한국 거래일)이 남는다."""
    with _at(when):
        kept = [str(d.date()) for d in _trim_to_session(FRAME, "KR").index]
    assert "2026-09-07" in kept


def test_us_trim_still_uses_us_calendar():
    """미국 경로는 종전대로 미국 마지막 확정 세션까지만 남긴다."""
    with _at(KR_AFTER):
        kept = [str(d.date()) for d in _trim_to_session(FRAME, "US").index]
    assert kept == ["2026-09-03", "2026-09-04"]


def test_live_badge_follows_its_own_market():
    with _at(KR_SESSION):
        assert _market_open_flag("KR") is True
        assert _market_open_flag("US") is False
    with _at(US_SESSION):
        assert _market_open_flag("US") is True
        assert _market_open_flag("KR") is False


def test_kr_status_does_not_depend_on_lagging_calendar(monkeypatch):
    """오늘은 KRX 캘린더에 없다 — 그걸로 판단하면 장중에 'closed' 가 된다.

    **캘린더가 오늘을 모르는 상태를 직접 만든다.** 예전에는 진짜 관측 캘린더를
    불러 `in (True, False)` 로 확인했다. 그 단언은 무엇이 와도 참이라 전제를
    재지 못했고, 부르는 순간 야후로 3번 나갔다 (curl_cffi 가 소켓 가드를 우회).
    """
    today = KR_SESSION.date()
    lagging = frozenset(
        today - timedelta(days=i) for i in range(1, 60)
        if (today - timedelta(days=i)).weekday() < 5
    )
    monkeypatch.setattr(mc, "_krx_trading_days", lambda: lagging)

    with _at(KR_SESSION):
        # 전제 — 관측 캘린더는 오늘을 모른다. 이게 참이어야 아래가 무언가를 잰다.
        assert mc.is_kr_trading_day(today) is False
        assert mc.kr_market_status() == "open"
