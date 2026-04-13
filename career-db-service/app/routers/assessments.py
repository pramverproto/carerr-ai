import json
from typing import Any

import aiomysql
from fastapi import APIRouter, HTTPException, Query

from app.database import get_pool

router = APIRouter()


@router.get("/assessments")
async def list_assessments(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT assessment_id, session_id, status, error, created_at, updated_at
                   FROM assessment_jobs
                   ORDER BY created_at DESC
                   LIMIT %s OFFSET %s""",
                (limit, offset),
            )
            rows = await cur.fetchall()
            await cur.execute("SELECT COUNT(*) AS total FROM assessment_jobs")
            total_row = await cur.fetchone()

    total = total_row["total"] if total_row else 0
    items = []
    for row in rows:
        items.append({
            "assessment_id": row["assessment_id"],
            "session_id": row["session_id"],
            "status": row["status"],
            "error": row["error"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        })
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/assessments/{assessment_id}")
async def get_assessment(assessment_id: str) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """SELECT assessment_id, session_id, status, error, created_at, updated_at
                   FROM assessment_jobs WHERE assessment_id = %s""",
                (assessment_id,),
            )
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"assessment_id={assessment_id} not found")
    return {
        "assessment_id": row["assessment_id"],
        "session_id": row["session_id"],
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }
