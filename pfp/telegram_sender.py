import os
import requests
import pathlib
from dotenv import load_dotenv

load_dotenv(dotenv_path=pathlib.Path(__file__).parent / '.env', override=True)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def send_report(filepath: str, ticker: str, company_name: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[텔레그램] 토큰/챗ID 미설정 — BOT:{BOT_TOKEN[:10]} CHAT:{CHAT_ID}")
        return False

    if not os.path.exists(filepath):
        print(f"[텔레그램] 파일 없음: {filepath}")
        return False

    print(f"[텔레그램] {ticker} 레포트 전송 중... (BOT:{BOT_TOKEN[:15]} CHAT:{CHAT_ID})")

    caption = f"📊 LENS CAPITAL RESEARCH\n\n{ticker} — {company_name}\n레포트가 완성되었습니다."

    try:
        with open(filepath, "rb") as f:
            response = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                data={"chat_id": CHAT_ID, "caption": caption},
                files={"document": (os.path.basename(filepath), f,
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                timeout=60
            )

        print(f"[텔레그램] 응답: {response.text[:200]}")

        if response.status_code == 200 and response.json().get("ok"):
            print(f"[텔레그램] ✅ 전송 완료!")
            return True
        else:
            print(f"[텔레그램] ❌ 전송 실패: {response.text}")
            return False

    except Exception as e:
        print(f"[텔레그램] ❌ 오류: {e}")
        return False


def send_message(text: str) -> bool:
    load_dotenv(dotenv_path=pathlib.Path(__file__).parent / '.env', override=True)
    tok  = os.getenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    chat = os.getenv("TELEGRAM_CHAT_ID", CHAT_ID)
    if not tok or not chat:
        return False
    try:
        requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data={"chat_id": chat, "text": text},
            timeout=10
        )
        return True
    except:
        return False
