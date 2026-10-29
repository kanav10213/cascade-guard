@echo off
rem Cascade Guard: double-click to install (first run) and start. Needs Python 3.12 or newer.
cd /d "%~dp0"
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>nul
if errorlevel 1 (
  echo Python 3.12 or newer is needed. Install it from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo Setting up for the first time, this takes a minute...
  py -3 -m venv .venv || goto :fail
)
rem Install only on the first run or when the tested versions change, so later starts work offline.
fc /b constraints.txt .venv\installed-constraints.txt >nul 2>&1
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt || goto :fail
  copy /y constraints.txt .venv\installed-constraints.txt >nul
)
if not exist .env (
  .venv\Scripts\cascade-guard.exe init >nul
  echo.
  echo  A settings file .env was created and opens in Notepad now.
  echo  Paste your API key after ANTHROPIC_API_KEY= , save, and close Notepad.
  echo  No key yet? Change LLM_PROVIDER=anthropic to LLM_PROVIDER=fake to try it free.
  echo.
  notepad .env
)
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8000"
.venv\Scripts\cascade-guard.exe serve
exit /b 0
:fail
echo Setup failed. Check your internet connection and try again.
pause
exit /b 1
