#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ -x "$project_dir/backend/.venv/bin/python" ] && [ -d "$project_dir/frontend/node_modules" ] || {
  echo "依赖尚未安装，请先运行 ./scripts/setup.sh。" >&2
  exit 1
}

(cd "$project_dir/backend" && .venv/bin/pytest)
(cd "$project_dir/frontend" && npm run check)
