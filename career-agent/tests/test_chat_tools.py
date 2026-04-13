"""
chat_tools.py 单元测试。

覆盖内容：
1. query_profile — 有数据 / 无数据
2. query_my_assessments — 有记录 / 无记录
3. query_my_plans — 有记录 / 无记录
4. query_today_tasks — 有任务 / 无任务
5. update_profile — 正常更新 / 无记录时报错
6. context user_id 未设置时的错误处理
"""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LLM_MODEL",    "gpt-4o-mini")
os.environ.setdefault("LLM_API_KEY",  "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://localhost:9999")
os.environ.setdefault("DB_HOST",      "localhost")
os.environ.setdefault("DB_USER",      "test")
os.environ.setdefault("DB_PASSWORD",  "test")
os.environ.setdefault("DB_NAME",      "test")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from agent.tools.context import current_user_id


# ── Helper: mock DB pool ──────────────────────────────────────────────

class MockCursor:
    """模拟 aiomysql cursor，支持 DictCursor 行为。"""
    def __init__(self, rows=None):
        self._rows = rows or []
        self._idx = 0

    async def execute(self, sql, args=None):
        pass

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
        return self._cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockPool:
    def __init__(self, rows=None):
        self._rows = rows

    def acquire(self):
        return MockConnection(MockCursor(self._rows))


# ── Tests ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def set_user_id():
    """每个测试前设置 user_id，测试后重置。"""
    token = current_user_id.set(1)
    yield
    current_user_id.reset(token)


class TestQueryProfile:
    @pytest.mark.asyncio
    async def test_no_profile(self):
        import agent.memory.db as memory_db
        memory_db._pool = MockPool(rows=[])

        from agent.tools.chat_tools import query_profile
        result = json.loads(await query_profile())
        assert "message" in result
        assert "未找到" in result["message"]

    @pytest.mark.asyncio
    async def test_has_profile(self):
        import agent.memory.db as memory_db
        extracted = json.dumps({"name": "张三", "age": 28}, ensure_ascii=False)
        memory_db._pool = MockPool(rows=[
            {"upload_id": "u1", "extracted": extracted, "created_at": "2024-01-01"}
        ])

        from agent.tools.chat_tools import query_profile
        result = json.loads(await query_profile())
        assert "profile" in result
        assert result["profile"]["name"] == "张三"

    @pytest.mark.asyncio
    async def test_no_user_id(self):
        """user_id 未设置时返回错误。"""
        import agent.memory.db as memory_db
        memory_db._pool = MockPool()

        token = current_user_id.set(None)
        try:
            from agent.tools.chat_tools import query_profile
            result = json.loads(await query_profile())
            assert "error" in result
        finally:
            current_user_id.reset(token)


class TestQueryMyAssessments:
    @pytest.mark.asyncio
    async def test_no_assessments(self):
        import agent.memory.db as memory_db
        memory_db._pool = MockPool(rows=[])

        from agent.tools.chat_tools import query_my_assessments
        result = json.loads(await query_my_assessments())
        assert "message" in result

    @pytest.mark.asyncio
    async def test_has_assessments(self):
        import agent.memory.db as memory_db
        memory_db._pool = MockPool(rows=[
            {"assessment_id": "a1", "status": "done", "created_at": "2024-01-01", "updated_at": None}
        ])

        from agent.tools.chat_tools import query_my_assessments
        result = json.loads(await query_my_assessments())
        assert "assessments" in result
        assert len(result["assessments"]) == 1


class TestQueryMyPlans:
    @pytest.mark.asyncio
    async def test_no_plans(self):
        import agent.memory.db as memory_db
        memory_db._pool = MockPool(rows=[])

        from agent.tools.chat_tools import query_my_plans
        result = json.loads(await query_my_plans())
        assert "message" in result

    @pytest.mark.asyncio
    async def test_has_plans(self):
        import agent.memory.db as memory_db
        memory_db._pool = MockPool(rows=[
            {"plan_id": "p1", "assessment_id": "a1", "onetsoc_code": "11-1011.00",
             "duration_weeks": 4, "start_date": "2024-01-01", "status": "daily_ready", "created_at": "2024-01-01"}
        ])

        from agent.tools.chat_tools import query_my_plans
        result = json.loads(await query_my_plans())
        assert "plans" in result


class TestQueryTodayTasks:
    @pytest.mark.asyncio
    async def test_no_tasks(self):
        import agent.memory.db as memory_db
        memory_db._pool = MockPool(rows=[])

        from agent.tools.chat_tools import query_today_tasks
        result = json.loads(await query_today_tasks())
        assert "message" in result

    @pytest.mark.asyncio
    async def test_has_tasks(self):
        import agent.memory.db as memory_db
        tasks_json = json.dumps([{"id": "t1", "title": "学习Python", "duration_min": 45, "type": "study"}])
        completed_json = json.dumps(["t1"])
        memory_db._pool = MockPool(rows=[
            {"plan_id": "p1", "week_number": 1, "day_number": 1,
             "date": "2024-01-01", "tasks": tasks_json, "completed_ids": completed_json}
        ])

        from agent.tools.chat_tools import query_today_tasks
        result = json.loads(await query_today_tasks())
        assert "today_tasks" in result
        assert result["today_tasks"][0]["tasks"][0]["completed"] is True


class TestUpdateProfile:
    @pytest.mark.asyncio
    async def test_no_record(self):
        import agent.memory.db as memory_db
        memory_db._pool = MockPool(rows=[])

        from agent.tools.chat_tools import update_profile
        result = json.loads(await update_profile("name", "李四"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_success(self):
        import agent.memory.db as memory_db
        extracted = json.dumps({"name": "张三", "age": 28}, ensure_ascii=False)
        # MockPool 的 rows 用于 fetchone
        memory_db._pool = MockPool(rows=[
            {"upload_id": "u1", "extracted": extracted}
        ])

        from agent.tools.chat_tools import update_profile
        result = json.loads(await update_profile("name", "李四"))
        assert "message" in result
        assert "已更新" in result["message"]
        assert result["new_value"] == "李四"

    @pytest.mark.asyncio
    async def test_update_json_value(self):
        import agent.memory.db as memory_db
        extracted = json.dumps({"name": "张三", "skills": []}, ensure_ascii=False)
        memory_db._pool = MockPool(rows=[
            {"upload_id": "u1", "extracted": extracted}
        ])

        from agent.tools.chat_tools import update_profile
        result = json.loads(await update_profile("skills", '["Python","SQL"]'))
        assert result["new_value"] == ["Python", "SQL"]
