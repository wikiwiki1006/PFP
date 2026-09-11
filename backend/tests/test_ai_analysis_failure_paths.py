"""
`ai_analysis` 의 실패 분기들 — 한 번도 실행된 적이 없던 아홉 곳.

폴백 감사에서 이 모듈의 `except: pass` 가 **아홉 개** 나왔고, 전부 테스트가
한 번도 실행하지 않는 분기였다. 프롬프트를 만드는 모듈에서 수집 실패를
통째로 삼키면 **그 블록이 조용히 빠진 프롬프트**가 나가고, 모델은 빈자리를
사전지식으로 메운다 — 생략이 환각을 부르는 그 형태다.

pfp-44 가 아홉 개를 전부 고치고, 고친 뒤 **강제로 발동시켜 로그를 눈으로
확인**했다. 그 확인은 한 번 돌고 끝나는 스크립트였다. 여기로 옮기는 이유는
하나다 — **고친 코드가 다시 '한 번도 안 도는' 상태로 돌아가지 않게** 하려는
것이다. `market_calendar` 의 폴백이 그렇게 몇 달을 지냈다.

## 이 파일이 재는 것

파서의 실패는 `None` 으로 나가야 한다. 빈 리스트가 아니다 — 빈 리스트는
"카드가 없는 리포트" 라는 **결과**이고, `None` 은 "파싱하지 못했다" 다.
호출자가 둘을 구별할 수 있어야 섹션을 비울지 오류를 알릴지 정한다 (§1.3).
"""
from __future__ import annotations

import logging
from unittest import mock

import pytest

from backend.services import ai_analysis


# ── 판정 카드 파싱 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, why", [
    ("완전히 JSON 이 아닌 텍스트", "중괄호가 없다 — 정규식이 아예 안 걸린다"),
    # 위 케이스는 `re.search` 가 실패해 **`except` 를 지나지 않는다.** 중괄호는
    # 있는데 JSON 이 아닌 입력이 있어야 `json.loads` 가 던지는 경로가 돈다.
    # 처음에 이 케이스가 없어서, 계약은 고정했지만 그 분기는 여전히 미실행이었다.
    ('{"cards": [oops}', "중괄호는 있는데 JSON 이 아니다"),
    ('{"cards": [{"title": "A", "color": "danger"', "중간에 잘렸다"),
    ("", "빈 응답"),
    ('{"cards": "리스트가 아님"}', "cards 가 리스트가 아니다"),
    ('{"cards": []}', "카드가 비어 있다"),
])
def test_unparsable_verdict_cards_return_none(raw, why, caplog):
    """파싱 실패는 `None` 이고, **로그가 남는다.**

    빈 리스트로 돌려주면 호출자는 "카드가 없는 정상 리포트" 로 읽는다.
    그리고 로그가 없으면 리포트에서 판정 섹션이 사라진 이유를 나중에 찾을
    단서가 아무것도 없다.
    """
    with caplog.at_level(logging.WARNING):
        out = ai_analysis.parse_verdict_cards(raw)

    assert out is None, (
        f"{why}: got {out!r} -- an empty list means 'a report with no cards', "
        "which is a result. None means 'could not parse'. "
        "(둘을 같게 만들면 호출자가 구별하지 못한다.)"
    )
    assert caplog.records, (
        f"{why}: nothing was logged -- the verdict section vanishes from the "
        "report with no trace of why. (조용히 사라진다.)"
    )


def test_valid_verdict_cards_survive():
    """대조군 — 정상 JSON 은 통과한다.

    없으면 위 검사들은 "언제나 None 을 준다" 는 구현으로 전부 통과한다.
    """
    out = ai_analysis.parse_verdict_cards('{"cards": [{"title": "A", "color": "danger"}]}')

    assert out and out[0]["title"] == "A", f"정상 입력이 {out!r} 로 나왔다"


def test_an_unknown_colour_is_normalised_not_dropped():
    """모르는 색은 카드를 버리지 않고 기본값으로 바꾼다.

    카드를 통째로 빼면 판정 하나가 사라지는데, 색 하나 때문에 내용을 잃는
    것은 비용이 맞지 않는다.
    """
    out = ai_analysis.parse_verdict_cards('{"cards": [{"title": "A", "color": "zzz"}]}')

    assert out and len(out) == 1, f"모르는 색 때문에 카드가 사라졌다: {out!r}"
    assert out[0]["color"] != "zzz", "정규화되지 않았다"


# ── 포트폴리오 액션 파싱 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, why", [
    ("배열이 없는 텍스트", "배열이 없다"),
    ("[{broken", "배열이 깨졌다"),
    ("", "빈 응답"),
])
def test_unparsable_portfolio_actions_return_none(raw, why, caplog):
    """액션 파싱 실패도 `None` + 로그다."""
    with caplog.at_level(logging.WARNING):
        out = ai_analysis.parse_portfolio_actions(raw)

    assert out is None, f"{why}: got {out!r}, expected None"
    assert caplog.records, f"{why}: 로그가 없다 — 액션이 왜 비었는지 알 수 없다"


def test_valid_portfolio_actions_survive():
    """대조군 — 정상 배열은 통과한다."""
    out = ai_analysis.parse_portfolio_actions('[{"action": "BUY"}]')

    assert out and out[0]["action"] == "BUY", f"정상 입력이 {out!r} 로 나왔다"


# ── 시장 지표 수집이 전부 실패할 때 ────────────────────────────────────────────

def test_market_data_says_unavailable_when_every_source_fails(caplog):
    """DB 캐시와 yfinance 가 둘 다 죽으면 **그 사실이 프롬프트에 남는다.**

    조용히 빈 문자열을 주면 그 블록만 사라지고, 모델은 시장 상황을 자기
    기억에서 꺼내 쓴다. 없다고 적으면 안 쓴다.
    """
    # 거시지표도 같이 막는다. 안 막으면 `build_macro_block` 이 FRED 로
    # **실제로 나간다** — 이 테스트는 DB 와 yfinance 만 막고 있었고, 그 호출은
    # 네트워크 가드가 생기기 전까지 매 실행 나갔다. 여기서 재는 것은 시장
    # 지표 수집 실패이지 거시지표가 아니다.
    with mock.patch("backend.db.market_cache.get_prices_from_db",
                    side_effect=RuntimeError("db down")), \
         mock.patch("backend.db.is_available", return_value=True), \
         mock.patch("yfinance.download", side_effect=RuntimeError("net down")), \
         mock.patch.object(ai_analysis, "build_macro_block", return_value=""), \
         caplog.at_level(logging.WARNING):
        text = ai_analysis.gather_yfinance_market_data("US")

    assert text.strip(), (
        "every source failed and the market block came back empty -- an "
        "omission is not 'no data' to the model, it is no instruction at all. "
        "(생략이 환각을 부른다.)"
    )
    assert any(tok in text for tok in ("unavailable", "실패", "없음", "수집 못")), (
        f"the block does not say it failed: {text[:200]!r}"
    )
    assert caplog.records, "수집이 전부 실패했는데 로그가 없다"


# ── 보유가 없는 계좌 ──────────────────────────────────────────────────────────

def test_cash_only_account_reports_no_pnl_total():
    """현금만 있는 계좌에 '오늘 손익 합계' 를 적지 않는다.

    더할 종목이 하나도 없는데 합계를 적으면 그 0 은 '오늘 변동 없음' 으로
    읽힌다. 실제로는 잴 대상이 없는 것이다 (§1.3).
    """
    with mock.patch.object(ai_analysis, "build_macro_block", return_value="  (없음)"), \
         mock.patch.object(ai_analysis, "call_claude", lambda prompt, *a, **kw: prompt):
        prompt = ai_analysis.generate_daily_brief({"CASH": {"q": 1000.0}}, {}, [], "US")

    total_lines = [ln for ln in prompt.splitlines() if "총자산" in ln]
    assert total_lines, f"총자산 줄이 없다:\n{prompt[:300]}"
    assert not any("손익 합계" in ln for ln in total_lines), (
        f"a cash-only account was given a day's P&L total: {total_lines} -- "
        "there is nothing to sum, and 0 reads as 'unchanged today'. "
        "(잴 대상이 없는 것과 변동이 없는 것은 다르다.)"
    )
