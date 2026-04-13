import json
from typing import Any

import aiomysql
from fastapi import APIRouter, HTTPException

from app.database import get_pool

router = APIRouter()

_BLOCK_IDS = ("match_overview", "jd_recommendations", "gap_analysis", "action_plan")


@router.get("/career-plan/{assessment_id}/{onetsoc_code}")
async def get_career_plan(assessment_id: str, onetsoc_code: str) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT block_id, block_json FROM career_plan_blocks
                   WHERE assessment_id = %s AND onetsoc_code = %s""",
                (assessment_id, onetsoc_code),
            )
            rows = await cur.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"assessment_id={assessment_id} onetsoc_code={onetsoc_code} 的职业规划未找到",
        )

    blocks: dict[str, Any] = {}
    for row in rows:
        bj = row["block_json"]
        blocks[row["block_id"]] = json.loads(bj) if isinstance(bj, str) else bj

    return {
        "assessment_id": assessment_id,
        "onetsoc_code": onetsoc_code,
        "blocks": {bid: blocks.get(bid) for bid in _BLOCK_IDS},
    }
