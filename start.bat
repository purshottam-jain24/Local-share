@echo off
REM LocalShare launcher for Windows - double-click to run.
REM Creates a virtual environment on first run, installs dependencies,
REM starts the server and opens it in your browser.
REM   start.bat          start (HTTPS)
REM   start.bat --http   start over plain HTTP
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.9 or newer is required. Get it from https://www.python.org/downloads/
  echo During install, tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo ==^> Creating virtual environment ^(first run only^)...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Could not create a virtual environment.
    pause
    exit /b 1
  )
)

REM Reinstall only when requirements.txt changes (stamp is reset whenever it is edited).
set "NEED_INSTALL="
if not exist ".venv\.requirements-installed" set "NEED_INSTALL=1"
if exist ".venv\.requirements-installed" (
  for /f %%i in ('powershell -NoProfile -Command "if ((Get-Item requirements.txt).LastWriteTime -gt (Get-Item .venv\.requirements-installed).LastWriteTime) { 1 } else { 0 }"') do (
    if "%%i"=="1" set "NEED_INSTALL=1"
  )
)
if defined NEED_INSTALL (
  echo ==^> Installing dependencies...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
  )
  type nul > ".venv\.requirements-installed"
)

".venv\Scripts\python.exe" share.py --open %*
pause
