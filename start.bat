@echo off
REM Personal Financial Platform — 로컬 네트워크 실행 스크립트
REM 다른 컴퓨터에서도 접근 가능하도록 0.0.0.0으로 바인딩

echo ============================================
echo  Personal Financial Platform
echo  Backend : http://0.0.0.0:8000
echo  Frontend: http://0.0.0.0:3000
echo ============================================
echo.

REM 백엔드 (0.0.0.0 바인딩 — LAN 전체에서 접근 가능)
start "PFP Backend" cmd /k "cd /d %~dp0 && venv\Scripts\uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload"

REM 잠깐 대기 후 프론트엔드 시작
timeout /t 3 /nobreak >nul
start "PFP Frontend" cmd /k "cd /d %~dp0\frontend && npm run dev"

echo.
echo 시작 완료! 브라우저에서 http://localhost:3000 접속
echo 다른 컴퓨터에서: http://[이 PC의 IP]:3000
echo.
pause
