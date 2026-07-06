#!/usr/bin/env bash
# Launch the SSR Platform from a WSL (or Git Bash) terminal.
# Backend :8001 + frontend :5173 run as Windows-side processes via interop,
# so your Windows browser reaches them at http://localhost:5173 as usual.
# Logs: platform/.logs/   Stop with: ./stop.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR/.logs"

listening() {
  powershell.exe -NoProfile -Command \
    "if (Get-NetTCPConnection -LocalPort $1 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" \
    2>/dev/null
}

if listening 8001; then
  echo "backend already running on :8001"
else
  (cd "$DIR/backend" && nohup ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8001 \
    >"$DIR/.logs/backend.log" 2>&1 &)
  echo "backend starting on :8001  (log: platform/.logs/backend.log)"
fi

if listening 5173; then
  echo "frontend already running on :5173"
else
  # MSYS_NO_PATHCONV stops Git Bash mangling /c into C:\ (no effect under WSL)
  (cd "$DIR/frontend" && MSYS_NO_PATHCONV=1 nohup cmd.exe /c npm run dev \
    >"$DIR/.logs/frontend.log" 2>&1 &)
  echo "frontend starting on :5173  (log: platform/.logs/frontend.log)"
fi

echo "open http://localhost:5173 in your Windows browser"
