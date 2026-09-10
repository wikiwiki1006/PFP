"""
backend/db/reports_repo.py
────────────────────────────
레포트 저장소 + AI 분석 결과 캐시.
DB 전용 — 파일시스템(outputs/)에 쓰지 않음 (클라우드 배포 대응).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)


# ── Reports ────────────────────────────────────────────────────────────────────

# 공용 리포트 재사용 유효시간 (시간)
SHARED_TTL_HOURS: dict[str, int] = {
    "equity_research":   24,   # 종목: 하루 단위로 시황·주가가 바뀌므로 24시간
    "industry_research": 72,   # 산업: 변화 속도가 느려 72시간
}


def save_report(
    filename: str,
    content: str,
    report_type: str = "other",
    metadata: Optional[dict] = None,
    user_id: str = "default",
    scope: str = "private",
    subject_key: Optional[str] = None,
    market: str = "US",
) -> Optional[int]:
    """보고서를 DB에 저장. DB 미연결 시 경고 후 None 반환.

    scope='shared' + subject_key 를 주면 다른 사용자도 재사용할 수 있는
    공용 리포트가 된다 (find_fresh_shared_report 로 조회).
    """
    if not is_available():
        logger.error("DB 미연결 — 레포트를 저장할 수 없습니다.")
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO reports
                           (user_id, report_type, filename, content, metadata, scope,
                            subject_key, market)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(filename) DO UPDATE
                       SET content=EXCLUDED.content,
                           metadata=EXCLUDED.metadata,
                           scope=EXCLUDED.scope,
                           subject_key=EXCLUDED.subject_key,
                           market=EXCLUDED.market,
                           created_at=NOW()
                       RETURNING id""",
                    (user_id, report_type, filename, content,
                     json.dumps(metadata or {}), scope,
                     subject_key.upper() if subject_key else None, market),
                )
                row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"DB save_report 실패: {e}")
        return None


def find_fresh_shared_report(
    report_type: str,
    subject_key: str,
    model_tier: str = "basic",
    max_age_hours: Optional[int] = None,
    market: str = "US",
) -> Optional[dict]:
    """유효시간 내 공용 리포트 조회. 없으면 None.

    누가 만들었는지는 따지지 않는다 — 같은 종목/산업이면 결과가 동일하므로
    최초 1인이 만든 것을 전체가 공유한다.

    단 **분석 등급(model_tier)은 구분한다.** basic(기본)과 deep(심층)은 사용하는
    모델과 분석 깊이가 달라 결과물의 성격이 다르므로, 심층을 요청한 사용자에게
    기본 리포트를 주면 기대와 어긋난다. 등급이 정확히 일치할 때만 재사용한다.
    (구 버전 리포트는 metadata 에 model_tier 가 없으므로 basic 으로 간주)

    반환: {filename, content, metadata, created_at, age_hours, author}
    """
    if not is_available() or not subject_key:
        return None
    ttl = max_age_hours if max_age_hours is not None else SHARED_TTL_HOURS.get(report_type, 24)
    tier = model_tier if model_tier in ("basic", "deep") else "basic"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT filename, content, metadata, created_at, user_id,
                              EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600.0
                       FROM reports
                       WHERE scope='shared' AND market=%s
                         AND report_type=%s AND subject_key=%s
                         AND COALESCE(metadata->>'model_tier', 'basic') = %s
                         AND created_at >= NOW() - (%s * INTERVAL '1 hour')
                       ORDER BY created_at DESC
                       LIMIT 1""",
                    (market, report_type, subject_key.upper(), tier, ttl),
                )
                r = cur.fetchone()
        if not r:
            return None
        return {
            "filename":   r[0],
            "content":    r[1],
            "metadata":   r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}"),
            "created_at": r[3].isoformat() if r[3] else None,
            "author":     r[4],
            "age_hours":  round(float(r[5]), 2),
        }
    except Exception as e:
        logger.error(f"DB find_fresh_shared_report 실패: {e}")
        return None


def list_reports(
    user_id: str,
    report_type: Optional[str] = None,
    limit: int = 30,
    market: str = "US",
) -> list[dict]:
    """DB에서 **본인이 만든** 레포트 목록 조회.

    scope 와 무관하게 작성자 본인 것만 돌려준다.

    예전에는 공용(scope='shared') 리포트를 작성자와 무관하게 전부 포함했다.
    그 결과 '과거 레포트' 목록에 남이 만든 리포트가 섞여 보였다 — 목록은
    "내가 무엇을 분석했는가"의 기록인데 남의 활동이 노출되는 셈이었다.

    공용 리포트의 재사용은 목록이 아니라 **조회 시점**에 일어난다:
    사용자가 종목을 검색하거나 산업을 고르면 find_fresh_shared_report() 가
    유효시간 내 공용 리포트를 찾아 그때만 보여준다. 그 경로가 공유의 유일한
    통로이고, 여기서 다시 열어줄 이유가 없다.
    """
    if not is_available():
        return []
    try:
        sql = (
            "SELECT filename, report_type, metadata, created_at, scope, user_id "
            "FROM reports WHERE user_id=%s AND market=%s"
        )
        params: list = [user_id, market]
        if report_type:
            sql += " AND report_type=%s"
            params.append(report_type)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [
            {
                "name":       r[0],
                "type":       r[1],
                "metadata":   r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}"),
                "created_at": r[3].isoformat() if r[3] else None,
                "size":       len(r[0]),
                "scope":      r[4] or "private",
                "shared":     (r[4] == "shared"),
                "mine":       (r[5] == user_id),
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"DB list_reports 실패: {e}")
        return []


def get_report_content(filename: str, user_id: str) -> Optional[str]:
    """DB에서 레포트 내용 조회 — **반드시 소유자 검사를 거친다**.

    예전에는 filename 만으로 조회했다. 파일명은 추측 가능하므로 그대로 두면
    남의 개인 리포트를 그대로 읽을 수 있었다(IDOR). list_reports 와 동일하게
    "본인 것 또는 공용" 조건을 건다.
    """
    if not is_available() or not user_id:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM reports "
                    "WHERE filename=%s AND (user_id=%s OR scope='shared')",
                    (filename, user_id),
                )
                row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"DB get_report_content 실패: {e}")
        return None


# ── Analysis Cache ─────────────────────────────────────────────────────────────

def save_analysis(
    analysis_type: str,
    cache_key: str,
    result: dict,
    ttl_hours: int = 24,
    user_id: str = "default",
):
    """AI 분석 결과 저장 (upsert, TTL 설정)."""
    if not is_available():
        return
    expires = datetime.now() + timedelta(hours=ttl_hours)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO analysis_cache
                           (user_id, analysis_type, cache_key, result, created_at, expires_at)
                       VALUES(%s,%s,%s,%s,NOW(),%s)
                       ON CONFLICT(user_id, analysis_type, cache_key) DO UPDATE
                       SET result=EXCLUDED.result,
                           created_at=NOW(),
                           expires_at=EXCLUDED.expires_at""",
                    (user_id, analysis_type, cache_key, json.dumps(result), expires),
                )
    except Exception as e:
        logger.error(f"DB save_analysis 실패: {e}")


def get_analysis(
    analysis_type: str,
    cache_key: str,
    user_id: str = "default",
) -> Optional[dict]:
    """만료되지 않은 분석 결과 반환. 없으면 None."""
    if not is_available():
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT result FROM analysis_cache
                       WHERE user_id=%s AND analysis_type=%s AND cache_key=%s
                         AND (expires_at IS NULL OR expires_at > NOW())
                       ORDER BY created_at DESC LIMIT 1""",
                    (user_id, analysis_type, cache_key),
                )
                row = cur.fetchone()
        if row:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception as e:
        logger.error(f"DB get_analysis 실패: {e}")
    return None
