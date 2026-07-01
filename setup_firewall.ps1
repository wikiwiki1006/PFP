# PFP 방화벽 포트 자동 개방 (자동 관리자 권한 상승)
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    Start-Process PowerShell -Verb RunAs "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

Write-Host "PFP 방화벽 규칙 설정 중..." -ForegroundColor Cyan

netsh advfirewall firewall delete rule name="PFP Backend 8000" 2>$null | Out-Null
netsh advfirewall firewall delete rule name="PFP Frontend 3000" 2>$null | Out-Null

netsh advfirewall firewall add rule name="PFP Backend 8000" dir=in action=allow protocol=TCP localport=8000
netsh advfirewall firewall add rule name="PFP Frontend 3000" dir=in action=allow protocol=TCP localport=3000

Write-Host ""
Write-Host "완료!" -ForegroundColor Green
Write-Host "이제 다른 컴퓨터에서 http://$(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias '*' | Where-Object { $_.IPAddress -like '192.168.*' } | Select-Object -First 1 -ExpandProperty IPAddress):3000 으로 접속하세요."
Read-Host "엔터를 누르면 종료"
