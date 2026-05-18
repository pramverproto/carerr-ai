# CareerAI

AI 驱动的职业规划平台，集成职业能力评估、职业路径规划、简历分析等功能。

## 项目结构

```
career-ai/
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
- MySQL 数据库（执行 `career-agent/db/schema.sql` 初始化）
- Qdrant 向量数据库（默认 `localhost:6333`）
- Redis（默认 `localhost:6379`）

### 1. 配置环境变量

```bash
cp career-agent/.env.example career-agent/.env
# 必填项：LLM_MODEL、LLM_API_KEY、JWT_SECRET、DB_PASSWORD
```

### 2. 启动服务

```bash
docker compose up -d
```

服务启动后：
- 前端：`http://localhost`
- API 文档：`http://localhost:8000/docs`

## API 调用

`POST /invoke/custom` — 无鉴权调用入口，调用方自带模型配置。

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
| `system_prompt` | string | ❌ | 系统提示词，默认"你是一个有帮助的 AI 助手。" |
| `message` | string | ✅ | 本轮用户消息 |
| `allowed_tools` | array | ❌ | 工具白名单，`[]` 不使用工具，不传则使用全部工具 |
| `mcp_url` | string | ❌ | MCP 服务地址（可选外挂工具） |
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
| `update_profile` | 更新用户个人资料字段 |
| `query_my_assessments` | 查询用户所有能力评估记录 |
| `query_my_plans` | 查询用户所有职业计划 |
| `query_today_tasks` | 查询今日待办任务 |
| `run_assessment` | 执行完整职业能力评估 |
| `generate_and_save_report` | 生成并保存评估报告 |
| `match_careers` | 匹配适合的职业方向 |
| `generate_career_plan` | 生成详细职业发展规划 |
| `generate_action_plan` | 生成短期行动计划 |
| `save_resume_data` | 保存解析后的简历数据 |
| `rewrite_resume_text` | 优化简历文本表达 |
| `delegate_task` | 委托子 Agent 执行任务 |

## 开发

```bash
# 仅启动后端开发服务
cd career-agent
uv run uvicorn api:app --reload

# 仅启动前端开发服务
cd career-frontend
npm install && npm run dev
```
