"""
O*NET 职业向量化脚本
从 MySQL onet_occupations 表读取所有职业，
将每条职业的文本描述 embed 后写入 Qdrant onet_occupations collection。

用法：
    uv run python db/embed_onet_to_qdrant.py

说明：
    - 幂等：重复运行会 upsert（覆盖已存在的点）
    - 批量 embed：每批 20 条，避免单次请求过大
    - 职业文本格式：title + description + core_tasks + tech_tools + interests + work_values
    - Qdrant payload 保留所有数值字段，供检索后的后过滤使用
"""

import asyncio
import json
import os
from pathlib import Path

import aiomysql
from dotenv import load_dotenv
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)

load_dotenv(Path(__file__).parent.parent / ".env")

# ------------------------------------------------------------------ #
#  配置
# ------------------------------------------------------------------ #
DB_HOST     = os.getenv("DB_HOST", "115.120.251.185")
DB_PORT     = int(os.getenv("DB_PORT", 3306))
DB_USER     = os.getenv("DB_USER", "user01")
DB_PASS     = os.getenv("DB_PASSWORD", "187423")
DB_NAME     = "career_agent"

QDRANT_URL      = os.getenv("QDRANT_URL", "http://115.120.251.185:6333")
COLLECTION_NAME = "onet_occupations"
VECTOR_SIZE     = 1536

OPENAI_API_KEY  = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
EMBED_MODEL     = "text-embedding-3-small"
BATCH_SIZE      = 20   # 每批 embed 数量

# RIASEC 类型名称
RIASEC_NAMES = {
    "R": "Realistic", "I": "Investigative", "A": "Artistic",
    "S": "Social", "E": "Enterprising", "C": "Conventional",
}


# ------------------------------------------------------------------ #
#  职业文本构建（决定检索语义）
# ------------------------------------------------------------------ #

def build_occupation_text(row: dict) -> str:
    """
    将一条职业记录拼成供 embedding 的文本。
    格式设计原则：
      - 标题和描述放最前（权重最高）
      - 核心任务体现"做什么"
      - 技术工具体现"用什么"
      - 兴趣/价值观体现"适合谁"
    总长度控制在 400-500 token 以内。
    """
    parts = []

    # 1. 标题 + 描述
    title = row.get("title", "")
    desc  = row.get("description", "") or ""
    parts.append(f"{title}. {desc[:400]}")

    # 2. 核心任务（最多 3 条）
    tasks_raw = row.get("core_tasks_json")
    if tasks_raw:
        tasks = json.loads(tasks_raw) if isinstance(tasks_raw, str) else tasks_raw
        if tasks:
            task_text = "; ".join(t[:80] for t in tasks[:3])
            parts.append(f"Core tasks: {task_text}")

    # 3. 技术工具（最多 10 个）
    tools_raw = row.get("tech_tools_json")
    if tools_raw:
        tools = json.loads(tools_raw) if isinstance(tools_raw, str) else tools_raw
        if tools:
            parts.append(f"Key tools: {', '.join(tools[:10])}")

    # 4. 职业兴趣（只列出高分 RIASEC，>= 4.0）
    riasec_raw = row.get("riasec_json")
    if riasec_raw:
        riasec = json.loads(riasec_raw) if isinstance(riasec_raw, str) else riasec_raw
        high = [(RIASEC_NAMES.get(k, k), round(v, 1))
                for k, v in riasec.items() if v and v >= 4.0]
        high.sort(key=lambda x: -x[1])
        if high:
            parts.append("Interests: " + ", ".join(f"{n}({s})" for n, s in high))

    # 5. 工作价值观（简要）
    val_parts = []
    for col, label in [
        ("work_value_achievement", "Achievement"),
        ("work_value_independence", "Independence"),
        ("work_value_recognition", "Recognition"),
    ]:
        v = row.get(col)
        if v and float(v) >= 5.0:
            val_parts.append(label)
    if val_parts:
        parts.append(f"Work values: {', '.join(val_parts)}")

    return "\n".join(parts)


def build_payload(row: dict) -> dict:
    """构建 Qdrant payload，保留所有数值和 JSON 字段供后过滤使用。"""
    def _float(v):
        return float(v) if v is not None else None

    riasec_raw = row.get("riasec_json")
    riasec = json.loads(riasec_raw) if isinstance(riasec_raw, str) and riasec_raw else {}

    tools_raw = row.get("tech_tools_json")
    tools = json.loads(tools_raw) if isinstance(tools_raw, str) and tools_raw else []

    tasks_raw = row.get("core_tasks_json")
    tasks = json.loads(tasks_raw) if isinstance(tasks_raw, str) and tasks_raw else []

    related_raw = row.get("related_occ_json")
    related = json.loads(related_raw) if isinstance(related_raw, str) and related_raw else []

    return {
        "onetsoc_code":  row["onetsoc_code"],
        "title":         row["title"],
        "description":   (row.get("description") or "")[:300],
        "job_zone":      int(row["job_zone"]) if row.get("job_zone") else None,

        # RIASEC（供 Holland Code 后过滤）
        "riasec_R": _float(riasec.get("R")),
        "riasec_I": _float(riasec.get("I")),
        "riasec_A": _float(riasec.get("A")),
        "riasec_S": _float(riasec.get("S")),
        "riasec_E": _float(riasec.get("E")),
        "riasec_C": _float(riasec.get("C")),

        # 六维汇总（供匹配参考）
        "dim_abilities":   _float(row.get("dim_abilities")),
        "dim_skills":      _float(row.get("dim_skills")),
        "dim_knowledge":   _float(row.get("dim_knowledge")),
        "dim_work_styles": _float(row.get("dim_work_styles")),
        "dim_work_values": _float(row.get("dim_work_values")),

        # 附加信息
        "tech_tools":    tools[:15],
        "core_tasks":    tasks[:3],
        "related_occ":   related[:5],
    }


# ------------------------------------------------------------------ #
#  Qdrant collection 初始化
# ------------------------------------------------------------------ #

async def ensure_collection(qdrant: AsyncQdrantClient) -> None:
    existing = await qdrant.get_collections()
    names = [c.name for c in existing.collections]
    if COLLECTION_NAME not in names:
        await qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        print(f"  ✅ 创建 collection: {COLLECTION_NAME}")
    else:
        print(f"  collection {COLLECTION_NAME} 已存在，将 upsert")


# ------------------------------------------------------------------ #
#  主流程
# ------------------------------------------------------------------ #

async def main():
    print("=== O*NET 职业向量化 ===\n")

    # 1. 读 MySQL
    print("[1/4] 从 MySQL 读取职业数据...")
    conn = await aiomysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS, db=DB_NAME,
        charset="utf8mb4",
    )
    async with conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute("SELECT * FROM onet_occupations")
        rows = await cur.fetchall()
    conn.close()
    print(f"  读取 {len(rows)} 条职业记录")

    # 2. 初始化客户端
    print("\n[2/4] 初始化 OpenAI 和 Qdrant 客户端...")
    openai = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    qdrant = AsyncQdrantClient(url=QDRANT_URL)
    await ensure_collection(qdrant)

    # 3. 批量 embed + 写入
    print(f"\n[3/4] 批量 embed（每批 {BATCH_SIZE} 条）并写入 Qdrant...")
    total = len(rows)
    upserted = 0

    for i in range(0, total, BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]

        # 构建文本
        texts = [build_occupation_text(r) for r in batch]

        # 调用 embedding API
        resp = await openai.embeddings.create(
            model=EMBED_MODEL,
            input=texts,
        )
        vectors = [item.embedding for item in resp.data]

        # 构建 Qdrant points（用 onetsoc_code 的数字部分作为 ID）
        points = []
        for j, (row, vec) in enumerate(zip(batch, vectors)):
            # onetsoc_code 形如 "15-2051.00"，转为唯一整数
            code = row["onetsoc_code"].replace("-", "").replace(".", "")
            point_id = int(code)
            points.append(PointStruct(
                id=point_id,
                vector=vec,
                payload=build_payload(row),
            ))

        await qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
        upserted += len(batch)
        print(f"  已处理 {min(upserted, total)}/{total}  "
              f"(tokens used: {resp.usage.total_tokens})")

    # 4. 验证
    print("\n[4/4] 验证写入结果...")
    info = await qdrant.get_collection(COLLECTION_NAME)
    print(f"  collection vectors_count: {info.vectors_count}")
    print(f"  collection points_count:  {info.points_count}")

    # 抽查一条
    results = await qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query_filter=None,
        limit=1,
        with_payload=True,
        with_vectors=False,
        query=[0.0] * VECTOR_SIZE,  # 随便一个向量，只为取一条看结构
    )
    if results.points:
        p = results.points[0]
        print(f"\n  样例点 id={p.id}")
        print(f"  title: {p.payload.get('title')}")
        print(f"  job_zone: {p.payload.get('job_zone')}")
        print(f"  riasec_I: {p.payload.get('riasec_I')}")
        print(f"  dim_abilities: {p.payload.get('dim_abilities')}")
        print(f"  tech_tools: {p.payload.get('tech_tools', [])[:3]}")

    await qdrant.close()
    print(f"\n✅ 完成！{upserted} 条职业已向量化写入 Qdrant [{COLLECTION_NAME}]")


if __name__ == "__main__":
    asyncio.run(main())
