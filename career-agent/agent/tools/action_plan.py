"""
行动计划生成工具。

generate_action_plan:
    接收 gap_context（来自 generate_career_plan 的返回值），
    启动 Action Plan Sub-Agent 动态规划三阶段行动计划（Block 4）。

    Sub-Agent 有完整自由度编写 action/deliverable/resource，
    但输出格式由代码强制校验，保证可解析入库。

    预留工具槽位：后期可为 Sub-Agent 挂载知识库检索和网络检索工具。
"""

import json
import re
from pathlib import Path

from dotenv import load_dotenv

from agent.agent import Agent
from agent.tools.registry import tool
from agent.agent_config import MAIN_AGENT_CONFIG
from agent.providers.llm import LLMProvider
from agent.logger import get_logger
import agent.memory.db as memory_db
import aiomysql

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

logger = get_logger("action_plan")

# ------------------------------------------------------------------ #
#  Action Plan Sub-Agent 系统提示
# ------------------------------------------------------------------ #

ACTION_PLAN_SYSTEM_PROMPT = """\
你是一名资深职业发展教练，专注为职场人士制定切实可行的能力提升计划。

你将收到候选人的完整背景信息和差距分析数据，需要制定一份三阶段行动计划。

【阶段划分依据】
- phase_1（0-3个月）：优先解决 severity=high 的差距，建立基础能力
- phase_2（3-6个月）：解决 severity=medium 差距 + 开始实战积累（主动创造项目机会）
- phase_3（6-12个月）：面试准备 + 求职市场激活（固定任务）

【输出规则】
1. 必须输出 3 个 phase，不允许增减
2. 每个 phase 必须包含 2-3 个 action
3. 每个 action 必须包含 item/severity/action/deliverable/resource 全部字段
4. item 来自输入的 priority_gaps 的 area 字段；phase_3 的两个 item 固定
5. resource 必须给出具体的课程名/书名/工具名/平台名，不能泛泛而谈
6. action ≤150字（具体描述怎么做，结合候选人工作年限和目标岗位核心任务）
7. deliverable ≤100字（可量化的产出，有时间节点）
8. focus ≤40字，描述该阶段的核心主题
9. 只输出 JSON，不输出任何解释文字，不使用 markdown 代码块

【高质量 action 写法示例】
差：action: "提升商业知识"
好：action: "用6周系统完成 Coursera《Wharton Business Foundations》专项课（财务+运营+战略），每周5-6小时；同步每周解读1份行业上市公司财报，用'北极星指标+P&L拆解'框架做笔记，共积累6份分析文档"

【JSON Schema（严格遵守，不得增删字段）】
{
  "block_id": "action_plan",
  "phases": [
    {
      "phase_id": "phase_1",
      "label": "0-3个月：补核心短板",
      "focus": "...",
      "actions": [
        {
          "item": "<来自priority_gaps的area>",
          "severity": "high",
          "action": "...",
          "deliverable": "...",
          "resource": "..."
        }
      ]
    },
    {
      "phase_id": "phase_2",
      "label": "3-6个月：实战积累",
      "focus": "...",
      "actions": [...]
    },
    {
      "phase_id": "phase_3",
      "label": "6-12个月：求职激活",
      "focus": "面试准备与求职市场激活",
      "actions": [
        {
          "item": "面试话术准备",
          "severity": null,
          "action": "针对目标岗位高频面试题，用STAR结构整理5-8个核心案例故事，覆盖：技术深度展示、跨部门协作、复杂决策、失败复盘等场景，每个故事必须含量化成果数据",
          "deliverable": "完成5-8个STAR结构面试故事文档，每个故事含量化结果",
          "resource": "目标公司 glassdoor 面试反馈；LinkedIn 同岗位从业者公开经历；《面试圣经》（汤佑诚著）"
        },
        {
          "item": "求职市场激活",
          "severity": null,
          "action": "...",
          "deliverable": "...",
          "resource": "..."
        }
      ]
    }
  ]
}
"""

# ------------------------------------------------------------------ #
#  格式校验
# ------------------------------------------------------------------ #

REQUIRED_PHASE_KEYS = {"phase_id", "label", "focus", "actions"}
REQUIRED_ACTION_KEYS = {"item", "severity", "action", "deliverable", "resource"}
FIXED_PHASE_IDS = ["phase_1", "phase_2", "phase_3"]
FIXED_PHASE_LABELS = {
    "phase_1": "0-3个月：补核心短板",
    "phase_2": "3-6个月：实战积累",
    "phase_3": "6-12个月：求职激活",
}


def _validate_and_fix(block: dict, current_keyword_coverage: int = 0) -> tuple[bool, str, dict]:
    """
    校验 action_plan block 格式，返回 (is_valid, error_message, fixed_block)。
    修复可自动处理的问题（phase_3 deliverable 覆盖写入、固定字段还原）。
    """
    if block.get("block_id") != "action_plan":
        block["block_id"] = "action_plan"  # 修复

    phases = block.get("phases", [])
    if len(phases) != 3:
        return False, f"phases 长度应为 3，实际为 {len(phases)}", block

    for i, phase in enumerate(phases):
        # 校验必填字段
        missing_keys = REQUIRED_PHASE_KEYS - set(phase.keys())
        if missing_keys:
            return False, f"phase_{i+1} 缺少字段：{missing_keys}", block

        # 修复固定值字段
        phase["phase_id"] = FIXED_PHASE_IDS[i]
        phase["label"] = FIXED_PHASE_LABELS[FIXED_PHASE_IDS[i]]

        actions = phase.get("actions", [])
        if not actions:
            return False, f"phase_{i+1} 的 actions 为空", block

        for j, action in enumerate(actions):
            missing_action_keys = REQUIRED_ACTION_KEYS - set(action.keys())
            if missing_action_keys:
                return False, f"phase_{i+1}.actions[{j}] 缺少字段：{missing_action_keys}", block
            # 校验非 null 字段（gap_value 允许 null）
            for k in ("item", "action", "deliverable", "resource"):
                if not action.get(k):
                    return False, f"phase_{i+1}.actions[{j}].{k} 不能为空", block

    # 修复 phase_3：固定第一个 action 的 item 和 focus
    phase3 = phases[2]
    if phase3["actions"]:
        phase3["actions"][0]["item"] = "面试话术准备"
        phase3["focus"] = "面试准备与求职市场激活"

    return True, "", block


# ------------------------------------------------------------------ #
#  Sub-Agent 调用
# ------------------------------------------------------------------ #

async def _run_action_plan_subagent(task: str) -> str:
    """
    启动 Action Plan Sub-Agent，执行单次任务，返回字符串结果。

    Sub-Agent 当前无额外工具（预留工具槽位：后期挂载知识库检索、网络检索）。
    allowed_tools=[] 表示纯 LLM 推理模式。
    """
    llm = LLMProvider(
        model=MAIN_AGENT_CONFIG["model"],
        api_key=MAIN_AGENT_CONFIG["api_key"],
        base_url=MAIN_AGENT_CONFIG["base_url"],
    )
    # allowed_tools=[] 当前无工具，后期扩展时在此添加工具名称
    # 例如：allowed_tools=["search_knowledge_base", "web_search"]
    sub_agent = Agent(
        llm=llm,
        system_prompt=ACTION_PLAN_SYSTEM_PROMPT,
        allowed_tools=[],   # 预留槽位，后期添加工具
        mcp=None,
        session_id=None,
    )
    return await sub_agent.run_once(task)


# ------------------------------------------------------------------ #
#  DB 写入
# ------------------------------------------------------------------ #

async def _save_block(assessment_id: str, onetsoc_code: str, block: dict) -> None:
    if memory_db._pool is None:
        return
    async with memory_db._pool.acquire() as conn:
        async with conn.cursor() as cur:
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


# ------------------------------------------------------------------ #
#  工具注册
# ------------------------------------------------------------------ #

@tool(
    description=(
        "为候选人生成分阶段行动计划（Block 4）。"
        "内部启动 Action Plan Sub-Agent 动态规划三阶段计划，"
        "代码层强制校验输出格式后写入 DB。"
        "必须在 generate_career_plan 完成后，传入其返回的 gap_context_json。"
        "后期可为 Sub-Agent 挂载知识库检索和网络检索工具。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "gap_context_json": {
                "type": "string",
                "description": "generate_career_plan 返回值中的 gap_context 字段，JSON 字符串",
            },
        },
        "required": ["gap_context_json"],
    },
)
async def generate_action_plan(gap_context_json: str) -> str:
    """启动 Sub-Agent 生成行动计划 Block 4，校验格式后写入 DB。"""
    # 解析 gap_context
    try:
        gap_context = json.loads(gap_context_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"gap_context_json 解析失败：{e}"}, ensure_ascii=False)

    assessment_id = gap_context.get("assessment_id", "")
    onetsoc_code = gap_context.get("onetsoc_code", "")

    logger.info(f"[generate_action_plan] 开始 assessment_id={assessment_id} onetsoc_code={onetsoc_code}")

    # 构建 Sub-Agent 任务提示
    candidate_name = gap_context.get("candidate_name") or "候选人"
    target_role = gap_context.get("candidate_target_role") or ""
    core_tasks = gap_context.get("onet_core_tasks", [])
    occ_desc = gap_context.get("occupation_description", "")
    priority_gaps = gap_context.get("priority_gaps", [])
    key_strengths = gap_context.get("key_strengths", [])
    match_verdict = gap_context.get("match_verdict", "")
    match_narrative = gap_context.get("match_narrative", "")

    task = (
        f"请为以下候选人制定三阶段行动计划，严格按照系统提示的 JSON Schema 输出，每个 phase 必须有 2-3 个 action。\n\n"
        f"【候选人信息】\n"
        f"姓名：{candidate_name}\n"
        f"目前方向/目标岗位：{target_role or '未指定'}\n"
        f"工作年限：{gap_context.get('candidate_years_of_experience')} 年\n\n"
        f"【目标职业】\n"
        f"职业名称：{gap_context.get('occupation_title')}\n"
        f"职业描述：{occ_desc}\n"
        f"核心工作任务：{json.dumps(core_tasks, ensure_ascii=False)}\n\n"
        f"【综合匹配评估】\n"
        f"匹配判断：{match_verdict}\n"
        f"综合分析：{match_narrative}\n\n"
        f"【差距分析（按严重程度排列）】\n"
        f"{json.dumps(priority_gaps, ensure_ascii=False, indent=2)}\n\n"
        f"【候选人核心优势（行动计划中可适当发挥）】\n"
        f"{json.dumps(key_strengths, ensure_ascii=False, indent=2)}\n\n"
        f"要求：\n"
        f"- phase_1 优先处理 severity=high 的差距（2-3项）\n"
        f"- phase_2 处理 severity=medium 差距 + 1条实战积累任务\n"
        f"- phase_3 固定为：第1个action=「面试话术准备」，第2个=「求职市场激活」\n"
        f"- 每个 action 要结合候选人工作年限和目标职业核心任务，说清楚具体怎么做\n"
        f"- resource 必须给出具体课程名/书名/平台名"
    )

    # 调用 Sub-Agent（最多重试2次，处理格式错误）
    max_retries = 2
    last_error = ""
    for attempt in range(max_retries + 1):
        if attempt > 0:
            # 重试时附加上一次的错误信息
            retry_task = (
                task + f"\n\n【重要】上一次输出有格式错误：{last_error}，请修正后重新输出，只输出 JSON。"
            )
        else:
            retry_task = task

        raw = await _run_action_plan_subagent(retry_task)
        logger.info(f"[generate_action_plan] Sub-Agent 输出（attempt {attempt}）：{raw[:200]}...")

        # 提取 JSON
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            last_error = "输出中未找到 JSON 对象"
            continue

        try:
            block = json.loads(m.group())
        except json.JSONDecodeError as e:
            last_error = f"JSON 解析失败：{e}"
            continue

        is_valid, err_msg, fixed_block = _validate_and_fix(block)
        if not is_valid:
            last_error = err_msg
            continue

        # 校验通过，写入 DB
        await _save_block(assessment_id, onetsoc_code, fixed_block)
        logger.info(f"[generate_action_plan] 完成，Block 4 已写入 DB")
        return json.dumps({
            "status": "done",
            "block": fixed_block,
        }, ensure_ascii=False, indent=2)

    # 所有重试失败
    logger.error(f"[generate_action_plan] 所有重试失败，最后错误：{last_error}")
    return json.dumps({
        "error": f"generate_action_plan 格式校验失败（{max_retries+1} 次尝试）：{last_error}",
    }, ensure_ascii=False)
