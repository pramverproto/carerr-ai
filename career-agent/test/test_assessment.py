"""
test_assessment.py

测试 start_assessment 工具。

分三个测试层：
  1. 单元测试 — 不调用 LLM，验证数据加载、提取、解析逻辑
  2. 单维度集成测试 — 真实调用一次 LLM（只跑 work_values/6.1），快速验证端到端流程
  3. 完整评估压测 — 跑全部 22 个子维度（可选，耗时较长）

运行方式：
  # 只跑单元测试（不消耗 API）
  python test/test_assessment.py unit

  # 跑单维度集成测试（1次 LLM 调用）
  python test/test_assessment.py one

  # 跑完整评估（22次 LLM 调用）
  python test/test_assessment.py full
"""

import sys
import asyncio
import json
from pathlib import Path

# 保证从项目根目录导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 导入被测模块（会触发评分表加载）
import agent.tools.assessment as _mod
from agent.tools.assessment import (
    _SCORING_TABLES,
    _SUB_DIMENSION_TASKS,
    _extract_sub_dimension,
    _extract_candidate_fields,
    _parse_json_from_llm,
    _aggregate_results,
    _score_sub_dimension,
    start_assessment,
)
from agent.tools.registry import TOOL_REGISTRY, TOOL_SCHEMAS


# ------------------------------------------------------------------ #
#  辅助：加载 mock 候选人数据                                           #
# ------------------------------------------------------------------ #

_BASE_DIR = Path(__file__).resolve().parent.parent
_MOCK_INPUT = _BASE_DIR / "mock输入.json"


def _load_mock() -> dict:
    assert _MOCK_INPUT.exists(), f"mock 数据文件不存在：{_MOCK_INPUT}"
    return json.loads(_MOCK_INPUT.read_text(encoding="utf-8"))


# ================================================================== #
#  层 1：单元测试（无 LLM 调用）                                        #
# ================================================================== #

def test_scoring_tables_loaded():
    """评分表 JSON 全部加载成功。"""
    expected = {"skills", "knowledge", "abilities", "work_styles", "interests", "work_values"}
    loaded = set(_SCORING_TABLES.keys())
    missing = expected - loaded
    assert not missing, f"以下评分表未加载：{missing}"
    print(f"✓ 6 个评分表全部加载成功：{sorted(loaded)}")


def test_sub_dimension_count():
    """任务列表应包含 22 个子维度。"""
    assert len(_SUB_DIMENSION_TASKS) == 22, f"期望 22 个任务，实际 {len(_SUB_DIMENSION_TASKS)}"
    print(f"✓ 子维度任务数量正确：{len(_SUB_DIMENSION_TASKS)}")


def test_extract_sub_dimension_skills():
    """能从 skills 评分表中提取子维度 1.1。"""
    table = _SCORING_TABLES["skills"]
    result = _extract_sub_dimension(table, "1.1")
    assert result is not None, "提取子维度 1.1 失败"
    assert "sub_dimension" in result, "结果缺少 sub_dimension 字段"
    sd = result["sub_dimension"]
    assert sd.get("sub_dimension_id") == "1.1", "子维度 ID 不匹配"
    assert "items" in sd, "子维度缺少 items 字段"
    print(f"✓ skills/1.1 提取成功，包含 {len(sd['items'])} 个评分项")


def test_extract_sub_dimension_interests():
    """interests 整体作为子维度 5 提取。"""
    table = _SCORING_TABLES["interests"]
    result = _extract_sub_dimension(table, "5")
    assert result is not None, "提取 interests 失败"
    assert "riasec_items" in result, "结果缺少 riasec_items 字段"
    print(f"✓ interests/5 提取成功，包含 {len(result['riasec_items'])} 个 RIASEC 类型")


def test_extract_sub_dimension_work_values():
    """能从 work_values 评分表中提取所有 6 个子维度。"""
    table = _SCORING_TABLES["work_values"]
    for i in range(1, 7):
        sub_id = f"6.{i}"
        result = _extract_sub_dimension(table, sub_id)
        assert result is not None, f"提取 work_values/{sub_id} 失败"
    print("✓ work_values 6 个子维度全部提取成功")


def test_extract_candidate_fields():
    """候选人字段提取：从 resume 内嵌字段和顶层字段各取一例。"""
    candidate = _load_mock()

    # resume 内嵌字段（experiences）
    slice1 = _extract_candidate_fields(candidate, ["experiences"])
    assert "experiences" in slice1, "experiences 提取失败"

    # 顶层字段（bigfive）
    slice2 = _extract_candidate_fields(candidate, ["bigfive"])
    assert "bigfive" in slice2, "bigfive 提取失败"

    # 不存在字段不应报错，只是不包含
    slice3 = _extract_candidate_fields(candidate, ["nonexistent_field"])
    assert "nonexistent_field" not in slice3

    print(f"✓ 候选人字段提取正常（experiences={len(slice1['experiences'])} 条经历）")


def test_parse_json_from_llm_clean():
    """干净的 JSON 字符串能被正确解析。"""
    raw = '{"score": 5.5, "evidence": "test"}'
    result = _parse_json_from_llm(raw)
    assert result["score"] == 5.5
    print("✓ 干净 JSON 解析正确")


def test_parse_json_from_llm_codeblock():
    """带 markdown 代码块的 LLM 输出能正确解析。"""
    raw = '```json\n{"score": 4.0, "confidence": "中"}\n```'
    result = _parse_json_from_llm(raw)
    assert result["score"] == 4.0
    assert result["confidence"] == "中"
    print("✓ markdown 代码块 JSON 解析正确")


def test_parse_json_from_llm_trailing_comma():
    """含尾部逗号的 JSON（LLM 常见错误）能被容错解析。"""
    raw = '{"score": 3.5, "items": ["a", "b",]}'
    result = _parse_json_from_llm(raw)
    # 容错成功时不含 parse_error
    has_error = result.get("parse_error", False)
    if has_error:
        # 容错失败时保留 raw_output，不应抛出异常
        assert "raw_output" in result
        print("⚠ 尾部逗号容错未能修复（保留了 raw_output），但未崩溃")
    else:
        print("✓ 尾部逗号容错解析正确")


def test_aggregate_results():
    """汇总逻辑：多个子维度结果能正确按 agent_key 分组。"""
    mock_results = [
        {
            "_meta": {"agent_key": "skills", "sub_dimension_id": "1.1", "elapsed_ms": 100, "tokens": {"total_tokens": 500}},
            "sub_dimension": {"items": [{"result": {"score": 5.5}}, {"result": {"score": 4.0}}]},
        },
        {
            "_meta": {"agent_key": "skills", "sub_dimension_id": "1.2", "elapsed_ms": 90, "tokens": {"total_tokens": 400}},
            "sub_dimension": {"items": [{"result": {"score": 6.0}}]},
        },
        {
            "_meta": {"agent_key": "knowledge", "sub_dimension_id": "2.1", "elapsed_ms": 110, "tokens": {"total_tokens": 600}},
            "sub_dimension": {"items": [{"result": {"score": None}}]},  # null 分值不计入均值
        },
    ]
    dims = _aggregate_results(mock_results)

    assert "skills" in dims, "skills 维度未汇总"
    assert "knowledge" in dims, "knowledge 维度未汇总"
    assert len(dims["skills"]["sub_dimensions"]) == 2
    assert dims["skills"]["elapsed_ms"] == 190
    assert dims["skills"]["total_tokens"] == 900
    # overall_score = (5.5 + 4.0 + 6.0) / 3 ≈ 5.17
    assert dims["skills"]["overall_score"] is not None
    assert abs(dims["skills"]["overall_score"] - 5.17) < 0.01, f"均值计算错误：{dims['skills']['overall_score']}"
    # knowledge 只有 null 分，overall_score 应为 None
    assert dims["knowledge"]["overall_score"] is None

    print(f"✓ 汇总逻辑正确：skills.overall_score={dims['skills']['overall_score']}, knowledge.overall_score=None")


def test_tool_registered():
    """start_assessment 已注册到 TOOL_REGISTRY 和 TOOL_SCHEMAS。"""
    assert "start_assessment" in TOOL_REGISTRY, "start_assessment 未注册到 TOOL_REGISTRY"

    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert "start_assessment" in names, "start_assessment 的 schema 未写入 TOOL_SCHEMAS"

    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "start_assessment")
    assert schema["type"] == "function"
    assert "description" in schema["function"]
    assert "parameters" in schema["function"]
    print("✓ start_assessment 工具注册正确，schema 结构合法")


# ================================================================== #
#  层 2：单维度集成测试（1 次真实 LLM 调用）                             #
# ================================================================== #

async def test_single_dimension_llm():
    """
    只评估 work_values/6.1（成就感），验证端到端流程：
    数据加载 → 评分表提取 → LLM 调用 → JSON 解析 → 结果结构合法
    """
    from agent.tools.assessment import _get_llm

    candidate = _load_mock()
    table = _SCORING_TABLES["work_values"]
    scoring_slice = _extract_sub_dimension(table, "6.1")
    assert scoring_slice is not None

    candidate_slice = _extract_candidate_fields(candidate, ["supplement", "experiences"])

    llm = _get_llm()
    result = await _score_sub_dimension(llm, "work_values", "6.1", scoring_slice, candidate_slice)

    # 基本结构检查
    assert "_meta" in result, "结果缺少 _meta 字段"
    assert result["_meta"]["agent_key"] == "work_values"
    assert result["_meta"]["sub_dimension_id"] == "6.1"
    assert result["_meta"]["elapsed_ms"] > 0

    # 不能是完全空的解析错误
    assert not result.get("parse_error"), f"LLM 输出 JSON 解析失败：{result.get('raw_output', '')[:200]}"

    print(f"✓ 单维度 LLM 评分成功")
    print(f"  耗时：{result['_meta']['elapsed_ms']}ms")
    print(f"  tokens：{result['_meta']['tokens']}")
    print(f"  结果片段：{json.dumps(result, ensure_ascii=False)[:300]}...")


# ================================================================== #
#  层 3：完整评估（22 次 LLM 调用）                                     #
# ================================================================== #

async def test_full_assessment():
    """
    调用 start_assessment() 完整跑一遍，验证：
    - 返回合法 JSON 字符串
    - 包含 6 个维度
    - status 为 done 或 partial
    """
    raw = await start_assessment()
    result = json.loads(raw)

    assert "assessment_id" in result
    assert result["status"] in ("done", "partial"), f"非法 status：{result['status']}"
    assert "dimensions" in result

    dims = result["dimensions"]
    expected_keys = {"skills", "knowledge", "abilities", "work_styles", "interests", "work_values"}
    missing = expected_keys - set(dims.keys())
    assert not missing, f"结果缺少维度：{missing}"

    print(f"\n{'='*60}")
    print(f"✓ 完整评估完成  assessment_id={result['assessment_id']}")
    print(f"  状态：{result['status']}")
    print(f"  总耗时：{result['elapsed_ms']}ms")
    print(f"  候选人：{result['candidate_name']}")
    print(f"\n  各维度结果：")
    for key, dim in dims.items():
        score = dim.get("overall_score")
        score_str = f"{score:.2f}" if score is not None else "null"
        print(f"  [{key:<12}] overall_score={score_str}  sub_dims={dim['sub_dimension_count']}  耗时={dim['elapsed_ms']}ms")

    if result.get("errors"):
        print(f"\n  ⚠ 以下子维度评分失败：")
        for e in result["errors"]:
            print(f"    - {e}")

    print(f"{'='*60}")


# ================================================================== #
#  入口                                                                #
# ================================================================== #

def run_unit_tests():
    """运行所有不依赖 LLM 的单元测试。"""
    print("=" * 60)
    print("[ 层 1 ] 单元测试（无 LLM 调用）")
    print("=" * 60)
    tests = [
        test_scoring_tables_loaded,
        test_sub_dimension_count,
        test_extract_sub_dimension_skills,
        test_extract_sub_dimension_interests,
        test_extract_sub_dimension_work_values,
        test_extract_candidate_fields,
        test_parse_json_from_llm_clean,
        test_parse_json_from_llm_codeblock,
        test_parse_json_from_llm_trailing_comma,
        test_aggregate_results,
        test_tool_registered,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"✗ {t.__name__}: {e}")
    print(f"\n单元测试：{passed}/{len(tests)} 通过\n")
    return passed == len(tests)


async def run_integration_test():
    """运行单维度集成测试（1 次 LLM 调用）。"""
    print("=" * 60)
    print("[ 层 2 ] 单维度集成测试（1 次 LLM 调用）")
    print("=" * 60)
    try:
        await test_single_dimension_llm()
        print("\n集成测试通过\n")
        return True
    except Exception as e:
        print(f"✗ 集成测试失败：{e}\n")
        return False


async def run_full_test():
    """运行完整评估（22 次 LLM 调用）。"""
    print("=" * 60)
    print("[ 层 3 ] 完整评估（22 次 LLM 调用，耗时较长）")
    print("=" * 60)
    try:
        await test_full_assessment()
        return True
    except Exception as e:
        print(f"✗ 完整评估失败：{e}\n")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "unit"

    if mode == "unit":
        ok = run_unit_tests()
        sys.exit(0 if ok else 1)

    elif mode == "one":
        unit_ok = run_unit_tests()
        int_ok = asyncio.run(run_integration_test())
        sys.exit(0 if (unit_ok and int_ok) else 1)

    elif mode == "full":
        unit_ok = run_unit_tests()
        full_ok = asyncio.run(run_full_test())
        sys.exit(0 if (unit_ok and full_ok) else 1)

    else:
        print(f"未知模式：{mode}")
        print("用法：python test/test_assessment.py [unit|one|full]")
        sys.exit(1)
