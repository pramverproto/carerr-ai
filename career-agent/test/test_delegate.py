import sys
import asyncio
sys.path.append(".")

import agent.tools.delegate  # 触发注册
from agent.tools.registry import TOOL_REGISTRY, TOOL_SCHEMAS, call_tool


async def test():
    # 测试 1：模块加载后 delegate_task 自动注册到 TOOL_REGISTRY
    assert "delegate_task" in TOOL_REGISTRY, "delegate_task 未注册到 TOOL_REGISTRY"
    print("✓ delegate_task 注册成功")

    # 测试 2：TOOL_SCHEMAS 里有 delegate_task 的 schema，且包含 agent_name 参数
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert "delegate_task" in names, "delegate_task schema 未写入 TOOL_SCHEMAS"
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "delegate_task")
    assert "agent_name" in schema["function"]["parameters"]["properties"], "schema 缺少 agent_name 参数"
    assert "task" in schema["function"]["parameters"]["properties"], "schema 缺少 task 参数"
    print("✓ delegate_task schema 结构正确")

    # 测试 3：传入未知 agent_name 返回错误提示
    result = await call_tool("delegate_task", {"task": "测试任务", "agent_name": "unknown_agent"})
    assert "未知的 agent_name" in result, f"期望错误提示，实际得到：{result}"
    print("✓ 未知 agent_name 处理正确")

    # 测试 4：传入合法 agent_name，sub_agent 能正常执行并返回结果（需要真实 API）
    result = await call_tool("delegate_task", {"task": "你好，回复'子任务完成'四个字", "agent_name": "assistant"})
    assert isinstance(result, str) and len(result) > 0, f"sub_agent 返回结果异常：{result}"
    print(f"✓ sub_agent 执行成功，返回：{result}")

    print("\n全部测试通过")


asyncio.run(test())
