#!/usr/bin/env bash
# Copy the app to the test VM and (re)start it there.
#   ./deploy.sh                 deploy and restart
#   ./deploy.sh status          show whether it is running + last log lines
#   ./deploy.sh logs            follow the server log
#   ./deploy.sh stop            stop the app
# Override the target with DWAB_HOST=user@host and DWAB_DIR=folder.
set -euo pipefail
cd "$(dirname "$0")"

HOST="${DWAB_HOST:-eric@192.168.109.129}"
DIR="${DWAB_DIR:-dwab4D}"
PORT="${PORT:-8000}"

remote() { ssh -o BatchMode=yes "$HOST" "cd ~/$DIR && $1"; }

case "${1:-deploy}" in
  status)
    remote 'if [ -f app.pid ] && kill -0 "$(cat app.pid)" 2>/dev/null; then echo "running (pid $(cat app.pid))"; else echo "not running"; fi; tail -n 15 app.log 2>/dev/null || true'
    exit ;;
  logs)
    ssh -t "$HOST" "tail -n 50 -f ~/$DIR/app.log"
    exit ;;
  stop)
    remote 'if [ -f app.pid ]; then kill "$(cat app.pid)" 2>/dev/null || true; rm -f app.pid; fi; echo stopped'
    exit ;;
  deploy) ;;
  *) echo "usage: $0 [deploy|status|logs|stop]" >&2; exit 2 ;;
esac

echo "→ Copying files to $HOST:~/$DIR"
ssh -o BatchMode=yes "$HOST" "mkdir -p ~/$DIR"
rsync -az --delete \
  --exclude .git/ --exclude .venv/ --exclude data/ --exclude .env \
  --exclude __pycache__/ --exclude .pytest_cache/ --exclude app.pid --exclude app.log \
  ./ "$HOST:$DIR/"

echo "→ Installing dependencies and restarting"
ssh -o BatchMode=yes "$HOST" "cd ~/$DIR && PORT=$PORT bash -s" <<'EOF'
set -euo pipefail
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt
if [ -f app.pid ] && kill -0 "$(cat app.pid)" 2>/dev/null; then
  kill "$(cat app.pid)"
  for _ in $(seq 20); do kill -0 "$(cat app.pid)" 2>/dev/null || break; sleep 0.25; done
fi
[ -f .env ] || echo "  note: no .env on the VM yet — AI features need ANTHROPIC_API_KEY (copy .env.example to .env)"
set -a; [ -f .env ] && . ./.env; set +a
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT" >> app.log 2>&1 < /dev/null &
echo $! > app.pid
for _ in $(seq 40); do
  curl -fs -o /dev/null "http://127.0.0.1:$PORT/" && { echo "  app is up (pid $(cat app.pid))"; exit 0; }
  sleep 0.25
done
echo "  app did not start — last log lines:"; tail -n 20 app.log; exit 1
EOF

IP="${HOST#*@}"
echo "✓ Open http://$IP:$PORT"
