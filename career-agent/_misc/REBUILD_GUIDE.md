# nanobot 重写/复现指南

> 本文档面向希望从零重建一个类似 nanobot 的 AI 助手框架的开发者。
> 按照从简到繁、从核心到外围的顺序组织，每一阶段都是可独立运行的里程碑。

---

## 一、技术选型

| 组件 | nanobot 使用 | 可替代方案 |
|------|-------------|-----------|
| 语言 | Python 3.11+ | - |
| LLM 接入 | LiteLLM（统一适配器） | 直接用 openai SDK |
| 数据校验 | Pydantic v2 | dataclasses |
| CLI | Typer | Click、argparse |
| HTTP 客户端 | httpx（async） | aiohttp |
| 日志 | loguru | logging |
| 定时任务 | croniter | APScheduler |
| 会话持久化 | JSONL 文件 | SQLite |
| 并发模型 | asyncio（原生） | - |

---

## 二、构建路线图（推荐顺序）

```
阶段 1：最小可用 Agent（CLI 单轮对话）
    ↓
阶段 2：会话历史（多轮对话）
    ↓
阶段 3：工具调用（文件/Shell/搜索）
    ↓
阶段 4：消息总线 + 渠道解耦
    ↓
阶段 5：记忆压缩系统
    ↓
阶段 6：多渠道接入（Telegram 等）
    ↓
阶段 7：定时任务 / 心跳 / 子 Agent（可选增强）
```

---

## 阶段 1：最小可用 Agent

**目标**：能与 LLM 单轮对话，跑通 LLM API。

### 1.1 项目骨架

```
my_agent/
├── providers/
│   └── base.py          # LLMProvider 抽象 + LLMResponse
│   └── litellm_provider.py
└── main.py
```

### 1.2 核心数据结构

```python
# providers/base.py
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list = field(default_factory=list)
    finish_reason: str = "stop"

    @property
    def has_tool_calls(self):
        return len(self.tool_calls) > 0

class LLMProvider(ABC):
    @abstractmethod
    async def chat(self, messages, tools=None, model=None, **kwargs) -> LLMResponse:
        pass
```

### 1.3 LiteLLM 适配器要点

```python
# providers/litellm_provider.py
import litellm

class LiteLLMProvider(LLMProvider):
    def __init__(self, api_key, model="gpt-4o-mini"):
        self.api_key = api_key
        self.default_model = model

    async def chat(self, messages, tools=None, model=None, **kwargs) -> LLMResponse:
        response = await litellm.acompletion(
            model=model or self.default_model,
            messages=messages,
            tools=tools,
            api_key=self.api_key,
        )
        choice = response.choices[0]
        msg = choice.message
        # 解析 tool_calls
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments or "{}"),
                ))
        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
        )
```

**关键注意事项：**
- LiteLLM 统一了 100+ 模型的接口，模型名格式：`anthropic/claude-opus-4-5`、`openai/gpt-4o`
- 不同提供商的 tool_calls 格式略有差异，litellm 已统一处理
- 需要处理 `finish_reason == "tool_calls"` 的情况

---

## 阶段 2：Agent 循环（多轮 + 工具调用）

**目标**：实现 ReAct 模式的 Agent 循环（推理 → 行动 → 观察 → 推理…）

### 2.1 核心循环逻辑

```python
# agent/loop.py 最简版本
async def run_agent_loop(messages, provider, tools, max_iterations=40):
    for _ in range(max_iterations):
        response = await provider.chat(messages=messages, tools=tools)

        if response.has_tool_calls:
            # 1. 把 assistant 的 tool_call 消息追加进去
            messages.append({
                "role": "assistant",
                "content": response.content,
                "tool_calls": [tc.to_openai_dict() for tc in response.tool_calls]
            })

            # 2. 执行每个工具，把结果追加进去
            for tc in response.tool_calls:
                result = await execute_tool(tc.name, tc.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": result,
                })
        else:
            # 3. 最终回复，退出循环
            messages.append({"role": "assistant", "content": response.content})
            return response.content, messages

    return "达到最大迭代次数", messages
```

### 2.2 工具注册中心

```python
# agent/tools/registry.py
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get_definitions(self) -> list[dict]:
        """返回 OpenAI function calling 格式的 JSON Schema 列表"""
        return [t.get_definition() for t in self._tools.values()]

    async def execute(self, name: str, args: dict) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Unknown tool: {name}"
        try:
            return await tool.execute(**args)
        except Exception as e:
            return f"Tool error: {e}"
```

### 2.3 工具基类

```python
# agent/tools/base.py
from abc import ABC, abstractmethod

class BaseTool(ABC):
    name: str
    description: str

    def get_definition(self) -> dict:
        """子类实现，返回 OpenAI tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.get_parameters(),
            }
        }

    @abstractmethod
    def get_parameters(self) -> dict:
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        pass
```

### 2.4 实现一个最简工具（文件读取）

```python
class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file"

    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"}
            },
            "required": ["path"]
        }

    async def execute(self, path: str) -> str:
        try:
            return Path(path).read_text(encoding="utf-8")
        except Exception as e:
            return f"Error: {e}"
```

---

## 阶段 3：会话历史持久化

**目标**：对话历史跨请求保持，支持多用户/多会话。

### 3.1 Session 数据结构

```python
# session/manager.py
@dataclass
class Session:
    key: str          # "telegram:12345"
    messages: list[dict] = field(default_factory=list)
    last_consolidated: int = 0  # 已压缩消息数（记忆系统用）

    def get_history(self) -> list[dict]:
        """返回未压缩的消息，过滤掉 runtime context 等内部字段"""
        return self.messages[self.last_consolidated:]
```

### 3.2 JSONL 持久化

nanobot 选择 **JSONL 格式**（每行一个 JSON）而非 SQLite，优点：
- 可以追加写入，不需要事务
- 方便 grep 调试
- 对话消息天然是顺序追加的

```python
# 保存：每条消息一行
def save(self, session: Session, path: Path):
    with open(path, "w") as f:
        for msg in session.messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

# 加载：逐行解析
def load(self, path: Path) -> Session:
    messages = []
    with open(path) as f:
        for line in f:
            messages.append(json.loads(line))
    return Session(messages=messages)
```

### 3.3 重要实现细节：孤立 tool_result 问题

当历史消息被截断时，可能出现 `role=tool` 消息但找不到对应的 `role=assistant` 中的 `tool_calls`，这会导致 LLM API 返回 400 错误。

解决方法（参考 `session/manager.py:_find_legal_start`）：
```python
def find_legal_start(messages):
    """找到第一个合法的起始位置：所有 tool 结果都有对应的 tool_call 声明"""
    declared_ids = set()
    for i, msg in enumerate(messages):
        if msg["role"] == "assistant":
            for tc in msg.get("tool_calls") or []:
                declared_ids.add(tc["id"])
        elif msg["role"] == "tool":
            if msg.get("tool_call_id") not in declared_ids:
                # 这条 tool 结果找不到对应的声明，从下一条开始重新找
                declared_ids.clear()
                return find_legal_start(messages[i+1:])
    return messages
```

---

## 阶段 4：消息总线 + 渠道解耦

**目标**：Agent 不直接依赖任何聊天平台，通过总线通信。

### 4.1 消息总线（核心设计）

```python
# bus/queue.py  —— 整个文件不超过 30 行
import asyncio

class MessageBus:
    def __init__(self):
        self.inbound: asyncio.Queue = asyncio.Queue()
        self.outbound: asyncio.Queue = asyncio.Queue()

    async def publish_inbound(self, msg): await self.inbound.put(msg)
    async def consume_inbound(self): return await self.inbound.get()
    async def publish_outbound(self, msg): await self.outbound.put(msg)
    async def consume_outbound(self): return await self.outbound.get()
```

### 4.2 渠道抽象

```python
# channels/base.py
class BaseChannel(ABC):
    name: str

    def __init__(self, config, bus: MessageBus):
        self.config = config
        self.bus = bus

    @abstractmethod
    async def start(self): pass   # 监听平台消息，推入 bus.inbound

    @abstractmethod
    async def stop(self): pass

    @abstractmethod
    async def send(self, msg: OutboundMessage): pass  # 从 bus.outbound 消费并发送

    async def _handle_message(self, sender_id, chat_id, content, ...):
        # 权限检查
        if not self.is_allowed(sender_id):
            return
        await self.bus.publish_inbound(InboundMessage(...))
```

### 4.3 渠道管理器

```python
# channels/manager.py
class ChannelManager:
    async def start_all(self):
        # 并发启动所有渠道
        tasks = [asyncio.create_task(ch.start()) for ch in self.channels]
        # 同时启动出站消息分发器
        tasks.append(asyncio.create_task(self._outbound_dispatcher()))
        await asyncio.gather(*tasks)

    async def _outbound_dispatcher(self):
        """把 bus.outbound 的消息路由到对应渠道"""
        while True:
            msg = await self.bus.consume_outbound()
            channel = self._find_channel(msg.channel)
            if channel:
                await channel.send(msg)
```

### 4.4 接入 Telegram（最简版本）

```python
# channels/telegram.py
from telegram.ext import Application, MessageHandler, filters

class TelegramChannel(BaseChannel):
    name = "telegram"

    async def start(self):
        self.app = Application.builder().token(self.config.token).build()
        self.app.add_handler(MessageHandler(filters.TEXT, self._on_message))
        await self.app.run_polling()

    async def _on_message(self, update, context):
        await self._handle_message(
            sender_id=str(update.message.from_user.id),
            chat_id=str(update.message.chat_id),
            content=update.message.text,
        )

    async def send(self, msg: OutboundMessage):
        await self.app.bot.send_message(chat_id=msg.chat_id, text=msg.content)
```

---

## 阶段 5：系统提示词构建

**目标**：构建丰富的系统提示词，让 Agent 有"人格"和"记忆"。

### 5.1 系统提示词组成

```
[Identity]          ← 固定：机器人名称、工作区路径、行为准则
[Bootstrap files]   ← 用户自定义：SOUL.md / USER.md / TOOLS.md（workspace 根目录）
[Memory]            ← 动态：workspace/memory/MEMORY.md
[Skills summary]    ← 动态：已安装技能列表（只显示标题，不加载全文）
---
[Session history]   ← 对话历史
---
[Runtime context]   ← 当前时间 + 渠道信息（每条消息前注入）
[User message]      ← 用户输入
```

### 5.2 关键设计：Runtime Context 注入

nanobot 将当前时间等动态信息注入到**每条用户消息之前**（而非系统提示词），原因：
- 系统提示词可以被 LLM 缓存（prompt cache），节省 token 费用
- 时间等动态信息不适合放在可缓存的系统提示词中

```python
def build_messages(self, history, current_message, channel=None, chat_id=None):
    runtime_ctx = f"[Runtime Context]\nCurrent Time: {datetime.now()}\nChannel: {channel}"
    merged_user_msg = f"{runtime_ctx}\n\n{current_message}"
    return [
        {"role": "system", "content": self.build_system_prompt()},
        *history,
        {"role": "user", "content": merged_user_msg},
    ]
```

---

## 阶段 6：记忆压缩系统

**目标**：防止 context window 溢出，同时保留长期重要信息。

### 6.1 压缩触发策略

```
每次处理消息前检查：
  当前 prompt token 数 > context_window_tokens ?
    → 触发压缩（目标压缩到 50% 以下）
  否则
    → 跳过
```

### 6.2 压缩流程

```python
# 1. 选取一批旧消息（到某个 user 轮次为边界）
chunk = session.messages[session.last_consolidated : end_idx]

# 2. 用 LLM 归纳，强制调用 save_memory 工具
messages = [
    {"role": "system", "content": "You are a memory consolidation agent."},
    {"role": "user", "content": f"Process this conversation:\n{format_messages(chunk)}"},
]
response = await provider.chat(
    messages=messages,
    tools=[SAVE_MEMORY_TOOL_SCHEMA],
    tool_choice={"type": "function", "function": {"name": "save_memory"}},
)

# 3. 解析结果，写入文件
args = response.tool_calls[0].arguments
memory_file.write_text(args["memory_update"])
history_file.append(args["history_entry"])

# 4. 更新压缩指针
session.last_consolidated = end_idx
```

### 6.3 两层记忆文件

- **MEMORY.md**：结构化事实（用户偏好、重要决定、项目信息等），每次压缩时 LLM 会更新整个文件
- **HISTORY.md**：时间戳日志（`[2024-01-15 14:30] 讨论了...`），只追加，永不覆盖

---

## 阶段 7：技能系统

**目标**：用 Markdown 文件扩展 Agent 能力，无需修改代码。

### 7.1 技能文件格式

```markdown
<!-- skills/weather/SKILL.md -->
# weather — 天气查询

Queries current weather and forecasts using OpenWeatherMap API.

## API Key
Set OPENWEATHERMAP_API_KEY in environment.

## Usage
1. Call exec tool: `curl "api.openweathermap.org/data/2.5/weather?q={city}&appid={key}"`
2. Parse JSON response...
```

### 7.2 技能加载策略

```python
# 系统提示词中只显示摘要（第一行标题），节省 token
def build_skills_summary(self) -> str:
    lines = []
    for skill_dir in self.get_skill_dirs():
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            first_line = skill_md.read_text().split("\n")[0].strip("# ")
            lines.append(f"- {skill_dir.name}: {first_line}")
    return "\n".join(lines)

# Agent 通过 read_file 工具按需读取完整内容
# 提示词中说明：
# "To use a skill, read its SKILL.md file using the read_file tool."
```

---

## 阶段 8：子 Agent 系统

**目标**：主 Agent 派生后台任务，不阻塞主对话。

### 8.1 子 Agent 工具定义

```python
# 注册给主 Agent 的工具
SPAWN_TOOL_SCHEMA = {
    "name": "spawn",
    "description": "Spawn a background subagent to handle a long-running task",
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "label": {"type": "string"},
        },
        "required": ["task"]
    }
}
```

### 8.2 子 Agent 生命周期

```python
async def spawn(self, task, label, origin_channel, origin_chat_id):
    task_id = uuid.uuid4().hex[:8]

    async def _run():
        # 子 Agent 有自己独立的工具集（不含 spawn/message）
        tools = build_subagent_tools()
        messages = [
            {"role": "system", "content": "You are a focused subagent..."},
            {"role": "user", "content": task},
        ]
        # 最多 15 次迭代
        result = await run_agent_loop(messages, provider, tools, max_iterations=15)

        # 完成后通过 bus 通知主 Agent
        announce = f"[Subagent '{label}' completed]\nResult: {result}"
        await bus.publish_inbound(InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin_channel}:{origin_chat_id}",
            content=announce,
        ))

    asyncio.create_task(_run())
    return f"Background task started: {label}"
```

---

## 九、关键实现细节 & 踩坑记录

### 1. 工具调用参数解析

不同 LLM 返回的 `arguments` 格式不统一（有时是字符串，有时是 dict，有时是 list）：

```python
def parse_tool_args(arguments) -> dict:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            if isinstance(parsed, list) and parsed:
                return parsed[0]  # 某些模型把参数包在数组里
            return parsed
        except json.JSONDecodeError:
            # 用 json-repair 容错解析
            import json_repair
            return json_repair.loads(arguments)
    return {}
```

### 2. 防止空内容消息

部分提供商在 tool_calls 消息中不允许 `content` 为空字符串，必须为 `null`：

```python
# 保存 assistant 消息时：
if tool_calls and not content:
    content = None  # 而不是 ""
```

### 3. 提供商重试策略

区分**临时性错误**（429、502、503）和**永久性错误**（400、401）：

```python
TRANSIENT_MARKERS = ("429", "rate limit", "500", "502", "503", "timeout", "overloaded")

async def chat_with_retry(self, ...):
    for delay in [1, 2, 4]:  # 指数退避
        response = await self.chat(...)
        if response.finish_reason != "error":
            return response
        if not is_transient(response.content):
            return response  # 永久错误，不重试
        await asyncio.sleep(delay)
    return await self.chat(...)  # 最后一次尝试
```

### 4. 图片内容处理

某些不支持图片的模型收到 image_url 内容会报错，需要降级处理：

```python
async def chat_with_retry(self, messages, ...):
    response = await self.chat(messages, ...)
    if response.finish_reason == "error" and not is_transient(response.content):
        # 尝试去掉图片重新请求
        stripped = strip_images(messages)
        if stripped:
            return await self.chat(stripped, ...)
    return response
```

### 5. LLM 的 `<think>` 块处理

部分推理模型（DeepSeek-R1 等）在 content 中嵌入 `<think>...</think>` 思考过程，发送给用户前需要去掉：

```python
import re

def strip_think(text):
    if not text:
        return text
    return re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
```

### 6. 连续同角色消息问题

某些提供商不允许连续出现两条 `role=user` 的消息。在 `build_messages()` 中要把 runtime context 和用户消息**合并为一条**：

```python
# 错误：两条 user 消息
[..., {"role": "user", "content": runtime_ctx}, {"role": "user", "content": user_input}]

# 正确：合并为一条
[..., {"role": "user", "content": f"{runtime_ctx}\n\n{user_input}"}]
```

### 7. Cron 消息避免重复触发

CronService 计算下次触发时间时，要保存上次执行时间戳，防止重启后重复执行：

```python
# 保存到 cron_jobs.json 的字段
{
    "expression": "0 9 * * *",
    "message": "早安，请检查今日日程",
    "last_run": "2024-01-15T09:00:00",  # 必须持久化
    "next_run": "2024-01-16T09:00:00"
}
```

---

## 十、最简 MVP 代码量估算

| 模块 | 估计行数 |
|------|---------|
| LLM provider（LiteLLM 适配） | ~150 行 |
| Agent loop（工具调用循环） | ~100 行 |
| 工具注册中心 | ~50 行 |
| 3 个基础工具（read/write/exec） | ~150 行 |
| 消息总线 | ~30 行 |
| Session 管理（JSONL） | ~100 行 |
| 上下文构建器 | ~80 行 |
| CLI 入口 | ~50 行 |
| **合计 MVP** | **~710 行** |

加上渠道（Telegram 约 +150 行）、记忆压缩（+200 行）、定时任务（+150 行），完整版约 **1200~1500 行**核心代码。这也是 nanobot 宣称 "99% fewer lines than OpenClaw" 的底气所在。

---

## 十一、推荐开发顺序

```
Week 1：阶段 1-2
  ✓ 跑通 LiteLLM API 调用
  ✓ 实现 run_agent_loop()
  ✓ 注册 read_file / write_file / exec 工具
  ✓ CLI 能对话并调用工具

Week 2：阶段 3-4
  ✓ Session JSONL 持久化
  ✓ MessageBus 解耦
  ✓ BaseChannel + CLI 渠道
  ✓ 接入 Telegram（验证渠道架构）

Week 3：阶段 5-6
  ✓ 系统提示词构建（Identity + Memory + Skills）
  ✓ 记忆压缩（MEMORY.md + HISTORY.md）
  ✓ web_search + web_fetch 工具

Week 4：阶段 7-8 及打磨
  ✓ 技能 Markdown 系统
  ✓ 子 Agent（spawn）
  ✓ 定时任务（cron）
  ✓ 错误处理、重试、安全防护
```

---

## 十二、config.json 最简配置参考

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.mybot/workspace",
      "model": "openai/gpt-4o-mini",
      "maxTokens": 4096,
      "contextWindowTokens": 32768,
      "temperature": 0.1
    }
  },
  "providers": {
    "openai": {
      "apiKey": "sk-..."
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "...",
      "allowFrom": ["your_telegram_id"]
    }
  },
  "tools": {
    "web": {
      "search": {
        "provider": "duckduckgo"
      }
    }
  }
}
```
