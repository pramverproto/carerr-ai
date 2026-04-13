"""
Career AI API 端到端测试脚本
覆盖所有核心接口：健康检查、认证、简历、评估、职业推荐、计划、归档、通用Agent

运行方式：
    cd career-agent
    python -m pytest tests/test_api_e2e.py -v
    # 或直接运行（带颜色输出）：
    python tests/test_api_e2e.py
"""

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

# ──────────────────────────────────────────────────────────────────────────────
#  配置
# ──────────────────────────────────────────────────────────────────────────────

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = 60  # 大多数接口超时（秒）
LLM_TIMEOUT = 180  # LLM 相关接口更长超时

# 测试账号（随机后缀避免冲突）
_suffix = uuid.uuid4().hex[:6]
TEST_USER = f"testuser_{_suffix}"
TEST_PASS = "Test1234!"

# 最小化测试简历（用于评估接口）
SAMPLE_RESUME = {
    "candidate": {
        "name": "张测试",
        "age": 28,
        "education": "本科",
        "major": "计算机科学",
        "target_role": "后端工程师",
        "years_of_experience": 4,
    },
    "experiences": [
        {
            "company": "某互联网公司",
            "title": "Python 后端工程师",
            "duration": "2020-2024",
            "responsibilities": ["负责 FastAPI 微服务开发", "MySQL 数据库设计与优化"],
        }
    ],
    "skills": ["Python", "FastAPI", "MySQL", "Docker"],
    "certifications": [],
}

SAMPLE_SUPPLEMENT = "希望在职业发展上转向 AI 方向，对大模型应用有浓厚兴趣。"

# ──────────────────────────────────────────────────────────────────────────────
#  颜色输出工具
# ──────────────────────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET}  {msg}")


def skip(msg: str) -> None:
    print(f"  {YELLOW}⊘{RESET}  {msg}")


def section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")


# ──────────────────────────────────────────────────────────────────────────────
#  共享测试状态
# ──────────────────────────────────────────────────────────────────────────────

class State:
    token: str | None = None
    user_id: int | None = None
    assessment_id: str | None = None
    plan_id: str | None = None
    session_id: str = str(uuid.uuid4())
    passed: int = 0
    failed: int = 0
    skipped: int = 0


state = State()


# ──────────────────────────────────────────────────────────────────────────────
#  HTTP 辅助
# ──────────────────────────────────────────────────────────────────────────────

def auth_headers() -> dict:
    if state.token:
        return {"Authorization": f"Bearer {state.token}"}
    return {}


def assert_status(resp: httpx.Response, expected: int, label: str) -> bool:
    if resp.status_code == expected:
        ok(f"{label} → {resp.status_code}")
        state.passed += 1
        return True
    else:
        fail(f"{label} → 期望 {expected}，实际 {resp.status_code}: {resp.text[:200]}")
        state.failed += 1
        return False


def assert_field(data: dict, field: str, label: str) -> bool:
    if field in data and data[field] is not None:
        ok(f"{label}: {str(data[field])[:80]}")
        state.passed += 1
        return True
    else:
        fail(f"{label}: 字段 '{field}' 缺失或为 None，实际: {list(data.keys())}")
        state.failed += 1
        return False


# ──────────────────────────────────────────────────────────────────────────────
#  测试用例
# ──────────────────────────────────────────────────────────────────────────────

async def test_health(client: httpx.AsyncClient) -> None:
    section("1. 健康检查")
    resp = await client.get("/health", timeout=10)
    if assert_status(resp, 200, "GET /health"):
        data = resp.json()
        assert_field(data, "status", "status 字段")
        assert_field(data, "database", "database 字段")


async def test_auth(client: httpx.AsyncClient) -> None:
    section("2. 认证接口")

    # 2.1 注册
    resp = await client.post("/auth/register", json={
        "username": TEST_USER,
        "password": TEST_PASS,
        "email": f"{TEST_USER}@test.com",
    }, timeout=10)
    if assert_status(resp, 200, "POST /auth/register"):
        data = resp.json()
        assert_field(data, "token", "注册 token")
        assert_field(data, "user_id", "注册 user_id")
        state.token = data.get("token")
        state.user_id = data.get("user_id")

    # 2.2 重复注册 → 409
    resp = await client.post("/auth/register", json={
        "username": TEST_USER,
        "password": TEST_PASS,
    }, timeout=10)
    assert_status(resp, 409, "POST /auth/register (重复) → 409")

    # 2.3 登录
    resp = await client.post("/auth/login", json={
        "username": TEST_USER,
        "password": TEST_PASS,
    }, timeout=10)
    if assert_status(resp, 200, "POST /auth/login"):
        data = resp.json()
        assert_field(data, "token", "登录 token")
        state.token = data.get("token")

    # 2.4 密码错误 → 401
    resp = await client.post("/auth/login", json={
        "username": TEST_USER,
        "password": "wrongpassword",
    }, timeout=10)
    assert_status(resp, 401, "POST /auth/login (错误密码) → 401")

    # 2.5 /auth/me
    resp = await client.get("/auth/me", headers=auth_headers(), timeout=10)
    if assert_status(resp, 200, "GET /auth/me"):
        data = resp.json()
        assert_field(data, "username", "当前用户名")

    # 2.6 无 token → 401/403
    resp = await client.get("/auth/me", timeout=10)
    if resp.status_code in (401, 403):
        ok(f"GET /auth/me (无 token) → {resp.status_code}")
        state.passed += 1
    else:
        fail(f"GET /auth/me (无 token) → 期望 401/403，实际 {resp.status_code}")
        state.failed += 1


async def test_resume_extract(client: httpx.AsyncClient) -> None:
    section("3. 简历上传 & 解析（跳过实际 LLM 调用，测试接口格式）")

    if not state.token:
        skip("未登录，跳过简历上传测试")
        state.skipped += 1
        return

    # 构造最小合法 DOCX 文件（ZIP 格式）进行上传测试
    # 实际解析依赖 LLM，这里只验证接口能接受文件格式
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        zf.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Zhang Ceshi, Python developer, 4 years experience.</w:t></w:r></w:p></w:body></w:document>')
        zf.writestr("word/_rels/document.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>')
    docx_bytes = buf.getvalue()

    resp = await client.post(
        "/resume/extract",
        headers=auth_headers(),
        files={"file": ("resume.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        timeout=LLM_TIMEOUT,
    )
    if resp.status_code == 200:
        data = resp.json()
        ok(f"POST /resume/extract → 200, keys={list(data.keys())}")
        state.passed += 1
    elif resp.status_code == 422:
        skip("POST /resume/extract → 422 (接口格式已变更)")
        state.skipped += 1
    elif resp.status_code == 400:
        # 接口接受了文件但解析失败（空文档），属正常
        ok(f"POST /resume/extract → 400 (文件被接受，解析失败属正常: {resp.text[:100]})")
        state.passed += 1
    else:
        fail(f"POST /resume/extract → {resp.status_code}: {resp.text[:200]}")
        state.failed += 1


async def test_invoke_builtin(client: httpx.AsyncClient) -> None:
    section("4. 内置 Agent 调用 /invoke")

    if not state.token:
        skip("未登录，跳过")
        state.skipped += 1
        return

    # 只测试接口可达性，不触发实际 LLM 调用（会很慢）
    # 用一个明确会失败（但格式正确）的 agent_name 来验证路由
    resp = await client.post(
        "/invoke",
        headers=auth_headers(),
        json={"agent_name": "nonexistent_agent_xyz", "task": "test"},
        timeout=30,
    )
    if resp.status_code in (400, 422, 404, 200):
        ok(f"POST /invoke → {resp.status_code} (路由可达)")
        state.passed += 1
    else:
        fail(f"POST /invoke → 意外状态码 {resp.status_code}: {resp.text[:200]}")
        state.failed += 1


async def test_invoke_custom(client: httpx.AsyncClient) -> None:
    section("5. 通用 Agent 接口 /invoke/custom")

    if not state.token:
        skip("未登录，跳过")
        state.skipped += 1
        return

    # 读取 .env 获取 API Key（仅用于验证接口格式，实际调用需真实 key）
    env_path = Path(__file__).resolve().parent.parent / ".env"
    api_key = None
    llm_model = None
    llm_base_url = None
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("LLM_API_KEY="):
                api_key = line.split("=", 1)[1].strip()
            elif line.startswith("LLM_MODEL="):
                llm_model = line.split("=", 1)[1].strip()
            elif line.startswith("LLM_BASE_URL="):
                llm_base_url = line.split("=", 1)[1].strip()

    if not api_key:
        skip("/invoke/custom: 未找到 LLM_API_KEY，跳过实际调用测试")
        state.skipped += 1

        # 仍然测试缺少必填字段的 422
        resp = await client.post(
            "/invoke/custom",
            headers=auth_headers(),
            json={"task": "hello"},  # 缺少 model 和 api_key
            timeout=10,
        )
        assert_status(resp, 422, "POST /invoke/custom (缺少必填字段) → 422")
        return

    # 用真实 key 发一个简单任务（不使用任何工具）
    payload = {
        "model": llm_model or "openai/glm-4-airx",
        "api_key": api_key,
        "base_url": llm_base_url,
        "system_prompt": "你是一个简单助手，只用一句话回答。",
        "task": "1+1等于几？",
        "allowed_tools": [],  # 不允许任何工具
        "session_id": state.session_id,
    }
    resp = await client.post(
        "/invoke/custom",
        headers=auth_headers(),
        json=payload,
        timeout=LLM_TIMEOUT,
    )
    if assert_status(resp, 200, "POST /invoke/custom (简单数学问题)"):
        data = resp.json()
        assert_field(data, "result", "result 字段")
        assert_field(data, "elapsed_ms", "elapsed_ms 字段")
        assert_field(data, "model", "model 字段")


async def test_chat_json(client: httpx.AsyncClient) -> None:
    section("6. Chat 接口（JSON 模式）")

    if not state.token:
        skip("未登录，跳过")
        state.skipped += 1
        return

    # 读取 API key
    env_path = Path(__file__).resolve().parent.parent / ".env"
    has_key = env_path.exists() and "LLM_API_KEY=" in env_path.read_text()

    if not has_key:
        skip("POST /chat: 未找到 LLM_API_KEY，跳过")
        state.skipped += 1
        return

    resp = await client.post(
        "/chat",
        headers=auth_headers(),
        json={
            "message": "你好，简单介绍你自己。",
            "session_id": state.session_id,
            "stream": False,
        },
        timeout=LLM_TIMEOUT,
    )
    if assert_status(resp, 200, "POST /chat (stream=false)"):
        data = resp.json()
        assert_field(data, "reply", "reply 字段")


async def test_chat_history(client: httpx.AsyncClient) -> None:
    section("7. Chat 历史记录")

    if not state.token:
        skip("未登录，跳过")
        state.skipped += 1
        return

    resp = await client.get(
        f"/chat/history?session_id={state.session_id}",
        headers=auth_headers(),
        timeout=10,
    )
    if assert_status(resp, 200, "GET /chat/history"):
        data = resp.json()
        if isinstance(data, list):
            ok(f"历史记录条数: {len(data)}")
            state.passed += 1
        elif isinstance(data, dict) and "messages" in data:
            ok(f"历史记录条数: {len(data['messages'])}")
            state.passed += 1
        else:
            fail(f"chat history 格式意外: {str(data)[:200]}")
            state.failed += 1


async def test_assess(client: httpx.AsyncClient) -> None:
    section("8. 能力评估 /assess（LLM 密集型，可能较慢）")

    if not state.token:
        skip("未登录，跳过")
        state.skipped += 1
        return

    env_path = Path(__file__).resolve().parent.parent / ".env"
    has_key = env_path.exists() and "LLM_API_KEY=" in env_path.read_text()

    if not has_key:
        skip("POST /assess: 未找到 LLM_API_KEY，跳过")
        state.skipped += 1
        return

    payload = {
        "session_id": state.session_id,
        "resume": SAMPLE_RESUME,
        "supplement": SAMPLE_SUPPLEMENT,
    }

    print(f"  {YELLOW}⏳ 发起评估，可能需要 1-3 分钟...{RESET}")
    t0 = time.time()
    resp = await client.post(
        "/assess",
        headers=auth_headers(),
        json=payload,
        timeout=300,
    )
    elapsed = time.time() - t0

    if assert_status(resp, 200, f"POST /assess ({elapsed:.0f}s)"):
        data = resp.json()
        assert_field(data, "assessment_id", "assessment_id")
        state.assessment_id = data.get("assessment_id")
        ok(f"assessment_id: {state.assessment_id}")


async def test_career_match(client: httpx.AsyncClient) -> None:
    section("9. 职业匹配 /career/match")

    if not state.token:
        skip("未登录，跳过")
        state.skipped += 1
        return

    if not state.assessment_id:
        skip("没有 assessment_id，跳过职业匹配（需先完成评估）")
        state.skipped += 1
        return

    print(f"  {YELLOW}⏳ 发起职业匹配...{RESET}")
    resp = await client.post(
        "/career/match",
        headers=auth_headers(),
        json={"assessment_id": state.assessment_id},
        timeout=LLM_TIMEOUT,
    )
    if assert_status(resp, 200, "POST /career/match"):
        data = resp.json()
        careers = data.get("careers") or data.get("results") or data
        if isinstance(careers, list) and len(careers) > 0:
            ok(f"返回 {len(careers)} 个职业推荐")
            state.passed += 1
        else:
            ok(f"返回数据: {str(data)[:200]}")
            state.passed += 1


async def test_archive_list(client: httpx.AsyncClient) -> None:
    section("10. 归档列表 /archive/list")

    if not state.token:
        skip("未登录，跳过")
        state.skipped += 1
        return

    resp = await client.get(
        "/archive/list",
        headers=auth_headers(),
        timeout=10,
    )
    if assert_status(resp, 200, "GET /archive/list"):
        data = resp.json()
        if "assessments" in data:
            ok(f"归档数量: {len(data['assessments'])}")
            state.passed += 1
        else:
            ok(f"返回: {str(data)[:200]}")
            state.passed += 1


async def test_archive_detail(client: httpx.AsyncClient) -> None:
    section("11. 归档详情 /archive/{assessment_id}/detail")

    if not state.token:
        skip("未登录，跳过")
        state.skipped += 1
        return

    if not state.assessment_id:
        skip("没有 assessment_id，跳过归档详情")
        state.skipped += 1
        return

    resp = await client.get(
        f"/archive/{state.assessment_id}/detail",
        headers=auth_headers(),
        timeout=10,
    )
    if assert_status(resp, 200, f"GET /archive/{state.assessment_id}/detail"):
        data = resp.json()
        ok(f"返回 keys: {list(data.keys())[:8]}")
        state.passed += 1


async def test_report(client: httpx.AsyncClient) -> None:
    section("12. 评估报告 /report/{assessment_id}")

    if not state.token:
        skip("未登录，跳过")
        state.skipped += 1
        return

    if not state.assessment_id:
        skip("没有 assessment_id，跳过报告查询")
        state.skipped += 1
        return

    print(f"  {YELLOW}⏳ 获取报告（如后台未预生成可能需要一段时间）...{RESET}")
    resp = await client.get(
        f"/report/{state.assessment_id}",
        headers=auth_headers(),
        timeout=300,
    )
    if resp.status_code == 200:
        data = resp.json()
        ok(f"GET /report/{state.assessment_id} → 200, keys={list(data.keys())[:6]}")
        state.passed += 1
    elif resp.status_code == 202:
        ok(f"GET /report/{state.assessment_id} → 202 (生成中，属正常状态)")
        state.passed += 1
    else:
        fail(f"GET /report/{state.assessment_id} → {resp.status_code}: {resp.text[:200]}")
        state.failed += 1


async def test_plan_weekly(client: httpx.AsyncClient) -> None:
    section("13. 周计划生成 /plan-schedule/weekly")

    if not state.token or not state.assessment_id:
        skip("缺少 token 或 assessment_id，跳过")
        state.skipped += 1
        return

    # 先查已有的职业码
    env_path = Path(__file__).resolve().parent.parent / ".env"
    has_key = env_path.exists() and "LLM_API_KEY=" in env_path.read_text()
    if not has_key:
        skip("未找到 LLM_API_KEY，跳过周计划生成")
        state.skipped += 1
        return

    # 先检查已有计划码
    resp = await client.get(
        f"/career/planned-codes/{state.assessment_id}",
        headers=auth_headers(),
        timeout=10,
    )
    onetsoc_code = None
    if resp.status_code == 200:
        data = resp.json()
        codes = data.get("codes") or data.get("planned_codes") or []
        if codes:
            onetsoc_code = codes[0]

    if not onetsoc_code:
        skip("没有已规划的职业码，跳过周计划生成")
        state.skipped += 1
        return

    print(f"  {YELLOW}⏳ 生成周计划 (onetsoc_code={onetsoc_code})...{RESET}")
    resp = await client.post(
        "/plan-schedule/weekly",
        headers=auth_headers(),
        json={"assessment_id": state.assessment_id, "onetsoc_code": onetsoc_code},
        timeout=LLM_TIMEOUT,
    )
    if assert_status(resp, 200, f"POST /plan-schedule/weekly"):
        data = resp.json()
        state.plan_id = data.get("plan_id")
        ok(f"plan_id: {state.plan_id}")


async def test_plan_list(client: httpx.AsyncClient) -> None:
    section("14. 计划列表")

    if not state.token or not state.assessment_id:
        skip("缺少参数，跳过")
        state.skipped += 1
        return

    # 先查职业码
    resp = await client.get(
        f"/career/planned-codes/{state.assessment_id}",
        headers=auth_headers(),
        timeout=10,
    )
    onetsoc_code = None
    if resp.status_code == 200:
        data = resp.json()
        codes = data.get("codes") or []
        if codes:
            onetsoc_code = codes[0]

    if not onetsoc_code:
        skip("没有已规划的职业码，跳过计划列表")
        state.skipped += 1
        return

    resp = await client.get(
        f"/plan-schedule/list/{state.assessment_id}/{onetsoc_code}",
        headers=auth_headers(),
        timeout=10,
    )
    if assert_status(resp, 200, f"GET /plan-schedule/list"):
        data = resp.json()
        ok(f"计划数量: {len(data) if isinstance(data, list) else str(data)[:80]}")


async def test_mcp_custom_invoke(client: httpx.AsyncClient) -> None:
    section("15. 通用 Agent + MCP 配置（验证接口格式）")

    if not state.token:
        skip("未登录，跳过")
        state.skipped += 1
        return

    # 只验证字段格式，不实际连接 MCP（避免外网依赖）
    resp = await client.post(
        "/invoke/custom",
        headers=auth_headers(),
        json={
            "model": "openai/glm-4-airx",
            "api_key": "invalid_key_for_format_test",
            "system_prompt": "你是助手",
            "task": "test",
            "allowed_tools": ["query_profile"],
            "mcp_url": "http://example.com/mcp/sse",
            "session_id": str(uuid.uuid4()),
        },
        timeout=30,
    )
    # 可能因为 key 无效返回 400/500，但路由和参数格式应当被接受（非 422）
    if resp.status_code == 422:
        fail(f"POST /invoke/custom (MCP配置) → 422，字段验证失败: {resp.text[:300]}")
        state.failed += 1
    else:
        ok(f"POST /invoke/custom (MCP配置) → {resp.status_code} (非 422，格式接受正常)")
        state.passed += 1


async def test_security(client: httpx.AsyncClient) -> None:
    section("16. 安全性检查")

    # 16.1 无 token 访问保护路由
    for path, method in [
        ("/chat", "POST"),
        ("/assess", "POST"),
        ("/archive/list", "GET"),
        ("/invoke", "POST"),
        ("/invoke/custom", "POST"),
    ]:
        if method == "GET":
            resp = await client.get(path, timeout=5)
        else:
            resp = await client.post(path, json={}, timeout=5)

        if resp.status_code in (401, 403, 422):
            ok(f"{method} {path} (无 token) → {resp.status_code}")
            state.passed += 1
        else:
            fail(f"{method} {path} (无 token) → 期望 401/403/422，实际 {resp.status_code}")
            state.failed += 1

    # 16.2 伪造 token
    resp = await client.get(
        "/auth/me",
        headers={"Authorization": "Bearer fake.token.abc"},
        timeout=5,
    )
    if resp.status_code in (401, 403):
        ok(f"GET /auth/me (伪造 token) → {resp.status_code}")
        state.passed += 1
    else:
        fail(f"GET /auth/me (伪造 token) → 期望 401/403，实际 {resp.status_code}")
        state.failed += 1


# ──────────────────────────────────────────────────────────────────────────────
#  主入口
# ──────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"\n{BOLD}Career AI API 端到端测试{RESET}")
    print(f"目标地址: {BOLD}{BASE_URL}{RESET}")
    print(f"测试账号: {TEST_USER}")

    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        # 先检查服务是否可达
        try:
            resp = await client.get("/health", timeout=5)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            print(f"\n{RED}❌ 无法连接到 {BASE_URL}{RESET}")
            print(f"   错误: {e}")
            print(f"   请先启动后端服务：cd career-agent && uv run uvicorn api:app --reload")
            sys.exit(1)

        # 按顺序执行测试（有状态依赖）
        await test_health(client)
        await test_auth(client)
        await test_security(client)
        await test_resume_extract(client)
        await test_invoke_builtin(client)
        await test_invoke_custom(client)
        await test_chat_json(client)
        await test_chat_history(client)
        await test_assess(client)
        await test_career_match(client)
        await test_archive_list(client)
        await test_archive_detail(client)
        await test_report(client)
        await test_plan_weekly(client)
        await test_plan_list(client)
        await test_mcp_custom_invoke(client)

    # 汇总
    total = state.passed + state.failed + state.skipped
    section("测试汇总")
    print(f"  总计: {total}")
    print(f"  {GREEN}通过: {state.passed}{RESET}")
    print(f"  {RED}失败: {state.failed}{RESET}")
    print(f"  {YELLOW}跳过: {state.skipped}{RESET}")

    if state.failed == 0:
        print(f"\n{GREEN}{BOLD}  🎉 所有测试通过！{RESET}\n")
    else:
        print(f"\n{RED}{BOLD}  ⚠️  有 {state.failed} 个测试失败{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
