"""
backend/db/__init__.py
──────────────────────
PostgreSQL 연결 풀 + 편의 실행 헬퍼.
DB 연결 실패 시 is_available() = False 로 앱이 파일 폴백 모드로 동작.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

try:
    import psycopg2
    from psycopg2.pool import ThreadedConnectionPool
    from psycopg2.extras import RealDictCursor
    _PSYCOPG2_OK = True
except ImportError:
    _PSYCOPG2_OK = False

_pool: Optional[Any] = None


def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST', 'localhost')} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'postgres')} "
        f"user={os.getenv('DB_USER', 'postgres')} "
        f"password={os.getenv('DB_PASSWORD', '')} "
        f"connect_timeout=5"
    )


def init_pool(minconn: int = 2, maxconn: int = 20) -> bool:
    """연결 풀 초기화. 성공 시 True, 실패 시 False(파일 폴백)."""
    global _pool
    if not _PSYCOPG2_OK:
        logger.warning("psycopg2 미설치 → 파일 폴백 모드")
        return False
    try:
        _pool = ThreadedConnectionPool(minconn, maxconn, dsn=_dsn())
        logger.info(
            f"PostgreSQL 연결 풀 초기화 완료 "
            f"({os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')})"
        )
        return True
    except Exception as e:
        logger.warning(f"PostgreSQL 연결 실패 → 파일 폴백 모드: {e}")
        _pool = None
        return False


def is_available() -> bool:
    """DB 풀이 활성 상태인지 확인."""
    return _pool is not None


@contextmanager
def get_conn():
    """풀에서 커넥션을 꺼내 컨텍스트 매니저로 제공. 완료 시 commit, 예외 시 rollback."""
    import time
    if _pool is None:
        raise RuntimeError("DB 풀 미초기화 (is_available() == False)")
    conn = None
    for attempt in range(3):
        try:
            conn = _pool.getconn()
            break
        except Exception:
            if attempt < 2:
                time.sleep(0.3)
            else:
                raise
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if conn is not None:
            _pool.putconn(conn)


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
