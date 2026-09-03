#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backend_python="$project_dir/backend/.venv/bin/python"

[ -x "$backend_python" ] && [ -d "$project_dir/frontend/node_modules" ] || {
  echo "依赖尚未安装，请先运行 ./scripts/setup.sh。" >&2
  exit 1
}

set -a
[ ! -f "$project_dir/backend/.env" ] || . "$project_dir/backend/.env"
[ ! -f "$project_dir/frontend/.env.local" ] || . "$project_dir/frontend/.env.local"
set +a

export RIFFLOOM_API_BASE_URL=${RIFFLOOM_API_BASE_URL:-http://127.0.0.1:8100/api/v1}
export RIFFLOOM_PUBLIC_ORIGIN=${RIFFLOOM_PUBLIC_ORIGIN:-http://127.0.0.1:3100}

"$backend_python" -m uvicorn app.main:app --app-dir "$project_dir/backend" --host 127.0.0.1 --port 8100 &
backend_pid=$!
frontend_pid=
trap 'kill "$backend_pid" "$frontend_pid" 2>/dev/null || true' EXIT INT TERM

(cd "$project_dir/frontend" && npm run dev -- --hostname 127.0.0.1 --port 3100) &
frontend_pid=$!
wait "$backend_pid" "$frontend_pid"
