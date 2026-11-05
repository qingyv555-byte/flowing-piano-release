@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
if defined AI_PIANO_PYTHON (
    "%AI_PIANO_PYTHON%" -m ai_piano_studio.nocturne %*
) else if exist "%~dp0.venv-dev\Scripts\python.exe" (
    "%~dp0.venv-dev\Scripts\python.exe" -m ai_piano_studio.nocturne %*
) else if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m ai_piano_studio.nocturne %*
) else if exist "%~dp0..\flowing-piano\.venv-dev\Scripts\python.exe" (
    "%~dp0..\flowing-piano\.venv-dev\Scripts\python.exe" -m ai_piano_studio.nocturne %*
) else (
    py -3.12 -m ai_piano_studio.nocturne %*
)
if errorlevel 1 (
    echo Nocturne could not start. Install the project dependencies in Python 3.12.
    pause
)
