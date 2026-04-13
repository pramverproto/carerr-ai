"""
test_career.py

测试 match_careers 工具。

分三个测试层：
  1. unit  — 不调用 LLM/Qdrant，验证召回逻辑、合并去重、画像文本构建
  2. recall — 只跑三路召回（不做 JD 验证和 LLM 审核），验证召回是否有结果
  3. full  — 完整流程（三路召回 + JD 验证 + LLM 审核），需要真实 assessment_id

运行方式：
  # 单元测试（不消耗 API）
  python test/test_career.py unit

  # 只测召回（消耗 embed API）
  python test/test_career.py recall <assessment_id>

  # 完整流程测试（消耗 embed API + LLM API）
  python test/test_career.py full <assessment_id>
"""

import sys
import asyncio
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools.career import (
    _build_candidate_profile_text,
    _merge_recalls,
    _recall_semantic,
    _recall_holland,
    _recall_dim_filter,
    _validate_with_jd,
    _load_assessment,
    _load_candidate_basic,
    QDRANT_URL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    EMBED_MODEL,
)
from agent.agent_config import DB_CONFIG
import agent.memory.db as memory_db


# ------------------------------------------------------------------ #
#  1. 单元测试
# ------------------------------------------------------------------ #

def test_build_profile_text():
    """验证候选人画像文本构建逻辑。"""
    print("\n[Unit] test_build_profile_text")
    dims = {
        "skills":      {"overall_score": 5.2, "sub_dimensions": []},
        "knowledge":   {"overall_score": 4.8, "sub_dimensions": []},
        "abilities":   {"overall_score": 5.5, "sub_dimensions": []},
        "work_styles": {"overall_score": 4.6, "sub_dimensions": []},
        "work_values": {"overall_score": 5.0, "sub_dimensions": []},
    }
    candidate = {
        "name": "张三",
        "target_role": "数据产品经理",
        "years_of_experience": 5,
        "riasec": {"I": 6.0, "E": 5.5, "C": 4.0, "R": 2.0, "A": 3.0, "S": 3.5},
    }
    text = _build_candidate_profile_text(dims, candidate)
    print(f"  画像文本：\n{text}")
    assert "数据产品经理" in text
    assert "Investigative" in text
    assert "Skills" in text
    print("  ✅ PASS")


def test_merge_recalls():
    """验证合并去重逻辑：被多路命中的职业排名靠前。"""
    print("\n[Unit] test_merge_recalls")
    recall1 = [
        {"onetsoc_code": "15-2051.00", "title": "Data Scientists", "_score": 0.85, "_sources": ["semantic"]},
        {"onetsoc_code": "11-3021.00", "title": "Computer Managers", "_score": 0.75, "_sources": ["semantic"]},
    ]
    recall2 = [
        {"onetsoc_code": "15-2051.00", "title": "Data Scientists", "_score": 0.0, "_sources": ["holland"]},
        {"onetsoc_code": "13-2051.00", "title": "Financial Analysts", "_score": 0.0, "_sources": ["holland"]},
    ]
    recall3 = [
        {"onetsoc_code": "15-2051.00", "title": "Data Scientists", "_score": 0.0, "_sources": ["dim_filter"]},
        {"onetsoc_code": "17-2061.00", "title": "Computer Engineers", "_score": 0.0, "_sources": ["dim_filter"]},
    ]
    merged = _merge_recalls(recall1, recall2, recall3)
    print(f"  合并结果数量：{len(merged)}")
    print(f"  第一名：{merged[0]['title']}  来源：{merged[0]['_sources']}")
    assert merged[0]["onetsoc_code"] == "15-2051.00", "三路命中的职业应排第一"
    assert len(merged[0]["_sources"]) == 3, "应有3个来源"
    print("  ✅ PASS")


def run_unit_tests():
    test_build_profile_text()
    test_merge_recalls()
    print("\n✅ 所有单元测试通过")


# ------------------------------------------------------------------ #
#  2. 召回测试（需要真实 DB + Qdrant + embed API）
# ------------------------------------------------------------------ #

async def test_recall(assessment_id: str):
    """测试三路召回是否正常返回结果。"""
    print(f"\n[Recall] 开始测试  assessment_id={assessment_id}")
    await memory_db.init_pool(**DB_CONFIG)

    try:
        # 加载数据
        dims = await _load_assessment(assessment_id)
        if not dims:
            print(f"  ❌ 未找到评估数据，请检查 assessment_id")
            return
        candidate = await _load_candidate_basic(assessment_id)
        print(f"  候选人：{candidate.get('name')}  目标岗位：{candidate.get('target_role')}")

        riasec = candidate.get("riasec") or {}
        print(f"  RIASEC：{riasec}")
        print(f"  六维得分：" + ", ".join(
            f"{k}={v.get('overall_score')}"
            for k, v in dims.items()
            if v.get("overall_score") is not None
        ))

        from openai import AsyncOpenAI
        from qdrant_client import AsyncQdrantClient

        qdrant = AsyncQdrantClient(url=QDRANT_URL)
        openai = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

        profile_text = _build_candidate_profile_text(dims, {**candidate, "riasec": riasec})
        print(f"\n  候选人画像文本：\n{profile_text}\n")

        # 三路并发召回
        r1, r2, r3 = await asyncio.gather(
            _recall_semantic(profile_text, qdrant, openai),
            _recall_holland(riasec),
            _recall_dim_filter(dims),
        )

        print(f"\n  路线1 语义召回：{len(r1)} 条")
        for occ in r1[:3]:
            print(f"    - [{occ['_score']:.3f}] {occ['title']} ({occ['onetsoc_code']})")

        print(f"\n  路线2 Holland召回：{len(r2)} 条")
        for occ in r2[:3]:
            print(f"    - {occ['title']} ({occ['onetsoc_code']})")

        print(f"\n  路线3 六维过滤召回：{len(r3)} 条")
        for occ in r3[:3]:
            print(f"    - {occ['title']} ({occ['onetsoc_code']})")

        merged = _merge_recalls(r1, r2, r3)
        print(f"\n  合并后 Top 10：")
        for occ in merged[:10]:
            print(f"    [{len(occ['_sources'])}路] {occ['title']}  score={occ['_score']:.3f}")

        await qdrant.close()
        print("\n✅ 召回测试通过")

    finally:
        await memory_db.close_pool()


# ------------------------------------------------------------------ #
#  3. 完整流程测试
# ------------------------------------------------------------------ #

async def test_full(assessment_id: str):
    """完整流程测试：三路召回 + JD 验证 + LLM 审核。"""
    print(f"\n[Full] 开始完整测试  assessment_id={assessment_id}")

    # 直接调用工具函数
    import agent.tools.career  # 确保工具注册
    from agent.tools.career import match_careers

    await memory_db.init_pool(**DB_CONFIG)
    try:
        result_str = await match_careers(assessment_id)
        result = json.loads(result_str)

        if "error" in result:
            print(f"  ❌ 错误：{result['error']}")
            return

        print(f"\n  候选人：{result.get('candidate_name')}")
        print(f"  召回统计：{result.get('recall_stats')}")

        recommended = result.get("recommended", [])
        print(f"\n  推荐职业（{len(recommended)} 个）：")
        for i, occ in enumerate(recommended, 1):
            print(f"\n  [{i}] {occ.get('title')}  匹配度：{occ.get('match_score')}%")
            print(f"      推荐理由：{occ.get('match_reason')}")
            print(f"      关键差距：{occ.get('key_gaps')}")
            print(f"      市场信号：{occ.get('jd_market_signal')}")
            print(f"      JD技能词：{occ.get('typical_jd_skills')}")

        if result.get("excluded_reason"):
            print(f"\n  剔除说明：{result['excluded_reason']}")

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

    elif mode == "recall":
        if len(sys.argv) < 3:
            print("用法：python test/test_career.py recall <assessment_id>")
            sys.exit(1)
        asyncio.run(test_recall(sys.argv[2]))

    elif mode == "full":
        if len(sys.argv) < 3:
            print("用法：python test/test_career.py full <assessment_id>")
            sys.exit(1)
        asyncio.run(test_full(sys.argv[2]))

    else:
        print("未知模式，可选：unit / recall / full")
        sys.exit(1)
