import sys
import asyncio
from agent.providers.llm import LLMProvider
from agent.tools.mcp import MCPClient
from agent.agent import Agent
from agent.agent_config import MAIN_AGENT_CONFIG, DB_CONFIG
import agent.memory.db as memory_db


async def main(session_id: str | None = None):
    # 启动时初始化 MySQL 连接池，自动建表
    await memory_db.init_pool(**DB_CONFIG)

    llm = LLMProvider(
        model=MAIN_AGENT_CONFIG["model"],
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    mcp = MCPClient(MAIN_AGENT_CONFIG["mcp_url"])

    agent = Agent(
        llm=llm,
        system_prompt=MAIN_AGENT_CONFIG["system_prompt"],
        mcp=mcp,
        session_id=session_id,  # None 时不持久化，传入则加载/保存历史
    )

    try:
        await agent.run()
    finally:
        await memory_db.close_pool()


# session_id 从命令行传入，例如：python main.py my-session-001
# 不传则每次启动新对话，不持久化
if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(sid))
