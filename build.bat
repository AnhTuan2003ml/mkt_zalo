@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD=python"
if defined BUILD_PYTHON set "PYTHON_CMD=%BUILD_PYTHON%"

echo Building with %PYTHON_CMD%
%PYTHON_CMD% -m PyInstaller --clean --noconfirm ".\ZaloMemberTool.spec"
if errorlevel 1 exit /b %errorlevel%

if not exist "dist\Nexus.exe" (
    echo Missing dist\Nexus.exe build output.
    exit /b 1
)

echo Built dist\Nexus.exe
