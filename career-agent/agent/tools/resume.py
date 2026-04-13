"""
简历提取相关工具。

注册两个工具给 resume_extract_agent 使用：
  1. save_resume_data     — 将提取后的结构化简历数据写入 resume_uploads 表
  2. rewrite_resume_text  — 对 OCR 识别有噪声的文本片段做轻量清洗/改写

该 agent 的工作流程：
  前置步骤（由 /resume/extract 端点完成）：多模态模型对简历图片做 OCR → 得到原始文本
  ↓
  Agent 接收 OCR 文本 + upload_id，按 Profile 表单 schema 提取字段
  ↓
  可选：对有噪声的字段调用 rewrite_resume_text 做清洗
  ↓
  最终调用 save_resume_data 将结构化 JSON 存库，并返回该 JSON 供前端填充表单
"""

import json

from agent.tools.registry import tool
from agent.runner import run_prompt
from agent.logger import get_logger
import agent.memory.db as memory_db

logger = get_logger("tools.resume")


@tool(
    description=(
        "将提取后的结构化简历 JSON 存入数据库 resume_uploads 表。"
        "必须在完成所有字段提取后调用一次。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "upload_id": {
                "type": "string",
                "description": "本次上传的唯一 ID，由调用方在任务中明确给出，直接原样使用。",
            },
            "extracted_json": {
                "type": "string",
                "description": (
                    "提取出的简历结构化数据，必须是合法 JSON 字符串，"
                    "字段结构需匹配 Profile 表单：name/age/education/current_title/"
                    "years_of_experience/experiences[]/skills[]/certifications[]/supplement。"
                ),
            },
        },
        "required": ["upload_id", "extracted_json"],
    },
)
async def save_resume_data(upload_id: str, extracted_json: str) -> str:
    if memory_db._pool is None:
        return "数据库未初始化，无法保存"

    # 验证 JSON 格式
    try:
        parsed = json.loads(extracted_json)
    except json.JSONDecodeError as e:
        return f"extracted_json 不是合法 JSON：{e}"

    # 更新 extracted 字段（记录应已由端点预先插入）
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            affected = await cur.execute(
                "UPDATE resume_uploads SET extracted = %s WHERE upload_id = %s",
                (json.dumps(parsed, ensure_ascii=False), upload_id),
            )
            if affected == 0:
                # 记录不存在时插入一条兜底
                await cur.execute(
                    "INSERT INTO resume_uploads (upload_id, extracted) VALUES (%s, %s)",
                    (upload_id, json.dumps(parsed, ensure_ascii=False)),
                )

    logger.info(f"[resume] save_resume_data  upload_id={upload_id}  fields={list(parsed.keys())}")
    return f"已保存简历数据到数据库，upload_id={upload_id}"


@tool(
    description=(
        "使用 LLM 对 OCR 识别出的带噪声简历文本片段做清洗/改写。"
        "仅在某个字段明显有识别错误或格式混乱时才调用，不要滥用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要清洗的原始文本片段，如姓名、公司名、岗位描述等。",
            },
            "purpose": {
                "type": "string",
                "description": "该文本的语义用途，例如：'person_name'、'company_name'、'job_responsibility'。",
            },
        },
        "required": ["text", "purpose"],
    },
)
async def rewrite_resume_text(text: str, purpose: str) -> str:
    if not text or not text.strip():
        return ""

    system = (
        "你是简历文本清洗助手。输入是一段 OCR 可能存在识别噪声的简历片段，"
        "请根据 purpose 给出用途语义，输出清洗后的干净文本。"
        "要求：\n"
        "1. 只修正明显的 OCR 错别字和标点错乱\n"
        "2. 不得添加原文没有的信息\n"
        "3. 直接输出清洗后的文本，不要加解释、引号或 markdown 标记"
    )
    user = f"purpose: {purpose}\n原始文本:\n{text}"
    cleaned, _, _ = await run_prompt(
        system_prompt=system,
        user_message=user,
        agent_name="resume_rewrite",
    )
    logger.debug(f"[resume] rewrite_resume_text  purpose={purpose}  len={len(text)}→{len(cleaned)}")
    return cleaned or text
