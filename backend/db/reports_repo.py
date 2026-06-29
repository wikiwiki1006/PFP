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

def save_report(
    filename: str,
    content: str,
    report_type: str = "other",
    metadata: Optional[dict] = None,
    user_id: str = "default",
) -> Optional[int]:
    """보고서를 DB에 저장. DB 미연결 시 경고 후 None 반환."""
    if not is_available():
        logger.error("DB 미연결 — 레포트를 저장할 수 없습니다.")
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO reports(user_id, report_type, filename, content, metadata)
                       VALUES(%s,%s,%s,%s,%s)
                       ON CONFLICT(filename) DO UPDATE
                       SET content=EXCLUDED.content,
                           metadata=EXCLUDED.metadata,
                           created_at=NOW()
                       RETURNING id""",
                    (user_id, report_type, filename, content,
                     json.dumps(metadata or {})),
                )
                row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"DB save_report 실패: {e}")
        return None


def list_reports(
    user_id: str = "default",
    report_type: Optional[str] = None,
    limit: int = 30,
) -> list[dict]:
    """DB에서 레포트 목록 조회."""
    if not is_available():
        return []
    try:
        sql = (
            "SELECT filename, report_type, metadata, created_at "
            "FROM reports WHERE user_id=%s"
        )
        params: list = [user_id]
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
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"DB list_reports 실패: {e}")
        return []


def get_report_content(filename: str) -> Optional[str]:
    """DB에서 레포트 내용 조회."""
    if not is_available():
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM reports WHERE filename=%s",
                    (filename,),
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
