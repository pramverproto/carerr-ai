"""
报告导出脚本：从数据库查询指定 assessment_id 的完整报告，整理输出为 txt 文件。
用法：
    python test/export_report.py <assessment_id>
    python test/export_report.py          # 自动取最新一条
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import aiomysql
import asyncio

# ------------------------------------------------------------------ #
#  DB 配置（与 .env 一致）
# ------------------------------------------------------------------ #
DB_HOST = "115.120.251.185"
DB_PORT = 3306
DB_USER = "user01"
DB_PASS = "187423"
DB_NAME = "career_agent"

OUTPUT_DIR = Path(__file__).parent.parent / "output-temp"
OUTPUT_DIR.mkdir(exist_ok=True)


async def fetch_report(assessment_id: str | None) -> None:
    conn = await aiomysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS, db=DB_NAME,
        charset="utf8mb4", autocommit=True,
    )
    cur = await conn.cursor(aiomysql.DictCursor)

    # 如果没有传 assessment_id，取最新一条 done 的
    if not assessment_id:
        await cur.execute(
            "SELECT assessment_id FROM assessment_jobs WHERE status IN ('done','partial') "
            "ORDER BY created_at DESC LIMIT 1"
        )
        row = await cur.fetchone()
        if not row:
            print("❌ 数据库中没有已完成的评估记录")
            await cur.close()
            conn.close()
            return
        assessment_id = row["assessment_id"]
        print(f"[自动选取] assessment_id = {assessment_id}")

    # 拉取 job 基本信息
    await cur.execute(
        "SELECT assessment_id, session_id, status, created_at FROM assessment_jobs WHERE assessment_id=%s",
        (assessment_id,)
    )
    job = await cur.fetchone()
    if not job:
        print(f"❌ assessment_id={assessment_id} 不存在")
        await cur.close()
        conn.close()
        return

    # 拉取 summary
    await cur.execute(
        "SELECT * FROM assessment_summary WHERE assessment_id=%s", (assessment_id,)
    )
    summary = await cur.fetchone()

    # 拉取 dimensions
    await cur.execute(
        "SELECT dimension, overall_score, confidence, dimension_summary, sub_dimensions, "
        "highlights, focus_areas, status FROM assessment_dimensions WHERE assessment_id=%s",
        (assessment_id,)
    )
    dimensions = await cur.fetchall()

    # 拉取 report blocks
    await cur.execute(
        "SELECT block_id, block_json, generated_at FROM assessment_report_blocks "
        "WHERE assessment_id=%s ORDER BY block_id",
        (assessment_id,)
    )
    blocks = await cur.fetchall()

    await cur.close()
    conn.close()

    # ------------------------------------------------------------------ #
    #  组装输出文本
    # ------------------------------------------------------------------ #
    lines = []

    def h1(t): lines.append(f"\n{'='*60}\n{t}\n{'='*60}")
    def h2(t): lines.append(f"\n{'─'*40}\n{t}\n{'─'*40}")
    def h3(t): lines.append(f"\n【{t}】")
    def p(t):  lines.append(str(t))
    def img(src): lines.append(f"[图片占位] {src}")

    h1(f"能力评估报告")
    p(f"assessment_id : {assessment_id}")
    p(f"状态          : {job['status']}")
    p(f"生成时间      : {job['created_at']}")

    # ── Summary ──
    if summary:
        h1("总览 Overview")
        p(f"画像标签    : {summary.get('persona_label', '—')}")
        p(f"成长方向    : {summary.get('next_direction', '—')}")
        p(f"\n个性化叙事：\n{summary.get('narrative_intro', '—')}")

        kw = summary.get('keywords')
        if kw:
            kw_list = json.loads(kw) if isinstance(kw, str) else kw
            p(f"\n能力关键词  : {' · '.join(kw_list)}")

        tc = summary.get('top_cards')
        if tc:
            tc_list = json.loads(tc) if isinstance(tc, str) else tc
            p(f"三张王牌    : {' / '.join(tc_list)}")

    # ── Score Table ──
    h1("六维得分总览")
    block_map = {}
    for b in blocks:
        block_map[b["block_id"]] = json.loads(b["block_json"]) if isinstance(b["block_json"], str) else b["block_json"]

    score_table = block_map.get("score_table", {})
    if score_table:
        rows = score_table.get("rows", [])
        p(f"{'维度':<12} {'得分':>6} {'置信度':>6} {'状态':>8}")
        p("─" * 38)
        for row in rows:
            score = row.get('overall_score')
            score_str = f"{score:.2f}" if score is not None else "  —  "
            conf = row.get('confidence') or '—'
            p(f"{row.get('name_zh',''):<12} {score_str:>6} {conf:>6} {row.get('status',''):>8}")
        img("[雷达图] 六维能力雷达图 → radar_chart.png")

    # ── 核心优势 TOP3 ──
    if summary:
        strengths = summary.get('top3_strengths')
        if strengths:
            st_list = json.loads(strengths) if isinstance(strengths, str) else strengths
            h1("核心优势 TOP 3")
            for i, s in enumerate(st_list, 1):
                h3(f"优势 {i}：{s.get('title', '')}")
                p(f"职场意义：{s.get('career_meaning', '')}")
                p(f"放大建议：{s.get('how_to_amplify', '')}")

        improvements = summary.get('top3_improvements')
        if improvements:
            imp_list = json.loads(improvements) if isinstance(improvements, str) else improvements
            h1("提升方向 TOP 3")
            for i, imp in enumerate(imp_list, 1):
                h3(f"方向 {i}：{imp.get('title', '')}")
                p(f"当前状态：{imp.get('current_state', '')}")
                p(f"目标状态：{imp.get('target_state', '')}")
                ap = imp.get('action_plan', {})
                if ap:
                    p(f"第1个月  ：{ap.get('month_1', '')}")
                    p(f"第2-3个月：{ap.get('month_2_3', '')}")
                    p(f"第4-6个月：{ap.get('month_4_6', '')}")
                p(f"预期效果：{imp.get('expected_outcome', '')}")

    # ── 六维画像块 ──
    dim_block_order = [
        ("dimension_skills",      "技能画像 Skills"),
        ("dimension_knowledge",   "知识储备 Knowledge"),
        ("dimension_abilities",   "认知能力 Abilities"),
        ("dimension_work_styles", "工作特质 Work Styles"),
        ("dimension_interests",   "职业兴趣 Interests"),
        ("dimension_work_values", "工作价值观 Work Values"),
    ]

    for block_id, label in dim_block_order:
        block = block_map.get(block_id)
        if not block:
            # fallback：从 assessment_dimensions 原始数据
            dim_raw = next((d for d in dimensions if d["dimension"] == block_id.replace("dimension_", "")), None)
            if dim_raw:
                h1(f"维度画像：{label}")
                p(f"综合得分：{dim_raw.get('overall_score', '—')}  置信度：{dim_raw.get('confidence', '—')}")
                p(f"维度概述：{dim_raw.get('dimension_summary', '—')}")
            continue

        h1(f"维度画像：{label}")
        score = block.get('overall_score')
        p(f"综合得分：{f'{score:.2f}' if score else '—'}  置信度：{block.get('confidence', '—')}")
        img(f"[维度雷达/柱状图] {block_id}_chart.png")
        p(f"\n{block.get('dimension_summary_prose', block.get('unlock_intro', ''))}")

        # holland code（interests 特有）
        if block.get("holland_code"):
            p(f"\nHolland Code：{block['holland_code']}")
            roles = block.get("suitable_roles", [])
            if roles:
                p(f"适合岗位：{' / '.join(roles)}")

        # bigfive（work_styles 特有）
        if block.get("bigfive_display"):
            p("\n大五人格原始分：")
            for k, v in block["bigfive_display"].items():
                p(f"  {k}：{v}")

        # persona_tag（work_values 特有）
        if block.get("persona_tag"):
            p(f"\n价值观标签：{block['persona_tag']}")

        # 子维度
        sub_dims = block.get("sub_dimensions", [])
        for sd in sub_dims:
            dim_id = sd.get("id") or sd.get("type", "")
            dim_name = sd.get("name", "")
            score = sd.get("score")
            tag = sd.get("tag", "normal")
            star = "★" * (sd.get("star_rating") or 0) + "☆" * (5 - (sd.get("star_rating") or 0))
            tag_label = {"highlight": "⬆优势", "focus": "⬇待提升", "normal": "→正常", "no_evidence": "－无数据"}.get(tag, "")

            h3(f"{dim_id} {dim_name}  {f'{score:.1f}/7' if score else '—'}  {star}  {tag_label}")

            evidence = sd.get("evidence_bullets", sd.get("keywords_matched", []))
            if evidence:
                p("证据：")
                for e in evidence:
                    p(f"  • {e}")

            prose = sd.get("meaning_prose") or sd.get("meaning") or ""
            if prose:
                p(f"\n解读：{prose}")

            caution = sd.get("caution_prose", "")
            if caution:
                p(f"风险提示：{caution}")

            advice = sd.get("career_advice_prose", "")
            if advice:
                p(f"求职建议：{advice}")

    # ── Narrative Summary 块（如有独立报告块）──
    narrative_block = block_map.get("narrative_summary")
    if narrative_block and not summary:
        h1("叙事摘要")
        p(json.dumps(narrative_block, ensure_ascii=False, indent=2))

    # ── 评估方法说明 ──
    h1("评估方法说明")
    p("框架：美国劳工部 O*NET Content Model（www.onetonline.org）")
    p("人格：Big Five / IPIP-NEO-120（Public Domain）")
    p("兴趣：Holland RIASEC（O*NET 官方采用）")
    p("评分：1-7分，对齐 O*NET 原生 Level Scale")
    p("融合：量表/测验数据权重×0.6 + LLM简历行为推断×0.4")
    p("\n免责：本报告为辅助参考工具，不构成任何法律意义上的能力认证或就业建议。")

    # ------------------------------------------------------------------ #
    #  写文件
    # ------------------------------------------------------------------ #
    output_text = "\n".join(lines)
    out_path = OUTPUT_DIR / f"report_{assessment_id}.txt"
    out_path.write_text(output_text, encoding="utf-8")
    print(f"\n✅ 报告已导出：{out_path}")
    print(f"   总行数：{len(lines)}")


if __name__ == "__main__":
    aid = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(fetch_report(aid))
