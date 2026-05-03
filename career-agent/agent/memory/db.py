import json
import aiomysql
from agent.logger import get_logger

logger = get_logger("agent.memory")

# 全局连接池，通过 init_pool() 初始化，整个进程共享
_pool: aiomysql.Pool | None = None

_CREATE_MESSAGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id           BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_id   VARCHAR(64)  NOT NULL,
    role         VARCHAR(20)  NOT NULL,
    content      LONGTEXT     NOT NULL,
    tool_call_id VARCHAR(255) DEFAULT NULL,
    created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_CREATE_TRACES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS traces (
    id               BIGINT PRIMARY KEY AUTO_INCREMENT,
    trace_id         VARCHAR(32)  NOT NULL,
    parent_trace_id  VARCHAR(32)  DEFAULT NULL,
    session_id       VARCHAR(64)  DEFAULT NULL,
    span_type        VARCHAR(20)  NOT NULL,
    name             VARCHAR(255) DEFAULT NULL,
    input            LONGTEXT     DEFAULT NULL,
    output           LONGTEXT     DEFAULT NULL,
    elapsed_ms       INT          DEFAULT NULL,
    prompt_tokens    INT          DEFAULT NULL,
    completion_tokens INT         DEFAULT NULL,
    total_tokens     INT          DEFAULT NULL,
    created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_trace (trace_id),
    INDEX idx_parent (parent_trace_id),
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_CREATE_CHAT_SESSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id VARCHAR(64)  PRIMARY KEY,
    user_id    BIGINT       NOT NULL,
    created_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_cs_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_CREATE_PLAN_SCHEDULES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS plan_schedules (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    plan_id       VARCHAR(32)  NOT NULL UNIQUE,
    assessment_id VARCHAR(64)  NOT NULL,
    onetsoc_code  VARCHAR(20)  NOT NULL,
    duration_weeks INT         NOT NULL DEFAULT 4,
    start_date    DATE         NOT NULL,
    status        VARCHAR(30)  DEFAULT 'weekly_draft',
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_ps_assessment (assessment_id, onetsoc_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_CREATE_PLAN_WEEKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS plan_weeks (
    id           BIGINT PRIMARY KEY AUTO_INCREMENT,
    plan_id      VARCHAR(32)  NOT NULL,
    week_number  INT          NOT NULL,
    theme        VARCHAR(200),
    focus        TEXT,
    weekly_goals JSON,
    phase_ref    VARCHAR(20),
    confirmed    BOOLEAN      DEFAULT FALSE,
    UNIQUE KEY uq_pw (plan_id, week_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_CREATE_PLAN_DAILY_TASKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS plan_daily_tasks (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    plan_id       VARCHAR(32)  NOT NULL,
    week_number   INT          NOT NULL,
    day_number    INT          NOT NULL,
    date          DATE         NOT NULL,
    tasks         JSON         NOT NULL,
    completed_ids JSON         DEFAULT (JSON_ARRAY()),
    task_notes    JSON         DEFAULT (JSON_OBJECT()),
    INDEX idx_pdt (plan_id, week_number, day_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_ALTER_PLAN_DAILY_TASKS_ADD_NOTES_SQL = """
ALTER TABLE plan_daily_tasks
ADD COLUMN task_notes JSON DEFAULT (JSON_OBJECT());
"""

_CREATE_RESUME_UPLOADS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS resume_uploads (
    id         BIGINT PRIMARY KEY AUTO_INCREMENT,
    upload_id  VARCHAR(32)  NOT NULL UNIQUE,
    session_id VARCHAR(64)  DEFAULT NULL,
    file_name  VARCHAR(255) DEFAULT NULL,
    raw_text   LONGTEXT,
    extracted  JSON,
    created_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ru_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_CREATE_USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id         BIGINT PRIMARY KEY AUTO_INCREMENT,
    username   VARCHAR(50)  NOT NULL UNIQUE,
    password   VARCHAR(255) NOT NULL,
    email      VARCHAR(255) DEFAULT NULL,
    is_admin   BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_CREATE_CANDIDATES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS candidates (
    id                   BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id              BIGINT NOT NULL,
    name                 VARCHAR(100),
    age                  INT,
    city                 VARCHAR(50),
    education            VARCHAR(50),
    current_title        VARCHAR(200),
    target_role          VARCHAR(200),
    years_of_experience  INT DEFAULT 0,
    resume_raw           JSON,
    supplement           TEXT,
    bigfive              JSON,
    riasec               JSON,
    quiz_abilities       JSON,
    quiz_knowledge       JSON,
    third_party          JSON,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user (user_id),
    INDEX idx_cand_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# 为核心表追加 user_id 字段（列已存在时 1060 Duplicate column 可忽略）
_ALTER_ADD_USER_ID_SQLS = [
    "ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE assessment_jobs ADD COLUMN user_id BIGINT DEFAULT NULL",
    "ALTER TABLE plan_schedules ADD COLUMN user_id BIGINT DEFAULT NULL",
    "ALTER TABLE resume_uploads ADD COLUMN user_id BIGINT DEFAULT NULL",
]

# 补充索引（索引已存在时 1061 可忽略）
_ALTER_ADD_INDEX_SQLS = [
    "ALTER TABLE assessment_jobs ADD INDEX idx_aj_user (user_id)",
    "ALTER TABLE assessment_jobs ADD INDEX idx_aj_status (status)",
    "ALTER TABLE assessment_jobs ADD INDEX idx_aj_user_created (user_id, created_at DESC)",
    "ALTER TABLE assessment_jobs ADD INDEX idx_aj_user_status (user_id, status)",
    "ALTER TABLE plan_schedules ADD INDEX idx_ps_user (user_id)",
    "ALTER TABLE plan_daily_tasks ADD INDEX idx_pdt_date (plan_id, date)",
    "ALTER TABLE resume_uploads ADD INDEX idx_ru_user (user_id)",
    "ALTER TABLE career_plan_blocks ADD INDEX idx_cpb_assessment (assessment_id)",
    "ALTER TABLE assessment_dimensions ADD INDEX idx_ad_assessment (assessment_id)",
]


async def init_pool(host: str, port: int, user: str, password: str, db: str,
                    minsize: int = 1, maxsize: int = 5) -> None:
    """
    初始化全局 MySQL 连接池，并自动建表（如果不存在）。
    在进程启动时调用一次即可。
    """
    global _pool
    _pool = await aiomysql.create_pool(
        host=host, port=port,
        user=user, password=password, db=db,
        minsize=minsize, maxsize=maxsize,
        autocommit=True,
        charset="utf8mb4",
    )
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_CREATE_MESSAGES_TABLE_SQL)
            await cur.execute(_CREATE_TRACES_TABLE_SQL)
            await cur.execute(_CREATE_CHAT_SESSIONS_TABLE_SQL)
            await cur.execute(_CREATE_PLAN_SCHEDULES_TABLE_SQL)
            await cur.execute(_CREATE_PLAN_WEEKS_TABLE_SQL)
            await cur.execute(_CREATE_PLAN_DAILY_TASKS_TABLE_SQL)
            await cur.execute(_CREATE_RESUME_UPLOADS_TABLE_SQL)
            await cur.execute(_CREATE_USERS_TABLE_SQL)
            await cur.execute(_CREATE_CANDIDATES_TABLE_SQL)
            # 旧库迁移：为已有 plan_daily_tasks 增加 task_notes 列；列已存在时 1060 可忽略
            try:
                await cur.execute(_ALTER_PLAN_DAILY_TASKS_ADD_NOTES_SQL)
            except Exception as e:
                if "1060" not in str(e) and "Duplicate column name" not in str(e):
                    raise
                logger.debug("[Memory] task_notes 列已存在，跳过 ALTER")
            # 为核心表追加 user_id 字段
            for sql in _ALTER_ADD_USER_ID_SQLS:
                try:
                    await cur.execute(sql)
                except Exception as e:
                    if "1060" not in str(e) and "Duplicate column name" not in str(e):
                        raise
            # 补充缺失索引（1061=索引已存在，可忽略）
            for sql in _ALTER_ADD_INDEX_SQLS:
                try:
                    await cur.execute(sql)
                except Exception as e:
                    err_str = str(e)
                    if "1061" not in err_str and "Duplicate key name" not in err_str:
                        logger.warning(f"[Memory] 建索引跳过: {e}")
    logger.debug(f"[Memory] 连接池初始化成功，数据库：{db}@{host}:{port}")


async def close_pool() -> None:
    """关闭全局连接池，在进程退出时调用。"""
    global _pool
    if _pool:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        logger.debug("[Memory] 连接池已关闭")


async def save_message(session_id: str, role: str, content, tool_call_id: str | None = None) -> None:
    """
    持久化一条消息到数据库。
    content 允许传字符串或 LiteLLM 返回的 message 对象（自动序列化为 JSON 字符串）。
    """
    if _pool is None:
        return  # 未初始化时静默跳过，不影响主流程

    # LiteLLM 的 assistant message 是对象（含 tool_calls），需要序列化
    if isinstance(content, str):
        content_str = content
    else:
        content_str = json.dumps(content, default=str)

    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id) VALUES (%s, %s, %s, %s)",
                (session_id, role, content_str, tool_call_id),
            )
    logger.debug(f"[Memory] 保存消息  session={session_id}  role={role}")


async def load_messages(session_id: str) -> list[dict]:
    """
    加载某会话的全部历史消息，按时间升序，返回与 self.messages 兼容的 list[dict]。
    如果连接池未初始化或会话不存在，返回空列表。
    """
    if _pool is None:
        return []

    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT role, content, tool_call_id FROM messages "
                "WHERE session_id = %s ORDER BY created_at ASC, id ASC",
                (session_id,),
            )
            rows = await cur.fetchall()

    messages = []
    for row in rows:
        msg: dict = {"role": row["role"], "content": row["content"]}
        if row["tool_call_id"]:
            msg["tool_call_id"] = row["tool_call_id"]
        # assistant 消息若是 JSON 字符串（含 tool_calls），尝试反序列化
        if row["role"] == "assistant":
            try:
                parsed = json.loads(row["content"])
                if isinstance(parsed, dict):
                    msg = parsed  # 恢复完整的 message 对象格式
            except (json.JSONDecodeError, TypeError):
                pass  # 普通文本 content，保持不变
        messages.append(msg)

    logger.debug(f"[Memory] 加载历史  session={session_id}  共 {len(messages)} 条消息")
    return messages


async def register_chat_session(session_id: str, user_id: int) -> None:
    """记录 session_id → user_id 映射（已存在时忽略）。"""
    if _pool is None:
        return
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO chat_sessions (session_id, user_id) VALUES (%s, %s)",
                (session_id, user_id),
            )


async def delete_user_messages(user_id: int) -> int:
    """删除某用户的全部聊天记录，返回删除条数。"""
    if _pool is None:
        return 0
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            affected = await cur.execute(
                """DELETE m FROM messages m
                   JOIN chat_sessions cs ON cs.session_id = m.session_id
                   WHERE cs.user_id = %s""",
                (user_id,),
            )
            await cur.execute("DELETE FROM chat_sessions WHERE user_id = %s", (user_id,))
    logger.info(f"[Memory] 删除用户 {user_id} 的聊天记录  共 {affected} 条")
    return affected


async def save_span(
    trace_id: str,
    span_type: str,
    *,
    parent_trace_id: str | None = None,
    session_id: str | None = None,
    name: str | None = None,
    input: object = None,
    output: object = None,
    elapsed_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    """
    写入一条 trace span 记录。
    input / output 允许传任意对象，自动序列化为 JSON 字符串。
    """
    if _pool is None:
        return

    def _to_str(v) -> str | None:
        if v is None:
            return None
        return v if isinstance(v, str) else json.dumps(v, default=str)

    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO traces
                   (trace_id, parent_trace_id, session_id, span_type, name,
                    input, output, elapsed_ms,
                    prompt_tokens, completion_tokens, total_tokens)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    trace_id, parent_trace_id, session_id, span_type, name,
                    _to_str(input), _to_str(output), elapsed_ms,
                    prompt_tokens, completion_tokens, total_tokens,
                ),
            )
    logger.debug(f"[Trace] 写入 span  trace={trace_id}  type={span_type}  name={name}")
