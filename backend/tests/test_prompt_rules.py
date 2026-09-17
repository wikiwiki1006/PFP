"""
프롬프트 빌더 규칙 — 빌더 **전부**에 같은 검사를 건다.

규칙은 `agent/reportmanage` 가 정의했다. 오늘 프롬프트에서 같은 형태의 결함을
네 번 밟은 창이고, 그 넷이 전부 **"한 빌더는 규칙을 알고 사본은 모르는"** 형태였다:
통화 표기(`$333605.94B`), 거시지표 시장 분기(한국 브리프에 연준 금리), 금액 계산
(모델이 곱해서 67배 오차), 도구 없는 웹서치 지시(모델이 기억으로 채움).

한 곳만 고치면 다음 결함은 나머지에서 나온다. 그래서 규칙을 코드가 아니라
**프롬프트 문자열**에 걸고, 빌더 목록(`helpers/prompt_corpus.py`)에 전부 모은다.

## 왜 정적 검사인가

LLM 을 부르지 않는다. `_format_portfolio`·`build_macro_block`·`_build_prompt` 등이
인자만 주면 문자열이 나오는 순수 함수라, 문자열을 놓고 규칙을 걸면 비용 0 이고
결과가 결정적이다. 모델 출력을 검사하는 방식은 비결정적이라 게이트가 못 된다.

산술 규칙(D)은 여기서 다루지 않는다 — "비율만 주고 모델이 곱한다" 는 프롬프트
문자열로는 드러나지 않고 실제 출력을 봐야 나온다.

## 게이트로서의 설계

지금까지 만든 가드와 같은 원칙이다:

- **판정을 좁게.** 애매하면 통과시킨다. 거짓 양성 하나가 게이트 전체의 신뢰를
  깎고, 그러면 다들 우회한다.
- **검출기를 자기검사한다.** 답을 아는 문자열을 넣어 양방향으로 잰다. 이런
  검사는 검출기가 망가져도 "위반 0건" 으로 조용히 통과한다.
- **아직 안 고친 위반은 xfail(strict).** 게이트를 빨간 채로 두지 않되, 고치는
  순간 XPASS 로 마커 제거가 통보된다. 남의 소유 파일은 직접 못 고친다.
"""
from __future__ import annotations

import re

import pytest

from backend.tests.helpers.prompt_corpus import Prompt, build_corpus

# 코퍼스는 한 번만 만든다 — 빌더 호출이 가볍지만 파라미터마다 부를 이유가 없다.
_CORPUS = build_corpus()


def _cases(*, xfail: dict[str, str] | None = None, market: str | None = None):
    """코퍼스를 pytest 파라미터로. `xfail` 은 {Prompt.id: 사유}.

    `market` 을 주면 그 시장만 남긴다. 해당 없는 항목을 skip 으로 남기지 않는
    이유는, skip 이 쌓이면 **정말로 빠진 검사와 구별이 안 되기** 때문이다.
    """
    xfail = xfail or {}
    return [
        pytest.param(
            p, id=p.id,
            marks=([pytest.mark.xfail(strict=True, reason=xfail[p.id])]
                   if p.id in xfail else []),
        )
        for p in _CORPUS
        if market is None or p.market == market
    ]


# ── A1. 한국 프롬프트에 달러 기호가 없다 ────────────────────────────────────────

_A1_XFAIL: dict[str, str] = {}


@pytest.mark.parametrize("prompt", _cases(xfail=_A1_XFAIL, market="KR"))
def test_kr_prompt_has_no_dollar_sign(prompt: Prompt):
    """A1 — 시장이 KR 이면 프롬프트 어디에도 `$` 가 없다."""
    found = [prompt.text[max(0, m.start() - 20):m.start() + 20]
             for m in re.finditer(r"\$", prompt.text)]
    assert not found, (
        f"{prompt.builder} renders '$' in a KR prompt -- the model reads the "
        f"number as dollars. Context: {found[:3]} "
        "(원화 금액에 달러 기호가 붙었다.)"
    )


# ── A2. 거시지표가 시장을 넘어가지 않는다 (양방향) ──────────────────────────────

# 한쪽만 검사하면 반대 방향 회귀를 놓친다. 단어 경계를 쓰는 이유는 'Fed' 가
# 'Federal'·'feed' 안에 들어가 오탐을 내기 때문이다.
#
# 변동성 지수도 시장 것이다 (US VIX · KR VKOSPI). 한국 프롬프트의 `VIX` 는 "미국 "
# 이 바로 앞에 붙어 미국 지표라고 밝힐 때만 허용한다 — 리포트 품질 역할의 제안.
# 그 세 글자 없이 "VIX 지수: 17.7" 이 한국 프롬프트에 실리면 모델은 그걸 한국
# 변동성으로 읽는다 (한국 피드백에 실제로 "VIX 지수" 가 붙어 나갔다, f2b7802).
_US_ONLY_MACRO = [r"\bFed funds\b", r"\bFOMC\b", r"\bFed rate\b",
                  r"10Y-2Y", r"\bUnemployment\b", r"\bHY spread\b",
                  r"(?<!미국 )\bVIX\b"]
_KR_ONLY_MACRO = [r"한국은행 기준금리", r"국고채", r"\bVKOSPI\b"]


@pytest.mark.parametrize("prompt", _cases())
def test_macro_indicators_stay_in_their_market(prompt: Prompt):
    """A2 — KR 에 미국 거시지표가, US 에 한국 거시지표가 들어가지 않는다."""
    patterns = _US_ONLY_MACRO if prompt.market == "KR" else _KR_ONLY_MACRO
    other = "US" if prompt.market == "KR" else "KR"

    leaked = [p for p in patterns if re.search(p, prompt.text, re.IGNORECASE)]
    assert not leaked, (
        f"{prompt.builder} puts {other}-only macro indicators {leaked} into a "
        f"{prompt.market} prompt -- the model reasons from the wrong economy. "
        "(다른 시장의 거시지표가 근거로 실렸다.)"
    )


# ── A3. 원화에 B/M/T 를 쓰지 않는다 ─────────────────────────────────────────────

# `$333605.94B` 사고의 직접 원인. 원화는 조·억·만으로 끊는다.
_WON_WITH_SI_SUFFIX = re.compile(r"₩[\d,.]+\s*[BMT](?![a-zA-Z])")


@pytest.mark.parametrize("prompt", _cases())
def test_won_amounts_use_korean_units(prompt: Prompt):
    """A3 — 원화 금액에 10억(B)·100만(M)·1조(T) 접미사를 붙이지 않는다."""
    found = _WON_WITH_SI_SUFFIX.findall(prompt.text)
    assert not found, (
        f"{prompt.builder} writes won with an SI suffix: {found[:3]} -- the "
        "model reads B as billions of dollars. Use 조/억/만. "
        "(원화에 B 를 쓰면 달러로 읽힌다.)"
    )


# ── A4. 원화에 소수점이 없다 ───────────────────────────────────────────────────

# 조·억·만으로 끊은 금액의 소수점은 호가 미만 정밀도가 아니다 — `₩4.2조` 의
# `.2` 는 2,000억이다. `_fmt_amount` 가 큰 원화 금액을 그렇게 적고 CLAUDE.md §1.4
# 표도 `₩4.20조` 로 규정한다. 단위 접미사가 뒤따르면 잡지 않는다.
_WON_WITH_DECIMAL = re.compile(r"₩[\d,]+\.\d+(?!\d*\s*[조억만])")


@pytest.mark.parametrize("prompt", _cases())
def test_won_amounts_have_no_decimals(prompt: Prompt):
    """A4 — 호가 단위가 1원이라 `₩71,900.00` 은 없는 정밀도다."""
    found = _WON_WITH_DECIMAL.findall(prompt.text)
    assert not found, (
        f"{prompt.builder} writes won with decimals: {found[:3]} -- the tick "
        "size is 1 won, so those digits are invented precision. "
        "(원화에 소수점을 붙이지 않는다.)"
    )


# ── B2. 없는 값을 자리표시자 문자열로 넣지 않는다 ────────────────────────────────

# 모델은 'N/A' 나 '?' 를 수치처럼 인용한다. 행을 빼거나 '불명'이라고 적어야 한다.
# 판정을 좁게 둔다: 산문 속 물음표가 아니라 **값 자리**의 자리표시자만 잡는다.
_PLACEHOLDER = re.compile(r"(?:[:\s(|])\?(?=[\sB%|)])|\bN/A\b|\$\?")


def _data_lines(text: str) -> str:
    """마크다운 표 행을 뺀 나머지.

    프롬프트에는 **모델이 채울 출력 서식**이 표로 들어간다. 그 칸의 `?%` 는
    데이터가 아니라 빈칸 표시다 — 같은 행의 다른 칸도 '어떤 조건이 갖춰지면'
    처럼 지시문이다. 이 리포의 프롬프트는 `XX%`·`[BUY/HOLD/SELL]`·`$XXX.XX`
    도 같은 뜻으로 쓴다.

    그걸 위반으로 잡으면 정상적인 서식 지정이 전부 빨개지고, 그러면 이 검사는
    무시당한다. 그래서 `|` 로 시작하는 줄은 보지 않는다.

    **놓치는 것**: 데이터를 마크다운 표로 실어 보내는 빌더가 생기면 그 안의
    자리표시자는 안 걸린다. 지금 그런 빌더는 없고, 실제 결함
    (`_build_ticker_section` 의 `$?B | ? — ?`)은 `|` 로 시작하지 않아 그대로
    잡힌다. 그런 빌더가 생기면 이 함수를 다시 봐야 한다.
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("|"))

_B2_XFAIL: dict[str, str] = {}


@pytest.mark.parametrize("prompt", _cases(xfail=_B2_XFAIL))
def test_missing_values_are_not_placeholder_strings(prompt: Prompt):
    """B2 — `N/A` · `?` 를 값 자리에 적지 않는다."""
    found = _PLACEHOLDER.findall(_data_lines(prompt.text))
    assert not found, (
        f"{prompt.builder} puts placeholder text where a value belongs "
        f"({len(found)} occurrence(s)) -- the model quotes it as if it were a "
        "figure. Omit the row or say it is unknown. "
        "(없는 값을 문자열로 넣으면 모델이 수치처럼 인용한다.)"
    )


# ── C3/C4. 모델이 못 하는 일을, 빈 곳을 메우라고 시키지 않는다 ──────────────────

# 이 빌더들의 출력은 전부 **도구 없는** 호출로 나간다 (확인:
# daily_report._generate_with_claude 의 최종 messages.create 에 tools 인자가 없다).
# 도구 없는 모델에게 검색을 시키면 못 한다고 말하지 않고 기억으로 채운다 —
# 낡은 뉴스가 오늘 날짜 리포트에 실린 것이 그 결과다.
_FILL_THE_GAP = [
    "웹서치", "웹 검색", "web search", "검색해", "검색하여", "검색하고",
    "학습 지식", "사전 지식", "알고 있는", "기억하는",
]

# reportmanage 가 f72813b 에서 지시를 제거했다 — 이제 "제공된 데이터로만
# 작성하라. 검색하거나 기억에 의존하지 마라" 로 되어 있다. 위반이 없으므로
# 목록이 비어 있고, 다시 생기면 이 검사가 잡는다.
_C_XFAIL: dict[str, str] = {}


@pytest.mark.parametrize("prompt", _cases(xfail=_C_XFAIL))
def test_prompt_does_not_ask_the_model_to_fill_gaps(prompt: Prompt):
    """C3/C4 — 검색·사전지식으로 빈 곳을 메우라고 지시하지 않는다."""
    found = [tok for tok in _FILL_THE_GAP if tok in prompt.text]
    assert not found, (
        f"{prompt.builder} instructs the model to {found} -- these builders "
        "are sent to calls without tools, so the model invents rather than "
        "refusing. Say the data is missing instead. "
        "(도구 없는 모델에게 검색을 시키면 기억으로 채운다.)"
    )


# ── 검출기 자기검사 ────────────────────────────────────────────────────────────
#
# 위 검사들은 정규식 하나가 어긋나면 **"위반 0건" 으로 조용히 통과한다.**
# 볼 것이 없는 것과 문제가 없는 것이 출력에서 똑같이 보인다. 그래서 답을 아는
# 문자열로 양방향을 잰다 — 놓치는 쪽과 과잉 검출 쪽 모두.

@pytest.mark.parametrize("text, should_match, why", [
    ("시가총액 ₩500조", False, "조 단위는 정상"),
    ("시가총액 ₩500.0B", True, "원화에 B"),
    ("매출 ₩333,605.94 B", True, "공백이 끼어도 잡는다"),
    ("시가총액 $500.0B", False, "달러에 B 는 정상"),
    ("₩71,900 (Bloomberg)", False, "B 로 시작하는 단어는 접미사가 아니다"),
])
def test_won_si_suffix_detector(text, should_match, why):
    assert bool(_WON_WITH_SI_SUFFIX.search(text)) is should_match, why


@pytest.mark.parametrize("text, should_match, why", [
    ("현재가 ₩71,900", False, "소수점 없음 — 정상"),
    ("현재가 ₩71,900.00", True, "원화에 소수점"),
    ("현재가 $231.45", False, "달러 소수점은 정상"),
    ("₩71,900. 다음 문장", False, "문장 끝 마침표는 소수점이 아니다"),
])
def test_won_decimal_detector(text, should_match, why):
    assert bool(_WON_WITH_DECIMAL.search(text)) is should_match, why


@pytest.mark.parametrize("text, should_match, why", [
    ("시가총액 $?B", True, "값 자리의 물음표"),
    # 처음에 이걸 '안 걸린다'로 적었다가 자기검사에서 틀린 쪽이 기대값이라는 게
    # 드러났다. `섹터 ? — 산업 ?` 는 실제로 optimizer 가 내보내는 값 자리다.
    ("섹터 ? — 산업 ?", True, "값 자리의 물음표 — 산문이 아니다"),
    ("PER: ?%", True, "값 자리의 물음표"),
    ("배당수익률 N/A", True, "N/A"),
    ("이 종목의 성장성은 어떤가?", False, "산문 속 물음표는 잡지 않는다"),
    ("어느 쪽이 유리한가? 아래를 보라", False, "산문 속 물음표"),
    # 출력 서식 표의 빈칸. 모델이 채우라고 둔 자리이지 데이터가 아니다.
    ("| 낙관 (상승) | ?% | 어떤 조건이 갖춰지면 | S&P500 +?% 예상 |", False,
     "마크다운 표는 모델이 채울 서식이다"),
    ("  ■ 005930.KS  (시가총액 $?B | Technology — Semi)", True,
     "표가 아닌 줄의 자리표시자는 데이터다 — `|` 가 있어도 줄 시작이 아니다"),
])
def test_placeholder_detector(text, should_match, why):
    assert bool(_PLACEHOLDER.search(_data_lines(text))) is should_match, why


@pytest.mark.parametrize("pattern, text, should_match", [
    (r"\bFed funds\b", "  Fed funds: 4.25%", True),
    (r"\bFed funds\b", "Federal Reserve 의 정책", False),
    (r"\bUnemployment\b", "  Unemployment: 4.1%", True),
    (r"\bUnemployment\b", "unemployment insurance", True),
    (r"한국은행 기준금리", "한국은행 기준금리: 2.50%", True),
    (r"국고채", "국고채 3년: 2.61%", True),
    (r"(?<!미국 )\bVIX\b", "- VIX 지수: 17.7 (정상)", True),
    (r"(?<!미국 )\bVIX\b", "매크로 지표: VIX: 18.0 (-1.00%)", True),
    (r"(?<!미국 )\bVIX\b", "시장 변동성(VIX) 변화 요인", True),
    (r"(?<!미국 )\bVIX\b", "미국 VIX 는 15.7 이다", False),
    (r"(?<!미국 )\bVIX\b", "- VKOSPI 지수: 43.0 (위험)", False),
    (r"(?<!미국 )\bVIX\b", "VIXY ETF", False),
    (r"\bVKOSPI\b", "VKOSPI: 43.02 (-3.11%, 2026-09-17 기준)", True),
    (r"\bVKOSPI\b", "KOSPI 전일 변동: +0.40%", False),
])
def test_macro_token_detector(pattern, text, should_match):
    assert bool(re.search(pattern, text, re.IGNORECASE)) is should_match


def test_corpus_covers_every_known_builder():
    """코퍼스가 비면 위 검사가 전부 조용히 통과한다.

    빌더 목록은 손으로 관리한다 — 프롬프트를 만드는 함수를 자동으로 알아낼
    방법이 마땅치 않다. 그래서 최소한 **알려진 빌더가 빠지지 않았는지**는
    고정한다. `portfolio_optimizer` 가 어느 목록에도 없었던 것이 그 이유다.
    """
    builders = {p.builder.split("(")[0] for p in _CORPUS}
    expected = {
        "ai_analysis._format_portfolio",
        "ai_analysis.build_macro_block",
        "daily_report._build_prompt",
        "report_writer._equity_prompt",
        "report_writer._equity_prompt_part1",
        "report_writer._equity_prompt_part2",
        "report_writer._industry_prompt",
        "portfolio_optimizer._build_ticker_section",
        "ai_analysis._analyst_feedback_prompt",
    }
    missing = expected - builders
    assert not missing, f"prompt corpus lost {sorted(missing)} -- rules stopped covering them"

    empty = [p.id for p in _CORPUS if not p.text.strip()]
    assert not empty, f"these builders produced an empty prompt: {empty} -- nothing was checked"


# 변형 항목이 **그 경로를 실제로 지나는지.** 빌더가 `__VOL` 을 더는 안 그리거나 뉴스
# 섹션 머리를 바꾸면, 위 규칙들은 그 변형에서 아무것도 안 보고 통과한다. 문구 전체가
# 아니라 경로의 흔적(섹션 머리 · 지수 이름과 값이 한 줄에)만 본다 — 문장 다듬기는
# 막지 않는다.
_PATH_MARKS = {
    "daily_report._build_prompt(변동성·뉴스)[KR]":
        [r"\bVKOSPI\b[^\n]*43\.02", r"\[국내 매체 수집\]", r"\[급등락 종목 원인 수집\]"],
    "daily_report._build_prompt(국내매체-빈수집)[KR]": [r"\[국내 매체 수집\][^\n]*없음"],
    "daily_report._build_prompt(변동성·뉴스)[US]": [r"\[급등락 종목 원인 수집\]"],
    "ai_analysis._analyst_feedback_prompt(값)[KR]": [r"^- VKOSPI\b[^\n]*43\.0"],
    "ai_analysis._analyst_feedback_prompt(값)[US]": [r"^- VIX\b[^\n]*17\.7"],
    "ai_analysis._analyst_feedback_prompt(값-없음)[KR]": [r"^- VKOSPI\b[^\n]*산출 불가"],
    "ai_analysis._analyst_feedback_prompt(값-없음)[US]": [r"^- VIX\b[^\n]*산출 불가"],
    "daily_report._build_prompt(결손·섹터없음)[KR]":
        [r"000660\.KS: 데이터 없음 — \S", r"합산 불가", r"005930\.KS:[^\n]*섹터 정보 없음"],
    "daily_report._build_prompt(결손·섹터없음)[US]":
        [r"MSFT: 데이터 없음 — \S", r"합산 불가", r"AAPL:[^\n]*섹터 정보 없음"],
}


@pytest.mark.parametrize("prompt_id", sorted(_PATH_MARKS))
def test_corpus_variants_reach_the_path_they_were_added_for(prompt_id):
    by_id = {p.id: p for p in _CORPUS}
    assert prompt_id in by_id, f"{prompt_id} is no longer in the corpus"
    text = by_id[prompt_id].text
    missing = [m for m in _PATH_MARKS[prompt_id] if not re.search(m, text, re.M)]
    assert not missing, (
        f"{prompt_id} no longer renders {missing} -- the rules above now pass on this "
        "variant without seeing the section it was added to cover"
    )


def test_intercepted_builders_reached_the_model():
    """모델 호출을 가로채 얻는 항목은 **정말로 그 호출까지 갔어야** 한다.

    `generate_daily_brief` 는 키가 없으면 `"ANTHROPIC_API_KEY 미설정"` 을
    돌려주고 끝난다. 그 한 줄에는 달러 기호도 거시지표도 없어서 위 규칙이
    전부 통과한다 — 검사한 게 아니라 **검사할 것이 없었다.** 빈 문자열 검사는
    이걸 못 잡는다(비어 있지 않다).

    코퍼스는 키를 고정해 그 길을 막았다. 이 검사는 다른 이른 반환이 생겨도
    같은 방식으로 조용히 통과하지 않게 한다.
    """
    intercepted = [p for p in _CORPUS if p.reached_model is not None]
    assert intercepted, (
        "no corpus entry is built by intercepting the model call -- the daily brief "
        "left the corpus, or this flag stopped being set, and the check below is empty."
    )
    short_circuited = {p.id: p.text[:80] for p in intercepted if not p.reached_model}
    assert not short_circuited, (
        f"these builders returned before calling the model, so their corpus text is "
        f"not a prompt: {short_circuited} -- every rule above passed on that text "
        "without checking anything. (모델까지 가지 않았다 — 규칙이 빈 것을 검사했다)"
    )
