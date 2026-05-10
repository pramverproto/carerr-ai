"""
职业路线推荐工具集。

match_careers:
    根据候选人评估结果，通过双路 JD 召回 + LLM 深度匹配，
    推荐 3-4 条职业发展路线（每条含 3 个阶段：起点→中期→远期）。

召回策略：
  Path A: 语义向量召回 —— 候选人中文画像 embed → Qdrant jobs（Top 30）
  Path B: 技能关键词召回 —— 候选人技能分组 embed → Qdrant jobs（3-4 组 × Top 15）

合并去重 → 预过滤 → LLM 深度匹配（路线设计 + 评估） → 输出 3-4 条职业发展路线
"""

import asyncio
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

import aiomysql
from dotenv import load_dotenv
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient

from agent.tools.registry import tool
from agent.logger import get_logger
from agent.runner import run_prompt
import agent.memory.db as memory_db

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

logger = get_logger("career")

# ------------------------------------------------------------------ #
#  配置
# ------------------------------------------------------------------ #

QDRANT_URL       = os.getenv("QDRANT_URL", "http://115.120.251.185:6333")
JOBS_COLLECTION  = "jobs"
EMBED_MODEL      = "text-embedding-3-small"
OPENAI_API_KEY   = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL  = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")

VECTOR_TOP_N     = 30   # Path A 向量召回数量
KEYWORD_PER_Q    = 15   # Path B 每组技能查询的召回数量
MAX_SKILL_GROUPS = 4    # Path B 最多技能分组数
PREFILTER_MAX    = 30   # 预过滤后送入 LLM 的最大 JD 数


# ------------------------------------------------------------------ #
#  数据加载
# ------------------------------------------------------------------ #

async def _load_assessment(assessment_id: str) -> dict | None:
    """从 DB 读取评估维度数据，返回结构化 dict。"""
    if memory_db._pool is None:
        return None
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT dimension, overall_score, dimension_summary,
                          sub_dimensions, highlights, focus_areas, status
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
            "dimension_summary": row.get("dimension_summary") or "",
            "sub_dimensions": json.loads(sub) if isinstance(sub, str) else (sub or []),
            "highlights": json.loads(row["highlights"]) if isinstance(row["highlights"], str) else [],
            "focus_areas": json.loads(row["focus_areas"]) if isinstance(row["focus_areas"], str) else [],
            "status": row["status"],
        }
    return dims


async def _load_candidate_basic(assessment_id: str) -> dict:
    """通过 assessment_jobs.user_id 关联 candidates，读取候选人基本信息。"""
    if memory_db._pool is None:
        return {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT user_id FROM assessment_jobs WHERE assessment_id = %s",
                (assessment_id,),
            )
            job_row = await cur.fetchone()
        if not job_row or not job_row["user_id"]:
            return {}
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT name, target_role, years_of_experience, riasec FROM candidates WHERE user_id = %s",
                (job_row["user_id"],),
            )
            row = await cur.fetchone()
    if not row:
        return {}
    riasec = row["riasec"]
    return {
        "name": row["name"] or "",
        "target_role": row["target_role"] or "",
        "years_of_experience": row["years_of_experience"] or 0,
        "riasec": json.loads(riasec) if isinstance(riasec, str) and riasec else {},
    }


async def _load_resume_skills(assessment_id: str) -> list[str]:
    """从 resume_uploads.extracted 加载技能列表，不存在时回退到 candidates.resume_raw。"""
    if memory_db._pool is None:
        return []
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 优先读 resume_uploads.extracted（agent 提取后写入）
            await cur.execute(
                """SELECT ru.extracted FROM resume_uploads ru
                   JOIN assessment_jobs aj ON aj.user_id = ru.user_id
                   WHERE aj.assessment_id = %s AND ru.extracted IS NOT NULL
                   ORDER BY ru.created_at DESC LIMIT 1""",
                (assessment_id,),
            )
            row = await cur.fetchone()
        if row and row[0]:
            extracted = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            skills = extracted.get("skills", [])
            if skills:
                return skills
        # 回退：从 candidates.resume_raw 读
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT c.resume_raw FROM candidates c
                   JOIN assessment_jobs aj ON aj.user_id = c.user_id
                   WHERE aj.assessment_id = %s LIMIT 1""",
                (assessment_id,),
            )
            row2 = await cur.fetchone()
        if not row2 or not row2[0]:
            return []
        resume_raw = json.loads(row2[0]) if isinstance(row2[0], str) else row2[0]
        return resume_raw.get("skills", [])


def _is_valid_highlight(h: str) -> bool:
    """Return False for sub-dimension codes like '1.1', '4.2.3', or single uppercase letters like 'S'."""
    h = h.strip()
    if not h:
        return False
    if re.match(r'^\d+(\.\d+)*$', h):
        return False
    if re.match(r'^[A-Z]$', h):
        return False
    return True


def _skill_tokens(skill: str) -> set[str]:
    """Split a skill string into lowercase tokens (split on spaces and hyphens)."""
    return {t for t in re.split(r'[\s\-_]+', skill.lower()) if len(t) > 1}


def _extract_candidate_skills(resume_skills: list, candidate: dict) -> set[str]:
    """从简历技能 + 目标岗位提取技能关键词集合（小写，含分词 token）。"""
    skills: set[str] = set()
    for s in resume_skills:
        if isinstance(s, str) and s.strip():
            norm = s.strip().lower()
            skills.add(norm)
            skills.update(_skill_tokens(norm))
    target = candidate.get("target_role", "")
    if target:
        for part in target.replace("/", " ").replace("、", " ").split():
            if part.strip():
                skills.add(part.strip().lower())
    return skills


# ------------------------------------------------------------------ #
#  候选人画像构建（中文，用于向量召回）
# ------------------------------------------------------------------ #

def _build_chinese_profile_text(dims: dict, candidate: dict, resume_skills: list) -> str:
    """构建中文候选人画像文本，用于与中文 JD 做语义匹配。"""
    parts = []

    if candidate.get("target_role"):
        parts.append(f"目标岗位：{candidate['target_role']}")

    if resume_skills:
        parts.append(f"核心技能：{', '.join(resume_skills[:15])}")

    yoe = candidate.get("years_of_experience", 0)
    if yoe:
        parts.append(f"工作经验：{yoe} 年")

    # 六维得分概述
    dim_lines = []
    for key, label in [
        ("skills", "技能"), ("knowledge", "知识"), ("abilities", "能力"),
        ("work_styles", "工作特质"), ("work_values", "价值观"),
    ]:
        d = dims.get(key, {})
        score = d.get("overall_score")
        if score is not None:
            dim_lines.append(f"{label}{score:.1f}")
    if dim_lines:
        parts.append(f"能力维度：{', '.join(dim_lines)}")

    # 评估亮点
    all_highlights = []
    for key in ["skills", "knowledge", "abilities"]:
        d = dims.get(key, {})
        for h in (d.get("highlights") or [])[:3]:
            text = h if isinstance(h, str) else h.get("name", str(h)) if isinstance(h, dict) else None
            if text and _is_valid_highlight(text):
                all_highlights.append(text)
    if all_highlights:
        parts.append(f"能力亮点：{', '.join(all_highlights[:6])}")

    # RIASEC
    riasec = candidate.get("riasec") or {}
    riasec_scores = {k: v for k, v in riasec.items()
                     if k in "RIASEC" and isinstance(v, (int, float))}
    if riasec_scores:
        riasec_names = {"R": "现实型", "I": "研究型", "A": "艺术型",
                        "S": "社会型", "E": "企业型", "C": "传统型"}
        top_3 = sorted(riasec_scores.items(), key=lambda x: -x[1])[:3]
        riasec_str = ", ".join(f"{riasec_names.get(k, k)}({v:.1f})" for k, v in top_3)
        parts.append(f"兴趣倾向：{riasec_str}")

    return "\n".join(parts)


# ------------------------------------------------------------------ #
#  JD 解析
# ------------------------------------------------------------------ #

def _parse_jd_point(point, source: str = "vector") -> dict:
    """解析 Qdrant jobs collection 的 point 为标准 dict。"""
    payload = point.payload or {}
    skill_tags_raw = payload.get("skill_tags") or ""
    parsed_skills = []
    if isinstance(skill_tags_raw, str) and skill_tags_raw.strip():
        parsed_skills = [t.strip() for t in skill_tags_raw.split(",") if t.strip()]
    return {
        "jd_id": str(point.id),
        "job_title": payload.get("job_title") or payload.get("title") or "",
        "job_description": (payload.get("job_description") or payload.get("description") or "")[:300],
        "skill_tags": parsed_skills,
        "job_type": payload.get("job_type", ""),
        "salary": payload.get("salary") or payload.get("salary_value") or "",
        "experience": payload.get("experience_value") or "",
        "education": payload.get("education_required") or "",
        "location": payload.get("work_location") or "",
        "company": payload.get("company_name") or "",
        "_score": float(point.score),
        "_source": source,
    }


# ------------------------------------------------------------------ #
#  Path A: 语义向量召回
# ------------------------------------------------------------------ #

async def _recall_vector(vec: list[float], qdrant: AsyncQdrantClient) -> list[dict]:
    """候选人画像 embed → Qdrant jobs → Top N。"""
    try:
        results = await qdrant.query_points(
            collection_name=JOBS_COLLECTION,
            query=vec,
            limit=VECTOR_TOP_N,
            with_payload=True,
            with_vectors=False,
        )
        jds = [_parse_jd_point(p, "vector") for p in results.points]
        logger.info(f"[向量召回] {len(jds)} 条 JD")
        return jds
    except Exception as e:
        logger.warning(f"[向量召回] 失败：{e}")
        return []


# ------------------------------------------------------------------ #
#  Path B: 技能关键词召回（多组技能 embed）
# ------------------------------------------------------------------ #

def _build_skill_queries(resume_skills: list, target_role: str) -> list[str]:
    """将候选人技能拆分为 3-4 组查询字符串。"""
    queries = []

    # 第一组：目标岗位 + top5 技能（最重要）
    top5 = resume_skills[:5] if resume_skills else []
    q1_parts = []
    if target_role:
        q1_parts.append(target_role)
    q1_parts.extend(top5)
    if q1_parts:
        queries.append(" ".join(q1_parts))

    # 后续组：每 4 个技能一组
    remaining = resume_skills[5:] if len(resume_skills) > 5 else resume_skills
    for i in range(0, len(remaining), 4):
        chunk = remaining[i:i + 4]
        if chunk:
            queries.append(" ".join(chunk))
        if len(queries) >= MAX_SKILL_GROUPS:
            break

    return queries[:MAX_SKILL_GROUPS]


async def _recall_keyword(vecs: list[list[float]], qdrant: AsyncQdrantClient) -> list[dict]:
    """多组技能查询 embed → Qdrant jobs → 合并。"""
    if not vecs:
        return []

    try:
        async def _query(vec):
            return await qdrant.query_points(
                collection_name=JOBS_COLLECTION,
                query=vec,
                limit=KEYWORD_PER_Q,
                with_payload=True,
                with_vectors=False,
            )

        results_list = await asyncio.gather(*[_query(v) for v in vecs])

        all_jds = []
        for results in results_list:
            for p in results.points:
                all_jds.append(_parse_jd_point(p, "keyword"))

        logger.info(f"[技能召回] {len(vecs)} 组查询 → {len(all_jds)} 条 JD（含重复）")
        return all_jds
    except Exception as e:
        logger.warning(f"[技能召回] 失败：{e}")
        return []


# ------------------------------------------------------------------ #
#  合并去重 + 预过滤
# ------------------------------------------------------------------ #

def _merge_and_dedup(vector_results: list[dict], keyword_results: list[dict]) -> list[dict]:
    """合并双路结果，按 jd_id 去重，双路命中优先。"""
    seen: dict[str, dict] = {}

    for jd in vector_results:
        seen[jd["jd_id"]] = jd

    for jd in keyword_results:
        jd_id = jd["jd_id"]
        if jd_id in seen:
            seen[jd_id]["_multi_hit"] = True
            seen[jd_id]["_score"] = max(seen[jd_id]["_score"], jd["_score"])
            seen[jd_id]["_source"] = "both"
        else:
            seen[jd_id] = jd

    # 排序：双路命中优先 → 语义分高
    sorted_jds = sorted(
        seen.values(),
        key=lambda x: (-(1 if x.get("_multi_hit") else 0), -x["_score"]),
    )

    logger.info(f"[合并去重] 向量 {len(vector_results)} + 技能 {len(keyword_results)} → 去重后 {len(sorted_jds)}")
    return sorted_jds


def _prefilter(jds: list[dict], candidate_skills: set[str]) -> list[dict]:
    """轻量预过滤：移除无技能交集且语义分低的 JD。"""
    candidate_lower = {s.lower() for s in candidate_skills}
    result = []
    for jd in jds:
        # Expand JD skill_tags with per-tag tokens (e.g. "ai-agent" → {"ai","agent"})
        jd_skills: set[str] = set()
        for s in jd["skill_tags"]:
            sl = s.lower()
            jd_skills.add(sl)
            jd_skills.update(_skill_tokens(sl))
        overlap = candidate_lower & jd_skills
        jd["_skill_overlap"] = len(overlap)
        jd["_skill_overlap_names"] = list(overlap)
        # 保留：有技能交集 或 语义分够高
        if overlap or jd["_score"] >= 0.45:
            result.append(jd)

    logger.info(f"[预过滤] {len(jds)} → {len(result)} 条（有技能交集或语义分 ≥ 0.45）")
    return result[:PREFILTER_MAX]


# ------------------------------------------------------------------ #
#  LLM 深度匹配
# ------------------------------------------------------------------ #

def _build_llm_candidate_context(dims: dict, candidate: dict, resume_skills: list) -> str:
    """为 LLM 构建候选人完整上下文。"""
    parts = []

    parts.append(f"姓名：{candidate.get('name', '候选人')}")
    parts.append(f"目标岗位：{candidate.get('target_role', '未指定')}")
    parts.append(f"工作年限：{candidate.get('years_of_experience', 0)} 年")

    if resume_skills:
        parts.append(f"技能列表：{', '.join(resume_skills[:20])}")

    # 六维得分
    dim_lines = []
    for key, label in [
        ("skills", "技能"), ("knowledge", "知识"), ("abilities", "能力"),
        ("work_styles", "工作特质"), ("work_values", "价值观"),
    ]:
        d = dims.get(key, {})
        score = d.get("overall_score")
        if score is not None:
            dim_lines.append(f"{label}={score:.1f}/7")
    if dim_lines:
        parts.append(f"评估得分：{', '.join(dim_lines)}")

    # 维度概述（如果有）
    for key, label in [("skills", "技能"), ("knowledge", "知识"), ("abilities", "能力")]:
        d = dims.get(key, {})
        summary = d.get("dimension_summary", "")
        if summary:
            parts.append(f"{label}评估概述：{summary[:150]}")

    # 评估亮点
    all_highlights = []
    for key in ["skills", "knowledge", "abilities", "work_styles"]:
        d = dims.get(key, {})
        for h in (d.get("highlights") or [])[:2]:
            text = h if isinstance(h, str) else h.get("name", str(h)) if isinstance(h, dict) else None
            if text and _is_valid_highlight(text):
                all_highlights.append(text)
    if all_highlights:
        parts.append(f"能力亮点：{', '.join(all_highlights[:6])}")

    # 待发展领域
    all_focus = []
    for key in ["skills", "knowledge", "abilities"]:
        d = dims.get(key, {})
        for f in (d.get("focus_areas") or [])[:2]:
            text = f if isinstance(f, str) else f.get("name", str(f)) if isinstance(f, dict) else None
            if text and _is_valid_highlight(text):
                all_focus.append(text)
    if all_focus:
        parts.append(f"待发展领域：{', '.join(all_focus[:4])}")

    # RIASEC
    riasec = candidate.get("riasec") or {}
    riasec_scores = {k: v for k, v in riasec.items()
                     if k in "RIASEC" and isinstance(v, (int, float))}
    if riasec_scores:
        riasec_names = {"R": "现实型", "I": "研究型", "A": "艺术型",
                        "S": "社会型", "E": "企业型", "C": "传统型"}
        top_3 = sorted(riasec_scores.items(), key=lambda x: -x[1])[:3]
        riasec_str = ", ".join(f"{riasec_names.get(k, k)}({v:.1f})" for k, v in top_3)
        parts.append(f"兴趣倾向（Holland）：{riasec_str}")

    return "\n".join(parts)


def _generate_path_code(path_name: str) -> str:
    """为职业路线生成确定性 path-xxxxxxxx 合成码。"""
    return f"path-{hashlib.md5(path_name.encode()).hexdigest()[:8]}"


async def _llm_deep_match(jds: list[dict], dims: dict,
                          candidate: dict, resume_skills: list,
                          custom_start: str = "") -> dict:
    """LLM 一次调用：基于 JD 数据 + 候选人画像，设计 3-4 条职业发展路线。"""
    candidate_context = _build_llm_candidate_context(dims, candidate, resume_skills)

    # 压缩 JD 列表（只保留关键字段）
    jd_entries = []
    for i, jd in enumerate(jds):
        entry = {
            "idx": i + 1,
            "title": jd["job_title"],
            "skills": jd["skill_tags"][:10],
            "desc": jd["job_description"][:200],
            "company": jd.get("company", ""),
            "salary": jd.get("salary", ""),
            "experience": jd.get("experience", ""),
            "overlap": jd.get("_skill_overlap_names", []),
            "source": jd.get("_source", ""),
        }
        jd_entries.append(entry)

    custom_start_instruction = ""
    if custom_start:
        custom_start_instruction = f"""
**用户指定起点**：用户期望从「{custom_start}」方向起步，请围绕此方向设计路线，Stage 1 必须贴近该方向。
"""

    system_prompt = f"""\
你是一名资深中国职业规划顾问。你将收到候选人的完整能力画像和一组真实市场招聘信息（JD）。

**你的任务**：
1. 分析这些 JD，了解市场岗位分布
2. 为候选人设计 3-4 条**职业发展路线**，每条路线包含 3 个阶段（起点→中期→远期）
3. Stage 1（起点）必须基于实际 JD 数据推荐，Stage 2-3 基于行业发展规律推断
{custom_start_instruction}
**路线设计规则**：
- Stage 1（起点岗位）：从 JD 列表中选出候选人当前最适合切入的岗位方向，必须有 JD 数据支撑
- Stage 2（中期目标，2-4年后）：基于 Stage 1 的自然晋升方向，技能在 Stage 1 基础上拓展
- Stage 3（远期目标，5-8年后）：Stage 2 的进一步发展，通常涉及更高层级或管理方向
- 每条路线的 3 个阶段必须形成合理的职业递进关系
- 不同路线之间应有明显差异（如技术深耕 vs 管理转型 vs 跨领域）

**Stage 1 匹配评估依据**（优先级从高到低）：
1. 候选人技能与 JD 技能的直接重叠（overlap 字段是已计算的交集）
2. 候选人目标岗位与该方向的吻合度
3. 候选人工作经验年限与 JD 要求的匹配
4. 评估维度得分与该方向核心要求的契合
5. Holland 兴趣类型与该方向的契合

**评分规则**：
- overall_score 0-100：综合路线可行性（Stage 1 匹配度权重最大）
- Stage 1 的 match_score 0-100：与当前岗位的匹配度

**市场热度**（基于该路线 Stage 1 方向下的 JD 数量）：
- 3+ 条 JD → "热门"
- 1-2 条 → "一般"

**输出格式**（严格 JSON，不加任何 markdown 或解释文字）：
{{
  "recommended": [
    {{
      "path_name": "路线简称（如 AI应用开发→架构师路线）",
      "path_summary": "1-2句话描述这条路线的发展逻辑",
      "overall_score": 82,
      "market_signal": "热门/一般",
      "stages": [
        {{
          "stage": 1,
          "title": "Stage 1 岗位名称（中国市场真实岗位名）",
          "timeframe": "当前起步",
          "salary_range": "基于JD数据的薪资范围（如15-25K）",
          "match_score": 85,
          "key_skills": ["该阶段需要的3-5个核心技能"],
          "match_reason": "2-3句，说明候选人为何适合从这里起步，引用具体技能",
          "key_gaps": ["候选人目前缺少的1-3个技能"],
          "_jd_indices": [1, 3, 7],
          "_jd_count": 3
        }},
        {{
          "stage": 2,
          "title": "Stage 2 岗位名称",
          "timeframe": "2-4年后",
          "salary_range": "推测薪资范围",
          "key_skills": ["该阶段需要新增的3-5个技能"],
          "transition_from_prev": "1-2句，从Stage 1到Stage 2需要哪些关键能力跃迁"
        }},
        {{
          "stage": 3,
          "title": "Stage 3 岗位名称",
          "timeframe": "5-8年后",
          "salary_range": "推测薪资范围",
          "key_skills": ["该阶段核心能力"],
          "transition_from_prev": "1-2句，从Stage 2到Stage 3的关键转变"
        }}
      ]
    }}
  ],
  "excluded_reason": "简要说明排除了哪些路线方向及原因"
}}

**注意**：
- 输出 3-4 条路线，按 overall_score 降序
- 所有文字中文，岗位名称贴近中国市场
- Stage 1 的 match_reason 必须引用具体技能名称
- 每条路线的 path_name 格式为「起点→终点路线」"""

    user_message = (
        f"【候选人画像】\n{candidate_context}\n\n"
        f"【市场 JD 列表（共 {len(jd_entries)} 条）】\n"
        f"{json.dumps(jd_entries, ensure_ascii=False, indent=2)}"
    )

    logger.info(f"[LLM路线匹配] 送入 {len(jd_entries)} 条 JD + 候选人画像"
                + (f"，用户指定起点：{custom_start}" if custom_start else ""))

    text, _, _ = await run_prompt(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name="career_path_match",
    )

    # 解析 LLM 输出
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    result_text = match.group(1).strip() if match else text.strip()
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        logger.warning(f"[LLM路线匹配] JSON 解析失败，原始输出：{text[:500]}")
        result = {"raw_output": text, "parse_error": True}

    # 为每条路线生成确定性 path_code
    for rec in result.get("recommended", []):
        rec["path_code"] = _generate_path_code(rec.get("path_name", "unknown"))

    return result


# ------------------------------------------------------------------ #
#  工具注册
# ------------------------------------------------------------------ #

@tool(
    description=(
        "根据候选人评估结果推荐 3-4 条职业发展路线。"
        "内部执行双路 JD 召回（语义向量 + 技能关键词）→ 合并去重 → LLM 路线设计，"
        "每条路线含 3 个阶段（起点→中期→远期），Stage 1 基于真实 JD 数据。"
        "必须在 run_assessment 完成后调用，传入 assessment_id。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "assessment_id": {
                "type": "string",
                "description": "run_assessment 返回的 assessment_id",
            },
            "custom_start": {
                "type": "string",
                "description": "用户自定义的起始岗位方向（可选，如'产品经理'）",
            },
        },
        "required": ["assessment_id"],
    },
)
async def match_careers(assessment_id: str, custom_start: str = "") -> str:
    """双路 JD 召回 + LLM 路线设计，推荐职业发展路线。"""
    logger.info(f"[match_careers] 开始  assessment_id={assessment_id}"
                + (f"  custom_start={custom_start}" if custom_start else ""))

    # 1. 并发加载数据
    dims, candidate, resume_skills = await asyncio.gather(
        _load_assessment(assessment_id),
        _load_candidate_basic(assessment_id),
        _load_resume_skills(assessment_id),
    )

    if not dims:
        return json.dumps(
            {"error": f"assessment_id={assessment_id} 无评估数据，请先调用 run_assessment"},
            ensure_ascii=False,
        )

    candidate_skills = _extract_candidate_skills(resume_skills, candidate)
    logger.info(f"[match_careers] 候选人技能关键词 ({len(candidate_skills)})：{list(candidate_skills)[:15]}")

    # 2. 提取 RIASEC
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
    candidate["riasec"] = riasec

    # 3. 构建召回输入
    profile_text = _build_chinese_profile_text(dims, candidate, resume_skills)
    skill_queries = _build_skill_queries(resume_skills, candidate.get("target_role", ""))
    logger.info(f"[match_careers] 画像文本：\n{profile_text}")
    logger.info(f"[match_careers] 技能查询组 ({len(skill_queries)})：{skill_queries}")

    # 4. 双路并发召回（embedding 合并成一次 OpenAI 调用，再并发 Qdrant 查询）
    qdrant = AsyncQdrantClient(url=QDRANT_URL)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

    try:
        embed_inputs = [profile_text] + skill_queries
        try:
            embed_resp = await openai_client.embeddings.create(
                model=EMBED_MODEL, input=embed_inputs,
            )
            all_vecs = [d.embedding for d in embed_resp.data]
            profile_vec = all_vecs[0]
            skill_vecs = all_vecs[1:]
        except Exception as e:
            logger.warning(f"[embedding] 批量调用失败：{e}")
            profile_vec, skill_vecs = None, []

        vector_results, keyword_results = await asyncio.gather(
            _recall_vector(profile_vec, qdrant) if profile_vec else asyncio.sleep(0, result=[]),
            _recall_keyword(skill_vecs, qdrant),
        )

        # 5. 合并去重 + 预过滤
        merged = _merge_and_dedup(vector_results, keyword_results)
        filtered = _prefilter(merged, candidate_skills)

        if not filtered:
            return json.dumps(
                {"error": "召回的 JD 经预过滤后为空，可能是向量数据库无匹配内容"},
                ensure_ascii=False,
            )

        # 6. LLM 路线设计
        result = await _llm_deep_match(filtered, dims, candidate, resume_skills, custom_start)

        # 7. 补充元数据
        result["assessment_id"] = assessment_id
        result["candidate_name"] = candidate.get("name")
        result["recall_stats"] = {
            "vector": len(vector_results),
            "keyword": len(keyword_results),
            "merged": len(merged),
            "filtered": len(filtered),
        }

        rec_count = len(result.get("recommended", []))
        logger.info(f"[match_careers] 完成，推荐 {rec_count} 条职业路线")
        return json.dumps(result, ensure_ascii=False, indent=2)

    finally:
        await qdrant.close()
