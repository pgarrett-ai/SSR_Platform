@echo off
rem Platform launcher: API (:8001) + events worker + frontend (:5173).
rem The worker's worker.lock freshness guard makes a duplicate start exit cleanly.
cd /d "%~dp0backend"
if not exist .venv\Scripts\python.exe (
    echo No venv found. Run:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)
start "platform-api" .venv\Scripts\python.exe -m uvicorn app.main:app --port 8001
start "platform-worker" .venv\Scripts\python.exe -m app.worker
cd /d "%~dp0frontend"
start "platform-frontend" cmd /c "npm run dev"
echo Platform starting: API http://localhost:8001 (docs: /docs) + worker + frontend http://localhost:5173
