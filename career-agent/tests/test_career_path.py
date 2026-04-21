"""
职业路线（Career Path）相关接口测试脚本。
覆盖新增的接口：/career/match (路线格式)、/career/save-path、
/career/stage-complete、/career/path-progress。

运行方式（需要后端服务已启动）：
    cd career-agent
    python tests/test_career_path.py

    # 如果要指定目标地址：
    API_BASE_URL=http://115.120.251.185:8000 python tests/test_career_path.py

    # 如果要复用已有的 assessment_id（跳过注册+评估流程）：
    ASSESSMENT_ID=xxx python tests/test_career_path.py
"""

import asyncio
import json
import os
import sys
import time
import uuid

import httpx

# ──────────────────────────────────────────────────────────────────────
#  配置
# ──────────────────────────────────────────────────────────────────────

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = 60
LLM_TIMEOUT = 300

_suffix = uuid.uuid4().hex[:6]
TEST_USER = f"pathtest_{_suffix}"
TEST_PASS = "Test1234!"

# 可以通过环境变量传入已有的 assessment_id 来跳过评估步骤
EXISTING_ASSESSMENT_ID = os.getenv("ASSESSMENT_ID", "")

SAMPLE_RESUME = {
    "candidate": {
        "name": "李路线",
        "age": 26,
        "education": "本科",
        "major": "软件工程",
        "target_role": "AI 工程师",
        "years_of_experience": 3,
    },
    "experiences": [
        {
            "company": "某科技公司",
            "title": "Python 后端开发",
            "duration": "2021-2024",
            "responsibilities": [
                "负责 FastAPI 微服务开发和维护",
                "使用 LangChain 构建 RAG 检索增强生成系统",
                "MySQL + Redis 性能优化",
            ],
        }
    ],
    "skills": ["Python", "FastAPI", "LangChain", "MySQL", "Docker", "Redis"],
    "certifications": [],
}

SAMPLE_SUPPLEMENT = "对 AI Agent 开发有浓厚兴趣，希望从后端开发转向 AI 应用方向。"

# ──────────────────────────────────────────────────────────────────────
#  输出工具
# ──────────────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────────────
#  状态
# ──────────────────────────────────────────────────────────────────────

class State:
    token: str | None = None
    user_id: int | None = None
    assessment_id: str | None = None
    path_code: str | None = None
    path_data: dict | None = None
    passed: int = 0
    failed: int = 0
    skipped: int = 0


state = State()


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {state.token}"} if state.token else {}


def assert_ok(resp: httpx.Response, expected: int, label: str) -> bool:
    if resp.status_code == expected:
        ok(f"{label} → {resp.status_code}")
        state.passed += 1
        return True
    else:
        fail(f"{label} → 期望 {expected}，实际 {resp.status_code}: {resp.text[:300]}")
        state.failed += 1
        return False


# ──────────────────────────────────────────────────────────────────────
#  测试用例
# ──────────────────────────────────────────────────────────────────────

async def setup_auth(client: httpx.AsyncClient) -> None:
    """注册 + 登录获取 token"""
    section("0. 注册 & 登录")
    resp = await client.post("/auth/register", json={
        "username": TEST_USER,
        "password": TEST_PASS,
    }, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        state.token = data.get("token")
        state.user_id = data.get("user_id")
        ok(f"注册成功: user_id={state.user_id}")
        state.passed += 1
    elif resp.status_code == 409:
        # 已存在，尝试登录
        resp = await client.post("/auth/login", json={
            "username": TEST_USER,
            "password": TEST_PASS,
        }, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            state.token = data.get("token")
            state.user_id = data.get("user_id")
            ok(f"登录成功: user_id={state.user_id}")
            state.passed += 1
        else:
            fail(f"登录失败: {resp.status_code}")
            state.failed += 1
    else:
        fail(f"注册失败: {resp.status_code}: {resp.text[:200]}")
        state.failed += 1


async def setup_assessment(client: httpx.AsyncClient) -> None:
    """获取或创建 assessment_id"""
    section("1. 获取 assessment_id")

    if EXISTING_ASSESSMENT_ID:
        state.assessment_id = EXISTING_ASSESSMENT_ID
        ok(f"使用环境变量中的 assessment_id: {state.assessment_id}")
        state.passed += 1
        return

    if not state.token:
        skip("未登录，跳过评估")
        state.skipped += 1
        return

    print(f"  {YELLOW}⏳ 发起能力评估，约需 1-3 分钟...{RESET}")
    t0 = time.time()
    resp = await client.post(
        "/assess",
        headers=auth_headers(),
        json={
            "session_id": str(uuid.uuid4()),
            "resume": SAMPLE_RESUME,
            "supplement": SAMPLE_SUPPLEMENT,
        },
        timeout=300,
    )
    elapsed = time.time() - t0

    if assert_ok(resp, 200, f"POST /assess ({elapsed:.0f}s)"):
        data = resp.json()
        state.assessment_id = data.get("assessment_id")
        ok(f"assessment_id: {state.assessment_id}")


async def test_career_match_path(client: httpx.AsyncClient) -> None:
    """测试 /career/match 返回路线格式"""
    section("2. 职业路线匹配 /career/match")

    if not state.token or not state.assessment_id:
        skip("缺少 token 或 assessment_id")
        state.skipped += 1
        return

    print(f"  {YELLOW}⏳ 发起职业路线匹配 (force=true)...{RESET}")
    t0 = time.time()
    resp = await client.post(
        "/career/match",
        headers=auth_headers(),
        json={"assessment_id": state.assessment_id, "force": True},
        timeout=LLM_TIMEOUT,
    )
    elapsed = time.time() - t0

    if not assert_ok(resp, 200, f"POST /career/match ({elapsed:.0f}s)"):
        return

    data = resp.json()
    result = data.get("result", {})
    recommended = result.get("recommended", [])

    if not isinstance(recommended, list) or len(recommended) == 0:
        fail(f"recommended 应为非空数组，实际: {str(result)[:200]}")
        state.failed += 1
        return

    ok(f"返回 {len(recommended)} 条路线")
    state.passed += 1

    # 验证第一条路线的结构
    path = recommended[0]
    required_fields = ["path_name", "path_code", "overall_score", "stages"]
    for f in required_fields:
        if f in path:
            ok(f"路线字段 '{f}': {str(path[f])[:60]}")
            state.passed += 1
        else:
            fail(f"路线缺少字段 '{f}'，实际 keys: {list(path.keys())}")
            state.failed += 1

    stages = path.get("stages", [])
    if len(stages) >= 2:
        ok(f"路线有 {len(stages)} 个阶段")
        state.passed += 1

        # Stage 1 应有 match_score
        s1 = stages[0]
        if "match_score" in s1 or "match_reason" in s1:
            ok(f"Stage 1 有匹配详情: score={s1.get('match_score')}")
            state.passed += 1
        else:
            skip("Stage 1 无 match_score（LLM 未生成）")
            state.skipped += 1

        # Stage 2+ 应有 transition_from_prev
        s2 = stages[1]
        if "transition_from_prev" in s2:
            ok(f"Stage 2 有过渡描述: {str(s2['transition_from_prev'])[:60]}")
            state.passed += 1
        else:
            skip("Stage 2 无 transition_from_prev")
            state.skipped += 1
    else:
        fail(f"路线阶段数不足 2，实际: {len(stages)}")
        state.failed += 1

    # 保存到 state 供后续测试使用
    state.path_code = path.get("path_code", "")
    state.path_data = path


async def test_career_match_custom_start(client: httpx.AsyncClient) -> None:
    """测试 /career/match 带 custom_start 参数"""
    section("3. 自定义起点匹配 /career/match + custom_start")

    if not state.token or not state.assessment_id:
        skip("缺少 token 或 assessment_id")
        state.skipped += 1
        return

    print(f"  {YELLOW}⏳ 自定义起点匹配 (custom_start=产品经理)...{RESET}")
    resp = await client.post(
        "/career/match",
        headers=auth_headers(),
        json={
            "assessment_id": state.assessment_id,
            "force": True,
            "custom_start": "产品经理",
        },
        timeout=LLM_TIMEOUT,
    )

    if assert_ok(resp, 200, "POST /career/match + custom_start"):
        data = resp.json()
        result = data.get("result", {})
        recommended = result.get("recommended", [])
        if isinstance(recommended, list) and len(recommended) > 0:
            ok(f"自定义起点返回 {len(recommended)} 条路线")
            state.passed += 1
            # 检查路线名是否包含"产品"相关关键词
            names = [r.get("path_name", "") for r in recommended]
            ok(f"路线名称: {', '.join(names[:3])}")
            state.passed += 1
        else:
            skip(f"自定义起点返回空结果: {str(result)[:200]}")
            state.skipped += 1


async def test_save_path(client: httpx.AsyncClient) -> None:
    """测试 /career/save-path"""
    section("4. 保存路线 /career/save-path")

    if not state.token or not state.assessment_id or not state.path_code:
        skip("缺少必要数据")
        state.skipped += 1
        return

    resp = await client.post(
        "/career/save-path",
        headers=auth_headers(),
        json={
            "assessment_id": state.assessment_id,
            "path_code": state.path_code,
            "path_data": json.dumps(state.path_data, ensure_ascii=False),
        },
        timeout=TIMEOUT,
    )
    if assert_ok(resp, 200, "POST /career/save-path"):
        data = resp.json()
        if data.get("ok"):
            ok(f"路线已保存: path_code={data.get('path_code')}")
            state.passed += 1
        else:
            fail(f"save-path 返回非 ok: {data}")
            state.failed += 1


async def test_path_progress(client: httpx.AsyncClient) -> None:
    """测试 /career/path-progress"""
    section("5. 查询路线进度 /career/path-progress")

    if not state.token or not state.assessment_id or not state.path_code:
        skip("缺少必要数据")
        state.skipped += 1
        return

    resp = await client.get(
        f"/career/path-progress/{state.assessment_id}/{state.path_code}",
        headers=auth_headers(),
        timeout=TIMEOUT,
    )
    if assert_ok(resp, 200, "GET /career/path-progress"):
        data = resp.json()
        if "current_stage" in data:
            ok(f"当前阶段: {data['current_stage']}")
            state.passed += 1
        else:
            fail(f"缺少 current_stage 字段: {list(data.keys())}")
            state.failed += 1


async def test_stage_complete(client: httpx.AsyncClient) -> None:
    """测试 /career/stage-complete"""
    section("6. 阶段完成确认 /career/stage-complete")

    if not state.token or not state.assessment_id or not state.path_code:
        skip("缺少必要数据")
        state.skipped += 1
        return

    resp = await client.post(
        "/career/stage-complete",
        headers=auth_headers(),
        json={
            "assessment_id": state.assessment_id,
            "path_code": state.path_code,
            "completed_stage": 1,
            "user_note": "测试完成第一阶段",
        },
        timeout=TIMEOUT,
    )
    if assert_ok(resp, 200, "POST /career/stage-complete"):
        data = resp.json()
        if data.get("ok"):
            ok(f"阶段完成: next_stage={data.get('next_stage')}, total={data.get('total_stages')}")
            state.passed += 1
        else:
            fail(f"stage-complete 返回异常: {data}")
            state.failed += 1

    # 再次查询进度，确认 current_stage 已更新
    resp2 = await client.get(
        f"/career/path-progress/{state.assessment_id}/{state.path_code}",
        headers=auth_headers(),
        timeout=TIMEOUT,
    )
    if resp2.status_code == 200:
        data2 = resp2.json()
        if data2.get("current_stage", 0) >= 2:
            ok(f"进度已更新: current_stage={data2['current_stage']}")
            state.passed += 1
        else:
            fail(f"进度未更新: current_stage={data2.get('current_stage')}")
            state.failed += 1

        # 检查 stage_history
        history = data2.get("stage_history", [])
        if len(history) >= 1 and history[-1].get("stage") == 1:
            ok(f"stage_history 已记录: {len(history)} 条")
            state.passed += 1
        else:
            fail(f"stage_history 异常: {history}")
            state.failed += 1


async def test_career_plan_with_path(client: httpx.AsyncClient) -> None:
    """测试 /career/plan 带 path_data 参数（生成含 Block 5 的规划）"""
    section("7. 职业规划 + 路线数据 /career/plan (含 Block 5)")

    if not state.token or not state.assessment_id or not state.path_data:
        skip("缺少必要数据")
        state.skipped += 1
        return

    stages = state.path_data.get("stages", [])
    if not stages:
        skip("路线无阶段数据")
        state.skipped += 1
        return

    stage1 = stages[0]
    code = f"{state.path_code}-s1"

    print(f"  {YELLOW}⏳ 生成 Stage 1 规划 (含 Block 5)，约需 2-4 分钟...{RESET}")
    t0 = time.time()
    resp = await client.post(
        "/career/plan",
        headers=auth_headers(),
        json={
            "assessment_id": state.assessment_id,
            "onetsoc_code": code,
            "title": stage1.get("title", ""),
            "path_data": json.dumps(state.path_data, ensure_ascii=False),
            "current_stage": 1,
        },
        timeout=LLM_TIMEOUT,
    )
    elapsed = time.time() - t0

    if not assert_ok(resp, 200, f"POST /career/plan ({elapsed:.0f}s)"):
        return

    data = resp.json()
    blocks = data.get("blocks", {})

    # 验证基本 Block 存在
    for block_id in ["match_overview", "jd_recommendations", "gap_analysis"]:
        if block_id in blocks:
            ok(f"Block '{block_id}' 存在")
            state.passed += 1
        else:
            fail(f"Block '{block_id}' 缺失，实际 blocks: {list(blocks.keys())}")
            state.failed += 1

    # 验证 Block 5 (future_outlook)
    if "future_outlook" in blocks:
        fo = blocks["future_outlook"]
        ok(f"Block 5 'future_outlook' 存在")
        state.passed += 1

        if fo.get("current_stage") == 1:
            ok(f"future_outlook.current_stage = 1")
            state.passed += 1
        else:
            fail(f"future_outlook.current_stage 异常: {fo.get('current_stage')}")
            state.failed += 1

        next_stages = fo.get("next_stages", [])
        if len(next_stages) >= 1:
            ok(f"future_outlook 有 {len(next_stages)} 个后续阶段")
            state.passed += 1
            ns = next_stages[0]
            if ns.get("transition_tips"):
                ok(f"后续阶段有 transition_tips: {ns['transition_tips'][:50]}")
                state.passed += 1
        else:
            fail(f"future_outlook.next_stages 为空")
            state.failed += 1

        if fo.get("path_narrative"):
            ok(f"path_narrative: {fo['path_narrative'][:60]}")
            state.passed += 1
    else:
        # Block 5 是条件生成的，如果路线只有 1 个阶段则不生成
        if len(stages) > 1:
            fail(f"Block 5 缺失（路线有 {len(stages)} 个阶段，应当生成）")
            state.failed += 1
        else:
            skip("路线只有 1 个阶段，Block 5 不生成属正常")
            state.skipped += 1


# ──────────────────────────────────────────────────────────────────────
#  主入口
# ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"\n{BOLD}Career Path 接口测试{RESET}")
    print(f"目标地址: {BOLD}{BASE_URL}{RESET}")
    print(f"测试账号: {TEST_USER}")
    if EXISTING_ASSESSMENT_ID:
        print(f"复用 assessment_id: {EXISTING_ASSESSMENT_ID}")

    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        # 检查服务可达
        try:
            await client.get("/health", timeout=5)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            print(f"\n{RED}❌ 无法连接到 {BASE_URL}{RESET}")
            print(f"   错误: {e}")
            sys.exit(1)

        await setup_auth(client)
        await setup_assessment(client)
        await test_career_match_path(client)
        # 只跑快速接口，custom_start 测试比较慢可选跳过
        if os.getenv("SKIP_CUSTOM_START", "") != "1":
            await test_career_match_custom_start(client)
        await test_save_path(client)
        await test_path_progress(client)
        await test_stage_complete(client)

        # Block 5 测试很慢（需要 LLM），可选跳过
        if os.getenv("SKIP_PLAN_TEST", "") != "1":
            await test_career_plan_with_path(client)
        else:
            skip("SKIP_PLAN_TEST=1，跳过 career plan 测试")
            state.skipped += 1

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
