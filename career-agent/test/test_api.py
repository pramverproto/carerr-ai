"""
测试 FastAPI HTTP 接口
需要先启动服务：uv run uvicorn api:app --port 8000
"""
import sys
import json
import asyncio
sys.path.append(".")

try:
    import httpx
except ImportError:
    print("缺少 httpx，请执行：uv add httpx")
    sys.exit(1)

BASE_URL = "http://127.0.0.1:8000"
SESSION_ID = "test_api_session_001"


async def test():
    async with httpx.AsyncClient(timeout=60, transport=httpx.AsyncHTTPTransport(proxy=None)) as client:

        # 测试 1：health check
        r = await client.get(f"{BASE_URL}/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        print("✓ GET /health 正常")

        # 测试 2：非流式对话
        r = await client.post(f"{BASE_URL}/chat", json={
            "message": "你好，回复'非流式测试成功'六个字",
            "session_id": SESSION_ID,
            "stream": False,
        })
        assert r.status_code == 200
        data = r.json()
        assert "reply" in data and len(data["reply"]) > 0
        assert "elapsed_ms" in data
        print(f"✓ POST /chat 非流式：{data['reply']}  耗时：{data['elapsed_ms']}ms")

        # 测试 3：流式对话
        full_text = ""
        done_received = False
        async with client.stream("POST", f"{BASE_URL}/chat", json={
            "message": "用一句话介绍你自己",
            "session_id": SESSION_ID,
            "stream": True,
        }) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = json.loads(line[6:])
                if chunk["type"] == "text":
                    full_text += chunk["content"]
                elif chunk["type"] == "done":
                    done_received = True
                    elapsed_ms = chunk.get("elapsed_ms", 0)

        assert len(full_text) > 0, "流式响应文本为空"
        assert done_received, "未收到 done 事件"
        print(f"✓ POST /chat 流式：{full_text[:50]}...  耗时：{elapsed_ms}ms")

        # 测试 4：session_id=None 时不持久化，不报错
        r = await client.post(f"{BASE_URL}/chat", json={
            "message": "ping，回复'pong'",
            "stream": False,
        })
        assert r.status_code == 200
        assert "reply" in r.json()
        print(f"✓ session_id=None 不报错：{r.json()['reply']}")

        print("\n全部测试通过")


asyncio.run(test())
