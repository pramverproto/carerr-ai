"""Learn Plan 相关的 DB 访问层。

所有 learn_* 表的读写集中在这里，便于替换/测试。
"""
from __future__ import annotations

import json
from typing import Any

import agent.memory.db as memory_db


# ------------------------------------------------------------------ #
#  outlines                                                            #
# ------------------------------------------------------------------ #

async def insert_outline(
    *,
    plan_id: str,
    user_id: int,
    assessment_id: str,
    stage_code: str,
    stage_title: str | None,
    modules: list[dict],
    total_weight: float,
    estimated_weeks: int,
    user_preference: str | None,
    status: str = "pending",
) -> None:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO learn_outlines
                   (plan_id, user_id, assessment_id, stage_code, stage_title,
                    modules, total_weight, estimated_weeks, user_preference, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (plan_id, user_id, assessment_id, stage_code, stage_title,
                 json.dumps(modules, ensure_ascii=False),
                 total_weight, estimated_weeks, user_preference, status),
            )


async def update_outline(
    *,
    plan_id: str,
    modules: list[dict] | None = None,
    user_preference: str | None = None,
    status: str | None = None,
    total_weeks: int | None = None,
    estimated_weeks: int | None = None,
    error_msg: str | None = None,
) -> None:
    sets = []
    params: list[Any] = []
    if modules is not None:
        sets.append("modules=%s")
        params.append(json.dumps(modules, ensure_ascii=False))
    if user_preference is not None:
        sets.append("user_preference=%s")
        params.append(user_preference)
    if status is not None:
        sets.append("status=%s")
        params.append(status)
    if total_weeks is not None:
        sets.append("total_weeks=%s")
        params.append(total_weeks)
    if estimated_weeks is not None:
        sets.append("estimated_weeks=%s")
        params.append(estimated_weeks)
    if error_msg is not None:
        sets.append("error_msg=%s")
        params.append(error_msg)
    if not sets:
        return
    params.append(plan_id)
    sql = f"UPDATE learn_outlines SET {', '.join(sets)} WHERE plan_id=%s"
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, tuple(params))


async def get_outline(plan_id: str) -> dict | None:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT plan_id, user_id, assessment_id, stage_code, stage_title,
                          modules, total_weight, estimated_weeks, total_weeks,
                          user_preference, status, error_msg, created_at
                   FROM learn_outlines WHERE plan_id=%s""",
                (plan_id,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "plan_id": row[0], "user_id": row[1], "assessment_id": row[2],
        "stage_code": row[3], "stage_title": row[4],
        "modules": json.loads(row[5]) if row[5] else [],
        "total_weight": row[6], "estimated_weeks": row[7], "total_weeks": row[8],
        "user_preference": row[9], "status": row[10], "error_msg": row[11],
        "created_at": row[12].isoformat() if row[12] else None,
    }


async def get_latest_outline_for_user(user_id: int) -> dict | None:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT plan_id FROM learn_outlines
                   WHERE user_id=%s ORDER BY id DESC LIMIT 1""",
                (user_id,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    return await get_outline(row[0])


async def list_user_plans(user_id: int, assessment_id: str | None = None) -> list[dict]:
    """列出用户的所有学习计划 + 进度统计。

    返回：[{plan_id, stage_code, stage_title, status, created_at, total_weeks,
            modules_count, done_count, total_count, progress_pct, current_week_num}]
    """
    where_clauses = ["o.user_id=%s"]
    params: list = [user_id]
    if assessment_id:
        where_clauses.append("o.assessment_id=%s")
        params.append(assessment_id)
    where_sql = " AND ".join(where_clauses)

    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""SELECT o.plan_id, o.assessment_id, o.stage_code, o.stage_title,
                           o.status, o.total_weeks, o.estimated_weeks,
                           o.modules, o.created_at,
                           COALESCE(ts.total, 0) AS total,
                           COALESCE(ts.done, 0)  AS done,
                           COALESCE(ts.final_sum, 0) AS final_sum,
                           COALESCE(ts.actual_sum, 0) AS actual_sum,
                           COALESCE(cw.current_week, 1) AS current_week
                    FROM learn_outlines o
                    LEFT JOIN (
                        SELECT plan_id,
                               COUNT(*) AS total,
                               SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                               SUM(CASE WHEN status='done' THEN final_contribution ELSE 0 END) AS final_sum,
                               SUM(actual_contribution) AS actual_sum
                        FROM learn_tasks GROUP BY plan_id
                    ) ts ON ts.plan_id = o.plan_id
                    LEFT JOIN (
                        SELECT t.plan_id, MIN(w.week_num) AS current_week
                        FROM learn_tasks t
                        JOIN learn_weeks w ON w.id = t.week_id
                        WHERE t.status='pending'
                        GROUP BY t.plan_id
                    ) cw ON cw.plan_id = o.plan_id
                    WHERE {where_sql}
                    ORDER BY o.created_at DESC""",
                tuple(params),
            )
            rows = await cur.fetchall()
    out = []
    for r in rows:
        modules = json.loads(r[7]) if r[7] else []
        total = int(r[9] or 0)
        done = int(r[10] or 0)
        final_sum = float(r[11] or 0)
        actual_sum = float(r[12] or 0)
        progress_pct = round(final_sum, 2) if actual_sum > 0 else 0.0
        out.append({
            "plan_id": r[0],
            "assessment_id": r[1],
            "stage_code": r[2],
            "stage_title": r[3],
            "status": r[4],
            "total_weeks": r[5],
            "estimated_weeks": r[6],
            "modules_count": len(modules),
            "created_at": r[8].isoformat() if r[8] else None,
            "done_count": done,
            "total_count": total,
            "progress_pct": progress_pct,
            "current_week_num": int(r[13] or 1),
        })
    return out


async def delete_plan(plan_id: str) -> None:
    """级联删除一个 plan 的所有关联数据。"""
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM learn_tasks WHERE plan_id=%s", (plan_id,))
            await cur.execute("DELETE FROM learn_weeks WHERE plan_id=%s", (plan_id,))
            await cur.execute("DELETE FROM learn_months WHERE plan_id=%s", (plan_id,))
            await cur.execute("DELETE FROM learn_outlines WHERE plan_id=%s", (plan_id,))


# ------------------------------------------------------------------ #
#  months / weeks                                                      #
# ------------------------------------------------------------------ #

async def insert_months(plan_id: str, months: list[dict]) -> dict[int, int]:
    """批量插入 months，返回 {month_num: month_id} 映射。"""
    mapping: dict[int, int] = {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            for m in months:
                await cur.execute(
                    """INSERT INTO learn_months
                       (plan_id, month_num, theme, month_goal, covers_modules, weight_share)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (plan_id, m["month_num"], m["theme"], m.get("month_goal"),
                     json.dumps(m.get("covers_modules") or [], ensure_ascii=False),
                     m["weight_share"]),
                )
                await cur.execute("SELECT LAST_INSERT_ID()")
                row = await cur.fetchone()
                mapping[m["month_num"]] = int(row[0])
    return mapping


async def insert_weeks(
    plan_id: str,
    weeks: list[dict],
    month_id_map: dict[int, int],
) -> dict[int, int]:
    """批量插入 weeks，返回 {week_num: week_id} 映射。"""
    mapping: dict[int, int] = {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            for w in weeks:
                month_id = month_id_map.get(w.get("month_num", 1))
                await cur.execute(
                    """INSERT INTO learn_weeks
                       (plan_id, month_id, week_num, week_in_month, theme, week_goal,
                        covers_modules, weight_share, daily_status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'skeleton')""",
                    (plan_id, month_id, w["week_num"], w.get("week_in_month", 1),
                     w["theme"], w.get("week_goal"),
                     json.dumps(w.get("covers_modules") or [], ensure_ascii=False),
                     w["weight_share"]),
                )
                await cur.execute("SELECT LAST_INSERT_ID()")
                row = await cur.fetchone()
                mapping[w["week_num"]] = int(row[0])
    return mapping


async def get_week(plan_id: str, week_num: int) -> dict | None:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT id, month_id, week_num, week_in_month, theme, week_goal,
                          covers_modules, weight_share, daily_status, error_msg
                   FROM learn_weeks WHERE plan_id=%s AND week_num=%s""",
                (plan_id, week_num),
            )
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "month_id": row[1], "week_num": row[2], "week_in_month": row[3],
        "theme": row[4], "week_goal": row[5],
        "covers_modules": json.loads(row[6]) if row[6] else [],
        "weight_share": row[7], "daily_status": row[8], "error_msg": row[9],
    }


async def list_weeks(plan_id: str) -> list[dict]:
    """列出所有 weeks，附带每周任务完成统计（total_tasks / done_tasks）。"""
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT w.id, w.month_id, w.week_num, w.week_in_month, w.theme, w.week_goal,
                          w.covers_modules, w.weight_share, w.daily_status, w.error_msg, w.materialized_at,
                          COALESCE(stats.total, 0) AS total_tasks,
                          COALESCE(stats.done, 0) AS done_tasks
                   FROM learn_weeks w
                   LEFT JOIN (
                       SELECT week_id,
                              COUNT(*) AS total,
                              SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done
                       FROM learn_tasks
                       WHERE plan_id=%s
                       GROUP BY week_id
                   ) stats ON stats.week_id = w.id
                   WHERE w.plan_id=%s
                   ORDER BY w.week_num ASC""",
                (plan_id, plan_id),
            )
            rows = await cur.fetchall()
    return [{
        "id": r[0], "month_id": r[1], "week_num": r[2], "week_in_month": r[3],
        "theme": r[4], "week_goal": r[5],
        "covers_modules": json.loads(r[6]) if r[6] else [],
        "weight_share": r[7], "daily_status": r[8], "error_msg": r[9],
        "materialized_at": r[10].isoformat() if r[10] else None,
        "total_tasks": int(r[11] or 0),
        "done_tasks": int(r[12] or 0),
    } for r in rows]


async def list_months(plan_id: str) -> list[dict]:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT id, month_num, theme, month_goal, covers_modules, weight_share
                   FROM learn_months WHERE plan_id=%s ORDER BY month_num ASC""",
                (plan_id,),
            )
            rows = await cur.fetchall()
    return [{
        "id": r[0], "month_num": r[1], "theme": r[2], "month_goal": r[3],
        "covers_modules": json.loads(r[4]) if r[4] else [],
        "weight_share": r[5],
    } for r in rows]


async def try_claim_week_for_materialize(plan_id: str, week_num: int) -> bool:
    """原子 CAS：把 daily_status='skeleton' 改为 'materializing'。

    返回 True 表示成功抢到，调用方应该开始生成任务。
    返回 False 表示别的调用已经在处理，或已是 ready，跳过。
    """
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE learn_weeks SET daily_status='materializing'
                   WHERE plan_id=%s AND week_num=%s AND daily_status='skeleton'""",
                (plan_id, week_num),
            )
            return cur.rowcount > 0


async def mark_week_ready(plan_id: str, week_num: int) -> None:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE learn_weeks
                   SET daily_status='ready', materialized_at=CURRENT_TIMESTAMP, error_msg=NULL
                   WHERE plan_id=%s AND week_num=%s""",
                (plan_id, week_num),
            )


async def mark_week_error(plan_id: str, week_num: int, error_msg: str) -> None:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE learn_weeks
                   SET daily_status='error', error_msg=%s
                   WHERE plan_id=%s AND week_num=%s""",
                (error_msg[:500], plan_id, week_num),
            )


async def reset_week_to_skeleton(plan_id: str, week_num: int) -> None:
    """重试物化前先把状态置回 skeleton。"""
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE learn_weeks SET daily_status='skeleton', error_msg=NULL
                   WHERE plan_id=%s AND week_num=%s""",
                (plan_id, week_num),
            )
            await cur.execute(
                "DELETE FROM learn_tasks WHERE plan_id=%s AND week_id IN "
                "(SELECT id FROM learn_weeks WHERE plan_id=%s AND week_num=%s)",
                (plan_id, plan_id, week_num),
            )


# ------------------------------------------------------------------ #
#  tasks                                                               #
# ------------------------------------------------------------------ #

async def get_max_order_in_queue(plan_id: str) -> int:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT MAX(order_in_queue) FROM learn_tasks WHERE plan_id=%s",
                (plan_id,),
            )
            row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def insert_tasks(plan_id: str, week_id: int, tasks: list[dict], start_order: int) -> None:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            for i, t in enumerate(tasks):
                await cur.execute(
                    """INSERT INTO learn_tasks
                       (plan_id, week_id, module_id, order_in_queue, order_in_week,
                        title, description, task_type, est_minutes, target_dims,
                        raw_weight, actual_contribution, completion_criteria, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')""",
                    (plan_id, week_id, t.get("module_id"),
                     start_order + i + 1, t.get("order_in_week", i + 1),
                     t["title"], t.get("description"), t.get("task_type"),
                     t.get("est_minutes"),
                     json.dumps(t.get("target_dims") or [], ensure_ascii=False),
                     t.get("raw_weight"), t.get("actual_contribution"),
                     t.get("completion_criteria")),
                )


async def get_today_tasks(plan_id: str, limit: int) -> list[dict]:
    """返回"今日焦点"：今日 done + 最多 (limit - done_count) 个 pending。

    保证总数不超过 limit，避免刷新时悄悄塞入新任务。
    用户想要更多可以点"+ 新增任务"主动追加（走 /more 接口）。
    """
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 今日已完成的任务
            await cur.execute(
                """SELECT t.id, t.week_id, t.module_id, t.order_in_queue, t.order_in_week,
                          t.title, t.description, t.task_type, t.est_minutes, t.target_dims,
                          t.raw_weight, t.actual_contribution, t.completion_criteria, t.status,
                          w.week_num, w.theme
                   FROM learn_tasks t
                   JOIN learn_weeks w ON w.id = t.week_id
                   WHERE t.plan_id=%s AND t.status='done'
                     AND t.completed_at >= CURDATE()
                   ORDER BY t.completed_at DESC""",
                (plan_id,),
            )
            done_rows = await cur.fetchall()
            pending_limit = max(0, limit - len(done_rows))
            pending_rows: tuple = ()
            if pending_limit > 0:
                await cur.execute(
                    """SELECT t.id, t.week_id, t.module_id, t.order_in_queue, t.order_in_week,
                              t.title, t.description, t.task_type, t.est_minutes, t.target_dims,
                              t.raw_weight, t.actual_contribution, t.completion_criteria, t.status,
                              w.week_num, w.theme
                       FROM learn_tasks t
                       JOIN learn_weeks w ON w.id = t.week_id
                       WHERE t.plan_id=%s AND t.status='pending'
                       ORDER BY t.order_in_queue ASC LIMIT %s""",
                    (plan_id, pending_limit),
                )
                pending_rows = await cur.fetchall()
    return [_row_to_task(r) for r in pending_rows] + [_row_to_task(r) for r in done_rows]


async def get_more_tasks(plan_id: str, exclude_ids: list[int], limit: int) -> list[dict]:
    """再来一批：返回不在 exclude_ids 里的 pending 任务前 limit 个。"""
    if not exclude_ids:
        async with memory_db._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT t.id, t.week_id, t.module_id, t.order_in_queue, t.order_in_week,
                              t.title, t.description, t.task_type, t.est_minutes, t.target_dims,
                              t.raw_weight, t.actual_contribution, t.completion_criteria, t.status,
                              w.week_num, w.theme
                       FROM learn_tasks t
                       JOIN learn_weeks w ON w.id = t.week_id
                       WHERE t.plan_id=%s AND t.status='pending'
                       ORDER BY t.order_in_queue ASC LIMIT %s""",
                    (plan_id, limit),
                )
                rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]
    placeholders = ",".join(["%s"] * len(exclude_ids))
    sql = (
        f"""SELECT t.id, t.week_id, t.module_id, t.order_in_queue, t.order_in_week,
                   t.title, t.description, t.task_type, t.est_minutes, t.target_dims,
                   t.raw_weight, t.actual_contribution, t.completion_criteria, t.status,
                   w.week_num, w.theme
            FROM learn_tasks t
            JOIN learn_weeks w ON w.id = t.week_id
            WHERE t.plan_id=%s AND t.status='pending'
              AND t.id NOT IN ({placeholders})
            ORDER BY t.order_in_queue ASC LIMIT %s"""
    )
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, (plan_id, *exclude_ids, limit))
            rows = await cur.fetchall()
    return [_row_to_task(r) for r in rows]


async def get_task(task_id: int) -> dict | None:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT t.id, t.week_id, t.module_id, t.order_in_queue, t.order_in_week,
                          t.title, t.description, t.task_type, t.est_minutes, t.target_dims,
                          t.raw_weight, t.actual_contribution, t.completion_criteria, t.status,
                          w.week_num, w.theme, t.plan_id, w.id AS w_id
                   FROM learn_tasks t
                   JOIN learn_weeks w ON w.id = t.week_id
                   WHERE t.id=%s""",
                (task_id,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    d = _row_to_task(row)
    d["plan_id"] = row[16]
    return d


async def mark_task_done(
    task_id: int,
    reflection: str | None,
    grade_score: float,
    grade_comment: str,
    final_contribution: float,
) -> None:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE learn_tasks
                   SET status='done', reflection=%s, grade_score=%s, grade_comment=%s,
                       final_contribution=%s, completed_at=CURRENT_TIMESTAMP
                   WHERE id=%s""",
                (reflection, grade_score, grade_comment, final_contribution, task_id),
            )


async def count_week_pending(plan_id: str, week_id: int) -> int:
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM learn_tasks WHERE plan_id=%s AND week_id=%s AND status='pending'",
                (plan_id, week_id),
            )
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def list_recent_done_tasks(plan_id: str, days: int = 7) -> list[dict]:
    """列出最近 N 天内已完成的任务（按完成时间倒序）。"""
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT t.id, t.week_id, t.title, t.task_type,
                          t.actual_contribution, t.grade_score, t.grade_comment,
                          t.final_contribution, t.reflection, t.completed_at,
                          w.week_num, w.theme
                   FROM learn_tasks t
                   JOIN learn_weeks w ON w.id = t.week_id
                   WHERE t.plan_id=%s AND t.status='done'
                     AND t.completed_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                   ORDER BY t.completed_at DESC""",
                (plan_id, days),
            )
            rows = await cur.fetchall()
    return [{
        "id": r[0], "week_id": r[1], "title": r[2], "task_type": r[3],
        "actual_contribution": r[4], "grade_score": r[5], "grade_comment": r[6],
        "final_contribution": r[7], "reflection": r[8],
        "completed_at": r[9].isoformat() if r[9] else None,
        "week_num": r[10], "week_theme": r[11],
    } for r in rows]


async def compute_plan_progress(plan_id: str) -> dict:
    """基于 learn_tasks 计算进度统计。"""
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT status, actual_contribution, final_contribution
                   FROM learn_tasks WHERE plan_id=%s""",
                (plan_id,),
            )
            rows = await cur.fetchall()
    from agent.learn_plan_helpers import compute_progress
    tasks = [{"status": r[0], "actual_contribution": r[1], "final_contribution": r[2]} for r in rows]
    return compute_progress(tasks)


def _row_to_task(r: tuple) -> dict:
    target_dims = []
    if r[9]:
        try:
            target_dims = json.loads(r[9])
        except Exception:
            target_dims = []
    return {
        "id": r[0], "week_id": r[1], "module_id": r[2],
        "order_in_queue": r[3], "order_in_week": r[4],
        "title": r[5], "description": r[6], "task_type": r[7],
        "est_minutes": r[8], "target_dims": target_dims,
        "raw_weight": r[10], "actual_contribution": r[11],
        "completion_criteria": r[12], "status": r[13],
        "week_num": r[14], "week_theme": r[15],
    }
