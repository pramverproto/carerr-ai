"""
action_plan.py 单元测试。

覆盖内容：
1. _validate_and_fix  正常输入通过校验
2. _validate_and_fix  phases 数量错误
3. _validate_and_fix  缺少 phase 字段
4. _validate_and_fix  action 缺少字段
5. _validate_and_fix  自动修复 block_id / phase_3 固定字段
6. _validate_and_fix  phase_3 必须包含"面试话术准备"和"求职市场激活"
"""

import os
import sys

import pytest

os.environ.setdefault("LLM_MODEL",    "gpt-4o-mini")
os.environ.setdefault("LLM_API_KEY",  "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://localhost:9999")
os.environ.setdefault("DB_HOST",      "localhost")
os.environ.setdefault("DB_USER",      "test")
os.environ.setdefault("DB_PASSWORD",  "test")
os.environ.setdefault("DB_NAME",      "test")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from agent.tools.action_plan import _validate_and_fix


# ──────────────────────────────────────────────────────────────────────
# Helper: 构造合法的 action_plan block
# ──────────────────────────────────────────────────────────────────────

def _make_action(
    item="财务分析", severity="high",
    action="系统学习财务分析课程",
    deliverable="完成6周课程并输出分析报告",
    resource="Coursera《Wharton Business Foundations》"
) -> dict:
    return {
        "item": item,
        "severity": severity,
        "action": action,
        "deliverable": deliverable,
        "resource": resource,
    }


def _make_valid_block() -> dict:
    return {
        "block_id": "action_plan",
        "phases": [
            {
                "phase_id": "phase_1",
                "label": "0-3个月：补核心短板",
                "focus": "快速补足高优先级能力差距",
                "actions": [
                    _make_action("财务分析", "high"),
                    _make_action("项目管理", "high"),
                ]
            },
            {
                "phase_id": "phase_2",
                "label": "3-6个月：实战积累",
                "focus": "中优先级差距 + 实战积累",
                "actions": [
                    _make_action("数据可视化", "medium"),
                    _make_action("跨部门协作", "medium"),
                ]
            },
            {
                "phase_id": "phase_3",
                "label": "6-12个月：求职激活",
                "focus": "面试准备与求职市场激活",
                "actions": [
                    _make_action("面试话术准备", None, "用STAR整理5-8个案例",
                                 "完成面试故事文档", "glassdoor；《面试圣经》"),
                    _make_action("求职市场激活", None, "优化简历和LinkedIn",
                                 "投递20家目标公司", "LinkedIn；Boss直聘"),
                ]
            },
        ]
    }


# ══════════════════════════════════════════════════════════════════════
#  测试用例
# ══════════════════════════════════════════════════════════════════════

class TestValidateAndFix:

    def test_valid_block_passes(self):
        block = _make_valid_block()
        is_valid, err, fixed = _validate_and_fix(block)
        assert is_valid, f"合法 block 应通过校验，实际错误：{err}"

    def test_wrong_block_id_is_fixed(self):
        block = _make_valid_block()
        block["block_id"] = "wrong_id"
        is_valid, _, fixed = _validate_and_fix(block)
        assert fixed["block_id"] == "action_plan"

    def test_missing_phase_returns_error(self):
        """phases 少于 3 个时应返回错误。"""
        block = _make_valid_block()
        block["phases"] = block["phases"][:2]
        is_valid, err, _ = _validate_and_fix(block)
        assert not is_valid
        assert "3" in err

    def test_extra_phase_returns_error(self):
        """phases 超过 3 个时应返回错误。"""
        block = _make_valid_block()
        block["phases"].append(block["phases"][0].copy())
        is_valid, err, _ = _validate_and_fix(block)
        assert not is_valid

    def test_missing_phase_key_returns_error(self):
        """phase 缺少 focus 字段时应返回错误。"""
        block = _make_valid_block()
        del block["phases"][0]["focus"]
        is_valid, err, _ = _validate_and_fix(block)
        assert not is_valid
        assert "focus" in err

    def test_empty_actions_returns_error(self):
        """phase 的 actions 为空时应返回错误。"""
        block = _make_valid_block()
        block["phases"][1]["actions"] = []
        is_valid, err, _ = _validate_and_fix(block)
        assert not is_valid
        assert "actions" in err

    def test_missing_action_key_returns_error(self):
        """action 缺少 deliverable 时应返回错误。"""
        block = _make_valid_block()
        del block["phases"][0]["actions"][0]["deliverable"]
        is_valid, err, _ = _validate_and_fix(block)
        assert not is_valid
        assert "deliverable" in err

    def test_empty_action_field_returns_error(self):
        """action 的 action 字段为空字符串时应返回错误。"""
        block = _make_valid_block()
        block["phases"][0]["actions"][0]["action"] = ""
        is_valid, err, _ = _validate_and_fix(block)
        assert not is_valid

    def test_phase_ids_are_fixed(self):
        """phase_id 被自动修复为 phase_1/2/3。"""
        block = _make_valid_block()
        block["phases"][0]["phase_id"] = "wrong_id_1"
        block["phases"][1]["phase_id"] = "wrong_id_2"
        is_valid, _, fixed = _validate_and_fix(block)
        assert is_valid
        assert fixed["phases"][0]["phase_id"] == "phase_1"
        assert fixed["phases"][1]["phase_id"] == "phase_2"
        assert fixed["phases"][2]["phase_id"] == "phase_3"

    def test_phase_labels_are_fixed(self):
        """phase label 被自动修复为预定义值。"""
        block = _make_valid_block()
        block["phases"][0]["label"] = "自定义标签"
        _, _, fixed = _validate_and_fix(block)
        assert fixed["phases"][0]["label"] == "0-3个月：补核心短板"
        assert fixed["phases"][1]["label"] == "3-6个月：实战积累"
        assert fixed["phases"][2]["label"] == "6-12个月：求职激活"

    def test_phase3_first_action_item_fixed(self):
        """phase_3 第一个 action 的 item 被强制修复为'面试话术准备'。"""
        block = _make_valid_block()
        block["phases"][2]["actions"][0]["item"] = "随便什么内容"
        _, _, fixed = _validate_and_fix(block)
        assert fixed["phases"][2]["actions"][0]["item"] == "面试话术准备"

    def test_phase3_focus_is_fixed(self):
        """phase_3 的 focus 被强制为'面试准备与求职市场激活'。"""
        block = _make_valid_block()
        block["phases"][2]["focus"] = "错误的focus"
        _, _, fixed = _validate_and_fix(block)
        assert fixed["phases"][2]["focus"] == "面试准备与求职市场激活"

    def test_severity_can_be_none(self):
        """severity 允许为 None（phase_3 固定 action）。"""
        block = _make_valid_block()
        block["phases"][2]["actions"][0]["severity"] = None
        is_valid, err, _ = _validate_and_fix(block)
        assert is_valid, f"severity=None 应该合法，实际错误：{err}"

    def test_all_actions_retain_content(self):
        """校验通过后，原始 action/deliverable/resource 内容不被清空。"""
        block = _make_valid_block()
        original_action = block["phases"][0]["actions"][0]["action"]
        _, _, fixed = _validate_and_fix(block)
        assert fixed["phases"][0]["actions"][0]["action"] == original_action


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
