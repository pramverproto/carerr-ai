"""
Chat 专用查询/更新工具。
通过 contextvars 获取当前请求的 user_id，实现数据隔离。
"""

import datetime
import json

import aiomysql

import agent.memory.db as memory_db
from agent.tools.context import current_user_id
from agent.tools.registry import tool


# ── 查询：我的评估记录 ────────────────────────────────────────────────

@tool(
    description="查询当前用户的所有能力评估记录列表，返回每条记录的 assessment_id、状态、创建时间。",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def query_my_assessments() -> str:
    user_id = current_user_id.get()
    if not user_id or memory_db._pool is None:
        return json.dumps({"error": "无法获取用户信息或数据库未初始化"}, ensure_ascii=False)

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT assessment_id, status, created_at, updated_at
                   FROM assessment_jobs WHERE user_id = %s
                   ORDER BY created_at DESC LIMIT 20""",
                (user_id,),
            )
            rows = await cur.fetchall()

    for r in rows:
        for k in ("created_at", "updated_at"):
            if r.get(k):
                r[k] = str(r[k])

    if not rows:
        return json.dumps({"message": "你还没有任何评估记录"}, ensure_ascii=False)
    return json.dumps({"assessments": rows}, ensure_ascii=False)


# ── 查询：我的计划列表 ────────────────────────────────────────────────

@tool(
    description="查询当前用户的所有职业规划报告和详细计划。返回职业规划报告（含匹配概览、JD推荐、差距分析、行动计划）以及详细日程计划。",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def query_my_plans() -> str:
    user_id = current_user_id.get()
    if not user_id or memory_db._pool is None:
        return json.dumps({"error": "无法获取用户信息或数据库未初始化"}, ensure_ascii=False)

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 1) 查职业规划报告（career_plan_blocks 通过 assessment_jobs 关联 user_id）
            await cur.execute(
                """SELECT cpb.assessment_id, cpb.onetsoc_code, cpb.block_id,
                          cpb.block_json, cpb.generated_at
                   FROM career_plan_blocks cpb
                   JOIN assessment_jobs aj ON aj.assessment_id = cpb.assessment_id
                   WHERE aj.user_id = %s
                   ORDER BY cpb.generated_at DESC LIMIT 40""",
                (user_id,),
            )
            block_rows = await cur.fetchall()

            # 2) 查详细日程计划（plan_schedules）
            await cur.execute(
                """SELECT plan_id, assessment_id, onetsoc_code,
                          duration_weeks, start_date, status, created_at
                   FROM plan_schedules WHERE user_id = %s
                   ORDER BY created_at DESC LIMIT 20""",
                (user_id,),
            )
            schedule_rows = await cur.fetchall()

    # 整理规划报告：按 assessment_id + onetsoc_code 分组
    career_reports: dict[str, dict] = {}
    for r in block_rows:
        key = f"{r['assessment_id']}_{r['onetsoc_code']}"
        if key not in career_reports:
            career_reports[key] = {
                "assessment_id": r["assessment_id"],
                "onetsoc_code": r["onetsoc_code"],
                "generated_at": str(r["generated_at"]) if r.get("generated_at") else None,
                "blocks": {},
            }
        block_json = r["block_json"]
        if isinstance(block_json, str):
            try:
                block_json = json.loads(block_json)
            except (json.JSONDecodeError, TypeError):
                pass
        career_reports[key]["blocks"][r["block_id"]] = block_json

    # 整理日程计划
    for r in schedule_rows:
        for k in ("start_date", "created_at"):
            if r.get(k):
                r[k] = str(r[k])

    result = {}
    if career_reports:
        result["career_reports"] = list(career_reports.values())
    if schedule_rows:
        result["detailed_schedules"] = schedule_rows

    if not result:
        return json.dumps({"message": "你还没有任何职业规划记录"}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, default=str)


# ── 查询：今天的任务 ──────────────────────────────────────────────────

@tool(
    description="查询当前用户今天的待办任务列表，包括任务标题、类型、时长、是否已完成。",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def query_today_tasks() -> str:
    user_id = current_user_id.get()
    if not user_id or memory_db._pool is None:
        return json.dumps({"error": "无法获取用户信息或数据库未初始化"}, ensure_ascii=False)

    today = datetime.date.today().isoformat()

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT pdt.plan_id, pdt.week_number, pdt.day_number,
                          pdt.date, pdt.tasks, pdt.completed_ids
                   FROM plan_daily_tasks pdt
                   JOIN plan_schedules ps ON ps.plan_id = pdt.plan_id
                   WHERE ps.user_id = %s AND pdt.date = %s""",
                (user_id, today),
            )
            rows = await cur.fetchall()

    result = []
    for r in rows:
        tasks = json.loads(r["tasks"]) if isinstance(r["tasks"], str) else (r["tasks"] or [])
        completed = json.loads(r["completed_ids"]) if isinstance(r["completed_ids"], str) else (r["completed_ids"] or [])
        for t in tasks:
            t["completed"] = t.get("id") in completed
        result.append({
            "plan_id": r["plan_id"],
            "date": str(r["date"]),
            "tasks": tasks,
        })

    if not result:
        return json.dumps({"message": "今天没有待办任务"}, ensure_ascii=False)
    return json.dumps({"today_tasks": result}, ensure_ascii=False)


# ── 查询：个人资料 ────────────────────────────────────────────────────

@tool(
    description="查询当前用户的个人资料信息（最近一次简历解析结果），包括姓名、年龄、学历、当前职位、技能等。",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def query_profile() -> str:
    user_id = current_user_id.get()
    if not user_id or memory_db._pool is None:
        return json.dumps({"error": "无法获取用户信息或数据库未初始化"}, ensure_ascii=False)

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT upload_id, extracted, created_at
                   FROM resume_uploads
                   WHERE user_id = %s AND extracted IS NOT NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id,),
            )
            row = await cur.fetchone()

    if not row:
        return json.dumps({"message": "未找到个人资料，请先上传简历或在「信息完善」页面填写信息"}, ensure_ascii=False)

    extracted = json.loads(row["extracted"]) if isinstance(row["extracted"], str) else row["extracted"]
    return json.dumps({"profile": extracted, "upload_id": row["upload_id"]}, ensure_ascii=False)


# ── 更新：个人资料字段 ────────────────────────────────────────────────

@tool(
    description=(
        "更新当前用户的个人资料字段。可更新的字段包括：name, age, education, "
        "current_title, years_of_experience, skills, supplement。"
        "传入要更新的字段名和新值。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "description": "要更新的字段名",
                "enum": ["name", "age", "education", "current_title",
                         "years_of_experience", "skills", "supplement"],
            },
            "value": {
                "type": "string",
                "description": "新值（如 skills 传 JSON 数组字符串 '[\"Python\",\"SQL\"]'）",
            },
        },
        "required": ["field", "value"],
    },
)
async def update_profile(field: str, value: str) -> str:
    user_id = current_user_id.get()
    if not user_id or memory_db._pool is None:
        return json.dumps({"error": "无法获取用户信息或数据库未初始化"}, ensure_ascii=False)

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT upload_id, extracted FROM resume_uploads
                   WHERE user_id = %s AND extracted IS NOT NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id,),
            )
            row = await cur.fetchone()

    if not row:
        return json.dumps({"error": "未找到个人资料记录，请先上传简历或填写信息"}, ensure_ascii=False)

    extracted = json.loads(row["extracted"]) if isinstance(row["extracted"], str) else row["extracted"]

    # 尝试 JSON 解析，失败则作为纯字符串
    try:
        parsed_value = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed_value = value

    extracted[field] = parsed_value

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE resume_uploads SET extracted = %s WHERE upload_id = %s",
                (json.dumps(extracted, ensure_ascii=False), row["upload_id"]),
            )

    return json.dumps({"message": f"已更新字段 {field}", "new_value": parsed_value}, ensure_ascii=False)
