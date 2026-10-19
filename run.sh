#!/usr/bin/env bash
# Start the shop support stack in dependency order, or reset the database.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/.run" "$ROOT/runs/failing" "$ROOT/reports" "$ROOT/.phoenix"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export TOOLBOX_DB_PASSWORD="${TOOLBOX_DB_PASSWORD:-support_toolbox_dev}"
export POSTGRES_PORT="${POSTGRES_PORT:-5433}"
export POSTGRES_USER="${POSTGRES_USER:-postgres}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
export POSTGRES_DB="${POSTGRES_DB:-support}"
export PHOENIX_PORT="${PHOENIX_PORT:-6006}"
export PHOENIX_HOST="${PHOENIX_HOST:-127.0.0.1}"
export PHOENIX_WORKING_DIR="$ROOT/.phoenix"

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Missing $PYTHON. Create the 3.12 virtualenv first." >&2
  exit 1
fi

PG_CONTAINER="${PG_CONTAINER:-maven-support-pg}"

http_code() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$1" 2>/dev/null || true)"
  if [[ -z "$code" || "$code" == "000" ]]; then
    echo 000
  else
    echo "$code"
  fi
}

wait_http() {
  local url="$1"
  local name="$2"
  local i code
  for i in $(seq 1 60); do
    code="$(http_code "$url")"
    if [[ "$code" != "000" ]]; then
      return 0
    fi
    sleep 0.5
  done
  echo "$name did not become ready ($url)" >&2
  return 1
}

pid_alive() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  local pid
  pid="$(cat "$file")"
  kill -0 "$pid" 2>/dev/null
}

start_bg() {
  local name="$1"
  shift
  local pidfile="$ROOT/.run/${name}.pid"
  if pid_alive "$pidfile"; then
    return 0
  fi
  PYTHONUNBUFFERED=1 nohup "$@" >"$ROOT/.run/${name}.log" 2>&1 &
  local pid=$!
  echo "$pid" >"$pidfile"
  disown "$pid" 2>/dev/null || true
}

stop_pid() {
  local name="$1"
  local pidfile="$ROOT/.run/${name}.pid"
  if pid_alive "$pidfile"; then
    kill "$(cat "$pidfile")" 2>/dev/null || true
    sleep 0.3
    kill -9 "$(cat "$pidfile")" 2>/dev/null || true
  fi
  rm -f "$pidfile"
}

start_postgres() {
  if docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    return 0
  fi
  if docker ps -a --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    docker start "$PG_CONTAINER" >/dev/null
    return 0
  fi
  docker run -d --name "$PG_CONTAINER" \
    -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    -e POSTGRES_DB="$POSTGRES_DB" \
    -p "${POSTGRES_PORT}:5432" \
    postgres:16-alpine >/dev/null
}

wait_postgres() {
  local i
  for i in $(seq 1 60); do
    if docker exec "$PG_CONTAINER" pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "Postgres did not become ready" >&2
  return 1
}

psql_exec() {
  docker exec -i "$PG_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"
}

cmd_reset() {
  start_postgres
  wait_postgres
  psql_exec -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
  psql_exec <"$ROOT/db/seed.sql"
  psql_exec <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'support_toolbox') THEN
    CREATE ROLE support_toolbox LOGIN PASSWORD '${TOOLBOX_DB_PASSWORD}';
  ELSE
    ALTER ROLE support_toolbox WITH LOGIN PASSWORD '${TOOLBOX_DB_PASSWORD}';
  END IF;
END
\$\$;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO support_toolbox;
GRANT USAGE ON SCHEMA public TO support_toolbox;
GRANT SELECT ON users, customer_orders TO support_toolbox;
GRANT SELECT, INSERT ON actions_log TO support_toolbox;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO support_toolbox;
SQL
  echo "database reset"
}

render_toolbox_config() {
  TOOLBOX_DB_PASSWORD="$TOOLBOX_DB_PASSWORD" POSTGRES_PORT="$POSTGRES_PORT" \
    "$PYTHON" - <<'PY'
import os
from pathlib import Path
text = Path("mcp_toolbox/tools.yaml").read_text()
text = text.replace("${TOOLBOX_DB_PASSWORD}", os.environ["TOOLBOX_DB_PASSWORD"])
text = text.replace("port: 5433", f"port: {os.environ['POSTGRES_PORT']}")
Path(".run/tools.yaml").write_text(text)
PY
}

start_toolbox() {
  render_toolbox_config
  if [[ "$(http_code http://127.0.0.1:5100/)" != "000" ]]; then
    return 0
  fi
  start_bg toolbox npx -y @toolbox-sdk/server \
    --config "$ROOT/.run/tools.yaml" \
    --enable-api \
    --address 127.0.0.1 \
    --port 5100
  wait_http "http://127.0.0.1:5100/" "Toolbox"
}

start_judge() {
  if [[ "$(http_code http://127.0.0.1:10002/health)" == "200" ]]; then
    return 0
  fi
  start_bg judge "$PYTHON" -m guards.judge_server
  wait_http "http://127.0.0.1:10002/health" "Judge"
}

start_masker() {
  if [[ "$(http_code http://127.0.0.1:10003/health)" == "200" ]]; then
    return 0
  fi
  start_bg masker "$PYTHON" -m guards.masker_server
  wait_http "http://127.0.0.1:10003/health" "Masker"
}

start_phoenix() {
  if [[ "$(http_code http://127.0.0.1:${PHOENIX_PORT}/)" != "000" ]]; then
    return 0
  fi
  start_bg phoenix "$PYTHON" -m phoenix.server.main serve
  wait_http "http://127.0.0.1:${PHOENIX_PORT}/" "Phoenix"
}

start_web() {
  if [[ "$(http_code http://127.0.0.1:8000/health)" != "000" ]]; then
    return 0
  fi
  start_bg web "$PYTHON" -m support.web
  wait_http "http://127.0.0.1:8000/health" "Web"
}

cmd_status() {
  python_status() {
    local name="$1" url="$2"
    local code
    code="$(http_code "$url")"
    if [[ "$code" == "000" ]]; then
      echo "$name down"
    else
      echo "$name up ($code)"
    fi
  }
  if docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    echo "postgres up :$POSTGRES_PORT"
  else
    echo "postgres down"
  fi
  python_status toolbox "http://127.0.0.1:5100/"
  python_status judge "http://127.0.0.1:10002/health"
  python_status masker "http://127.0.0.1:10003/health"
  python_status phoenix "http://127.0.0.1:${PHOENIX_PORT}/"
  python_status web "http://127.0.0.1:8000/health"
}

cmd_stop() {
  stop_pid web
  stop_pid judge
  stop_pid masker
  stop_pid toolbox
  stop_pid phoenix
  echo "stopped app processes; postgres container left running"
}

cmd_stack() {
  start_postgres
  wait_postgres
  start_toolbox
  start_judge
  start_masker
  start_phoenix
}

usage() {
  cat <<EOF
Usage: ./run.sh <command>
  reset     drop and recreate the shop database
  status    show which processes answer
  web       start the stack, then the web UI on :8000
  cli       start the stack, then the terminal desk
  stop      stop toolbox, judge, masker, phoenix and web
  failing   stop the Judge and record one error turn in runs/failing
EOF
}

cmd="${1:-status}"
case "$cmd" in
  reset) cmd_reset ;;
  status) cmd_status ;;
  stop) cmd_stop ;;
  web)
    cmd_stack
    start_web
    echo "web http://127.0.0.1:8000"
    ;;
  cli)
    cmd_stack
    exec "$PYTHON" -m support.cli
    ;;
  failing)
    cmd_stack
    start_web
    stop_pid judge
    "$PYTHON" - <<'PY'
import json, os, urllib.request
from pathlib import Path
body = json.dumps({"email": "alice.jones@example.com", "password": "alice"}).encode()
req = urllib.request.Request("http://127.0.0.1:8000/api/login", data=body, headers={"content-type": "application/json"})
urllib.request.urlopen(req).read()
chat = json.dumps({"user_id": "alice.jones@example.com", "message": "Where is my last order?"}).encode()
req = urllib.request.Request("http://127.0.0.1:8000/api/chat", data=chat, headers={"content-type": "application/json"})
text = urllib.request.urlopen(req).read().decode()
print(text)
events = [json.loads(line) for line in text.splitlines() if line.strip()]
turn_id = events[0]["turn_id"]
src = Path("runs") / f"{turn_id}.json"
dest = Path("runs/failing") / src.name
dest.write_text(src.read_text())
src.unlink()
print(f"saved {dest}")
PY
    ;;
  *)
    usage
    exit 1
    ;;
esac
