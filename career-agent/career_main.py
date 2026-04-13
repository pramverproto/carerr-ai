"""
职业选择 Agent 入口。

用法：
    uv run python career_main.py <assessment_id>

示例：
    uv run python career_main.py abc123def456
"""

import sys
import asyncio
from agent.providers.llm import LLMProvider
from agent.agent import Agent
from agent.agent_config import MAIN_AGENT_CONFIG, CAREER_AGENT_CONFIG, DB_CONFIG
import agent.memory.db as memory_db
import agent.tools.career  # 注册 match_careers 工具


async def main(assessment_id: str):
    await memory_db.init_pool(**DB_CONFIG)

    llm = LLMProvider(
        model=MAIN_AGENT_CONFIG["model"],
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )

    agent = Agent(
        llm=llm,
        system_prompt=CAREER_AGENT_CONFIG["system_prompt"],
        allowed_tools=CAREER_AGENT_CONFIG["allowed_tools"],
    )

    try:
        result = await agent.run_once(
            f"请为评估 ID 为 {assessment_id} 的候选人推荐匹配职业。"
        )
        print(result)
    finally:
        await memory_db.close_pool()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：uv run python career_main.py <assessment_id>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
