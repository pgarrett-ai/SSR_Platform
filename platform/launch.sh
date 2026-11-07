#!/usr/bin/env bash
# Start the API (:8001), the EDGAR events worker and the frontend (:5173) in this terminal.
# Ctrl-C stops all three. On Windows, use platform_launch.bat.
set -euo pipefail
cd "$(dirname "$0")"
PY="$PWD/backend/.venv/bin/python"
[ -x "$PY" ] || PY="$PWD/backend/.venv/Scripts/python.exe"
if [ ! -x "$PY" ]; then
  echo "no venv: python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt"
  exit 1
fi
trap 'kill 0' EXIT INT TERM
(cd backend && "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8001) &
(cd backend && "$PY" -m app.worker) &
npm --prefix frontend run dev &
for _ in $(seq 30); do
  if curl -sf http://127.0.0.1:8001/api/health >/dev/null; then
    echo "ready: open http://localhost:5173"
    break
  fi
  sleep 1
done
wait
