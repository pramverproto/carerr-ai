"""
测试 trace 模块：调用链记录 + token 消耗持久化
运行前确保数据库可访问：115.120.251.185:3306 career_agent
"""
import sys
import asyncio
sys.path.append(".")

from agent.agent_config import DB_CONFIG, MAIN_AGENT_CONFIG
import agent.memory.db as db

TEST_TRACE_ID = "test_trace_abc123"
TEST_SESSION = "test_trace_session_001"


async def test():
    await db.init_pool(**DB_CONFIG)

    # 清理上一次的测试数据
    async with db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM traces WHERE trace_id = %s", (TEST_TRACE_ID,))

    # 测试 1：写入 run_start span
    await db.save_span(
        TEST_TRACE_ID, "run_start",
        session_id=TEST_SESSION,
        name="run",
        input={"user_input": "你好"},
    )
    print("✓ run_start span 写入成功")

    # 测试 2：写入 llm_call span（含 token 消耗）
    await db.save_span(
        TEST_TRACE_ID, "llm_call",
        session_id=TEST_SESSION,
        name="gpt-3.5-turbo",
        input={"messages_count": 2},
        output={"finish_reason": "stop"},
        elapsed_ms=850,
        prompt_tokens=120,
        completion_tokens=45,
        total_tokens=165,
    )
    print("✓ llm_call span（含 token）写入成功")

    # 测试 3：写入 tool_call span
    await db.save_span(
        TEST_TRACE_ID, "tool_call",
        session_id=TEST_SESSION,
        name="get_current_weather",
        input={"city": "北京"},
        output="北京晴，25度",
        elapsed_ms=120,
    )
    print("✓ tool_call span 写入成功")

    # 测试 4：写入 run_end span
    await db.save_span(
        TEST_TRACE_ID, "run_end",
        session_id=TEST_SESSION,
        name="run",
        output={"status": "done"},
        elapsed_ms=1200,
    )
    print("✓ run_end span 写入成功")

    # 测试 5：从数据库读回，验证字段和顺序
    async with db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT span_type, name, elapsed_ms, prompt_tokens, completion_tokens, total_tokens "
                "FROM traces WHERE trace_id = %s ORDER BY id ASC",
                (TEST_TRACE_ID,)
            )
            rows = await cur.fetchall()

    assert len(rows) == 4, f"期望 4 条，实际 {len(rows)} 条"
    assert rows[0][0] == "run_start"
    assert rows[1][0] == "llm_call"
    assert rows[1][3] == 120   # prompt_tokens
    assert rows[1][4] == 45    # completion_tokens
    assert rows[1][5] == 165   # total_tokens
    assert rows[2][0] == "tool_call"
    assert rows[3][0] == "run_end"
    print("✓ span 顺序和 token 字段验证正确")

    # 测试 6：写入子 agent span，验证 parent_trace_id 关联
    sub_trace_id = "test_sub_trace_xyz"
    async with db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM traces WHERE trace_id = %s", (sub_trace_id,))

    await db.save_span(
        sub_trace_id, "run_start",
        parent_trace_id=TEST_TRACE_ID,
        session_id=TEST_SESSION,
        name="run_once",
        input={"task": "子任务"},
    )
    async with db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT parent_trace_id FROM traces WHERE trace_id = %s",
                (sub_trace_id,)
            )
            row = await cur.fetchone()
    assert row[0] == TEST_TRACE_ID, f"parent_trace_id 不匹配：{row[0]}"
    print("✓ parent_trace_id 关联正确（多级 agent 链路）")

    # 测试 7：run_once 端到端 trace（真实 LLM 调用）
    from agent.providers.llm import LLMProvider
    from agent.agent import Agent

    llm = LLMProvider(
        model=MAIN_AGENT_CONFIG["model"],
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    agent = Agent(llm=llm, system_prompt="你是一个助手，回答要简洁。", session_id=None)
    result = await agent.run_once("你好，回复'测试成功'四个字")
    assert isinstance(result, str) and len(result) > 0
    print(f"✓ run_once 端到端 trace 完成，结果：{result}")

    # 清理测试数据
    async with db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM traces WHERE trace_id IN (%s, %s)",
                              (TEST_TRACE_ID, sub_trace_id))

    await db.close_pool()
    print("\n全部测试通过")


asyncio.run(test())
