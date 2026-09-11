"""
리포트가 사용자 사이를 넘어가지 않는가 (§1.2).

`reports_repo` 는 "본인 것만" 을 여러 번 명시한다 — `list_reports` 는
*"scope 와 무관하게 작성자 본인 것만"*, `get_report_content` 는 *"반드시
소유자 검사를 거친다 ... 파일명은 추측 가능하므로"*. 둘 다 실제로 재 본 적이
없다.

## 실DB 를 쓴다

`ON CONFLICT(filename)`, `user_id=%s OR scope='shared'`, TTL 비교, JSONB
`metadata->>'model_tier'` — 전부 **SQL 이 하는 일**이다. 커서를 모킹하면 내가
SQL 을 어떻게 읽었는지만 검증된다 (§6). 붙는 DB 가 이 창 것인지는 `live_db`
가 확인한다.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from backend.db import reports_repo as rr
from backend.db import users_repo as ur

_PREFIX = "__TEST_R_"
ALICE = f"{_PREFIX}alice"
BOB = f"{_PREFIX}bob"


@pytest.fixture
def two_users(live_db):
    """두 사용자와, 이 파일이 만든 행만 지우는 정리."""
    from backend.db import get_conn

    def purge():
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM reports WHERE LEFT(user_id, %s) = %s",
                        (len(_PREFIX), _PREFIX))
            cur.execute("DELETE FROM analysis_cache WHERE LEFT(user_id, %s) = %s",
                        (len(_PREFIX), _PREFIX))
            # 공용 리포트는 작성자가 누구든 이 파일이 만든 파일명으로 지운다.
            cur.execute("DELETE FROM reports WHERE LEFT(filename, %s) = %s",
                        (len(_PREFIX), _PREFIX))
            cur.execute("DELETE FROM users WHERE LEFT(id, %s) = %s",
                        (len(_PREFIX), _PREFIX))

    purge()
    for uid in (ALICE, BOB):
        assert ur.upsert_user(uid, email=f"{uid}@example.com") is True
    yield
    purge()


def _rows(filename):
    from backend.db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id, content FROM reports WHERE filename=%s",
                    (filename,))
        return cur.fetchall()


# ── 파일명이 사용자를 가르지 않는다 ────────────────────────────────────────────
#
# `reports.filename` 은 **전역 UNIQUE** 인데 라우터가 만드는 이름에는 사용자
# 식별자가 없다:
#
#     daily_brief_{date}.md            reports.py:122
#     lens_{ticker}_{date}.md          reports.py:176
#     macro_{date}_{event_slug}.json   macro.py:150
#
# 그래서 같은 날 두 번째 사용자의 저장이 첫 번째 사용자의 행을 덮는다.
# `ON CONFLICT(filename) DO UPDATE` 가 `user_id` 는 안 바꾸므로, 행은 첫
# 사용자 것으로 남고 **내용만 두 번째 사용자 것이 된다.**
#
# 일일 브리핑은 그 사용자의 보유·손익을 담는다. `macro_scenario` 는 코드가
# 스스로 *"프롬프트 내용 자체가 사생활일 수 있으므로 scope='private'"* 라고
# 적어 둔 것이다.

def test_the_filename_pattern_carries_no_user_identifier(two_users):
    """전제 — 라우터가 만드는 파일명에 사용자 식별자가 없다.

    이 전제가 깨지면(uid 를 넣도록 바뀌면) 아래 검사는 **일어날 수 없는
    충돌**을 재는 것이 된다. 그때는 이 파일을 통째로 지워야 한다.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "routers" / "reports.py"
    names = re.findall(r'filename\s*=\s*f"([^"]+)"', src.read_text(encoding="utf-8"))
    assert names, "라우터에서 파일명 생성을 못 찾았다 — 이 검사의 전제가 없다"

    with_uid = [n for n in names if "uid" in n or "user" in n]
    assert not with_uid, (
        f"filenames now include a user identifier ({with_uid}) -- the collision "
        "this file pins can no longer happen, so delete this file."
    )


def test_one_users_report_never_lands_in_another_users_row(two_users):
    """같은 날 같은 종류를 만들어도 서로의 내용을 받지 않는다.

    지금은 앨리스의 '과거 레포트' 목록에 밥의 일일 브리핑 **내용**이 뜨고,
    밥의 리포트는 목록에서 사라진다. 브리핑에는 그 사용자의 보유와 손익이
    들어 있다.
    """
    filename = "daily_brief_2026-09-11.md"
    rr.save_report(filename, "앨리스의 보유·손익", report_type="daily_brief",
                   user_id=ALICE, scope="private")
    rr.save_report(filename, "밥의 보유·손익", report_type="daily_brief",
                   user_id=BOB, scope="private")

    assert rr.get_report_content(filename, ALICE) == "앨리스의 보유·손익", (
        "Alice's report now holds Bob's content -- the row is keyed by "
        "filename alone, and the daily brief contains that user's holdings "
        "and P&L. (앨리스 목록에 밥의 자산 내역이 뜬다.)"
    )
    assert [n["name"] for n in rr.list_reports(BOB)] == [filename], (
        "Bob's report vanished from his own list -- the row kept Alice as "
        "owner, so Bob can neither see nor read what he generated."
    )


def test_different_filenames_keep_both_reports(two_users):
    """대조군 — 파일명이 다르면 둘 다 남는다.

    없으면 위 검사들은 "저장이 아예 안 된다" 는 상태로도 성립한다.
    """
    rr.save_report(f"{_PREFIX}a.md", "앨리스 것", user_id=ALICE, scope="private")
    rr.save_report(f"{_PREFIX}b.md", "밥 것", user_id=BOB, scope="private")

    assert [n["name"] for n in rr.list_reports(ALICE)] == [f"{_PREFIX}a.md"]
    assert [n["name"] for n in rr.list_reports(BOB)] == [f"{_PREFIX}b.md"]


# ── 덮어쓴 공용 리포트의 작성자는 **먼저 만든 사람**으로 남는다 ───────────────
#
# `ON CONFLICT ... DO UPDATE` 가 `user_id` 는 안 바꾼다. 그래서 같은 키로
# 덮어쓰면 행은 첫 작성자 것으로 남고 내용만 두 번째 것이 된다.
#
# **`user_id=EXCLUDED.user_id` 를 넣고 싶어진다** — "더 정확한 값" 으로
# 보이기 때문이다. 넣지 않는 이유 둘:
#
#   ① 넣으면 첫 작성자의 목록에서 그 항목이 **사라진다**(`list_reports` 는
#      작성자로 거른다). 지금은 항목이 남되 내용이 남의 것이다. 둘 다
#      틀렸지만 **후자는 본인이 눈치챌 수 있고 전자는 못 챈다.** 틀렸을 때
#      누가 알아차리는가가 기준이다 (§1.3).
#   ② `user_id` 는 소유자가 아니라 **작성자**다. `list_reports` 가 그것을
#      "내가 무엇을 분석했는가" 로 쓰고, 탈퇴 시 `__deleted__` 로 익명화하는
#      것도 작성자 의미다. 마지막에 덮은 사람으로 바꾸면 그 의미가 깨진다.
#
# 제대로 고치려면 공용 리포트를 목록에서 작성자로 묶지 않거나, 작성자를
# 여럿 담아야 한다. 그 전까지 지금 동작을 여기 못 박는다 — 근거가 주석뿐이면
# 다음 사람이 ①의 유혹에 그대로 넘어간다.

def test_overwriting_a_shared_report_keeps_the_first_author(two_users):
    """덮어써도 작성자는 먼저 만든 사람이다.

    바꾸면 첫 작성자의 목록에서 항목이 조용히 사라진다 — 신호가 없는 쪽이
    더 나쁘다. 위 설명 참고.
    """
    fn = f"{_PREFIX}shared_same_tier.md"
    for uid, body in ((ALICE, "앨리스가 먼저"), (BOB, "밥이 나중")):
        rr.save_report(fn, body, report_type="equity_research",
                       metadata={"model_tier": "basic"}, user_id=uid,
                       scope="shared", subject_key="AAPL")

    assert _rows(fn) == [(ALICE, "밥이 나중")], (
        f"the author changed on overwrite: {_rows(fn)} -- making the last "
        "writer the author removes the entry from the first author's list "
        "with no signal at all. See the note above before changing this."
    )
    assert [n["name"] for n in rr.list_reports(ALICE)] == [fn], (
        "첫 작성자의 목록에서 항목이 사라졌다 — 그게 이 검사가 막는 것이다"
    )


# ── 소유자 검사 (§1.2) ─────────────────────────────────────────────────────────

def test_a_private_report_is_not_readable_by_another_user(two_users):
    """남의 개인 리포트는 파일명을 알아도 못 읽는다.

    파일명은 날짜와 티커로 만들어져 **추측 가능하다**. 소유자 검사가 없으면
    그대로 IDOR 다 — 실제로 그 상태였다.
    """
    fn = f"{_PREFIX}private.md"
    rr.save_report(fn, "앨리스만의 것", user_id=ALICE, scope="private")

    assert rr.get_report_content(fn, BOB) is None, (
        "another user read a private report by guessing its filename (IDOR) -- "
        "filenames are built from dates and tickers. (§1.2)"
    )
    assert rr.get_report_content(fn, ALICE) == "앨리스만의 것", "주인도 못 읽는다"


def test_a_shared_report_is_readable_by_anyone(two_users):
    """공용 리포트는 누가 만들었든 읽을 수 있다 — **의도된 동작**이다.

    종목·산업 리서치는 분석 대상이 같으면 결과가 같으므로 최초 1인이 만든
    것을 전체가 쓴다. 여기서 막으면 중복 생성 비용이 그대로 돌아온다.
    """
    fn = f"{_PREFIX}shared.md"
    rr.save_report(fn, "공용 리서치", user_id=ALICE, scope="shared",
                   subject_key="AAPL", report_type="equity_research")

    assert rr.get_report_content(fn, BOB) == "공용 리서치"


def test_someone_elses_shared_report_is_not_in_my_list(two_users):
    """목록에는 **내가 만든 것**만 나온다 — 공용이라도.

    목록은 "내가 무엇을 분석했는가" 의 기록이다. 남의 공용 리포트가 섞이면
    남의 활동이 노출된다. 읽기는 조회 시점에 열리고, 목록이 그 통로는 아니다.
    """
    fn = f"{_PREFIX}shared.md"
    rr.save_report(fn, "공용 리서치", user_id=ALICE, scope="shared",
                   subject_key="AAPL", report_type="equity_research")

    assert rr.list_reports(BOB) == [], (
        f"someone else's activity showed up in Bob's list: {rr.list_reports(BOB)}"
    )
    assert [n["name"] for n in rr.list_reports(ALICE)] == [fn], "주인 목록에는 있어야 한다"


def test_a_missing_user_id_lists_nothing(two_users):
    """사용자를 모르면 아무것도 안 준다 — 전부 주지 않는다.

    `list_reports` 는 `require_uid` 를 거치지 않는다. 열려 있으면 인증이
    빠진 호출 하나가 전 사용자 목록을 내보낸다.
    """
    rr.save_report(f"{_PREFIX}a.md", "앨리스 것", user_id=ALICE, scope="private")

    assert rr.list_reports(None) == [], "사용자 없이 목록이 나왔다"
    assert rr.list_reports("") == [], "빈 사용자로 목록이 나왔다"


def test_reading_without_a_user_id_returns_nothing(two_users):
    """내용 조회도 같다 — 사용자가 없으면 닫는다."""
    fn = f"{_PREFIX}shared.md"
    rr.save_report(fn, "공용 리서치", user_id=ALICE, scope="shared",
                   subject_key="AAPL", report_type="equity_research")

    assert rr.get_report_content(fn, None) is None
    assert rr.get_report_content(fn, "") is None


# ── 공용 리포트 재사용 규칙 ────────────────────────────────────────────────────

def _shared(subject, *, tier=None, market="US", age_hours=0, uid=ALICE):
    """공용 리포트 하나. `age_hours` 만큼 과거로 밀어 둔다."""
    from backend.db import get_conn
    fn = f"{_PREFIX}{subject}_{tier}_{market}_{age_hours}.md"
    meta = {} if tier is None else {"model_tier": tier}
    rr.save_report(fn, f"{subject} 리서치", report_type="equity_research",
                   metadata=meta, user_id=uid, scope="shared",
                   subject_key=subject, market=market)
    if age_hours:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE reports SET created_at = NOW() - (%s * INTERVAL '1 hour') "
                        "WHERE filename=%s", (age_hours, fn))
    return fn


def test_a_deep_request_does_not_get_a_basic_report(two_users):
    """심층을 요청했는데 기본 리포트를 주면 안 된다.

    등급이 다르면 쓰는 모델과 분석 깊이가 달라 결과물의 성격이 다르다.
    심층 비용을 낸 사용자가 기본 결과를 받으면 그 사실이 화면에 안 보인다.
    """
    _shared("AAPL", tier="basic")

    assert rr.find_fresh_shared_report("equity_research", "AAPL",
                                       model_tier="deep") is None, (
        "a deep request was served a basic report -- the user paid for a "
        "different kind of analysis and cannot tell from the screen."
    )


def test_a_basic_request_does_not_get_a_deep_report(two_users):
    """반대 방향도 막는다 — 등급이 정확히 일치할 때만 재사용한다."""
    _shared("AAPL", tier="deep")

    assert rr.find_fresh_shared_report("equity_research", "AAPL",
                                       model_tier="basic") is None


def test_a_matching_tier_is_reused(two_users):
    """대조군 — 등급이 같으면 재사용한다.

    없으면 위 둘은 "아무것도 재사용하지 않는다" 는 구현으로도 통과하고,
    그러면 공유의 목적(중복 생성·비용 방지)이 통째로 사라진다.
    """
    _shared("AAPL", tier="deep")

    got = rr.find_fresh_shared_report("equity_research", "AAPL", model_tier="deep")
    assert got and got["content"] == "AAPL 리서치"


def test_an_old_report_without_a_tier_counts_as_basic(two_users):
    """등급이 안 적힌 옛 리포트는 basic 으로 본다 — 코드가 그렇게 적고 있다."""
    _shared("AAPL", tier=None)

    assert rr.find_fresh_shared_report("equity_research", "AAPL",
                                       model_tier="basic") is not None
    assert rr.find_fresh_shared_report("equity_research", "AAPL",
                                       model_tier="deep") is None


def test_an_unknown_tier_is_silently_treated_as_basic(two_users):
    """지금 동작을 적어 둔다 — 모르는 등급은 조용히 basic 이 된다.

    `tier = model_tier if model_tier in ("basic","deep") else "basic"`.
    세 번째 등급이 생기면 그 요청이 **기본 리포트를 받고**, 받은 쪽은
    등급이 내려간 줄 모른다. 지금은 등급이 둘뿐이라 도달하지 않지만,
    적어 두지 않으면 등급을 추가하는 사람이 이 줄을 안 본다.
    """
    _shared("AAPL", tier="basic")

    got = rr.find_fresh_shared_report("equity_research", "AAPL", model_tier="premium")
    assert got is not None, (
        f"an unknown tier no longer falls back to basic ({got}) -- if that "
        "changed on purpose, delete this note."
    )


# ── 공용 리포트: 등급이 저장 키에 들어가 있다 ──────────────────────────────────
#
# `find_fresh_shared_report` 는 `model_tier` 를 **신원의 일부로** 다룬다 —
# 심층을 요청한 사람에게 기본을 주지 않는다. 한동안 저장 키는
# `(market, filename)` 뿐이었고 파일명 `lens_{ticker}_{date}.md` 에는 등급이
# 없어서, **조회가 다르다고 보는 둘이 한 행을 썼다.** 그래서 남의 기본
# 리포트가 내 심층 리포트를 덮었다 — 심층은 하루 한 번 제한이 걸리는
# 기능이라 그날치 결과가 사라졌다.
#
# 지금은 공용 유니크 인덱스가 `COALESCE(metadata->>'model_tier','basic')` 을
# 포함한다. **조회가 이미 쓰던 식을 저장이 그대로 쓴다** — 신원의 정의가
# 한 곳에만 있다. 파일명에 등급을 넣는 안은 프론트가 파일명을 파싱해
# 제목을 만들기 때문에 버려졌다(표시 문자열에 정체성을 심는 형태가 된다).

def test_a_basic_report_does_not_overwrite_a_deep_one(two_users):
    """등급이 다르면 서로를 덮지 않는다 — 조회가 그 둘을 다르게 보므로."""
    fn = "lens_AAPL_2026-09-11.md"          # 라우터가 만드는 실제 형식
    rr.save_report(fn, "심층 리서치", report_type="equity_research",
                   metadata={"model_tier": "deep"}, user_id=ALICE,
                   scope="shared", subject_key="AAPL")
    rr.save_report(fn, "기본 리서치", report_type="equity_research",
                   metadata={"model_tier": "basic"}, user_id=BOB,
                   scope="shared", subject_key="AAPL")

    assert rr.find_fresh_shared_report("equity_research", "AAPL",
                                       model_tier="deep") is not None, (
        "a basic report overwrote a deep one -- the lookup treats the two "
        "tiers as different products, so they must not share one row. Deep "
        "runs are quota-limited, so someone else's basic request destroys "
        "that day's deep result."
    )


def test_a_shared_report_does_not_cross_markets(two_users):
    """한국 리포트가 미국 요청에 나오면 안 된다 (§1.1).

    같은 티커가 두 시장에 있을 수 있고, 통화도 지표도 다르다.
    """
    _shared("AAPL", tier="basic", market="KR")

    assert rr.find_fresh_shared_report("equity_research", "AAPL",
                                       model_tier="basic", market="US") is None, (
        "a KR shared report was served to a US request -- currencies and "
        "benchmarks differ. (§1.1)"
    )
    assert rr.find_fresh_shared_report("equity_research", "AAPL",
                                       model_tier="basic", market="KR") is not None


def test_an_expired_report_is_not_reused(two_users):
    """유효시간이 지난 공용 리포트는 안 준다.

    묵은 리서치를 오늘 것으로 내보내면 사용자는 그 차이를 못 본다.
    화면에는 나이가 아니라 내용만 뜬다.
    """
    _shared("AAPL", tier="basic", age_hours=100)   # equity_research TTL = 24h

    assert rr.find_fresh_shared_report("equity_research", "AAPL",
                                       model_tier="basic") is None


def test_a_fresh_report_is_reused(two_users):
    """대조군 — 유효시간 안이면 준다. 나이도 같이 알려준다."""
    _shared("AAPL", tier="basic", age_hours=2)

    got = rr.find_fresh_shared_report("equity_research", "AAPL", model_tier="basic")
    assert got is not None
    assert 1.5 < got["age_hours"] < 2.5, f"age_hours={got['age_hours']}"


# ── 분석 캐시도 사용자별이다 ───────────────────────────────────────────────────

def test_the_analysis_cache_does_not_cross_users(two_users):
    """한 사용자의 분석 결과가 다른 사용자에게 안 간다.

    캐시 키는 프롬프트·종목으로 만들어져 두 사용자가 같은 키를 쓸 수 있다.
    결과는 그 사용자의 포트폴리오에 종속되므로 넘어가면 남의 자산을 본다.
    """
    rr.save_analysis("feedback", "same-key", {"text": "앨리스 결과"}, user_id=ALICE)

    assert rr.get_analysis("feedback", "same-key", user_id=BOB) is None, (
        "one user's analysis was served to another -- cache keys are built "
        "from prompts and tickers, so collisions are normal. (§1.2)"
    )
    assert rr.get_analysis("feedback", "same-key", user_id=ALICE) == {"text": "앨리스 결과"}


def test_an_expired_analysis_is_not_returned(two_users):
    """만료된 캐시는 안 준다. 대조군으로 살아 있는 것은 준다.

    만료 시각은 **SQL 로 직접 밀어 둔다.** `ttl_hours` 로 만들면 아래
    xfail 이 가리키는 시계 문제에 걸려, 이 검사가 시간대에 따라 다른 답을
    낸다 — 실제로 처음에 그렇게 썼고 KST 에서만 빨갰다.
    """
    from backend.db import get_conn

    rr.save_analysis("feedback", "gone", {"text": "옛 결과"}, user_id=ALICE)
    rr.save_analysis("feedback", "alive", {"text": "새 결과"}, user_id=ALICE)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE analysis_cache SET expires_at = NOW() - INTERVAL '1 hour' "
                    "WHERE user_id=%s AND cache_key='gone'", (ALICE,))

    assert rr.get_analysis("feedback", "gone", user_id=ALICE) is None, (
        "an expired analysis was served -- the screen shows the content, not "
        "its age, so the user cannot tell it is stale."
    )
    assert rr.get_analysis("feedback", "alive", user_id=ALICE) == {"text": "새 결과"}


# ── 캐시 수명이 **앱 프로세스의 시계**에 달려 있다 ─────────────────────────────
#
# `save_analysis` 는 `datetime.now() + timedelta(hours=ttl)` 로 만료를 만든다.
# 그건 **시간대 정보가 없는 로컬 시각**이고, `TIMESTAMPTZ` 컬럼에 들어가면
# 포스트그레스가 **세션 시간대로** 읽는다. 만료 검사(`expires_at > NOW()`)는
# DB 시계로 하므로 둘의 기준이 다르다.
#
# 실측 (이 창): DB `SHOW timezone` = Etc/UTC, DB NOW() = 07:02+00,
# 파이썬 `datetime.now()` = 16:02 (naive). 즉 로컬에서 `ttl_hours=24` 는
# 실제로 **33시간**이다. Cloud Run 컨테이너는 UTC 라 운영에서는 맞는다 —
# **개발하는 곳에서만 틀리는** 형태다. TZ 가 다른 곳에 배포하면 조용히 바뀐다.
#
# **고쳤다.** 만료를 SQL 에 맡긴다:
# `expires_at = NOW() + (%s * INTERVAL '1 hour')`. 기준이 하나가 됐다.
# 고친 뒤 같은 머신에서 다시 재니 24.00시간(오차 -0.00)이다. xfail(strict) 이
# XPASS 로 뒤집혀 마커를 지웠다 — 원장이 스스로 회수를 요구한 형태다.
#
# 위 실측 기록은 남겨 둔다. 다음에 누가 "파이썬에서 만료를 만드는 게 읽기
# 쉽다" 며 되돌릴 때, **운영에서는 우연히 맞았다**는 사실이 그 자리에 있어야
# 한다. 기준이 둘인 채로 맞는 것은 고쳐진 것이 아니다.

def test_the_cache_lifetime_does_not_depend_on_the_process_clock(two_users):
    """프로세스 시계가 틀려도 방금 저장한 캐시는 살아 있어야 한다.

    시계를 2000년으로 돌려 두고 **24시간짜리**를 저장한다. 수명의 기준이
    DB 시계라면 여전히 읽혀야 한다. 지금은 프로세스 시계가 기준이라 이미
    만료된 행이 저장된다.

    이 방식이라 **시간대와 무관하게 결정적**이다 — 실제 오프셋으로 재면
    UTC 머신에서는 통과하고 KST 머신에서는 실패해서, 통과가 "고쳐졌다" 가
    아니라 "여기서는 안 보인다" 를 뜻하게 된다.
    """
    from unittest import mock

    # create=True 로 건다. 고친 뒤 save_analysis 는 프로세스 시계를 **아예
    # 안 읽으므로** 모듈에 `datetime` 이 없다. 그게 이 검사가 원하는 상태다 —
    # 대상이 없다고 실패하면 "고쳐져서" 빨개지는 검사가 된다. 그래도 패치는
    # 남겨 둔다: 누가 파이썬 시계를 다시 들여오면 그 순간 이 검사가 잡는다.
    with mock.patch.object(rr, "datetime", create=True) as fake:
        fake.now.return_value = datetime(2000, 1, 1)
        rr.save_analysis("feedback", "clock", {"text": "결과"}, ttl_hours=24,
                         user_id=ALICE)

    assert rr.get_analysis("feedback", "clock", user_id=ALICE) == {"text": "결과"}, (
        "a cache entry written with a 24h TTL was already dead -- its lifetime "
        "is anchored on the app process clock, not the database clock, so a "
        "container in another timezone silently changes every TTL. "
        "(수명 기준이 두 개다.)"
    )


def test_the_cache_survives_with_a_correct_clock(two_users):
    """대조군 — 시계를 안 건드리면 24시간짜리는 읽힌다.

    없으면 위 검사가 "캐시가 아예 안 읽힌다" 는 상태로도 성립한다.
    """
    rr.save_analysis("feedback", "normal", {"text": "결과"}, ttl_hours=24,
                     user_id=ALICE)

    assert rr.get_analysis("feedback", "normal", user_id=ALICE) == {"text": "결과"}
