@echo off
REM Đưa máy chủ cấp phép Nexus (Docker) lên mỗi khi khởi động máy.
REM Chờ Docker Desktop sẵn sàng rồi chạy compose (container có restart: unless-stopped).
cd /d "%~dp0"
for /l %%i in (1,1,60) do (
    docker info >nul 2>&1 && goto :ready
    timeout /t 5 /nobreak >nul
)
:ready
docker compose up -d
