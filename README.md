# CareerAI

AI 驱动的职业规划平台，集成职业能力评估、职业路径规划、简历分析等功能。

## 项目结构

```
carerr-ai/
├── career-agent/        # 主后端（FastAPI + AI Agent）
├── career-db-service/   # 数据库微服务（FastAPI）
├── career-frontend/     # 前端（React + Vite + Tailwind）
├── docker-compose.yml   # 一键部署配置
└── deploy.sh            # 部署脚本
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React、TypeScript、Vite、Tailwind CSS |
| 主后端 | Python、FastAPI、LiteLLM |
| 数据库服务 | Python、FastAPI、MySQL、Qdrant（向量检索） |
| 容器化 | Docker、Docker Compose |

## 快速启动

### 环境要求

- Docker & Docker Compose
- 有效的 LLM API Key（支持 OpenAI 格式，可使用第三方中转）

### 1. 配置环境变量

```bash
cp career-agent/.env.example career-agent/.env
# 编辑 .env，填写 LLM_MODEL、LLM_API_KEY 等必填项
```

### 2. 启动服务

```bash
docker compose up -d
```

服务启动后：
- 前端：`http://localhost`
- API 文档：`http://localhost:8000/docs`

## API 调用

`POST /invoke/custom` — 无状态调用入口，调用方自带模型配置。

```bash
curl -X POST http://localhost:8000/invoke/custom \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4o",
    "api_key": "your_api_key",
    "message": "帮我分析一下我的职业发展方向",
    "session_id": "user-001"
  }'
```

**参数说明**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model` | string | ✅ | 模型名，如 `openai/gpt-4o` |
| `api_key` | string | ✅ | 对应模型的 API Key |
| `base_url` | string | ❌ | 自定义 API 地址（使用第三方中转时填写） |
| `system_prompt` | string | ❌ | 系统提示词 |
| `message` | string | ✅ | 本轮用户消息 |
| `allowed_tools` | array | ❌ | 工具白名单，`[]` 不使用工具，不传则使用全部工具 |
| `session_id` | string | ❌ | 会话 ID，传相同值保持多轮对话历史 |

**返回示例**

```json
{
  "result": "...",
  "elapsed_ms": 910,
  "model": "openai/gpt-4o",
  "session_id": "user-001"
}
```

## Agent 工具

| 工具 | 功能 |
|------|------|
| `query_profile` | 查询用户个人资料 |
| `update_profile` | 更新用户个人资料 |
| `query_my_assessments` | 查询用户能力评估记录 |
| `query_my_plans` | 查询用户职业计划 |
| `assessment` | 执行职业能力评估 |
| `career_plan` | 生成职业发展规划 |
| `resume` | 简历分析与优化 |

## 开发

```bash
# 仅启动后端开发服务
cd career-agent
uv run uvicorn main:app --reload

# 仅启动前端开发服务
cd career-frontend
npm install && npm run dev
```
