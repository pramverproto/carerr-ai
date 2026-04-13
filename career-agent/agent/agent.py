import json
import time
import uuid
from typing import AsyncGenerator
import agent.tools.delegate  # 触发 @tool 装饰器，将 delegate_task 注册到 TOOL_REGISTRY
import agent.tools.assessment  # 触发 @tool 装饰器，将 start_assessment 注册到 TOOL_REGISTRY
from agent.tools.registry import TOOL_SCHEMAS, TOOL_REGISTRY, call_tool
from agent.tools.mcp import MCPClient
from agent.providers.llm import LLMProvider
from agent.logger import get_logger
import agent.memory.db as memory_db

logger = get_logger("agent")


def _short_id() -> str:
    return uuid.uuid4().hex[:16]


class Agent:
    def __init__(
        self,
        llm: LLMProvider,
        system_prompt: str,
        mcp: MCPClient | None = None,
        allowed_tools: list[str] | None = None,
        session_id: str | None = None,
        parent_trace_id: str | None = None,
    ):
        self.llm = llm
        self.mcp = mcp
        # allowed_tools 为 None 表示不限制，使用所有已注册工具。
        # 传入名称列表则只允许使用指定工具，用于限制 sub_agent 的权限。
        self.allowed_tools = allowed_tools
        # session_id 由调用方（前端）传入，用于关联 MySQL 中的历史消息。
        # 为 None 时不启用持久化（如 sub_agent 的临时执行）。
        self.session_id = session_id
        # parent_trace_id 由上层 agent 传入，实现调用链关联
        self.parent_trace_id = parent_trace_id
        self._system_prompt = system_prompt
        # system prompt 作为第一条消息，贯穿整个对话；历史消息在 _load_history() 中加载
        self.messages = [{"role": "system", "content": system_prompt}]

    # ------------------------------------------------------------------ #
    #  Memory helpers                                                       #
    # ------------------------------------------------------------------ #

    async def _load_history(self) -> None:
        """
        从数据库加载历史消息，替换当前 self.messages。
        仅在 session_id 不为 None 且数据库中存在历史时执行。
        如果历史为空，保留默认的 [system] 消息。
        """
        if not self.session_id:
            return
        history = await memory_db.load_messages(self.session_id)
        if history:
            self.messages = history
        else:
            # 首次对话，将 system prompt 写入数据库
            await memory_db.save_message(self.session_id, "system", self._system_prompt)

    async def _persist(self, role: str, content, tool_call_id: str | None = None) -> None:
        """持久化一条消息，仅在 session_id 存在时执行。"""
        if self.session_id:
            await memory_db.save_message(self.session_id, role, content, tool_call_id)

    # ------------------------------------------------------------------ #
    #  Trace helpers                                                        #
    # ------------------------------------------------------------------ #

    async def _trace(
        self,
        trace_id: str,
        span_type: str,
        **kwargs,
    ) -> None:
        """写入一条 trace span，静默失败不影响主流程。"""
        try:
            await memory_db.save_span(
                trace_id,
                span_type,
                parent_trace_id=self.parent_trace_id,
                session_id=self.session_id,
                **kwargs,
            )
        except Exception as e:
            logger.debug(f"[Trace] 写入失败（已忽略）：{e}")

    # ------------------------------------------------------------------ #
    #  Tools setup                                                          #
    # ------------------------------------------------------------------ #

    async def _setup_tools(self) -> list:
        # 连接 MCP server 并拉取远程工具列表，与本地 TOOL_SCHEMAS 合并。
        # 如果设置了 allowed_tools，则按名称过滤，只保留允许的工具。
        mcp_tools = []
        if self.mcp:
            await self.mcp.connect()
            mcp_tools = await self.mcp.list_tools()
            logger.debug(f"MCP 工具加载完成：{[t['function']['name'] for t in mcp_tools]}")

        all_tools = TOOL_SCHEMAS + mcp_tools

        if self.allowed_tools is not None:
            all_tools = [t for t in all_tools if t["function"]["name"] in self.allowed_tools]

        logger.debug(f"最终可用工具列表：{[t['function']['name'] for t in all_tools]}")
        return all_tools

    # ------------------------------------------------------------------ #
    #  Tool execution                                                       #
    # ------------------------------------------------------------------ #

    async def _execute_tool_calls(self, response, all_tools: list, trace_id: str):
        # 处理一轮 tool_calls：遍历 LLM 请求的所有工具，分别执行并写回 messages。
        # 执行完毕后重新请求 LLM，返回新的 (response, elapsed_ms, usage) 供外层循环继续判断。
        assistant_msg = response.choices[0].message
        self.messages.append(assistant_msg)
        # 持久化 assistant 消息（含 tool_calls，序列化为 JSON 字符串）
        await self._persist("assistant", assistant_msg)

        for tool_call in response.choices[0].message.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            logger.info(f"[工具调用] {name}  参数：{args}")
            t0 = time.perf_counter()

            # 本地工具（含 delegate_task）：走 TOOL_REGISTRY，支持 async
            # delegate_task 额外注入 _parent_trace_id，实现多级 agent 调用链关联
            # MCP 工具：直接调用远程 MCP server
            if name in TOOL_REGISTRY:
                if name == "delegate_task":
                    args["_parent_trace_id"] = trace_id
                result = await call_tool(name, args)
            else:
                result = await self.mcp.call_tool(name, args)

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(f"[工具结果] {name}  耗时：{elapsed_ms}ms  结果：{result}")

            # trace: tool_call span
            await self._trace(
                trace_id, "tool_call",
                name=name,
                input=args,
                output=str(result),
                elapsed_ms=elapsed_ms,
            )

            # 工具结果以 role=tool 写回 messages，tool_call_id 用于与请求对应
            self.messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result),
            })
            await self._persist("tool", str(result), tool_call_id=tool_call.id)

        return self.llm.chat(self.messages, tools=all_tools)

    # ------------------------------------------------------------------ #
    #  run_once — sub_agent entry point                                     #
    # ------------------------------------------------------------------ #

    async def run_once(self, task: str) -> str:
        # 执行单轮任务，不等待用户输入，完成后返回结果字符串。
        # 供 delegate_task 调用，作为 sub_agent 的入口。
        trace_id = _short_id()
        logger.debug(f"[sub_agent] 开始任务：{task}  trace_id={trace_id}")
        await self._load_history()
        all_tools = await self._setup_tools()
        self.messages.append({"role": "user", "content": task})
        await self._persist("user", task)

        t_run = time.perf_counter()

        # trace: run_start
        await self._trace(trace_id, "run_start", name="run_once", input={"task": task})

        response, elapsed_ms, usage = self.llm.chat(self.messages, tools=all_tools)

        # trace: first llm_call
        await self._trace(
            trace_id, "llm_call",
            name=self.llm.model,
            input={"messages_count": len(self.messages)},
            output={"finish_reason": response.choices[0].finish_reason},
            elapsed_ms=elapsed_ms,
            **usage,
        )

        try:
            while True:
                finish_reason = response.choices[0].finish_reason

                if finish_reason == "stop":
                    result = response.choices[0].message.content
                    total_elapsed_ms = int((time.perf_counter() - t_run) * 1000)
                    logger.debug(f"[sub_agent] 任务完成  耗时：{total_elapsed_ms}ms")
                    await self._persist("assistant", result)
                    # trace: run_end
                    await self._trace(
                        trace_id, "run_end",
                        name="run_once",
                        output={"status": "done"},
                        elapsed_ms=total_elapsed_ms,
                    )
                    return result

                elif finish_reason == "tool_calls":
                    response, elapsed_ms, usage = await self._execute_tool_calls(response, all_tools, trace_id)
                    # trace: subsequent llm_call
                    await self._trace(
                        trace_id, "llm_call",
                        name=self.llm.model,
                        input={"messages_count": len(self.messages)},
                        output={"finish_reason": response.choices[0].finish_reason},
                        elapsed_ms=elapsed_ms,
                        **usage,
                    )

                else:
                    logger.warning(f"[sub_agent] 未预期的 finish_reason：{finish_reason}")
                    await self._trace(trace_id, "run_end", name="run_once",
                                      output={"status": "unexpected_finish_reason"})
                    return "sub_agent 返回未预期的 finish_reason"
        finally:
            if self.mcp:
                await self.mcp.disconnect()

    # ------------------------------------------------------------------ #
    #  stream_once — streaming HTTP entry point                             #
    # ------------------------------------------------------------------ #

    async def stream_once(self, task: str) -> AsyncGenerator[dict, None]:
        """
        流式执行单轮任务，逐块 yield dict，供 HTTP SSE 接口消费。
        yield 格式：
          {"type": "text",  "content": "..."}                       # 文本 token
          {"type": "tool",  "name": "...", "status": "calling"}     # 工具调用通知
          {"type": "done",  "usage": {...}, "elapsed_ms": ...}      # 结束
        工具调用完整执行后，再继续流式输出 LLM 回答。
        """
        trace_id = _short_id()
        logger.debug(f"[stream_once] 开始任务：{task}  trace_id={trace_id}")
        await self._load_history()
        all_tools = await self._setup_tools()
        self.messages.append({"role": "user", "content": task})
        await self._persist("user", task)

        t_run = time.perf_counter()
        await self._trace(trace_id, "run_start", name="stream_once", input={"task": task})

        try:
            while True:
                stream = self.llm.chat_stream(self.messages, tools=all_tools)

                # 收集流式输出：累积文本 + 收集 tool_calls
                full_text = ""
                tool_calls_buf: dict[int, dict] = {}  # index -> {id, name, arguments}
                finish_reason = None
                t0 = time.perf_counter()

                for chunk in stream:
                    choice = chunk.choices[0]
                    finish_reason = choice.finish_reason
                    delta = choice.delta

                    # 文本 token
                    if delta.content:
                        full_text += delta.content
                        yield {"type": "text", "content": delta.content}

                    # 工具调用增量（name/arguments 分多个 chunk 到达）
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_buf:
                                tool_calls_buf[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc.id:
                                tool_calls_buf[idx]["id"] = tc.id
                            if tc.function.name:
                                tool_calls_buf[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_buf[idx]["arguments"] += tc.function.arguments

                elapsed_ms = int((time.perf_counter() - t0) * 1000)

                if finish_reason == "stop":
                    # 持久化 assistant 文本消息，写 trace
                    self.messages.append({"role": "assistant", "content": full_text})
                    await self._persist("assistant", full_text)
                    total_elapsed_ms = int((time.perf_counter() - t_run) * 1000)
                    await self._trace(trace_id, "run_end", name="stream_once",
                                      output={"status": "done"}, elapsed_ms=total_elapsed_ms)
                    yield {"type": "done", "elapsed_ms": total_elapsed_ms}
                    return

                elif finish_reason == "tool_calls":
                    # 构造 assistant message（含 tool_calls），写入 messages
                    tool_calls_list = [
                        {
                            "id": v["id"],
                            "type": "function",
                            "function": {"name": v["name"], "arguments": v["arguments"]},
                        }
                        for v in tool_calls_buf.values()
                    ]
                    assistant_msg = {
                        "role": "assistant",
                        "content": full_text or None,
                        "tool_calls": tool_calls_list,
                    }
                    self.messages.append(assistant_msg)
                    await self._persist("assistant", assistant_msg)

                    # 逐个执行工具
                    for tc in tool_calls_list:
                        name = tc["function"]["name"]
                        args = json.loads(tc["function"]["arguments"])
                        yield {"type": "tool", "name": name, "status": "calling"}

                        logger.info(f"[stream工具调用] {name}  参数：{args}")
                        t_tool = time.perf_counter()
                        if name in TOOL_REGISTRY:
                            if name == "delegate_task":
                                args["_parent_trace_id"] = trace_id
                            result = await call_tool(name, args)
                        else:
                            result = await self.mcp.call_tool(name, args)
                        tool_elapsed_ms = int((time.perf_counter() - t_tool) * 1000)

                        logger.info(f"[stream工具结果] {name}  耗时：{tool_elapsed_ms}ms")
                        await self._trace(trace_id, "tool_call", name=name,
                                          input=args, output=str(result), elapsed_ms=tool_elapsed_ms)

                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": str(result),
                        })
                        await self._persist("tool", str(result), tool_call_id=tc["id"])

                    # 继续下一轮流式调用
                    continue

                else:
                    logger.warning(f"[stream_once] 未预期的 finish_reason：{finish_reason}")
                    await self._trace(trace_id, "run_end", name="stream_once",
                                      output={"status": "unexpected_finish_reason"})
                    yield {"type": "done", "elapsed_ms": int((time.perf_counter() - t_run) * 1000)}
                    return

        finally:
            if self.mcp:
                await self.mcp.disconnect()

    # ------------------------------------------------------------------ #
    #  run — interactive main loop                                          #
    # ------------------------------------------------------------------ #

    async def run(self):
        # 交互式主循环，等待用户输入，持续对话直到用户退出。
        # 作为 main agent 的入口。
        logger.info("Agent 启动，输入 exit 或 quit 退出。")
        await self._load_history()
        all_tools = await self._setup_tools()

        try:
            while True:
                user_input = input("你：")
                if user_input.lower() in ["exit", "quit"]:
                    logger.info("用户退出对话。")
                    break

                trace_id = _short_id()
                logger.debug(f"[用户输入] {user_input}  trace_id={trace_id}")
                self.messages.append({"role": "user", "content": user_input})
                await self._persist("user", user_input)

                t_run = time.perf_counter()

                # trace: run_start
                await self._trace(trace_id, "run_start", name="run", input={"user_input": user_input})

                response, elapsed_ms, usage = self.llm.chat(self.messages, tools=all_tools)

                # trace: first llm_call
                await self._trace(
                    trace_id, "llm_call",
                    name=self.llm.model,
                    input={"messages_count": len(self.messages)},
                    output={"finish_reason": response.choices[0].finish_reason},
                    elapsed_ms=elapsed_ms,
                    **usage,
                )

                # 内层循环：LLM 可能连续调用多轮工具，直到 finish_reason == "stop" 才输出最终回答
                while True:
                    finish_reason = response.choices[0].finish_reason

                    if finish_reason == "stop":
                        reply = response.choices[0].message.content
                        total_elapsed_ms = int((time.perf_counter() - t_run) * 1000)
                        logger.debug(f"[LLM 响应] 耗时：{total_elapsed_ms}ms")
                        print("AI：", reply)
                        self.messages.append({"role": "assistant", "content": reply})
                        await self._persist("assistant", reply)
                        # trace: run_end
                        await self._trace(
                            trace_id, "run_end",
                            name="run",
                            output={"status": "done"},
                            elapsed_ms=total_elapsed_ms,
                        )
                        break

                    elif finish_reason == "tool_calls":
                        response, elapsed_ms, usage = await self._execute_tool_calls(response, all_tools, trace_id)
                        # trace: subsequent llm_call
                        await self._trace(
                            trace_id, "llm_call",
                            name=self.llm.model,
                            input={"messages_count": len(self.messages)},
                            output={"finish_reason": response.choices[0].finish_reason},
                            elapsed_ms=elapsed_ms,
                            **usage,
                        )

                    else:
                        logger.warning(f"未预期的 finish_reason：{finish_reason}")
                        await self._trace(trace_id, "run_end", name="run",
                                          output={"status": "unexpected_finish_reason"})
                        break

        finally:
            # 无论正常退出还是异常，都确保 MCP 连接被关闭
            if self.mcp:
                await self.mcp.disconnect()
