@echo off
setlocal
REM ==============================================================
REM  Chay MAY CHU CAP PHEP Nexus + ngrok (URL CO DINH).
REM  Mat dien -> bat may la tu chay lai, cung URL, KHONG can URL moi.
REM ==============================================================

set "NGROK_DOMAIN=unwrestled-trisyllabically-brendan.ngrok-free.dev"

cd /d "%~dp0"

REM --- Tim Python that (uu tien conda base da co Flask) ---
set "PY=D:\package\conda\Miniconda3\python.exe"
if not exist "%PY%" set "PY=python"

REM --- Tim ngrok (uu tien tren PATH, khong co thi dung WindowsApps) ---
set "NGROK=ngrok"
where ngrok >nul 2>&1 || set "NGROK=%LOCALAPPDATA%\Microsoft\WindowsApps\ngrok.exe"

echo [1/2] Khoi dong license server (cong 5555)...
start "Nexus License Server" cmd /k "%PY%" license_server.py

echo Cho server san sang...
timeout /t 4 /nobreak >nul

echo [2/2] Mo tunnel co dinh -^> https://%NGROK_DOMAIN%
start "ngrok tunnel" "%NGROK%" http --domain=%NGROK_DOMAIN% 5555

echo.
echo Da chay xong. May chu cap phep: https://%NGROK_DOMAIN%
echo Dong 2 cua so (server + ngrok) neu muon tat.
endlocal
