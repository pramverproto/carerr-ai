"""
职业选择工具集。

match_careers:
    根据候选人评估结果，通过三路并发召回 + JD 市场验证 + LLM 审核，
    推荐最匹配的 3-5 个职业。

召回策略（互相兜底）：
  路线1: 语义召回 —— 候选人画像文本 embed → Qdrant onet_occupations（Top 20）
  路线2: Holland Code 精确召回 —— RIASEC 高分类型 → MySQL onet_occupations（Top 20）
  路线3: 六维得分范围过滤 —— 候选人六维得分 ± 容差 → MySQL onet_occupations（Top 20）

合并去重 → Top 15 → JD 验证（Qdrant jobs）→ LLM 审核 → 输出 3-5 个推荐职业
"""

import asyncio
import json
import os
from pathlib import Path

import aiomysql
from dotenv import load_dotenv
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, Range

from agent.tools.registry import tool
from agent.agent_config import DB_CONFIG
from agent.logger import get_logger
from agent.runner import run_prompt
import agent.memory.db as memory_db

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

logger = get_logger("career")

# ------------------------------------------------------------------ #
#  配置
# ------------------------------------------------------------------ #

QDRANT_URL       = os.getenv("QDRANT_URL", "http://115.120.251.185:6333")
ONET_COLLECTION  = "onet_occupations"
JOBS_COLLECTION  = "jobs"
EMBED_MODEL      = "text-embedding-3-small"
OPENAI_API_KEY   = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL  = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")

RECALL_TOP_N     = 20   # 每路召回数量
MERGE_TOP_N      = 15   # 合并后进入 JD 验证的数量
JD_TOP_K         = 5    # 每个职业查几条 JD
RIASEC_THRESHOLD_100 = 50  # Holland Code 路线最低分（0-100原始分，对应 1-7 量表约 4.4）
RIASEC_THRESHOLD_7  = 4.0  # Holland Code 路线最低分（1-7量表）
DIM_TOLERANCE    = 1.5  # 六维得分容差


# ------------------------------------------------------------------ #
#  数据加载：从 assessment_dimensions 读取候选人评估结果
# ------------------------------------------------------------------ #

async def _load_assessment(assessment_id: str) -> dict | None:
    """从 DB 读取评估维度数据，返回结构化 dict。"""
    if memory_db._pool is None:
        return None
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT dimension, overall_score, sub_dimensions, highlights, focus_areas, status
                   FROM assessment_dimensions WHERE assessment_id = %s""",
                (assessment_id,),
            )
            rows = await cur.fetchall()

    if not rows:
        return None

    dims = {}
    for row in rows:
        sub = row["sub_dimensions"]
        dims[row["dimension"]] = {
            "overall_score": float(row["overall_score"]) if row["overall_score"] else None,
            "sub_dimensions": json.loads(sub) if isinstance(sub, str) else (sub or []),
            "highlights": json.loads(row["highlights"]) if isinstance(row["highlights"], str) else [],
            "focus_areas": json.loads(row["focus_areas"]) if isinstance(row["focus_areas"], str) else [],
            "status": row["status"],
        }
    return dims


async def _load_candidate_basic(assessment_id: str) -> dict:
    """从 assessment_jobs 拿 candidate_id，再从 candidates 读基本信息。"""
    if memory_db._pool is None:
        return {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT session_id FROM assessment_jobs WHERE assessment_id = %s",
                (assessment_id,),
            )
            job_row = await cur.fetchone()
        if not job_row:
            return {}
        candidate_id = job_row["session_id"]
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT name, target_role, years_of_experience, riasec FROM candidates WHERE id = %s",
                (candidate_id,),
            )
            row = await cur.fetchone()
    if not row:
        return {}
    riasec = row["riasec"]
    return {
        "name": row["name"],
        "target_role": row["target_role"] or "",
        "years_of_experience": row["years_of_experience"] or 0,
        "riasec": json.loads(riasec) if isinstance(riasec, str) and riasec else {},
    }


# ------------------------------------------------------------------ #
#  候选人画像文本构建（用于语义 embed）
# ------------------------------------------------------------------ #

def _build_candidate_profile_text(dims: dict, candidate: dict) -> str:
    """将评估数据构建为候选人画像文本，用于语义向量检索。"""
    parts = []

    if candidate.get("target_role"):
        parts.append(f"Target role: {candidate['target_role']}")

    # 六维得分概述
    dim_lines = []
    for key, label in [
        ("skills", "Skills"), ("knowledge", "Knowledge"), ("abilities", "Abilities"),
        ("work_styles", "Work Styles"), ("work_values", "Work Values"),
    ]:
        d = dims.get(key, {})
        score = d.get("overall_score")
        if score is not None:
            dim_lines.append(f"{label}: {score:.1f}/7")
    if dim_lines:
        parts.append("Six-dimension profile: " + ", ".join(dim_lines))

    # RIASEC（按分数排序取前3）
    riasec = candidate.get("riasec") or {}
    if not riasec:
        # 从 interests 维度的 sub_dimensions 提取
        interests_dim = dims.get("interests", {})
        for sd in interests_dim.get("sub_dimensions", []):
            riasec_items = sd.get("riasec_items", [])
            for item in riasec_items:
                t = item.get("type") or item.get("riasec_type")
                r = item.get("result", {})
                s = r.get("score")
                if t and s:
                    riasec[t] = s
    if riasec:
        # 只取 R/I/A/S/E/C 字母键（排除 holland_code 等字符串字段）
        riasec_scores = {k: v for k, v in riasec.items()
                         if k in "RIASEC" and isinstance(v, (int, float))}
        if riasec_scores:
            sorted_riasec = sorted(riasec_scores.items(), key=lambda x: -x[1])[:3]
            riasec_str = ", ".join(f"{k}={v}" for k, v in sorted_riasec)
            riasec_names = {"R": "Realistic", "I": "Investigative", "A": "Artistic",
                            "S": "Social", "E": "Enterprising", "C": "Conventional"}
            top_names = " + ".join(riasec_names.get(k, k) for k, _ in sorted_riasec)
            parts.append(f"Holland interests: {top_names} ({riasec_str})")

    # 亮点维度关键词
    highlights = []
    for key in ["skills", "knowledge", "abilities", "work_styles", "work_values"]:
        d = dims.get(key, {})
        for sd in d.get("sub_dimensions", []):
            sub = sd.get("sub_dimension", sd)
            items = sub.get("items", sub.get("needs", []))
            for item in items:
                r = item.get("result", {})
                s = r.get("score")
                if isinstance(s, (int, float)) and s >= 5.5:
                    name = item.get("name", "")
                    if name:
                        highlights.append(name)
    if highlights:
        parts.append("Key strengths: " + ", ".join(highlights[:8]))

    return "\n".join(parts)


# ------------------------------------------------------------------ #
#  路线1：语义召回（Qdrant）
# ------------------------------------------------------------------ #

async def _recall_semantic(profile_text: str, qdrant: AsyncQdrantClient,
                           openai: AsyncOpenAI) -> list[dict]:
    """候选人画像 embed → Qdrant onet_occupations 语义检索。"""
    resp = await openai.embeddings.create(model=EMBED_MODEL, input=[profile_text])
    vec = resp.data[0].embedding

    results = await qdrant.query_points(
        collection_name=ONET_COLLECTION,
        query=vec,
        limit=RECALL_TOP_N,
        with_payload=True,
        with_vectors=False,
    )

    occupations = []
    for p in results.points:
        payload = p.payload or {}
        occupations.append({
            "onetsoc_code": payload.get("onetsoc_code", str(p.id)),
            "title": payload.get("title", ""),
            "description": payload.get("description", ""),
            "job_zone": payload.get("job_zone"),
            "riasec": {k: payload.get(f"riasec_{k}") for k in "RIASEC"},
            "dim_abilities":   payload.get("dim_abilities"),
            "dim_skills":      payload.get("dim_skills"),
            "dim_knowledge":   payload.get("dim_knowledge"),
            "dim_work_styles": payload.get("dim_work_styles"),
            "dim_work_values": payload.get("dim_work_values"),
            "tech_tools":  payload.get("tech_tools", []),
            "core_tasks":  payload.get("core_tasks", []),
            "_score": float(p.score),
            "_sources": ["semantic"],
        })
    logger.info(f"[语义召回] {len(occupations)} 条")
    return occupations


# ------------------------------------------------------------------ #
#  路线2：Holland Code 精确召回（MySQL）
# ------------------------------------------------------------------ #

async def _recall_holland(riasec: dict) -> list[dict]:
    """RIASEC 高分类型 → MySQL 精确过滤。"""
    if not riasec or memory_db._pool is None:
        return []

    # 取分数 >= 阈值的类型（只处理 R/I/A/S/E/C 数值字段），按分数降序最多取3个
    # 自适应量纲：>7 视为 0-100 原始分，否则视为 1-7 量表分
    def _is_high(v):
        if v > 7:   # 0-100 原始分
            return v >= RIASEC_THRESHOLD_100
        return v >= RIASEC_THRESHOLD_7  # 1-7 量表

    high_types = sorted(
        [(k, v) for k, v in riasec.items()
         if k in "RIASEC" and isinstance(v, (int, float)) and _is_high(v)],
        key=lambda x: -x[1]
    )[:3]

    if not high_types:
        return []

    # 候选人分数转换为 onet_occupations 的 0-7 量纲（onet 用 OI scale 0-7）
    def _to_onet_scale(v):
        if v > 7:  # 0-100 原始分 → 0-7
            return round(v / 100 * 7, 2)
        return v   # 已是 1-7

    # 动态拼接 WHERE 条件：取任意一个高分类型达标
    conditions = " OR ".join([f"riasec_{k} >= %s" for k, _ in high_types])
    params = [_to_onet_scale(v) * 0.7 for _, v in high_types]  # 略低于候选人分，扩大召回

    # 按高分类型之和排序
    order_cols = " + ".join([f"COALESCE(riasec_{k}, 0)" for k, _ in high_types])
    sql = f"""
        SELECT onetsoc_code, title, description, job_zone,
               riasec_R, riasec_I, riasec_A, riasec_S, riasec_E, riasec_C,
               dim_abilities, dim_skills, dim_knowledge, dim_work_styles, dim_work_values,
               tech_tools_json, core_tasks_json
        FROM onet_occupations
        WHERE {conditions}
        ORDER BY ({order_cols}) DESC
        LIMIT {RECALL_TOP_N}
    """
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

    occupations = []
    for row in rows:
        occupations.append({
            "onetsoc_code": row["onetsoc_code"],
            "title": row["title"],
            "description": (row.get("description") or "")[:300],
            "job_zone": row.get("job_zone"),
            "riasec": {k: row.get(f"riasec_{k}") for k in "RIASEC"},
            "dim_abilities":   row.get("dim_abilities"),
            "dim_skills":      row.get("dim_skills"),
            "dim_knowledge":   row.get("dim_knowledge"),
            "dim_work_styles": row.get("dim_work_styles"),
            "dim_work_values": row.get("dim_work_values"),
            "tech_tools": json.loads(row["tech_tools_json"]) if row.get("tech_tools_json") else [],
            "core_tasks": json.loads(row["core_tasks_json"]) if row.get("core_tasks_json") else [],
            "_score": 0.0,
            "_sources": ["holland"],
        })
    logger.info(f"[Holland召回] {len(occupations)} 条")
    return occupations


# ------------------------------------------------------------------ #
#  路线3：六维得分范围过滤（MySQL）
# ------------------------------------------------------------------ #

async def _recall_dim_filter(dims: dict) -> list[dict]:
    """候选人六维得分 ± 容差 → MySQL 范围过滤。"""
    if memory_db._pool is None:
        return []

    dim_map = {
        "abilities":   "dim_abilities",
        "skills":      "dim_skills",
        "knowledge":   "dim_knowledge",
        "work_styles": "dim_work_styles",
        "work_values": "dim_work_values",
    }

    conditions = []
    params = []
    for key, col in dim_map.items():
        score = (dims.get(key) or {}).get("overall_score")
        if score is not None:
            conditions.append(
                f"({col} IS NULL OR {col} BETWEEN %s AND %s)"
            )
            params.extend([max(0, score - DIM_TOLERANCE), score + DIM_TOLERANCE])

    if not conditions:
        return []

    sql = f"""
        SELECT onetsoc_code, title, description, job_zone,
               riasec_R, riasec_I, riasec_A, riasec_S, riasec_E, riasec_C,
               dim_abilities, dim_skills, dim_knowledge, dim_work_styles, dim_work_values,
               tech_tools_json, core_tasks_json
        FROM onet_occupations
        WHERE {" AND ".join(conditions)}
        LIMIT {RECALL_TOP_N}
    """
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

    occupations = []
    for row in rows:
        occupations.append({
            "onetsoc_code": row["onetsoc_code"],
            "title": row["title"],
            "description": (row.get("description") or "")[:300],
            "job_zone": row.get("job_zone"),
            "riasec": {k: row.get(f"riasec_{k}") for k in "RIASEC"},
            "dim_abilities":   row.get("dim_abilities"),
            "dim_skills":      row.get("dim_skills"),
            "dim_knowledge":   row.get("dim_knowledge"),
            "dim_work_styles": row.get("dim_work_styles"),
            "dim_work_values": row.get("dim_work_values"),
            "tech_tools": json.loads(row["tech_tools_json"]) if row.get("tech_tools_json") else [],
            "core_tasks": json.loads(row["core_tasks_json"]) if row.get("core_tasks_json") else [],
            "_score": 0.0,
            "_sources": ["dim_filter"],
        })
    logger.info(f"[六维过滤召回] {len(occupations)} 条")
    return occupations


# ------------------------------------------------------------------ #
#  合并去重
# ------------------------------------------------------------------ #

def _merge_recalls(*recall_lists: list[dict]) -> list[dict]:
    """
    合并多路召回结果，按以下优先级排序：
    1. 出现在多路召回中的次数（多路命中优先）
    2. 语义相似度得分（_score）
    """
    merged: dict[str, dict] = {}

    for occ_list in recall_lists:
        for occ in occ_list:
            code = occ["onetsoc_code"]
            if code not in merged:
                merged[code] = occ.copy()
            else:
                # 合并来源
                existing_sources = set(merged[code]["_sources"])
                new_sources = set(occ["_sources"])
                merged[code]["_sources"] = list(existing_sources | new_sources)
                # 取最高语义分
                if occ["_score"] > merged[code]["_score"]:
                    merged[code]["_score"] = occ["_score"]

    # 排序：命中路数多 → 语义分高
    sorted_list = sorted(
        merged.values(),
        key=lambda x: (-len(x["_sources"]), -x["_score"])
    )

    logger.info(f"[合并去重] 三路共 {len(merged)} 个职业，取 Top {MERGE_TOP_N}")
    return sorted_list[:MERGE_TOP_N]


# ------------------------------------------------------------------ #
#  JD 市场验证（Qdrant jobs collection）
# ------------------------------------------------------------------ #

async def _validate_with_jd(occupations: list[dict], qdrant: AsyncQdrantClient,
                             openai: AsyncOpenAI) -> list[dict]:
    """
    对每个候选职业，用职业标题 embed 后检索 jobs collection，
    补充市场热度 + JD 关键词信息。
    """
    titles = [occ["title"] for occ in occupations]
    if not titles:
        return occupations

    # 批量 embed 所有职业标题
    resp = await openai.embeddings.create(model=EMBED_MODEL, input=titles)
    vecs = [item.embedding for item in resp.data]

    # 并发查询每个职业的 JD
    async def _query_jd(occ: dict, vec: list[float]) -> dict:
        try:
            results = await qdrant.query_points(
                collection_name=JOBS_COLLECTION,
                query=vec,
                limit=JD_TOP_K,
                with_payload=True,
                with_vectors=False,
            )
            jd_count = len(results.points)
            # 提取 JD 关键词（title + skills）
            jd_titles = []
            jd_skills = []
            for p in results.points:
                payload = p.payload or {}
                jt = payload.get("job_title") or payload.get("title") or ""
                if jt:
                    jd_titles.append(jt)
                skills = payload.get("skills") or payload.get("requirements") or []
                if isinstance(skills, list):
                    jd_skills.extend(skills[:5])
                elif isinstance(skills, str):
                    jd_skills.append(skills)

            occ["_jd_count"] = jd_count
            occ["_jd_titles"] = list(dict.fromkeys(jd_titles))[:5]
            occ["_jd_skills"] = list(dict.fromkeys(jd_skills))[:10]
        except Exception as e:
            logger.warning(f"[JD验证] {occ['title']} 查询失败：{e}")
            occ["_jd_count"] = 0
            occ["_jd_titles"] = []
            occ["_jd_skills"] = []
        return occ

    tasks = [_query_jd(occ, vec) for occ, vec in zip(occupations, vecs)]
    results = await asyncio.gather(*tasks)
    logger.info(f"[JD验证] 完成 {len(results)} 个职业")
    return list(results)


# ------------------------------------------------------------------ #
#  LLM 综合审核
# ------------------------------------------------------------------ #

async def _llm_review(occupations: list[dict], dims: dict,
                      candidate: dict) -> str:
    """将候选职业 + 评估数据送给 LLM，输出最终 3-5 个推荐职业 JSON。"""
    # 构建简洁的候选人摘要
    dim_summary = {}
    for key in ["skills", "knowledge", "abilities", "work_styles", "work_values"]:
        d = dims.get(key, {})
        dim_summary[key] = d.get("overall_score")

    riasec = {k: v for k, v in (candidate.get("riasec") or {}).items()
              if k in "RIASEC" and isinstance(v, (int, float))}
    holland_code = "".join(
        k for k, _ in sorted(riasec.items(), key=lambda x: -x[1])[:3]
    ) if riasec else "未知"

    candidate_summary = {
        "name": candidate.get("name"),
        "target_role": candidate.get("target_role"),
        "years_of_experience": candidate.get("years_of_experience"),
        "holland_code": holland_code,
        "six_dimensions": dim_summary,
    }

    # 简化职业列表（只保留关键字段）
    occ_list = []
    for i, occ in enumerate(occupations):
        occ_list.append({
            "rank": i + 1,
            "onetsoc_code": occ["onetsoc_code"],
            "title": occ["title"],
            "description": occ.get("description", "")[:200],
            "job_zone": occ.get("job_zone"),
            "riasec_top": sorted(
                [(k, v) for k, v in (occ.get("riasec") or {}).items() if v],
                key=lambda x: -x[1]
            )[:3],
            "recall_sources": occ.get("_sources", []),
            "jd_market_count": occ.get("_jd_count", 0),
            "jd_sample_titles": occ.get("_jd_titles", [])[:3],
            "jd_key_skills": occ.get("_jd_skills", [])[:5],
            "core_tasks": occ.get("core_tasks", [])[:2],
        })

    system_prompt = """\
你是一名资深职业规划顾问。根据候选人的六维能力评估结果和三路召回的候选职业列表，
从中选出最适合的 3-5 个职业，给出结构化推荐结果。

选择原则：
1. 优先选被多路召回命中的职业（recall_sources 包含多个来源）
2. 市场有真实招聘需求（jd_market_count > 0）
3. 职业难度（job_zone）与候选人经验年限匹配
4. Holland Code 与职业 riasec_top 契合
5. 剔除明显不符合候选人背景的职业（如候选人无相关经验但职业要求很高）

输出严格 JSON，不含任何解释文字：
{
  "recommended": [
    {
      "onetsoc_code": "...",
      "title": "...",
      "match_reason": "2-3句，说明为何推荐，结合具体分数和 Holland Code",
      "match_score": 85,
      "key_gaps": ["候选人需要补强的1-3个具体能力或知识点"],
      "jd_market_signal": "市场热度评价（热门/一般/稀缺）",
      "typical_jd_skills": ["JD 中高频出现的技能关键词，最多5个"]
    }
  ],
  "excluded_reason": "简要说明剔除了哪类职业及原因（1句）"
}"""

    user_message = (
        f"候选人画像：\n{json.dumps(candidate_summary, ensure_ascii=False, indent=2)}\n\n"
        f"候选职业列表（共 {len(occ_list)} 个）：\n"
        f"{json.dumps(occ_list, ensure_ascii=False, indent=2)}"
    )

    text, _, _ = await run_prompt(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name="career_match_review",
    )
    return text


# ------------------------------------------------------------------ #
#  工具注册
# ------------------------------------------------------------------ #

@tool(
    description=(
        "根据候选人评估结果推荐最匹配的职业（职业选择第一步）。"
        "内部执行三路并发召回（语义/Holland/六维过滤）→ 合并去重 → JD 市场验证 → LLM 综合审核，"
        "返回 3-5 个推荐职业，每个附推荐理由、匹配度、关键差距和市场信号。"
        "必须在 run_assessment 完成后调用，传入 assessment_id。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "assessment_id": {
                "type": "string",
                "description": "run_assessment 返回的 assessment_id",
            }
        },
        "required": ["assessment_id"],
    },
)
async def match_careers(assessment_id: str) -> str:
    """三路召回 + JD 验证 + LLM 审核，推荐最匹配职业。"""
    logger.info(f"[match_careers] 开始  assessment_id={assessment_id}")

    # 1. 加载评估数据
    dims = await _load_assessment(assessment_id)
    if not dims:
        return json.dumps({"error": f"assessment_id={assessment_id} 无评估数据，请先调用 run_assessment"},
                          ensure_ascii=False)

    candidate = await _load_candidate_basic(assessment_id)

    # 2. 提取 RIASEC（只保留 R/I/A/S/E/C 数值字段）
    riasec_raw = candidate.get("riasec") or {}
    riasec = {k: v for k, v in riasec_raw.items()
              if k in "RIASEC" and isinstance(v, (int, float))}
    if not riasec:
        interests_dim = dims.get("interests", {})
        for sd in interests_dim.get("sub_dimensions", []):
            riasec_items = sd.get("riasec_items", [])
            for item in riasec_items:
                t = item.get("type") or item.get("riasec_type")
                r = item.get("result", {})
                s = r.get("score")
                if t and isinstance(s, (int, float)):
                    riasec[t] = s

    # 3. 初始化客户端
    qdrant = AsyncQdrantClient(url=QDRANT_URL)
    openai = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

    try:
        # 4. 三路并发召回
        profile_text = _build_candidate_profile_text(dims, {**candidate, "riasec": riasec})
        logger.info(f"[match_careers] 候选人画像文本：\n{profile_text}")

        recall1, recall2, recall3 = await asyncio.gather(
            _recall_semantic(profile_text, qdrant, openai),
            _recall_holland(riasec),
            _recall_dim_filter(dims),
        )

        # 5. 合并去重
        merged = _merge_recalls(recall1, recall2, recall3)

        # 6. JD 市场验证
        validated = await _validate_with_jd(merged, qdrant, openai)

        # 7. LLM 综合审核
        logger.info(f"[match_careers] LLM 审核 {len(validated)} 个候选职业...")
        llm_result = await _llm_review(validated, dims, {**candidate, "riasec": riasec})

        # 解析 LLM 输出
        import re
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", llm_result)
        result_text = match.group(1).strip() if match else llm_result.strip()
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            result = {"raw_output": llm_result, "parse_error": True}

        result["assessment_id"] = assessment_id
        result["candidate_name"] = candidate.get("name")
        result["recall_stats"] = {
            "semantic": len(recall1),
            "holland": len(recall2),
            "dim_filter": len(recall3),
            "merged": len(merged),
        }

        logger.info(f"[match_careers] 完成，推荐 {len(result.get('recommended', []))} 个职业")
        return json.dumps(result, ensure_ascii=False, indent=2)

    finally:
        await qdrant.close()
