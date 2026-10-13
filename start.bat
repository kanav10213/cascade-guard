@echo off
cd /d "%~dp0"
echo Installing requirements (first run only takes a minute)...
py -m pip install -q -r requirements.txt
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8000"
echo Cascade Guard is starting at http://localhost:8000  (press Ctrl+C to stop)
py -m uvicorn app.api:app --port 8000
