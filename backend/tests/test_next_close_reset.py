"""
`market_calendar.next_close_reset` — 다음 '장 마감 + 30분'.

포트폴리오 최적화의 종목별 AI 뷰 캐시가 이 값을 **만료 시각**으로 쓴다.
DB·최적화·테스트 세 창이 같은 경계를 써야 해서 통합 소유 캘린더에 먼저
들어왔다 (dd4421d). 경계가 한 칸 어긋나도 예외는 나지 않는다 — 캐시가
틀린 날의 뷰를 조용히 하루 더 들고 있을 뿐이다. 그래서 틀렸다는 신호를
낼 곳이 여기뿐이다.

계약 (docstring 과 dd4421d):

    미국  NYSE 거래일 16:00 ET + delay   규칙 캘린더라 미래 날짜도 안다
    한국  **평일** 15:30 KST + delay      관측 캘린더는 미래를 모른다
    반환은 `now` 보다 **엄격히 뒤**       초기화 시각 정각에 부르면 다음 것
    aware `now` 는 시장 시간대로 옮기고, naive 는 그 시장의 현지 시각으로 읽는다
    모르는 시장은 ValueError

## 오라클을 모듈에서 빌리지 않는다

시간대는 이 파일이 만든 `ZoneInfo` 로 읽고, 마감 시각과 NYSE 휴장일은
거래소 사실을 **구체 값으로** 적는다. 모듈의 `ET`·`_MARKET_CLOSE`·
`is_us_trading_day` 로 기대값을 만들면 그것들이 틀렸을 때 기대값도 같이
틀려서 검사가 볼 수 없다 (CLAUDE.md §6).

## 사례와 성질을 둘 다 둔다

사례는 통합이 잰 경계를 그대로 고정한다. 성질은 휴장·연말·서머타임 전환을
품은 창을 30분 간격과 모든 초기화 시각의 ±1µs 로 훑는다 — 사례를 아무리
골라도 "정각", "자정 직후", "다른 시간대로 온 입력" 의 조합을 다 적을 수는
없다.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.services import market_calendar as mc
from backend.services.markets import MARKETS

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc

# 거래소 사실. 모듈 상수를 읽지 않는다.
_CLOSE = {"US": time(16, 0), "KR": time(15, 30)}
_TZ = {"US": ET, "KR": KST}
_OTHER_TZ = {"US": KST, "KR": ET}

# NYSE 정기 휴장일 — 2026 전부와 2027 년 1월. 성질 검사의 창은 이 범위 안에만
# 둔다 (`_FACTS_COVER`). 범위를 벗어나면 모르는 휴장일을 거래일로 읽어 검사가
# 엉뚱한 이유로 빨개지므로, 창을 옮길 때는 이 목록부터 넓힌다.
_US_HOLIDAYS = frozenset({
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18),
})
_FACTS_COVER = (date(2026, 1, 1), date(2027, 1, 31))


def _et(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def _kst(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=KST)


def _is_session(market: str, d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in _US_HOLIDAYS if market == "US" else True


def _reset_on(market: str, d: date, delay: int) -> datetime:
    return datetime.combine(d, _CLOSE[market], tzinfo=_TZ[market]) + timedelta(minutes=delay)


# ── 사례 — 통합이 잰 경계 ─────────────────────────────────────────────────────

_CASES = [
    pytest.param("US", _et(2026, 9, 17, 16, 10), _et(2026, 9, 17, 16, 30),
                 id="US-목-16시10분-같은날"),
    pytest.param("US", _et(2026, 9, 17, 16, 30), _et(2026, 9, 18, 16, 30),
                 id="US-초기화-정각은-다음-거래일"),
    pytest.param("US", _et(2026, 9, 18, 17, 0), _et(2026, 9, 21, 16, 30),
                 id="US-금-밤-월"),
    pytest.param("US", _et(2026, 11, 25, 17, 0), _et(2026, 11, 27, 16, 30),
                 id="US-추수감사절-전날-휴장-건너뜀"),
    pytest.param("US", _et(2026, 3, 6, 17, 0), _et(2026, 3, 9, 16, 30),
                 id="US-서머타임-시작-주말-EST에서-EDT"),
    pytest.param("US", _et(2026, 12, 24, 17, 0), _et(2026, 12, 28, 16, 30),
                 id="US-성탄-연휴"),
    pytest.param("US", _et(2026, 12, 31, 17, 0), _et(2027, 1, 4, 16, 30),
                 id="US-신정-연휴-해를-넘김"),
    # KST 금 05:00 = ET 목 16:00 → 목 16:30. 옮기지 않고 시간대 딱지만 바꾸면
    # (ET 금 05:00) 금 16:30, 입력 날짜(금)로 판단해도 금 16:30 이 나와 구별된다.
    # 통합이 잰 "KST 금 06:00(= ET 목 17:00)" 은 셋 다 금 16:30 이라 이 구별을
    # 못 한다 — 변이로 확인했다.
    pytest.param("US", _kst(2026, 9, 18, 5, 0), _et(2026, 9, 17, 16, 30),
                 id="US-KST로-온-입력은-ET로-옮겨서"),
    # naive 20:00 을 ET 로 읽으면 초기화 뒤 → 다음 날. UTC(=16:00 ET)나
    # KST(=07:00 ET)로 읽으면 같은 날이 나와 구별된다.
    pytest.param("US", datetime(2026, 9, 17, 20, 0), _et(2026, 9, 18, 16, 30),
                 id="US-naive는-ET로"),
    pytest.param("KR", _kst(2026, 9, 17, 15, 59), _kst(2026, 9, 17, 16, 0),
                 id="KR-목-15시59분-같은날"),
    pytest.param("KR", _kst(2026, 9, 18, 16, 0), _kst(2026, 9, 21, 16, 0),
                 id="KR-금-초기화-정각은-월"),
    pytest.param("KR", _kst(2026, 9, 19, 12, 0), _kst(2026, 9, 21, 16, 0),
                 id="KR-토-월"),
    # 통합이 잰 사례. 다만 UTC 06:00 을 옮기지 않고 KST 06:00 으로 읽어도 같은
    # 답이 나와서, 변환을 재는 것은 바로 아래 사례다.
    pytest.param("KR", datetime(2026, 9, 17, 6, 0, tzinfo=UTC), _kst(2026, 9, 17, 16, 0),
                 id="KR-UTC-06시는-KST-15시-같은날"),
    # UTC 07:00 = KST 16:00 정각 → 다음 평일. KST 07:00 으로 잘못 읽으면 같은 날.
    pytest.param("KR", datetime(2026, 9, 17, 7, 0, tzinfo=UTC), _kst(2026, 9, 18, 16, 0),
                 id="KR-UTC-07시는-KST-초기화-정각이라-다음날"),
    # naive 15:00 을 UTC 나 ET 로 읽으면 KST 로는 다음 날이 되어 구별된다.
    pytest.param("KR", datetime(2026, 9, 17, 15, 0), _kst(2026, 9, 17, 16, 0),
                 id="KR-naive는-KST로"),
    # 한국에 미국 캘린더를 쓰면 여기서 11-27 로 밀린다.
    pytest.param("KR", _kst(2026, 11, 26, 15, 0), _kst(2026, 11, 26, 16, 0),
                 id="KR-미국-추수감사절에도-초기화"),
]


@pytest.mark.parametrize("market, now, expected", _CASES)
def test_known_boundaries(market, now, expected):
    got = mc.next_close_reset(market, now)

    assert got.tzinfo is not None and got.utcoffset() is not None, (
        f"next_close_reset({market!r}, {now!r}) returned a naive datetime {got!r} -- "
        "the cache compares it with aware clocks and a naive expiry either raises "
        "or is read in whatever zone the reader assumes. (시간대 없는 만료 시각)"
    )
    assert got == expected, (
        f"next_close_reset({market!r}, {now.isoformat()}) = {got.isoformat()} "
        f"({got.astimezone(_TZ[market]).isoformat()} local), expected "
        f"{expected.isoformat()} -- the per-ticker AI view would expire at the "
        "wrong moment and nothing else would notice. (초기화 경계가 어긋났다)"
    )


# ── 성질 — 창을 훑는다 ────────────────────────────────────────────────────────

_WINDOWS = [
    # 추수감사절(목) · 성탄(금) · 신정(금) — 휴장이 주말과 붙는 연말.
    pytest.param("US", date(2026, 11, 20), date(2027, 1, 8), id="US-연말연시"),
    # 2026-03-08(일) 02:00 EST → 03:00 EDT.
    pytest.param("US", date(2026, 3, 5), date(2026, 3, 11), id="US-서머타임-시작"),
    # 2026-11-01(일) 02:00 EDT → 01:00 EST. 01:00~02:00 이 두 번 온다.
    pytest.param("US", date(2026, 10, 29), date(2026, 11, 4), id="US-서머타임-해제"),
    # 한국 창에는 한국 공휴일을 넣지 않는다. 한국이 공휴일에도 초기화하는 것은
    # 계약이 **허용하는** 부작용이지 요구가 아니다 — 여기서 고정하면 나중에
    # 한국 휴장일을 미리 아는 방법이 생겼을 때 개선을 막는다. 대신 미국
    # 추수감사절을 품게 해 "한국에 미국 캘린더" 를 잡는다.
    pytest.param("KR", date(2026, 11, 16), date(2026, 12, 18), id="KR-미국-추수감사절을-품은-창"),
]


def _instants(market: str, start: date, end: date, delay: int) -> list[datetime]:
    """창 안의 30분 간격(UTC) + 창 안 모든 초기화 시각과 그 ±1µs."""
    tz = _TZ[market]
    t = datetime.combine(start, time(0), tzinfo=tz).astimezone(UTC)
    stop = datetime.combine(end + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
    out = []
    while t < stop:
        out.append(t)
        t += timedelta(minutes=30)
    d = start
    while d <= end:
        if _is_session(market, d):
            r = _reset_on(market, d, delay).astimezone(UTC)
            out += [r - timedelta(microseconds=1), r, r + timedelta(microseconds=1)]
        d += timedelta(days=1)
    return out


def _representations(market: str, t: datetime) -> dict[str, datetime]:
    """같은 순간을 호출자가 넘길 법한 네 가지 모양으로.

    naive 는 시장 현지 벽시계에서 `fold` 를 그대로 둔 채 시간대만 뗀다 —
    서머타임 해제로 두 번 오는 01:30 도 같은 순간을 가리키게 하려는 것이다.
    """
    local = t.astimezone(_TZ[market])
    return {
        "utc": t,
        "market-tz": local,
        "other-market-tz": t.astimezone(_OTHER_TZ[market]),
        "naive-local": local.replace(tzinfo=None),
    }


@pytest.mark.parametrize("delay", [None, 0, 90], ids=["기본-30분", "delay-0", "delay-90"])
@pytest.mark.parametrize("market, start, end", _WINDOWS)
def test_properties_over_a_window(market, start, end, delay):
    """모든 표본에서 다섯 성질이 선다.

      ① 같은 순간이면 어떤 모양으로 넘겨도 같은 결과 (시간대 붙은 값)
      ② 결과 > now — 엄격히
      ③ 결과를 그 시장 시간대로 보면 마감 + delay 정각
      ④ 결과 날짜가 세션일 (미국 NYSE 거래일, 한국 평일)
      ⑤ now 와 결과 사이에 **더 이른 유효 초기화가 없다** — 하루씩 훑는다

    ②③④ 만으로는 "다음 거래일을 건너뛰고 그다음 것" 도 통과한다. ⑤ 가 그걸
    막는다. `delay` 를 바꿔 도는 이유는 인자를 무시하고 30 을 박은 구현을
    기본값 검사로는 볼 수 없어서다.
    """
    assert _FACTS_COVER[0] <= start and end + timedelta(days=10) <= _FACTS_COVER[1], (
        f"window {start}..{end} runs past the holiday facts in this file "
        f"({_FACTS_COVER[0]}..{_FACTS_COVER[1]}) -- extend _US_HOLIDAYS first, or "
        "an unknown holiday is read as a session day. (창이 휴장일 사실 밖이다)"
    )
    minutes = 30 if delay is None else delay
    kwargs = {} if delay is None else {"delay_minutes": delay}
    tz = _TZ[market]
    wall = (datetime.combine(date(2000, 1, 3), _CLOSE[market]) + timedelta(minutes=minutes)).time()

    problems: list[str] = []
    instants = _instants(market, start, end, minutes)
    for t in instants:
        got = {shape: mc.next_close_reset(market, value, **kwargs)
               for shape, value in _representations(market, t).items()}

        naive = [shape for shape, r in got.items() if r.tzinfo is None or r.utcoffset() is None]
        if naive:
            problems.append(f"① {t.isoformat()}: naive result for {naive}")
            continue
        r = got["utc"]
        differ = {shape: x.isoformat() for shape, x in got.items() if x != r}
        if differ:
            problems.append(f"① {t.isoformat()}: utc→{r.isoformat()} but {differ}")

        if not r > t:
            problems.append(f"② {t.isoformat()}: result {r.isoformat()} is not after now")

        local = r.astimezone(tz)
        if local.time() != wall:
            problems.append(f"③ {t.isoformat()}: {local.isoformat()} is not {wall} local")

        if not _is_session(market, local.date()):
            problems.append(f"④ {t.isoformat()}: {local.date()} ({local:%a}) is not a session day")

        d = t.astimezone(tz).date()
        while d <= local.date():
            if _is_session(market, d):
                c = _reset_on(market, d, minutes)
                if t < c < r:
                    problems.append(
                        f"⑤ {t.isoformat()}: skipped {c.isoformat()} and returned {r.isoformat()}"
                    )
                    break
            d += timedelta(days=1)

    assert instants, "no samples -- the window produced nothing to check"
    assert not problems, (
        f"{len(problems)} violation(s) over {len(instants)} instants "
        f"({market} {start}..{end}, delay={minutes}):\n  "
        + "\n  ".join(problems[:12])
        + ("\n  ..." if len(problems) > 12 else "")
    )


# ── 한국은 관측 캘린더를 기다리지 않는다 ────────────────────────────────────────

def test_kr_resets_today_before_the_observed_calendar_knows_today(monkeypatch):
    """한국 관측 캘린더(`_krx_trading_days`)는 **첫 체결 전에는 오늘을 모른다.**

    그걸로 세션일을 판단하면 오전에 만든 뷰의 만료가 오늘 16:00 을 건너뛰어
    내일 이후로 밀린다 — 하루 넘게 같은 뷰가 나간다. 계약이 한국을 평일로만
    보는 이유가 이것이다 (docstring). 관측 캘린더를 오늘 첫 체결 전 상태로
    만들어 두고 오늘 초기화가 살아 있는지 본다.
    """
    today = date(2026, 9, 17)          # 목요일
    seen = frozenset(
        today - timedelta(days=i) for i in range(1, 400)
        if (today - timedelta(days=i)).weekday() < 5
    )
    monkeypatch.setattr(mc, "_krx_trading_days", lambda: seen)

    # 전제 — 관측 캘린더는 정말로 오늘을 모른다. 이게 참이 아니면 아래 단언은
    # 관측 캘린더를 쓰는 구현도 통과시켜 아무것도 재지 않는다.
    assert mc.is_kr_trading_day(today) is False

    got = mc.next_close_reset("KR", _kst(2026, 9, 17, 8, 0))

    assert got == _kst(2026, 9, 17, 16, 0), (
        f"at 08:00 KST on a weekday the reset is {got.isoformat()}, not today 16:00 -- "
        "the observed KRX calendar does not know today until the first trade prints, "
        "so a view built this morning outlives today's close. "
        "(관측 캘린더를 기다리면 오늘 초기화를 건너뛴다)"
    )


# ── 시장 인자 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("market", ["JP", "", "USA", None])
def test_unknown_market_is_refused_rather_than_read_as_us(market):
    """모르는 시장을 미국으로 읽으면 그 시장의 캐시가 미국 시각에 만료된다 (§1.1)."""
    with pytest.raises(ValueError):
        mc.next_close_reset(market, _et(2026, 9, 17, 12, 0))


def test_every_market_the_app_defines_has_a_reset():
    """`markets.MARKETS` 에 시장이 늘면 여기도 따라와야 한다.

    `market_param` 을 통과한 값은 전부 `MARKETS` 의 키다. 그중 하나라도
    ValueError 면 그 시장의 최적화 요청은 캐시 만료를 계산하다 죽는다.
    """
    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    for code in MARKETS:
        got = mc.next_close_reset(code, now)
        assert got > now, f"{code}: {got!r} is not after {now!r}"


def test_without_now_it_reads_the_clock(monkeypatch):
    """`now` 를 안 주면 지금 시각으로 — 캐시 호출자는 대개 이 모양으로 부른다.

    고정한 시각은 2026 추수감사절 주간이라, 그 주가 아닌 날 기본 경로가 이
    시계 대신 진짜 시계(또는 기계의 naive 현지 시각)를 읽으면 결과가 달라진다.
    """
    frozen = _et(2026, 11, 25, 17, 0)          # = KST 11-26(목) 07:00
    monkeypatch.setattr(mc, "now_et", lambda: frozen)

    assert mc.next_close_reset("US") == _et(2026, 11, 27, 16, 30)
    assert mc.next_close_reset("KR") == _kst(2026, 11, 26, 16, 0)
