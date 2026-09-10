"""
backend/db/__init__.py
──────────────────────
PostgreSQL 연결 풀 + 편의 실행 헬퍼.
DB 연결 실패 시 is_available() = False 로 앱이 파일 폴백 모드로 동작.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

try:
    import psycopg2
    from psycopg2.pool import PoolError, ThreadedConnectionPool
    from psycopg2.extras import RealDictCursor
    _PSYCOPG2_OK = True
except ImportError:
    _PSYCOPG2_OK = False

    class PoolError(Exception):      # type: ignore[no-redef]
        """psycopg2 가 없을 때의 자리표시자 — 아래 except 절이 참조한다."""

_pool: Optional[Any] = None
# 재초기화를 한 번에 하나만 하게 한다. 여러 스레드가 동시에 실패하면 다 같이
# 여기로 들어오는데, 각자 풀을 만들면 풀마다 minconn 만큼 커넥션이 새로 열린다.
_reinit_lock = threading.Lock()


class DBBusy(RuntimeError):
    """지금은 처리할 수 없지만 **다시 시도하면 되는** 상태.

    고장이 아니라 혼잡이다. 그래서 500(우리가 망가졌다)이 아니라 503 으로
    번역돼야 한다 — 500 은 사용자에게 재시도해도 되는지를 알려주지 않는다.
    `main.py` 의 예외 핸들러가 이 계열을 503 + `str(exc)` 로 내려보낸다.
    """


class PoolExhausted(DBBusy):
    """풀에 남은 커넥션이 없다. **커넥션 장애가 아니라 백프레셔다.**

    둘을 구분하는 이유는 대응이 정반대이기 때문이다. 커넥션이 죽었으면 풀을
    새로 만드는 게 맞지만, 고갈은 새 풀을 만들어도 똑같이 만석이 된다 —
    동시 요청 수가 줄어야 풀린다. 그런데 재초기화는 `closeall()` 을 하므로
    **진행 중인 다른 요청들의 커넥션까지 끊는다.** 즉 부하 스파이크 하나가
    그 순간의 모든 트랜잭션을 죽이는 구조였다.

    advisory lock 이 세션 단위라 피해가 더 크다. `portfolio_repo.user_write_lock`
    이 쥐고 있던 락이 `closeall()` 로 함께 풀리는데, 재초기화를 유발한 요청
    하나만 에러를 받고 나머지는 **락이 사라진 줄도 모른 채 계속 진행한다.**
    (실측: 풀을 채운 뒤 pg_locks 를 관찰하니 advisory lock 이 1 → 0 이 됐다.)
    """


class WriteLockUnavailable(DBBusy):
    """사용자별 쓰기 락을 잡지 못했다.

    예전에는 못 잡아도 그냥 진행했다(가용성 우선). 그런데 락 획득이 실패하는
    주된 원인이 풀 고갈이고, **풀이 고갈됐다는 건 동시 요청이 몰렸다는 뜻이라
    바로 그때가 경쟁이 실제로 터지는 순간이다.** 가장 위험한 지점에서만
    보호가 사라지는 셈이었다.

    유실은 되돌릴 수 없고 사용자가 알지도 못한다. 503 은 다시 누르면 된다.
    """


def _dsn() -> str:
    """psycopg2 연결 문자열.

    DATABASE_URL 이 있으면 그것을 우선한다 — Neon·Cloud Run 등 관리형 환경은
    호스트/포트를 나눠 주지 않고 하나의 URL 로 제공한다.
    없으면 기존 DB_HOST/DB_PORT... 조합으로 폴백해 로컬 개발을 그대로 지원한다.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # psycopg2 는 channel_binding 파라미터를 인식하지 못해 연결이 실패한다.
        # Neon 이 붙여 주는 값이므로 제거한다 (sslmode=require 로 암호화는 유지).
        url = re.sub(r"[?&]channel_binding=[^&]*", "", url)
        if "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + "sslmode=require"
        return url

    host = os.getenv("DB_HOST", "localhost")
    # 원격 연결 시 SSL 필수, 로컬 localhost 연결 시 불필요
    is_remote = host != "localhost" and host != "127.0.0.1"
    ssl_part  = "sslmode=require " if is_remote else ""
    return (
        f"host={host} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'postgres')} "
        f"user={os.getenv('DB_USER', 'postgres')} "
        f"password={os.getenv('DB_PASSWORD', '')} "
        f"connect_timeout=10 "
        f"{ssl_part}"
        f"keepalives=1 keepalives_idle=60 keepalives_interval=10 keepalives_count=5"
    )


def _dsn_label() -> str:
    """로그에 찍을 접속 대상. 비밀번호는 뺀다.

    예전에는 DATABASE_URL 로 접속해도 로그에 DB_HOST 값을 찍었다. 그래서
    운영 DB 에 붙여 놓고도 'localhost' 로 보여, 어디에 연결됐는지 확인할 방법이
    없었다. 실제로 쓰는 DSN 을 그대로 보여준다.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        host = re.sub(r"^.*@", "", url).split("/")[0].split("?")[0]
        return f"DATABASE_URL → {host}"
    return f"{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'postgres')}"


def init_pool(minconn: int = 2, maxconn: Optional[int] = None) -> bool:
    """연결 풀 초기화. 성공 시 True, 실패 시 False(파일 폴백).

    maxconn 기본값은 Cloud Run 의 인스턴스당 동시 요청 수(40)의 **두 배**다.
    이보다 작으면 요청은 스레드풀에 올라갔는데 커넥션이 없어 대기하게 되고,
    동시 접속이 늘수록 그 대기가 그대로 응답 지연이 된다.
    DB(Neon) 는 900 연결까지 받으므로 5 인스턴스 × 80 = 400 은 여유가 있다.

    **두 배인 이유는 쓰기 요청이 커넥션을 두 개 쥐기 때문이다.**
    `portfolio_repo.user_write_lock` 이 advisory lock 용으로 하나를 잡아
    핸들러가 끝날 때까지 놓지 않고, 그 안의 실제 쿼리가 `get_conn()` 으로
    또 하나를 꺼낸다 (실측으로 확인: 요청 1건 = 커넥션 2개).
    예전 값 40 은 "1요청 = 1커넥션" 을 가정한 값이었고, 락이 들어오면서 그
    가정이 깨졌는데 숫자는 그대로였다. 그래서 쓰기 20건이면 40개가 다 나갔다.

    락과 본문 쿼리가 같은 커넥션을 쓰도록 바꾸면(그게 근본 해법이다) 다시
    1요청 = 1커넥션이 되므로 **이 두 배도 같이 되돌려야 한다.** 그때 이 주석을
    고치지 않으면 80 이 근거를 잃은 채 굳는다 — 지금 40 이 그랬던 것처럼.
    """
    global _pool
    if maxconn is None:
        maxconn = max(4, int(os.getenv("DB_POOL_MAX", "80")))
    if not _PSYCOPG2_OK:
        logger.warning("psycopg2 미설치 → 파일 폴백 모드")
        return False
    try:
        _pool = ThreadedConnectionPool(minconn, maxconn, dsn=_dsn())
        logger.info(
            f"PostgreSQL 연결 풀 초기화 완료 ({_dsn_label()}, 최대 {maxconn} 커넥션)"
        )
        return True
    except Exception as e:
        logger.warning(f"PostgreSQL 연결 실패 → 파일 폴백 모드: {e}")
        _pool = None
        return False


def is_available() -> bool:
    """DB 풀이 활성 상태인지 확인."""
    return _pool is not None


def _try_reinit_pool() -> bool:
    """풀이 죽었을 때 재초기화 시도. 성공 시 True.

    **커넥션이 실제로 죽었을 때만 부른다.** 고갈에는 부르지 않는다 —
    PoolExhausted 의 설명 참고.

    ## 옛 풀을 닫지 않는다

    예전에는 `closeall()` 을 했다. 그건 지금 나가 있는 커넥션까지 전부 끊는다.
    advisory lock 은 세션 단위라 락을 쥔 요청의 락이 함께 풀리는데, 재초기화를
    유발한 요청 하나만 예외를 받고 나머지는 **락이 사라진 줄도 모른 채 임계구역을
    계속 실행한다.** 복구 동작이 아직 멀쩡한 요청의 상호배제를 깨뜨린 셈이다.

    지금은 새 풀만 만들고 옛 풀은 참조를 놓는다. 남는 비용을 재봤다
    (`pg_stat_activity` 로 서버 쪽을 직접 셈):

    - 옛 풀의 **유휴** 커넥션 — 참조가 끊기는 즉시 refcount 로 닫힌다.
    - 옛 풀에서 **나가 있던** 커넥션 — 살아남는다. 그런데 이건 누수가 아니다.
      진행 중인 요청의 것이고, `closeall()` 을 하던 때에도 같은 수가 존재했다.
      차이는 "요청이 끝나면 반납" 이냐 "지금 강제로 끊음" 이냐뿐이다.

    즉 옛 동작은 아무도 요구하지 않은 커넥션 수 보장을 위해 상호배제를 팔고
    있었다. 상한도 문제되지 않는다 — 재초기화가 **성공**했다는 건 DB 는 멀쩡한데
    풀의 커넥션이 상했다는 뜻이고, 그 커넥션들은 이미 서버 쪽에서 죽어 있어
    연결 수에 잡히지 않는다. DB 자체가 안 되면 아래 `init_pool()` 이 실패해
    새 풀조차 안 생긴다.

    반납 대상이 어긋나지 않아야 성립한다 — `get_conn` 과 `user_write_lock` 은
    시작 시점의 풀을 고정해 두고 거기로 되돌린다. 전역을 다시 읽으면 새 풀에
    옛 커넥션을 넣어 실패하고, 그 커넥션이 옛 풀에 영영 남아 **그때는 진짜
    누수가 된다.**
    """
    global _pool
    old = _pool
    with _reinit_lock:
        # 기다리는 사이 다른 스레드가 이미 갈아끼웠으면 또 만들지 않는다.
        # 풀마다 minconn 만큼 커넥션을 새로 열기 때문에 중복은 그대로 비용이다.
        if _pool is not old:
            return _pool is not None

        in_use = 0
        try:
            in_use = len(old._used) if old is not None else 0   # type: ignore[union-attr]
        except Exception:
            pass
        logger.warning(
            f"DB 연결 풀 재초기화 시도 중... 옛 풀은 닫지 않는다 "
            f"(나가 있는 커넥션 {in_use}개는 각 요청이 끝나면 반납된다)."
        )
        try:
            _pool = None
            return init_pool()
        except Exception as e:
            logger.error(f"DB 풀 재초기화 실패: {e}")
            return False


def _is_exhaustion(e: BaseException) -> bool:
    """이 예외가 '풀에 남은 게 없다'인가, 아니면 커넥션 장애인가.

    메시지 문자열로 가르지 않는다. `PoolError` 는 고갈 말고 "pool is closed"
    에도 쓰이는데, 그쪽은 진짜로 재초기화가 필요한 상태다. 풀 객체의 `closed`
    를 직접 보는 편이 문구 변경에 흔들리지 않는다.
    """
    if not isinstance(e, PoolError):
        return False
    try:
        return not _pool.closed      # type: ignore[union-attr]
    except Exception:
        return False


@contextmanager
def get_conn():
    """풀에서 커넥션을 꺼내 컨텍스트 매니저로 제공. 완료 시 commit, 예외 시 rollback.

    커넥션 장애는 재시도하고, 마지막엔 풀을 새로 만든다. **고갈은 그 경로로
    보내지 않고 PoolExhausted 로 올린다** — 이유는 그 예외의 설명에 있다.
    """
    import time
    pool = _pool          # 이 요청이 쓸 풀을 고정한다. 도중에 다른 스레드가
                          # 재초기화하면 전역 _pool 이 바뀌는데, 그때 새 풀에
                          # 옛 커넥션을 반납하면 "unkeyed connection" 이 난다.
    if pool is None:
        raise RuntimeError("DB 풀 미초기화 (is_available() == False)")
    conn = None
    for attempt in range(3):
        try:
            conn = pool.getconn()
            # 끊긴 커넥션 감지 후 교체
            if conn.closed:
                pool.putconn(conn, close=True)
                conn = None
                time.sleep(0.2)
                continue
            # 간단한 ping으로 연결 유효성 검증
            with conn.cursor() as _cur:
                _cur.execute("SELECT 1")
            break
        except Exception as e:
            if conn is not None:
                try:
                    pool.putconn(conn, close=True)
                except Exception:
                    pass
                conn = None
            exhausted = _is_exhaustion(e)
            if attempt < 2:
                # 고갈이어도 잠깐은 기다려 본다 — 다른 요청이 끝나면 자리가 난다.
                time.sleep(0.3)
            elif exhausted:
                maxconn = getattr(pool, "maxconn", "?")
                logger.error(
                    f"DB 커넥션 풀 고갈 (maxconn={maxconn}). 풀은 그대로 두고 "
                    f"요청을 거절한다 — 재초기화해도 새 풀이 똑같이 만석이 되고, "
                    f"진행 중인 다른 요청의 커넥션과 advisory lock 만 끊긴다."
                )
                raise PoolExhausted(
                    "DB 커넥션이 부족해 요청을 처리하지 못했다"
                ) from e
            else:
                # 커넥션이 실제로 죽은 경우에만 풀 전체 재초기화
                if _try_reinit_pool():
                    raise RuntimeError("풀 재초기화 완료, 다음 요청에서 재시도") from e
                raise
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if conn is not None:
            try:
                pool.putconn(conn)
            except Exception as e:
                # 반납 실패로 본래 결과·예외를 덮어쓰지 않는다. 커넥션 하나가
                # 새는 것보다 요청 결과가 바뀌는 쪽이 나쁘다.
                logger.warning(f"커넥션 반납 실패: {e}")


def execute(sql: str, params=None, fetch: str = "none") -> Any:
    """단일 쿼리 편의 함수. fetch: 'none' | 'one' | 'all'"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            elif fetch == "all":
                return cur.fetchall()
            return None


def executemany(sql: str, params_list: list):
    """배치 INSERT/UPDATE 편의 함수."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)


def close_pool():
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
