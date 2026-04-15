import time
import litellm
from agent.logger import get_logger

logger = get_logger("agent.llm")


class LLMProvider:
    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def chat(self, messages: list, tools: list | None = None):
        # 发起 LLM 请求，返回 (response, elapsed_ms, usage) 三元组。
        # usage 为 dict：{"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
        # tools 为 None 时不传工具参数，避免部分模型对空列表报错。
        logger.debug(f"[LLM 请求] model={self.model}  messages={len(messages)} 条  tools={len(tools) if tools else 0} 个")
        t0 = time.perf_counter()
        response = litellm.completion(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            messages=messages,
            tools=tools or None,
            max_tokens=4096,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        finish_reason = response.choices[0].finish_reason if response.choices else "unknown"
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        logger.debug(
            f"[LLM 响应] 耗时：{elapsed_ms}ms  finish_reason={finish_reason}"
            f"  tokens={usage.get('total_tokens', '?')}"
        )
        return response, elapsed_ms, usage

    def chat_stream(self, messages: list, tools: list | None = None):
        """
        发起流式 LLM 请求，返回 litellm streaming response（可迭代）。
        每次迭代返回一个 chunk，通过 chunk.choices[0].delta 获取增量内容。
        """
        logger.debug(f"[LLM 流式请求] model={self.model}  messages={len(messages)} 条")
        return litellm.completion(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            messages=messages,
            tools=tools or None,
            stream=True,
        )
