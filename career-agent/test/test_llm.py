import sys
sys.path.append(".")

from agent.providers.llm import LLMProvider

llm = LLMProvider(
    model="gpt-3.5-turbo",
    api_key="sk-DBBhVjVOKG45XDIqTa9Ew8whk0w2KGEa4bslvLg1FR7JN2XM",
    base_url="https://www.dmxapi.cn/v1",
)

# 测试 1：普通对话能正常返回，chat() 返回 (response, elapsed_ms, usage) 三元组
response, elapsed_ms, usage = llm.chat([
    {"role": "user", "content": "你好，回复'测试成功'这四个字"}
])
assert response.choices[0].finish_reason == "stop"
assert response.choices[0].message.content is not None
assert isinstance(elapsed_ms, int) and elapsed_ms > 0
print("✓ 普通对话返回正常")
print(f"  回答：{response.choices[0].message.content}")
print(f"  耗时：{elapsed_ms}ms")

# 测试 2：usage 包含 token 消耗字段
assert "prompt_tokens" in usage
assert "completion_tokens" in usage
assert "total_tokens" in usage
assert isinstance(usage["total_tokens"], int) and usage["total_tokens"] > 0
print(f"✓ token 消耗记录正确：{usage}")

# 测试 3：传入 tools 时能正常返回（不报错）
tools = [{
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "获取当前时间",
        "parameters": {"type": "object", "properties": {}}
    }
}]
response, elapsed_ms, usage = llm.chat(
    [{"role": "user", "content": "现在几点了？"}],
    tools=tools
)
assert response.choices[0].finish_reason in ("stop", "tool_calls")
print("✓ 携带 tools 请求不报错")
print(f"  finish_reason：{response.choices[0].finish_reason}")

# 测试 4：tools=None 时不报错（避免部分模型对空列表报错的回归测试）
response, _, _ = llm.chat(
    [{"role": "user", "content": "ping"}],
    tools=None
)
assert response.choices[0].message.content is not None
print("✓ tools=None 不报错")

print("\n全部测试通过")
