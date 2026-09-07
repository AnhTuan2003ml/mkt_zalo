@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD=python"
if defined BUILD_PYTHON set "PYTHON_CMD=%BUILD_PYTHON%"

if not exist ".env" (
    echo Missing .env at project root. Create .env with SENDMAIL_USER/SENDMAIL_PASS/SECRET_KEY before building.
    exit /b 1
)

echo Building with %PYTHON_CMD%
%PYTHON_CMD% -m PyInstaller --clean --noconfirm ".\ZaloMemberTool.spec"
if errorlevel 1 exit /b %errorlevel%

if not exist "dist\Nexus.exe" (
    echo Missing dist\Nexus.exe build output.
    exit /b 1
)

%PYTHON_CMD% build_check.py "dist\Nexus.exe"
if errorlevel 1 (
    echo Build check FAILED - see messages above.
    exit /b 1
)

echo Built dist\Nexus.exe
