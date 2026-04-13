TOOL_REGISTRY: dict = {}
TOOL_SCHEMAS: list = []


def tool(description: str, parameters: dict):
    # 装饰器工厂：@tool(description="...", parameters={...})
    # 同时将函数注册到 TOOL_REGISTRY，将 schema 追加到 TOOL_SCHEMAS。
    def decorator(fn):
        TOOL_REGISTRY[fn.__name__] = fn
        TOOL_SCHEMAS.append({
            "type": "function",
            "function": {
                "name": fn.__name__,
                "description": description,
                "parameters": parameters,
            }
        })
        return fn
    return decorator


async def call_tool(tool_name: str, tool_args: dict) -> str:
    fn = TOOL_REGISTRY.get(tool_name)
    if fn is None:
        return f"未知工具：{tool_name}"
    try:
        # 兼容同步和异步工具函数
        import asyncio
        if asyncio.iscoroutinefunction(fn):
            return str(await fn(**tool_args))
        return str(fn(**tool_args))
    except Exception as e:
        return f"执行出错：{e}"
