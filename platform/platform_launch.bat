@echo off
rem Unified platform launcher — backend only (Phase 0). The merged frontend arrives in Phase 2;
rem until then the capstack frontend (npm run dev in capstack/frontend) works against this API.
cd /d "%~dp0backend"
if not exist .venv\Scripts\python.exe (
    echo No venv found. Run:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)
start "platform-api" .venv\Scripts\python.exe -m uvicorn app.main:app --port 8001
echo Platform API starting on http://localhost:8001  (docs: /docs)
