"""learn_plan_helpers 纯函数单元测试。

运行：cd career-agent && uv run --no-sync python -m pytest tests/test_learn_plan_helpers.py -v
或：  cd career-agent && uv run --no-sync python tests/test_learn_plan_helpers.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.learn_plan_helpers import (
    apply_default_grade,
    clamp_grade,
    compute_progress,
    extract_json,
    normalize_task_contributions,
    should_invoke_grader,
    should_materialize_next_week,
    validate_outline,
    validate_roadmap,
    validate_daily_tasks,
    BASE_GRADE_SCORE,
    MATERIALIZE_TRIGGER_THRESHOLD,
    MIN_WEEKS,
    MAX_WEEKS,
)


# ------------------------------------------------------------------ #
#  extract_json                                                        #
# ------------------------------------------------------------------ #

class TestExtractJson:
    def test_plain_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_markdown_json_fence(self):
        raw = "```json\n{\"modules\": [1,2]}\n```"
        assert extract_json(raw) == {"modules": [1, 2]}

    def test_markdown_generic_fence(self):
        raw = "```\n{\"x\": true}\n```"
        assert extract_json(raw) == {"x": True}

    def test_trailing_comma(self):
        raw = '{"a": [1, 2, 3,], "b": 4,}'
        assert extract_json(raw) == {"a": [1, 2, 3], "b": 4}

    def test_with_surrounding_text(self):
        raw = "Sure, here you go:\n{\"ok\": 1}\nThat's it."
        assert extract_json(raw) == {"ok": 1}

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            extract_json("not json at all")


# ------------------------------------------------------------------ #
#  validate_outline                                                    #
# ------------------------------------------------------------------ #

class TestValidateOutline:
    def _sample(self, weights=None):
        weights = weights or [20, 20, 20, 20, 20]
        return {
            "modules": [
                {
                    "id": f"m{i + 1}",
                    "title": f"模块 {i + 1}",
                    "weight": w,
                    "est_hours": 15,
                    "target_dims": ["skills.2.1"],
                    "completion_criteria": "通过测试",
                }
                for i, w in enumerate(weights)
            ],
            "total_weight": sum(weights),
            "estimated_weeks": 8,
        }

    def test_valid_outline(self):
        out = validate_outline(self._sample())
        assert out["total_weight"] == 100.0
        assert len(out["modules"]) == 5
        assert out["estimated_weeks"] == 8

    def test_weights_normalized_to_100(self):
        # agent 给出不加和到 100 的权重
        out = validate_outline(self._sample(weights=[10, 20, 30, 40, 50]))
        assert abs(sum(m["weight"] for m in out["modules"]) - 100) < 0.1

    def test_estimated_weeks_clamped(self):
        data = self._sample()
        data["estimated_weeks"] = 100
        out = validate_outline(data)
        assert out["estimated_weeks"] == MAX_WEEKS

    def test_estimated_weeks_floor(self):
        data = self._sample()
        data["estimated_weeks"] = 1
        out = validate_outline(data)
        assert out["estimated_weeks"] >= MIN_WEEKS

    def test_too_few_modules(self):
        data = {"modules": [{"id": "m1", "title": "x", "weight": 100}], "estimated_weeks": 4}
        with pytest.raises(ValueError, match="数量过少"):
            validate_outline(data)

    def test_too_many_modules(self):
        data = self._sample(weights=[10] * 11)
        with pytest.raises(ValueError, match="数量过多"):
            validate_outline(data)

    def test_missing_title(self):
        data = self._sample()
        data["modules"][0]["title"] = ""
        with pytest.raises(ValueError, match="title"):
            validate_outline(data)

    def test_zero_weight(self):
        data = self._sample()
        data["modules"][0]["weight"] = 0
        with pytest.raises(ValueError, match="weight"):
            validate_outline(data)

    def test_not_dict(self):
        with pytest.raises(ValueError):
            validate_outline("not a dict")


# ------------------------------------------------------------------ #
#  validate_roadmap                                                    #
# ------------------------------------------------------------------ #

class TestValidateRoadmap:
    def _sample(self, total_weeks=4):
        weeks = []
        for i in range(total_weeks):
            weeks.append({
                "week_num": i + 1,
                "week_in_month": (i % 4) + 1,
                "month_num": (i // 4) + 1,
                "theme": f"第 {i + 1} 周主题",
                "week_goal": "goal",
                "covers_modules": [{"module_id": "m1", "share": 0.25}],
                "weight_share": 25.0,
            })
        return {
            "total_weeks": total_weeks,
            "months": [{
                "month_num": 1,
                "theme": "第 1 月",
                "month_goal": "goal",
                "covers_modules": [{"module_id": "m1", "share": 1.0}],
                "weight_share": 100,
            }],
            "weeks": weeks,
        }

    def test_valid(self):
        out = validate_roadmap(self._sample(), {"m1", "m2"})
        assert out["total_weeks"] == 4
        assert len(out["weeks"]) == 4
        assert abs(sum(w["weight_share"] for w in out["weeks"]) - 100) < 0.1

    def test_weeks_share_normalized(self):
        data = self._sample()
        # 故意让 weeks 的 share 不为 100
        for w in data["weeks"]:
            w["weight_share"] = 10  # 总和 40
        out = validate_roadmap(data, {"m1"})
        assert abs(sum(w["weight_share"] for w in out["weeks"]) - 100) < 0.1

    def test_covers_filters_invalid_module_id(self):
        data = self._sample()
        data["weeks"][0]["covers_modules"] = [
            {"module_id": "m1", "share": 0.5},
            {"module_id": "unknown", "share": 0.5},  # 应被过滤
        ]
        out = validate_roadmap(data, {"m1"})
        assert len(out["weeks"][0]["covers_modules"]) == 1
        assert out["weeks"][0]["covers_modules"][0]["module_id"] == "m1"

    def test_missing_weeks(self):
        data = {"total_weeks": 4, "months": [{"theme": "x"}], "weeks": []}
        with pytest.raises(ValueError, match="weeks"):
            validate_roadmap(data, {"m1"})

    def test_total_weeks_clamped(self):
        data = self._sample(total_weeks=4)
        data["total_weeks"] = 999
        out = validate_roadmap(data, {"m1"})
        # total_weeks 取 weeks 实际长度（因为 weeks 只有 4 个）
        assert out["total_weeks"] == 4

    def test_total_weeks_too_small(self):
        data = self._sample(total_weeks=4)
        data["total_weeks"] = 0
        with pytest.raises(ValueError, match="total_weeks"):
            validate_roadmap(data, {"m1"})


# ------------------------------------------------------------------ #
#  normalize_task_contributions                                        #
# ------------------------------------------------------------------ #

class TestNormalizeTaskContributions:
    def test_sum_equals_week_share(self):
        tasks = [
            {"raw_weight": 5},
            {"raw_weight": 10},
            {"raw_weight": 5},
        ]
        out = normalize_task_contributions(tasks, week_weight_share=10.0)
        total = sum(t["actual_contribution"] for t in out)
        assert abs(total - 10.0) < 0.01

    def test_proportional(self):
        tasks = [{"raw_weight": 1}, {"raw_weight": 3}]  # 1:3
        out = normalize_task_contributions(tasks, week_weight_share=8.0)
        assert abs(out[0]["actual_contribution"] - 2.0) < 0.01
        assert abs(out[1]["actual_contribution"] - 6.0) < 0.01

    def test_zero_raw_weights_fallback_equal_split(self):
        tasks = [{"raw_weight": 0}, {"raw_weight": 0}, {"raw_weight": 0}]
        out = normalize_task_contributions(tasks, week_weight_share=9.0)
        for t in out:
            assert abs(t["actual_contribution"] - 3.0) < 0.01

    def test_missing_raw_weights_treated_as_zero(self):
        tasks = [{}, {}]
        out = normalize_task_contributions(tasks, week_weight_share=10.0)
        for t in out:
            assert abs(t["actual_contribution"] - 5.0) < 0.01

    def test_empty_tasks(self):
        assert normalize_task_contributions([], 10.0) == []


# ------------------------------------------------------------------ #
#  validate_daily_tasks                                                #
# ------------------------------------------------------------------ #

class TestValidateDailyTasks:
    def test_valid(self):
        data = {
            "week_num": 1,
            "tasks": [
                {
                    "order": 1,
                    "title": "任务 1",
                    "description": "desc",
                    "task_type": "reading",
                    "est_minutes": 30,
                    "target_dims": ["skills.2.1"],
                    "raw_weight": 5,
                    "completion_criteria": "做完",
                },
            ],
        }
        out = validate_daily_tasks(data, week_num=1)
        assert len(out) == 1
        assert out[0]["task_type"] == "reading"

    def test_invalid_type_fallback(self):
        data = {"tasks": [{"title": "t", "task_type": "invalid", "raw_weight": 1}]}
        out = validate_daily_tasks(data, week_num=1)
        assert out[0]["task_type"] == "exercise"

    def test_empty_title_filtered(self):
        data = {"tasks": [{"title": "", "raw_weight": 1}, {"title": "ok", "raw_weight": 1}]}
        out = validate_daily_tasks(data, week_num=1)
        assert len(out) == 1
        assert out[0]["title"] == "ok"

    def test_est_minutes_clamped(self):
        data = {"tasks": [{"title": "t", "est_minutes": 1000}]}
        out = validate_daily_tasks(data, week_num=1)
        assert out[0]["est_minutes"] == 240

    def test_no_tasks_raises(self):
        with pytest.raises(ValueError):
            validate_daily_tasks({"tasks": []}, week_num=1)


# ------------------------------------------------------------------ #
#  打分相关                                                             #
# ------------------------------------------------------------------ #

class TestGrading:
    def test_default_grade_for_empty(self):
        # 默认给满分（不惩罚未填感悟）
        score, _ = apply_default_grade(None)
        assert score == 1.0

    def test_should_invoke_for_long(self):
        assert should_invoke_grader("这是一个足够长的感悟，超过十个字符") is True

    def test_should_not_invoke_for_short(self):
        assert should_invoke_grader("短") is False
        assert should_invoke_grader("") is False
        assert should_invoke_grader(None) is False
        assert should_invoke_grader("   \n  ") is False

    def test_clamp_normal(self):
        assert clamp_grade(0.85) == 0.85

    def test_clamp_below_base(self):
        assert clamp_grade(0.3) == BASE_GRADE_SCORE

    def test_clamp_above_1(self):
        assert clamp_grade(1.5) == 1.0

    def test_clamp_invalid(self):
        assert clamp_grade("abc") == BASE_GRADE_SCORE
        assert clamp_grade(None) == BASE_GRADE_SCORE


# ------------------------------------------------------------------ #
#  物化触发                                                             #
# ------------------------------------------------------------------ #

class TestMaterializeTrigger:
    def test_trigger_when_pending_low(self):
        assert should_materialize_next_week(
            current_week_pending=MATERIALIZE_TRIGGER_THRESHOLD,
            next_week_daily_status="skeleton",
        ) is True

    def test_no_trigger_when_pending_high(self):
        assert should_materialize_next_week(
            current_week_pending=MATERIALIZE_TRIGGER_THRESHOLD + 1,
            next_week_daily_status="skeleton",
        ) is False

    def test_no_trigger_when_already_ready(self):
        assert should_materialize_next_week(
            current_week_pending=1,
            next_week_daily_status="ready",
        ) is False

    def test_no_trigger_when_no_next_week(self):
        assert should_materialize_next_week(
            current_week_pending=0,
            next_week_daily_status=None,
        ) is False

    def test_no_trigger_when_materializing(self):
        assert should_materialize_next_week(
            current_week_pending=0,
            next_week_daily_status="materializing",
        ) is False


# ------------------------------------------------------------------ #
#  进度计算                                                             #
# ------------------------------------------------------------------ #

class TestComputeProgress:
    def test_empty(self):
        r = compute_progress([])
        assert r["total_pct"] == 0
        assert r["potential_pct"] == 0
        assert r["done_count"] == 0

    def test_all_pending(self):
        tasks = [
            {"status": "pending", "actual_contribution": 5},
            {"status": "pending", "actual_contribution": 3},
        ]
        r = compute_progress(tasks)
        assert r["total_pct"] == 0
        assert r["potential_pct"] == 8
        assert r["done_count"] == 0
        assert r["total_count"] == 2

    def test_partial_done_with_final(self):
        tasks = [
            {"status": "done", "actual_contribution": 5, "final_contribution": 4.25},
            {"status": "pending", "actual_contribution": 3},
        ]
        r = compute_progress(tasks)
        assert r["total_pct"] == 4.25
        assert r["done_count"] == 1

    def test_done_fallback_to_actual_when_no_final(self):
        tasks = [{"status": "done", "actual_contribution": 5, "final_contribution": None}]
        r = compute_progress(tasks)
        assert r["total_pct"] == 5


# ------------------------------------------------------------------ #
#  stand-alone runner                                                  #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
