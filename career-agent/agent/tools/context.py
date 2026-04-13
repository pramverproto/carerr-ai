"""请求级上下文变量，在 /chat 端点设置，工具函数中读取。"""

from contextvars import ContextVar

current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)
