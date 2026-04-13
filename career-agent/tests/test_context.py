"""
context.py 单元测试。

覆盖内容：
1. current_user_id 默认值为 None
2. set / get / reset 正常流程
3. 嵌套 set/reset 不互相干扰
"""

import os
import sys

os.environ.setdefault("LLM_MODEL",    "gpt-4o-mini")
os.environ.setdefault("LLM_API_KEY",  "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://localhost:9999")
os.environ.setdefault("DB_HOST",      "localhost")
os.environ.setdefault("DB_USER",      "test")
os.environ.setdefault("DB_PASSWORD",  "test")
os.environ.setdefault("DB_NAME",      "test")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from agent.tools.context import current_user_id


class TestContextVar:
    def test_default_is_none(self):
        assert current_user_id.get() is None

    def test_set_and_get(self):
        token = current_user_id.set(42)
        try:
            assert current_user_id.get() == 42
        finally:
            current_user_id.reset(token)
        assert current_user_id.get() is None

    def test_nested_set_reset(self):
        t1 = current_user_id.set(1)
        try:
            assert current_user_id.get() == 1
            t2 = current_user_id.set(2)
            try:
                assert current_user_id.get() == 2
            finally:
                current_user_id.reset(t2)
            assert current_user_id.get() == 1
        finally:
            current_user_id.reset(t1)
        assert current_user_id.get() is None
