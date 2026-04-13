"""
agent/runner.py - 通用单轮 LLM 调用（run_prompt）测试脚本。

覆盖点：
  1. 默认参数：仅传 system_prompt + user_message，返回三元组
  2. 传入自定义 LLMProvider：run_prompt 复用该 provider，不重建
  3. 传入 model 覆盖默认模型
  4. agent_name 传入后可体现在日志/trace（通过 mock save_span 捕获）
  5. trace 写入参数正确：run_start / llm_call / run_end 三段 + 正确的 parent_trace_id
  6. save_span 抛异常时不会影响主流程（静默失败）
  7. 并发调用：多个 run_prompt 任务并发跑不相互干扰

该脚本可脱机运行（通过 monkeypatch LLMProvider.chat 和 memory_db.save_span）；
也可以改为真实调用（末尾注释部分）。
"""

import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.append(".")

from agent.runner import run_prompt
from agent.providers.llm import LLMProvider
import agent.runner as runner_mod
import agent.memory.db as memory_db


# ------------------------------------------------------------------ #
#  测试 stub：伪造 litellm response 对象
# ------------------------------------------------------------------ #

def _fake_llm_response(content: str = "测试回答", finish_reason: str = "stop"):
    """构造一个符合 LLMProvider.chat 返回格式的假 response。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content),
        )],
        usage=SimpleNamespace(
            prompt_tokens=42,
            completion_tokens=18,
            total_tokens=60,
        ),
    )


class FakeLLMProvider:
    """替代 LLMProvider，不真正发起 HTTP。记录每次调用的参数。"""

    def __init__(self, model="fake-model", api_key="fake", base_url="http://fake"):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.call_log: list[dict] = []

    def chat(self, messages, tools=None):
        self.call_log.append({"messages": messages, "tools": tools})
        resp = _fake_llm_response(content=f"echo[{len(messages)}]")
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        }
        return resp, 123, usage


# ------------------------------------------------------------------ #
#  全局 stub：拦截 save_span
# ------------------------------------------------------------------ #

_save_span_calls: list[dict] = []


async def _stub_save_span(trace_id, span_type, **kwargs):
    _save_span_calls.append({"trace_id": trace_id, "span_type": span_type, **kwargs})


async def _failing_save_span(trace_id, span_type, **kwargs):
    raise RuntimeError("fake trace failure")


# ------------------------------------------------------------------ #
#  测试用例
# ------------------------------------------------------------------ #

async def test_basic_default_provider():
    """测试 1：不传 llm 时，run_prompt 按 MAIN_AGENT_CONFIG 构造 provider。"""
    _save_span_calls.clear()

    # patch LLMProvider 构造 → 返回 FakeLLMProvider
    fake = FakeLLMProvider(model="mocked-default")
    original_cls = runner_mod.LLMProvider
    runner_mod.LLMProvider = lambda **kw: fake  # type: ignore

    # patch save_span
    memory_db.save_span = _stub_save_span  # type: ignore

    try:
        text, elapsed_ms, usage = await run_prompt(
            system_prompt="你是助手",
            user_message="ping",
            agent_name="unit_test_default",
        )

        assert isinstance(text, str) and text.startswith("echo"), f"unexpected text: {text}"
        assert elapsed_ms == 123, f"elapsed_ms should come from fake llm: {elapsed_ms}"
        assert usage["total_tokens"] == 60
        assert usage["prompt_tokens"] == 42
        assert usage["completion_tokens"] == 18
        assert len(fake.call_log) == 1
        assert fake.call_log[0]["messages"][0]["role"] == "system"
        assert fake.call_log[0]["messages"][0]["content"] == "你是助手"
        assert fake.call_log[0]["messages"][1]["role"] == "user"
        assert fake.call_log[0]["messages"][1]["content"] == "ping"
        assert fake.call_log[0]["tools"] is None

        print("✓ 测试 1：默认 provider，消息结构 & 返回值正确")
    finally:
        runner_mod.LLMProvider = original_cls  # type: ignore


async def test_custom_llm_provider_reuse():
    """测试 2：传入自定义 LLMProvider 时应直接复用，不重建。"""
    _save_span_calls.clear()

    custom = FakeLLMProvider(model="custom-model")
    # 不 patch 构造器 → 如果 run_prompt 意外重建 provider，会用到真 LLMProvider，结果异常
    memory_db.save_span = _stub_save_span  # type: ignore

    text, _, usage = await run_prompt(
        system_prompt="sys",
        user_message="hi",
        llm=custom,
        agent_name="unit_test_custom",
    )
    assert text.startswith("echo")
    assert usage["total_tokens"] == 60
    assert len(custom.call_log) == 1

    print("✓ 测试 2：自定义 LLMProvider 被正确复用")


async def test_trace_spans_written():
    """测试 3：run_prompt 应写入 run_start / llm_call / run_end 三段 span。"""
    _save_span_calls.clear()
    memory_db.save_span = _stub_save_span  # type: ignore
    custom = FakeLLMProvider(model="trace-probe")

    parent_tid = "parent_trace_abc"
    sid = "session_xyz"
    await run_prompt(
        system_prompt="sys",
        user_message="u",
        llm=custom,
        agent_name="unit_trace",
        parent_trace_id=parent_tid,
        session_id=sid,
    )

    span_types = [c["span_type"] for c in _save_span_calls]
    assert span_types == ["run_start", "llm_call", "run_end"], f"unexpected span order: {span_types}"

    # parent_trace_id / session_id 应传递到每个 span
    for span in _save_span_calls:
        assert span["parent_trace_id"] == parent_tid
        assert span["session_id"] == sid

    # llm_call span 应包含 usage 字段
    llm_span = _save_span_calls[1]
    assert llm_span["prompt_tokens"] == 42
    assert llm_span["completion_tokens"] == 18
    assert llm_span["total_tokens"] == 60
    assert llm_span["elapsed_ms"] == 123
    assert llm_span["name"] == "trace-probe"  # 应该是模型名

    # run_start / run_end 的 name 应是 agent_name
    assert _save_span_calls[0]["name"] == "unit_trace"
    assert _save_span_calls[2]["name"] == "unit_trace"

    # 同一次 run_prompt 的三段 span 应共享 trace_id
    trace_ids = {c["trace_id"] for c in _save_span_calls}
    assert len(trace_ids) == 1, f"trace_ids should be unique per run_prompt: {trace_ids}"

    print(f"✓ 测试 3：trace spans 写入顺序正确，共享 trace_id={list(trace_ids)[0]}")


async def test_trace_failure_silent():
    """测试 4：save_span 抛异常时 run_prompt 应静默忽略不影响返回。"""
    memory_db.save_span = _failing_save_span  # type: ignore
    custom = FakeLLMProvider(model="silent")

    text, _, _ = await run_prompt(
        system_prompt="s",
        user_message="u",
        llm=custom,
        agent_name="silent_fail",
    )
    assert text.startswith("echo"), "save_span 失败不应影响 run_prompt 返回结果"

    print("✓ 测试 4：save_span 异常被正确吞掉，主流程不受影响")


async def test_concurrent_runs():
    """测试 5：并发多个 run_prompt 任务时互相独立，各自写自己的 trace。"""
    _save_span_calls.clear()
    memory_db.save_span = _stub_save_span  # type: ignore
    llms = [FakeLLMProvider(model=f"concurrent-{i}") for i in range(5)]

    results = await asyncio.gather(*[
        run_prompt(
            system_prompt=f"sys-{i}",
            user_message=f"msg-{i}",
            llm=llms[i],
            agent_name=f"concurrent_{i}",
        )
        for i in range(5)
    ])

    assert len(results) == 5
    assert all(r[0].startswith("echo") for r in results)

    # 每个 run_prompt 应写 3 条 span，合计 15 条
    assert len(_save_span_calls) == 15, f"expected 15 spans, got {len(_save_span_calls)}"

    # 5 个独立的 trace_id
    run_start_calls = [c for c in _save_span_calls if c["span_type"] == "run_start"]
    trace_ids = {c["trace_id"] for c in run_start_calls}
    assert len(trace_ids) == 5

    # 每个 agent_name 都应出现在对应 run_start 中
    agent_names = {c["name"] for c in run_start_calls}
    assert agent_names == {f"concurrent_{i}" for i in range(5)}

    print("✓ 测试 5：5 个并发 run_prompt 任务互相独立，trace 正确")


async def test_model_override():
    """测试 6：不传 llm 时，传入 model 参数应生效（通过捕获的构造参数断言）。"""
    captured = {}

    def fake_cls(model, api_key, base_url):
        captured["model"] = model
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        return FakeLLMProvider(model=model, api_key=api_key, base_url=base_url)

    original_cls = runner_mod.LLMProvider
    runner_mod.LLMProvider = fake_cls  # type: ignore
    memory_db.save_span = _stub_save_span  # type: ignore

    try:
        await run_prompt(
            system_prompt="s",
            user_message="u",
            model="gpt-4o-mini",
            api_key="sk-override",
            base_url="https://override.example.com/v1",
            agent_name="override_test",
        )
        assert captured["model"] == "gpt-4o-mini"
        assert captured["api_key"] == "sk-override"
        assert captured["base_url"] == "https://override.example.com/v1"
        print("✓ 测试 6：model/api_key/base_url 覆盖生效")
    finally:
        runner_mod.LLMProvider = original_cls  # type: ignore


# ------------------------------------------------------------------ #
#  Entrypoint
# ------------------------------------------------------------------ #

async def main():
    # 保存原始 save_span，测试结束恢复
    original_save_span = memory_db.save_span
    try:
        await test_basic_default_provider()
        await test_custom_llm_provider_reuse()
        await test_trace_spans_written()
        await test_trace_failure_silent()
        await test_concurrent_runs()
        await test_model_override()
        print("\n全部测试通过 ✓")
    finally:
        memory_db.save_span = original_save_span  # type: ignore


if __name__ == "__main__":
    asyncio.run(main())


# ------------------------------------------------------------------ #
#  ▸ 真实调用（可选）：取消下面注释用真 key 跑一轮端到端
# ------------------------------------------------------------------ #
#
# async def test_real_call():
#     llm = LLMProvider(
#         model="gpt-3.5-turbo",
#         api_key="sk-xxxx",
#         base_url="https://www.dmxapi.cn/v1",
#     )
#     text, elapsed_ms, usage = await run_prompt(
#         system_prompt="你是一个助手，回复要简短。",
#         user_message="用五个字夸我一下",
#         llm=llm,
#         agent_name="real_probe",
#     )
#     print(f"text={text} elapsed_ms={elapsed_ms} usage={usage}")
#
# asyncio.run(test_real_call())
