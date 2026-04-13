import json
from typing import Any

import aiomysql
from fastapi import APIRouter, HTTPException

from app.database import get_pool

router = APIRouter()


@router.get("/candidate/{assessment_id}")
async def get_candidate(assessment_id: str) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT input_snapshot FROM assessment_jobs WHERE assessment_id = %s",
                (assessment_id,),
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"assessment_id={assessment_id} not found")

    snapshot = row["input_snapshot"]
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail="候选人原始信息未存储（评估发起时版本较旧，不含 input_snapshot）",
        )

    if isinstance(snapshot, str):
        data = json.loads(snapshot)
    else:
        data = snapshot  # aiomysql 有时直接返回 dict

    return {"assessment_id": assessment_id, "data": data}
