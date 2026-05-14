# GitHub Secrets 配置说明

GitHub → 仓库 → Settings → Secrets and variables → Actions

## Secrets 列表

### SERVER_HOST
服务器公网 IP
```
1.94.5.169
```

### SERVER_USER
SSH 登录用户名
```
root
```

### SSH_PRIVATE_KEY
服务器 SSH 私钥内容（用于 CI 自动部署）
```
（粘贴私钥文件内容，以 -----BEGIN ... PRIVATE KEY----- 开头）
```

### BACKEND_ENV
后端 .env 文件完整内容，部署时写入服务器 career-agent/.env
```
LLM_MODEL=openai/glm-4-airx
LLM_API_KEY=sk-8TDbJTR74TJtBZV1DsMYdnwe3kC3PXYj1ZGD5WqxRwr3sn3a
LLM_BASE_URL=https://www.dmxapi.cn/v1
AGENT_SYSTEM_PROMPT=你是我的人工智能助手，协助我完成各种任务。
MCP_URL=http://115.190.165.29:5235/yoolee/huita-Search/sse
DB_HOST=host.docker.internal
DB_PORT=3306
DB_USER=career_app
DB_PASSWORD=CareerApp2025!
# career_app 权限：SELECT/INSERT/UPDATE/DELETE/CREATE/INDEX/ALTER（无DROP）
DB_NAME=career_agent
DB_POOL_MIN=1
DB_POOL_MAX=5
JWT_SECRET=cAr33r-AI-s3cR3t-K3y-2026!xYz
ADMIN_USERNAMES=admin
API_HOST=0.0.0.0
API_PORT=8000
REDIS_URL=redis://host.docker.internal:6379/0
QDRANT_URL=http://host.docker.internal:6333
```

### DB_SERVICE_ENV
career-db-service 的 .env 内容（如有独立 DB 服务）
```
（根据 career-db-service 实际配置填写）
```

### GITHUB_TOKEN
GitHub 自动提供，无需手动配置，用于推送镜像到 GHCR。

---

## 说明

- `BACKEND_ENV` 每次部署时整体写入服务器，修改后需重新触发 CI 部署才生效
- `SSH_PRIVATE_KEY` 对应服务器 `~/.ssh/authorized_keys` 里的公钥
- 本文件不要 commit 敏感信息的实际值，此处仅作结构说明（API Key 已脱敏处理请自行替换）
