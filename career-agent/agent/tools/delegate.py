from agent.tools.registry import tool
from agent.agent_config import SUB_AGENT_CONFIGS, MAIN_AGENT_CONFIG


@tool(
    description=(
        "将子任务委派给指定的 sub_agent 执行，返回执行结果。"
        f"可用的 agent_name：{list(SUB_AGENT_CONFIGS.keys())}"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "交给 sub_agent 的任务描述，越具体越好"
            },
            "agent_name": {
                "type": "string",
                "description": f"指定使用哪个 sub_agent，可选值：{list(SUB_AGENT_CONFIGS.keys())}",
                "enum": list(SUB_AGENT_CONFIGS.keys()),
            },
        },
        "required": ["task", "agent_name"]
    }
)
async def delegate_task(task: str, agent_name: str, _parent_trace_id: str | None = None) -> str:
    from agent.agent import Agent
    from agent.providers.llm import LLMProvider
    from agent.tools.mcp import MCPClient

    config = SUB_AGENT_CONFIGS.get(agent_name)
    if config is None:
        return f"未知的 agent_name：{agent_name}，可选值：{list(SUB_AGENT_CONFIGS.keys())}"

    # model 为 None 时沿用 MAIN_AGENT_CONFIG 里的模型
    model = config["model"] or MAIN_AGENT_CONFIG["model"]
    llm = LLMProvider(
        model=model,
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )

    # mcp_url 有配置则连接，否则不带 MCP
    mcp_url = config.get("mcp_url") or MAIN_AGENT_CONFIG.get("mcp_url")
    mcp = MCPClient(mcp_url) if mcp_url else None

    sub_agent = Agent(
        llm=llm,
        mcp=mcp,
        system_prompt=config["system_prompt"],
        allowed_tools=config["allowed_tools"],
        parent_trace_id=_parent_trace_id,  # 传递调用链 trace_id
    )
    return await sub_agent.run_once(task)
