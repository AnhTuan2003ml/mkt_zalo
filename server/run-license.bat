@echo off
REM ==============================================================
REM  Chay MAY CHU CAP PHEP Nexus + ngrok (URL CO DINH).
REM  Muc dich: mat dien -> bat may la tu chay lai, cung URL,
REM  KHONG can lay URL moi.
REM
REM  Sua NGROK_DOMAIN ben duoi thanh domain tinh ban da dat trong
REM  ngrok Dashboard (vd: nexus-abcd.ngrok-free.app).
REM ==============================================================

set "NGROK_DOMAIN=unwrestled-trisyllabically-brendan.ngrok-free.dev"

cd /d "%~dp0"

echo [1/2] Khoi dong license server (cong 5555)...
start "Nexus License Server" cmd /k python license_server.py

echo Cho server san sang...
timeout /t 4 /nobreak >nul

echo [2/2] Mo tunnel co dinh ngrok -> https://%NGROK_DOMAIN%
start "ngrok tunnel" ngrok http --domain=%NGROK_DOMAIN% 5555

echo.
echo Da chay xong. May chu cap phep: https://%NGROK_DOMAIN%
echo Dong 2 cua so den (server + ngrok) neu muon tat.
