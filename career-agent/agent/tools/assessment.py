"""
个人能力评估工具。

注册为 @tool，assessment_agent 调用以触发完整评估流程。
内部逻辑：
  1. 从 DB candidates 表加载候选人数据（按 candidate_id）
  2. 按 22 个子维度拆分任务，并发调用 LLM 评分
  3. 汇总结果，写入 assessment_jobs / assessment_dimensions 表
  4. 返回 assessment_id + 各维度完整 JSON 供后续生成报告
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

import aiomysql

from agent.tools.registry import tool
from agent.agent_config import MAIN_AGENT_CONFIG, DB_CONFIG
from agent.providers.llm import LLMProvider
from agent.runner import run_prompt
from agent.logger import get_logger
import agent.memory.db as memory_db

logger = get_logger("assessment")

# ------------------------------------------------------------------ #
#  常量 & 数据加载                                                      #
# ------------------------------------------------------------------ #

_BASE_DIR = Path(__file__).resolve().parent.parent.parent  # 项目根目录
_SCORING_DIR = _BASE_DIR / "scoring_tables"

# 启动时加载评分表
_SCORING_TABLES: dict[str, dict] = {}


def _load_scoring_tables() -> None:
    """一次性加载全部 6 个评分表 JSON 到内存。"""
    files = {
        "skills":      "agent2_skills_scoring_table.json",
        "knowledge":   "agent3_knowledge_scoring_table.json",
        "abilities":   "agent4_abilities_scoring_table.json",
        "work_styles": "agent5_workstyles_scoring_table.json",
        "interests":   "agent6_interests_scoring_table.json",
        "work_values": "agent7_workvalues_scoring_table.json",
    }
    for key, filename in files.items():
        path = _SCORING_DIR / filename
        if path.exists():
            _SCORING_TABLES[key] = json.loads(path.read_text(encoding="utf-8"))
            logger.info(f"评分表已加载：{key} ← {filename}")
        else:
            logger.warning(f"评分表文件不存在：{path}")


_load_scoring_tables()


# ------------------------------------------------------------------ #
#  子维度任务定义                                                        #
# ------------------------------------------------------------------ #

# 每个子维度任务：(agent_key, sub_dimension_id, 需要的候选人数据字段)
_SUB_DIMENSION_TASKS: list[tuple[str, str, list[str]]] = [
    # Agent 2 技能 — 4 个子维度
    ("skills", "1.1", ["experiences", "skills", "certifications"]),
    ("skills", "1.2", ["experiences", "supplement"]),
    ("skills", "1.3", ["skills", "experiences", "certifications"]),
    ("skills", "1.4", ["experiences", "supplement"]),
    # Agent 3 知识 — 4 个子维度
    ("knowledge", "2.1", ["candidate", "experiences", "certifications"]),
    ("knowledge", "2.2", ["candidate", "experiences", "skills", "certifications"]),
    ("knowledge", "2.3", ["candidate", "experiences"]),
    ("knowledge", "2.4", ["candidate", "experiences"]),
    # Agent 4 认知能力 — 3 个子维度
    ("abilities", "3.1", ["experiences", "quiz_abilities"]),
    ("abilities", "3.2", ["experiences", "quiz_abilities"]),
    ("abilities", "3.3", ["experiences", "quiz_abilities"]),
    # Agent 5 工作特质 — 4 个子维度
    ("work_styles", "4.1", ["experiences", "supplement", "bigfive"]),
    ("work_styles", "4.2", ["experiences", "supplement", "bigfive"]),
    ("work_styles", "4.3", ["experiences", "supplement", "bigfive"]),
    ("work_styles", "4.4", ["experiences", "supplement", "bigfive"]),
    # Agent 6 职业兴趣 — 1 个整体
    ("interests", "5", ["skills", "experiences", "supplement", "riasec"]),
    # Agent 7 工作价值观 — 6 个子维度
    ("work_values", "6.1", ["supplement", "experiences"]),
    ("work_values", "6.2", ["supplement", "experiences"]),
    ("work_values", "6.3", ["supplement", "experiences"]),
    ("work_values", "6.4", ["supplement", "experiences"]),
    ("work_values", "6.5", ["supplement", "experiences"]),
    ("work_values", "6.6", ["supplement", "experiences"]),
]


# ------------------------------------------------------------------ #
#  6 个 Agent 的系统提示词模板                                            #
# ------------------------------------------------------------------ #

_SYSTEM_PROMPTS: dict[str, str] = {
    "skills": """\
你是一名 O*NET 职业技能评估专家。每次调用只需对评分表中给出的若干评分项打分，不处理其他内容。

【锚点映射规则】
O*NET 技能量表只有三个行为锚点：Level 2（初级）、Level 4（中级）、Level 6（高级）。
分值映射：Level 2 ≈ 2 分，Level 4 ≈ 4 分，Level 6 ≈ 6 分。
候选人表现明显超过某锚点但未达下一档时，可在该锚点 +0.5 到 +1 分内上调；
明显低于某锚点时，可在 -0.5 到 -1 分内下调。
最终 score 范围 1.0–7.0，步长 0.5。

【证据规则】
- evidence 必须直接引用候选人数据中的原文片段（职位名称、项目描述、技能列表等），禁止改写或编造
- anchor_level_hit 填写最接近的锚点 Level（2/4/6），无把握时填 null
- confidence 三档："高"（有明确行为证据）、"中"（有间接推断）、"无法判断"（无相关证据）
- 无相关证据时：score=null, anchor_level_hit=null, confidence="无法判断", evidence="无相关证据"

【输出格式】
只输出 JSON，结构与评分表中的子维度完全一致，每个 item 的 result 字段已填写。禁止输出任何解释文字。""",

    "knowledge": """\
你是一名 O*NET 知识储备评估专家。每次调用只需对评分表中给出的若干知识领域打分，不处理其他内容。

【信号优先级】
1. 教育背景（专业方向 + 学位层级）— 最强信号，专业直接对应的领域可直接定档
2. 工作经历和项目经验 — 次强信号
3. 证书、技能关键词 — 辅助参考

【锚点映射规则】
O*NET 知识量表使用三个锚点：Level 1（入门）、Level 4（熟练）、Level 7（专家）。
分值映射：Level 1 ≈ 1 分，Level 4 ≈ 4 分，Level 7 ≈ 7 分。
候选人表现在两档之间时，按比例内插（如接近 Level 4 但未到专家级 ≈ 5–6 分）。
最终 score 范围 1.0–7.0，步长 0.5。

【证据规则】
- evidence 必须引用候选人数据原文，禁止编造
- 候选人完全没有接触过的知识领域：score=null, confidence="无法判断", evidence="无相关证据"，不要强行打分
- anchor_level_hit 填最接近的锚点 Level（1/4/7）
- confidence："高"（专业/工作直接对口）、"中"（有间接经验）、"无法判断"

【输出格式】
只输出 JSON，结构与评分表中的子维度完全一致，每个 item 的 result 字段已填写。禁止输出任何解释文字。""",

    "abilities": """\
你是一名 O*NET 认知能力评估专家。每次调用只需对评分表中给出的若干认知能力项打分，不处理其他内容。

【双模式规则】
■ Mode A — 有 quiz_abilities 测试数据时（优先使用）：
  score = round(quiz_score_100 / 100 * 6 + 1, 1)
  score_range = null
  confidence = "高"
  仍需从简历中提取 evidence 作为行为佐证

■ Mode B — 无 quiz_abilities 数据时：
  score = null
  score_range = [推断下限, 推断上限]（基于简历行为和锚点对照，范围跨度不超过 2 分）
  confidence = "低"

【锚点映射规则】
对照 anchors 中 Level 2/4/6 行为描述（个别项有 Level 1/3/5）判断候选人水平区间。
Mode B 的 score_range 下限不低于最近偏低锚点分值，上限不超过最近偏高锚点分值 +1。

【证据规则】
- evidence 必须引用候选人数据原文，禁止编造
- anchor_level_hit：Mode A 时填最近锚点 Level；Mode B 时可填范围中点对应锚点或 null
- 无任何相关证据：score=null, score_range=null, confidence="无法判断", evidence="无相关证据"

【输出格式】
只输出 JSON，结构与评分表中的子维度完全一致，每个 item 的 result 字段已填写。禁止输出任何解释文字。""",

    "work_styles": """\
你是一名 O*NET 工作特质评估专家。每次调用只需对评分表中给出的若干工作特质项打分，不处理其他内容。

【双模式规则】
■ 有 Big Five 数据时（优先使用）：
  Step 1：将 Big Five 各维度百分制转为 1–7 分：trait_7 = round(raw_100 / 100 * 6 + 1, 1)
          神经质需反向处理：N_7_stable = 7 - N_7
  Step 2：按评分项中 bigfive_mapping 的权重计算基准分：base = Σ(trait_7 × weight)
  Step 3：依据简历行为对基准分微调 ±0.5（score_guidance 和 resume_signals 是参考依据）
  Step 4：最终 score = round(base × 0.6 + resume_score × 0.4, 1)
  confidence = "高"

■ 无 Big Five 数据时：
  纯简历推断，参照 score_guidance（low/mid/high/top 档描述）和 resume_signals 判断
  confidence = "中"

【通用规则】
- score 范围 1.0–7.0，步长 0.5
- evidence 必须引用候选人数据原文，禁止编造
- 无相关证据：score=null, confidence="无法判断", evidence="无相关证据"

【输出格式】
只输出 JSON，结构与评分表中的子维度完全一致，每个 item 的 result 字段已填写。禁止输出任何解释文字。""",

    "interests": """\
你是一名 Holland RIASEC 职业兴趣评估专家。每次调用对评分表中给出的全部 RIASEC 类型评分。

【双模式规则】
■ Mode A — 有 riasec 测试数据时（优先使用）：
  score = round(riasec_score_100 / 100 * 6 + 1, 1)
  keyword_match_count = null（测试数据优先，关键词匹配仅作佐证）
  matched_keywords = []（可选列举与测试结果吻合的关键词）
  confidence = "高"
  status = "done"

■ Mode B — 无 riasec 数据时：
  遍历评分表中每种 RIASEC 类型的 keywords（action + object）
  在候选人的 skills、经历描述、补充说明中逐一匹配
  score = round(matched_count / total_keywords_count * 6 + 1, 1)（最高 7，最低 1）
  keyword_match_count = 命中数量（整数）
  matched_keywords = 具体命中的关键词列表
  confidence = "低"
  status = "locked"

【通用规则】
- evidence 引用候选人数据中最能体现该类型兴趣的原文片段
- 6 种类型（R/I/A/S/E/C）全部输出，没有任何证据的类型 score 可填 1.0

【输出格式】
只输出 JSON，结构与评分表中的 riasec_items 完全一致，每个 item 的 result 字段已填写。禁止输出任何解释文字。""",

    "work_values": """\
你是一名工作价值观评估专家，基于 O*NET 工作价值观框架（TWA 理论）。每次调用只需对评分表中给出的若干需求项打分，不处理其他内容。

【信号优先级】
1. 个人补充说明（supplement）— 最强信号（候选人直接陈述的偏好、动机、期望）
2. 职位/公司/行业的选择模式 — 次强信号（如屡次选择创业公司 → 自主性高）
3. 简历行为描述 — 辅助信号

【评分规则】
- 对照每个需求项的 high_signal（高分信号）和 low_signal（低分信号）判断倾向方向
- score 范围 1.0–7.0，步长 0.5
- confidence 上限为 "中"（全部依赖行为推断，无标准测试），有明确陈述时可填 "中"，推断时填 "低"
- evidence 必须引用候选人数据原文，禁止编造
- 无任何相关证据：score=null, confidence="无法判断", evidence="无相关证据"

【输出格式】
只输出 JSON，结构与评分表中的子维度完全一致，每个 item 的 result 字段已填写。禁止输出任何解释文字。""",
}


# ------------------------------------------------------------------ #
#  核心评分逻辑                                                         #
# ------------------------------------------------------------------ #

def _get_llm() -> LLMProvider:
    """创建 LLM 实例（复用主 Agent 的模型配置）。"""
    return LLMProvider(
        model=MAIN_AGENT_CONFIG["model"],
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )


def _extract_sub_dimension(scoring_table: dict, sub_dim_id: str) -> dict | None:
    """从完整评分表中提取单个子维度的评分项。"""
    # interests 没有 sub_dimensions，整体就是一个评分单元
    if sub_dim_id == "5":
        return {
            "meta": scoring_table.get("meta"),
            "riasec_items": scoring_table.get("riasec_items"),
        }

    for sd in scoring_table.get("sub_dimensions", []):
        if sd.get("sub_dimension_id") == sub_dim_id:
            return {
                "meta": scoring_table.get("meta"),
                "sub_dimension": sd,
            }
    return None


def _extract_candidate_fields(candidate_data: dict, fields: list[str]) -> dict:
    """从完整候选人数据中只提取指定字段，减少 token 消耗。"""
    result = {}
    for field in fields:
        # 顶层字段
        if field in candidate_data:
            result[field] = candidate_data[field]
        # resume 内嵌字段
        elif "resume" in candidate_data and field in candidate_data["resume"]:
            result[field] = candidate_data["resume"][field]
    return result


def _parse_json_from_llm(raw: str) -> dict:
    """从 LLM 输出中提取 JSON。"""
    import re
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    text = match.group(1).strip() if match else raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([\}\]])", r"\1", text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {"raw_output": raw, "parse_error": True}


async def _score_sub_dimension(
    llm: LLMProvider,
    agent_key: str,
    sub_dim_id: str,
    scoring_table_slice: dict,
    candidate_slice: dict,
) -> dict:
    """
    对单个子维度调用 LLM 评分，返回填充后的 result。
    """
    system_prompt = _SYSTEM_PROMPTS[agent_key]

    user_message = (
        f"=== 评分表（本次只评估子维度 {sub_dim_id}） ===\n"
        f"{json.dumps(scoring_table_slice, ensure_ascii=False, indent=2)}\n\n"
        f"=== 候选人数据 ===\n"
        f"{json.dumps(candidate_slice, ensure_ascii=False, indent=2)}\n\n"
        f"请严格按照评分表中 result 字段的结构，填写每个评分项的 score / anchor_level_hit / evidence / confidence，然后输出完整的子维度 JSON。"
    )

    t0 = time.perf_counter()
    raw_output, elapsed_ms, usage = await run_prompt(
        system_prompt=system_prompt,
        user_message=user_message,
        llm=llm,
        agent_name=f"score_{agent_key}_{sub_dim_id}",
    )
    elapsed = int((time.perf_counter() - t0) * 1000)

    logger.info(
        f"[评分完成] {agent_key}/{sub_dim_id}  "
        f"耗时={elapsed}ms  tokens={usage.get('total_tokens', '?')}"
    )

    result = _parse_json_from_llm(raw_output)
    result["_meta"] = {
        "agent_key": agent_key,
        "sub_dimension_id": sub_dim_id,
        "elapsed_ms": elapsed,
        "tokens": usage,
    }
    return result


# ------------------------------------------------------------------ #
#  汇总逻辑                                                            #
# ------------------------------------------------------------------ #

def _aggregate_results(all_results: list[dict]) -> dict:
    """将 22 个子维度的评分结果汇总为 6 个维度的总表。"""
    dimensions = {}

    for result in all_results:
        meta = result.get("_meta", {})
        agent_key = meta.get("agent_key", "unknown")

        if agent_key not in dimensions:
            dimensions[agent_key] = {
                "dimension": agent_key,
                "sub_dimensions": [],
                "elapsed_ms": 0,
                "total_tokens": 0,
            }

        dim = dimensions[agent_key]
        dim["sub_dimensions"].append(result)
        dim["elapsed_ms"] += meta.get("elapsed_ms", 0)
        dim["total_tokens"] += meta.get("tokens", {}).get("total_tokens", 0)

    # 计算每个维度的汇总分
    for dim in dimensions.values():
        scores = []
        for sd in dim["sub_dimensions"]:
            # 尝试从子维度结果中提取 items 的分数
            sub_dim_data = sd.get("sub_dimension", sd)
            items = sub_dim_data.get("items", sub_dim_data.get("needs", []))
            if isinstance(items, list):
                for item in items:
                    r = item.get("result", {})
                    s = r.get("score")
                    if isinstance(s, (int, float)):
                        scores.append(s)
            # riasec 特殊处理
            riasec_items = sd.get("riasec_items", [])
            if isinstance(riasec_items, list):
                for item in riasec_items:
                    r = item.get("result", {})
                    s = r.get("score")
                    if isinstance(s, (int, float)):
                        scores.append(s)

        if scores:
            dim["overall_score"] = round(sum(scores) / len(scores), 2)
            dim["highlights"] = [s for s in scores if s >= 5.5]
            dim["focus_areas"] = [s for s in scores if s <= 4.5]
        else:
            dim["overall_score"] = None

    return dimensions


# ------------------------------------------------------------------ #
#  工具注册                                                             #
# ------------------------------------------------------------------ #

async def _load_candidate_from_db(user_id: int) -> dict | None:
    """从 candidates 表读取候选人完整数据（通过 user_id），组装为评估所需格式。"""
    if memory_db._pool is None:
        return None
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT name, age, city, current_title, target_role,
                          years_of_experience, education, resume_raw, supplement,
                          bigfive, riasec, quiz_abilities, quiz_knowledge, third_party
                   FROM candidates WHERE user_id = %s""",
                (user_id,),
            )
            row = await cur.fetchone()
    if not row:
        return None

    def _j(v):
        if v is None:
            return None
        return json.loads(v) if isinstance(v, str) else v

    return {
        "resume": {
            "candidate": {
                "name": row["name"],
                "age": row["age"],
                "city": row["city"],
                "current_title": row["current_title"],
                "target_role": row["target_role"],
                "years_of_experience": row["years_of_experience"],
                "education": _j(row["education"]) or [],
            },
            **(_j(row["resume_raw"]) or {}),
        },
        "supplement": row["supplement"] or "",
        "bigfive":        _j(row["bigfive"]),
        "riasec":         _j(row["riasec"]),
        "quiz_abilities": _j(row["quiz_abilities"]),
        "quiz_knowledge": _j(row["quiz_knowledge"]),
        "third_party":    _j(row["third_party"]),
    }


async def _db_write_assessment_job(assessment_id: str, candidate_id: int, status: str) -> None:
    """写入 / 更新 assessment_jobs 状态。"""
    if memory_db._pool is None:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO assessment_jobs (assessment_id, session_id, status)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE status=VALUES(status), updated_at=NOW()""",
                (assessment_id, str(candidate_id), status),
            )


async def _db_upsert_dimension(assessment_id: str, dim: dict) -> None:
    """将单个维度结果写入 assessment_dimensions。"""
    if memory_db._pool is None:
        return
    dimension = dim.get("dimension", "unknown")
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO assessment_dimensions
                   (assessment_id, dimension, overall_score, confidence,
                    dimension_summary, sub_dimensions, highlights, focus_areas, extra, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                     overall_score=VALUES(overall_score),
                     confidence=VALUES(confidence),
                     dimension_summary=VALUES(dimension_summary),
                     sub_dimensions=VALUES(sub_dimensions),
                     highlights=VALUES(highlights),
                     focus_areas=VALUES(focus_areas),
                     extra=VALUES(extra),
                     status=VALUES(status)""",
                (
                    assessment_id, dimension,
                    dim.get("overall_score"),
                    dim.get("confidence"),
                    dim.get("dimension_summary"),
                    json.dumps(dim.get("sub_dimensions", []), ensure_ascii=False),
                    json.dumps(dim.get("highlights", []), ensure_ascii=False),
                    json.dumps(dim.get("focus_areas", []), ensure_ascii=False),
                    json.dumps({k: v for k, v in dim.items()
                                if k not in {"dimension", "overall_score", "confidence",
                                             "dimension_summary", "sub_dimensions",
                                             "highlights", "focus_areas", "status"}},
                               ensure_ascii=False),
                    dim.get("status", "done"),
                ),
            )


@tool(
    description=(
        "启动个人能力评估。根据 candidate_id 从数据库加载候选人数据，"
        "按 22 个子维度并发调用 LLM 评分，汇总后将结果写入数据库，"
        "返回 assessment_id 和 6 个维度的完整评估 JSON，供后续生成报告使用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "candidate_id": {
                "type": "integer",
                "description": "candidates 表中的候选人 ID",
            }
        },
        "required": ["candidate_id"],
    },
)
async def run_assessment(candidate_id: int) -> str:
    """从 DB 加载候选人数据，执行完整 22 子维度并发评估，结果写库后返回。"""
    assessment_id = uuid.uuid4().hex[:12]
    logger.info(f"[评估启动] assessment_id={assessment_id}  candidate_id={candidate_id}")
    t_start = time.perf_counter()

    # ---- 1. 从 DB 加载候选人数据 ----
    candidate_data = await _load_candidate_from_db(candidate_id)
    if not candidate_data:
        return json.dumps({"error": f"candidate_id={candidate_id} 不存在"}, ensure_ascii=False)

    candidate_name = candidate_data["resume"]["candidate"].get("name", "未知")
    logger.info(f"[评估] 候选人数据已加载：{candidate_name}")

    await _db_write_assessment_job(assessment_id, candidate_id, "running")

    # ---- 2. 构建 22 个子维度评分任务 ----
    llm = _get_llm()
    tasks = []

    for agent_key, sub_dim_id, fields in _SUB_DIMENSION_TASKS:
        scoring_table = _SCORING_TABLES.get(agent_key)
        if not scoring_table:
            logger.warning(f"[评估] 评分表未加载：{agent_key}，跳过")
            continue

        # 提取该子维度的评分项
        scoring_slice = _extract_sub_dimension(scoring_table, sub_dim_id)
        if not scoring_slice:
            logger.warning(f"[评估] 子维度不存在：{agent_key}/{sub_dim_id}，跳过")
            continue

        # 提取该子维度需要的候选人数据
        candidate_slice = _extract_candidate_fields(candidate_data, fields)

        # 创建异步任务
        tasks.append(
            _score_sub_dimension(llm, agent_key, sub_dim_id, scoring_slice, candidate_slice)
        )

    logger.info(f"[评估] 共 {len(tasks)} 个子维度评分任务，开始并发执行...")

    # ---- 3. 并发执行所有评分任务 ----
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 处理异常
    valid_results = []
    errors = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            agent_key, sub_dim_id, _ = _SUB_DIMENSION_TASKS[i]
            errors.append(f"{agent_key}/{sub_dim_id}: {str(r)}")
            logger.error(f"[评估] 评分失败：{agent_key}/{sub_dim_id} → {r}")
        else:
            valid_results.append(r)

    # ---- 4. 汇总结果 ----
    dimensions = _aggregate_results(valid_results)

    total_elapsed = int((time.perf_counter() - t_start) * 1000)
    logger.info(f"[评估完成] assessment_id={assessment_id}  总耗时={total_elapsed}ms")

    # ---- 5. 写入 DB ----
    for key, dim in dimensions.items():
        dim["dimension"] = key
        await _db_upsert_dimension(assessment_id, dim)

    status = "done" if not errors else "partial"
    await _db_write_assessment_job(assessment_id, candidate_id, status)
    logger.info(f"[评估完成] assessment_id={assessment_id}  总耗时={total_elapsed}ms  status={status}")

    # ---- 6. 构建返回值（assessment_id + 各维度完整数据，供 generate_and_save_report 使用）----
    return json.dumps({
        "assessment_id": assessment_id,
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "status": status,
        "elapsed_ms": total_elapsed,
        "errors": errors if errors else None,
        "dimensions": {
            key: {
                "overall_score": dim.get("overall_score"),
                "sub_dimensions": dim.get("sub_dimensions", []),
                "highlights": dim.get("highlights", []),
                "focus_areas": dim.get("focus_areas", []),
                "elapsed_ms": dim.get("elapsed_ms"),
            }
            for key, dim in dimensions.items()
        },
    }, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ #
#  报告生成 & 存储工具                                                   #
# ------------------------------------------------------------------ #

# 报告各模块的 system prompt（内部 LLM 调用，不经过 agent 层）
_REPORT_SECTION_PROMPTS: dict[str, str] = {

    "narrative_summary": """\
你是一位高级职业发展顾问，基于候选人的六维能力评估结果，撰写一段个性化叙事摘要。
要求：
- 整体画像标签（4-6个字）
- 3-5句整体概括，体现跨维度联系（技能+知识的T型结构、工作特质对技能的放大/制约、量表与简历行为的一致性/矛盾）
- 最突出的3张牌（跨维度得分最高、置信度最高的3个子维度标签）
- 成长方向一句话点题（必须与候选人目标岗位挂钩）
- 5-6个能力画像关键词（简洁有力）
输出严格 JSON：
{"persona_label":"...","narrative_intro":"...","top_cards":["..."],"next_direction":"...","keywords":["..."]}""",

    "strength_top3": """\
你是一位职业顾问，从候选人六维评估结果中提炼TOP3核心优势。
规则：优先选 score 最高且 confidence 为"高"的子维度，每项含：
- title：维度名+分数
- career_meaning：2-3句，解释在目标岗位的独特价值
- how_to_amplify：2-3条6个月内可执行的具体行动
输出严格 JSON：
{"top3_strengths":[{"ref_dimension":"...","ref_sub_id":"...","title":"...","career_meaning":"...","how_to_amplify":"..."}]}""",

    "growth_top3": """\
你是一位职业顾问，从候选人六维评估结果中提炼TOP3最高价值提升方向。
规则：从 focus_areas 和低分子维度中选与目标岗位相关度最高的3个，每项含：
- title：维度名+当前分数
- current_state：用具体分数和行为证据描述差距
- target_state：可量化的6个月目标
- action_plan：{month_1, month_2_3, month_4_6}
- expected_outcome：可观察/可量化的预期效果
输出严格 JSON：
{"top3_improvements":[{"ref_dimension":"...","ref_sub_id":"...","title":"...","current_state":"...","target_state":"...","action_plan":{"month_1":"...","month_2_3":"...","month_4_6":"..."},"expected_outcome":"..."}]}""",

    "dimension_skills": """\
你是报告撰写专家，将技能维度评估数据改写为面向候选人的第二人称报告块。
对每个子维度：evidence 原样保留，meaning 改写为第二人称2-4句，
计算 tag(highlight≥5.5/focus≤4.5/normal)、star_rating(round(score/7*5))、collapsed(normal时true)。
输出严格 JSON：
{"block_id":"skills","dimension_label":"技能画像 Skills","overall_score":0.0,"confidence":"高/中",
"dimension_summary_prose":"...","sub_dimensions":[{"id":"...","name":"...","score":0.0,"tag":"...","star_rating":0,"evidence_bullets":[],"meaning_prose":"...","collapsed":false}],"tech_gap":[]}""",

    "dimension_knowledge": """\
你是报告撰写专家，将知识维度评估数据改写为面向候选人的第二人称报告块。规则同技能维度。
输出严格 JSON：
{"block_id":"knowledge","dimension_label":"知识储备 Knowledge","overall_score":0.0,"confidence":"高/中",
"dimension_summary_prose":"...","sub_dimensions":[{"id":"...","name":"...","score":0.0,"tag":"...","star_rating":0,"evidence_bullets":[],"meaning_prose":"...","collapsed":false}]}""",

    "dimension_abilities": """\
你是报告撰写专家，将认知能力维度评估数据改写为面向候选人的第二人称报告块。
支持 done/locked 双模式：done时正常输出各子维度；locked时输出 unlock_intro + estimate_ranges + unlock_cta。
输出严格 JSON（done模式）：
{"block_id":"abilities","status":"done","dimension_label":"认知能力 Abilities","overall_score":0.0,"confidence":"高",
"dimension_summary_prose":"...","sub_dimensions":[{"id":"...","name":"...","score":0.0,"tag":"...","star_rating":0,"evidence_bullets":[],"meaning_prose":"...","collapsed":false}]}""",

    "dimension_work_styles": """\
你是报告撰写专家，将工作特质维度评估数据改写为面向候选人的第二人称报告块。
每个子维度额外输出 caution_prose（风险提示，第二人称1-2句）和 sub_items（原样保留）。
输出严格 JSON：
{"block_id":"work_styles","dimension_label":"工作特质 Work Styles","overall_score":0.0,"confidence":"高/中",
"dimension_summary_prose":"...","bigfive_display":{},"sub_dimensions":[{"id":"...","name":"...","score":0.0,"tag":"...","star_rating":0,"sub_items":{},"evidence_bullets":[],"meaning_prose":"...","caution_prose":"...","collapsed":false}]}""",

    "dimension_interests": """\
你是报告撰写专家，将职业兴趣维度评估数据改写为面向候选人的第二人称报告块。
支持 done/locked 双模式。done时：holland_code + 6个类型（前3高分tag=highlight）+ suitable_roles。
输出严格 JSON（done模式）：
{"block_id":"interests","status":"done","dimension_label":"职业兴趣 Interests","holland_code":"...","dimension_summary_prose":"...","suitable_roles":[],"sub_dimensions":[{"type":"...","name":"...","score":0.0,"tag":"...","keywords_matched":[],"meaning_prose":"...","collapsed":false}]}""",

    "dimension_work_values": """\
你是报告撰写专家，将工作价值观维度评估数据改写为面向候选人的第二人称报告块。
每个子维度额外输出 career_advice_prose（具体可操作建议，第二人称）。
输出严格 JSON：
{"block_id":"work_values","dimension_label":"工作价值观 Work Values","overall_score":0.0,"confidence":"中",
"persona_tag":"...","dimension_summary_prose":"...","sub_dimensions":[{"id":"...","name":"...","score":0.0,"tag":"...","star_rating":0,"evidence_bullets":[],"meaning_prose":"...","career_advice_prose":"...","collapsed":false}]}""",
}

# section_key → 依赖的维度（用于从评估结果中提取输入）
_SECTION_DIM_MAP: dict[str, list[str]] = {
    "narrative_summary":   ["skills", "knowledge", "abilities", "work_styles", "interests", "work_values"],
    "strength_top3":       ["skills", "knowledge", "abilities", "work_styles", "interests", "work_values"],
    "growth_top3":         ["skills", "knowledge", "abilities", "work_styles", "interests", "work_values"],
    "dimension_skills":    ["skills"],
    "dimension_knowledge": ["knowledge"],
    "dimension_abilities": ["abilities"],
    "dimension_work_styles": ["work_styles"],
    "dimension_interests": ["interests"],
    "dimension_work_values": ["work_values"],
}


def _build_score_table(dimensions: dict) -> dict:
    """规则生成维度得分总览表（不调用 LLM）。"""
    rows = []
    for dim_key, dim in dimensions.items():
        rows.append({
            "dimension": dim_key,
            "name_zh": {
                "skills": "技能画像", "knowledge": "知识储备", "abilities": "认知能力",
                "work_styles": "工作特质", "interests": "职业兴趣", "work_values": "工作价值观",
            }.get(dim_key, dim_key),
            "overall_score": dim.get("overall_score"),
            "confidence": dim.get("confidence"),
            "status": dim.get("status", "done"),
            "highlights": dim.get("highlights", []),
            "focus_areas": dim.get("focus_areas", []),
        })
    return {"block_id": "score_table", "rows": rows}


async def _generate_section(
    llm: LLMProvider,
    section_key: str,
    dimensions: dict,
) -> dict:
    """调用 LLM 生成单个报告模块，返回解析后的 dict。"""
    import re

    dep_dims = _SECTION_DIM_MAP.get(section_key, [])
    input_data = {k: dimensions[k] for k in dep_dims if k in dimensions}

    system_prompt = _REPORT_SECTION_PROMPTS[section_key]
    user_message = (
        f"以下是评估数据，请按要求生成报告块 JSON：\n\n"
        f"{json.dumps(input_data, ensure_ascii=False, indent=2)}"
    )

    t0 = time.perf_counter()
    raw, elapsed_ms, _ = await run_prompt(
        system_prompt=system_prompt,
        user_message=user_message,
        llm=llm,
        agent_name=f"report_{section_key}",
    )
    elapsed = int((time.perf_counter() - t0) * 1000)
    logger.info(f"[报告] {section_key} 生成完成  耗时={elapsed}ms")

    # 提取 JSON
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    text = match.group(1).strip() if match else raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([\}\]])", r"\1", text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {"section_key": section_key, "raw_output": raw, "parse_error": True}


async def _db_save_report(assessment_id: str, sections: dict) -> None:
    """将报告各模块写入 assessment_report_blocks 表。"""
    if memory_db._pool is None:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            for block_id, block in sections.items():
                await cur.execute(
                    """INSERT INTO assessment_report_blocks (assessment_id, block_id, block_json)
                       VALUES (%s, %s, %s)
                       ON DUPLICATE KEY UPDATE block_json=VALUES(block_json), generated_at=NOW()""",
                    (assessment_id, block_id, json.dumps(block, ensure_ascii=False)),
                )


async def _load_dimensions_from_db(assessment_id: str) -> dict:
    """从 assessment_dimensions 表读取所有维度评估结果。"""
    if memory_db._pool is None:
        return {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT dimension, overall_score, confidence, dimension_summary,
                          sub_dimensions, highlights, focus_areas, extra, status
                   FROM assessment_dimensions WHERE assessment_id=%s""",
                (assessment_id,),
            )
            rows = await cur.fetchall()

    result = {}
    for row in rows:
        def _j(v):
            if v is None:
                return None
            return json.loads(v) if isinstance(v, str) else v

        extra = _j(row["extra"]) or {}
        dim = {
            "dimension": row["dimension"],
            "overall_score": row["overall_score"],
            "confidence": row["confidence"],
            "dimension_summary": row["dimension_summary"],
            "sub_dimensions": _j(row["sub_dimensions"]) or [],
            "highlights": _j(row["highlights"]) or [],
            "focus_areas": _j(row["focus_areas"]) or [],
            "status": row["status"],
            **extra,
        }
        result[row["dimension"]] = dim
    return result


@tool(
    description=(
        "根据已完成的评估结果生成完整报告并存入数据库。"
        "内部从数据库读取评估数据，并发调用 LLM 生成 narrative_summary / strength_top3 / growth_top3 / 6个维度画像块，"
        "规则生成 score_table，全部写入 assessment_report_blocks 表。"
        "必须在 run_assessment 完成后调用，只需传入 assessment_id。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "assessment_id": {
                "type": "string",
                "description": "run_assessment 返回的 assessment_id",
            },
        },
        "required": ["assessment_id"],
    },
)
async def generate_and_save_report(assessment_id: str) -> str:
    """从 DB 读取评估数据，并发生成所有报告模块并写入 DB。"""
    dimensions = await _load_dimensions_from_db(assessment_id)
    if not dimensions:
        return json.dumps({"error": f"assessment_id={assessment_id} 无评估数据，请先调用 run_assessment"}, ensure_ascii=False)

    llm = _get_llm()
    t_start = time.perf_counter()
    logger.info(f"[报告生成] assessment_id={assessment_id}  开始并发生成 {len(_SECTION_DIM_MAP)} 个模块")

    # 并发生成所有 LLM 模块
    llm_section_keys = list(_REPORT_SECTION_PROMPTS.keys())
    tasks = [_generate_section(llm, key, dimensions) for key in llm_section_keys]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    sections: dict[str, dict] = {}
    errors = []
    for key, result in zip(llm_section_keys, results):
        if isinstance(result, Exception):
            errors.append(f"{key}: {result}")
            logger.error(f"[报告] {key} 生成失败：{result}")
        else:
            sections[key] = result

    # 规则生成 score_table
    sections["score_table"] = _build_score_table(dimensions)

    # 写入 DB
    await _db_save_report(assessment_id, sections)

    total_elapsed = int((time.perf_counter() - t_start) * 1000)
    logger.info(f"[报告完成] assessment_id={assessment_id}  耗时={total_elapsed}ms  模块数={len(sections)}")

    return json.dumps({
        "status": "done" if not errors else "partial",
        "assessment_id": assessment_id,
        "sections_saved": list(sections.keys()),
        "errors": errors if errors else None,
        "elapsed_ms": total_elapsed,
    }, ensure_ascii=False)
