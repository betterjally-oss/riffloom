#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${RIFFLOOM_PYTHON_BIN:-python3}

command -v "$python_bin" >/dev/null 2>&1 || {
  echo "找不到 Python。请安装 Python 3.11+，或设置 RIFFLOOM_PYTHON_BIN。" >&2
  exit 1
}
command -v npm >/dev/null 2>&1 || {
  echo "找不到 npm。请安装 Node.js 20+。" >&2
  exit 1
}

mkdir -p "$project_dir/data" "$project_dir/uploads"
if [ ! -x "$project_dir/backend/.venv/bin/python" ]; then
  "$python_bin" -m venv "$project_dir/backend/.venv"
fi
"$project_dir/backend/.venv/bin/python" -m pip install -r "$project_dir/backend/requirements-dev.txt"
(cd "$project_dir/backend" && .venv/bin/alembic upgrade head)
(cd "$project_dir/frontend" && npm_config_cache="${RIFFLOOM_NPM_CACHE:-$project_dir/.npm-cache}" npm ci)

echo "安装完成。运行 ./scripts/dev.sh 启动 Riffloom。"
