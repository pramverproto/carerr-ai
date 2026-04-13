import sys
import asyncio
sys.path.append(".")

from agent.tools.registry import tool, call_tool, TOOL_REGISTRY, TOOL_SCHEMAS

# 注册一个仅用于测试的工具
@tool(
    description="测试加法",
    parameters={
        "type": "object",
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "integer"},
        },
        "required": ["a", "b"]
    }
)
def add(a: int, b: int) -> int:
    return a + b


async def test():
    # 测试 1：工具函数是否注册到 TOOL_REGISTRY
    assert "add" in TOOL_REGISTRY, "add 未注册到 TOOL_REGISTRY"
    print("✓ 工具注册成功")

    # 测试 2：call_tool 能正确调用并返回字符串结果
    result = await call_tool("add", {"a": 1, "b": 2})
    assert result == "3", f"期望 '3'，实际得到 '{result}'"
    print("✓ call_tool 调用正确")

    # 测试 3：调用未知工具时返回错误提示
    result = await call_tool("unknown_tool", {})
    assert "未知工具" in result, f"期望错误提示，实际得到 '{result}'"
    print("✓ 未知工具处理正确")

    # 测试 4：TOOL_SCHEMAS 中包含对应的 schema
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert "add" in names, "add 的 schema 未写入 TOOL_SCHEMAS"
    print("✓ TOOL_SCHEMAS 写入正确")

    # 测试 5：schema 结构符合 OpenAI function calling 规范
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "add")
    assert schema["type"] == "function"
    assert "description" in schema["function"]
    assert "parameters" in schema["function"]
    print("✓ schema 结构正确")

    print("\n全部测试通过")


asyncio.run(test())
