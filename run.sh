#!/usr/bin/env bash
# Start the Date with a Book app. Creates the Python environment on first run.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Setting up Python environment (first run only)…"
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

if [ -f .env ]; then
  set -a; . ./.env; set +a
else
  echo "Note: no .env file found — copy .env.example to .env and add your ANTHROPIC_API_KEY."
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
echo "Date with a Book is running at http://localhost:${PORT}  (Ctrl+C to stop)"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
