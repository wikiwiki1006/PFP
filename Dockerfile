# Cloud Run 용 FastAPI 백엔드 이미지.
# 프론트엔드는 Firebase Hosting 이 서빙하므로 여기엔 포함하지 않는다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 의존성 먼저 설치 — 소스만 바뀐 재빌드에서 이 레이어를 재사용한다
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

# Cloud Run 은 PORT 를 주입한다 (기본 8080).
# 서버리스라 백그라운드 스케줄러는 끈다 — 인스턴스가 0으로 내려가면 못 돈다.
ENV PORT=8080 ENABLE_SCHEDULER=false
EXPOSE 8080

CMD exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --workers 1
