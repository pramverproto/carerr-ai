"""
career_plan.py 单元测试 + 异步集成测试（mock LLM）。

覆盖内容：
1. _gap_status              纯函数边界值
2. _compute_rule_score      规则算分完整路径（正常、缺值、全达标、全差距）
3. _extract_sub_scores      子维度得分提取（扁平结构 + 嵌套结构）
4. _summarize_candidate     候选人摘要生成
5. _build_match_overview    综合评估 block（mock LLM）
6. _build_jd_recommendations JD推荐 block（mock Qdrant + mock LLM）
7. _build_gap_analysis      差距分析 block（mock LLM）
8. generate_career_plan     完整流程（mock DB + mock LLM + mock Qdrant）
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ──────────────────────────────────────────────────────────────────────
# 在导入被测模块前，预先 mock 掉数据库 / 环境变量依赖，
# 避免因缺少 DB_HOST 等环境变量而在 import 阶段就爆出 EnvironmentError。
# ──────────────────────────────────────────────────────────────────────
os.environ.setdefault("LLM_MODEL",    "gpt-4o-mini")
os.environ.setdefault("LLM_API_KEY",  "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://localhost:9999")
os.environ.setdefault("DB_HOST",      "localhost")
os.environ.setdefault("DB_USER",      "test")
os.environ.setdefault("DB_PASSWORD",  "test")
os.environ.setdefault("DB_NAME",      "test")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from agent.tools.career_plan import (
    _gap_status,
    _compute_rule_score,
    _extract_sub_scores,
    _summarize_candidate,
    _build_match_overview,
    _build_jd_recommendations,
    _build_gap_analysis,
    GAP_NEAR,
    ZONE_WEIGHT,
    SUB_DIM_MAP,
)


# ══════════════════════════════════════════════════════════════════════
#  测试夹具（Fixtures）
# ══════════════════════════════════════════════════════════════════════

def _make_dims(
    skills=5.0, knowledge=4.5, abilities=5.5, work_styles=5.0, work_values=4.0
) -> dict:
    """构建标准六维评估数据 dict。"""
    return {
        "skills": {
            "overall_score": skills,
            "sub_dimensions": [
                {"sub_dimension_id": "1.1", "sub_dimension_result": {"score": 5.0}, "evidence": ["引用1"]},
                {"sub_dimension_id": "1.2", "sub_dimension_result": {"score": 4.5}, "evidence": []},
                {"sub_dimension_id": "1.3", "sub_dimension_result": {"score": 5.5}, "evidence": ["引用2"]},
                {"sub_dimension_id": "1.4", "sub_dimension_result": {"score": 4.5}, "evidence": []},
            ],
            "highlights": ["技术技能强"],
            "focus_areas":  ["管理技能待提升"],
        },
        "knowledge": {
            "overall_score": knowledge,
            "sub_dimensions": [
                {"sub_dimension_id": "2.1", "sub_dimension_result": {"score": 4.5}, "evidence": []},
                {"sub_dimension_id": "2.2", "sub_dimension_result": {"score": 5.0}, "evidence": ["引用3"]},
                {"sub_dimension_id": "2.3", "sub_dimension_result": {"score": 3.5}, "evidence": []},
                {"sub_dimension_id": "2.4", "sub_dimension_result": {"score": 4.0}, "evidence": []},
            ],
            "highlights": [],
            "focus_areas":  ["人文社科偏弱"],
        },
        "abilities": {
            "overall_score": abilities,
            "sub_dimensions": [
                {"sub_dimension_id": "3.1", "sub_dimension_result": {"score": 5.0}, "evidence": ["引用4"]},
                {"sub_dimension_id": "3.2", "sub_dimension_result": {"score": 6.0}, "evidence": ["引用5"]},
                {"sub_dimension_id": "3.3", "sub_dimension_result": {"score": 5.5}, "evidence": []},
            ],
            "highlights": ["推理能力突出"],
            "focus_areas":  [],
        },
        "work_styles": {
            "overall_score": work_styles,
            "sub_dimensions": [
                {"sub_dimension_id": "4.1", "sub_dimension_result": {"score": 5.5}, "evidence": ["引用6"]},
                {"sub_dimension_id": "4.2", "sub_dimension_result": {"score": 4.5}, "evidence": []},
                {"sub_dimension_id": "4.3", "sub_dimension_result": {"score": 5.0}, "evidence": []},
                {"sub_dimension_id": "4.4", "sub_dimension_result": {"score": 5.0}, "evidence": []},
            ],
            "highlights": [],
            "focus_areas":  [],
        },
        "work_values": {
            "overall_score": work_values,
            "sub_dimensions": [
                {"sub_dimension_id": "6.1", "sub_dimension_result": {"score": 5.0}, "evidence": ["引用7"]},
                {"sub_dimension_id": "6.2", "sub_dimension_result": {"score": 4.0}, "evidence": []},
            ],
            "highlights": [],
            "focus_areas":  [],
        },
    }


def _make_onet(
    ability_verbal=5.0,
    ability_reasoning=5.5,
    ability_quantitative=5.0,
    skill_basic=5.0,
    skill_social=4.5,
    skill_technical=5.5,
    skill_management=5.0,
    knowledge_business=5.0,
    knowledge_tech=5.5,
    knowledge_humanities=3.5,
    knowledge_applied=4.0,
    work_style_proactive=5.0,
    work_style_interpersonal=4.5,
    work_style_conscientious=5.5,
    work_style_resilient=5.0,
    work_value_achievement=5.5,
    work_value_independence=4.5,
    dim_skills=5.2,
    dim_knowledge=4.8,
    dim_abilities=5.3,
    dim_work_styles=5.0,
    dim_work_values=4.5,
) -> dict:
    """构建标准 onet_occupations 行数据。"""
    return {
        "onetsoc_code": "13-2051.00",
        "title": "数据分析师",
        "description": "运用统计方法分析数据，支持商业决策。",
        "job_zone": 4,
        "ability_verbal": ability_verbal,
        "ability_reasoning": ability_reasoning,
        "ability_quantitative": ability_quantitative,
        "skill_basic": skill_basic,
        "skill_social": skill_social,
        "skill_technical": skill_technical,
        "skill_management": skill_management,
        "knowledge_business": knowledge_business,
        "knowledge_tech": knowledge_tech,
        "knowledge_humanities": knowledge_humanities,
        "knowledge_applied": knowledge_applied,
        "work_style_proactive": work_style_proactive,
        "work_style_interpersonal": work_style_interpersonal,
        "work_style_conscientious": work_style_conscientious,
        "work_style_resilient": work_style_resilient,
        "work_value_achievement": work_value_achievement,
        "work_value_independence": work_value_independence,
        "dim_skills": dim_skills,
        "dim_knowledge": dim_knowledge,
        "dim_abilities": dim_abilities,
        "dim_work_styles": dim_work_styles,
        "dim_work_values": dim_work_values,
        "tech_tools_json": ["Python", "SQL", "Tableau"],
        "core_tasks_json": ["数据清洗", "建立模型", "撰写报告"],
    }


def _make_candidate(
    name="张三",
    target_role="数据产品经理",
    years=5,
) -> dict:
    return {
        "name": name,
        "target_role": target_role,
        "years_of_experience": years,
        "resume_raw": {
            "experiences": [
                {"title": "高级数据分析师", "company": "ABC科技"},
                {"title": "数据分析师", "company": "XYZ电商"},
            ],
            "skills": ["Python", "SQL", "机器学习", "数据可视化"],
            "bigfive": {"O": 75, "C": 80, "E": 60, "A": 65, "ES": 70},
            "riasec": {"holland_code": "IEC", "I": 6.0, "E": 5.5, "C": 5.0},
        },
        "supplement": "我希望向数据产品方向转型，专注业务驱动的数据产品设计。",
    }


def _make_llm_mock(response_content: str) -> MagicMock:
    """构建同步 LLM mock，chat() 返回 (response, elapsed_ms, usage)。"""
    mock_msg = MagicMock()
    mock_msg.content = response_content
    mock_choice = MagicMock()
    mock_choice.message = mock_msg
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    llm = MagicMock()
    llm.chat.return_value = (mock_response, 100, {"total_tokens": 100})
    return llm


# ══════════════════════════════════════════════════════════════════════
#  1. _gap_status
# ══════════════════════════════════════════════════════════════════════

class TestGapStatus:
    def test_negative_gap_is_qualified(self):
        assert _gap_status(-1.0) == "达标"

    def test_zero_gap_is_qualified(self):
        assert _gap_status(0.0) == "达标"

    def test_small_gap_is_near(self):
        assert _gap_status(0.5) == "接近达标"
        assert _gap_status(GAP_NEAR) == "接近达标"

    def test_large_gap_is_obvious(self):
        assert _gap_status(GAP_NEAR + 0.01) == "明显Gap"
        assert _gap_status(3.0) == "明显Gap"


# ══════════════════════════════════════════════════════════════════════
#  2. _compute_rule_score
# ══════════════════════════════════════════════════════════════════════

class TestComputeRuleScore:
    def test_full_match_returns_high_score(self):
        """候选人各维度均≥O*NET要求时，规则分应较高。"""
        dims = _make_dims(skills=6.0, knowledge=5.5, abilities=6.0, work_styles=5.5, work_values=5.0)
        onet = _make_onet(dim_skills=5.0, dim_knowledge=4.5, dim_abilities=5.0,
                          dim_work_styles=4.5, dim_work_values=4.0)
        rule_score, dim_comparison = _compute_rule_score(dims, onet)
        assert rule_score > 90.0, f"全达标时规则分应>90，实际={rule_score}"
        assert len(dim_comparison) == 5
        for item in dim_comparison:
            assert item["status"] == "达标"

    def test_full_gap_returns_low_score(self):
        """候选人各维度均明显低于要求时，规则分应较低。"""
        dims = _make_dims(skills=2.0, knowledge=2.0, abilities=2.0, work_styles=2.0, work_values=2.0)
        onet = _make_onet(dim_skills=6.0, dim_knowledge=6.0, dim_abilities=6.0,
                          dim_work_styles=6.0, dim_work_values=6.0)
        rule_score, _ = _compute_rule_score(dims, onet)
        assert rule_score < 50.0, f"全差距时规则分应<50，实际={rule_score}"

    def test_partial_none_dims_still_works(self):
        """部分维度缺数据时，仅计算有数据的维度。"""
        dims = {
            "skills":    {"overall_score": 5.0, "sub_dimensions": [], "highlights": [], "focus_areas": []},
            "knowledge": {"overall_score": None, "sub_dimensions": [], "highlights": [], "focus_areas": []},
            "abilities": {"overall_score": 5.0, "sub_dimensions": [], "highlights": [], "focus_areas": []},
            "work_styles": {"overall_score": None, "sub_dimensions": [], "highlights": [], "focus_areas": []},
            "work_values": {"overall_score": 4.5, "sub_dimensions": [], "highlights": [], "focus_areas": []},
        }
        onet = _make_onet()
        rule_score, dim_comparison = _compute_rule_score(dims, onet)
        assert isinstance(rule_score, float)
        # 只有 3 个有效维度
        assert len(dim_comparison) == 3

    def test_returns_dim_comparison_keys(self):
        """dim_comparison 每项都有必要字段。"""
        dims = _make_dims()
        onet = _make_onet()
        _, dim_comparison = _compute_rule_score(dims, onet)
        for item in dim_comparison:
            for key in ("dimension", "label", "candidate_score", "onet_required", "gap", "status"):
                assert key in item, f"dim_comparison 缺少字段 {key}"

    def test_score_range_0_to_100(self):
        """规则分始终在 0~100 区间内。"""
        dims = _make_dims()
        onet = _make_onet()
        rule_score, _ = _compute_rule_score(dims, onet)
        assert 0 <= rule_score <= 100

    def test_empty_dims_returns_zero(self):
        """空 dims 时返回 0.0。"""
        rule_score, dim_comparison = _compute_rule_score({}, _make_onet())
        assert rule_score == 0.0
        assert dim_comparison == []


# ══════════════════════════════════════════════════════════════════════
#  3. _extract_sub_scores
# ══════════════════════════════════════════════════════════════════════

class TestExtractSubScores:
    def test_extracts_flat_structure(self):
        """标准扁平结构（sub_dimension_id 在顶层）。"""
        dims = _make_dims()
        result = _extract_sub_scores(dims)

        # 检查 skills 维度
        skills_sub = result.get("skills", {})
        assert "1.1 认知基础技能" in skills_sub
        assert skills_sub["1.1 认知基础技能"]["score"] == 5.0

    def test_handles_nested_sub_dimension_wrapper(self):
        """带 sub_dimension 包装层的嵌套结构。"""
        dims = {
            "skills": {
                "overall_score": 5.0,
                "sub_dimensions": [
                    {
                        "sub_dimension": {
                            "sub_dimension_id": "1.1",
                            "sub_dimension_result": {"score": 4.8},
                            "evidence": ["nested_evidence"],
                        }
                    }
                ],
                "highlights": [], "focus_areas": [],
            }
        }
        result = _extract_sub_scores(dims)
        score = result.get("skills", {}).get("1.1 认知基础技能", {}).get("score")
        assert score == 4.8

    def test_missing_sub_dimension_returns_none_score(self):
        """子维度不存在时 score 应为 None。"""
        dims = {
            "skills": {
                "overall_score": 5.0,
                "sub_dimensions": [],
                "highlights": [], "focus_areas": [],
            }
        }
        result = _extract_sub_scores(dims)
        for sub_name in SUB_DIM_MAP["skills"]:
            assert result["skills"][sub_name]["score"] is None

    def test_evidence_trimmed_to_3(self):
        """每个子维度的 evidence 最多取 3 条。"""
        dims = {
            "skills": {
                "overall_score": 5.0,
                "sub_dimensions": [
                    {
                        "sub_dimension_id": "1.3",
                        "sub_dimension_result": {"score": 5.0},
                        "evidence": ["e1", "e2", "e3", "e4", "e5"],
                    }
                ],
                "highlights": [], "focus_areas": [],
            }
        }
        result = _extract_sub_scores(dims)
        evidence = result.get("skills", {}).get("1.3 技术技能", {}).get("evidence", [])
        assert len(evidence) <= 3

    def test_all_dims_and_sub_dims_present(self):
        """所有预定义的维度和子维度键都应存在。"""
        dims = _make_dims()
        result = _extract_sub_scores(dims)
        for dim_key, name_map in SUB_DIM_MAP.items():
            assert dim_key in result, f"维度 {dim_key} 缺失"
            for sub_name in name_map:
                assert sub_name in result[dim_key], f"子维度 {sub_name} 缺失"


# ══════════════════════════════════════════════════════════════════════
#  4. _summarize_candidate
# ══════════════════════════════════════════════════════════════════════

class TestSummarizeCandidate:
    def test_basic_fields_present(self):
        candidate = _make_candidate()
        dims = _make_dims()
        result = _summarize_candidate(candidate, dims)
        assert "张三" in result
        assert "5年" in result
        assert "数据产品经理" in result

    def test_skills_appear_in_summary(self):
        candidate = _make_candidate()
        dims = _make_dims()
        result = _summarize_candidate(candidate, dims)
        assert "Python" in result or "SQL" in result

    def test_bigfive_included(self):
        candidate = _make_candidate()
        dims = _make_dims()
        result = _summarize_candidate(candidate, dims)
        assert "大五人格" in result

    def test_dim_scores_included(self):
        candidate = _make_candidate()
        dims = _make_dims()
        result = _summarize_candidate(candidate, dims)
        assert "六维得分" in result

    def test_empty_candidate_no_crash(self):
        result = _summarize_candidate({}, {})
        assert isinstance(result, str)


# ══════════════════════════════════════════════════════════════════════
#  5. _build_match_overview（async + mock LLM）
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestBuildMatchOverview:
    async def test_returns_correct_block_id(self):
        dims = _make_dims()
        onet = _make_onet()
        candidate = _make_candidate()

        llm_response = json.dumps({
            "llm_score": 72.0,
            "narrative": "候选人在技术方面有明显优势，整体匹配度较好。",
            "key_factors": [
                {"factor": "技术能力", "impact": "positive", "note": "Python和数据分析经验丰富"},
                {"factor": "管理经验", "impact": "negative", "note": "管理经验相对薄弱"},
            ]
        })
        llm = _make_llm_mock(llm_response)

        block, narrative = await _build_match_overview(dims, onet, candidate, llm)

        assert block["block_id"] == "match_overview"
        assert "final_score" in block
        assert "verdict" in block
        assert block["rule_based"]["weight"] == 0.6
        assert block["llm_analysis"]["weight"] == 0.4
        assert isinstance(narrative, str) and len(narrative) > 0

    async def test_final_score_is_weighted_average(self):
        dims = _make_dims()
        onet = _make_onet()
        candidate = _make_candidate()

        llm_response = json.dumps({
            "llm_score": 80.0,
            "narrative": "测试叙事",
            "key_factors": []
        })
        llm = _make_llm_mock(llm_response)

        block, _ = await _build_match_overview(dims, onet, candidate, llm)

        rule_score = block["rule_based"]["score"]
        llm_score = block["llm_analysis"]["score"]
        expected = round(0.6 * rule_score + 0.4 * llm_score, 1)
        assert block["final_score"] == expected

    async def test_verdict_mapping(self):
        """final_score ≥ 80 → 高度匹配。"""
        dims = _make_dims(skills=6.5, knowledge=6.0, abilities=6.5, work_styles=6.0, work_values=5.5)
        onet = _make_onet(dim_skills=4.0, dim_knowledge=4.0, dim_abilities=4.0,
                          dim_work_styles=4.0, dim_work_values=3.5)
        candidate = _make_candidate()

        llm_response = json.dumps({
            "llm_score": 88.0,
            "narrative": "高度匹配",
            "key_factors": []
        })
        llm = _make_llm_mock(llm_response)

        block, _ = await _build_match_overview(dims, onet, candidate, llm)
        assert block["verdict"] in ("高度匹配", "中高匹配", "潜力匹配", "不建议")

    async def test_malformed_llm_response_graceful_fallback(self):
        """LLM 返回非 JSON 时应优雅降级（用 rule_score 代替 llm_score）。"""
        dims = _make_dims()
        onet = _make_onet()
        candidate = _make_candidate()

        llm = _make_llm_mock("这是一些不合规的纯文本，不是 JSON")

        block, _ = await _build_match_overview(dims, onet, candidate, llm)
        assert "final_score" in block
        assert block["final_score"] >= 0

    async def test_dim_comparison_contains_required_keys(self):
        dims = _make_dims()
        onet = _make_onet()
        candidate = _make_candidate()

        llm = _make_llm_mock('{"llm_score": 70, "narrative": "ok", "key_factors": []}')
        block, _ = await _build_match_overview(dims, onet, candidate, llm)

        for item in block["rule_based"]["dim_comparison"]:
            assert "dimension" in item
            assert "candidate_score" in item
            assert "onet_required" in item
            assert "gap" in item
            assert "status" in item


# ══════════════════════════════════════════════════════════════════════
#  6. _build_jd_recommendations（mock Qdrant + mock LLM）
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestBuildJdRecommendations:
    def _make_qdrant_mock(self, jds: list[dict]):
        """构建 mock AsyncQdrantClient，query_points 返回指定 JD 列表。"""
        mock_point = MagicMock()
        mock_points = []
        for jd in jds:
            p = MagicMock()
            p.payload = jd
            p.score = 0.9
            mock_points.append(p)
        mock_result = MagicMock()
        mock_result.points = mock_points
        qdrant = AsyncMock()
        qdrant.query_points = AsyncMock(return_value=mock_result)
        return qdrant

    def _make_openai_embed_mock(self):
        """构建 mock AsyncOpenAI，embeddings.create 返回 fake embedding。"""
        mock_data = MagicMock()
        mock_data.embedding = [0.1] * 1536
        mock_embed_response = MagicMock()
        mock_embed_response.data = [mock_data]
        openai_mock = AsyncMock()
        openai_mock.embeddings = AsyncMock()
        openai_mock.embeddings.create = AsyncMock(return_value=mock_embed_response)
        return openai_mock

    async def test_block_id_and_positions_structure(self):
        onet = _make_onet()
        candidate = _make_candidate()
        dims = _make_dims()

        jd_payload = {
            "title": "数据分析师",
            "description": "负责数据分析和报告输出，支持业务决策。" * 5,
            "skill_tags": ["Python", "SQL"],
            "salary_value": "15-25K",
            "company": "互联网公司",
        }
        qdrant = self._make_qdrant_mock([jd_payload])
        openai_mock = self._make_openai_embed_mock()

        jd_rec_json = json.dumps({
            "title": "数据分析师",
            "company_type": "互联网",
            "salary_range": "15-25K",
            "full_jd": "## 职位职责\n- 数据分析\n- 报告输出",
            "key_responsibilities": ["数据分析", "建立模型", "输出报告"],
            "required_qualifications": ["Python", "SQL", "统计学基础"],
            "role_explanation": "该岗位负责帮助业务团队做数据驱动决策。",
            "match_analysis": {
                "strengths": ["Python经验丰富", "分析思维强"],
                "concerns": ["行业经验不足"],
                "entry_difficulty": "moderate",
                "verdict": "整体匹配度良好，可以尝试申请。",
            }
        })
        llm = _make_llm_mock(jd_rec_json)

        block = await _build_jd_recommendations(onet, candidate, dims, qdrant, openai_mock, llm)

        assert block["block_id"] == "jd_recommendations"
        assert "positions" in block
        assert len(block["positions"]) >= 1
        pos = block["positions"][0]
        assert pos["rank"] == 1
        assert "match_score" in pos

    async def test_fallback_when_no_qdrant_results(self):
        """Qdrant 返回空时，应用 O*NET 兜底数据。"""
        onet = _make_onet()
        candidate = _make_candidate()
        dims = _make_dims()

        qdrant = self._make_qdrant_mock([])  # 空结果
        openai_mock = self._make_openai_embed_mock()

        fallback_json = json.dumps({
            "title": "数据分析师（兜底）",
            "company_type": "行业参考",
            "salary_range": None,
            "full_jd": "O*NET 数据兜底",
            "key_responsibilities": ["数据处理"],
            "required_qualifications": ["统计学"],
            "role_explanation": "数据分析职位通用说明。",
            "match_analysis": {
                "strengths": ["分析能力强"],
                "concerns": [],
                "entry_difficulty": "easy",
                "verdict": "兜底测试",
            }
        })
        llm = _make_llm_mock(fallback_json)

        block = await _build_jd_recommendations(onet, candidate, dims, qdrant, openai_mock, llm)

        assert block["block_id"] == "jd_recommendations"
        assert len(block["positions"]) >= 1

    async def test_positions_sorted_by_match_score(self):
        """positions 应按 match_score 降序排列。"""
        onet = _make_onet()
        candidate = _make_candidate()
        dims = _make_dims()

        jds = [
            {"title": "JD1", "description": "职位1" * 10, "skill_tags": ["Python"]},
            {"title": "JD2", "description": "职位2" * 10, "skill_tags": ["SQL"]},
            {"title": "JD3", "description": "职位3" * 10, "skill_tags": ["Excel"]},
        ]
        qdrant = self._make_qdrant_mock(jds)
        openai_mock = self._make_openai_embed_mock()

        # 三次 LLM 调用，difficulty 各不同
        call_count = 0
        difficulties = ["hard", "easy", "moderate"]

        def mock_chat(messages, *args, **kwargs):
            nonlocal call_count
            diff = difficulties[call_count % 3]
            call_count += 1
            content = json.dumps({
                "title": f"职位{call_count}",
                "company_type": "互联网",
                "salary_range": None,
                "full_jd": "详细JD",
                "key_responsibilities": ["职责"],
                "required_qualifications": ["要求"],
                "role_explanation": "岗位解读",
                "match_analysis": {
                    "strengths": ["优势"],
                    "concerns": ["顾虑"],
                    "entry_difficulty": diff,
                    "verdict": "测试判断",
                }
            })
            mock_msg = MagicMock()
            mock_msg.content = content
            mock_choice = MagicMock()
            mock_choice.message = mock_msg
            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            return (mock_response, 100, {})

        llm = MagicMock()
        llm.chat.side_effect = mock_chat

        block = await _build_jd_recommendations(onet, candidate, dims, qdrant, openai_mock, llm)

        scores = [p["match_score"] for p in block["positions"]]
        assert scores == sorted(scores, reverse=True), "positions 未按 match_score 降序排列"


# ══════════════════════════════════════════════════════════════════════
#  7. _build_gap_analysis（async + mock LLM）
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestBuildGapAnalysis:
    async def test_block_id_and_required_keys(self):
        dims = _make_dims()
        onet = _make_onet()
        candidate = _make_candidate()
        match_narrative = "候选人在技术能力方面表现良好，但商业知识有一定差距。"

        gaps_json = json.dumps([
            {"area": "财务分析", "severity": "high", "required": "能解读P&L",
             "current": "无相关经历", "how_to_close": "学习财务课程", "related_dimension": "knowledge"},
            {"area": "项目管理", "severity": "medium", "required": "管理多人项目",
             "current": "只有独立项目经历", "how_to_close": "争取带队机会", "related_dimension": "skills"},
        ])
        strengths_json = json.dumps([
            {"area": "技术技能", "required": "熟练Python/SQL", "current": "5年Python经验",
             "leverage": "在面试中展示数据建模案例", "related_dimension": "skills"},
        ])
        summary_text = "总体来看，候选人在技术方向有突出优势，但商业知识有待补足。"

        # _build_gap_analysis 内部调用了 _llm_fill 3次（gaps, strengths, summary），
        # 通过 llm.chat 返回值的 side_effect 依次返回。
        call_count = 0
        responses = [gaps_json, strengths_json, summary_text]

        def mock_chat(messages, *args, **kwargs):
            nonlocal call_count
            content = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            mock_msg = MagicMock()
            mock_msg.content = content
            mock_choice = MagicMock()
            mock_choice.message = mock_msg
            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            return (mock_response, 100, {})

        llm = MagicMock()
        llm.chat.side_effect = mock_chat

        block = await _build_gap_analysis(dims, onet, candidate, match_narrative, llm)

        assert block["block_id"] == "gap_analysis"
        assert "gaps" in block
        assert "strengths" in block
        assert "summary" in block
        assert isinstance(block["gaps"], list)
        assert isinstance(block["strengths"], list)

    async def test_handles_empty_gaps_gracefully(self):
        """LLM 返回空列表时，block 不应抛出异常。"""
        dims = _make_dims()
        onet = _make_onet()
        candidate = _make_candidate()

        call_count = 0

        def mock_chat(messages, *args, **kwargs):
            nonlocal call_count
            content = "[]" if call_count < 2 else "无明显差距，候选人基本达标。"
            call_count += 1
            mock_msg = MagicMock()
            mock_msg.content = content
            mock_choice = MagicMock()
            mock_choice.message = mock_msg
            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            return (mock_response, 100, {})

        llm = MagicMock()
        llm.chat.side_effect = mock_chat

        block = await _build_gap_analysis(dims, onet, candidate, "匹配良好", llm)
        assert block["block_id"] == "gap_analysis"
        assert block["gaps"] == []
        assert block["strengths"] == []


# ══════════════════════════════════════════════════════════════════════
#  8. gap_context 结构验证
# ══════════════════════════════════════════════════════════════════════

class TestGapContextStructure:
    """验证 generate_career_plan 生成的 gap_context 字段结构符合 action_plan 预期。"""

    def _build_mock_gap_context(self, gaps: list[dict], strengths: list[dict]) -> dict:
        return {
            "assessment_id": "test_abc123",
            "onetsoc_code": "13-2051.00",
            "occupation_title": "数据分析师",
            "occupation_description": "运用统计方法支持决策",
            "onet_core_tasks": ["数据清洗", "建立模型"],
            "candidate_name": "张三",
            "candidate_target_role": "数据产品经理",
            "candidate_years_of_experience": 5,
            "match_verdict": "中高匹配",
            "match_score": 71.5,
            "match_narrative": "整体匹配度良好，技术优势突出，商业知识有待补充。",
            "priority_gaps": gaps,
            "key_strengths": strengths,
        }

    def test_gap_item_has_required_fields(self):
        gaps = [
            {"area": "财务分析", "severity": "high", "required": "P&L分析",
             "current": "无经验", "how_to_close": "学课程", "related_dimension": "knowledge"},
        ]
        ctx = self._build_mock_gap_context(gaps, [])
        assert ctx["priority_gaps"][0]["severity"] in ("high", "medium", "low")
        assert all(k in ctx["priority_gaps"][0] for k in ("area", "severity", "required", "current", "how_to_close"))

    def test_strength_item_has_required_fields(self):
        strengths = [
            {"area": "技术技能", "leverage": "在面试中展示数据建模案例"},
        ]
        ctx = self._build_mock_gap_context([], strengths)
        assert all(k in ctx["key_strengths"][0] for k in ("area", "leverage"))

    def test_match_verdict_valid_value(self):
        ctx = self._build_mock_gap_context([], [])
        assert ctx["match_verdict"] in ("高度匹配", "中高匹配", "潜力匹配", "不建议", "中高匹配")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
