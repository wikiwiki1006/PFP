@echo off
REM Personal Financial Platform — 개발 실행 스크립트
REM 백엔드(8000)는 localhost에서만 접근. 프론트(3000)가 /api/* 를 프록시로 중계.
REM LAN 접속 주소: http://[이 PC IP]:3000

title PFP Launcher

echo ============================================
echo  Personal Financial Platform (dev)
echo  Backend  : http://localhost:8000  (내부)
echo  Frontend : http://0.0.0.0:3000   (LAN 공개)
echo ============================================
echo.

REM 이전에 실행 중인 uvicorn/node 프로세스 종료 (충돌 방지)
taskkill /F /IM "uvicorn.exe" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq PFP Backend*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq PFP Frontend*" >nul 2>&1
timeout /t 1 /nobreak >nul

REM 포트 3000 방화벽 (프론트 LAN 공개용 — 관리자 없으면 조용히 실패)
netsh advfirewall firewall show rule name="PFP Frontend 3000" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="PFP Frontend 3000" dir=in action=allow protocol=TCP localport=3000 >nul 2>&1
    if not errorlevel 1 echo [방화벽] 포트 3000 규칙 추가
)

REM 백엔드 — localhost 바인딩 (LAN에 직접 노출 불필요, 프록시가 중계)
start "PFP Backend" cmd /k "cd /d %~dp0 && venv\Scripts\uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload"

REM 백엔드 기동 대기
timeout /t 3 /nobreak >nul

REM 프론트엔드 — 0.0.0.0 바인딩 (LAN 접근 허용), Vite proxy 설정 포함
start "PFP Frontend" cmd /k "cd /d %~dp0\frontend && npm run dev"

echo.
echo 시작 완료!
echo  로컬:  http://localhost:3000
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr "192.168"') do (
    set LAN_IP=%%a
    goto :found_ip
)
:found_ip
if defined LAN_IP (
    echo  LAN:   http://%LAN_IP: =%:3000
)
echo.
echo [주의] vite.config.ts 변경 후에는 이 스크립트를 다시 실행하세요.
echo.
pause
