import asyncio
import base64
import datetime
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import aiomysql
import redis.asyncio as aioredis
from dotenv import load_dotenv

# 在导入其他模块之前加载 .env，确保 JWT_SECRET 等环境变量可用
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from auth import get_current_user, hash_password, verify_password, create_token

from agent.agent import Agent
from agent.agent_config import (
    MAIN_AGENT_CONFIG, SUB_AGENT_CONFIGS, REPORT_AGENT_CONFIGS,
    ASSESSMENT_AGENT_CONFIG, CAREER_AGENT_CONFIG, CAREER_PLAN_AGENT_CONFIG,
    RESUME_EXTRACT_AGENT_CONFIG, DB_CONFIG,
    LEARN_PLAN_AGENT_CONFIGS,
)
from agent.logger import get_logger
from agent.providers.llm import LLMProvider
from agent.tools.mcp import MCPClient
import agent.memory.db as memory_db
from agent.learn_plan_schema import ALL_CREATE_SQLS as LEARN_PLAN_CREATE_SQLS

logger = get_logger("api")
import agent.tools.career       # 注册 match_careers
import agent.tools.career_plan  # 注册 generate_career_plan
import agent.tools.action_plan  # 注册 generate_action_plan
import agent.tools.resume       # 注册 save_resume_data / rewrite_resume_text
import agent.tools.chat_tools   # 注册 query_my_assessments / query_my_plans / query_today_tasks / query_profile / update_profile
from agent.tools.context import current_user_id

# ------------------------------------------------------------------ #
#  O*NET 数据文件路径映射（在模块加载时读取，避免每次请求重复 IO）     #
# ------------------------------------------------------------------ #

_ONET_DIR = Path(__file__).parent / "agent" / "prompts" / "onet_extracted_data"

_ONET_FILES: dict[str, str] = {
    "skills_agent":      "agent2_skills_complete.json",
    "knowledge_agent":   "agent3_knowledge_complete.json",
    "abilities_agent":   "agent4_cognitive_abilities.json",
    "work_styles_agent": "agent5_work_styles.json",
    "interests_agent":   "agent6_interests_complete.json",
    "work_values_agent": "agent7_work_values.json",
}

_ONET_DATA: dict[str, str] = {}

def _load_onet_data() -> None:
    for agent_name, filename in _ONET_FILES.items():
        path = _ONET_DIR / filename
        if path.exists():
            _ONET_DATA[agent_name] = path.read_text(encoding="utf-8")

_load_onet_data()


def _configured_admin_usernames() -> set[str]:
    raw = os.getenv("ADMIN_USERNAMES", "")
    return {name.strip() for name in raw.split(",") if name.strip()}


def _is_configured_admin(username: str | None) -> bool:
    return bool(username and username in _configured_admin_usernames())


# ------------------------------------------------------------------ #
#  Lifespan：startup / shutdown                                         #
# ------------------------------------------------------------------ #

_CREATE_CAREER_PLAN_BLOCKS_SQL = """
CREATE TABLE IF NOT EXISTS career_plan_blocks (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    assessment_id VARCHAR(32)  NOT NULL,
    onetsoc_code  VARCHAR(40)  NOT NULL,
    block_id      VARCHAR(50)  NOT NULL,
    block_json    JSON         NOT NULL,
    generated_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_plan_block (assessment_id, onetsoc_code, block_id),
    KEY idx_plan_assessment (assessment_id, onetsoc_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_CAREER_PATH_PROGRESS_SQL = """
CREATE TABLE IF NOT EXISTS career_path_progress (
    id             BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id        VARCHAR(32)  NOT NULL,
    assessment_id  VARCHAR(32)  NOT NULL,
    path_code      VARCHAR(20)  NOT NULL,
    path_data      JSON         NOT NULL,
    current_stage  INT          NOT NULL DEFAULT 1,
    stage_history  JSON         DEFAULT NULL,
    created_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_path (assessment_id, path_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_ALTER_PLAN_BLOCKS_CODE_LEN = """
ALTER TABLE career_plan_blocks MODIFY onetsoc_code VARCHAR(40) NOT NULL
"""

_ALTER_JOBS_INPUT_SNAPSHOT = """
ALTER TABLE assessment_jobs
ADD COLUMN IF NOT EXISTS input_snapshot JSON NULL AFTER session_id
"""


REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client: aioredis.Redis | None = None

MATCH_CACHE_TTL = 60 * 60 * 24 * 7  # 缓存 7 天


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    await memory_db.init_pool(**DB_CONFIG)
    if memory_db._pool is not None:
        async with memory_db._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_CREATE_CAREER_PLAN_BLOCKS_SQL)
                await cur.execute(_CREATE_CAREER_PATH_PROGRESS_SQL)
                for sql in LEARN_PLAN_CREATE_SQLS:
                    await cur.execute(sql)
                try:
                    await cur.execute(_ALTER_JOBS_INPUT_SNAPSHOT)
                except Exception:
                    pass  # 列已存在时忽略
                try:
                    await cur.execute(_ALTER_PLAN_BLOCKS_CODE_LEN)
                except Exception:
                    pass  # 已修改时忽略
    # 初始化 Redis
    try:
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
        logger.info(f"[Redis] 连接成功: {REDIS_URL}")
    except Exception as e:
        logger.warning(f"[Redis] 连接失败（缓存不可用）: {e}")
        redis_client = None
    yield
    if redis_client:
        await redis_client.aclose()
    await memory_db.close_pool()


# ------------------------------------------------------------------ #
#  限流：按用户 ID（已登录）或客户端 IP（未登录）计数                       #
# ------------------------------------------------------------------ #

def _rate_limit_key(request: Request) -> str:
    """优先按已认证用户限流；未认证或解析失败时退回 IP。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(None, 1)[1].strip()
        try:
            import jwt
            secret = os.getenv("JWT_SECRET", "")
            if secret:
                payload = jwt.decode(token, secret, algorithms=["HS256"])
                uid = payload.get("user_id") or payload.get("sub")
                if uid:
                    return f"user:{uid}"
        except Exception:
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_rate_limit_key)


app = FastAPI(
    title="CareerAI API",
    description="""
## CareerAI 职业 AI 规划系统接口文档

通过以下模块帮助用户完成职业规划全流程：

- **系统** — 健康检查
- **认证** — 注册 / 登录 / 用户信息
- **Agent 调用** — 通用 Agent 单次调用
- **对话** — 多轮 AI 对话（SSE 流式）
- **简历** — 简历文件上传与结构化提取
- **评估** — 多维度职业能力评估
- **职业** — 职业匹配与规划生成
- **计划** — 行动计划管理与每日打卡
- **成长档案** — 历史评估记录与里程碑
""",
    version="1.0.0",
    lifespan=lifespan,
    swagger_ui_parameters={"syntaxHighlight": False},
    docs_url=None,
    redoc_url=None,
    openapi_tags=[
        {"name": "系统", "description": "健康检查，确认服务与数据库连接正常"},
        {"name": "认证", "description": "用户注册、登录、JWT Token 获取与验证"},
        {"name": "Agent 调用", "description": "通用 Agent 单次调用接口，支持内置工具和自定义配置"},
        {"name": "对话", "description": "多轮 AI 对话，支持 SSE 流式输出，保持 session 上下文"},
        {"name": "简历", "description": "上传简历文件（PDF/图片/Word），多模态模型提取结构化信息"},
        {"name": "评估", "description": "六维度职业能力评估（技能、知识、能力、工作风格、兴趣、价值观）"},
        {"name": "职业", "description": "基于评估结果的职业匹配推荐与详细职业规划生成"},
        {"name": "计划", "description": "行动计划创建、周/日任务管理、打卡进度与感悟记录"},
        {"name": "成长档案", "description": "历史评估记录查询、成长里程碑统计、档案删除"},
        {"name": "后台运维", "description": "管理员查看用户、评估任务、数据资源与异常状态"},
    ],
)

# 注册限流器与异常处理器
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# 使用 unpkg CDN（国内可访问）挂载 Swagger UI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="CareerAI API",
        swagger_js_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css",
    )


# ------------------------------------------------------------------ #
#  CORS：允许前端（Vite dev server）跨域访问                              #
# ------------------------------------------------------------------ #
_cors_origins_str = os.getenv("CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()] if _cors_origins_str else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ #
#  Agent 工厂                                                           #
# ------------------------------------------------------------------ #


# 对话界面只开放查询类工具，避免 AI 在对话里误触发耗时的评估/规划流程
_CHAT_ALLOWED_TOOLS = [
    "query_profile",
    "update_profile",
    "query_my_assessments",
    "query_my_plans",
    "query_today_tasks",
]

def _make_agent(session_id: str | None) -> Agent:
    """每次请求创建新 Agent 实例，无共享状态，并发安全。"""
    llm = LLMProvider(
        model=MAIN_AGENT_CONFIG["model"],
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    mcp_url = MAIN_AGENT_CONFIG.get("mcp_url")
    mcp = MCPClient(mcp_url) if mcp_url else None
    return Agent(
        llm=llm,
        system_prompt=MAIN_AGENT_CONFIG["system_prompt"],
        mcp=mcp,
        session_id=session_id,
        allowed_tools=_CHAT_ALLOWED_TOOLS,
    )


# ------------------------------------------------------------------ #
#  Request / Response models                                            #
# ------------------------------------------------------------------ #

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    stream: bool = False


class InvokeRequest(BaseModel):
    agent_name: str
    task: str


class CustomAgentConfig(BaseModel):
    """通用 Agent 调用的完整配置，全部由调用方传入。"""
    # LLM 配置（必填）
    model: str
    api_key: str
    base_url: str | None = None
    # Agent 行为
    system_prompt: str = "你是一个有帮助的 AI 助手。"
    # 消息内容（必填），传相同 session_id 可保持多轮对话历史
    message: str
    # 工具过滤：传 [] 表示不允许任何工具，传 None 表示不限制
    allowed_tools: list[str] | None = None
    # MCP 配置（可选）
    mcp_url: str | None = None
    # 会话/追踪（可选）
    session_id: str | None = None


# ── Auth 请求模型 ────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class ResumeExperience(BaseModel):
    company: str
    title: str
    duration: str
    responsibilities: list[str]
    key_projects: list[dict] | None = None

class ResumeSection(BaseModel):
    candidate: dict                         # 基础信息：姓名、年龄、学历、岗位等
    experiences: list[ResumeExperience]
    skills: list[str]
    certifications: list[str] | None = None

class BigFiveSection(BaseModel):
    O: float = Field(..., ge=0, le=100)
    C: float = Field(..., ge=0, le=100)
    E: float = Field(..., ge=0, le=100)
    A: float = Field(..., ge=0, le=100)
    ES: float = Field(..., ge=0, le=100)
    facets: dict | None = None

class RiasecSection(BaseModel):
    R: float = Field(..., ge=0, le=100)
    I: float = Field(..., ge=0, le=100)
    A: float = Field(..., ge=0, le=100)
    S: float = Field(..., ge=0, le=100)
    E: float = Field(..., ge=0, le=100)
    C: float = Field(..., ge=0, le=100)
    holland_code: str | None = None

class QuizAbilitiesSection(BaseModel):
    verbal:       dict   # { score, percentile, sub_scores }
    reasoning:    dict
    quantitative: dict

class QuizKnowledgeSection(BaseModel):
    business_management:  dict   # { score, percentile, sub_scores }
    tech_engineering:     dict
    humanities_social:    dict

class ThirdPartySection(BaseModel):
    leadership: dict | None = None   # 企业领导力测评
    disc:        dict | None = None  # DISC
    writing:     dict | None = None  # 写作与表达评估

class AssessRequest(BaseModel):
    session_id:  str | None = None

    # 必选
    resume:      ResumeSection
    supplement:  str                        # 个人补充（职业动机、偏好、价值观、自我反思、典型事件）

    # 可选
    bigfive:     BigFiveSection   | None = None
    riasec:      RiasecSection    | None = None
    quiz_abilities: QuizAbilitiesSection | None = None
    quiz_knowledge: QuizKnowledgeSection | None = None
    third_party: ThirdPartySection | None = None


# ------------------------------------------------------------------ #
#  Routes                                                               #
# ------------------------------------------------------------------ #

@app.get("/health", tags=["系统"], summary="健康检查")
async def health():
    db_ok = False
    if memory_db._pool is not None:
        try:
            async with memory_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
            db_ok = True
        except Exception:
            pass
    if not db_ok:
        raise HTTPException(503, "数据库连接异常")
    return {"status": "ok", "database": "connected"}


# ------------------------------------------------------------------ #
#  Auth 路由                                                            #
# ------------------------------------------------------------------ #

@app.post("/auth/register", tags=["认证"], summary="用户注册")
async def auth_register(req: RegisterRequest):
    """用户名唯一，密码 bcrypt 哈希存储，注册成功后直接返回 JWT Token。"""
    if len(req.username) < 2 or len(req.username) > 30:
        raise HTTPException(400, "用户名需 2-30 个字符")
    if not re.match(r'^[\w\u4e00-\u9fa5]+$', req.username):
        raise HTTPException(400, "用户名只能包含字母、数字、下划线和中文")
    if len(req.password) < 8:
        raise HTTPException(400, "密码至少 8 个字符")
    if memory_db._pool is None:
        raise HTTPException(503, "数据库未初始化")

    hashed = hash_password(req.password)
    is_admin = _is_configured_admin(req.username)
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO users (username, password, email, is_admin) VALUES (%s, %s, %s, %s)",
                    (req.username, hashed, req.email, is_admin),
                )
                user_id = cur.lastrowid
            except Exception as e:
                if "Duplicate" in str(e):
                    raise HTTPException(409, "用户名已存在")
                raise

    # 自动认领无主数据（user_id IS NULL）给新注册用户
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            for tbl in ("resume_uploads", "assessment_jobs", "plan_schedules"):
                await cur.execute(
                    f"UPDATE {tbl} SET user_id = %s WHERE user_id IS NULL",
                    (user_id,),
                )

    token = create_token(user_id, req.username)
    return {"user_id": user_id, "username": req.username, "token": token, "is_admin": is_admin}


@app.post("/auth/login", tags=["认证"], summary="用户登录")
async def auth_login(req: LoginRequest):
    """用户登录：验证密码，返回 JWT token。"""
    if memory_db._pool is None:
        raise HTTPException(503, "数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, username, password, is_admin FROM users WHERE username=%s",
                (req.username,),
            )
            row = await cur.fetchone()

    if not row or not verify_password(req.password, row[2]):
        logger.warning(f"[Auth] 登录失败: username={req.username}")
        raise HTTPException(401, "用户名或密码错误")

    user_id = row[0]

    # 自动认领无主数据（user_id IS NULL）给当前用户
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            for tbl in ("resume_uploads", "assessment_jobs", "plan_schedules"):
                await cur.execute(
                    f"UPDATE {tbl} SET user_id = %s WHERE user_id IS NULL",
                    (user_id,),
                )

    token = create_token(user_id, row[1])
    is_admin = bool(row[3]) or _is_configured_admin(row[1])
    return {"user_id": user_id, "username": row[1], "token": token, "is_admin": is_admin}


@app.get("/auth/me", tags=["认证"], summary="获取当前登录用户信息")
async def auth_me(user: dict = Depends(get_current_user)):
    """验证 token 有效性，返回当前用户信息。"""
    if memory_db._pool is None:
        raise HTTPException(503, "数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, username, email, is_admin FROM users WHERE id=%s",
                (user["user_id"],),
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(404, "用户不存在")
    return {"user_id": row[0], "username": row[1], "email": row[2], "is_admin": bool(row[3]) or _is_configured_admin(row[1])}


# ── Auth 辅助函数 ────────────────────────────────────────────────────

async def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    """管理员认证依赖：支持 users.is_admin，也支持 ADMIN_USERNAMES 环境变量兜底。"""
    if memory_db._pool is None:
        raise HTTPException(503, "数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT username, is_admin FROM users WHERE id=%s",
                (user["user_id"],),
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(404, "用户不存在")
    if not (bool(row[1]) or _is_configured_admin(row[0])):
        raise HTTPException(403, "需要管理员权限")
    return {"user_id": user["user_id"], "username": row[0], "is_admin": True}


def _snapshot_profile(snapshot: object) -> dict:
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except (json.JSONDecodeError, TypeError):
            snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    candidate = snapshot.get("resume", {}).get("candidate", {})
    return {
        "name": candidate.get("name", ""),
        "current_title": candidate.get("current_title", ""),
        "education": candidate.get("education", ""),
    }


async def _admin_table_count(cur, table_name: str) -> int:
    try:
        await cur.execute(f"SELECT COUNT(*) AS cnt FROM {table_name}")
        row = await cur.fetchone()
        return int(row["cnt"] if isinstance(row, dict) else row[0])
    except Exception:
        return 0


@app.get("/admin/overview", tags=["后台运维"], summary="后台运维总览")
async def admin_overview(admin: dict = Depends(get_current_admin)):
    """返回管理员首页所需的全局统计、任务状态分布和最近失败任务。"""
    if memory_db._pool is None:
        raise HTTPException(503, "数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            total_users = await _admin_table_count(cur, "users")
            total_assessments = await _admin_table_count(cur, "assessment_jobs")
            total_plans = await _admin_table_count(cur, "plan_schedules")
            total_career_blocks = await _admin_table_count(cur, "career_plan_blocks")

            await cur.execute(
                "SELECT status, COUNT(*) AS cnt FROM assessment_jobs GROUP BY status"
            )
            status_rows = await cur.fetchall()

            await cur.execute(
                """SELECT j.assessment_id, j.user_id, u.username, j.status, j.error,
                          j.created_at, j.updated_at, j.input_snapshot
                   FROM assessment_jobs j
                   LEFT JOIN users u ON u.id = j.user_id
                   WHERE j.status='failed' OR j.error IS NOT NULL
                   ORDER BY COALESCE(j.updated_at, j.created_at) DESC
                   LIMIT 8"""
            )
            failed_rows = await cur.fetchall()

            await cur.execute(
                """SELECT DATE(created_at) AS day, COUNT(*) AS cnt
                   FROM assessment_jobs
                   WHERE created_at >= DATE_SUB(CURRENT_DATE, INTERVAL 6 DAY)
                   GROUP BY DATE(created_at)
                   ORDER BY day ASC"""
            )
            daily_rows = await cur.fetchall()

    return {
        "metrics": {
            "users": total_users,
            "assessments": total_assessments,
            "plans": total_plans,
            "career_plan_blocks": total_career_blocks,
        },
        "assessment_status": {r["status"] or "unknown": int(r["cnt"]) for r in status_rows},
        "recent_failed": [
            {
                "assessment_id": r["assessment_id"],
                "user_id": r["user_id"],
                "username": r["username"],
                "status": r["status"],
                "error": r["error"],
                "created_at": str(r["created_at"]) if r["created_at"] else None,
                "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
                **_snapshot_profile(r["input_snapshot"]),
            }
            for r in failed_rows
        ],
        "daily_assessments": [
            {"date": str(r["day"]), "count": int(r["cnt"])}
            for r in daily_rows
        ],
    }


@app.get("/admin/users", tags=["后台运维"], summary="用户运行数据列表")
async def admin_users(limit: int = 100, admin: dict = Depends(get_current_admin)):
    """查看用户账号、管理员标识、评估次数、计划次数和最近活跃时间。"""
    if memory_db._pool is None:
        raise HTTPException(503, "数据库未初始化")

    safe_limit = max(1, min(int(limit), 500))
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                f"""SELECT u.id, u.username, u.email, u.is_admin, u.created_at,
                          COUNT(DISTINCT j.assessment_id) AS assessment_count,
                          COUNT(DISTINCT p.plan_id) AS plan_count,
                          MAX(j.created_at) AS last_assessment_at
                   FROM users u
                   LEFT JOIN assessment_jobs j ON j.user_id = u.id
                   LEFT JOIN plan_schedules p ON p.user_id = u.id
                   GROUP BY u.id, u.username, u.email, u.is_admin, u.created_at
                   ORDER BY u.created_at DESC
                   LIMIT {safe_limit}"""
            )
            rows = await cur.fetchall()

    return {
        "items": [
            {
                "user_id": r["id"],
                "username": r["username"],
                "email": r["email"],
                "is_admin": bool(r["is_admin"]) or _is_configured_admin(r["username"]),
                "created_at": str(r["created_at"]) if r["created_at"] else None,
                "assessment_count": int(r["assessment_count"] or 0),
                "plan_count": int(r["plan_count"] or 0),
                "last_assessment_at": str(r["last_assessment_at"]) if r["last_assessment_at"] else None,
            }
            for r in rows
        ]
    }


@app.get("/admin/assessments", tags=["后台运维"], summary="评估任务监控列表")
async def admin_assessments(
    status: str | None = None,
    limit: int = 100,
    admin: dict = Depends(get_current_admin),
):
    """查看全局评估任务，可按状态过滤，用于排查运行和失败任务。"""
    if memory_db._pool is None:
        raise HTTPException(503, "数据库未初始化")

    safe_limit = max(1, min(int(limit), 500))
    where = ""
    params: list[object] = []
    if status:
        where = "WHERE j.status=%s"
        params.append(status)

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                f"""SELECT j.assessment_id, j.session_id, j.user_id, u.username,
                          j.status, j.error, j.created_at, j.updated_at, j.input_snapshot,
                          COUNT(DISTINCT d.dimension) AS dimension_count,
                          COUNT(DISTINCT p.plan_id) AS plan_count
                   FROM assessment_jobs j
                   LEFT JOIN users u ON u.id = j.user_id
                   LEFT JOIN assessment_dimensions d ON d.assessment_id = j.assessment_id
                   LEFT JOIN plan_schedules p ON p.assessment_id = j.assessment_id
                   {where}
                   GROUP BY j.assessment_id, j.session_id, j.user_id, u.username,
                            j.status, j.error, j.created_at, j.updated_at, j.input_snapshot
                   ORDER BY j.created_at DESC
                   LIMIT {safe_limit}""",
                params,
            )
            rows = await cur.fetchall()

    return {
        "items": [
            {
                "assessment_id": r["assessment_id"],
                "session_id": r["session_id"],
                "user_id": r["user_id"],
                "username": r["username"],
                "status": r["status"],
                "error": r["error"],
                "created_at": str(r["created_at"]) if r["created_at"] else None,
                "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
                "dimension_count": int(r["dimension_count"] or 0),
                "plan_count": int(r["plan_count"] or 0),
                **_snapshot_profile(r["input_snapshot"]),
            }
            for r in rows
        ]
    }


@app.get("/admin/resources", tags=["后台运维"], summary="数据资源状态")
async def admin_resources(admin: dict = Depends(get_current_admin)):
    """查看系统依赖的数据表规模、O*NET 文件加载状态和缓存服务状态。"""
    if memory_db._pool is None:
        raise HTTPException(503, "数据库未初始化")

    table_names = [
        "assessment_jobs",
        "assessment_dimensions",
        "career_plan_blocks",
        "career_path_progress",
        "plan_schedules",
        "plan_daily_tasks",
        "resume_uploads",
        "messages",
        "traces",
    ]
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            tables = [
                {"name": name, "count": await _admin_table_count(cur, name)}
                for name in table_names
            ]

    redis_ok = False
    if redis_client is not None:
        try:
            redis_ok = bool(await redis_client.ping())
        except Exception:
            redis_ok = False

    return {
        "tables": tables,
        "onet_files": [
            {
                "agent": agent_name,
                "file": filename,
                "loaded": agent_name in _ONET_DATA,
                "characters": len(_ONET_DATA.get(agent_name, "")),
            }
            for agent_name, filename in _ONET_FILES.items()
        ],
        "services": {
            "mysql": True,
            "redis": redis_ok,
            "vector_index": bool(os.getenv("QDRANT_URL") or os.getenv("QDRANT_HOST")),
        },
    }


async def _verify_assessment_owner(assessment_id: str, user_id: int) -> None:
    """验证 assessment_id 属于当前用户，不属于则抛 403。"""
    if memory_db._pool is None:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM assessment_jobs WHERE assessment_id=%s",
                (assessment_id,),
            )
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"assessment_id={assessment_id} 不存在")
    if row[0] is not None and row[0] != user_id:
        logger.warning(f"[Auth] 越权访问: user_id={user_id} 尝试访问 assessment_id={assessment_id} (owner={row[0]})")
        raise HTTPException(403, "无权访问此评估")


async def _verify_plan_owner(plan_id: str, user_id: int) -> None:
    """验证 plan_id 属于当前用户。"""
    if memory_db._pool is None:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM plan_schedules WHERE plan_id=%s",
                (plan_id,),
            )
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"plan_id={plan_id} 不存在")
    if row[0] is not None and row[0] != user_id:
        logger.warning(f"[Auth] 越权访问: user_id={user_id} 尝试访问 plan_id={plan_id} (owner={row[0]})")
        raise HTTPException(403, "无权访问此计划")


# 合并所有已注册的 agent 配置，供 /invoke 查找
_ALL_AGENT_CONFIGS: dict[str, dict] = {
    **SUB_AGENT_CONFIGS,
    **REPORT_AGENT_CONFIGS,
    "assessment_agent": ASSESSMENT_AGENT_CONFIG,
}


@app.post("/invoke", tags=["Agent 调用"], summary="内置 Agent 单次调用")
async def invoke(req: InvokeRequest, user: dict = Depends(get_current_user)):
    """
    通用 Agent 调用接口。
    传入 agent_name（对应 SUB_AGENT_CONFIGS 或 REPORT_AGENT_CONFIGS 中的 key）
    和 task（用户消息/任务文本），返回该 agent 的输出。
    """
    if req.agent_name not in _ALL_AGENT_CONFIGS:
        valid = sorted(_ALL_AGENT_CONFIGS.keys())
        raise HTTPException(
            status_code=400,
            detail=f"未知 agent_name: '{req.agent_name}'。可用的有: {valid}",
        )

    t0 = time.perf_counter()
    config = _ALL_AGENT_CONFIGS[req.agent_name]
    model = config["model"] or MAIN_AGENT_CONFIG["model"]
    llm = LLMProvider(
        model=model,
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    agent = Agent(
        llm=llm,
        system_prompt=config["system_prompt"],
        allowed_tools=config.get("allowed_tools", []),
        mcp=None,
        session_id=None,
    )
    result = await agent.run_once(req.task)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "agent_name": req.agent_name,
        "result": result,
        "elapsed_ms": elapsed_ms,
    }


@app.post("/invoke/custom", tags=["Agent 调用"], summary="自定义 Agent 调用（自定义 prompt + tools）")
async def invoke_custom(req: CustomAgentConfig):
    """
    通用 Agent 调用接口——由外部完整传入配置。

    支持自定义：模型、API Key、Base URL、系统提示词、工具列表、MCP 服务。
    适合外部系统动态调用，不依赖服务器内置的 agent 配置。

    allowed_tools 说明：
      - null / 不传  → 使用所有已注册工具
      - []           → 不允许任何工具（纯对话）
      - ["tool_a"]   → 只允许指定工具
    """
    llm = LLMProvider(
        model=req.model,
        api_key=req.api_key,
        base_url=req.base_url,
    )

    mcp = MCPClient(req.mcp_url) if req.mcp_url else None

    agent = Agent(
        llm=llm,
        system_prompt=req.system_prompt,
        allowed_tools=req.allowed_tools,
        mcp=mcp,
        session_id=req.session_id,
    )

    t0 = time.perf_counter()
    try:
        result = await agent.run_once(req.message)
    finally:
        if mcp:
            await mcp.disconnect()

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "result": result,
        "elapsed_ms": elapsed_ms,
        "model": req.model,
        "session_id": req.session_id,
    }


@app.post("/chat", tags=["对话"], summary="多轮 AI 对话（支持 SSE 流式输出）")
async def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    if req.stream:
        return StreamingResponse(
            _stream_chat(req.message, req.session_id, user["user_id"]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    else:
        return await _json_chat(req.message, req.session_id, user["user_id"])


@app.get("/chat/history", tags=["对话"], summary="获取指定 session 的对话历史")
async def chat_history(session_id: str, user: dict = Depends(get_current_user)):
    """加载聊天历史，只返回 user / assistant 的纯文本消息。"""
    raw = await memory_db.load_messages(session_id)
    messages = []
    for msg in raw:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        # assistant 的 tool_calls 中间消息跳过（含 tool_calls 或 content 为空）
        if role == "assistant" and (not content or msg.get("tool_calls")):
            continue
        messages.append({"role": role, "content": content})
    return {"session_id": session_id, "messages": messages}


# ------------------------------------------------------------------ #
#  Non-streaming                                                        #
# ------------------------------------------------------------------ #

async def _json_chat(message: str, session_id: str | None, user_id: int) -> dict:
    tok = current_user_id.set(user_id)
    try:
        t0 = time.perf_counter()
        agent = _make_agent(session_id)
        reply = await agent.run_once(message)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "reply": reply,
            "session_id": session_id,
            "elapsed_ms": elapsed_ms,
        }
    finally:
        current_user_id.reset(tok)


# ------------------------------------------------------------------ #
#  Streaming (SSE)                                                      #
# ------------------------------------------------------------------ #

async def _cleanup_orphan_messages(session_id: str) -> None:
    """删除 session 末尾连续的未配对 user/tool 消息（没有对应 assistant 回复的）。"""
    if not memory_db._pool:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 从末尾往前找，删除连续的非 assistant/system 消息
            await cur.execute(
                "SELECT id, role FROM messages WHERE session_id = %s ORDER BY id DESC LIMIT 20",
                (session_id,),
            )
            rows = await cur.fetchall()
            ids_to_delete = []
            for row_id, role in rows:
                if role in ("assistant", "system"):
                    break
                ids_to_delete.append(row_id)
            if ids_to_delete:
                placeholders = ",".join(["%s"] * len(ids_to_delete))
                await cur.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids_to_delete)
                logger.debug(f"[Chat Stream] 清理了 {len(ids_to_delete)} 条孤立消息")


async def _stream_chat(message: str, session_id: str | None, user_id: int):
    """
    SSE 生成器，每条消息格式为：
        data: {json}\n\n
    事件类型通过 JSON 内的 "type" 字段区分：
        text   — 文本 token
        tool   — 工具调用通知
        done   — 结束（含 elapsed_ms）
        error  — 出错
    """
    tok = current_user_id.set(user_id)
    try:
        agent = _make_agent(session_id)
        async for chunk in agent.stream_once(message):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    except Exception as exc:
        logger.error(f"[Chat Stream] 错误: {exc}", exc_info=True)
        # 清理本次失败留下的孤立消息，防止损坏的历史导致后续请求持续失败
        if session_id:
            try:
                await _cleanup_orphan_messages(session_id)
            except Exception:
                logger.debug("[Chat Stream] 清理孤立消息失败（已忽略）")
        err_str = str(exc)
        if "额度" in err_str or "balance" in err_str.lower() or "insufficient" in err_str.lower():
            msg = "AI 服务余额不足，请联系管理员充值后重试。"
        else:
            msg = "抱歉，AI 处理时出现错误，请稍后重试。"
        yield f"data: {json.dumps({'type': 'error', 'content': msg}, ensure_ascii=False)}\n\n"
    finally:
        current_user_id.reset(tok)


# ------------------------------------------------------------------ #
#  /resume/extract — 多模态简历解析                                     #
# ------------------------------------------------------------------ #

# 多模态 OCR 模型配置（与主模型同地址/同 key，仅 model 名不同）
_OCR_MODEL_NAME = os.getenv("RESUME_OCR_MODEL", "DeepSeek-OCR")


def _litellm_model_name(raw: str) -> str:
    """litellm 需要 provider 前缀；若 .env 未显式带前缀则默认视为 openai 兼容接口。"""
    if "/" in raw:
        return raw
    return f"openai/{raw}"


@app.post("/resume/extract", tags=["简历"], summary="上传简历文件并提取结构化信息")
async def resume_extract(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """
    接收用户上传的简历图片/PDF，使用多模态模型做 OCR，然后交给 resume_extract_agent
    提取结构化字段并写入数据库，最终返回结构化 JSON 供前端填充表单。
    """
    t0 = time.perf_counter()

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(raw_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，请上传 10MB 以内的图片/PDF")

    mime = file.content_type or "image/png"
    is_docx = (
        mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or (file.filename and file.filename.lower().endswith(".docx"))
    )
    is_pdf = (
        mime == "application/pdf"
        or (file.filename and file.filename.lower().endswith(".pdf"))
    )
    if not (mime.startswith("image/") or is_pdf or is_docx):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{mime}")

    upload_id = uuid.uuid4().hex[:16]

    # ── Step 1: 获取简历文本 ───────────��────────────────────────────
    if is_docx:
        # docx 直接提取文本，无需 OCR
        import io
        from docx import Document as DocxDocument
        try:
            doc = DocxDocument(io.BytesIO(raw_bytes))
            ocr_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            logger.error(f"[ResumeExtract] docx 解析失败: {e}")
            raise HTTPException(status_code=422, detail=f"Word 文件解析失败：{e}")
        if not ocr_text.strip():
            raise HTTPException(status_code=422, detail="Word 文件内容为空")
    elif is_pdf:
        # PDF 直接抽取文本，避开视觉模型不支持 PDF 的限制
        import io
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(raw_bytes))
            pages_text = []
            for page in reader.pages:
                t = page.extract_text() or ""
                if t.strip():
                    pages_text.append(t.strip())
            ocr_text = "\n".join(pages_text)
        except Exception as e:
            logger.error(f"[ResumeExtract] PDF 解析失败: {e}")
            raise HTTPException(status_code=422, detail=f"PDF 文件解析失败：{e}")
        if not ocr_text.strip():
            raise HTTPException(
                status_code=422,
                detail="PDF 内未提取到文本，可能是扫描版/图片版 PDF。请上传图片格式（PNG/JPG）或 Word 文件",
            )
    else:
        # 图片 → 多模态 OCR
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        import litellm
        try:
            ocr_resp = litellm.completion(
                model=_litellm_model_name(_OCR_MODEL_NAME),
                api_key=MAIN_AGENT_CONFIG["api_key"],
                base_url=MAIN_AGENT_CONFIG["base_url"],
                messages=[
                    {"role": "system", "content": "<image>\nFree OCR."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
            )
            ocr_text = (ocr_resp.choices[0].message.content or "").strip()
        except Exception as e:
            logger.error(f"[ResumeExtract] OCR 失败: {e}")
            raise HTTPException(status_code=502, detail=f"简历 OCR 失败：{e}")
        if not ocr_text:
            raise HTTPException(status_code=422, detail="OCR 结果为空，请换一张更清晰的简历")

    # 预先插入一条 resume_uploads 记录（raw_text 先存，extracted 由 tool 更新）
    if memory_db._pool is not None:
        async with memory_db._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO resume_uploads (upload_id, file_name, raw_text, user_id)
                       VALUES (%s, %s, %s, %s)""",
                    (upload_id, file.filename, ocr_text, user["user_id"]),
                )

    # ── Step 2: resume_extract_agent 提取 + 保存 ─────────────────────
    config = RESUME_EXTRACT_AGENT_CONFIG
    model = config["model"] or MAIN_AGENT_CONFIG["model"]
    llm = LLMProvider(
        model=model,
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    agent = Agent(
        llm=llm,
        system_prompt=config["system_prompt"],
        allowed_tools=config["allowed_tools"],
        mcp=None,
        session_id=None,
    )
    task = (
        f"upload_id: {upload_id}\n\n"
        f"以下是简历 OCR 文本，请按 schema 提取字段、调用 save_resume_data 保存，"
        f"然后仅输出最终 JSON：\n\n{ocr_text}"
    )
    try:
        raw = await agent.run_once(task)
    except Exception as e:
        logger.error(f"[ResumeExtract] agent 执行失败: {e}")
        raise HTTPException(status_code=500, detail=f"简历信息提取失败：{e}")

    extracted = _parse_json_output(raw)
    if not isinstance(extracted, dict) or extracted.get("error"):
        raise HTTPException(
            status_code=500,
            detail=f"简历信息解析失败：{extracted.get('error') if isinstance(extracted, dict) else raw[:200]}",
        )

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(f"[ResumeExtract] upload_id={upload_id}  耗时 {elapsed_ms}ms")

    # 更新候选人画像（姓名、学历、当前职位等）
    cand = extracted.get("candidate", {}) if isinstance(extracted, dict) else {}
    await _upsert_candidate(
        user["user_id"],
        name=cand.get("name"),
        age=cand.get("age"),
        city=cand.get("city"),
        education=cand.get("education"),
        current_title=cand.get("current_title"),
        years_of_experience=cand.get("years_of_experience"),
        resume_raw=extracted,
    )

    return {
        "upload_id": upload_id,
        "extracted": extracted,
        "elapsed_ms": elapsed_ms,
    }


# ------------------------------------------------------------------ #
#  /assess 路由                                                        #
# ------------------------------------------------------------------ #

@app.post("/assess", tags=["评估"], summary="发起六维度职业能力评估（立即返回，后台执行）")
@limiter.limit("5/hour")
async def assess(request: Request, req: AssessRequest, user: dict = Depends(get_current_user)):
    """
    立即返回 assessment_id，后台异步执行 6 维度 Agent + SummaryAgent + 报告预生成。
    前端通过 GET /assess/{id}/status 轮询真实进度。
    """
    assessment_id = uuid.uuid4().hex
    await _db_insert_job(
        assessment_id, req.session_id, "running",
        input_snapshot=req.model_dump(exclude={"session_id"}, exclude_none=True),
        user_id=user["user_id"],
    )
    asyncio.create_task(_run_assessment_bg(assessment_id, req, user["user_id"]))
    return {
        "assessment_id": assessment_id,
        "status": "running",
        "elapsed_ms": 0,
    }


async def _run_assessment_bg(assessment_id: str, req: AssessRequest, user_id: int) -> None:
    """后台运行评估全流程：6 维 Agent → SummaryAgent → 报告预生成。失败标记 failed。"""
    try:
        await _run_assessment(assessment_id, req)
        await _db_update_job(assessment_id, "done")
        # 评估完成后更新候选人画像（测评数据）
        cand = req.resume.candidate if req.resume else {}
        await _upsert_candidate(
            user_id,
            name=cand.get("name"),
            target_role=cand.get("target_role"),
            supplement=req.supplement,
            bigfive=req.bigfive.model_dump() if req.bigfive else None,
            riasec=req.riasec.model_dump() if req.riasec else None,
            quiz_abilities=req.quiz_abilities.model_dump() if req.quiz_abilities else None,
            quiz_knowledge=req.quiz_knowledge.model_dump() if req.quiz_knowledge else None,
            third_party=req.third_party.model_dump() if req.third_party else None,
        )
        # 报告预生成（继续后台跑，独立失败也无所谓）
        asyncio.create_task(_pre_generate_report(assessment_id))
    except Exception as exc:
        logger.error(f"[Assess/bg] assessment_id={assessment_id} 失败: {exc}")
        await _db_update_job(assessment_id, "failed", str(exc))


@app.get("/assess/{assessment_id}/status", tags=["评估"], summary="查询评估真实进度")
async def assess_status(assessment_id: str, user: dict = Depends(get_current_user)):
    """返回评估 job 状态 + 已完成的维度/汇总/报告块，前端据此渲染进度。"""
    await _verify_assessment_owner(assessment_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="DB 不可用")
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, error FROM assessment_jobs WHERE assessment_id=%s",
                (assessment_id,),
            )
            job_row = await cur.fetchone()
            await cur.execute(
                "SELECT dimension FROM assessment_dimensions WHERE assessment_id=%s",
                (assessment_id,),
            )
            dims = sorted({r[0] for r in await cur.fetchall()})
            await cur.execute(
                "SELECT 1 FROM assessment_summary WHERE assessment_id=%s LIMIT 1",
                (assessment_id,),
            )
            summary_done = bool(await cur.fetchone())
            await cur.execute(
                "SELECT block_id FROM assessment_report_blocks WHERE assessment_id=%s",
                (assessment_id,),
            )
            blocks = sorted({r[0] for r in await cur.fetchall()})
    if not job_row:
        raise HTTPException(status_code=404, detail=f"assessment_id={assessment_id} 不存在")
    return {
        "assessment_id": assessment_id,
        "status": job_row[0],
        "error": job_row[1],
        "completed_dimensions": dims,
        "summary_done": summary_done,
        "completed_report_blocks": blocks,
    }


# ------------------------------------------------------------------ #
#  Orchestrator                                                        #
# ------------------------------------------------------------------ #

async def _run_assessment(assessment_id: str, req: AssessRequest) -> dict:
    """
    全流程协调：
      1. 6 个维度 Agent 并发运行（每个 agent 拿结构化 JSON 输入 + O*NET 数据）
      2. SummaryAgent — 跨维度整合
    """
    # 将请求序列化为 JSON 字符串，供各 agent 读取
    candidate_json = req.model_dump_json(exclude={"session_id"}, exclude_none=True, indent=2)

    # ── 1. 6 个维度 Agent 并发 ───────────────────────────────────── #
    eval_agents = [
        "skills_agent",
        "knowledge_agent",
        "abilities_agent",
        "work_styles_agent",
        "interests_agent",
        "work_values_agent",
    ]

    tasks = [
        _eval_agent_with_retry(agent_name, assessment_id, candidate_json)
        for agent_name in eval_agents
    ]
    dimension_results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    # 写入 DB（_eval_agent_with_retry 已经返回解析后的 dim_data）
    dimension_results: dict[str, dict] = {}
    for agent_name, result in zip(eval_agents, dimension_results_raw):
        expected_dim = agent_name.replace("_agent", "")
        if isinstance(result, Exception):
            dim_data = {"dimension": expected_dim,
                        "status": "error", "error": str(result)}
        else:
            dim_data = result
            dim_data["dimension"] = expected_dim
        dimension_results[agent_name] = dim_data
        await _db_upsert_dimension(assessment_id, dim_data)

    # ── 3. SummaryAgent ─────────────────────────────────────────────
    summary_input = _build_summary_input(assessment_id, dimension_results)
    summary_raw = await _call_sub_agent(
        agent_name="summary_agent",
        task=summary_input,
    )
    summary = _parse_json_output(summary_raw)
    await _db_upsert_summary(assessment_id, summary)

    return {"dimensions": dimension_results, "summary": summary}


async def _call_sub_agent(agent_name: str, task: str) -> str:
    """创建临时 Sub-Agent 实例，运行单次任务，返回字符串结果。"""
    config = SUB_AGENT_CONFIGS[agent_name]
    model = config["model"] or MAIN_AGENT_CONFIG["model"]
    llm = LLMProvider(
        model=model,
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    sub = Agent(
        llm=llm,
        system_prompt=config["system_prompt"],
        allowed_tools=config["allowed_tools"],
        mcp=None,
        session_id=None,
    )
    return await sub.run_once(task)


async def _call_eval_agent(agent_name: str, assessment_id: str, candidate_json: str) -> str:
    """为评估 Agent 构建 User Message（注入结构化候选人 JSON + O*NET 数据）并调用。"""
    onet_text = _ONET_DATA.get(agent_name, "（O*NET 数据文件未找到）")
    task = (
        f"assessment_id: {assessment_id}\n\n"
        f"=== 候选人数据（JSON） ===\n{candidate_json}\n\n"
        f"=== O*NET 参考数据 ===\n{onet_text}"
    )
    return await _call_sub_agent(agent_name=agent_name, task=task)


async def _eval_agent_with_retry(
    agent_name: str,
    assessment_id: str,
    candidate_json: str,
    max_retries: int = 2,
) -> dict:
    """评估 Agent + JSON 解析失败时自动重试。

    返回解析后的 dim_data（含 _compute_scores 处理后的 overall_score）。
    全部尝试失败时返回 {status: 'parse_error', raw_output: ...}。
    """
    last_dim_data: dict = {}
    last_raw: str = ""
    for attempt in range(max_retries + 1):
        try:
            raw = await _call_eval_agent(agent_name, assessment_id, candidate_json)
            last_raw = raw
            dim_data = _parse_json_output(raw)
            if dim_data.get("status") != "parse_error":
                _compute_scores(dim_data)
                return dim_data
            last_dim_data = dim_data
            logger.warning(f"[Eval/{agent_name}] parse_error，重试 {attempt + 1}/{max_retries}")
        except Exception as e:
            logger.warning(f"[Eval/{agent_name}] 调用异常 {attempt + 1}/{max_retries}：{e}")
            if attempt == max_retries:
                raise
    # 所有重试都失败
    return last_dim_data or {"status": "parse_error", "raw_output": last_raw[:500]}


def _extract_tool_result(messages: list, tool_name: str) -> dict:
    """
    从 agent.messages 中提取指定工具的返回值（假定为 JSON 字符串）。
    策略：遍历 assistant 消息，找到 tool_calls 中名为 tool_name 的调用，
          然后根据 tool_call.id 查找对应的 role=tool 消息，解析其 content。
    找不到或解析失败返回 {}。
    """
    # 先建立 tool_call_id → tool_name 映射
    id_to_name: dict[str, str] = {}
    for msg in messages:
        # openai SDK 返回的可能是对象，也可能是 dict；统一通过 getattr/[]
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role != "assistant":
            continue
        tool_calls = getattr(msg, "tool_calls", None) or (
            msg.get("tool_calls") if isinstance(msg, dict) else None
        )
        if not tool_calls:
            continue
        for tc in tool_calls:
            tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else None)
            fn = getattr(tc, "function", None) or (tc.get("function") if isinstance(tc, dict) else None)
            fn_name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)
            if tc_id and fn_name:
                id_to_name[tc_id] = fn_name

    # 再找 role=tool 消息，命中目标工具则解析返回值
    for msg in messages:
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role != "tool":
            continue
        tc_id = (
            getattr(msg, "tool_call_id", None)
            or (msg.get("tool_call_id") if isinstance(msg, dict) else None)
        )
        if id_to_name.get(tc_id) != tool_name:
            continue
        content = (
            getattr(msg, "content", None)
            or (msg.get("content") if isinstance(msg, dict) else None)
            or ""
        )
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _parse_json_output(raw: str):
    """从 LLM 输出中提取 JSON（支持 markdown code block 包裹 + 截断修复）。"""
    # 先尝试提取 ```json ... ``` 块
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    text = match.group(1).strip() if match else raw.strip()

    for attempt_text in [text, re.sub(r",\s*([\}\]])", r"\1", text)]:
        try:
            return json.loads(attempt_text)
        except json.JSONDecodeError:
            pass

    # 尝试修复被截断的 JSON（补齐缺失的括号）
    repaired = _repair_truncated_json(text)
    if repaired is not None:
        return repaired

    return {"raw_output": raw, "status": "parse_error"}


def _repair_truncated_json(text: str):
    """尝试修复被截断的 JSON：从后往前找到最后一个完整元素，补齐括号。"""
    # 从后往前找最后一个 } 或 ]，截断到那里，然后补齐
    for i in range(len(text) - 1, max(0, len(text) - 2000), -1):
        if text[i] not in ('}', ']'):
            continue
        fragment = text[: i + 1]
        # 移除 fragment 末尾的 trailing comma（如 ...}, 变成 ...}）
        fragment = re.sub(r",\s*$", "", fragment)
        # 计算未关闭的括号
        open_sq = fragment.count('[') - fragment.count(']')
        open_br = fragment.count('{') - fragment.count('}')
        if open_sq < 0 or open_br < 0:
            continue
        # 尝试多种闭合顺序
        closings = set()
        closings.add(']' * open_sq + '}' * open_br)
        closings.add('}' * open_br + ']' * open_sq)
        # 常见模式：截断在 tasks 数组内部 → 需要先 ] 再 } 再 ]
        if open_br > 0 and open_sq > 0:
            closings.add(']' * 1 + '}' * open_br + ']' * (open_sq - 1))
        for closing in closings:
            try:
                result = json.loads(fragment + closing)
                if isinstance(result, list) and len(result) > 0:
                    return result
            except json.JSONDecodeError:
                continue
    return None


def _compute_scores(dim: dict) -> None:
    """后处理：计算 overall_score、highlights、focus_areas（LLM 不做计算）。"""
    sub_dims = dim.get("sub_dimensions", [])
    if not sub_dims:
        return

    scores = [s.get("score") for s in sub_dims if isinstance(s.get("score"), (int, float))]
    if scores:
        dim["overall_score"] = round(sum(scores) / len(scores), 2)

    dim["highlights"] = [
        s.get("id") or s.get("type") for s in sub_dims
        if isinstance(s.get("score"), (int, float)) and s["score"] >= 5.5
    ]
    dim["focus_areas"] = [
        s.get("id") or s.get("type") for s in sub_dims
        if isinstance(s.get("score"), (int, float)) and s["score"] <= 4.5
    ]


def _build_summary_input(assessment_id: str, dimension_results: dict) -> str:
    """为 SummaryAgent 拼装跨维度完整数据输入（含子维度评分和证据）。"""
    full_data = []
    for agent_name, dim in dimension_results.items():
        # 传递完整维度数据：子维度评分、证据、meaning 都包含
        # 仅排除 assessment_id（冗余）和 status（系统字段）
        entry = {k: v for k, v in dim.items() if k not in {"assessment_id", "status"}}
        entry["dimension"] = dim.get("dimension", agent_name.replace("_agent", ""))
        full_data.append(entry)
    return (
        f"assessment_id: {assessment_id}\n\n"
        f"=== 6 个维度完整评估数据 ===\n"
        f"{json.dumps(full_data, ensure_ascii=False, indent=2)}"
    )


# ------------------------------------------------------------------ #
#  DB 写入辅助                                                         #
# ------------------------------------------------------------------ #

async def _upsert_candidate(user_id: int, **fields) -> None:
    """INSERT OR UPDATE candidates 行，只更新非 None 的字段。"""
    if memory_db._pool is None:
        return
    json_fields = {"resume_raw", "bigfive", "riasec", "quiz_abilities", "quiz_knowledge", "third_party"}
    cols, vals = [], []
    for k, v in fields.items():
        if v is None:
            continue
        cols.append(k)
        vals.append(json.dumps(v, ensure_ascii=False) if k in json_fields and not isinstance(v, str) else v)
    if not cols:
        return
    set_clause = ", ".join(f"{c}=VALUES({c})" for c in cols)
    col_str = ", ".join(cols)
    placeholder = ", ".join(["%s"] * len(cols))
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""INSERT INTO candidates (user_id, {col_str})
                    VALUES (%s, {placeholder})
                    ON DUPLICATE KEY UPDATE {set_clause}""",
                (user_id, *vals),
            )


async def _db_insert_job(assessment_id: str, session_id: str | None, status: str,
                         input_snapshot: dict | None = None,
                         user_id: int | None = None) -> None:
    if memory_db._pool is None:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO assessment_jobs
                   (assessment_id, session_id, input_snapshot, status, user_id)
                   VALUES (%s, %s, %s, %s, %s)""",
                (assessment_id, session_id,
                 json.dumps(input_snapshot, ensure_ascii=False) if input_snapshot else None,
                 status, user_id),
            )


async def _db_update_job(assessment_id: str, status: str, error: str | None = None) -> None:
    if memory_db._pool is None:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE assessment_jobs SET status=%s, error=%s,
                   updated_at=CURRENT_TIMESTAMP WHERE assessment_id=%s""",
                (status, error, assessment_id),
            )


async def _db_upsert_dimension(assessment_id: str, dim: dict) -> None:
    if memory_db._pool is None:
        return
    dimension = dim.get("dimension", "unknown")
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO assessment_dimensions
                   (assessment_id, dimension, overall_score, confidence,
                    dimension_summary, sub_dimensions, highlights, focus_areas, extra, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                     overall_score=VALUES(overall_score),
                     confidence=VALUES(confidence),
                     dimension_summary=VALUES(dimension_summary),
                     sub_dimensions=VALUES(sub_dimensions),
                     highlights=VALUES(highlights),
                     focus_areas=VALUES(focus_areas),
                     extra=VALUES(extra),
                     status=VALUES(status)""",
                (
                    assessment_id,
                    dimension,
                    dim.get("overall_score"),
                    dim.get("confidence"),
                    dim.get("dimension_summary"),
                    json.dumps(dim.get("sub_dimensions", []), ensure_ascii=False),
                    json.dumps(dim.get("highlights", []), ensure_ascii=False),
                    json.dumps(dim.get("focus_areas", []), ensure_ascii=False),
                    json.dumps({k: v for k, v in dim.items()
                                if k not in {"assessment_id", "dimension", "overall_score",
                                             "confidence", "dimension_summary", "sub_dimensions",
                                             "highlights", "focus_areas", "status"}},
                               ensure_ascii=False),
                    dim.get("status", "done"),
                ),
            )


async def _db_upsert_summary(assessment_id: str, summary: dict) -> None:
    if memory_db._pool is None:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO assessment_summary
                   (assessment_id, persona_label, narrative_intro,
                    top_cards, next_direction, keywords,
                    top3_strengths, top3_improvements, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                     persona_label=VALUES(persona_label),
                     narrative_intro=VALUES(narrative_intro),
                     top_cards=VALUES(top_cards),
                     next_direction=VALUES(next_direction),
                     keywords=VALUES(keywords),
                     top3_strengths=VALUES(top3_strengths),
                     top3_improvements=VALUES(top3_improvements),
                     status=VALUES(status)""",
                (
                    assessment_id,
                    summary.get("persona_label"),
                    summary.get("narrative_intro"),
                    json.dumps(summary.get("top_cards", []), ensure_ascii=False),
                    summary.get("next_direction"),
                    json.dumps(summary.get("keywords", []), ensure_ascii=False),
                    json.dumps(summary.get("top3_strengths", []), ensure_ascii=False),
                    json.dumps(summary.get("top3_improvements", []), ensure_ascii=False),
                    summary.get("status", "done"),
                ),
            )


async def _db_upsert_report_block(assessment_id: str, block_id: str, block: dict) -> None:
    if memory_db._pool is None:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO assessment_report_blocks
                   (assessment_id, block_id, block_json)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE block_json=VALUES(block_json), generated_at=NOW()""",
                (assessment_id, block_id, json.dumps(block, ensure_ascii=False)),
            )


async def _db_get_report_blocks(assessment_id: str) -> dict[str, dict]:
    """返回已缓存的报告块 {block_id: block_json_dict}。"""
    if memory_db._pool is None:
        return {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT block_id, block_json FROM assessment_report_blocks WHERE assessment_id=%s",
                (assessment_id,),
            )
            rows = await cur.fetchall()
    return {row[0]: json.loads(row[1]) for row in rows}


async def _db_get_dimensions(assessment_id: str) -> dict[str, dict]:
    """从 DB 读取所有维度评估结果，返回 {dimension: full_dim_dict}。"""
    if memory_db._pool is None:
        return {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT dimension, overall_score, confidence, dimension_summary,
                          sub_dimensions, highlights, focus_areas, extra, status
                   FROM assessment_dimensions WHERE assessment_id=%s""",
                (assessment_id,),
            )
            rows = await cur.fetchall()
    result = {}
    for row in rows:
        dim_name = row[0]
        sub_dims = json.loads(row[4]) if row[4] else []
        highlights = json.loads(row[5]) if row[5] else []
        focus_areas = json.loads(row[6]) if row[6] else []
        extra = json.loads(row[7]) if row[7] else {}
        dim = {
            "dimension": dim_name,
            "overall_score": row[1],
            "confidence": row[2],
            "dimension_summary": row[3],
            "sub_dimensions": sub_dims,
            "highlights": highlights,
            "focus_areas": focus_areas,
            "status": row[8],
            **extra,
        }
        result[dim_name] = dim
    return result


async def _db_get_summary(assessment_id: str) -> dict:
    """从 DB 读取 SummaryAgent 结果。"""
    if memory_db._pool is None:
        return {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT persona_label, narrative_intro, top_cards, next_direction,
                          keywords, top3_strengths, top3_improvements, status
                   FROM assessment_summary WHERE assessment_id=%s""",
                (assessment_id,),
            )
            row = await cur.fetchone()
    if not row:
        return {}
    return {
        "persona_label": row[0],
        "narrative_intro": row[1],
        "top_cards": json.loads(row[2]) if row[2] else [],
        "next_direction": row[3],
        "keywords": json.loads(row[4]) if row[4] else [],
        "top3_strengths": json.loads(row[5]) if row[5] else [],
        "top3_improvements": json.loads(row[6]) if row[6] else [],
        "status": row[7],
    }


async def _pre_generate_report(assessment_id: str) -> None:
    """评估完成后后台预生成报告块，用户打开报告页时直接读缓存。"""
    try:
        dims = await _db_get_dimensions(assessment_id)
        if not dims:
            return
        cached = await _db_get_report_blocks(assessment_id)
        dim_block_map = {
            "skills": "skills_report_agent",
            "knowledge": "knowledge_report_agent",
            "abilities": "abilities_report_agent",
            "work_styles": "work_styles_report_agent",
            "interests": "interests_report_agent",
            "work_values": "work_values_report_agent",
        }
        missing = {d: a for d, a in dim_block_map.items() if d not in cached and d in dims}
        if not missing:
            return
        tasks = [_call_dim_report_agent(d, dims[d], a) for d, a in missing.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (dim, _), raw in zip(missing.items(), results):
            block = raw if not isinstance(raw, Exception) else {"block_id": dim, "error": str(raw), "status": "error"}
            await _db_upsert_report_block(assessment_id, dim, block)
        logger.info(f"[Report] 预生成完成 assessment_id={assessment_id}, 生成 {len(missing)} 个块")
    except Exception as e:
        logger.warning(f"[Report] 预生成失败 assessment_id={assessment_id}: {e}")


# ------------------------------------------------------------------ #
#  /report 路由                                                        #
# ------------------------------------------------------------------ #

@app.post("/assess/{assessment_id}/dimension/{dim_name}/retry",
          tags=["评估"], summary="单独重试某个评估维度")
async def assess_retry_dimension(
    assessment_id: str, dim_name: str,
    user: dict = Depends(get_current_user),
):
    """对历史 assessment 中失败的维度单独重跑（不需要整个重做）。

    场景：knowledge 维度因为 LLM 输出 JSON 解析失败显示 error。
    """
    await _verify_assessment_owner(assessment_id, user["user_id"])
    valid = {"skills", "knowledge", "abilities",
             "work_styles", "interests", "work_values"}
    if dim_name not in valid:
        raise HTTPException(status_code=400, detail=f"非法维度：{dim_name}")
    agent_name = f"{dim_name}_agent"

    # 拉原始评估输入（从 assessment_jobs.input_snapshot）
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="DB 不可用")
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT input_snapshot FROM assessment_jobs WHERE assessment_id=%s",
                (assessment_id,),
            )
            row = await cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=404,
                            detail="找不到该评估的原始输入快照，无法重跑")
    snapshot = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    candidate_json = json.dumps(snapshot, ensure_ascii=False, indent=2)

    # 重跑该维度（含自动重试）
    try:
        dim_data = await _eval_agent_with_retry(
            agent_name=agent_name,
            assessment_id=assessment_id,
            candidate_json=candidate_json,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重跑失败：{e}")

    dim_data["dimension"] = dim_name
    await _db_upsert_dimension(assessment_id, dim_data)

    # 该维度的 report_block 缓存清掉，下次打开报告页会重新生成
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM assessment_report_blocks WHERE assessment_id=%s AND block_id=%s",
                (assessment_id, dim_name),
            )

    logger.info(f"[Assess/RetryDim] assessment_id={assessment_id} dim={dim_name} "
                f"status={dim_data.get('status', 'done')}")
    return {
        "ok": True,
        "dimension": dim_name,
        "status": dim_data.get("status", "done"),
        "overall_score": dim_data.get("overall_score"),
    }


@app.get("/report/{assessment_id}", tags=["评估"], summary="获取评估报告详情")
async def get_report(assessment_id: str, user: dict = Depends(get_current_user)):
    """
    生成或返回缓存的完整报告（12个块）。
    流程：
      1. 检查 assessment_report_blocks 缓存
      2. 从 assessment_dimensions + assessment_summary 读取数据
      3. 并发调用 6 个 DimReportAgent 生成维度块（如未缓存）
      4. 拼装静态块（header/radar/unlock/methodology）
      5. 返回完整 report JSON
    """
    await _verify_assessment_owner(assessment_id, user["user_id"])
    # 检查评估任务是否存在
    dims = await _db_get_dimensions(assessment_id)
    if not dims:
        raise HTTPException(status_code=404, detail=f"assessment_id={assessment_id} not found")

    summary = await _db_get_summary(assessment_id)

    # 检查是否已有缓存的报告块
    cached_blocks = await _db_get_report_blocks(assessment_id)

    # 需要生成的维度块列表
    dim_block_map = {
        "skills":      "skills_report_agent",
        "knowledge":   "knowledge_report_agent",
        "abilities":   "abilities_report_agent",
        "work_styles": "work_styles_report_agent",
        "interests":   "interests_report_agent",
        "work_values": "work_values_report_agent",
    }

    # 对未缓存的维度块，并发生成
    missing = {dim: agent for dim, agent in dim_block_map.items()
               if dim not in cached_blocks and dim in dims}

    if missing:
        tasks = [
            _call_dim_report_agent(dim, dims[dim], agent_name)
            for dim, agent_name in missing.items()
        ]
        new_blocks_raw = await asyncio.gather(*tasks, return_exceptions=True)

        for (dim, _agent_name), raw in zip(missing.items(), new_blocks_raw):
            if isinstance(raw, Exception):
                block = {"block_id": dim, "error": str(raw), "status": "error"}
            else:
                block = raw
            cached_blocks[dim] = block
            await _db_upsert_report_block(assessment_id, dim, block)

    # 拼装静态块
    static_blocks = _build_static_blocks(assessment_id, dims, summary)

    # 组装最终报告
    report = _assemble_report(assessment_id, static_blocks, cached_blocks, summary)
    return report


async def _call_dim_report_agent(dim_name: str, dim_data: dict, agent_name: str) -> dict:
    """调用 DimReportAgent，将评估 JSON 转化为报告块 JSON。"""
    task = (
        f"以下是维度 '{dim_name}' 的评估数据，请按要求生成报告块 JSON：\n\n"
        f"{json.dumps(dim_data, ensure_ascii=False, indent=2)}"
    )
    config = REPORT_AGENT_CONFIGS[agent_name]
    model = config["model"] or MAIN_AGENT_CONFIG["model"]
    llm = LLMProvider(
        model=model,
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    agent = Agent(
        llm=llm,
        system_prompt=config["system_prompt"],
        allowed_tools=config["allowed_tools"],
        mcp=None,
        session_id=None,
    )
    raw = await agent.run_once(task)
    return _parse_json_output(raw)


def _build_static_blocks(assessment_id: str, dims: dict, summary: dict) -> dict:
    """生成不需要 LLM 的静态块：header, radar, unlock, methodology。"""

    # header 块：从 summary 和 dims 推断数据来源
    data_sources = {
        "resume": True,
        "supplement": True,
        "bigfive": any("bigfive_raw" in d for d in dims.values()),
        "riasec": dims.get("interests", {}).get("status") == "done",
        "quiz": dims.get("abilities", {}).get("status") == "done",
    }
    header = {
        "block_id": "header",
        "assessment_id": assessment_id,
        "data_sources": data_sources,
    }

    # radar 块：汇总6维得分
    dim_meta = {
        "skills":      {"name": "技能画像",   "source_done": "简历+补充",    "source_locked": "需完成技能评估"},
        "knowledge":   {"name": "知识储备",   "source_done": "简历",         "source_locked": "需完成知识评估"},
        "abilities":   {"name": "认知能力",   "source_done": "认知测试",     "source_locked": "需完成认知测试"},
        "work_styles": {"name": "工作特质",   "source_done": "大五人格+简历", "source_locked": "需提供大五人格数据"},
        "interests":   {"name": "职业兴趣",   "source_done": "RIASEC量表",   "source_locked": "需完成RIASEC量表"},
        "work_values": {"name": "工作价值观", "source_done": "简历偏好推断", "source_locked": "需完成价值观评估"},
    }
    radar_dims = []
    for dim_id, meta in dim_meta.items():
        d = dims.get(dim_id, {})
        status = d.get("status", "error")
        locked = status == "locked"
        radar_dims.append({
            "id": dim_id,
            "name": meta["name"],
            "score": None if locked else d.get("overall_score"),
            "confidence": None if locked else d.get("confidence"),
            "status": status,
            "source": meta["source_locked"] if locked else meta["source_done"],
        })
    radar = {
        "block_id": "radar",
        "dimensions": radar_dims,
        "confidence_legend": {
            "高": "有标准化量表/做题数据支撑，结果可直接用于职业决策参考",
            "中": "仅依赖简历+LLM行为推断，结果可参考但建议补测验证",
        },
    }

    # unlock 块：仅包含 locked 状态的维度
    unlock_items = []
    abilities_status = dims.get("abilities", {}).get("status")
    interests_status = dims.get("interests", {}).get("status")
    if abilities_status == "locked":
        unlock_items.append({
            "test_type": "cognitive",
            "locked": True,
            "title": "认知能力测试",
            "duration_min": 15,
            "teaser": "你的简历中存在认知能力信号，完成测试后可精确定位你的认知优势方向",
        })
    if interests_status == "locked":
        unlock_items.append({
            "test_type": "riasec",
            "locked": True,
            "title": "Holland 职业兴趣量表",
            "duration_min": 10,
            "teaser": "完成后可获得精确3位Holland代码和专属岗位推荐",
        })
    unlock = {"block_id": "unlock", "items": unlock_items}

    # methodology 块（静态文本）
    methodology = {
        "block_id": "methodology",
        "framework": "基于美国劳工部 O*NET Content Model（www.onetonline.org）",
        "personality_model": "Big Five / IPIP-NEO-120（Public Domain, ipip.ori.org）",
        "interest_model": "Holland RIASEC（O*NET 官方采用）",
        "scale": "1-7分，对齐 O*NET 原生 Level Scale",
        "fusion_strategy": "量表/测验数据权重×0.6 + LLM简历行为推断权重×0.4",
        "score_guide": {
            "1-2": "初学者水平，仅具备基础认知",
            "3-4": "胜任者水平，能独立完成常规任务",
            "5-6": "精通者水平，能处理复杂非常规问题",
            "7":   "专家水平，能指导他人并推动领域创新",
        },
        "disclaimer": "本报告为辅助参考工具，评估结果基于用户提供的数据质量，不构成任何法律意义上的能力认证或就业建议。",
    }

    return {
        "header": header,
        "radar": radar,
        "unlock": unlock,
        "methodology": methodology,
    }


def _assemble_report(
    assessment_id: str,
    static_blocks: dict,
    dim_blocks: dict,
    summary: dict,
) -> dict:
    """将所有块组装为完整报告 JSON。"""

    # overview 块：从 summary 数据构建
    overview = {
        "block_id": "overview",
        "persona_label": summary.get("persona_label"),
        "narrative_intro": summary.get("narrative_intro"),
        "top_cards": summary.get("top_cards", []),
        "next_direction": summary.get("next_direction"),
        "keywords": summary.get("keywords", []),
    }

    # action 块：从 summary 数据构建
    action = {
        "block_id": "action",
        "top3_strengths": summary.get("top3_strengths", []),
        "top3_improvements": summary.get("top3_improvements", []),
    }

    return {
        "assessment_id": assessment_id,
        "blocks": {
            "header":       static_blocks["header"],
            "radar":        static_blocks["radar"],
            "overview":     overview,
            "skills":       dim_blocks.get("skills", {"block_id": "skills", "status": "pending"}),
            "knowledge":    dim_blocks.get("knowledge", {"block_id": "knowledge", "status": "pending"}),
            "abilities":    dim_blocks.get("abilities", {"block_id": "abilities", "status": "pending"}),
            "work_styles":  dim_blocks.get("work_styles", {"block_id": "work_styles", "status": "pending"}),
            "interests":    dim_blocks.get("interests", {"block_id": "interests", "status": "pending"}),
            "work_values":  dim_blocks.get("work_values", {"block_id": "work_values", "status": "pending"}),
            "action":       action,
            "unlock":       static_blocks["unlock"],
            "methodology":  static_blocks["methodology"],
        },
    }


# ================================================================== #
#  /career/match — 职业推荐选择                                        #
# ================================================================== #

class CareerMatchRequest(BaseModel):
    assessment_id: str
    force: bool = False          # 前端传 true 时强制重新匹配
    custom_start: str | None = None  # 用户自定义起始岗位方向


def _match_cache_key(assessment_id: str) -> str:
    return f"career:match:{assessment_id}"


@app.post("/career/match", tags=["职业"], summary="基于评估结果进行职业匹配推荐")
@limiter.limit("20/hour")
async def career_match(request: Request, req: CareerMatchRequest, user: dict = Depends(get_current_user)):
    """
    职业推荐接口（Redis 缓存）。
    默认返回缓存结果（秒级响应），force=true 时重新生成。
    """
    await _verify_assessment_owner(req.assessment_id, user["user_id"])

    # 优先读 Redis 缓存（非 force 模式）
    cache_key = _match_cache_key(req.assessment_id)
    if not req.force and redis_client:
        try:
            cached_str = await redis_client.get(cache_key)
            if cached_str:
                cached = json.loads(cached_str)
                logger.info(f"[career/match] Redis 缓存命中 assessment_id={req.assessment_id}")
                return {
                    "assessment_id": req.assessment_id,
                    "result": cached.get("result", cached),
                    "agent_reply": cached.get("agent_reply", ""),
                    "elapsed_ms": 0,
                    "cached": True,
                }
        except Exception as e:
            logger.warning(f"[career/match] Redis 读取失败: {e}")

    t0 = time.perf_counter()
    config = CAREER_AGENT_CONFIG
    model = config["model"] or MAIN_AGENT_CONFIG["model"]
    llm = LLMProvider(
        model=model,
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    agent = Agent(
        llm=llm,
        system_prompt=config["system_prompt"],
        allowed_tools=config["allowed_tools"],
        mcp=None,
        session_id=None,
    )
    custom_part = f" 用户期望的起始方向：{req.custom_start}" if req.custom_start else ""
    task = f"请为评估 ID 为 {req.assessment_id} 的候选人推荐职业发展路线。{custom_part}"
    result_str = await agent.run_once(task)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    parsed = _extract_tool_result(agent.messages, "match_careers")
    if not parsed:
        parsed = _parse_json_output(result_str)

    response = {
        "assessment_id": req.assessment_id,
        "result": parsed,
        "agent_reply": result_str,
        "elapsed_ms": elapsed_ms,
    }

    # 写入 Redis 缓存
    if redis_client:
        try:
            await redis_client.set(cache_key, json.dumps(response, ensure_ascii=False), ex=MATCH_CACHE_TTL)
        except Exception as e:
            logger.warning(f"[career/match] Redis 写入失败: {e}")

    return response


# ================================================================== #
#  /career/plan — 详细职业规划                                          #
# ================================================================== #

class CareerPlanRequest(BaseModel):
    assessment_id: str
    onetsoc_code: str
    title: str | None = None        # 岗位标题
    path_data: str | None = None    # 完整路线 JSON（前端传入）
    current_stage: int = 1          # 当前规划的阶段编号


@app.post("/career/plan", tags=["职业"], summary="生成指定职业的详细规划")
async def career_plan(req: CareerPlanRequest, user: dict = Depends(get_current_user)):
    """
    详细职业规划接口。
    输入：assessment_id + onetsoc_code + 可选 path_data/current_stage
    流程：
      Career Plan Agent
        → generate_career_plan (Block 1/2/3/5，并发模板填充)
        → generate_action_plan (Block 4，Action Plan Sub-Agent 动态规划)
    输出：4-5 个报告块 JSON（存入 career_plan_blocks，同时返回给调用方）
    """
    await _verify_assessment_owner(req.assessment_id, user["user_id"])
    t0 = time.perf_counter()
    config = CAREER_PLAN_AGENT_CONFIG
    model = config["model"] or MAIN_AGENT_CONFIG["model"]
    llm = LLMProvider(
        model=model,
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    agent = Agent(
        llm=llm,
        system_prompt=config["system_prompt"],
        allowed_tools=config["allowed_tools"],
        mcp=None,
        session_id=None,
    )
    title_part = f"（职业标题：{req.title}）" if req.title else ""
    path_part = f"（路线数据：{req.path_data}）" if req.path_data else ""
    stage_part = f"（当前阶段：{req.current_stage}）" if req.current_stage > 1 else ""
    task = (
        f"请为评估 ID 为 {req.assessment_id} 的候选人，"
        f"针对目标职业 {req.onetsoc_code}{title_part}{stage_part}{path_part} 生成完整详细规划报告。"
    )
    result_str = await agent.run_once(task)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # 从 DB 读取已写入的 Block 返回给调用方
    blocks = await _db_get_career_plan_blocks(req.assessment_id, req.onetsoc_code)

    return {
        "assessment_id": req.assessment_id,
        "onetsoc_code": req.onetsoc_code,
        "status": "done" if len(blocks) >= 4 else "partial",
        "blocks": blocks,
        "agent_reply": result_str,
        "elapsed_ms": elapsed_ms,
    }


@app.get("/career/plan/{assessment_id}/{onetsoc_code}", tags=["职业"], summary="读取已缓存的职业规划（不重新生成）")
async def get_career_plan_cached(assessment_id: str, onetsoc_code: str, user: dict = Depends(get_current_user)):
    """读取已缓存的职业规划 blocks（不触发重新生成）。"""
    await _verify_assessment_owner(assessment_id, user["user_id"])
    blocks = await _db_get_career_plan_blocks(assessment_id, onetsoc_code)
    if not blocks:
        raise HTTPException(404, "该职业尚未生成规划")
    return {
        "assessment_id": assessment_id,
        "onetsoc_code": onetsoc_code,
        "status": "done" if len(blocks) >= 4 else "partial",
        "blocks": blocks,
        "agent_reply": "[cached]",
        "elapsed_ms": 0,
    }


async def _db_get_career_plan_blocks(assessment_id: str, onetsoc_code: str) -> dict:
    """从 career_plan_blocks 读取已生成的报告块。"""
    if memory_db._pool is None:
        return {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT block_id, block_json FROM career_plan_blocks
                   WHERE assessment_id=%s AND onetsoc_code=%s""",
                (assessment_id, onetsoc_code),
            )
            rows = await cur.fetchall()
    return {row[0]: json.loads(row[1]) for row in rows}


@app.get("/career/planned-codes/{assessment_id}", tags=["职业"], summary="查询已生成规划的职业代码列表")
async def get_planned_codes(assessment_id: str, user: dict = Depends(get_current_user)):
    """返回该 assessment 下已生成职业规划的 onetsoc_code 列表及摘要信息。"""
    await _verify_assessment_owner(assessment_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(500, "DB not ready")
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT onetsoc_code,
                          MAX(CASE WHEN block_id='match_overview' THEN block_json END) AS overview_json
                   FROM career_plan_blocks
                   WHERE assessment_id=%s
                   GROUP BY onetsoc_code""",
                (assessment_id,),
            )
            rows = await cur.fetchall()
    planned: dict[str, dict] = {}
    for r in rows:
        code = r["onetsoc_code"]
        info: dict = {"onetsoc_code": code}
        if r.get("overview_json"):
            ov = json.loads(r["overview_json"])
            info["title"] = ov.get("occupation_title", "")
            info["final_score"] = ov.get("final_score")
            info["verdict"] = ov.get("verdict", "")
        planned[code] = info
    return {"assessment_id": assessment_id, "planned": planned}


# ================================================================== #
#  /career/path-progress — 路线进度管理                                  #
# ================================================================== #

class SavePathRequest(BaseModel):
    assessment_id: str
    path_code: str
    path_data: str  # JSON 字符串：完整路线数据


@app.post("/career/save-path", tags=["职业"], summary="保存用户选择的职业路线")
async def save_career_path(req: SavePathRequest, user: dict = Depends(get_current_user)):
    """用户选择路线后，保存到 career_path_progress 表。"""
    await _verify_assessment_owner(req.assessment_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(500, "DB not ready")
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO career_path_progress
                   (user_id, assessment_id, path_code, path_data, current_stage, stage_history)
                   VALUES (%s, %s, %s, %s, 1, %s)
                   ON DUPLICATE KEY UPDATE
                   path_data=VALUES(path_data), updated_at=NOW()""",
                (user["user_id"], req.assessment_id, req.path_code, req.path_data,
                 json.dumps([], ensure_ascii=False)),
            )
    return {"ok": True, "path_code": req.path_code, "current_stage": 1}


class StageCompleteRequest(BaseModel):
    assessment_id: str
    path_code: str
    completed_stage: int
    user_note: str = ""


@app.post("/career/stage-complete", tags=["职业"], summary="确认完成当前阶段")
async def confirm_stage_complete(req: StageCompleteRequest, user: dict = Depends(get_current_user)):
    """
    用户确认完成当前阶段，更新进度，返回下一阶段信息。
    """
    await _verify_assessment_owner(req.assessment_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(500, "DB not ready")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT path_data, current_stage, stage_history
                   FROM career_path_progress
                   WHERE assessment_id=%s AND path_code=%s""",
                (req.assessment_id, req.path_code),
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(404, "未找到该路线记录，请先保存路线")

    path_data = json.loads(row["path_data"]) if isinstance(row["path_data"], str) else row["path_data"]
    stage_history = json.loads(row["stage_history"]) if isinstance(row["stage_history"], str) and row["stage_history"] else []
    stages = path_data.get("stages", [])
    total_stages = len(stages)

    if req.completed_stage >= total_stages:
        return {"ok": True, "message": "已完成全部阶段", "next_stage": None}

    # 记录完成
    stage_history.append({
        "stage": req.completed_stage,
        "completed_at": datetime.datetime.now().isoformat(),
        "user_note": req.user_note,
    })
    next_stage = req.completed_stage + 1

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE career_path_progress
                   SET current_stage=%s, stage_history=%s, updated_at=NOW()
                   WHERE assessment_id=%s AND path_code=%s""",
                (next_stage, json.dumps(stage_history, ensure_ascii=False),
                 req.assessment_id, req.path_code),
            )

    # 返回下一阶段信息
    next_stage_data = stages[next_stage - 1] if next_stage <= total_stages else None

    return {
        "ok": True,
        "completed_stage": req.completed_stage,
        "next_stage": next_stage,
        "next_stage_data": next_stage_data,
        "total_stages": total_stages,
    }


@app.get("/career/path-progress/{assessment_id}/{path_code}", tags=["职业"], summary="获取路线进度")
async def get_path_progress(assessment_id: str, path_code: str, user: dict = Depends(get_current_user)):
    """获取用户在某条路线上的进度。"""
    await _verify_assessment_owner(assessment_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(500, "DB not ready")
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT path_code, path_data, current_stage, stage_history
                   FROM career_path_progress
                   WHERE assessment_id=%s AND path_code=%s""",
                (assessment_id, path_code),
            )
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "未找到路线进度")
    return {
        "path_code": row["path_code"],
        "path_data": json.loads(row["path_data"]) if isinstance(row["path_data"], str) else row["path_data"],
        "current_stage": row["current_stage"],
        "stage_history": json.loads(row["stage_history"]) if isinstance(row["stage_history"], str) and row["stage_history"] else [],
    }


# ================================================================== #
#  /plan-schedule — 计划进度                                            #
# ================================================================== #

_WEEKLY_PLAN_SYSTEM = """你是职业规划顾问。根据行动计划phases和能力缺口分析，将目标时长智能分解为N周。

优先级规则：
- severity=high 的缺口对应的 actions → 优先安排在前几周，分配更多时间
- severity=medium → 中间阶段
- severity=low → 后期或时间充裕时安排
- 如果时间不够覆盖所有内容，优先保留高优先级内容，低优先级标注为"延后"

【增量叠加规则（重要）】
如果用户提供了 prior_plans_context（历史计划上下文），说明用户此前已经做过若干计划，并积累了一定进度。此时：
1. **不要重复已经涵盖过的 theme/goal**，可以直接跳过或只做轻量回顾（不超过 1 周）
2. 基于用户已完成的任务，**进阶性地安排更深入、更进一步的内容**（例如：已学基础→进阶；已做练习→实战项目；已完成简历优化→投递/面试）
3. 仍然优先覆盖 gap_analysis 中 severity=high 且在历史计划中尚未被充分处理的缺口
4. 如果历史计划的某个 phase 已经全部完成，当前计划应进入下一个 phase
5. 在 focus 字段里可以适当体现"延续上次进度"、"承接前一个计划的XX主题"

每周输出以下字段：week_number（整数，从1开始），theme（本周主题，20字内），focus（本周聚焦描述，50字内），weekly_goals（3-5条具体可执行目标字符串数组），phase_ref（对应action_plan的phase_id，如 phase_1）。
**严格输出JSON数组，不含任何解释或markdown代码块。**"""

_DAILY_TASKS_SYSTEM = """你是职业规划顾问。根据本周目标拆解为7天每日任务。

规则：
- 每天2-3个任务，每个30-60分钟
- 标题具体（如"完成Python第3章练习"而非"学习Python"）
- description 简短一句话，含具体资源或方法
- 高优缺口任务排周一至周四

严格输出JSON数组，禁止markdown包裹，禁止任何解释文字：
[{"day":1,"tasks":[{"id":"t1","title":"标题","duration_min":45,"type":"study","description":"一句话描述"}]},{"day":2,"tasks":[...]},...,{"day":7,"tasks":[...]}]
type: study/practice/network/apply"""


async def _llm_generate(
    system_prompt: str,
    user_message: str,
    *,
    agent_name: str = "plan_schedule",
) -> str:
    """使用通用 prompt agent（无工具）进行单轮对话，返回文本。

    内部走 agent.runner.run_prompt，统一日志/trace 写入。
    """
    from agent.runner import run_prompt
    text, _, _ = await run_prompt(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name=agent_name,
    )
    return text


class PlanWeeklyRequest(BaseModel):
    assessment_id: str
    onetsoc_code: str
    duration_weeks: int = 4
    start_date: str  # YYYY-MM-DD


class UpdateDayRequest(BaseModel):
    completed_ids: list[str]


async def _fetch_prior_plans_context(assessment_id: str, onetsoc_code: str) -> str:
    """汇总该职业下既有计划的进度摘要，作为增量规划的上下文。

    返回格式为文本：若无历史计划返回空字符串，否则包含每个计划的 theme 列表 +
    已完成任务条数 + 完成过的任务标题（去重，最多 30 条），供 LLM 做增量决策。
    """
    if memory_db._pool is None:
        return ""

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT plan_id, duration_weeks, start_date, status, created_at
                   FROM plan_schedules
                   WHERE assessment_id=%s AND onetsoc_code=%s
                   ORDER BY created_at ASC""",
                (assessment_id, onetsoc_code),
            )
            plans = await cur.fetchall()
            if not plans:
                return ""

            all_plan_ids = [p[0] for p in plans]
            placeholders = ",".join(["%s"] * len(all_plan_ids))
            await cur.execute(
                f"""SELECT plan_id, week_number, theme, focus, weekly_goals, phase_ref
                    FROM plan_weeks
                    WHERE plan_id IN ({placeholders})
                    ORDER BY plan_id, week_number""",
                tuple(all_plan_ids),
            )
            weeks = await cur.fetchall()
            await cur.execute(
                f"""SELECT plan_id, tasks, completed_ids
                    FROM plan_daily_tasks
                    WHERE plan_id IN ({placeholders})""",
                tuple(all_plan_ids),
            )
            days = await cur.fetchall()

    # 组装每个 plan 的摘要
    by_plan: dict[str, dict] = {}
    for p in plans:
        by_plan[p[0]] = {
            "plan_id": p[0],
            "duration_weeks": p[1],
            "start_date": str(p[2]),
            "status": p[3],
            "created_at": str(p[4]),
            "themes": [],
            "phase_refs": set(),
            "done_task_titles": [],
            "done_task_count": 0,
            "total_task_count": 0,
        }
    for w in weeks:
        plan_id, _wn, theme, _focus, goals_json, phase_ref = w
        if plan_id in by_plan:
            if theme:
                by_plan[plan_id]["themes"].append(theme)
            if phase_ref:
                by_plan[plan_id]["phase_refs"].add(phase_ref)

    for d in days:
        plan_id, tasks_json, completed_ids_json = d
        if plan_id not in by_plan:
            continue
        try:
            tasks = json.loads(tasks_json) if tasks_json else []
            completed_ids = set(json.loads(completed_ids_json) if completed_ids_json else [])
        except (json.JSONDecodeError, TypeError):
            continue
        by_plan[plan_id]["total_task_count"] += len(tasks)
        for t in tasks:
            if t.get("id") in completed_ids:
                by_plan[plan_id]["done_task_count"] += 1
                title = t.get("title")
                if title:
                    by_plan[plan_id]["done_task_titles"].append(title)

    # 序列化为紧凑文本，限长避免 prompt 过大
    lines = [f"共有 {len(plans)} 个历史计划（按创建时间升序）："]
    for info in by_plan.values():
        done_titles_dedup: list[str] = []
        seen: set = set()
        for t in info["done_task_titles"]:
            if t not in seen:
                seen.add(t)
                done_titles_dedup.append(t)
            if len(done_titles_dedup) >= 30:
                break
        lines.append(
            f"- plan_id={info['plan_id']} | {info['duration_weeks']}周 | "
            f"起始 {info['start_date']} | status={info['status']} | "
            f"完成任务 {info['done_task_count']}/{info['total_task_count']}"
        )
        if info["themes"]:
            lines.append(f"  涉及主题: {json.dumps(info['themes'], ensure_ascii=False)}")
        if info["phase_refs"]:
            lines.append(f"  涉及 phase: {sorted(info['phase_refs'])}")
        if done_titles_dedup:
            lines.append(
                f"  已完成任务示例: {json.dumps(done_titles_dedup, ensure_ascii=False)}"
            )

    return "\n".join(lines)


@app.post("/plan-schedule/weekly", tags=["计划"], summary="生成周计划概览")
async def create_weekly_plan(req: PlanWeeklyRequest, user: dict = Depends(get_current_user)):
    """生成周计划概览，写入 DB，返回 plan_id + 周列表。"""
    await _verify_assessment_owner(req.assessment_id, user["user_id"])
    blocks = await _db_get_career_plan_blocks(req.assessment_id, req.onetsoc_code)
    action_plan = blocks.get("action_plan", {})
    phases = action_plan.get("phases", [])
    if not phases:
        raise HTTPException(status_code=400, detail="action_plan 不存在，请先完成职业规划")

    gap_analysis = blocks.get("gap_analysis", {})
    gaps = gap_analysis.get("gaps", [])

    # 拉取历史计划摘要，供 LLM 做增量/叠加式规划
    prior_context = await _fetch_prior_plans_context(req.assessment_id, req.onetsoc_code)

    phases_json = json.dumps(phases, ensure_ascii=False, indent=2)
    gaps_json = json.dumps(gaps, ensure_ascii=False, indent=2) if gaps else "暂无"
    user_msg = (
        f"action_plan phases:\n{phases_json}\n\n"
        f"gap_analysis（能力缺口，含优先级）:\n{gaps_json}\n\n"
        f"duration_weeks: {req.duration_weeks}\n"
        f"start_date: {req.start_date}"
    )
    if prior_context:
        user_msg += (
            "\n\nprior_plans_context（历史计划摘要，请务必避免重复，进行进阶性叠加）:\n"
            + prior_context
        )
    raw = await _llm_generate(_WEEKLY_PLAN_SYSTEM, user_msg)
    weeks_data = _parse_json_output(raw)
    if not isinstance(weeks_data, list):
        raise HTTPException(status_code=500, detail="LLM 生成周计划失败，请重试")

    plan_id = uuid.uuid4().hex[:16]
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 查询同一职业下已有计划的最大 week_number，新计划从其后递增
            await cur.execute(
                """SELECT COALESCE(MAX(pw.week_number), 0)
                   FROM plan_weeks pw
                   JOIN plan_schedules ps ON ps.plan_id = pw.plan_id
                   WHERE ps.assessment_id = %s AND ps.onetsoc_code = %s""",
                (req.assessment_id, req.onetsoc_code),
            )
            max_week_row = await cur.fetchone()
            week_offset = max_week_row[0] if max_week_row else 0

            await cur.execute(
                """INSERT INTO plan_schedules
                   (plan_id, assessment_id, onetsoc_code, duration_weeks, start_date, status, user_id)
                   VALUES (%s, %s, %s, %s, %s, 'weekly_draft', %s)""",
                (plan_id, req.assessment_id, req.onetsoc_code, req.duration_weeks, req.start_date, user["user_id"]),
            )
            for w in weeks_data:
                original_num = w.get("week_number", 1)
                actual_num = week_offset + original_num
                w["week_number"] = actual_num  # 同步更新返回给前端的数据
                await cur.execute(
                    """INSERT INTO plan_weeks
                       (plan_id, week_number, theme, focus, weekly_goals, phase_ref)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                         theme=VALUES(theme), focus=VALUES(focus),
                         weekly_goals=VALUES(weekly_goals), phase_ref=VALUES(phase_ref)""",
                    (
                        plan_id, actual_num,
                        w.get("theme"), w.get("focus"),
                        json.dumps(w.get("weekly_goals", []), ensure_ascii=False),
                        w.get("phase_ref"),
                    ),
                )

    return {"plan_id": plan_id, "status": "weekly_draft", "weeks": weeks_data}


@app.post("/plan-schedule/{plan_id}/confirm", tags=["计划"], summary="确认计划并触发每日任务生成")
async def confirm_plan(plan_id: str, user: dict = Depends(get_current_user)):
    """确认周计划，触发后台每日任务生成。"""
    await _verify_plan_owner(plan_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT duration_weeks FROM plan_schedules WHERE plan_id=%s",
                (plan_id,),
            )
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"plan_id={plan_id} 不存在")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE plan_schedules SET status='generating_daily' WHERE plan_id=%s",
                (plan_id,),
            )

    asyncio.create_task(_generate_all_daily_tasks(plan_id))
    return {"plan_id": plan_id, "status": "generating_daily"}


@app.post("/plan-schedule/{plan_id}/retry-daily", tags=["计划"], summary="重试每日任务生成")
async def retry_daily_tasks(plan_id: str, user: dict = Depends(get_current_user)):
    """重新生成每日任务（用于 daily_failed 状态的计划）。"""
    await _verify_plan_owner(plan_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT status FROM plan_schedules WHERE plan_id=%s", (plan_id,))
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"plan_id={plan_id} 不存在")

    # 清除旧的每日任务数据，重置状态
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM plan_daily_tasks WHERE plan_id=%s", (plan_id,))
            await cur.execute(
                "UPDATE plan_schedules SET status='generating_daily' WHERE plan_id=%s",
                (plan_id,),
            )

    asyncio.create_task(_generate_all_daily_tasks(plan_id))
    return {"plan_id": plan_id, "status": "generating_daily"}


async def _generate_all_daily_tasks(plan_id: str) -> None:
    """后台任务：逐周生成每日任务写入 DB。"""
    if memory_db._pool is None:
        return

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT week_number, theme, focus, weekly_goals, phase_ref
                   FROM plan_weeks WHERE plan_id=%s ORDER BY week_number""",
                (plan_id,),
            )
            weeks = await cur.fetchall()
            await cur.execute(
                "SELECT start_date, assessment_id, onetsoc_code FROM plan_schedules WHERE plan_id=%s",
                (plan_id,),
            )
            ps_row = await cur.fetchone()

    if not ps_row:
        return

    start_date, assessment_id, onetsoc_code = ps_row[0], ps_row[1], ps_row[2]
    if isinstance(start_date, str):
        start_date = datetime.date.fromisoformat(start_date)

    # 拉取 career_plan_blocks，构建 phase/gap 索引
    blocks = await _db_get_career_plan_blocks(assessment_id, onetsoc_code)
    action_plan_phases = {
        p["phase_id"]: p
        for p in blocks.get("action_plan", {}).get("phases", [])
    }
    all_gaps = blocks.get("gap_analysis", {}).get("gaps", [])
    high_gaps = [g for g in all_gaps if g.get("severity") == "high"]

    # 计算本计划内的最小 week_number，用于日期偏移（week_number 可能从 5 开始）
    min_week_num = min(w[0] for w in weeks) if weeks else 1

    for week_row in weeks:
        week_num, theme, focus, weekly_goals_json, phase_ref = week_row
        weekly_goals = json.loads(weekly_goals_json) if weekly_goals_json else []

        # 本周对应的 phase actions
        phase = action_plan_phases.get(phase_ref, {}) if phase_ref else {}
        phase_actions = phase.get("actions", [])

        # 相关缺口：优先与 phase action item 名称匹配；否则取所有 high gaps
        action_items = {a.get("item", "") for a in phase_actions}
        relevant_gaps = [g for g in all_gaps if g.get("area", "") in action_items]
        if not relevant_gaps:
            relevant_gaps = high_gaps

        user_msg = (
            f"本周主题: {theme}\n"
            f"本周聚焦: {focus}\n"
            f"本周目标: {json.dumps(weekly_goals, ensure_ascii=False)}\n"
            f"week_number: {week_num}\n"
        )
        if phase_actions:
            user_msg += (
                f"\n本阶段行动项（含可交付物和资源）:\n"
                f"{json.dumps(phase_actions, ensure_ascii=False, indent=2)}\n"
            )
        if relevant_gaps:
            user_msg += (
                f"\n相关能力缺口（含学习建议）:\n"
                f"{json.dumps(relevant_gaps, ensure_ascii=False, indent=2)}\n"
            )
        try:
            days_data = None
            for _attempt in range(2):
                raw = await _llm_generate(_DAILY_TASKS_SYSTEM, user_msg)
                days_data = _parse_json_output(raw)
                if isinstance(days_data, list):
                    break
                logger.warning(f"[PlanSchedule] 第{week_num}周第{_attempt+1}次解析失败: {raw[:200]}")
            if not isinstance(days_data, list):
                continue

            async with memory_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    for day_item in days_data:
                        day_num = day_item.get("day", 1)
                        tasks = day_item.get("tasks", [])
                        # week_num 是绝对编号，用相对位置计算日期
                        week_index = week_num - min_week_num
                        date = start_date + datetime.timedelta(weeks=week_index, days=day_num - 1)
                        await cur.execute(
                            """INSERT INTO plan_daily_tasks
                               (plan_id, week_number, day_number, date, tasks, completed_ids)
                               VALUES (%s, %s, %s, %s, %s, JSON_ARRAY())
                               ON DUPLICATE KEY UPDATE tasks=VALUES(tasks)""",
                            (
                                plan_id, week_num, day_num,
                                date.isoformat(),
                                json.dumps(tasks, ensure_ascii=False),
                            ),
                        )
        except Exception as exc:
            logger.error(f"[PlanSchedule] 生成第{week_num}周每日任务失败: {exc}")

    # 检查实际生成的行数，0 行则标记失败
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM plan_daily_tasks WHERE plan_id=%s", (plan_id,),
            )
            cnt = (await cur.fetchone())[0]
            if cnt > 0:
                await cur.execute(
                    "UPDATE plan_schedules SET status='daily_ready' WHERE plan_id=%s",
                    (plan_id,),
                )
                logger.info(f"[PlanSchedule] plan_id={plan_id} 每日任务生成完毕，共 {cnt} 行")
            else:
                await cur.execute(
                    "UPDATE plan_schedules SET status='daily_failed' WHERE plan_id=%s",
                    (plan_id,),
                )
                logger.error(f"[PlanSchedule] plan_id={plan_id} 每日任务生成失败，0 行写入")


@app.get("/plan-schedule/list/{assessment_id}/{onetsoc_code}", tags=["计划"], summary="获取历史计划列表")
async def list_plans(assessment_id: str, onetsoc_code: str, user: dict = Depends(get_current_user)):
    """列出该职业的所有历史计划（按创建时间倒序）。"""
    await _verify_assessment_owner(assessment_id, user["user_id"])
    if memory_db._pool is None:
        return {"plans": []}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT plan_id, duration_weeks, start_date, status, created_at
                   FROM plan_schedules
                   WHERE assessment_id=%s AND onetsoc_code=%s
                   ORDER BY created_at DESC""",
                (assessment_id, onetsoc_code),
            )
            rows = await cur.fetchall()
    return {
        "plans": [
            {
                "plan_id": r[0], "duration_weeks": r[1],
                "start_date": str(r[2]), "status": r[3],
                "created_at": str(r[4]),
            }
            for r in rows
        ]
    }


@app.get("/plan-schedule/{plan_id}", tags=["计划"], summary="获取完整计划详情与进度")
async def get_plan(plan_id: str, user: dict = Depends(get_current_user)):
    """获取完整计划数据（含每周+每日任务+打卡进度）。"""
    await _verify_plan_owner(plan_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT assessment_id, onetsoc_code, duration_weeks, start_date, status
                   FROM plan_schedules WHERE plan_id=%s""",
                (plan_id,),
            )
            ps = await cur.fetchone()
            if not ps:
                raise HTTPException(status_code=404, detail=f"plan_id={plan_id} 不存在")
            assessment_id, onetsoc_code, duration_weeks, start_date, status = ps

            await cur.execute(
                """SELECT week_number, theme, focus, weekly_goals, phase_ref
                   FROM plan_weeks WHERE plan_id=%s ORDER BY week_number""",
                (plan_id,),
            )
            weeks = await cur.fetchall()
            await cur.execute(
                """SELECT week_number, day_number, date, tasks, completed_ids, task_notes
                   FROM plan_daily_tasks WHERE plan_id=%s ORDER BY week_number, day_number""",
                (plan_id,),
            )
            days = await cur.fetchall()

    days_by_week: dict[int, list] = {}
    for d in days:
        wn = d[0]
        if wn not in days_by_week:
            days_by_week[wn] = []
        days_by_week[wn].append({
            "day_number": d[1],
            "date": str(d[2]),
            "tasks": json.loads(d[3]) if d[3] else [],
            "completed_ids": json.loads(d[4]) if d[4] else [],
            "task_notes": json.loads(d[5]) if d[5] else {},
        })

    weeks_out = [
        {
            "week_number": w[0],
            "theme": w[1],
            "focus": w[2],
            "weekly_goals": json.loads(w[3]) if w[3] else [],
            "phase_ref": w[4],
            "days": days_by_week.get(w[0], []),
        }
        for w in weeks
    ]

    return {
        "plan_id": plan_id,
        "assessment_id": assessment_id,
        "onetsoc_code": onetsoc_code,
        "duration_weeks": duration_weeks,
        "start_date": str(start_date),
        "status": status,
        "weeks": weeks_out,
    }


@app.patch("/plan-schedule/{plan_id}/day/{week_number}/{day_number}", tags=["计划"], summary="更新每日打卡进度")
async def update_day_progress(
    plan_id: str, week_number: int, day_number: int, req: UpdateDayRequest,
    user: dict = Depends(get_current_user),
):
    """更新某天的打卡进度。"""
    await _verify_plan_owner(plan_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE plan_daily_tasks SET completed_ids=%s
                   WHERE plan_id=%s AND week_number=%s AND day_number=%s""",
                (json.dumps(req.completed_ids, ensure_ascii=False), plan_id, week_number, day_number),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="记录不存在")

    return {"ok": True}


class UpdateTaskNoteRequest(BaseModel):
    note: str


@app.patch("/plan-schedule/{plan_id}/day/{week_number}/{day_number}/note/{task_id}", tags=["计划"], summary="保存任务感悟笔记")
async def update_task_note(
    plan_id: str, week_number: int, day_number: int, task_id: str,
    req: UpdateTaskNoteRequest,
    user: dict = Depends(get_current_user),
):
    """保存或删除某任务的完成感悟（note 为空字符串则删除）。"""
    await _verify_plan_owner(plan_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT task_notes FROM plan_daily_tasks WHERE plan_id=%s AND week_number=%s AND day_number=%s",
                (plan_id, week_number, day_number),
            )
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")

    notes: dict = json.loads(row[0]) if row[0] else {}
    if req.note.strip():
        notes[task_id] = req.note.strip()
    else:
        notes.pop(task_id, None)

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE plan_daily_tasks SET task_notes=%s WHERE plan_id=%s AND week_number=%s AND day_number=%s",
                (json.dumps(notes, ensure_ascii=False), plan_id, week_number, day_number),
            )

    return {"ok": True}


@app.delete("/plan-schedule/{plan_id}", tags=["计划"], summary="删除计划（级联清理所有周/日任务）")
async def delete_plan(plan_id: str, user: dict = Depends(get_current_user)):
    """删除某个计划及其全部周/每日任务。级联清理 plan_schedules + plan_weeks + plan_daily_tasks。"""
    await _verify_plan_owner(plan_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 先确认计划存在，避免误返回 ok
            await cur.execute(
                "SELECT 1 FROM plan_schedules WHERE plan_id=%s",
                (plan_id,),
            )
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail=f"plan_id={plan_id} 不存在")

            await cur.execute("DELETE FROM plan_daily_tasks WHERE plan_id=%s", (plan_id,))
            await cur.execute("DELETE FROM plan_weeks       WHERE plan_id=%s", (plan_id,))
            await cur.execute("DELETE FROM plan_schedules   WHERE plan_id=%s", (plan_id,))

    logger.info(f"[PlanSchedule] 已删除 plan_id={plan_id}")
    return {"ok": True, "plan_id": plan_id}


@app.delete("/plan-schedule/{plan_id}/week/{week_number}", tags=["计划"], summary="删除计划中的某一周（最后一周则级联删除整计划）")
async def delete_plan_week(plan_id: str, week_number: int, user: dict = Depends(get_current_user)):
    """删除计划中的某一周（含其每日任务）。

    如果删除后该计划已无任何周，则级联删除整个 plan_schedules。
    返回字段 plan_deleted 标识此情形，前端可据此清空 currentPlanId。
    """
    await _verify_plan_owner(plan_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM plan_weeks WHERE plan_id=%s AND week_number=%s",
                (plan_id, week_number),
            )
            if not await cur.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail=f"plan_id={plan_id} 的第 {week_number} 周不存在",
                )

            await cur.execute(
                "DELETE FROM plan_daily_tasks WHERE plan_id=%s AND week_number=%s",
                (plan_id, week_number),
            )
            await cur.execute(
                "DELETE FROM plan_weeks WHERE plan_id=%s AND week_number=%s",
                (plan_id, week_number),
            )

            # 检查是否还有其他周，若没有则整计划一并清理
            await cur.execute(
                "SELECT COUNT(*) FROM plan_weeks WHERE plan_id=%s",
                (plan_id,),
            )
            (remaining,) = await cur.fetchone()

            plan_deleted = False
            if remaining == 0:
                await cur.execute(
                    "DELETE FROM plan_schedules WHERE plan_id=%s",
                    (plan_id,),
                )
                plan_deleted = True

    logger.info(
        f"[PlanSchedule] 删除 plan_id={plan_id} week={week_number}  "
        f"remaining_weeks={remaining}  plan_deleted={plan_deleted}"
    )
    return {
        "ok": True,
        "plan_id": plan_id,
        "week_number": week_number,
        "plan_deleted": plan_deleted,
    }


# ================================================================== #
#  成长档案（Archive）接口                                             #
# ================================================================== #

@app.get("/archive/list", tags=["成长档案"], summary="获取所有历史评估记录")
async def archive_list(user: dict = Depends(get_current_user)):
    """列出当前用户的所有历史评估，附带每个评估下的职业规划数和计划数。"""
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 先查主表，再批量查计数（避免慢关联子查询）
            await cur.execute(
                "SELECT assessment_id, input_snapshot, status, created_at "
                "FROM assessment_jobs WHERE user_id=%s ORDER BY created_at DESC",
                (user["user_id"],),
            )
            rows = await cur.fetchall()

            if not rows:
                return {"assessments": []}

            aids = [r["assessment_id"] for r in rows]
            placeholders = ",".join(["%s"] * len(aids))

            # 批量查职业规划计数
            await cur.execute(
                f"SELECT assessment_id, COUNT(DISTINCT onetsoc_code) AS cnt "
                f"FROM career_plan_blocks WHERE assessment_id IN ({placeholders}) "
                f"GROUP BY assessment_id",
                aids,
            )
            career_counts = {r["assessment_id"]: r["cnt"] for r in await cur.fetchall()}

            # 批量查计划计数
            await cur.execute(
                f"SELECT assessment_id, COUNT(*) AS cnt "
                f"FROM plan_schedules WHERE assessment_id IN ({placeholders}) "
                f"GROUP BY assessment_id",
                aids,
            )
            plan_counts = {r["assessment_id"]: r["cnt"] for r in await cur.fetchall()}

    assessments = []
    for row in rows:
        snapshot = row["input_snapshot"]
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except (json.JSONDecodeError, TypeError):
                snapshot = {}
        elif snapshot is None:
            snapshot = {}

        resume = snapshot.get("resume", {})
        candidate = resume.get("candidate", {})
        aid = row["assessment_id"]
        assessments.append({
            "assessment_id": aid,
            "name": candidate.get("name", ""),
            "current_title": candidate.get("current_title", ""),
            "education": candidate.get("education", ""),
            "status": row["status"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "career_count": career_counts.get(aid, 0),
            "plan_count": plan_counts.get(aid, 0),
        })

    return {"assessments": assessments}


@app.get("/archive/{assessment_id}/detail", tags=["成长档案"], summary="获取单次评估的完整档案")
async def archive_detail(assessment_id: str, user: dict = Depends(get_current_user)):
    """获取单次评估的完整档案：个人信息快照 + 维度 + 职业规划 + 计划进度。"""
    await _verify_assessment_owner(assessment_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 1. 基本信息
            await cur.execute(
                "SELECT assessment_id, input_snapshot, status, created_at FROM assessment_jobs WHERE assessment_id=%s",
                (assessment_id,),
            )
            job = await cur.fetchone()
            if not job:
                raise HTTPException(status_code=404, detail="评估不存在")

            snapshot = job["input_snapshot"]
            if isinstance(snapshot, str):
                try:
                    snapshot = json.loads(snapshot)
                except (json.JSONDecodeError, TypeError):
                    snapshot = {}
            elif snapshot is None:
                snapshot = {}

            resume = snapshot.get("resume", {})
            candidate = resume.get("candidate", {})
            profile = {
                "name": candidate.get("name", ""),
                "age": candidate.get("age"),
                "education": candidate.get("education", ""),
                "current_title": candidate.get("current_title", ""),
                "years_of_experience": candidate.get("years_of_experience"),
                "skills": resume.get("skills", []),
                "certifications": resume.get("certifications", []),
                "experiences": resume.get("experiences", []),
                "supplement": snapshot.get("supplement", ""),
            }

            # 2. 维度得分
            await cur.execute(
                """SELECT dimension, overall_score, confidence, dimension_summary,
                          highlights, focus_areas, status
                   FROM assessment_dimensions WHERE assessment_id=%s""",
                (assessment_id,),
            )
            dim_rows = await cur.fetchall()
            dimensions = {}
            for d in dim_rows:
                dim_name = d["dimension"]
                highlights = d["highlights"]
                if isinstance(highlights, str):
                    try:
                        highlights = json.loads(highlights)
                    except (json.JSONDecodeError, TypeError):
                        highlights = []
                focus_areas = d["focus_areas"]
                if isinstance(focus_areas, str):
                    try:
                        focus_areas = json.loads(focus_areas)
                    except (json.JSONDecodeError, TypeError):
                        focus_areas = []
                dimensions[dim_name] = {
                    "score": float(d["overall_score"]) if d["overall_score"] is not None else None,
                    "confidence": d["confidence"],
                    "summary": d["dimension_summary"],
                    "highlights": highlights or [],
                    "focus_areas": focus_areas or [],
                    "status": d["status"],
                }

            # 3. 职业规划（按 onetsoc_code 分组，取 match_overview block）
            await cur.execute(
                """SELECT onetsoc_code, block_id, block_json
                   FROM career_plan_blocks WHERE assessment_id=%s""",
                (assessment_id,),
            )
            career_rows = await cur.fetchall()
            careers_map: dict[str, dict] = {}
            for c in career_rows:
                code = c["onetsoc_code"]
                if code not in careers_map:
                    careers_map[code] = {"onetsoc_code": code}
                block_json = c["block_json"]
                if isinstance(block_json, str):
                    try:
                        block_json = json.loads(block_json)
                    except (json.JSONDecodeError, TypeError):
                        block_json = {}
                bid = c["block_id"]
                if bid == "match_overview":
                    careers_map[code]["title"] = block_json.get("occupation_title", "")
                    careers_map[code]["match_score"] = block_json.get("final_score")
                    careers_map[code]["verdict"] = block_json.get("verdict", "")

            # 4. 计划进度
            await cur.execute(
                """SELECT plan_id, onetsoc_code, duration_weeks, start_date, status, created_at
                   FROM plan_schedules WHERE assessment_id=%s ORDER BY created_at""",
                (assessment_id,),
            )
            plan_rows = await cur.fetchall()
            plans = []
            if plan_rows:
                plan_ids = [p["plan_id"] for p in plan_rows]
                ph = ",".join(["%s"] * len(plan_ids))
                # 一次查询获取所有计划的任务数据（避免 N+1）
                await cur.execute(
                    f"SELECT plan_id, tasks, completed_ids FROM plan_daily_tasks WHERE plan_id IN ({ph})",
                    plan_ids,
                )
                all_task_rows = await cur.fetchall()
                # 按 plan_id 分组统计
                task_stats: dict[str, tuple[int, int]] = {}
                for t in all_task_rows:
                    pid = t["plan_id"]
                    tasks_data = t["tasks"]
                    if isinstance(tasks_data, str):
                        try:
                            tasks_data = json.loads(tasks_data)
                        except (json.JSONDecodeError, TypeError):
                            tasks_data = []
                    cids = t["completed_ids"]
                    if isinstance(cids, str):
                        try:
                            cids = json.loads(cids)
                        except (json.JSONDecodeError, TypeError):
                            cids = []
                    total, done = task_stats.get(pid, (0, 0))
                    task_stats[pid] = (
                        total + (len(tasks_data) if isinstance(tasks_data, list) else 0),
                        done + (len(cids) if isinstance(cids, list) else 0),
                    )

                for p in plan_rows:
                    pid = p["plan_id"]
                    total_tasks, completed_tasks = task_stats.get(pid, (0, 0))
                    plans.append({
                        "plan_id": pid,
                        "onetsoc_code": p["onetsoc_code"],
                        "duration_weeks": p["duration_weeks"],
                        "start_date": str(p["start_date"]) if p["start_date"] else None,
                        "status": p["status"],
                        "created_at": str(p["created_at"]) if p["created_at"] else None,
                        "total_tasks": total_tasks,
                        "completed_tasks": completed_tasks,
                    })

    return {
        "assessment_id": assessment_id,
        "status": job["status"],
        "created_at": str(job["created_at"]) if job["created_at"] else None,
        "profile": profile,
        "dimensions": dimensions,
        "careers": list(careers_map.values()),
        "plans": plans,
    }


@app.get("/archive/milestones", tags=["成长档案"], summary="获取成长里程碑统计")
async def archive_milestones(user: dict = Depends(get_current_user)):
    """获取当前用户的成长里程碑事件，按时间倒序。"""
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    milestones = []
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 单次 UNION ALL 查询获取当前用户的里程碑
            await cur.execute("""
                SELECT 'assessment' AS mtype, j.assessment_id, j.created_at AS event_date,
                       j.input_snapshot AS extra
                FROM assessment_jobs j WHERE j.status IN ('done','partial') AND j.user_id=%s

                UNION ALL

                SELECT 'career_plan' AS mtype, c.assessment_id,
                       MAX(c.generated_at) AS event_date,
                       MAX(CASE WHEN c.block_id='match_overview' THEN c.block_json END) AS extra
                FROM career_plan_blocks c
                INNER JOIN assessment_jobs j2 ON c.assessment_id = j2.assessment_id AND j2.user_id=%s
                GROUP BY c.assessment_id, c.onetsoc_code

                ORDER BY event_date DESC
            """, (user["user_id"], user["user_id"]))
            rows = await cur.fetchall()

    for row in rows:
        mtype = row["mtype"]
        extra = row.get("extra")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (json.JSONDecodeError, TypeError):
                extra = {}
        elif extra is None:
            extra = {}

        if mtype == "assessment":
            name = extra.get("resume", {}).get("candidate", {}).get("name", "")
            milestones.append({
                "date": str(row["event_date"]) if row["event_date"] else None,
                "type": "assessment",
                "title": "完成能力评估",
                "description": f"{name} 的能力评估已完成" if name else "能力评估已完成",
                "assessment_id": row["assessment_id"],
            })
        elif mtype == "career_plan":
            title = extra.get("occupation_title", "")
            milestones.append({
                "date": str(row["event_date"]) if row["event_date"] else None,
                "type": "career_plan",
                "title": "生成职业规划",
                "description": f"目标职业：{title}" if title else "职业规划已生成",
                "assessment_id": row["assessment_id"],
            })

    milestones.sort(key=lambda m: m["date"] or "", reverse=True)
    return {"milestones": milestones}


@app.delete("/archive/{assessment_id}", tags=["成长档案"], summary="删除评估及所有关联数据")
async def archive_delete(assessment_id: str, user: dict = Depends(get_current_user)):
    """删除一次评估及其所有关联数据（维度、摘要、职业规划、计划等）。"""
    await _verify_assessment_owner(assessment_id, user["user_id"])
    if memory_db._pool is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 检查是否存在
            await cur.execute(
                "SELECT 1 FROM assessment_jobs WHERE assessment_id=%s",
                (assessment_id,),
            )
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="评估不存在")

            # 获取该评估下的所有 plan_id
            await cur.execute(
                "SELECT plan_id FROM plan_schedules WHERE assessment_id=%s",
                (assessment_id,),
            )
            plan_ids = [r[0] for r in await cur.fetchall()]

            # 级联删除
            for pid in plan_ids:
                await cur.execute("DELETE FROM plan_daily_tasks WHERE plan_id=%s", (pid,))
                await cur.execute("DELETE FROM plan_weeks WHERE plan_id=%s", (pid,))
            if plan_ids:
                await cur.execute(
                    "DELETE FROM plan_schedules WHERE assessment_id=%s",
                    (assessment_id,),
                )
            await cur.execute(
                "DELETE FROM career_plan_blocks WHERE assessment_id=%s",
                (assessment_id,),
            )
            await cur.execute(
                "DELETE FROM assessment_dimensions WHERE assessment_id=%s",
                (assessment_id,),
            )
            await cur.execute(
                "DELETE FROM assessment_summary WHERE assessment_id=%s",
                (assessment_id,),
            )
            await cur.execute(
                "DELETE FROM assessment_jobs WHERE assessment_id=%s",
                (assessment_id,),
            )

    logger.info(f"[Archive] 删除评估 assessment_id={assessment_id}，含 {len(plan_ids)} 个计划")
    return {"ok": True, "assessment_id": assessment_id}


# ============================================================ #
#  Learn Plan（任务计划）路由                                       #
#  四 agent 链路：outline → roadmap → daily → grader               #
#  四张表：learn_outlines / learn_months / learn_weeks / learn_tasks #
# ============================================================ #

from agent.learn_plan_helpers import (
    SUGGESTED_DAILY_TASKS, MIN_WEEKS, MAX_WEEKS,
    BASE_GRADE_SCORE, REFLECTION_MIN_LEN,
    apply_default_grade, clamp_grade, compute_progress as _helper_compute_progress,
    extract_json as _learn_extract_json,
    normalize_task_contributions, should_invoke_grader, should_materialize_next_week,
    validate_daily_tasks, validate_outline, validate_roadmap,
)
import agent.learn_plan_db as lpdb


async def _call_learn_agent(agent_name: str, task: str) -> str:
    """调用 LEARN_PLAN_AGENT_CONFIGS 中注册的 agent，返回字符串。"""
    config = LEARN_PLAN_AGENT_CONFIGS[agent_name]
    model = config["model"] or MAIN_AGENT_CONFIG["model"]
    llm = LLMProvider(
        model=model,
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    agent = Agent(
        llm=llm,
        system_prompt=config["system_prompt"],
        allowed_tools=config["allowed_tools"],
        mcp=None,
        session_id=None,
    )
    return await agent.run_once(task)


async def _verify_plan_owner(plan_id: str, user_id: int) -> dict:
    outline = await lpdb.get_outline(plan_id)
    if not outline:
        raise HTTPException(status_code=404, detail=f"plan_id={plan_id} not found")
    if outline["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="无权访问该计划")
    return outline


# ------------------------------------------------------------------ #
#  Step 1: 生成大纲                                                    #
# ------------------------------------------------------------------ #

class GenerateOutlineRequest(BaseModel):
    assessment_id: str
    stage_code: str
    user_preference: str | None = None


@app.post("/plan/generate", tags=["任务计划"], summary="生成学习大纲（新）")
@limiter.limit("10/hour")
async def plan_generate(
    request: Request, req: GenerateOutlineRequest,
    user: dict = Depends(get_current_user),
):
    """基于能力画像 + 目标 Stage 生成学习大纲。

    返回 plan_id 和 outline，用户确认后才进入 roadmap 阶段。
    """
    await _verify_assessment_owner(req.assessment_id, user["user_id"])

    # 读画像 + path progress 里的 Stage 信息
    dims = await _db_get_dimensions(req.assessment_id)
    summary = await _db_get_summary(req.assessment_id)
    if not dims:
        raise HTTPException(status_code=404, detail="评估数据未找到")

    stage_info = await _fetch_stage_info(req.assessment_id, req.stage_code)

    # 取 career_plan_blocks（如果有）作为 career_plan_context
    plan_context = await _fetch_career_plan_context(req.assessment_id, req.stage_code)

    # 组装 agent 输入
    agent_input = json.dumps({
        "candidate_profile": _build_profile_brief(dims, summary),
        "target_stage": stage_info,
        "career_plan_context": plan_context,
        "user_preference": req.user_preference or "",
    }, ensure_ascii=False, indent=2)

    try:
        raw = await _call_learn_agent("plan_outline_agent", agent_input)
        data = _learn_extract_json(raw)
        outline = validate_outline(data)
    except Exception as e:
        logger.warning(f"[Plan/generate] outline 生成失败: {e}")
        raise HTTPException(status_code=500, detail=f"大纲生成失败：{e}")

    plan_id = uuid.uuid4().hex
    await lpdb.insert_outline(
        plan_id=plan_id,
        user_id=user["user_id"],
        assessment_id=req.assessment_id,
        stage_code=req.stage_code,
        stage_title=stage_info.get("title") if isinstance(stage_info, dict) else None,
        modules=outline["modules"],
        total_weight=outline["total_weight"],
        estimated_weeks=outline["estimated_weeks"],
        user_preference=req.user_preference,
        status="pending",
    )

    logger.info(f"[Plan/generate] plan_id={plan_id} modules={len(outline['modules'])} "
                f"est_weeks={outline['estimated_weeks']}")
    return {
        "plan_id": plan_id,
        "outline": outline,
    }


@app.post("/plan/{plan_id}/regenerate-outline", tags=["任务计划"], summary="重新生成大纲")
async def plan_regenerate_outline(
    plan_id: str, payload: dict | None = None,
    user: dict = Depends(get_current_user),
):
    outline_row = await _verify_plan_owner(plan_id, user["user_id"])
    if outline_row["status"] not in ("pending", "ready", "error"):
        raise HTTPException(status_code=400, detail=f"当前状态 {outline_row['status']} 不可重新生成")

    preference = (payload or {}).get("user_preference") or outline_row.get("user_preference")
    stage_info = await _fetch_stage_info(outline_row["assessment_id"], outline_row["stage_code"])
    plan_context = await _fetch_career_plan_context(
        outline_row["assessment_id"], outline_row["stage_code"]
    )
    dims = await _db_get_dimensions(outline_row["assessment_id"])
    summary = await _db_get_summary(outline_row["assessment_id"])

    agent_input = json.dumps({
        "candidate_profile": _build_profile_brief(dims, summary),
        "target_stage": stage_info,
        "career_plan_context": plan_context,
        "user_preference": preference or "",
    }, ensure_ascii=False, indent=2)

    try:
        raw = await _call_learn_agent("plan_outline_agent", agent_input)
        data = _learn_extract_json(raw)
        outline = validate_outline(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重新生成失败：{e}")

    # 清除旧计划所有已有结构
    await lpdb.delete_plan(plan_id)
    await lpdb.insert_outline(
        plan_id=plan_id,
        user_id=user["user_id"],
        assessment_id=outline_row["assessment_id"],
        stage_code=outline_row["stage_code"],
        stage_title=outline_row.get("stage_title"),
        modules=outline["modules"],
        total_weight=outline["total_weight"],
        estimated_weeks=outline["estimated_weeks"],
        user_preference=preference,
        status="pending",
    )
    return {"plan_id": plan_id, "outline": outline}


# ------------------------------------------------------------------ #
#  Step 2: 确认大纲 → 同步生成 roadmap + Week1 日任务                   #
# ------------------------------------------------------------------ #

@app.post("/plan/{plan_id}/confirm-outline", tags=["任务计划"], summary="确认大纲并生成路线图")
async def plan_confirm_outline(
    plan_id: str,
    user: dict = Depends(get_current_user),
):
    outline_row = await _verify_plan_owner(plan_id, user["user_id"])
    if outline_row["status"] == "ready":
        return {"plan_id": plan_id, "status": "ready", "message": "该计划已就绪"}
    if outline_row["status"] not in ("pending", "error"):
        raise HTTPException(status_code=400, detail=f"当前状态 {outline_row['status']} 不可确认")

    # 生成 roadmap
    await lpdb.update_outline(plan_id=plan_id, status="planning")

    try:
        agent_input = json.dumps({
            "modules": outline_row["modules"],
            "total_weeks_hint": outline_row.get("estimated_weeks") or 8,
            "suggested_daily_tasks": SUGGESTED_DAILY_TASKS,
            "user_preference": outline_row.get("user_preference") or "",
        }, ensure_ascii=False, indent=2)
        raw = await _call_learn_agent("plan_roadmap_agent", agent_input)
        data = _learn_extract_json(raw)
        module_ids = {m["id"] for m in outline_row["modules"]}
        roadmap = validate_roadmap(data, module_ids)
    except Exception as e:
        await lpdb.update_outline(plan_id=plan_id, status="error", error_msg=str(e))
        raise HTTPException(status_code=500, detail=f"路线图生成失败：{e}")

    # 落库 months / weeks
    month_id_map = await lpdb.insert_months(plan_id, roadmap["months"])
    week_id_map = await lpdb.insert_weeks(plan_id, roadmap["weeks"], month_id_map)
    await lpdb.update_outline(plan_id=plan_id, total_weeks=roadmap["total_weeks"])

    # 物化 Week 1 的 daily 任务
    try:
        await _materialize_week_sync(plan_id, week_num=1)
    except Exception as e:
        logger.warning(f"[Plan/confirm] Week 1 物化失败（继续返回路线图）: {e}")

    await lpdb.update_outline(plan_id=plan_id, status="ready")

    # 返回完整路线图
    months = await lpdb.list_months(plan_id)
    weeks = await lpdb.list_weeks(plan_id)
    logger.info(f"[Plan/confirm] plan_id={plan_id} total_weeks={roadmap['total_weeks']}")
    return {
        "plan_id": plan_id,
        "status": "ready",
        "total_weeks": roadmap["total_weeks"],
        "months": months,
        "weeks": weeks,
    }


# ------------------------------------------------------------------ #
#  单周物化                                                            #
# ------------------------------------------------------------------ #

async def _materialize_week_sync(plan_id: str, week_num: int) -> None:
    """物化某一周的 daily 任务。

    使用 CAS 幂等：如果已在 materializing/ready，立即返回。
    """
    claimed = await lpdb.try_claim_week_for_materialize(plan_id, week_num)
    if not claimed:
        return

    try:
        outline_row = await lpdb.get_outline(plan_id)
        week = await lpdb.get_week(plan_id, week_num)
        prev_week = await lpdb.get_week(plan_id, week_num - 1) if week_num > 1 else None

        # 找到本周覆盖的模块详情
        covered_ids = {c["module_id"] for c in (week.get("covers_modules") or [])}
        modules_detail = [m for m in outline_row["modules"] if m["id"] in covered_ids]

        agent_input = json.dumps({
            "week": {
                "week_num": week["week_num"],
                "theme": week["theme"],
                "week_goal": week["week_goal"],
                "covers_modules": week.get("covers_modules") or [],
                "weight_share": week["weight_share"],
            },
            "modules_detail": modules_detail,
            "suggested_daily_tasks": SUGGESTED_DAILY_TASKS,
            "previous_week_tasks": _summarize_prev_tasks(plan_id, prev_week) if prev_week else [],
        }, ensure_ascii=False, indent=2)

        raw = await _call_learn_agent("plan_daily_agent", agent_input)
        data = _learn_extract_json(raw)
        tasks = validate_daily_tasks(data, week_num=week_num)

        # 按本周内覆盖的主模块给 task 分配 module_id（取第一个）
        main_module_id = next(iter(covered_ids), None)
        for t in tasks:
            t["module_id"] = main_module_id

        normalize_task_contributions(tasks, week_weight_share=week["weight_share"])

        start_order = await lpdb.get_max_order_in_queue(plan_id)
        await lpdb.insert_tasks(plan_id, week["id"], tasks, start_order=start_order)
        await lpdb.mark_week_ready(plan_id, week_num)
        logger.info(f"[Plan/materialize] plan_id={plan_id} week={week_num} tasks={len(tasks)}")
    except Exception as e:
        logger.warning(f"[Plan/materialize] plan_id={plan_id} week={week_num} 失败: {e}")
        await lpdb.mark_week_error(plan_id, week_num, str(e))
        raise


def _summarize_prev_tasks(plan_id: str, prev_week: dict) -> list[dict]:
    """只是给 agent 看的简要（不查 DB，传 theme/goal 即可）。"""
    if not prev_week:
        return []
    return [{"theme": prev_week.get("theme"), "week_goal": prev_week.get("week_goal")}]


@app.post("/plan/{plan_id}/week/{week_num}/retry", tags=["任务计划"], summary="重试某周物化")
async def plan_retry_week(
    plan_id: str, week_num: int,
    user: dict = Depends(get_current_user),
):
    await _verify_plan_owner(plan_id, user["user_id"])
    await lpdb.reset_week_to_skeleton(plan_id, week_num)
    try:
        await _materialize_week_sync(plan_id, week_num)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重试失败：{e}")
    return {"ok": True, "week_num": week_num}


# ------------------------------------------------------------------ #
#  今日任务 / 再来一批 / 完成任务                                       #
# ------------------------------------------------------------------ #

@app.get("/plan/{plan_id}/today", tags=["任务计划"], summary="今日任务（前 N 个 pending）")
async def plan_today(
    plan_id: str,
    user: dict = Depends(get_current_user),
):
    outline_row = await _verify_plan_owner(plan_id, user["user_id"])
    if outline_row["status"] != "ready":
        raise HTTPException(status_code=400, detail=f"计划未就绪（status={outline_row['status']}）")
    tasks = await lpdb.get_today_tasks(plan_id, limit=SUGGESTED_DAILY_TASKS)
    return {"plan_id": plan_id, "tasks": tasks, "daily_limit": SUGGESTED_DAILY_TASKS}


class MoreTasksRequest(BaseModel):
    exclude_ids: list[int] = Field(default_factory=list)
    limit: int | None = None


@app.post("/plan/{plan_id}/more", tags=["任务计划"], summary="再来一批任务")
async def plan_more(
    plan_id: str, req: MoreTasksRequest,
    user: dict = Depends(get_current_user),
):
    await _verify_plan_owner(plan_id, user["user_id"])
    limit = req.limit or SUGGESTED_DAILY_TASKS
    tasks = await lpdb.get_more_tasks(plan_id, req.exclude_ids, limit=limit)
    return {"plan_id": plan_id, "tasks": tasks}


class CompleteTaskRequest(BaseModel):
    reflection: str | None = None


@app.post("/plan/task/{task_id}/complete", tags=["任务计划"], summary="完成任务 + 打分")
async def plan_complete_task(
    task_id: int, req: CompleteTaskRequest,
    user: dict = Depends(get_current_user),
):
    task = await lpdb.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"task_id={task_id} not found")
    outline_row = await _verify_plan_owner(task["plan_id"], user["user_id"])
    if task["status"] == "done":
        raise HTTPException(status_code=400, detail="任务已完成")

    reflection = (req.reflection or "").strip() or None

    if should_invoke_grader(reflection):
        try:
            agent_input = json.dumps({
                "task_title": task["title"],
                "task_description": task.get("description") or "",
                "completion_criteria": task.get("completion_criteria") or "",
                "reflection": reflection,
            }, ensure_ascii=False, indent=2)
            raw = await _call_learn_agent("learn_grader_agent", agent_input)
            data = _learn_extract_json(raw)
            score = clamp_grade(data.get("score"))
            comment = (data.get("comment") or "").strip()[:200] or "已记录"
        except Exception as e:
            logger.warning(f"[Plan/complete] grader 失败，用默认分: {e}")
            score = BASE_GRADE_SCORE
            comment = "打分失败，已按默认记录"
    else:
        score, comment = apply_default_grade(reflection)

    ac = float(task.get("actual_contribution") or 0)
    final_contribution = round(ac * score, 3)

    await lpdb.mark_task_done(
        task_id=task_id,
        reflection=reflection,
        grade_score=score,
        grade_comment=comment,
        final_contribution=final_contribution,
    )

    # 触发下一周物化（倒数第 2 天规则）
    current_week_pending = await lpdb.count_week_pending(task["plan_id"], task["week_id"])
    next_week = await lpdb.get_week(task["plan_id"], task["week_num"] + 1)
    if next_week and should_materialize_next_week(
        current_week_pending,
        next_week.get("daily_status"),
    ):
        asyncio.create_task(_materialize_week_safe(task["plan_id"], task["week_num"] + 1))

    progress = await lpdb.compute_plan_progress(task["plan_id"])

    return {
        "task_id": task_id,
        "grade_score": score,
        "grade_comment": comment,
        "final_contribution": final_contribution,
        "progress": progress,
    }


async def _materialize_week_safe(plan_id: str, week_num: int) -> None:
    """异步触发物化的 wrapper，捕获异常避免污染事件循环。"""
    try:
        await _materialize_week_sync(plan_id, week_num)
    except Exception as e:
        logger.warning(f"[Plan/materialize/async] plan_id={plan_id} week={week_num} 失败: {e}")


# ------------------------------------------------------------------ #
#  路线图 / 进度 / 当前计划                                              #
# ------------------------------------------------------------------ #

@app.get("/plan/{plan_id}/roadmap", tags=["任务计划"], summary="完整路线图")
async def plan_roadmap(
    plan_id: str,
    user: dict = Depends(get_current_user),
):
    outline_row = await _verify_plan_owner(plan_id, user["user_id"])
    # 兼容历史数据：如果 stage_title 缺失，或者等于 stage_code（早期 bug 导致写错），
    # 临时从 career_path_progress 回填并更新 DB
    stage_title = outline_row.get("stage_title")
    stage_code = outline_row["stage_code"]
    if not stage_title or stage_title == stage_code:
        stage_info = await _fetch_stage_info(
            outline_row["assessment_id"], stage_code
        )
        resolved = stage_info.get("title") if isinstance(stage_info, dict) else None
        if resolved and resolved != stage_code:
            stage_title = resolved
            # 顺手回写 DB，下次不再重查
            try:
                async with memory_db._pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "UPDATE learn_outlines SET stage_title=%s WHERE plan_id=%s",
                            (resolved, plan_id),
                        )
            except Exception:
                pass
    months = await lpdb.list_months(plan_id)
    weeks = await lpdb.list_weeks(plan_id)
    return {
        "plan_id": plan_id,
        "status": outline_row["status"],
        "stage_code": outline_row["stage_code"],
        "stage_title": stage_title,
        "total_weeks": outline_row.get("total_weeks"),
        "modules": outline_row["modules"],
        "months": months,
        "weeks": weeks,
    }


@app.get("/plan/{plan_id}/progress", tags=["任务计划"], summary="进度条数据")
async def plan_progress(
    plan_id: str,
    user: dict = Depends(get_current_user),
):
    await _verify_plan_owner(plan_id, user["user_id"])
    progress = await lpdb.compute_plan_progress(plan_id)
    return {"plan_id": plan_id, **progress}


@app.get("/plan/{plan_id}/recent-done", tags=["任务计划"], summary="最近完成的任务")
async def plan_recent_done(
    plan_id: str,
    days: int = 7,
    user: dict = Depends(get_current_user),
):
    await _verify_plan_owner(plan_id, user["user_id"])
    days = max(1, min(30, days))
    tasks = await lpdb.list_recent_done_tasks(plan_id, days=days)
    return {"plan_id": plan_id, "days": days, "tasks": tasks}


@app.get("/plan/list", tags=["任务计划"], summary="用户所有学习计划列表（含进度）")
async def plan_list(
    assessment_id: str | None = None,
    user: dict = Depends(get_current_user),
):
    plans = await lpdb.list_user_plans(user["user_id"], assessment_id=assessment_id)
    return {"plans": plans}


@app.get("/plan/current", tags=["任务计划"], summary="当前用户最新的 learn plan")
async def plan_current(user: dict = Depends(get_current_user)):
    outline = await lpdb.get_latest_outline_for_user(user["user_id"])
    if not outline:
        return {"plan_id": None}
    return {
        "plan_id": outline["plan_id"],
        "status": outline["status"],
        "assessment_id": outline["assessment_id"],
        "stage_code": outline["stage_code"],
        "stage_title": outline.get("stage_title"),
        "total_weeks": outline.get("total_weeks"),
        "estimated_weeks": outline.get("estimated_weeks"),
        "modules": outline["modules"],
    }


@app.delete("/plan/{plan_id}", tags=["任务计划"], summary="删除计划")
async def plan_delete(
    plan_id: str,
    user: dict = Depends(get_current_user),
):
    await _verify_plan_owner(plan_id, user["user_id"])
    await lpdb.delete_plan(plan_id)
    return {"ok": True, "plan_id": plan_id}


# ------------------------------------------------------------------ #
#  Stage 信息 / career_plan 上下文提取（给 outline agent 用）            #
# ------------------------------------------------------------------ #

async def _fetch_stage_info(assessment_id: str, stage_code: str) -> dict:
    """从 career_path_progress 里找到对应 stage_code 的 stage 信息。

    支持两种 stage_code 格式：
    - AI 推荐：{path_code}-s{stage_num}，如 "path-609b4c23-s1"
    - 自己输入：manual:{用户输入的目标岗位}，如 "manual:AI 工程师"
    """
    # 自己输入的目标岗位：直接用 stage_code 中的 title 部分
    if stage_code.startswith("manual:"):
        title = stage_code[len("manual:"):].strip() or "自定目标岗位"
        return {
            "code": stage_code,
            "title": title,
            "key_skills": [],
            "transition_from_prev": "",
            "match_reason": "用户自定义目标岗位，按此岗位规划学习路径",
            "key_gaps": [],
        }

    path_code = stage_code
    target_stage_num: int | None = None
    m = re.match(r"^(.+)-s(\d+)$", stage_code)
    if m:
        path_code = m.group(1)
        target_stage_num = int(m.group(2))

    if memory_db._pool is None:
        return {"code": stage_code}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT path_data, current_stage FROM career_path_progress
                   WHERE assessment_id=%s AND path_code=%s""",
                (assessment_id, path_code),
            )
            row = await cur.fetchone()
    if not row:
        return await _fetch_stage_info_by_stage_code(assessment_id, stage_code)
    path_data = json.loads(row[0]) if row[0] else {}
    current_stage_db = row[1] or 1
    stages = path_data.get("stages") or []
    pick_stage = target_stage_num or current_stage_db
    target = next((s for s in stages if s.get("stage") == pick_stage),
                  stages[0] if stages else {})
    return {
        "code": stage_code,
        "title": target.get("title"),
        "timeframe": target.get("timeframe"),
        "salary_range": target.get("salary_range"),
        "key_skills": target.get("key_skills") or [],
        "transition_from_prev": target.get("transition_from_prev") or "",
        "match_score": target.get("match_score"),
        "match_reason": target.get("match_reason"),
        "key_gaps": target.get("key_gaps") or [],
    }


async def _fetch_stage_info_by_stage_code(assessment_id: str, stage_code: str) -> dict:
    if memory_db._pool is None:
        return {"code": stage_code}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT path_data FROM career_path_progress WHERE assessment_id=%s",
                (assessment_id,),
            )
            rows = await cur.fetchall()
    for r in rows:
        data = json.loads(r[0]) if r[0] else {}
        for s in data.get("stages") or []:
            # stage 对象里可能有 "code" 字段（与 path_code 不同）
            if s.get("code") == stage_code or s.get("title") == stage_code:
                return {
                    "code": stage_code,
                    "title": s.get("title"),
                    "key_skills": s.get("key_skills") or [],
                    "timeframe": s.get("timeframe"),
                    "transition_from_prev": s.get("transition_from_prev") or "",
                }
    return {"code": stage_code, "title": stage_code}


async def _fetch_career_plan_context(assessment_id: str, stage_code: str) -> dict:
    """读取 career_plan_blocks 里该 stage 对应的计划块作为上下文。"""
    if memory_db._pool is None:
        return {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT block_id, block_json FROM career_plan_blocks
                   WHERE assessment_id=%s AND onetsoc_code=%s""",
                (assessment_id, stage_code),
            )
            rows = await cur.fetchall()
    result: dict[str, Any] = {}
    for block_id, block_json in rows:
        try:
            result[block_id] = json.loads(block_json) if block_json else {}
        except Exception:
            continue
    return result


def _build_profile_brief(dims: dict, summary: dict) -> dict:
    """把 6 维度 + summary 压缩成 agent 输入用的简要画像。"""
    brief = {"dimensions": {}, "summary": None}
    for dim_name, dim_data in dims.items():
        brief["dimensions"][dim_name] = {
            "overall_score": dim_data.get("overall_score"),
            "confidence": dim_data.get("confidence"),
            "dimension_summary": (dim_data.get("dimension_summary") or "")[:300],
            "sub_dimensions": [
                {
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "score": s.get("score"),
                }
                for s in (dim_data.get("sub_dimensions") or [])[:10]
            ],
        }
    if summary:
        brief["summary"] = {
            "overall_persona": summary.get("overall_persona"),
            "strengths": summary.get("strengths") or [],
            "gaps": summary.get("gaps") or [],
        }
    return brief
