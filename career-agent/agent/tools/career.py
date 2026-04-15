"""
职业选择工具集。

match_careers:
    根据候选人评估结果，通过双路 JD 召回 + LLM 深度匹配，
    推荐最匹配的 3-5 个职业方向。

召回策略：
  Path A: 语义向量召回 —— 候选人中文画像 embed → Qdrant jobs（Top 30）
  Path B: 技能关键词召回 —— 候选人技能分组 embed → Qdrant jobs（3-4 组 × Top 15）

合并去重 → 预过滤 → LLM 深度匹配（聚类 + 评估） → 输出 3-5 个职业方向
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


async def _load_resume_skills(assessment_id: str) -> list[str]:
    """从 resume_uploads.extracted 加载候选人的技能列表。"""
    if memory_db._pool is None:
        return []
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT ru.extracted FROM resume_uploads ru
                   JOIN assessment_jobs aj ON aj.user_id = ru.user_id
                   WHERE aj.assessment_id = %s AND ru.extracted IS NOT NULL
                   ORDER BY ru.created_at DESC LIMIT 1""",
                (assessment_id,),
            )
            row = await cur.fetchone()
    if not row or not row[0]:
        return []
    extracted = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    return extracted.get("skills", [])


def _extract_candidate_skills(resume_skills: list, candidate: dict) -> set[str]:
    """从简历技能 + 目标岗位提取技能关键词集合（小写）。"""
    skills: set[str] = set()
    for s in resume_skills:
        if isinstance(s, str) and s.strip():
            skills.add(s.strip().lower())
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
            if isinstance(h, str):
                all_highlights.append(h)
            elif isinstance(h, dict):
                all_highlights.append(h.get("name", str(h)))
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

async def _recall_vector(profile_text: str, qdrant: AsyncQdrantClient,
                         openai_client: AsyncOpenAI) -> list[dict]:
    """候选人画像 embed → Qdrant jobs → Top N。"""
    try:
        resp = await openai_client.embeddings.create(model=EMBED_MODEL, input=[profile_text])
        vec = resp.data[0].embedding
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


async def _recall_keyword(skill_queries: list[str], qdrant: AsyncQdrantClient,
                          openai_client: AsyncOpenAI) -> list[dict]:
    """多组技能查询 embed → Qdrant jobs → 合并。"""
    if not skill_queries:
        return []

    try:
        resp = await openai_client.embeddings.create(model=EMBED_MODEL, input=skill_queries)
        vecs = [item.embedding for item in resp.data]

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

        logger.info(f"[技能召回] {len(skill_queries)} 组查询 → {len(all_jds)} 条 JD（含重复）")
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
        jd_skills = {s.lower() for s in jd["skill_tags"]}
        overlap = candidate_lower & jd_skills
        jd["_skill_overlap"] = len(overlap)
        jd["_skill_overlap_names"] = list(overlap)
        # 保留：有技能交集 或 语义分够高
        if overlap or jd["_score"] >= 0.65:
            result.append(jd)

    logger.info(f"[预过滤] {len(jds)} → {len(result)} 条（有技能交集或语义分 ≥ 0.65）")
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
            if isinstance(h, str):
                all_highlights.append(h)
            elif isinstance(h, dict):
                all_highlights.append(h.get("name", str(h)))
    if all_highlights:
        parts.append(f"能力亮点：{', '.join(all_highlights[:6])}")

    # 待发展领域
    all_focus = []
    for key in ["skills", "knowledge", "abilities"]:
        d = dims.get(key, {})
        for f in (d.get("focus_areas") or [])[:2]:
            if isinstance(f, str):
                all_focus.append(f)
            elif isinstance(f, dict):
                all_focus.append(f.get("name", str(f)))
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


def _generate_direction_code(title: str) -> str:
    """为职业方向生成确定性 jd-xxxxxxxx 合成码。"""
    return f"jd-{hashlib.md5(title.encode()).hexdigest()[:8]}"


async def _llm_deep_match(jds: list[dict], dims: dict,
                          candidate: dict, resume_skills: list) -> dict:
    """LLM 一次调用：将 JD 聚类为职业方向 + 深度匹配评估。"""
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

    system_prompt = """\
你是一名资深中国职业规划顾问。你将收到候选人的完整能力画像和一组真实市场招聘信息（JD）。

**你的任务**：
1. 将这些 JD 按「职业方向」聚类为 3-5 个方向（如"AI 应用开发工程师"、"Python 后端工程师"等）
2. 对每个方向，综合评估候选人与该方向的匹配程度
3. 输出结构化推荐

**聚类规则**：
- 同一方向下的 JD 应职责相似、技能要求有大量重叠
- 方向名称必须是中文，贴近中国市场真实岗位名称
- 不要强行合并差异很大的 JD
- 同一方向下至少有 1 条 JD 支撑

**匹配评估依据**（优先级从高到低）：
1. 候选人技能与 JD 技能的直接重叠（overlap 字段是已计算的交集）
2. 候选人目标岗位与该方向的吻合度
3. 候选人工作经验年限与 JD 要求的匹配
4. 候选人评估维度（技能/知识/能力得分和概述）与该方向核心要求的契合
5. 候选人 Holland 兴趣类型与该方向的契合

**评分规则**：
- match_score 0-100：
  90+：技能高度重叠 + 目标岗位完全一致 + 经验充分
  75-89：技能大部分匹配 + 方向契合 + 有小量差距
  60-74：技能部分匹配 + 需要补充 2-3 个关键技能
  <60：不推荐，不要输出

**市场热度**：
- 该方向下 3+ 条 JD → "热门"
- 1-2 条 JD → "一般"
- 仅 1 条 → "稀缺"

**输出格式**（严格 JSON，不加任何解释文字）：
{
  "recommended": [
    {
      "title": "中文职业方向名称",
      "match_score": 85,
      "match_reason": "2-3句中文，具体说明技能重叠、候选人优势、与目标岗位的关系",
      "key_gaps": ["候选人缺少的 1-3 个具体技能"],
      "typical_jd_skills": ["该方向 JD 中最高频的 5 个技能"],
      "jd_market_signal": "热门/一般/稀缺",
      "source": "jd_match",
      "_jd_indices": [1, 3, 7],
      "_jd_count": 3
    }
  ],
  "excluded_reason": "简要说明排除了哪些方向及原因"
}

**注意**：
- 必须输出 3-5 个推荐方向，按 match_score 降序排列
- 所有文字必须为中文
- title 使用中国市场常见的岗位名称
- match_reason 必须引用具体的技能名称，不要泛泛而谈"""

    user_message = (
        f"【候选人画像】\n{candidate_context}\n\n"
        f"【市场 JD 列表（共 {len(jd_entries)} 条）】\n"
        f"{json.dumps(jd_entries, ensure_ascii=False, indent=2)}"
    )

    logger.info(f"[LLM深度匹配] 送入 {len(jd_entries)} 条 JD + 候选人画像")

    text, _, _ = await run_prompt(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name="career_jd_match",
    )

    # 解析 LLM 输出
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    result_text = match.group(1).strip() if match else text.strip()
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        logger.warning(f"[LLM深度匹配] JSON 解析失败，原始输出：{text[:500]}")
        result = {"raw_output": text, "parse_error": True}

    # 为每个推荐生成确定性 onetsoc_code
    for rec in result.get("recommended", []):
        rec["onetsoc_code"] = _generate_direction_code(rec.get("title", "unknown"))

    return result


# ------------------------------------------------------------------ #
#  工具注册
# ------------------------------------------------------------------ #

@tool(
    description=(
        "根据候选人评估结果推荐最匹配的 3-5 个职业方向。"
        "内部执行双路 JD 召回（语义向量 + 技能关键词）→ 合并去重 → LLM 深度匹配，"
        "返回 3-5 个职业方向推荐，每个附推荐理由、匹配度、关键差距和市场信号。"
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
    """双路 JD 召回 + LLM 深度匹配，推荐最匹配职业方向。"""
    logger.info(f"[match_careers] 开始  assessment_id={assessment_id}")

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

    # 4. 双路并发召回
    qdrant = AsyncQdrantClient(url=QDRANT_URL)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

    try:
        vector_results, keyword_results = await asyncio.gather(
            _recall_vector(profile_text, qdrant, openai_client),
            _recall_keyword(skill_queries, qdrant, openai_client),
        )

        # 5. 合并去重 + 预过滤
        merged = _merge_and_dedup(vector_results, keyword_results)
        filtered = _prefilter(merged, candidate_skills)

        if not filtered:
            return json.dumps(
                {"error": "召回的 JD 经预过滤后为空，可能是向量数据库无匹配内容"},
                ensure_ascii=False,
            )

        # 6. LLM 深度匹配
        result = await _llm_deep_match(filtered, dims, candidate, resume_skills)

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
        logger.info(f"[match_careers] 完成，推荐 {rec_count} 个职业方向")
        return json.dumps(result, ensure_ascii=False, indent=2)

    finally:
        await qdrant.close()
