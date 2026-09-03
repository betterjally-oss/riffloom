# 配置说明

Riffloom 通过环境变量选择 Provider。仓库默认值适合本地演示，并且不会访问真实第三方服务。

## 默认安全模式

| 能力 | 默认值 | 行为 |
| --- | --- | --- |
| 登录 | `demo_headers` | 使用本地演示用户，不需要邀请码 |
| 数据库 | SQLite | 写入本机 `data/riffloom.db` |
| 采集 | `sandbox-v1` | 返回固定示例数据 |
| 文本模型 | `mock-v1` | 返回模拟结果 |
| 封面 | `mock-cover-v1` | 返回模拟方案，不生成付费图片 |
| 转写 | `disabled` | 不上传音视频 |
| 飞书 | `sandbox-feishu-v1` | 不向外部数据表写入 |

## 配置文件

后端读取 `backend/.env`，前端读取 `frontend/.env.local`。从示例开始：

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

不要把配置文件提交到 Git。`NEXT_PUBLIC_*` 会打包到浏览器，绝对不能放密钥。

## 可选 Provider

- **TikHub / 小红书采集**：设置 `TIKHUB_API_KEY`，再按代码支持的 Provider ID 修改 `RIFFLOOM_COLLECTION_PROVIDER`。
- **DeepSeek 文本生成**：设置 `DEEPSEEK_API_KEY`，并修改 `RIFFLOOM_MODEL_PROVIDER`。
- **火山方舟视觉、封面或转写**：设置 `ARK_API_KEY`，再启用对应 Provider。
- **飞书 OAuth 与多维表格**：设置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 和回调地址；完成应用权限配置后才可启用真实写入。

Provider ID 和必需条件以 `backend/app/core/config.py` 与 `backend/app/core/deployment_safety.py` 为准。启用真实服务前，请先在测试账号和隔离数据表中验证，并设置供应商侧费用与权限上限。

## 生产部署提醒

本仓库不绑定特定云平台。生产环境至少应使用：

- 随机且保密的认证密钥；
- HTTPS 与受控 CORS；
- 独立数据库、备份和恢复演练；
- 最小权限的第三方凭据；
- 日志脱敏、速率限制和费用告警。

本地 SQLite 配置面向开发和演示，不适合多实例生产部署。
