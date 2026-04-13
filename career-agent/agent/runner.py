"""
通用单轮 LLM 调用入口（prompt agent）。

本模块目的：
    把项目中所有"只要提示词不同、不需要 tool-calling 循环"的裸
    ``LLMProvider.chat([system, user])`` 调用统一到一个 chokepoint，
    使得：
      1. 所有大模型请求都经过统一的日志 + trace 写入
      2. 调用方不需要自己组装 messages / LLMProvider / 错误处理
      3. system_prompt 完全由调用方动态传入，不固定在配置里
      4. 默认复用 MAIN_AGENT_CONFIG 的地址 + key；也允许外部传入自定义 LLMProvider
         或覆盖 model/api_key/base_url

典型用法：
    from agent.runner import run_prompt

    text, elapsed_ms, usage = await run_prompt(
        system_prompt="你是…",
        user_message="请生成…",
        agent_name="career_ranking",
        parent_trace_id=parent_trace_id,  # 可选，用于调用链关联
    )

说明：
    ``run_prompt`` 本质是"通用 prompt agent"——没有 sub_agent 的固定配置，
    提示词完全动态化。它是轻量单轮执行，不会进入 tool-calling 循环，
    也不会把对话写入 messages 持久化表（只写 trace spans 供排障/统计）。
"""

import time
import uuid

from agent.providers.llm import LLMProvider
from agent.agent_config import MAIN_AGENT_CONFIG
from agent.logger import get_logger
import agent.memory.db as memory_db

logger = get_logger("agent.runner")


def _short_id() -> str:
    return uuid.uuid4().hex[:16]


async def _safe_trace(
    trace_id: str,
    span_type: str,
    *,
    parent_trace_id: str | None,
    session_id: str | None,
    **kwargs,
) -> None:
    """写 trace，静默失败不影响主流程。"""
    try:
        await memory_db.save_span(
            trace_id,
            span_type,
            parent_trace_id=parent_trace_id,
            session_id=session_id,
            **kwargs,
        )
    except Exception as e:
        logger.debug(f"[runner] save_span({span_type}) 失败（忽略）：{e}")


async def run_prompt(
    system_prompt: str,
    user_message: str,
    *,
    llm: LLMProvider | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    agent_name: str = "prompt_agent",
    session_id: str | None = None,
    parent_trace_id: str | None = None,
) -> tuple[str, int, dict]:
    """
    通用单轮 prompt-only LLM 调用。

    参数：
        system_prompt   - 系统提示词（动态，由调用方决定）
        user_message    - 本轮用户消息
        llm             - 可直接传入外部已构造好的 LLMProvider；否则用 model/api_key/base_url 新建
        model           - 模型名，默认 MAIN_AGENT_CONFIG["model"]
        api_key         - 默认 MAIN_AGENT_CONFIG["api_key"]
        base_url        - 默认 MAIN_AGENT_CONFIG["base_url"]
        agent_name      - 用于日志/trace 的逻辑名，便于筛选
        session_id      - 可选，关联到已有会话
        parent_trace_id - 可选，关联到上层 agent 的 trace 链

    返回：
        (assistant_text, elapsed_ms, usage_dict)
        usage_dict 形如 {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
    """
    if llm is None:
        llm = LLMProvider(
            model=model or MAIN_AGENT_CONFIG["model"],
            api_key=api_key or MAIN_AGENT_CONFIG["api_key"],
            base_url=base_url or MAIN_AGENT_CONFIG["base_url"],
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    trace_id = _short_id()
    t_start = time.perf_counter()

    await _safe_trace(
        trace_id, "run_start",
        parent_trace_id=parent_trace_id,
        session_id=session_id,
        name=agent_name,
        input={"messages_count": len(messages), "system_len": len(system_prompt)},
    )

    # 真正发起 LLM 请求（无 tools）
    response, elapsed_ms, usage = llm.chat(messages, tools=None)
    text = (response.choices[0].message.content or "").strip()
    finish_reason = response.choices[0].finish_reason if response.choices else "unknown"

    await _safe_trace(
        trace_id, "llm_call",
        parent_trace_id=parent_trace_id,
        session_id=session_id,
        name=llm.model,
        input={"messages_count": len(messages)},
        output={"finish_reason": finish_reason, "text_len": len(text)},
        elapsed_ms=elapsed_ms,
        **usage,
    )

    total_ms = int((time.perf_counter() - t_start) * 1000)
    await _safe_trace(
        trace_id, "run_end",
        parent_trace_id=parent_trace_id,
        session_id=session_id,
        name=agent_name,
        output={"status": "done", "finish_reason": finish_reason},
        elapsed_ms=total_ms,
    )

    logger.info(
        f"[{agent_name}] model={llm.model}  耗时={elapsed_ms}ms  "
        f"tokens={usage.get('total_tokens', '?')}  finish={finish_reason}"
    )
    return text, elapsed_ms, usage
