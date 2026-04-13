import time
from mcp.client.sse import sse_client
from mcp import ClientSession
from agent.logger import get_logger

logger = get_logger("agent.mcp")


class MCPClient:
    def __init__(self, server_url: str):
        self.server_url = server_url
        self._session = None
        self._sse_cm = None
        self._session_cm = None

    async def connect(self):
        # 通过 SSE（Server-Sent Events）协议与 MCP server 建立持久连接。
        # sse_client 返回一个异步上下文管理器，手动调用 __aenter__ 拿到
        # read/write 两个流，再用它们创建 ClientSession。
        # 最后调用 initialize() 完成 MCP 握手，之后才能正常收发消息。
        logger.debug(f"[MCP] 连接中：{self.server_url}")
        self._sse_cm = sse_client(self.server_url)
        read, write = await self._sse_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        logger.debug("[MCP] 连接成功")

    async def disconnect(self):
        # 按照先 session 后连接的顺序关闭，避免资源泄漏。
        if self._session_cm:
            await self._session_cm.__aexit__(None, None, None)
        if self._sse_cm:
            await self._sse_cm.__aexit__(None, None, None)
        logger.debug("[MCP] 已断开连接")

    async def list_tools(self) -> list[dict]:
        # 向 MCP server 查询当前可用的工具列表，并将格式转换为
        # OpenAI function calling 规范，这样可以直接传给 litellm/OpenAI 的
        # tools 参数，让 LLM 知道有哪些工具可以调用。
        result = await self._session.list_tools()
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.inputSchema,
                }
            }
            for t in result.tools
        ]

    async def call_tool(self, tool_name: str, tool_args: dict) -> str:
        # 通知 MCP server 执行指定工具，等待结果返回。
        logger.debug(f"[MCP 调用] {tool_name}  参数：{tool_args}")
        t0 = time.perf_counter()
        result = await self._session.call_tool(tool_name, tool_args)
        elapsed = time.perf_counter() - t0
        text = result.content[0].text
        logger.debug(f"[MCP 结果] {tool_name}  耗时：{elapsed:.2f}s  结果：{text}")
        return text
