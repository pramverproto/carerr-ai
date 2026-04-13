import json
from typing import Any

import aiomysql
from fastapi import APIRouter, HTTPException

from app.database import get_pool

router = APIRouter()


@router.get("/report/{assessment_id}")
async def get_report(assessment_id: str) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 验证评估任务存在
            await cur.execute(
                "SELECT status FROM assessment_jobs WHERE assessment_id = %s",
                (assessment_id,),
            )
            job = await cur.fetchone()
            if not job:
                raise HTTPException(status_code=404, detail=f"assessment_id={assessment_id} not found")

            # 读取报告 blocks 缓存
            await cur.execute(
                "SELECT block_id, block_json FROM assessment_report_blocks WHERE assessment_id = %s",
                (assessment_id,),
            )
            block_rows = await cur.fetchall()

    if not block_rows:
        raise HTTPException(
            status_code=404,
            detail="报告尚未生成，请先调用 career-agent 的 GET /report/{assessment_id} 生成报告",
        )

    blocks: dict[str, Any] = {}
    for row in block_rows:
        bj = row["block_json"]
        blocks[row["block_id"]] = json.loads(bj) if isinstance(bj, str) else bj

    return {
        "assessment_id": assessment_id,
        "status": job["status"],
        "blocks": blocks,
    }
