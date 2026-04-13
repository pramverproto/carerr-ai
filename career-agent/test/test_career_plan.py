"""
test_career_plan.py

测试 generate_career_plan 和 generate_action_plan 工具。

分三个测试层：
  1. unit   — 不调用 LLM/Qdrant，验证 Gap 计算、格式校验、gap_context 构建
  2. plan   — 只跑 generate_career_plan（Block 1/2/3/5），需要真实 DB + Qdrant + LLM
  3. full   — 完整流程（generate_career_plan + generate_action_plan），需要真实环境

运行方式：
  # 单元测试（不消耗 API）
  python test/test_career_plan.py unit

  # 只测 Block 1/2/3/5 生成
  python test/test_career_plan.py plan <assessment_id> <onetsoc_code>

  # 完整流程测试
  python test/test_career_plan.py full <assessment_id> <onetsoc_code>
"""

import sys
import asyncio
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools.career_plan import (
    _gap_status,
    _extract_sub_scores,
    _load_assessment,
    _load_onet_occupation,
)
from agent.tools.action_plan import (
    _validate_and_fix,
    generate_action_plan,
)
from agent.tools.career_plan import generate_career_plan
from agent.agent_config import DB_CONFIG
import agent.memory.db as memory_db


# ------------------------------------------------------------------ #
#  1. 单元测试
# ------------------------------------------------------------------ #

def test_gap_status():
    """验证 Gap 判定逻辑。"""
    print("\n[Unit] test_gap_status")
    assert _gap_status(-1.5) == "达标",      "负值应为达标"
    assert _gap_status(0.0)  == "达标",      "0应为达标"
    assert _gap_status(0.5)  == "接近达标",  "0.5应为接近达标"
    assert _gap_status(1.0)  == "接近达标",  "1.0应为接近达标"
    assert _gap_status(1.1)  == "明显Gap",   "1.1应为明显Gap"
    assert _gap_status(3.0)  == "明显Gap",   "3.0应为明显Gap"
    print("  ✅ PASS")


def test_validate_and_fix():
    """验证 action_plan 格式校验和自动修复。"""
    print("\n[Unit] test_validate_and_fix")

    # 合法 block（phases 完整）
    valid_block = {
        "block_id": "action_plan",
        "phases": [
            {
                "phase_id": "phase_1",
                "label": "0-3个月：补核心短板",
                "focus": "补商业知识",
                "actions": [
                    {
                        "item": "商业与管理知识",
                        "gap_value": 1.70,
                        "action": "完成网课",
                        "deliverable": "输出报告",
                        "resource": "Coursera",
                    }
                ],
            },
            {
                "phase_id": "phase_2",
                "label": "3-6个月：积累岗位经验",
                "focus": "补工具",
                "actions": [
                    {
                        "item": "Tableau",
                        "gap_value": None,
                        "action": "完成教程",
                        "deliverable": "发布Dashboard",
                        "resource": "Tableau官方教程",
                    }
                ],
            },
            {
                "phase_id": "phase_3",
                "label": "6-12个月：包装简历匹配市场语言",
                "focus": "提升覆盖率",
                "actions": [
                    {
                        "item": "关键词优化",  # 将被代码覆盖
                        "gap_value": None,
                        "action": "改写简历",
                        "deliverable": "覆盖率85%",  # 将被代码覆盖
                        "resource": "STAR法则",
                    }
                ],
            },
        ],
    }
    is_valid, err, fixed = _validate_and_fix(valid_block, current_keyword_coverage=68)
    assert is_valid, f"合法 block 应通过校验，错误：{err}"
    # 验证 phase_3 的固定字段被正确覆盖
    p3_action = fixed["phases"][2]["actions"][0]
    assert p3_action["item"] == "简历关键词覆盖率优化", "phase_3 item 应被修复"
    assert "68%" in p3_action["deliverable"], "phase_3 deliverable 应包含当前覆盖率"
    print(f"  phase_3 deliverable: {p3_action['deliverable']}")
    print("  ✅ PASS")

    # phases 数量不对
    bad_block = {"block_id": "action_plan", "phases": [valid_block["phases"][0]]}
    is_valid, err, _ = _validate_and_fix(bad_block, 68)
    assert not is_valid, "phases 数量不对应校验失败"
    print(f"  phases 数量错误检测：{err}  ✅")

    # 缺少 action 字段
    missing_field_block = json.loads(json.dumps(valid_block))  # deep copy
    del missing_field_block["phases"][0]["actions"][0]["resource"]
    is_valid, err, _ = _validate_and_fix(missing_field_block, 68)
    assert not is_valid, "缺少字段应校验失败"
    print(f"  缺少字段检测：{err}  ✅")

    print("  ✅ PASS（所有校验场景）")


def run_unit_tests():
    test_gap_status()
    test_validate_and_fix()
    print("\n✅ 所有单元测试通过")


# ------------------------------------------------------------------ #
#  2. Block 1/2/3/5 生成测试
# ------------------------------------------------------------------ #

async def test_plan(assessment_id: str, onetsoc_code: str):
    """测试 generate_career_plan 工具（Block 1/2/3/5）。"""
    print(f"\n[Plan] 开始测试  assessment_id={assessment_id}  onetsoc_code={onetsoc_code}")
    await memory_db.init_pool(**DB_CONFIG)

    try:
        result_str = await generate_career_plan(assessment_id, onetsoc_code)
        result = json.loads(result_str)

        if "error" in result:
            print(f"  ❌ 错误：{result['error']}")
            return

        blocks = result.get("blocks", {})
        print(f"\n  生成了 {len(blocks)} 个 Block：{list(blocks.keys())}")

        # Block 1
        b1 = blocks.get("gap_overview", {})
        print(f"\n  [Block 1] 职业：{b1.get('occupation_title')}  匹配度：{b1.get('overall_match_score')}%")
        for dim in b1.get("dim_comparison", []):
            print(f"    {dim['label']}: 候选人={dim['candidate_score']} O*NET要求={dim['onet_required']} Gap={dim['gap']} [{dim['status']}]")
        print(f"  总评：{b1.get('summary_prose', '')[:80]}...")

        # Block 2
        b2 = blocks.get("gap_detail", {})
        print(f"\n  [Block 2] 子维度数量：{len(b2.get('sub_dim_gaps', []))}")
        print(f"  Top 3 优先Gap：")
        for g in b2.get("top3_priority_gaps", []):
            print(f"    {g['name']}  gap={g['gap']}  priority={g['priority_score']}")

        # Block 3
        b3 = blocks.get("jd_supplement", {})
        print(f"\n  [Block 3] JD样本数：{b3.get('jd_sample_count')}  关键词覆盖率：{b3.get('keyword_coverage_pct')}%")
        print(f"  高频技能词（前5）：")
        for s in b3.get("high_freq_skills", [])[:5]:
            print(f"    {s['skill']} ({s['freq_pct']}%) in_candidate={s['in_candidate']} gap_type={s['gap_type']}")

        # Block 5
        b5 = blocks.get("resume_advice", {})
        print(f"\n  [Block 5] 改写建议数：{len(b5.get('rewrite_suggestions', []))}")
        for rw in b5.get("rewrite_suggestions", [])[:2]:
            print(f"    原文：{rw['original'][:40]}...")
            print(f"    改写：{rw['suggested'][:60]}...")

        # gap_context
        gap_ctx = result.get("gap_context", {})
        print(f"\n  gap_context priority_gaps 数量：{len(gap_ctx.get('priority_gaps', []))}")
        print(f"  gap_context jd_tool_gaps 数量：{len(gap_ctx.get('jd_tool_gaps', []))}")

        print("\n✅ Plan 测试通过")

    finally:
        await memory_db.close_pool()


# ------------------------------------------------------------------ #
#  3. 完整流程测试
# ------------------------------------------------------------------ #

async def test_full(assessment_id: str, onetsoc_code: str):
    """完整流程测试：generate_career_plan + generate_action_plan。"""
    print(f"\n[Full] 开始完整测试  assessment_id={assessment_id}  onetsoc_code={onetsoc_code}")
    await memory_db.init_pool(**DB_CONFIG)

    try:
        # Step 1: generate_career_plan
        print("\n  Step 1: generate_career_plan...")
        plan_result_str = await generate_career_plan(assessment_id, onetsoc_code)
        plan_result = json.loads(plan_result_str)
        if "error" in plan_result:
            print(f"  ❌ generate_career_plan 错误：{plan_result['error']}")
            return

        gap_context = plan_result.get("gap_context", {})
        print(f"  Block 1/2/3/5 生成完毕，职业：{gap_context.get('occupation_title')}")

        # Step 2: generate_action_plan
        print("\n  Step 2: generate_action_plan（Sub-Agent 动态规划）...")
        action_result_str = await generate_action_plan(
            gap_context_json=json.dumps(gap_context, ensure_ascii=False)
        )
        action_result = json.loads(action_result_str)
        if "error" in action_result:
            print(f"  ❌ generate_action_plan 错误：{action_result['error']}")
            return

        block4 = action_result.get("block", {})
        print(f"\n  [Block 4] 阶段数：{len(block4.get('phases', []))}")
        for phase in block4.get("phases", []):
            print(f"\n  [{phase['phase_id']}] {phase['label']}")
            print(f"  重点：{phase['focus']}")
            for action in phase.get("actions", []):
                print(f"    - {action['item']} (gap={action['gap_value']})")
                print(f"      行动：{action['action'][:60]}...")
                print(f"      产出：{action['deliverable'][:50]}...")
                print(f"      资源：{action['resource'][:60]}...")

        print("\n✅ 完整流程测试通过")

    finally:
        await memory_db.close_pool()


# ------------------------------------------------------------------ #
#  入口
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "unit"

    if mode == "unit":
        run_unit_tests()

    elif mode == "plan":
        if len(sys.argv) < 4:
            print("用法：python test/test_career_plan.py plan <assessment_id> <onetsoc_code>")
            sys.exit(1)
        asyncio.run(test_plan(sys.argv[2], sys.argv[3]))

    elif mode == "full":
        if len(sys.argv) < 4:
            print("用法：python test/test_career_plan.py full <assessment_id> <onetsoc_code>")
            sys.exit(1)
        asyncio.run(test_full(sys.argv[2], sys.argv[3]))

    else:
        print("未知模式，可选：unit / plan / full")
        sys.exit(1)
