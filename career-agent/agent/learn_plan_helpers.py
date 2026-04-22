"""Learn Plan 相关的纯函数 helpers。

不依赖 DB / LLM / FastAPI，纯数据转换与校验。便于单测。
"""
from __future__ import annotations

import json
import re
from typing import Any


# ------------------------------------------------------------------ #
#  参数（可被 api.py 引用）                                             #
# ------------------------------------------------------------------ #

SUGGESTED_DAILY_TASKS = 3
MIN_WEEKS = 2
MAX_WEEKS = 24
BASE_GRADE_SCORE = 0.6
REFLECTION_MIN_LEN = 10
# 当前周剩余任务 <= 该阈值时触发下一周物化（= N × 2）
MATERIALIZE_TRIGGER_THRESHOLD = SUGGESTED_DAILY_TASKS * 2


# ------------------------------------------------------------------ #
#  大纲校验与清洗                                                      #
# ------------------------------------------------------------------ #

def validate_outline(data: dict) -> dict:
    """校验 plan_outline_agent 输出，返回规范化后的大纲。

    - modules 数量校验（最少 3，最多 10）
    - 权重归一化到 100（容错 agent 输出不等于 100 的情况）
    - estimated_weeks clamp 到 [MIN_WEEKS, MAX_WEEKS]
    - 缺失字段填默认值

    失败时抛 ValueError，调用方捕获并返回 HTTP 400。
    """
    if not isinstance(data, dict):
        raise ValueError("outline 输出不是 JSON 对象")
    modules = data.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ValueError("outline.modules 缺失或为空")
    if len(modules) < 3:
        raise ValueError(f"modules 数量过少（{len(modules)}），至少 3 个")
    if len(modules) > 10:
        raise ValueError(f"modules 数量过多（{len(modules)}），最多 10 个")

    cleaned_modules = []
    for i, m in enumerate(modules):
        if not isinstance(m, dict):
            raise ValueError(f"modules[{i}] 不是对象")
        title = (m.get("title") or "").strip()
        if not title:
            raise ValueError(f"modules[{i}].title 为空")
        weight = m.get("weight")
        if not isinstance(weight, (int, float)) or weight <= 0:
            raise ValueError(f"modules[{i}].weight 不合法：{weight}")
        cleaned_modules.append({
            "id": m.get("id") or f"m{i + 1}",
            "title": title,
            "weight": float(weight),
            "est_hours": int(m.get("est_hours") or 0),
            "target_dims": list(m.get("target_dims") or []),
            "completion_criteria": (m.get("completion_criteria") or "").strip(),
        })

    # 归一化权重到 100
    total = sum(m["weight"] for m in cleaned_modules)
    if total <= 0:
        raise ValueError("所有模块权重之和为 0")
    for m in cleaned_modules:
        m["weight"] = round(m["weight"] / total * 100, 2)

    estimated_weeks = data.get("estimated_weeks")
    if not isinstance(estimated_weeks, int) or estimated_weeks < MIN_WEEKS:
        estimated_weeks = max(MIN_WEEKS, 4)
    estimated_weeks = min(MAX_WEEKS, estimated_weeks)

    return {
        "modules": cleaned_modules,
        "total_weight": 100.0,
        "estimated_weeks": estimated_weeks,
        "reasoning": (data.get("reasoning") or "").strip(),
    }


# ------------------------------------------------------------------ #
#  Roadmap 校验                                                        #
# ------------------------------------------------------------------ #

def validate_roadmap(data: dict, outline_module_ids: set[str]) -> dict:
    """校验 plan_roadmap_agent 输出。

    校验：
    - total_weeks 在 [MIN, MAX]
    - weeks/months 非空
    - weeks 的 weight_share 之和 ≈ 100（归一化）
    - months 的 weight_share 之和 ≈ 100（归一化）
    - covers_modules 引用的 module_id 必须在大纲中

    返回清洗后的字典。
    """
    if not isinstance(data, dict):
        raise ValueError("roadmap 输出不是 JSON 对象")
    total_weeks = data.get("total_weeks")
    if not isinstance(total_weeks, int) or total_weeks < MIN_WEEKS:
        raise ValueError(f"total_weeks 不合法：{total_weeks}")
    total_weeks = min(MAX_WEEKS, total_weeks)

    months_raw = data.get("months") or []
    weeks_raw = data.get("weeks") or []
    if not isinstance(months_raw, list) or not months_raw:
        raise ValueError("roadmap.months 缺失")
    if not isinstance(weeks_raw, list) or not weeks_raw:
        raise ValueError("roadmap.weeks 缺失")
    if len(weeks_raw) != total_weeks:
        # 以 weeks 的实际长度为准
        total_weeks = len(weeks_raw)

    months = []
    for i, m in enumerate(months_raw):
        if not isinstance(m, dict):
            raise ValueError(f"months[{i}] 不是对象")
        theme = (m.get("theme") or "").strip()
        if not theme:
            raise ValueError(f"months[{i}].theme 为空")
        months.append({
            "month_num": int(m.get("month_num") or (i + 1)),
            "theme": theme,
            "month_goal": (m.get("month_goal") or "").strip(),
            "covers_modules": _clean_covers(m.get("covers_modules"), outline_module_ids),
            "weight_share": float(m.get("weight_share") or 0),
        })

    weeks = []
    for i, w in enumerate(weeks_raw):
        if not isinstance(w, dict):
            raise ValueError(f"weeks[{i}] 不是对象")
        theme = (w.get("theme") or "").strip()
        if not theme:
            raise ValueError(f"weeks[{i}].theme 为空")
        weeks.append({
            "week_num": int(w.get("week_num") or (i + 1)),
            "week_in_month": int(w.get("week_in_month") or 1),
            "month_num": int(w.get("month_num") or 1),
            "theme": theme,
            "week_goal": (w.get("week_goal") or "").strip(),
            "covers_modules": _clean_covers(w.get("covers_modules"), outline_module_ids),
            "weight_share": float(w.get("weight_share") or 0),
        })

    # 归一化 weight_share 使总和 = 100（weeks 与 months 分别独立）
    _normalize_weight_share(weeks)
    _normalize_weight_share(months)

    return {
        "total_weeks": total_weeks,
        "months": months,
        "weeks": weeks,
    }


def _clean_covers(raw: Any, valid_ids: set[str]) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        mid = c.get("module_id")
        share = c.get("share")
        if not mid or mid not in valid_ids:
            continue
        try:
            share_f = max(0.0, min(1.0, float(share)))
        except (TypeError, ValueError):
            share_f = 0.0
        if share_f > 0:
            out.append({"module_id": mid, "share": share_f})
    return out


def _normalize_weight_share(items: list[dict]) -> None:
    """就地把 items 里的 weight_share 归一化到总和 100。"""
    total = sum(i.get("weight_share") or 0 for i in items)
    if total <= 0:
        # 均分
        if items:
            share = round(100 / len(items), 2)
            for i in items:
                i["weight_share"] = share
        return
    for i in items:
        i["weight_share"] = round((i.get("weight_share") or 0) / total * 100, 2)


# ------------------------------------------------------------------ #
#  任务权重归一化                                                      #
# ------------------------------------------------------------------ #

def normalize_task_contributions(
    tasks: list[dict],
    week_weight_share: float,
) -> list[dict]:
    """把 agent 输出的 raw_weight 归一化为全局 actual_contribution。

    公式：actual_contribution = (raw_weight / Σraw) × week_weight_share

    结果：所有任务的 actual_contribution 之和 = week_weight_share。
    所有周加起来 = 100（如果 roadmap 归一化过）。

    - tasks 应含 raw_weight 字段
    - week_weight_share 是本周占全局 100 的百分比
    - 若 raw_weight 缺失或为 0，均分剩余权重
    """
    if not tasks:
        return []
    raw_sum = 0.0
    for t in tasks:
        raw = t.get("raw_weight")
        t["raw_weight"] = float(raw) if isinstance(raw, (int, float)) and raw > 0 else 0.0
        raw_sum += t["raw_weight"]
    if raw_sum <= 0:
        # 全部均分
        per_task = week_weight_share / len(tasks)
        for t in tasks:
            t["actual_contribution"] = round(per_task, 3)
        return tasks
    for t in tasks:
        t["actual_contribution"] = round(
            (t["raw_weight"] / raw_sum) * week_weight_share, 3
        )
    return tasks


def validate_daily_tasks(data: dict, week_num: int) -> list[dict]:
    """校验 plan_daily_agent 输出，返回清洗后的 tasks 列表。"""
    if not isinstance(data, dict):
        raise ValueError("daily 输出不是 JSON 对象")
    tasks_raw = data.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError(f"week_num={week_num} 的 tasks 为空")

    valid_types = {"reading", "coding", "project", "exercise", "review"}
    cleaned = []
    for i, t in enumerate(tasks_raw):
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "").strip()
        if not title:
            continue
        task_type = t.get("task_type") or "exercise"
        if task_type not in valid_types:
            task_type = "exercise"
        raw_w = t.get("raw_weight")
        cleaned.append({
            "order_in_week": int(t.get("order") or (i + 1)),
            "title": title,
            "description": (t.get("description") or "").strip() or None,
            "task_type": task_type,
            "est_minutes": max(5, min(240, int(t.get("est_minutes") or 30))),
            "target_dims": list(t.get("target_dims") or []),
            "raw_weight": float(raw_w) if isinstance(raw_w, (int, float)) and raw_w > 0 else 1.0,
            "completion_criteria": (t.get("completion_criteria") or "").strip() or None,
        })
    if not cleaned:
        raise ValueError(f"week_num={week_num} 所有任务均非法")
    return cleaned


# ------------------------------------------------------------------ #
#  打分规则                                                            #
# ------------------------------------------------------------------ #

def apply_default_grade(reflection: str | None) -> tuple[float, str]:
    """当用户不写感悟或感悟过短时，走默认打分逻辑。

    默认给满分，不惩罚未填写感悟的用户；感悟是可选的自我记录工具。
    返回：(score, comment)。
    """
    return 1.0, "任务已完成"


def should_invoke_grader(reflection: str | None) -> bool:
    if not reflection:
        return False
    return len(reflection.strip()) >= REFLECTION_MIN_LEN


def clamp_grade(score: Any) -> float:
    """把 agent 输出的 score 安全 clamp 到 [BASE_GRADE_SCORE, 1.0]。"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return BASE_GRADE_SCORE
    if s < BASE_GRADE_SCORE:
        return BASE_GRADE_SCORE
    if s > 1.0:
        return 1.0
    return round(s, 3)


# ------------------------------------------------------------------ #
#  物化触发判断                                                        #
# ------------------------------------------------------------------ #

def should_materialize_next_week(
    current_week_pending: int,
    next_week_daily_status: str | None,
) -> bool:
    """判断是否应触发下一周的 daily 任务物化。

    条件：
    - 下一周存在（status 非 None）
    - 下一周仍是 skeleton（未物化过，幂等保证由 DB CAS 做）
    - 当前周 pending 数量 <= 阈值（触发点 = 倒数 2 天）
    """
    if next_week_daily_status != "skeleton":
        return False
    return current_week_pending <= MATERIALIZE_TRIGGER_THRESHOLD


# ------------------------------------------------------------------ #
#  进度计算                                                            #
# ------------------------------------------------------------------ #

def compute_progress(tasks_contributions: list[dict]) -> dict:
    """基于任务列表计算当前进度百分比。

    输入：[{status, actual_contribution, final_contribution}, ...]
    返回：{total_pct, potential_pct, done_count, total_count}
      total_pct: 已得贡献之和（final_contribution 或 actual_contribution fallback）
      potential_pct: 如果全部以满分完成的总贡献（= Σ actual_contribution）
    """
    total = 0.0
    potential = 0.0
    done = 0
    for t in tasks_contributions:
        ac = float(t.get("actual_contribution") or 0)
        potential += ac
        if t.get("status") == "done":
            done += 1
            fc = t.get("final_contribution")
            if fc is None:
                total += ac
            else:
                total += float(fc)
    return {
        "total_pct": round(total, 2),
        "potential_pct": round(potential, 2),
        "done_count": done,
        "total_count": len(tasks_contributions),
    }


# ------------------------------------------------------------------ #
#  JSON 解析（容错 markdown 包裹）                                      #
# ------------------------------------------------------------------ #

def extract_json(raw: str) -> dict:
    """从 LLM 输出提取 JSON。容错 ```json ...``` / trailing comma / 前后多余文字。

    解析失败抛 ValueError。
    """
    if not isinstance(raw, str):
        raise ValueError("LLM 输出不是字符串")
    s = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if match:
        s = match.group(1).strip()

    # 第一次尝试原样
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 去掉 trailing comma
    s2 = re.sub(r",\s*([\]}])", r"\1", s)
    try:
        return json.loads(s2)
    except json.JSONDecodeError:
        pass

    # 尝试截取首个 { ... } 块
    first = s.find("{")
    last = s.rfind("}")
    if first >= 0 and last > first:
        try:
            return json.loads(s[first:last + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"LLM 输出无法解析为 JSON：{s[:200]}")
