@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "VERSION_FILE=%CD%\VERSION"
if not exist "%VERSION_FILE%" (
    echo [ERROR] Khong tim thay VERSION
    exit /b 1
)
set /p APP_VERSION=<"%VERSION_FILE%"
if not defined APP_VERSION set "APP_VERSION=1.0.0"
set "SCRIPT=%CD%\installer.py"
set "OUTPUT_DIR=%CD%\installer_output"
set "OUTPUT=%OUTPUT_DIR%\Nexus_Setup_v%APP_VERSION%.exe"
set "WORK_DIR=%CD%\build\installer"
set "ICON=%CD%\static\ico\app.ico"
set "PYTHON_CMD="

if not exist "%SCRIPT%" (
    echo [ERROR] Khong tim thay installer.py
    exit /b 1
)

where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo [ERROR] Khong tim thay Python 3.
    echo Cai Python 3 va chon Add Python to PATH, sau do chay lai file nay.
    exit /b 1
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [ERROR] Can Python 3.10 tro len.
    exit /b 1
)

%PYTHON_CMD% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [SETUP] Dang cai PyInstaller...
    %PYTHON_CMD% -m pip install --upgrade pyinstaller
    if errorlevel 1 (
        echo [ERROR] Khong the cai PyInstaller.
        exit /b 1
    )
)

%PYTHON_CMD% -c "import certifi" >nul 2>nul
if errorlevel 1 (
    echo [SETUP] Dang cai bo chung chi HTTPS certifi...
    %PYTHON_CMD% -m pip install --upgrade certifi
    if errorlevel 1 (
        echo [ERROR] Khong the cai certifi de dong goi chung chi HTTPS.
        exit /b 1
    )
)

if exist "%WORK_DIR%" rmdir /s /q "%WORK_DIR%"
if exist "%OUTPUT%" del /q "%OUTPUT%"
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

if not exist "%WORK_DIR%" mkdir "%WORK_DIR%"

echo [BUILD] Dang tao Nexus_Setup_v%APP_VERSION%.exe...
if exist "%ICON%" (
    %PYTHON_CMD% -m PyInstaller ^
        --noconfirm ^
        --clean ^
        --collect-data certifi ^
        --hidden-import certifi ^
        --onefile ^
        --windowed ^
        --name "Nexus_Setup_v%APP_VERSION%" ^
        --distpath "%OUTPUT_DIR%" ^
        --workpath "%WORK_DIR%" ^
        --specpath "%WORK_DIR%" ^
        --icon "%ICON%" ^
        --add-data "%ICON%;static\ico" ^
        "%SCRIPT%"
) else (
    echo [WARN] Khong tim thay static\ico\app.ico, installer se dung icon mac dinh.
    %PYTHON_CMD% -m PyInstaller ^
        --noconfirm ^
        --clean ^
        --collect-data certifi ^
        --hidden-import certifi ^
        --onefile ^
        --windowed ^
        --name "Nexus_Setup_v%APP_VERSION%" ^
        --distpath "%OUTPUT_DIR%" ^
        --workpath "%WORK_DIR%" ^
        --specpath "%WORK_DIR%" ^
        "%SCRIPT%"
)

if errorlevel 1 (
    echo [ERROR] Build installer that bai.
    exit /b 1
)

if not exist "%OUTPUT%" (
    echo [ERROR] Build xong nhung khong tim thay:
    echo %OUTPUT%
    exit /b 1
)

echo.
echo [OK] Da tao installer Python:
echo %OUTPUT%
exit /b 0
