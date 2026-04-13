import asyncio
import sys
sys.path.append(".")
from agent.tools.mcp import MCPClient

SERVER_URL = "http://115.190.165.29:5235/yoolee/huita-Search/sse"

async def test():
    client = MCPClient(SERVER_URL)
    await client.connect()

    # 测试 list_tools
    tools = await client.list_tools()
    print("工具列表：")
    for t in tools:
        print(f"  - {t['function']['name']}: {t['function']['description']}")

    # 测试 call_tool
    result = await client.call_tool("knowledge_retrieval", {
        "text": "还款方式有哪些",
        "datasetId": "698059f5fc770f6d08650567"
    })
    print("\n调用结果：", result)

    await client.disconnect()

asyncio.run(test())
