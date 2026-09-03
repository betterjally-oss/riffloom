# 安装说明

## 环境要求

- Python 3.11 或更高版本
- Node.js 20 或更高版本
- npm
- macOS 或 Linux；Windows 建议使用 WSL 2

## 自动安装

```bash
git clone https://github.com/betterjally-oss/riffloom.git
cd riffloom
./scripts/setup.sh
./scripts/dev.sh
```

首次安装会创建 `backend/.venv`、安装依赖、创建本地 `data/riffloom.db` 并执行数据库迁移。启动后访问：

- 前端：<http://127.0.0.1:3100>
- API：<http://127.0.0.1:8100/api/v1/health>
- API 文档：<http://127.0.0.1:8100/docs>

按 `Ctrl+C` 会同时停止前后端。

## 手动安装

```bash
mkdir -p data uploads
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
(cd backend && .venv/bin/alembic upgrade head)
cd frontend && npm ci
```

分别启动两个终端：

```bash
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8100
```

```bash
cd frontend
RIFFLOOM_API_BASE_URL=http://127.0.0.1:8100/api/v1 npm run dev -- --hostname 127.0.0.1 --port 3100
```

## 本地配置

默认值已经可以直接运行。如需修改：

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

`scripts/dev.sh` 会读取这两个文件。真实 Provider 的配置见[配置说明](configuration.md)。

## 常见问题

- **端口被占用**：停止占用 8100 或 3100 的进程，或手动指定其他端口并同步修改 CORS 与 API 地址。
- **Python 版本不正确**：设置 `RIFFLOOM_PYTHON_BIN=/path/to/python3.11` 后重新执行安装脚本。
- **数据库结构过期**：运行 `backend/.venv/bin/alembic -c backend/alembic.ini upgrade head`。
