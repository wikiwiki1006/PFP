"""
잡 취소가 **실제로** 작업을 멈추는지 검증한다.

예전에는 취소가 잡 상태만 바꿨다. 작업 스레드는 그걸 모른 채 LLM 호출을
끝까지 진행했고, 리포트를 저장하고 텔레그램까지 보낸 뒤에야 취소를 확인했다.
화면에서만 멈춘 것처럼 보였을 뿐 비용과 시간은 그대로 나갔다.

여기서 지키려는 것은 세 가지다:
  · 취소 신호가 작업 함수까지 전달된다
  · LLM 응답을 받는 도중에도 끊긴다
  · 취소된 잡이 나중에 done 으로 되살아나지 않는다
"""
import pytest

from backend.services.job_store import JobCancelled, JobStore


# ── JobStore ────────────────────────────────────────────────────────────────

def test_cancel_raises_signal_for_worker():
    st = JobStore()
    st.set("j", {"status": "pending"}, owner="alice")
    token = st.cancel_token("j")

    assert token() is False
    st.cancel("j", owner="alice")
    assert token() is True, "취소했는데 작업 스레드가 신호를 못 받는다"


def test_other_user_cannot_cancel():
    st = JobStore()
    st.set("j", {"status": "pending"}, owner="alice")
    token = st.cancel_token("j")

    assert st.cancel("j", owner="bob") is None
    assert token() is False, "남이 내 잡을 멈출 수 있으면 안 된다"
    assert st.get("j", owner="alice")["status"] == "pending"


def test_cancelled_job_cannot_be_revived():
    """뒤늦게 끝난 스레드가 취소된 잡을 done 으로 덮어쓰면 안 된다."""
    st = JobStore()
    st.set("j", {"status": "pending"}, owner="alice")
    st.cancel("j", owner="alice")

    assert st.update_if("j", "pending", {"status": "done", "result": {}}) is False
    assert st.get("j", owner="alice")["status"] == "cancelled"


def test_evicted_job_counts_as_cancelled():
    """LRU 로 밀려난 잡은 결과를 돌려줄 곳이 없으니 계속 돌 이유도 없다."""
    st = JobStore(max_jobs=2)
    st.set("old", {"status": "pending"})
    token = st.cancel_token("old")
    st.set("a", {"status": "pending"})
    st.set("b", {"status": "pending"})

    assert token() is True


# ── 리포트 생성 ──────────────────────────────────────────────────────────────

@pytest.fixture
def rw(monkeypatch):
    """외부 호출(yfinance·Perplexity·LLM)을 가로챈 report_writer."""
    import backend.services.report_writer as mod

    calls: list[str] = []
    # 시장 인자가 추가돼 대역도 그 형태를 받아야 한다.
    monkeypatch.setattr(mod, "gather_equity_yfinance",
                        lambda t, market="US": (calls.append("yfinance"), ("Apple", "지표", {}))[1])
    monkeypatch.setattr(mod, "gather_equity_perplexity",
                        lambda *a, **k: (calls.append("perplexity"), "뉴스")[1])
    monkeypatch.setattr(mod, "_call_haiku",
                        lambda *a, **k: (calls.append("LLM"), "본문")[1])
    monkeypatch.setattr(mod, "_call_sonnet", mod._call_haiku)
    mod._calls = calls
    return mod


def test_report_runs_fully_without_cancel(rw):
    rw.write_equity_report("AAPL", "basic")
    assert rw._calls == ["yfinance", "perplexity", "LLM", "LLM"]


def test_report_stops_before_any_llm_call(rw):
    """이미 취소된 잡은 비용이 드는 호출을 한 번도 하지 않는다."""
    with pytest.raises(JobCancelled):
        rw.write_equity_report("AAPL", "basic", should_cancel=lambda: True)
    assert rw._calls == []


def test_report_stops_between_stages(rw):
    """수집이 끝난 뒤 취소하면 LLM 단계로 넘어가지 않는다."""
    n = {"i": 0}

    def cancel_after_gather():
        n["i"] += 1
        return n["i"] > 2

    with pytest.raises(JobCancelled):
        rw.write_equity_report("AAPL", "basic", should_cancel=cancel_after_gather)
    assert "LLM" not in rw._calls


# ── LLM 스트리밍 중단 ────────────────────────────────────────────────────────

def test_stream_aborts_midway(monkeypatch):
    """긴 응답을 받는 도중 취소하면 남은 조각을 기다리지 않고 연결을 끊는다.

    이 검사가 없으면 취소가 '다음 단계부터' 적용돼, 진행 중인 호출이 끝날
    때까지 수십 초를 그대로 기다리게 된다.
    """
    import backend.services.report_writer as mod

    consumed: list[int] = []
    closed = {"v": False}

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            closed["v"] = True      # with 이탈 = HTTP 연결 종료
            return False

        @property
        def text_stream(self):
            for i in range(100):
                consumed.append(i)
                yield "조각 "

        def get_final_message(self):
            class M:
                stop_reason = "end_turn"
            return M()

    class FakeClient:
        def __init__(self, **kw):
            self.messages = type("M", (), {"stream": lambda _s, **kw: FakeStream()})()

    monkeypatch.setattr(mod.anthropic, "Anthropic", FakeClient)

    n = {"i": 0}

    def cancel_after_few():
        n["i"] += 1
        return n["i"] > 6

    with pytest.raises(JobCancelled):
        mod._call_claude("m", "p", "s", 4096, should_cancel=cancel_after_few)

    assert len(consumed) < 100, "스트림을 끝까지 다 받았다 — 중단되지 않았다"
    assert closed["v"] is True, "연결이 닫히지 않았다"
