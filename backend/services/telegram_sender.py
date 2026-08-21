"""
backend/services/telegram_sender.py
──────────────────────────────────────
텔레그램 봇 전송 서비스 (pfp/telegram_sender.py 이식)
"""
from __future__ import annotations

import os
import requests


def send_message(text: str) -> bool:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return True
    except Exception:
        return False


def send_file_bytes(content: bytes, filename: str, caption: str = "") -> bool:
    """파일 바이트를 직접 전송."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendDocument",
            data={"chat_id": chat_id, "caption": caption},
            files={"document": (filename, content)},
            timeout=60,
        )
        return resp.status_code == 200 and resp.json().get("ok", False)
    except Exception:
        return False
