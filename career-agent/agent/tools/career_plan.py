"""
详细职业规划工具集 v2。

新 4-Block 架构：
  Block 1  match_overview      综合匹配评估（规则60% + LLM40%）
  Block 2  jd_recommendations  高匹配岗位推荐（最多3个完整JD + 对照分析）
  Block 3  gap_analysis        差距与优势分析（LLM驱动，引用评估证据）
  Block 4  action_plan         分阶段行动计划（由独立工具 generate_action_plan 生成）

生成流程：
  阶段1（并行）：数据加载（dims/candidate/onet）
  阶段2（并行）：Block1（规则算分+LLM综合） / Block2（JD检索+并发LLM）
  阶段3（顺序）：Block3（gaps+strengths并发LLM，需Block1 narrative作context）
  阶段4：写DB，返回 gap_context 供 generate_action_plan 使用
"""

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import aiomysql
from dotenv import load_dotenv
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient

from agent.tools.registry import tool
from agent.agent_config import MAIN_AGENT_CONFIG, DB_CONFIG
from agent.providers.llm import LLMProvider
from agent.runner import run_prompt
from agent.logger import get_logger
import agent.memory.db as memory_db

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

logger = get_logger("career_plan")

# ------------------------------------------------------------------ #
#  配置
# ------------------------------------------------------------------ #

QDRANT_URL      = os.getenv("QDRANT_URL", "http://115.120.251.185:6333")
JOBS_COLLECTION = "jobs"
EMBED_MODEL     = "text-embedding-3-small"
OPENAI_API_KEY  = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")

JD_TOP_K = 8   # Qdrant 召回数量，取前3展示

# 维度权重（规则算分用）
ZONE_WEIGHT = {
    "skills":      0.30,
    "knowledge":   0.25,
    "abilities":   0.20,
    "work_styles": 0.15,
    "work_values": 0.10,
}

# 候选人评估子维度 → onet_occupations 字段映射
SUB_DIM_MAP = {
    "abilities": {
        "3.1 言语能力":   "ability_verbal",
        "3.2 推理能力":   "ability_reasoning",
        "3.3 定量能力":   "ability_quantitative",
    },
    "skills": {
        "1.1 认知基础技能": "skill_basic",
        "1.2 社交技能":     "skill_social",
        "1.3 技术技能":     "skill_technical",
        "1.4 管理技能":     "skill_management",
    },
    "knowledge": {
        "2.1 商业与管理": "knowledge_business",
        "2.2 技术与工程": "knowledge_tech",
        "2.3 人文与社会": "knowledge_humanities",
        "2.4 应用与服务": "knowledge_applied",
    },
    "work_styles": {
        "4.1 主动进取": "work_style_proactive",
        "4.2 人际导向": "work_style_interpersonal",
        "4.3 尽责守则": "work_style_conscientious",
        "4.4 情绪韧性": "work_style_resilient",
    },
    "work_values": {
        "6.1 成就感":  "work_value_achievement",
        "6.2 独立性":  "work_value_independence",
    },
}

DIM_LABEL = {
    "skills":      "技能画像",
    "knowledge":   "知识储备",
    "abilities":   "认知能力",
    "work_styles": "工作特质",
    "work_values": "工作价值观",
}

GAP_NEAR = 1.0   # 0 < gap ≤ 1.0 → 接近达标；gap > 1.0 → 明显Gap；gap ≤ 0 → 达标


def _gap_status(gap: float) -> str:
    if gap <= 0:
        return "达标"
    elif gap <= GAP_NEAR:
        return "接近达标"
    else:
        return "明显Gap"


# ------------------------------------------------------------------ #
#  数据加载
# ------------------------------------------------------------------ #

async def _load_assessment(assessment_id: str) -> dict | None:
    """从 assessment_dimensions 读取候选人评估数据。"""
    if memory_db._pool is None:
        return None
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT dimension, overall_score, sub_dimensions, highlights, focus_areas
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
        }
    return dims


async def _load_candidate(assessment_id: str) -> dict:
    """从 candidates 读取候选人基本信息（含 resume_raw + supplement）。"""
    if memory_db._pool is None:
        return {}
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT session_id FROM assessment_jobs WHERE assessment_id = %s",
                (assessment_id,),
            )
            job = await cur.fetchone()
        if not job:
            return {}
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT name, target_role, years_of_experience, resume_raw, supplement
                   FROM candidates WHERE id = %s""",
                (job["session_id"],),
            )
            row = await cur.fetchone()
    if not row:
        return {}
    resume_raw = row["resume_raw"]
    return {
        "name": row["name"],
        "target_role": row["target_role"] or "",
        "years_of_experience": row["years_of_experience"] or 0,
        "resume_raw": json.loads(resume_raw) if isinstance(resume_raw, str) and resume_raw else {},
        "supplement": row.get("supplement") or "",
    }


async def _load_onet_occupation(onetsoc_code: str) -> dict | None:
    """从 onet_occupations 读取目标职业的所有数值字段。"""
    if memory_db._pool is None:
        return None
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT onetsoc_code, title, description, job_zone,
                          ability_verbal, ability_reasoning, ability_quantitative,
                          skill_basic, skill_social, skill_technical, skill_management,
                          knowledge_business, knowledge_tech, knowledge_humanities, knowledge_applied,
                          work_style_proactive, work_style_interpersonal,
                          work_style_conscientious, work_style_resilient,
                          work_value_achievement, work_value_independence,
                          dim_skills, dim_knowledge, dim_abilities, dim_work_styles, dim_work_values,
                          tech_tools_json, core_tasks_json
                   FROM onet_occupations WHERE onetsoc_code = %s""",
                (onetsoc_code,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    row = dict(row)
    for key in ("tech_tools_json", "core_tasks_json"):
        v = row.get(key)
        row[key] = json.loads(v) if isinstance(v, str) and v else []
    return row


# ------------------------------------------------------------------ #
#  JD 直接推荐：构建 onet-like 代理数据
# ------------------------------------------------------------------ #

async def _build_jd_proxy(
    title: str,
    onetsoc_code: str,
    qdrant: AsyncQdrantClient,
    openai_client: AsyncOpenAI,
) -> dict:
    """
    为 jd- 开头的合成职业代码构建 onet-like 字典。
    通过 Qdrant 搜索相似 JD 聚合描述和技能。
    """
    jds = await _fetch_top_jds(title, qdrant, openai_client, k=10)

    descriptions = []
    all_skills: list[str] = []
    for jd in jds[:5]:
        desc = jd.get("description", "")
        if desc:
            descriptions.append(desc)
        reqs = jd.get("requirements", "")
        if reqs:
            all_skills.extend([s.strip() for s in reqs.split("、") if s.strip()])

    # 去重保留顺序
    seen: set[str] = set()
    unique_skills: list[str] = []
    for s in all_skills:
        if s not in seen:
            seen.add(s)
            unique_skills.append(s)

    proxy: dict = {
        "onetsoc_code": onetsoc_code,
        "title": title,
        "description": descriptions[0][:400] if descriptions else f"JD 直接匹配职业方向：{title}",
        "core_tasks_json": unique_skills[:12],
        "tech_tools_json": [],
        "job_zone": None,
        "_is_jd_proxy": True,
    }
    # 所有维度子分数设为 None（规则算分将跳过）
    for name_map in SUB_DIM_MAP.values():
        for col in name_map.values():
            proxy[col] = None
    for key in ["skills", "knowledge", "abilities", "work_styles", "work_values"]:
        proxy[f"dim_{key}"] = None

    return proxy


# ------------------------------------------------------------------ #
#  子维度得分提取
# ------------------------------------------------------------------ #

def _extract_sub_scores(dims: dict) -> dict[str, dict[str, dict]]:
    """
    从 sub_dimensions 结构提取子维度得分和证据。
    返回 {dimension: {sub_name: {score, evidence}}}。
    """
    result: dict[str, dict[str, dict]] = {}
    for dim_key, name_map in SUB_DIM_MAP.items():
        dim_data = dims.get(dim_key, {})
        sub_dims = dim_data.get("sub_dimensions", [])
        result[dim_key] = {}
        for sub_name in name_map:
            sub_id_prefix = sub_name.split(" ")[0]
            score = None
            evidence = []
            for sd in sub_dims:
                sd_obj = sd.get("sub_dimension", sd)
                sd_id = sd_obj.get("sub_dimension_id", "") or sd_obj.get("id", "")
                if str(sd_id) == sub_id_prefix:
                    result_obj = sd_obj.get("sub_dimension_result", {})
                    score = result_obj.get("score")
                    if score is None:
                        score = sd_obj.get("score")
                    items = sd_obj.get("items", sd_obj.get("needs", []))
                    for item in items:
                        ev = (item.get("result") or {}).get("evidence", "") or item.get("evidence", "")
                        if ev and ev not in ("无相关证据", "null"):
                            evidence.append(ev)
                    if not evidence:
                        ev_direct = sd_obj.get("evidence", [])
                        if isinstance(ev_direct, list):
                            evidence = [e for e in ev_direct if e not in ("无相关证据", "null")]
                    break
            result[dim_key][sub_name] = {"score": score, "evidence": evidence[:3]}
    return result


# ------------------------------------------------------------------ #
#  通用 LLM 调用辅助
# ------------------------------------------------------------------ #

LLM_SYSTEM = "你是一名职业规划顾问，根据提供的数据生成简洁专业的中文内容，严格遵守字数限制，只输出要求的内容格式。"


async def _llm_fill(prompt: str, system: str, llm: LLMProvider) -> str:
    """单次 LLM 调用，返回纯文本。内部走通用 run_prompt，统一 trace/日志。"""
    text, _, _ = await run_prompt(
        system_prompt=system,
        user_message=prompt,
        llm=llm,
        agent_name="career_plan_fill",
    )
    return text


def _summarize_candidate(candidate: dict, dims: dict) -> str:
    """构建候选人紧凑 profile 供 LLM 上下文（控制 token 用量）。"""
    name = candidate.get("name", "候选人")
    years = candidate.get("years_of_experience", 0)
    target = candidate.get("target_role", "")
    supplement = candidate.get("supplement", "")
    resume = candidate.get("resume_raw") or {}

    # 近期职位
    experiences = resume.get("experiences", [])
    recent_titles = [
        str(e.get("title") or e.get("current_title") or "")
        for e in experiences[:3]
        if e.get("title") or e.get("current_title")
    ]

    # 技能
    skills_raw = resume.get("skills", [])
    skills = [s if isinstance(s, str) else str(s.get("name", "")) for s in skills_raw[:8]]

    # 人格
    bigfive = resume.get("bigfive") or {}
    riasec = resume.get("riasec") or {}

    # 六维得分
    dim_scores = []
    for dim_key in ["skills", "knowledge", "abilities", "work_styles", "work_values"]:
        dim_data = dims.get(dim_key) or {}
        score = dim_data.get("overall_score")
        if score:
            dim_scores.append(f"{DIM_LABEL.get(dim_key, dim_key)} {float(score):.1f}/7")

    # 亮点与关注点
    all_highlights = []
    all_focus = []
    for dim_data in dims.values():
        all_highlights.extend((dim_data.get("highlights") or [])[:2])
        all_focus.extend((dim_data.get("focus_areas") or [])[:1])

    parts = [
        f"姓名：{name}，{years}年工作经验",
        f"近期职位：{' / '.join(t for t in recent_titles if t)}" if recent_titles else "",
        f"目标方向：{target}" if target else "",
        f"核心技能：{', '.join(s for s in skills if s)}" if skills else "",
    ]
    if bigfive:
        parts.append(
            f"大五人格：O={bigfive.get('O')} C={bigfive.get('C')} "
            f"E={bigfive.get('E')} A={bigfive.get('A')} ES={bigfive.get('ES')}"
        )
    if riasec:
        parts.append(
            f"霍兰德兴趣：{riasec.get('holland_code')} "
            f"(I={riasec.get('I')} E={riasec.get('E')} C={riasec.get('C')})"
        )
    if dim_scores:
        parts.append(f"六维得分：{' / '.join(dim_scores)}")
    if all_highlights:
        parts.append(f"评估亮点：{str(all_highlights[:4])}")
    if all_focus:
        parts.append(f"待提升：{str(all_focus[:3])}")
    if supplement:
        parts.append(f"职业动机：{supplement[:300]}")

    return "\n".join(p for p in parts if p)


# ================================================================== #
#  Block 1: match_overview  综合匹配评估
# ================================================================== #

def _compute_rule_score(dims: dict, onet: dict) -> tuple[float, list[dict]]:
    """
    规则算分（60% 权重部分）。
    每个维度：gap<=0 贡献 weight×100；gap>0 按 gap/7 衰减。
    返回 (rule_score 0~100, dim_comparison 列表)。
    """
    dim_comparison = []
    weighted_sum = 0.0
    weight_total = 0.0

    for dim_key in ["skills", "knowledge", "abilities", "work_styles", "work_values"]:
        candidate_score = (dims.get(dim_key) or {}).get("overall_score")
        onet_required = onet.get(f"dim_{dim_key}")
        if candidate_score is None or onet_required is None:
            continue

        candidate_score = round(float(candidate_score), 1)
        onet_required = round(float(onet_required), 1)
        gap = round(onet_required - candidate_score, 1)
        status = _gap_status(gap)
        w = ZONE_WEIGHT.get(dim_key, 0.1)

        weighted_sum += (1 - max(gap, 0) / 7) * w
        weight_total += w

        dim_comparison.append({
            "dimension": dim_key,
            "label": DIM_LABEL.get(dim_key, dim_key),
            "candidate_score": candidate_score,
            "onet_required": onet_required,
            "gap": gap,
            "status": status,
        })

    rule_score = round(weighted_sum / weight_total * 100, 1) if weight_total else 0.0
    return rule_score, dim_comparison


async def _llm_match_analysis(
    candidate_summary: str,
    onet: dict,
    sub_scores: dict,
    rule_score: float,
    llm: LLMProvider,
) -> dict:
    """
    LLM 综合判断（40% 权重部分）。
    输入完整候选人 profile + 评估证据，LLM 独立打分 0-100 并给出叙事。
    返回 {llm_score, narrative, key_factors}。
    """
    # 压缩子维度证据（最多8条）
    evidence_parts = []
    for dim_key, sub_map in sub_scores.items():
        for sub_name, sub_data in sub_map.items():
            score = sub_data.get("score")
            evidence = sub_data.get("evidence", [])
            if evidence and score is not None:
                evidence_parts.append(f"{sub_name}（{float(score):.1f}/7）：{evidence[0]}")
    evidence_text = "\n".join(evidence_parts[:8]) or "暂无细项证据"

    core_tasks = onet.get("core_tasks_json", [])[:5]

    prompt = (
        f"你是资深职业顾问。请根据以下信息对候选人与目标岗位的匹配度做独立综合判断。\n\n"
        f"【候选人信息】\n{candidate_summary}\n\n"
        f"【关键评估证据（子维度）】\n{evidence_text}\n\n"
        f"【目标职业】\n"
        f"标题：{onet['title']}\n"
        f"描述：{(onet.get('description') or '')[:200]}\n"
        f"核心任务：{json.dumps(core_tasks, ensure_ascii=False)}\n\n"
        f"【规则算分（仅供参考，不要被锚定）】{rule_score}/100\n\n"
        f"注意：规则分只反映数值差距，看不到软因素（动机强度、学习速度、行业契合度）。\n"
        f"如果动机明确且学习能力强，即使分数差距大也可给较高分；反之亦然。\n\n"
        f"请输出如下 JSON，只输出 JSON，不加任何解释：\n"
        f'{{\n'
        f'  "llm_score": <0-100的浮点数，你独立给出的综合匹配分>,\n'
        f'  "narrative": "<150-250字的综合判断叙事，说明候选人适合/不适合该岗位的核心原因>",\n'
        f'  "key_factors": [\n'
        f'    {{"factor": "<因素名：职业动机/学习能力/行业经验/管理能力/软技能等>", '
        f'"impact": "<positive|negative|neutral>", "note": "<≤60字>"}},\n'
        f'    ... (3-5个因素)\n'
        f'  ]\n'
        f'}}'
    )

    raw = await _llm_fill(prompt, LLM_SYSTEM, llm)
    try:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            result = json.loads(m.group())
            return {
                "llm_score": float(result.get("llm_score") or rule_score),
                "narrative": str(result.get("narrative") or ""),
                "key_factors": result.get("key_factors") or [],
            }
    except Exception as e:
        logger.warning(f"[_llm_match_analysis] JSON解析失败: {e}")
    return {"llm_score": rule_score, "narrative": raw[:400], "key_factors": []}


async def _build_match_overview(
    dims: dict, onet: dict, candidate: dict, llm: LLMProvider
) -> tuple[dict, str]:
    """
    Block 1: 综合匹配评估。
    返回 (block_dict, llm_narrative)，narrative 供 Block 3 使用。
    """
    sub_scores = _extract_sub_scores(dims)
    candidate_summary = _summarize_candidate(candidate, dims)

    rule_score, dim_comparison = _compute_rule_score(dims, onet)
    llm_result = await _llm_match_analysis(candidate_summary, onet, sub_scores, rule_score, llm)

    # JD 直接推荐没有 O*NET 维度数据，100% 使用 LLM 评分
    if onet.get("_is_jd_proxy") or not dim_comparison:
        final_score = round(llm_result["llm_score"], 1)
    else:
        final_score = round(0.6 * rule_score + 0.4 * llm_result["llm_score"], 1)

    if final_score >= 80:
        verdict = "高度匹配"
    elif final_score >= 65:
        verdict = "中高匹配"
    elif final_score >= 50:
        verdict = "潜力匹配"
    else:
        verdict = "不建议"

    block = {
        "block_id": "match_overview",
        "occupation_title": onet["title"],
        "onetsoc_code": onet["onetsoc_code"],
        "rule_based": {
            "weight": 0.6,
            "score": rule_score,
            "dim_comparison": dim_comparison,
        },
        "llm_analysis": {
            "weight": 0.4,
            "score": llm_result["llm_score"],
            "narrative": llm_result["narrative"],
            "key_factors": llm_result["key_factors"],
        },
        "final_score": final_score,
        "verdict": verdict,
    }
    return block, llm_result["narrative"]


# ================================================================== #
#  Block 2: jd_recommendations  高匹配岗位推荐
# ================================================================== #

async def _fetch_top_jds(
    onet_title: str,
    qdrant: AsyncQdrantClient,
    openai_client: AsyncOpenAI,
    k: int = JD_TOP_K,
) -> list[dict]:
    """从 Qdrant jobs 集合检索最相关的 JD payload 列表。"""
    resp = await openai_client.embeddings.create(model=EMBED_MODEL, input=[onet_title])
    vec = resp.data[0].embedding
    results = await qdrant.query_points(
        collection_name=JOBS_COLLECTION,
        query=vec,
        limit=k,
        with_payload=True,
        with_vectors=False,
    )
    jds = []
    for p in results.points:
        payload = p.payload or {}
        description = (
            payload.get("description")
            or payload.get("job_description")
            or ""
        )
        if not description:
            continue  # 跳过无描述的 JD
        skill_tags = payload.get("skill_tags") or payload.get("skills") or payload.get("requirements") or ""
        if isinstance(skill_tags, list):
            skill_tags = "、".join(str(s) for s in skill_tags[:15])
        jds.append({
            "title": payload.get("title") or payload.get("job_title") or onet_title,
            "description": str(description)[:600],
            "requirements": str(skill_tags)[:300],
            "salary": str(
                payload.get("salary_value")
                or payload.get("salary_range")
                or payload.get("salary_min")
                or ""
            ),
            "experience": str(
                payload.get("experience_value")
                or payload.get("experience")
                or payload.get("work_years")
                or ""
            ),
            "company_type": str(
                payload.get("industry")
                or payload.get("company_type")
                or payload.get("company")
                or ""
            ),
            "score": p.score,
        })
    return jds


async def _llm_gen_jd_recommendation(
    jd_raw: dict,
    candidate_summary: str,
    onet_title: str,
    llm: LLMProvider,
) -> dict | None:
    """
    为单个 JD 生成结构化推荐（Agent调用，1次LLM）：
      - 标准JD格式（full_jd / 职责 / 要求）
      - 岗位解读（role_explanation）
      - 候选人对照分析（match_analysis）
    """
    prompt = (
        f"以下是一条真实市场职位信息，请完成三项任务：\n\n"
        f"【真实职位信息】\n"
        f"职位标题：{jd_raw.get('title', onet_title)}\n"
        f"行业/公司：{jd_raw.get('company_type', '未知')}\n"
        f"职位描述：{jd_raw.get('description', '')[:500]}\n"
        f"技能要求：{jd_raw.get('requirements', '')[:200]}\n"
        f"薪资：{jd_raw.get('salary', '未知') or '未知'}\n"
        f"经验要求：{jd_raw.get('experience', '未知') or '未知'}\n\n"
        f"【候选人概况】\n{candidate_summary}\n\n"
        f"任务1：整理为标准JD格式（中文，可适当补全）\n"
        f"任务2：用200字解释这个岗位日常在做什么、在什么类型的公司、职业发展路径\n"
        f"任务3：结合候选人概况，各列出3条优势和3条顾虑\n\n"
        f"只输出如下 JSON，不加任何解释：\n"
        f'{{\n'
        f'  "title": "<中文职位标题>",\n'
        f'  "company_type": "<行业/公司类型>",\n'
        f'  "salary_range": "<薪资范围，无则填null>",\n'
        f'  "full_jd": "<完整JD正文（Markdown格式，含职责和要求，300-500字）>",\n'
        f'  "key_responsibilities": ["<职责1>", "<职责2>", "<职责3>"],\n'
        f'  "required_qualifications": ["<要求1>", "<要求2>", "<要求3>"],\n'
        f'  "role_explanation": "<200字岗位解读>",\n'
        f'  "match_analysis": {{\n'
        f'    "strengths": ["<优势1>", "<优势2>", "<优势3>"],\n'
        f'    "concerns": ["<顾虑1>", "<顾虑2>", "<顾虑3>"],\n'
        f'    "entry_difficulty": "<easy|moderate|hard>",\n'
        f'    "verdict": "<一句话总结（≤60字）>"\n'
        f'  }}\n'
        f'}}'
    )

    raw = await _llm_fill(prompt, LLM_SYSTEM, llm)
    try:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            result = json.loads(m.group())
            difficulty_map = {"easy": 85, "moderate": 72, "hard": 60}
            diff = (result.get("match_analysis") or {}).get("entry_difficulty", "moderate")
            result["match_score"] = difficulty_map.get(diff, 72)
            return result
    except Exception as e:
        logger.warning(f"[_llm_gen_jd_recommendation] JSON解析失败: {e}, raw={raw[:200]}")
    return None


async def _build_jd_recommendations(
    onet: dict,
    candidate: dict,
    dims: dict,
    qdrant: AsyncQdrantClient,
    openai_client: AsyncOpenAI,
    llm: LLMProvider,
) -> dict:
    """Block 2: 高匹配岗位推荐（Qdrant取JD + 并发LLM生成，最多3个）。"""
    candidate_summary = _summarize_candidate(candidate, dims)

    # 取 Qdrant top-k，过滤无描述，取前3
    jd_list = await _fetch_top_jds(onet["title"], qdrant, openai_client)
    jd_candidates = [j for j in jd_list if j.get("description")][:3]

    # Qdrant 无数据时用 O*NET 兜底
    if not jd_candidates:
        jd_candidates = [{
            "title": onet["title"],
            "company_type": "（行业标准参考）",
            "description": onet.get("description", ""),
            "requirements": "、".join(str(t) for t in (onet.get("core_tasks_json") or [])[:6]),
            "salary": "",
            "experience": "",
        }]

    # 并发 LLM：每个 JD 一次调用
    tasks = [
        _llm_gen_jd_recommendation(jd, candidate_summary, onet["title"], llm)
        for jd in jd_candidates
    ]
    results = await asyncio.gather(*tasks)
    positions = [r for r in results if r is not None]

    # 全部失败时的最终兜底
    if not positions:
        positions = [{
            "title": onet["title"],
            "company_type": "行业标准",
            "salary_range": None,
            "match_score": 70,
            "full_jd": onet.get("description", ""),
            "key_responsibilities": [str(t) for t in (onet.get("core_tasks_json") or [])[:3]],
            "required_qualifications": [],
            "role_explanation": onet.get("description", "")[:200],
            "match_analysis": {
                "strengths": [], "concerns": [],
                "entry_difficulty": "moderate",
                "verdict": "数据不足，建议手动补充",
            },
        }]

    positions.sort(key=lambda x: -x.get("match_score", 0))
    for i, p in enumerate(positions, 1):
        p["rank"] = i

    return {
        "block_id": "jd_recommendations",
        "positions": positions,
    }


# ================================================================== #
#  Block 3: gap_analysis  差距与优势分析
# ================================================================== #

async def _llm_analyze_gaps(
    candidate_summary: str,
    onet: dict,
    sub_scores: dict,
    dims: dict,
    llm: LLMProvider,
) -> list[dict]:
    """
    分析候选人不足的地方（需要弥合的差距）。
    参考量化子维度 gap 数据，但输出以自然语言为主。
    """
    gap_items = []
    for dim_key, name_map in SUB_DIM_MAP.items():
        for sub_name, onet_col in name_map.items():
            sub_data = (sub_scores.get(dim_key) or {}).get(sub_name) or {}
            cand_score = sub_data.get("score")
            onet_score = onet.get(onet_col)
            if cand_score is None or onet_score is None:
                continue
            gap = float(onet_score) - float(cand_score)
            if gap > 0:
                gap_items.append({
                    "sub_name": sub_name,
                    "dim_label": DIM_LABEL.get(dim_key, dim_key),
                    "gap": round(gap, 2),
                    "evidence": (sub_data.get("evidence") or [])[:2],
                })
    gap_items.sort(key=lambda x: -x["gap"])

    core_tasks = json.dumps(onet.get("core_tasks_json", [])[:5], ensure_ascii=False)
    gap_data_text = "\n".join(
        f"- {g['dim_label']}/{g['sub_name']}：Gap={g['gap']:.2f}，"
        f"证据：{g['evidence'] or '无'}"
        for g in gap_items[:8]
    ) or "（无量化差距数据）"

    prompt = (
        f"请分析候选人与目标岗位之间的能力差距，给出3-5条最重要的差距分析。\n\n"
        f"【候选人概况】\n{candidate_summary}\n\n"
        f"【目标岗位】{onet['title']}\n"
        f"核心任务：{core_tasks}\n\n"
        f"【量化差距参考数据】\n{gap_data_text}\n\n"
        f"输出要求：\n"
        f"- 3-5条，按严重程度降序\n"
        f"- required 是岗位对这块的具体工作要求（说'做什么'，不是数字）\n"
        f"- current 引用候选人的真实证据\n"
        f"- how_to_close 具体可执行（≤100字，说清楚怎么做）\n\n"
        f"只输出 JSON 数组，不加任何解释：\n"
        f"[\n"
        f'  {{"area": "<差距方向，如：财务分析能力>", '
        f'"severity": "<high|medium|low>", '
        f'"required": "<岗位要求（自然语言，≤80字）>", '
        f'"current": "<候选人当前状态（引用证据，≤80字）>", '
        f'"how_to_close": "<闭合建议（具体，≤100字）>", '
        f'"related_dimension": "<skills|knowledge|abilities|work_styles|work_values>"}},\n'
        f"  ...\n"
        f"]"
    )

    raw = await _llm_fill(prompt, LLM_SYSTEM, llm)
    try:
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning(f"[_llm_analyze_gaps] JSON解析失败: {e}")
    return []


async def _llm_analyze_strengths(
    candidate_summary: str,
    onet: dict,
    sub_scores: dict,
    dims: dict,
    llm: LLMProvider,
) -> list[dict]:
    """
    分析候选人超过岗位要求的优势（与 _llm_analyze_gaps 并发执行）。
    """
    strength_items = []
    for dim_key, name_map in SUB_DIM_MAP.items():
        for sub_name, onet_col in name_map.items():
            sub_data = (sub_scores.get(dim_key) or {}).get(sub_name) or {}
            cand_score = sub_data.get("score")
            onet_score = onet.get(onet_col)
            if cand_score is None or onet_score is None:
                continue
            gap = float(onet_score) - float(cand_score)
            if gap < -0.5:  # 超过要求 0.5 分以上才算
                strength_items.append({
                    "sub_name": sub_name,
                    "dim_label": DIM_LABEL.get(dim_key, dim_key),
                    "exceed": round(-gap, 2),
                    "evidence": (sub_data.get("evidence") or [])[:2],
                })
    strength_items.sort(key=lambda x: -x["exceed"])

    all_highlights = []
    for dim_data in dims.values():
        all_highlights.extend((dim_data.get("highlights") or [])[:2])

    strength_data_text = "\n".join(
        f"- {s['dim_label']}/{s['sub_name']}：超过要求{s['exceed']:.2f}分，"
        f"证据：{s['evidence'] or '无'}"
        for s in strength_items[:6]
    ) or "（无量化超额数据）"

    prompt = (
        f"请分析候选人超过岗位要求的地方，给出3-5条最具差异化价值的优势分析。\n\n"
        f"【候选人概况】\n{candidate_summary}\n\n"
        f"【目标岗位】{onet['title']}\n\n"
        f"【量化超额参考数据】\n{strength_data_text}\n"
        f"【评估亮点】\n{str(all_highlights[:6])}\n\n"
        f"输出要求：\n"
        f"- 3-5条，聚焦最有差异化价值的优势\n"
        f"- required 是岗位对这块的基线期望（说'做到什么程度'，不是数字）\n"
        f"- current 引用候选人真实证据\n"
        f"- leverage 给出如何在面试/实际工作中放大这个优势（≤80字）\n\n"
        f"只输出 JSON 数组，不加任何解释：\n"
        f"[\n"
        f'  {{"area": "<优势方向，如：数据建模深度>", '
        f'"required": "<岗位基线要求（≤60字）>", '
        f'"current": "<候选人实际水平（引用证据，≤80字）>", '
        f'"leverage": "<如何放大（≤80字）>", '
        f'"related_dimension": "<skills|knowledge|abilities|work_styles|work_values>"}},\n'
        f"  ...\n"
        f"]"
    )

    raw = await _llm_fill(prompt, LLM_SYSTEM, llm)
    try:
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning(f"[_llm_analyze_strengths] JSON解析失败: {e}")
    return []


async def _build_gap_analysis(
    dims: dict,
    onet: dict,
    candidate: dict,
    match_narrative: str,
    llm: LLMProvider,
) -> dict:
    """
    Block 3: 差距与优势分析（2次并发LLM调用）。
    match_narrative 来自 Block 1，作为分析背景 context。
    """
    sub_scores = _extract_sub_scores(dims)
    candidate_summary = _summarize_candidate(candidate, dims)
    # 将 Block 1 的综合判断注入 context，让分析更有整体感
    candidate_ctx = candidate_summary + f"\n【整体匹配判断】{match_narrative[:200]}"

    gaps, strengths = await asyncio.gather(
        _llm_analyze_gaps(candidate_ctx, onet, sub_scores, dims, llm),
        _llm_analyze_strengths(candidate_ctx, onet, sub_scores, dims, llm),
    )

    # 生成总结句
    summary_prompt = (
        f"请用1-2句话（≤150字）总结候选人的差距与优势情况，语气专业有温度。\n"
        f"差距数量：{len(gaps)}项（最重要：{gaps[0]['area'] if gaps else '无'}）；"
        f"优势数量：{len(strengths)}项（最突出：{strengths[0]['area'] if strengths else '无'}）\n"
        f"目标岗位：{onet['title']}"
    )
    summary = await _llm_fill(summary_prompt, LLM_SYSTEM, llm)

    return {
        "block_id": "gap_analysis",
        "gaps": gaps,
        "strengths": strengths,
        "summary": summary,
    }


# ================================================================== #
#  DB 写入
# ================================================================== #

async def _save_blocks(assessment_id: str, onetsoc_code: str, blocks: list[dict]) -> None:
    """将多个 Block 写入 career_plan_blocks（UPSERT）。"""
    if memory_db._pool is None:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            for block in blocks:
                await cur.execute(
                    """INSERT INTO career_plan_blocks
                       (assessment_id, onetsoc_code, block_id, block_json)
                       VALUES (%s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                         block_json=VALUES(block_json), generated_at=NOW()""",
                    (
                        assessment_id,
                        onetsoc_code,
                        block["block_id"],
                        json.dumps(block, ensure_ascii=False),
                    ),
                )


# ================================================================== #
#  工具注册
# ================================================================== #

@tool(
    description=(
        "为已选定目标职业生成详细职业规划报告（Block 1/2/3）。\n"
        "Block1 match_overview: 规则算分60%+LLM综合40%的综合匹配评估；\n"
        "Block2 jd_recommendations: 从市场JD中取3个真实岗位，并发LLM生成完整JD+对照分析；\n"
        "Block3 gap_analysis: 差距（3-5项）与优势（3-5项）的LLM驱动深度分析。\n"
        "结果写入DB，返回3个Block JSON + gap_context（供 generate_action_plan 使用）。\n"
        "必须在 match_careers 完成后，用户已选定目标职业 onetsoc_code 时调用。\n"
        "当 onetsoc_code 以 'jd-' 开头时，表示 JD 直接匹配推荐，必须同时传入 title。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "assessment_id": {
                "type": "string",
                "description": "run_assessment 返回的 assessment_id",
            },
            "onetsoc_code": {
                "type": "string",
                "description": "目标职业代码。O*NET 代码如 '13-2051.00'，JD 直接推荐为 'jd-xxxxxxxx'",
            },
            "title": {
                "type": "string",
                "description": "职业标题。当 onetsoc_code 以 'jd-' 开头时必须提供",
            },
        },
        "required": ["assessment_id", "onetsoc_code"],
    },
)
async def generate_career_plan(assessment_id: str, onetsoc_code: str, title: str = "") -> str:
    """生成职业规划 Block 1/2/3，写入 DB，返回 blocks + gap_context。"""
    logger.info(f"[generate_career_plan] 开始 assessment_id={assessment_id} onetsoc_code={onetsoc_code} title={title}")

    is_jd_direct = onetsoc_code.startswith("jd-")

    # ① 并发加载数据
    dims, candidate = await asyncio.gather(
        _load_assessment(assessment_id),
        _load_candidate(assessment_id),
    )
    if not dims:
        return json.dumps({"error": f"assessment_id={assessment_id} 无评估数据"}, ensure_ascii=False)

    if is_jd_direct:
        if not title:
            return json.dumps({"error": "JD 直接推荐必须提供 title 参数"}, ensure_ascii=False)
        # JD 直接推荐：从 Qdrant JD 数据构建代理 onet 字典
        qdrant = AsyncQdrantClient(url=QDRANT_URL)
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        try:
            onet = await _build_jd_proxy(title, onetsoc_code, qdrant, openai_client)
        except Exception as e:
            logger.warning(f"[generate_career_plan] _build_jd_proxy 失败: {e}")
            onet = {
                "onetsoc_code": onetsoc_code, "title": title,
                "description": f"JD 直接匹配职业方向：{title}",
                "core_tasks_json": [], "tech_tools_json": [],
                "job_zone": None, "_is_jd_proxy": True,
            }
    else:
        onet = await _load_onet_occupation(onetsoc_code)
        if not onet:
            return json.dumps({"error": f"onetsoc_code={onetsoc_code} 不存在"}, ensure_ascii=False)

    # ② 初始化客户端（JD 直接推荐时 qdrant/openai_client 已在上方创建）
    llm = LLMProvider(
        model=MAIN_AGENT_CONFIG["model"],
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    if not is_jd_direct:
        qdrant = AsyncQdrantClient(url=QDRANT_URL)
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

    try:
        # ③ 阶段2（并行）：Block 1 / Block 2 互相独立，同时跑
        (block1, match_narrative), block2 = await asyncio.gather(
            _build_match_overview(dims, onet, candidate, llm),
            _build_jd_recommendations(onet, candidate, dims, qdrant, openai_client, llm),
        )

        # ④ 阶段3（顺序）：Block 3 需要 Block 1 的 narrative 作 context
        block3 = await _build_gap_analysis(dims, onet, candidate, match_narrative, llm)

        # ⑤ 写入 DB
        blocks = [block1, block2, block3]
        await _save_blocks(assessment_id, onetsoc_code, blocks)

        # ⑥ 构建 gap_context（供 generate_action_plan 使用）
        gap_context = {
            "assessment_id": assessment_id,
            "onetsoc_code": onetsoc_code,
            "occupation_title": onet["title"],
            "occupation_description": (onet.get("description") or "")[:200],
            "onet_core_tasks": onet.get("core_tasks_json", [])[:5],
            "candidate_name": candidate.get("name", ""),
            "candidate_target_role": candidate.get("target_role", ""),
            "candidate_years_of_experience": candidate.get("years_of_experience", 0),
            # Block 1 结果
            "match_verdict": block1["verdict"],
            "match_score": block1["final_score"],
            "match_narrative": match_narrative[:300],
            # Block 3 结果（供 action_plan 使用）
            "priority_gaps": [
                {
                    "area": g.get("area", ""),
                    "severity": g.get("severity", "medium"),
                    "required": g.get("required", ""),
                    "current": g.get("current", ""),
                    "how_to_close": g.get("how_to_close", ""),
                    "related_dimension": g.get("related_dimension", ""),
                }
                for g in (block3.get("gaps") or [])
            ],
            "key_strengths": [
                {
                    "area": s.get("area", ""),
                    "leverage": s.get("leverage", ""),
                }
                for s in (block3.get("strengths") or [])[:3]
            ],
        }

        logger.info(f"[generate_career_plan] 完成，3 个 Block 已写入 DB")
        return json.dumps({
            "status": "done",
            "blocks": {b["block_id"]: b for b in blocks},
            "gap_context": gap_context,
        }, ensure_ascii=False, indent=2)

    finally:
        await qdrant.close()
