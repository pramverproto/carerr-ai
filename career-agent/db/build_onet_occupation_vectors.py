"""
O*NET 职业宽表预处理脚本
将 db_30_2_mysql/ 下的 SQL 文件解析聚合，生成每职业一条宽记录，写入 MySQL。

用法：
    python db/build_onet_occupation_vectors.py

说明：
    - 完全本地解析 SQL 文件，不依赖本地 MySQL
    - 结果直接写入远程 MySQL（career_agent.onet_occupations）
    - 脚本幂等：重复运行会 REPLACE 原有数据
    - 聚合字段均为所属子维度的算术均值（LV/OI/EX/WI scale）
    - JSON 字段（riasec / tech_tools / core_tasks / related_occ）保留原始细节，供向量数据库或 LLM 使用
"""

import json
import re
import asyncio
from collections import defaultdict
from pathlib import Path

import aiomysql

# ------------------------------------------------------------------ #
#  配置
# ------------------------------------------------------------------ #
SQL_DIR = Path("/Users/jonysing/Downloads/G_Projeact/db_30_2_mysql")

DB_HOST = "127.0.0.1"
DB_PORT = 3306
DB_USER = "root"
DB_PASS = "187423"
DB_NAME = "career_agent"

# ------------------------------------------------------------------ #
#  Element ID 分组映射
#  说明：取 LV（level）scale；work_styles 取 WI；interests 取 OI；work_values 取 EX
# ------------------------------------------------------------------ #

# 认知能力子维度 → 评估报告对齐的三个子组
ABILITY_GROUPS = {
    "verbal":       ["1.A.1.a.1", "1.A.1.a.2", "1.A.1.a.3", "1.A.1.a.4"],
    "reasoning":    ["1.A.1.b.4", "1.A.1.b.5", "1.A.1.b.6",
                     "1.A.1.b.1", "1.A.1.b.2", "1.A.1.b.3", "1.A.1.b.7"],
    "quantitative": ["1.A.1.c.1", "1.A.1.c.2"],
}
# 全部认知能力（1.A.1.*）用于计算 dim_abilities
ABILITY_COGNITIVE_PREFIX = "1.A.1."

# 技能子维度
SKILL_GROUPS = {
    "basic":     ["2.A.1.a", "2.A.1.b", "2.A.1.c", "2.A.1.d", "2.A.1.e", "2.A.1.f",
                  "2.A.2.a", "2.A.2.b", "2.A.2.c", "2.A.2.d"],
    "social":    ["2.B.1.a", "2.B.1.b", "2.B.1.c", "2.B.1.d", "2.B.1.e", "2.B.1.f"],
    "technical": ["2.B.3.a", "2.B.3.b", "2.B.3.c", "2.B.3.d", "2.B.3.e",
                  "2.B.3.g", "2.B.3.h", "2.B.3.j", "2.B.3.k", "2.B.3.l", "2.B.3.m"],
    "management":["2.B.4.e", "2.B.4.g", "2.B.4.h"],
}

# 知识大类（2.C.X，对应评估报告四个子维度）
KNOWLEDGE_GROUPS = {
    "business_mgmt":    ["2.C.1"],   # Business and Management
    "tech_engineering": ["2.C.3", "2.C.4"],  # Engineering+Technology / Math+Science
    "humanities_social":["2.C.7", "2.C.9"],  # Arts+Humanities / Communications
    "applied_service":  ["2.C.5", "2.C.6", "2.C.8", "2.C.10", "2.C.2"],  # 其余
}

# 工作特质分组（1.D.*，scale WI）
WORK_STYLE_GROUPS = {
    "proactive":         ["1.D.1.a", "1.D.1.b", "1.D.1.c", "1.D.1.d",
                          "1.D.1.e", "1.D.1.f", "1.D.1.g", "1.D.1.h", "1.D.1.i"],
    "interpersonal":     ["1.D.2.a", "1.D.2.b", "1.D.2.c", "1.D.2.d", "1.D.2.e", "1.D.2.f"],
    "conscientious":     ["1.D.3.a", "1.D.3.b", "1.D.3.c", "1.D.3.d"],
    "resilient":         ["1.D.4.a", "1.D.4.b"],
}

# 工作价值观（1.B.2.*，scale EX，排除高点标记 g/h/i）
WORK_VALUE_GROUPS = {
    "achievement":   ["1.B.2.a"],
    "work_cond":     ["1.B.2.b"],
    "recognition":   ["1.B.2.c"],
    "relationships": ["1.B.2.d"],
    "support":       ["1.B.2.e"],
    "independence":  ["1.B.2.f"],
}

# 职业兴趣 RIASEC（1.B.1.*，scale OI）
RIASEC_MAP = {
    "R": "1.B.1.a",
    "I": "1.B.1.b",
    "A": "1.B.1.c",
    "S": "1.B.1.d",
    "E": "1.B.1.e",
    "C": "1.B.1.f",
}


# ------------------------------------------------------------------ #
#  SQL 解析工具
# ------------------------------------------------------------------ #

def parse_inserts(sql_path: Path, table_name: str) -> list[tuple]:
    """
    从 SQL 文件中解析 INSERT INTO <table_name> 语句，返回每行的值元组（均为字符串）。
    """
    pattern = re.compile(
        r"INSERT INTO " + re.escape(table_name) + r"\s*\([^)]+\)\s*VALUES\s*\((.+?)\);",
        re.DOTALL,
    )
    rows = []
    with open(sql_path, encoding="utf-8") as f:
        content = f.read()
    for m in pattern.finditer(content):
        raw = m.group(1)
        # 简单按逗号分割，处理带引号的字符串
        values = _split_values(raw)
        rows.append(values)
    return rows


def _split_values(raw: str) -> list[str]:
    """将 SQL VALUES 括号内容解析为列表，处理单引号转义。"""
    result = []
    current = []
    in_quote = False
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "'" and not in_quote:
            in_quote = True
            i += 1
        elif c == "'" and in_quote:
            if i + 1 < len(raw) and raw[i + 1] == "'":
                current.append("'")
                i += 2
            else:
                in_quote = False
                i += 1
        elif c == "," and not in_quote:
            result.append("".join(current).strip())
            current = []
            i += 1
        else:
            current.append(c)
            i += 1
    if current:
        result.append("".join(current).strip())
    return result


def safe_float(v: str) -> float | None:
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------ #
#  各维度数据加载函数
# ------------------------------------------------------------------ #

def load_occupations() -> dict[str, dict]:
    """返回 {onetsoc_code: {title, description}}"""
    rows = parse_inserts(SQL_DIR / "03_occupation_data.sql", "occupation_data")
    result = {}
    for r in rows:
        if len(r) >= 3:
            code, title, desc = r[0], r[1], r[2]
            result[code] = {"title": title, "description": desc}
    print(f"  职业数: {len(result)}")
    return result


def load_dimension_scores(filename: str, table: str,
                          target_scale: str,
                          col_order: tuple = (0, 1, 2, 3)
                          ) -> dict[str, dict[str, float]]:
    """
    通用维度得分加载器。
    返回 {onetsoc_code: {element_id: data_value}}
    col_order: (onetsoc_code_idx, element_id_idx, scale_id_idx, data_value_idx)
    """
    rows = parse_inserts(SQL_DIR / filename, table)
    result: dict[str, dict[str, float]] = defaultdict(dict)
    ci, ei, si, di = col_order
    for r in rows:
        if len(r) <= max(col_order):
            continue
        scale = r[si].strip()
        if scale != target_scale:
            continue
        code = r[ci].strip()
        eid = r[ei].strip()
        val = safe_float(r[di])
        if val is not None:
            result[code][eid] = val
    print(f"  {table}: {len(result)} 个职业")
    return dict(result)


def group_avg(scores: dict[str, float], element_ids: list[str]) -> float | None:
    """计算一组 element_id 的均值，忽略缺失项。"""
    vals = [scores[e] for e in element_ids if e in scores]
    return round(sum(vals) / len(vals), 2) if vals else None


def prefix_avg(scores: dict[str, float], prefix: str) -> float | None:
    """计算某前缀下所有 element_id 的均值。"""
    vals = [v for k, v in scores.items() if k.startswith(prefix)]
    return round(sum(vals) / len(vals), 2) if vals else None


def load_interests() -> dict[str, dict[str, float]]:
    """返回 {onetsoc_code: {R/I/A/S/E/C: score}}，仅取 OI scale。"""
    raw = load_dimension_scores("13_interests.sql", "interests", "OI",
                                col_order=(0, 1, 2, 3))
    result = {}
    for code, scores in raw.items():
        riasec = {}
        for letter, eid in RIASEC_MAP.items():
            if eid in scores:
                riasec[letter] = scores[eid]
        if riasec:
            result[code] = riasec
    return result


def load_tech_tools() -> dict[str, list[str]]:
    """返回 {onetsoc_code: [工具名...]}，优先取 hot_technology=Y 或 in_demand=Y。"""
    rows = parse_inserts(SQL_DIR / "31_technology_skills.sql", "technology_skills")
    result: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if len(r) < 5:
            continue
        code = r[0].strip()
        tool = r[1].strip()
        hot = r[3].strip()
        in_demand = r[4].strip()
        if hot == "Y" or in_demand == "Y":
            result[code].append(tool)
    # 去重并最多保留20个
    return {code: list(dict.fromkeys(tools))[:20] for code, tools in result.items()}


def load_core_tasks() -> dict[str, list[str]]:
    """返回 {onetsoc_code: [task...]}，最多5条核心任务。"""
    rows = parse_inserts(SQL_DIR / "17_task_statements.sql", "task_statements")
    result: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if len(r) < 3:
            continue
        code = r[0].strip()
        task = r[2].strip()
        if task and task.upper() != "NULL":
            result[code].append(task)
    return {code: tasks[:5] for code, tasks in result.items()}


def load_related_occupations() -> dict[str, list[str]]:
    """返回 {onetsoc_code: [related_code...]}，只取 Primary-Short tier。"""
    rows = parse_inserts(SQL_DIR / "27_related_occupations.sql", "related_occupations")
    result: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if len(r) < 4:
            continue
        code = r[0].strip()
        related = r[1].strip()
        tier = r[2].strip()
        if tier == "Primary-Short" and related != code:
            result[code].append(related)
    return dict(result)


def load_job_zones() -> dict[str, int]:
    """返回 {onetsoc_code: job_zone(1-5)}"""
    rows = parse_inserts(SQL_DIR / "14_job_zones.sql", "job_zones")
    result = {}
    for r in rows:
        if len(r) >= 2:
            code = r[0].strip()
            jz = safe_float(r[1])
            if jz is not None:
                result[code] = int(jz)
    return result


# ------------------------------------------------------------------ #
#  主聚合函数
# ------------------------------------------------------------------ #

def build_vectors() -> list[dict]:
    print("\n[1/9] 加载职业列表...")
    occupations = load_occupations()

    print("[2/9] 加载认知能力（abilities LV）...")
    abilities_raw = load_dimension_scores("11_abilities.sql", "abilities", "LV",
                                         col_order=(0, 1, 2, 3))

    print("[3/9] 加载技能（skills LV）...")
    skills_raw = load_dimension_scores("16_skills.sql", "skills", "LV",
                                       col_order=(0, 1, 2, 3))

    print("[4/9] 加载知识（knowledge LV）...")
    knowledge_raw = load_dimension_scores("15_knowledge.sql", "knowledge", "LV",
                                          col_order=(0, 1, 2, 3))

    print("[5/9] 加载工作特质（work_styles WI）...")
    work_styles_raw = load_dimension_scores("21_work_styles.sql", "work_styles", "WI",
                                            col_order=(0, 1, 2, 3))

    print("[6/9] 加载工作价值观（work_values EX）...")
    work_values_raw = load_dimension_scores("22_work_values.sql", "work_values", "EX",
                                            col_order=(0, 1, 2, 3))

    print("[7/9] 加载职业兴趣（interests OI）...")
    interests = load_interests()

    print("[8/9] 加载附加信息（tech_tools / tasks / related / job_zone）...")
    tech_tools = load_tech_tools()
    core_tasks = load_core_tasks()
    related_occ = load_related_occupations()
    job_zones = load_job_zones()

    print("[9/9] 聚合所有职业...")
    records = []
    for code, occ in occupations.items():
        ab = abilities_raw.get(code, {})
        sk = skills_raw.get(code, {})
        kn = knowledge_raw.get(code, {})
        ws = work_styles_raw.get(code, {})
        wv = work_values_raw.get(code, {})
        ri = interests.get(code, {})

        # ── 认知能力 ──
        ability_verbal       = group_avg(ab, ABILITY_GROUPS["verbal"])
        ability_reasoning    = group_avg(ab, ABILITY_GROUPS["reasoning"])
        ability_quantitative = group_avg(ab, ABILITY_GROUPS["quantitative"])
        dim_abilities        = prefix_avg(ab, ABILITY_COGNITIVE_PREFIX)

        # ── 技能 ──
        skill_basic      = group_avg(sk, SKILL_GROUPS["basic"])
        skill_social     = group_avg(sk, SKILL_GROUPS["social"])
        skill_technical  = group_avg(sk, SKILL_GROUPS["technical"])
        skill_management = group_avg(sk, SKILL_GROUPS["management"])
        # dim_skills = 均值（忽略 None）
        sk_vals = [v for v in [skill_basic, skill_social, skill_technical, skill_management] if v is not None]
        dim_skills = round(sum(sk_vals) / len(sk_vals), 2) if sk_vals else None

        # ── 知识 ──
        kn_business    = prefix_avg(kn, "2.C.1.")
        kn_tech        = None
        tech_vals = []
        for p in ["2.C.3.", "2.C.4."]:
            v = prefix_avg(kn, p)
            if v is not None:
                tech_vals.append(v)
        kn_tech = round(sum(tech_vals) / len(tech_vals), 2) if tech_vals else None

        hum_vals = []
        for p in ["2.C.7.", "2.C.9."]:
            v = prefix_avg(kn, p)
            if v is not None:
                hum_vals.append(v)
        kn_humanities = round(sum(hum_vals) / len(hum_vals), 2) if hum_vals else None

        applied_vals = []
        for p in ["2.C.2.", "2.C.5.", "2.C.6.", "2.C.8.", "2.C.10."]:
            v = prefix_avg(kn, p)
            if v is not None:
                applied_vals.append(v)
        kn_applied = round(sum(applied_vals) / len(applied_vals), 2) if applied_vals else None

        kn_all_vals = [v for v in [kn_business, kn_tech, kn_humanities, kn_applied] if v is not None]
        dim_knowledge = round(sum(kn_all_vals) / len(kn_all_vals), 2) if kn_all_vals else None

        # ── 工作特质 ──
        ws_proactive     = group_avg(ws, WORK_STYLE_GROUPS["proactive"])
        ws_interpersonal = group_avg(ws, WORK_STYLE_GROUPS["interpersonal"])
        ws_conscientious = group_avg(ws, WORK_STYLE_GROUPS["conscientious"])
        ws_resilient     = group_avg(ws, WORK_STYLE_GROUPS["resilient"])
        ws_vals = [v for v in [ws_proactive, ws_interpersonal, ws_conscientious, ws_resilient] if v is not None]
        dim_work_styles = round(sum(ws_vals) / len(ws_vals), 2) if ws_vals else None

        # ── 工作价值观 ──
        wv_achievement   = prefix_avg(wv, "1.B.2.a.")
        wv_work_cond     = prefix_avg(wv, "1.B.2.b.")
        wv_recognition   = prefix_avg(wv, "1.B.2.c.")
        wv_relationships = prefix_avg(wv, "1.B.2.d.")
        wv_support       = prefix_avg(wv, "1.B.2.e.")
        wv_independence  = prefix_avg(wv, "1.B.2.f.")
        wv_vals = [v for v in [wv_achievement, wv_work_cond, wv_recognition,
                                wv_relationships, wv_support, wv_independence] if v is not None]
        dim_work_values = round(sum(wv_vals) / len(wv_vals), 2) if wv_vals else None

        records.append({
            "onetsoc_code":   code,
            "title":          occ["title"],
            "description":    occ["description"],
            "job_zone":       job_zones.get(code),

            # RIASEC
            "riasec_R":  ri.get("R"),
            "riasec_I":  ri.get("I"),
            "riasec_A":  ri.get("A"),
            "riasec_S":  ri.get("S"),
            "riasec_E":  ri.get("E"),
            "riasec_C":  ri.get("C"),

            # 认知能力子维度
            "ability_verbal":       ability_verbal,
            "ability_reasoning":    ability_reasoning,
            "ability_quantitative": ability_quantitative,

            # 技能子维度
            "skill_basic":      skill_basic,
            "skill_social":     skill_social,
            "skill_technical":  skill_technical,
            "skill_management": skill_management,

            # 知识子维度
            "knowledge_business":    kn_business,
            "knowledge_tech":        kn_tech,
            "knowledge_humanities":  kn_humanities,
            "knowledge_applied":     kn_applied,

            # 工作特质子维度
            "work_style_proactive":     ws_proactive,
            "work_style_interpersonal": ws_interpersonal,
            "work_style_conscientious": ws_conscientious,
            "work_style_resilient":     ws_resilient,

            # 工作价值观子维度
            "work_value_achievement":   wv_achievement,
            "work_value_work_cond":     wv_work_cond,
            "work_value_recognition":   wv_recognition,
            "work_value_relationships": wv_relationships,
            "work_value_support":       wv_support,
            "work_value_independence":  wv_independence,

            # 六维汇总（对齐评估报告）
            "dim_skills":      dim_skills,
            "dim_knowledge":   dim_knowledge,
            "dim_abilities":   dim_abilities,
            "dim_work_styles": dim_work_styles,
            "dim_work_values": dim_work_values,

            # JSON 字段（详细数据，供 LLM 和向量库使用）
            "riasec_json":     json.dumps(ri, ensure_ascii=False) if ri else None,
            "tech_tools_json": json.dumps(tech_tools.get(code, []), ensure_ascii=False),
            "core_tasks_json": json.dumps(core_tasks.get(code, []), ensure_ascii=False),
            "related_occ_json":json.dumps(related_occ.get(code, []), ensure_ascii=False),
        })

    print(f"  聚合完成：{len(records)} 条记录")
    return records


# ------------------------------------------------------------------ #
#  建表 + 写入 MySQL
# ------------------------------------------------------------------ #

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS onet_occupations (
    onetsoc_code   CHAR(10)        NOT NULL,
    title          VARCHAR(200)    NOT NULL,
    description    TEXT,
    job_zone       TINYINT,

    -- RIASEC 职业兴趣（OI scale，0-7）
    riasec_R       DECIMAL(4,2),
    riasec_I       DECIMAL(4,2),
    riasec_A       DECIMAL(4,2),
    riasec_S       DECIMAL(4,2),
    riasec_E       DECIMAL(4,2),
    riasec_C       DECIMAL(4,2),

    -- 认知能力（LV scale，0-7）
    ability_verbal          DECIMAL(4,2),
    ability_reasoning       DECIMAL(4,2),
    ability_quantitative    DECIMAL(4,2),

    -- 技能（LV scale，0-7）
    skill_basic             DECIMAL(4,2),
    skill_social            DECIMAL(4,2),
    skill_technical         DECIMAL(4,2),
    skill_management        DECIMAL(4,2),

    -- 知识（LV scale，0-7）
    knowledge_business      DECIMAL(4,2),
    knowledge_tech          DECIMAL(4,2),
    knowledge_humanities    DECIMAL(4,2),
    knowledge_applied       DECIMAL(4,2),

    -- 工作特质（WI scale，0-7）
    work_style_proactive     DECIMAL(4,2),
    work_style_interpersonal DECIMAL(4,2),
    work_style_conscientious DECIMAL(4,2),
    work_style_resilient     DECIMAL(4,2),

    -- 工作价值观（EX scale，0-7）
    work_value_achievement   DECIMAL(4,2),
    work_value_work_cond     DECIMAL(4,2),
    work_value_recognition   DECIMAL(4,2),
    work_value_relationships DECIMAL(4,2),
    work_value_support       DECIMAL(4,2),
    work_value_independence  DECIMAL(4,2),

    -- 六维汇总（对齐评估报告维度，用于快速匹配）
    dim_skills               DECIMAL(4,2),
    dim_knowledge            DECIMAL(4,2),
    dim_abilities            DECIMAL(4,2),
    dim_work_styles          DECIMAL(4,2),
    dim_work_values          DECIMAL(4,2),

    -- JSON 详细字段（供 LLM 读取 / 后期向量化）
    riasec_json              JSON,
    tech_tools_json          JSON,
    core_tasks_json          JSON,
    related_occ_json         JSON,

    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (onetsoc_code),
    INDEX idx_riasec_I (riasec_I),
    INDEX idx_riasec_E (riasec_E),
    INDEX idx_job_zone (job_zone),
    INDEX idx_dim_abilities (dim_abilities)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def write_to_mysql(records: list[dict]) -> None:
    conn = await aiomysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS, db=DB_NAME,
        charset="utf8mb4", autocommit=True,
    )
    async with conn.cursor() as cur:
        print("\n建表（如不存在）...")
        await cur.execute(CREATE_TABLE_SQL)

        print(f"写入 {len(records)} 条记录（REPLACE INTO）...")

        FIELDS = [
            "onetsoc_code", "title", "description", "job_zone",
            "riasec_R", "riasec_I", "riasec_A", "riasec_S", "riasec_E", "riasec_C",
            "ability_verbal", "ability_reasoning", "ability_quantitative",
            "skill_basic", "skill_social", "skill_technical", "skill_management",
            "knowledge_business", "knowledge_tech", "knowledge_humanities", "knowledge_applied",
            "work_style_proactive", "work_style_interpersonal",
            "work_style_conscientious", "work_style_resilient",
            "work_value_achievement", "work_value_work_cond", "work_value_recognition",
            "work_value_relationships", "work_value_support", "work_value_independence",
            "dim_skills", "dim_knowledge", "dim_abilities", "dim_work_styles", "dim_work_values",
            "riasec_json", "tech_tools_json", "core_tasks_json", "related_occ_json",
        ]
        placeholders = ", ".join(["%s"] * len(FIELDS))
        sql = (
            f"REPLACE INTO onet_occupations ({', '.join(FIELDS)}) "
            f"VALUES ({placeholders})"
        )

        # 批量写入，每批200条
        batch_size = 200
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            rows = [tuple(r[f] for f in FIELDS) for r in batch]
            await cur.executemany(sql, rows)
            print(f"  已写入 {min(i + batch_size, len(records))}/{len(records)}")

    conn.close()
    print("\n✅ 完成！数据库已更新 career_agent.onet_occupations")


# ------------------------------------------------------------------ #
#  入口
# ------------------------------------------------------------------ #

async def main():
    print("=== O*NET 职业宽表构建 ===\n")
    print("解析 SQL 文件并聚合...")
    records = build_vectors()
    await write_to_mysql(records)

    # 打印一条样例验证
    sample = records[0]
    print(f"\n样例记录（{sample['onetsoc_code']}）：")
    for k, v in sample.items():
        if "json" in k:
            val = json.loads(v) if v else None
            print(f"  {k}: {str(val)[:80]}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
