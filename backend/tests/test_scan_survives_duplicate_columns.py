"""
중복된 티커 열이 들어와도 매매신호 스캔이 죽지 않는다.

한국 유니버스에 같은 티커가 두 번 들어가 있었다(350개 중 고유 348). 두
호출자가 똑같이 이렇게 부른다:

    valid = [c for c in universe if c in close_df.columns]
    sma_macd_rsi_scan(close_df[valid], volume_df, top_n=10)

`universe` 에 중복이 있으면 `valid` 에도 있고, **`close_df[valid]` 가 같은
열을 두 번 고른다.** 그러면 `close_df[c]` 가 Series 가 아니라 DataFrame 이
되고 그 다음 `int(...)` 가 `TypeError` 로 터진다 — 스캔 전체가 죽는다.

운영에서 **20분마다 죽어** 한국 매매신호가 09-08 에 멈춰 있었다. 화면에는
옛 결과가 그대로 떠 있어 멈춘 것으로 안 보인다 — 캐시가 6시간이라 "좀
오래된 것" 처럼 읽힌다.

## 입력을 호출자와 같은 방법으로 만든다

프레임을 손으로 중복시키지 않고 **`close_df[valid]`** 로 만든다. 손으로
만들면 실제로 일어날 수 없는 모양을 쉽게 짓게 된다 — 실제로 처음에
거래량 프레임도 중복시켰다가 그 입력이 **도달 불가**라는 것을 알았다.
`volume_df` 는 두 호출자 모두 `get_volume_from_db` 결과를 **그대로** 넘기고,
그 함수는 SQL 피벗이라 티커당 열이 하나다.

(그래서 이 파일은 거래량 중복을 단언하지 않는다. 나중에 누가 "일관성" 을
이유로 `volume_df[valid]` 로 바꾸면 그때 같은 종류의 죽음이 `ValueError`
로 돌아온다 — 실측으로 확인했다. 지금 단언하면 코드가 하지 않는 약속을
적는 것이라 적어만 둔다.)

유니버스를 만드는 쪽(`fetch_top_by_marketcap` 의 중복 제거)은 네이버를
불러야 해서 여기서 재지 않는다. 여기서 재는 것은 **중복이 어디서 왔든
스캔이 완주하는가** 다 — 유니버스가 다시 어긋나도 스캔은 살아야 한다.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from backend.services.trading_signals import sma_macd_rsi_scan

# SMA200 까지 계산되려면 충분한 이력이 필요하다 (`min_history=210`).
_IDX = pd.bdate_range("2025-01-01", periods=320)
_RNG = np.random.default_rng(11)
_UNIVERSE = [f"T{i:03d}.KS" for i in range(12)]
# 운영과 같은 모양 — 유니버스 목록에 같은 티커가 두 번 들어 있다.
_DUPED_UNIVERSE = _UNIVERSE + [_UNIVERSE[0], _UNIVERSE[3]]


def _panel(tickers: list[str]) -> pd.DataFrame:
    """DB 가 주는 모양 — 티커당 열 하나."""
    data = {}
    for i, t in enumerate(dict.fromkeys(tickers)):
        steps = _RNG.normal(0.0006, 0.012, len(_IDX))
        data[t] = (100.0 + i) * np.cumprod(1 + steps)
    return pd.DataFrame(data, index=_IDX)


def _as_caller_does(universe: list[str]):
    """호출자와 **같은 방법**으로 스캔 입력을 만든다.

        valid = [c for c in universe if c in close_df.columns]
        sma_macd_rsi_scan(close_df[valid], volume_df, ...)
    """
    close_df = _panel(universe)
    volume_df = _panel(universe)          # SQL 피벗 — 중복이 없다
    valid = [c for c in universe if c in close_df.columns]
    return close_df[valid], volume_df


def test_the_caller_shape_really_produces_duplicate_columns():
    """전제 — 이 만드는 방법이 실제로 중복 열을 낸다.

    이게 없으면 아래 검사는 **깨끗한 프레임**을 재면서 통과한다. 그러면
    "중복이 와도 괜찮다" 가 아니라 "중복이 안 왔다" 를 확인한 것이 된다.
    """
    close, volume = _as_caller_does(_DUPED_UNIVERSE)

    assert close.columns.duplicated().sum() == 2, (
        f"close frame has {close.columns.duplicated().sum()} duplicates -- "
        "the condition this file measures was not created."
    )
    assert not volume.columns.has_duplicates, (
        "거래량 프레임에 중복이 생겼다 — 호출자 경로에서는 나올 수 없는 모양이다"
    )


def test_a_duplicated_universe_does_not_crash_the_scan(caplog):
    """중복이 있어도 **완주한다** — 그리고 그 사실을 기록한다.

    예전에는 `TypeError` 로 스캔 전체가 죽었다. 조용히 죽으면 화면에는
    옛 결과가 남아 "좀 오래된 것" 으로 읽힌다.
    """
    close, volume = _as_caller_does(_DUPED_UNIVERSE)

    with caplog.at_level(logging.WARNING):
        out = sma_macd_rsi_scan(close, volume, top_n=5)

    assert isinstance(out, dict) and "long_picks" in out, (
        f"the scan did not finish on a duplicated universe: {out!r} -- in "
        "production this died every 20 minutes while the screen kept showing "
        "the last good result. (조용히 죽으면 '좀 오래된 것' 으로 읽힌다.)"
    )
    assert caplog.records, (
        "duplicate columns were folded without a word -- the universe is "
        "broken upstream and nothing says so."
    )


def test_the_duplicate_is_folded_not_counted_twice():
    """중복은 접힌다 — 같은 종목이 결과에 두 번 나오지 않는다.

    죽지 않는 것만으로는 부족하다. 같은 종목이 매수 후보에 두 번 뜨면
    사용자는 그걸 "두 개의 신호" 로 읽는다.
    """
    close, volume = _as_caller_does(_DUPED_UNIVERSE)

    out = sma_macd_rsi_scan(close, volume, top_n=10)

    for side in ("long_picks", "short_picks"):
        names = [p["ticker"] for p in out[side]]
        assert len(names) == len(set(names)), f"{side} 에 같은 종목이 두 번: {names}"

    assert out["scanned"] <= len(_UNIVERSE), (
        f"scanned={out['scanned']} but there are only {len(_UNIVERSE)} distinct "
        "tickers -- the duplicate was counted as another company."
    )


def test_a_clean_universe_still_scans_and_says_nothing(caplog):
    """대조군 — 중복이 없으면 그대로 돌고 **경고도 없다.**

    없으면 위 검사들은 "언제나 빈 결과" 라는 구현으로도 통과한다. 그리고
    경고가 매번 뜨면 진짜 중복이 났을 때 그 줄이 안 읽힌다.
    """
    close, volume = _as_caller_does(_UNIVERSE)

    with caplog.at_level(logging.WARNING):
        out = sma_macd_rsi_scan(close, volume, top_n=5)

    assert out["scanned"] > 0, f"깨끗한 유니버스인데 아무것도 못 훑었다: {out}"
    assert not [r for r in caplog.records if "중복" in r.getMessage()], (
        "a clean frame produced a duplicate-column warning -- a line that "
        "appears every run is a line nobody reads."
    )


def test_no_volume_data_at_all_is_survived():
    """거래량이 아예 없어도 죽지 않는다 — 완화 단계가 그걸 전제한다.

    `get_volume_from_db` 는 적재 전이면 `None` 을 준다. 중복과 겹치면
    두 폴백이 같이 도는데, 그 조합을 재 둔 곳이 없었다.
    """
    close, _ = _as_caller_does(_DUPED_UNIVERSE)

    out = sma_macd_rsi_scan(close, None, top_n=5)

    assert isinstance(out, dict) and "long_picks" in out
