@echo off
REM 이 파일을 "관리자 권한으로 실행"해야 합니다.
NET SESSION >nul 2>&1
if errorlevel 1 (
    echo 관리자 권한으로 실행해주세요. 이 파일을 우클릭 ^> 관리자 권한으로 실행
    pause
    exit /b 1
)

echo PFP 방화벽 규칙 설정 중...
netsh advfirewall firewall delete rule name="PFP Backend 8000" >nul 2>&1
netsh advfirewall firewall delete rule name="PFP Frontend 3000" >nul 2>&1
netsh advfirewall firewall add rule name="PFP Backend 8000" dir=in action=allow protocol=TCP localport=8000
netsh advfirewall firewall add rule name="PFP Frontend 3000" dir=in action=allow protocol=TCP localport=3000
echo.
echo 완료! 이제 다른 컴퓨터에서 http://[이 PC IP]:3000 으로 접속하세요.
pause
