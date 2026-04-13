import sys
import asyncio
sys.path.append(".")

from agent.providers.llm import LLMProvider
from agent.agent import Agent

LLM = LLMProvider(
    model="gpt-3.5-turbo",
    api_key="sk-DBBhVjVOKG45XDIqTa9Ew8whk0w2KGEa4bslvLg1FR7JN2XM",
    base_url="https://www.dmxapi.cn/v1",
)


async def test():
    # 测试 1：run_once 能完成纯文本任务（无工具调用）
    agent = Agent(llm=LLM, system_prompt="你是一个助手，回答要简洁。")
    result = await agent.run_once("你好，回复'测试成功'四个字")
    assert isinstance(result, str) and len(result) > 0
    print(f"✓ run_once 纯文本任务：{result}")

    # 测试 2：run_once 能调用本地工具并返回结果
    agent2 = Agent(
        llm=LLM,
        system_prompt="你是一个助手。",
        allowed_tools=["get_current_weather"],
    )
    result2 = await agent2.run_once("北京今天天气怎么样？")
    assert isinstance(result2, str) and len(result2) > 0
    print(f"✓ run_once 本地工具调用：{result2}")

    # 测试 3：allowed_tools 过滤生效，agent 看不到被过滤的工具
    agent3 = Agent(
        llm=LLM,
        system_prompt="你是一个助手。",
        allowed_tools=["get_current_time"],  # 只允许 get_current_time
    )
    # _setup_tools 返回的工具列表应只包含 get_current_time
    tools = await agent3._setup_tools()
    tool_names = [t["function"]["name"] for t in tools]
    assert "get_current_time" in tool_names
    assert "get_current_weather" not in tool_names
    print(f"✓ allowed_tools 过滤正确，工具列表：{tool_names}")

    print("\n全部测试通过")


asyncio.run(test())
