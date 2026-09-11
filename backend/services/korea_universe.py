"""
services/korea_universe.py
──────────────────────────
한국 상장 종목 목록과 신호 스캔용 유니버스(KOSPI 200 · KOSDAQ 150).

**전체 목록**은 KRX KIND 의 상장법인목록에서 받는다 (키 불필요, 2,800여 개).
종목 검색 자동완성이 이걸 쓴다.

**스캔 유니버스**는 시가총액 상위로 뽑는다. KRX 가 지수 구성종목을 여는
엔드포인트는 브라우저 세션을 요구해 서버에서 직접 못 받는다(요청하면 'LOGOUT'
만 돌아온다). 시총 상위는 지수 구성과 완전히 같지는 않지만(지수는 유동비율·
거래대금도 본다) 신호 스캔의 목적 — "볼 만한 대형주를 추린다" — 에는 충분하다.

시총 순위는 네이버 금융 시가총액 페이지에서 받는다. **이미 시총 내림차순으로
정렬돼 있어** 상위 N 개를 가지려면 앞쪽 몇 페이지만 읽으면 된다 — KOSPI 200 +
KOSDAQ 150 이 7 요청, 2초 미만이다. 종목마다 yfinance 로 시총을 물으면 2,700
종목에 한 시간 가까이 걸리는데, 그 경로는 폴백으로만 남겨 뒀다.
"""
from __future__ import annotations

import io
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_KIND_URL = (
    "http://kind.krx.co.kr/corpgeneral/corpList.do"
    "?method=download&searchType=13"
)

# common_cache 키
_ALL_KEY   = "kr_listed_all"        # 전체 상장 종목 (검색용)
_SCAN_KEY  = "kr_scan_universe"     # 시총 상위 (신호 스캔용)
_NAME_KEY  = "kr_name_map"          # 티커 → 종목명

KOSPI_TOP  = 200    # KOSPI 200 에 대응
KOSDAQ_TOP = 150    # KOSDAQ 150 에 대응

# 코넥스는 제외한다. 거래가 매우 얇아 기술적 신호가 의미를 갖기 어렵고,
# 검색에 섞이면 사용자가 원치 않는 종목을 고르게 된다.
_MARKET_SUFFIX = {"유가": ".KS", "코스닥": ".KQ"}


def fetch_listed_from_krx() -> list[dict]:
    """KRX KIND 에서 전체 상장 종목을 받아 [{ticker, name, market, sector}] 로.

    응답은 EUC-KR HTML 표다. 종목코드가 숫자로 파싱되면 앞자리 0 이 날아가므로
    문자열로 읽어 6자리로 채운다 ('005930' → 5930 이 되면 티커가 깨진다).
    """
    import pandas as pd
    import requests

    r = requests.get(_KIND_URL, timeout=30)
    r.raise_for_status()
    df = pd.read_html(io.BytesIO(r.content), encoding="euc-kr")[0]
    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)

    out: list[dict] = []
    for _, row in df.iterrows():
        suffix = _MARKET_SUFFIX.get(str(row.get("시장구분", "")).strip())
        if not suffix:
            continue                      # 코넥스 등 제외
        code = str(row["종목코드"]).strip()
        out.append({
            "ticker": f"{code}{suffix}",
            "name":   str(row.get("회사명", "")).strip(),
            "board":  str(row.get("시장구분", "")).strip(),
            "sector": str(row.get("업종", "")).strip(),
        })
    return out


def get_listed_all(refresh: bool = False) -> list[dict]:
    """전체 상장 종목. DB 캐시 우선, 없으면 KRX 에서 받아 저장(30일)."""
    from backend.db.market_cache import get_common, save_common

    if not refresh:
        cached = get_common(_ALL_KEY)
        if cached:
            return cached

    try:
        rows = fetch_listed_from_krx()
    except Exception as e:
        logger.error(f"KRX 상장목록 수집 실패: {e}")
        return get_common(_ALL_KEY) or []

    if rows:
        save_common(_ALL_KEY, rows, ttl_seconds=86400 * 30)
        logger.info(f"KRX 상장목록 {len(rows)}종목 저장")
    return rows


# 네이버 시총 순위 — **JSON API 를 쓴다. HTML 을 긁지 않는다.**
#
# 예전에는 `finance.naver.com/sise/sise_market_sum.naver` 를 정규식으로 파싱했다.
# 네이버가 그 주소를 `stock.naver.com` 으로 **302 리다이렉트**하도록 바꿨고,
# 그쪽은 JS 로 그리는 페이지라 정규식이 한 행도 못 잡는다. 결과:
#
#     .KS 목표 200 중 0종목만 수집
#     .KQ 목표 150 중 0종목만 수집
#
# 그래서 한국 신호 스캔 유니버스가 **비어 있었다** — `/timing` 화면의 한국
# 매매 신호에 후보가 하나도 없다. 로그는 남고 있었지만(§1.3 대로) 아무도
# 그 줄을 읽지 않았다. 오늘 네 번째 "외부 계약이 움직였다" 다
# (야후 미확정 종가, dividendYield 단위, pandas Series(None), 그리고 이것).
#
# JSON API 는 마크업 변경에 훨씬 덜 취약하다. 필드가 사라지면 KeyError 로
# 즉시 드러나지, 0행으로 조용히 성공하지 않는다.
_NAVER_MV = "https://m.stock.naver.com/api/stocks/marketValue/{board}"

# 네이버 보드 이름 → 야후 티커 접미사
_NAVER_BOARDS = (("KOSPI", ".KS", KOSPI_TOP), ("KOSDAQ", ".KQ", KOSDAQ_TOP))


def _is_common_stock(code: str) -> bool:
    """보통주만 통과. KRX 종목코드는 보통주가 0 으로 끝난다.

    우선주(삼성전자우 005935)·ETN 은 신호 스캔에서 뺀다. 우선주는 보통주와
    거의 같이 움직여 같은 종목이 두 번 잡히는 데다 거래가 얇아 기술적 신호의
    신뢰도가 떨어진다. KOSPI 200 지수도 대부분의 우선주를 넣지 않는다.
    """
    return code.endswith("0")


def fetch_top_by_marketcap() -> tuple[list[str], dict[str, str]]:
    """네이버 금융에서 시총 상위 종목을 받아 (야후 티커 목록, 티커→종목명).

    종목명을 같이 돌려주는 이유: KRX KIND 상장목록은 해외 IP 에서 403 을 낸다.
    Cloud Run(싱가포르)에서는 그 경로가 통째로 막혀 화면에 종목 코드만 남는다
    ('044490.KQ' 만 보면 무슨 회사인지 알 수 없다). 어차피 시총 페이지에
    이름이 같이 있으므로 여기서 챙겨 두면 별도 조회가 필요 없다.
    """
    import requests

    sess = requests.Session()
    # Referer 가 없으면 API 가 거부한다.
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 Chrome/120",
        "Referer": "https://m.stock.naver.com/",
    })

    universe: list[str] = []
    names: dict[str, str] = {}
    seen: set[str] = set()
    for board, suffix, want in _NAVER_BOARDS:
        picked: list[str] = []
        page = 1
        # 우선주를 걸러내면 한 페이지에서 100개를 다 못 채우므로 넉넉히 돈다.
        while len(picked) < want and page <= 8:
            try:
                r = sess.get(_NAVER_MV.format(board=board),
                             params={"page": page, "pageSize": 100}, timeout=20)
                r.raise_for_status()
                rows = r.json().get("stocks") or []
            except Exception as e:
                logger.warning("네이버 시총 %s/page%d 실패: %s", board, page, e)
                break
            if not rows:
                break
            for row in rows:
                code = str(row.get("itemCode") or "")
                name = str(row.get("stockName") or "").strip()
                # 코드나 이름이 없으면 응답 형태가 바뀐 것이다. 조용히 넘기면
                # 다시 "0종목만 수집" 으로 돌아가므로 무엇이 비었는지 남긴다.
                if len(code) != 6 or not name:
                    logger.warning("네이버 시총 %s: 예상 밖의 행 %r", board, list(row)[:6])
                    continue
                if _is_common_stock(code):
                    ticker = f"{code}{suffix}"
                    # 페이지 사이에 중복이 생긴다. 이 API 는 시총 내림차순으로
                    # **실시간 정렬**하므로, page 1 을 받고 page 2 를 받는 사이에
                    # 순위가 바뀐 종목이 양쪽에 들어온다. 운영 캐시가 350종목인데
                    # 고유는 348 이었고, 그 중복이 yfinance 프레임의 **중복 열**이
                    # 되어 매매신호 스캔을 20분마다 죽이고 있었다
                    # (close_df[c] 가 Series 가 아니라 DataFrame 이 된다).
                    if ticker in seen:
                        continue
                    seen.add(ticker)
                    picked.append(ticker)
                    names[ticker] = name
            page += 1
        if len(picked) < want:
            logger.warning("%s 목표 %d 중 %d종목만 수집", suffix, want, len(picked))
        universe += picked[:want]
    return universe, {t: n for t, n in names.items() if t in set(universe)}


def rebuild_scan_universe(max_probe: Optional[int] = None) -> dict:
    """시총 상위로 스캔 유니버스를 다시 만든다. **수집 작업에서만 호출한다.**

    종목당 1초가 넘는 조회라 전체를 훑으면 한 시간 가까이 걸린다. 요청 경로에서
    부르면 그대로 타임아웃이 난다.

    max_probe 를 주면 조회 대상을 그만큼으로 제한한다 — 한 번에 다 못 돌 때
    나눠 부르기 위한 것이다.
    """
    import yfinance as yf
    from backend.db.market_cache import get_common, save_common

    # ① 네이버 시총 순위가 있으면 그걸 쓴다 (2초). 아래 yfinance 경로는
    #    네이버가 막히거나 표 구조가 바뀌었을 때만 도는 폴백이다.
    fast, fast_names = fetch_top_by_marketcap()
    if len(fast) >= (KOSPI_TOP + KOSDAQ_TOP) * 0.8:
        save_common(_SCAN_KEY, fast, ttl_seconds=86400 * 7)
        if fast_names:
            # **덮어쓰지 않고 합친다.** 시총 상위 350종의 이름으로 통째로
            # 갈아치우면 그 밖의 종목은 화면에 코드만 남는다 (상장 전체는
            # 2,600종이 넘는다). 이 자리가 예전에는 안 도는 경로였는데
            # (네이버가 302 로 바뀌어 fast 가 항상 비었다) 그걸 고치자
            # 이름 사전이 2,651 → 350 으로 줄어드는 것으로 드러났다.
            #
            # 새로 받은 이름이 이기게 둔다 — 상장목록 캐시는 30일이고
            # 시총 페이지는 방금 받은 것이라 개명·재상장이 반영돼 있다.
            merged = dict(get_common(_NAME_KEY) or {})
            merged.update(fast_names)
            save_common(_NAME_KEY, merged, ttl_seconds=86400 * 7)
        logger.info(f"한국 스캔 유니버스 {len(fast)}종목 (네이버 시총순)")
        return {"ok": True, "universe": len(fast), "source": "naver", "remaining": 0}
    logger.warning(f"네이버 시총 수집 부족({len(fast)}종목) — yfinance 폴백")

    listed = get_listed_all()
    if not listed:
        return {"ok": False, "reason": "상장목록 없음"}

    # 이미 조회해 둔 시총이 있으면 재사용한다 (중간에 끊겨도 진척이 남는다).
    caps: dict[str, float] = dict(get_common("kr_market_caps") or {})

    todo = [r["ticker"] for r in listed if r["ticker"] not in caps]
    if max_probe:
        todo = todo[:max_probe]

    for t in todo:
        try:
            cap = (yf.Ticker(t).info or {}).get("marketCap")
            if cap:
                caps[t] = float(cap)
        except Exception:
            continue

    save_common("kr_market_caps", caps, ttl_seconds=86400 * 7)

    by_board: dict[str, list[str]] = {".KS": [], ".KQ": []}
    for r in listed:
        t = r["ticker"]
        if t in caps:
            by_board[".KS" if t.endswith(".KS") else ".KQ"].append(t)
    for suffix in by_board:
        by_board[suffix].sort(key=lambda t: caps.get(t, 0), reverse=True)

    universe = by_board[".KS"][:KOSPI_TOP] + by_board[".KQ"][:KOSDAQ_TOP]
    remaining = sum(1 for r in listed if r["ticker"] not in caps)

    # 목표에 못 미치면 **짧은 TTL** 로 저장한다.
    #
    # 네이버 경로는 `>= 목표의 80%` 일 때만 저장하는데(반쪽 유니버스가 7일
    # 굳으면 그동안 코스닥 신호가 통째로 없고 그게 "후보 없음" 으로 보인다),
    # 이 폴백 경로에는 그 가드가 없어 몇 종목만 훑고 끝난 결과도 7일짜리로
    # 저장했다. max_probe 를 주면 그게 정상 경로가 되므로 더 위험하다.
    #
    # 지금 있는 것은 쓰되(빈 것보다 낫다) 다음 주기가 이어받도록 짧게 둔다.
    enough = len(universe) >= (KOSPI_TOP + KOSDAQ_TOP) * 0.8
    ttl = 86400 * 7 if enough else 1800
    save_common(_SCAN_KEY, universe, ttl_seconds=ttl)

    logger.info("한국 스캔 유니버스 %d종목 (시총 조회 완료 %d, 남음 %d, TTL %ds)",
                len(universe), len(caps), remaining, ttl)
    if not enough:
        logger.warning(
            "한국 스캔 유니버스가 목표(%d)에 못 미친다 — %d종목만 저장했고 %d종목이 "
            "남았다. 다음 주기가 이어받는다.",
            KOSPI_TOP + KOSDAQ_TOP, len(universe), remaining)
    return {
        "ok": enough, "universe": len(universe),
        "probed": len(caps), "remaining": remaining,
    }


def get_scan_universe() -> list[str]:
    """신호 스캔 대상 (KOSPI 상위 200 + KOSDAQ 상위 150).

    캐시가 비어 있으면 빈 리스트를 돌려준다 — 여기서 만들지 않는다.
    요청 처리 중에 한 시간짜리 작업을 시작하면 안 된다.
    """
    from backend.db.market_cache import get_common
    return get_common(_SCAN_KEY) or []


def search_listed(query: str, limit: int = 5) -> list[dict]:
    """한국 상장 종목 검색. 종목명(한글)과 종목코드 둘 다로 찾는다.

    사용자는 '삼성전자'로도 '005930'으로도 찾는다. 정렬은 사람이 기대하는
    순서 — 코드 완전일치 → 이름이 검색어로 시작 → 이름에 포함 — 로 둔다.
    '삼성'을 쳤을 때 '삼성전자'보다 '에스원(구 삼성...)'이 위에 오면 안 된다.
    """
    q = (query or "").strip()
    if not q:
        return []
    qu = q.upper()

    exact, starts, contains = [], [], []
    for row in get_listed_all():
        code = row["ticker"].split(".")[0]
        name = row.get("name", "")
        if code == q:
            exact.append(row)
        elif name.startswith(q) or code.startswith(q):
            starts.append(row)
        elif q in name or qu in name.upper():
            contains.append(row)

    # 같은 묶음 안에서는 큰 회사를 먼저 보여준다. 이름 길이로만 정렬하면
    # '삼성'을 쳤을 때 삼성제약이 삼성전자보다 앞에 온다(둘 다 네 글자라
    # 종목코드 순으로 밀린다) — 사용자가 찾던 것은 거의 항상 큰 쪽이다.
    ranks = {t: i for i, t in enumerate(get_scan_universe())}
    big = len(ranks) + 1
    for group in (starts, contains):
        group.sort(key=lambda r: (ranks.get(r["ticker"], big),
                                  len(r.get("name", "")), r["ticker"]))

    merged = (exact + starts + contains)[:limit]
    return [{"ticker": r["ticker"], "name": r.get("name", "")} for r in merged]


def lookup(ticker: str) -> Optional[dict]:
    """한국 상장 목록에서 티커를 찾는다. 없으면 None.

    ticker_universe.lookup() 의 한국판이다. 그쪽은 미국 상장 목록만 담고 있어
    '005930.KS' 를 찾지 못하고, 시세 조회 전 존재 확인에서 걸러버린다 —
    실제로 그래서 한국 종목 현재가가 전부 404 였다.
    """
    sym = (ticker or "").upper().strip()
    if not sym:
        return None
    # 접미사는 무시하고 6자리 코드로 맞춘다.
    #
    # 사용자가 '.KS' 를 알 이유가 없고(005930 만 쳐도 찾아야 한다), 같은 종목이
    # 자료에 따라 .KS/.KQ 로 다르게 적히기도 한다. 정확히 일치를 요구하면
    # ticker-exists 는 찾는데 ticker-price 는 404 를 내는 어긋남이 생긴다 —
    # 실제로 삼성전자가 그렇게 조회되지 않았다.
    code = sym.split(".")[0]
    try:
        for row in get_listed_all():
            t = (row.get("ticker") or "").upper()
            if t == sym or t.split(".")[0] == code:
                return {"ticker": row["ticker"], "name": row.get("name") or "", "is_etf": False}
    except Exception as e:
        logger.warning(f"korea_universe 조회 실패 ({sym}): {e}")
    return None


def name_map() -> dict[str, str]:
    """티커 → 종목명. 화면에 코드 대신 이름을 보여줄 때 쓴다.

    유니버스를 만들 때 네이버에서 함께 받아 둔 이름을 먼저 쓴다. KRX KIND
    상장목록은 전 종목을 담고 있어 더 넓지만 해외 IP 에서 403 이라, 서버가
    한국 밖에 있으면 그 경로만으로는 이름이 하나도 나오지 않는다.
    """
    from backend.db.market_cache import get_common, save_common

    cached = get_common(_NAME_KEY)
    if cached:
        return dict(cached)

    # 캐시가 없으면 직접 채운다. 유니버스가 이미 만들어져 있으면 재생성이
    # 걸리지 않아 이름만 영영 비는데, 시총 페이지 조회는 2초면 끝난다.
    # 넓은 쪽(상장 전체)을 바닥에 깔고 좁고 신선한 쪽(시총 상위)을 위에 얹는다.
    # 순서를 뒤집으면 시총 상위 350종만 남아 나머지가 코드로 보인다.
    names: dict[str, str] = {}
    try:
        names.update({r["ticker"]: r.get("name", "") for r in get_listed_all()
                      if r.get("ticker") and r.get("name")})
    except Exception as e:
        logger.warning("상장목록에서 종목명 조회 실패: %s", e)

    try:
        _, top = fetch_top_by_marketcap()
        names.update(top)
    except Exception as e:
        logger.warning("네이버 종목명 수집 실패: %s", e)

    if names:
        save_common(_NAME_KEY, names, ttl_seconds=86400 * 7)
    else:
        # 둘 다 실패했다. 빈 사전을 캐시에 넣으면 그 상태가 7일 굳는다.
        logger.warning("한국 종목명을 한 곳에서도 받지 못했습니다 — 화면에 코드만 나옵니다")
    return names
