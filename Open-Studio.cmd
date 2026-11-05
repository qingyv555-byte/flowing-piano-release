@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "RUNTIME="
if defined AI_PIANO_PYTHON call :use_python "%AI_PIANO_PYTHON%"
if not defined RUNTIME call :use_python "%CD%\.venv-dev\Scripts\pythonw.exe"
if not defined RUNTIME call :use_python "%CD%\.venv\Scripts\pythonw.exe"
if not defined RUNTIME (
    echo Preparing a local Python 3.12 runtime...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\tools\setup-development-environment.ps1"
    if errorlevel 1 goto :missing_runtime
    call :use_python "%CD%\.venv-dev\Scripts\pythonw.exe"
)
if not defined RUNTIME goto :missing_runtime
if /i "%~1"=="--smoke-test" goto :smoke_test
start "" "%RUNTIME%" -m ai_piano_studio.studio %*
exit /b 0

:smoke_test
"%CONSOLE_PYTHON%" -m ai_piano_studio.studio %*
exit /b %errorlevel%

:use_python
set "CONSOLE_PYTHON=%~dp1python.exe"
if not exist "%~1" exit /b 1
if not exist "%CONSOLE_PYTHON%" exit /b 1
"%CONSOLE_PYTHON%" -c "import sys, keyring, numpy, PySide6, ai_piano_studio; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "RUNTIME=%~1"
exit /b 0

:missing_runtime
echo No usable Python 3.12 runtime was found.
echo Install Python 3.12, then run tools\setup-development-environment.ps1.
pause
exit /b 1
