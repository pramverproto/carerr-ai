"""
测试 memory 模块：MySQL 多轮对话持久化
运行前确保数据库可访问：115.120.251.185:3306 career_agent
"""
import sys
import asyncio
sys.path.append(".")

from agent.agent_config import DB_CONFIG
import agent.memory.db as db

TEST_SESSION = "test_memory_session_001"


async def test():
    await db.init_pool(**DB_CONFIG)

    # 清理上一次的测试数据，避免干扰
    async with db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM messages WHERE session_id = %s", (TEST_SESSION,))

    # 测试 1：空会话加载历史返回空列表
    history = await db.load_messages(TEST_SESSION)
    assert history == [], f"期望空列表，实际：{history}"
    print("✓ 空会话返回空列表")

    # 测试 2：保存并加载 system / user / assistant 消息
    await db.save_message(TEST_SESSION, "system", "你是一个助手。")
    await db.save_message(TEST_SESSION, "user", "你好")
    await db.save_message(TEST_SESSION, "assistant", "你好！有什么可以帮你？")

    history = await db.load_messages(TEST_SESSION)
    assert len(history) == 3
    assert history[0]["role"] == "system"
    assert history[1]["role"] == "user"
    assert history[2]["role"] == "assistant"
    assert history[2]["content"] == "你好！有什么可以帮你？"
    print("✓ system / user / assistant 消息保存和加载正确")

    # 测试 3：保存 tool 消息（含 tool_call_id）
    await db.save_message(TEST_SESSION, "tool", "北京晴，25度", tool_call_id="call_abc123")
    history = await db.load_messages(TEST_SESSION)
    tool_msg = next(m for m in history if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_abc123"
    assert tool_msg["content"] == "北京晴，25度"
    print("✓ tool 消息含 tool_call_id 保存和加载正确")

    # 测试 4：assistant 消息含 tool_calls 时，序列化存储后能正确反序列化
    import json
    mock_assistant_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_xyz", "type": "function",
                        "function": {"name": "get_weather", "arguments": "{}"}}]
    }
    await db.save_message(TEST_SESSION, "assistant", mock_assistant_msg)
    history = await db.load_messages(TEST_SESSION)
    last = history[-1]
    # 反序列化后应是 dict 且含 tool_calls
    assert isinstance(last, dict)
    assert "tool_calls" in last
    print("✓ assistant with tool_calls 序列化/反序列化正确")

    # 清理测试数据
    async with db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM messages WHERE session_id = %s", (TEST_SESSION,))

    await db.close_pool()
    print("\n全部测试通过")


asyncio.run(test())
