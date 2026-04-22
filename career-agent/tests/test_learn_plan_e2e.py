"""Learn Plan 端到端测试脚本（需要后端服务运行 + 已有 assessment）。

运行：
    cd career-agent
    API_BASE_URL=http://115.120.251.185:8000 \\
    ASSESSMENT_ID=<existing_id> \\
    STAGE_CODE=<path_code-s1> \\
    uv run --no-sync python tests/test_learn_plan_e2e.py

如果没有 ASSESSMENT_ID，脚本会尝试注册 + 评估 + 选路线（较慢，需 3-5 分钟 LLM）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

import httpx


BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = 60
LLM_TIMEOUT = 300

_suffix = uuid.uuid4().hex[:6]
TEST_USER = f"plan_{_suffix}"
TEST_PASS = "Test1234!"

EXISTING_ASSESSMENT_ID = os.getenv("ASSESSMENT_ID", "")
EXISTING_STAGE_CODE = os.getenv("STAGE_CODE", "")
EXISTING_TOKEN = os.getenv("TOKEN", "")


def _print_section(title: str):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


async def _register_and_login(client: httpx.AsyncClient) -> tuple[str, int]:
    r = await client.post(f"{BASE_URL}/auth/register", json={
        "username": TEST_USER, "password": TEST_PASS,
    })
    r.raise_for_status()
    data = r.json()
    return data["token"], data["user_id"]


async def _setup_or_reuse(client: httpx.AsyncClient) -> tuple[str, str, str]:
    """返回 (token, assessment_id, stage_code)。

    如果设置了环境变量就直接复用。否则需要用户手工先完成评估+路线选择。
    """
    if EXISTING_ASSESSMENT_ID and EXISTING_STAGE_CODE and EXISTING_TOKEN:
        return EXISTING_TOKEN, EXISTING_ASSESSMENT_ID, EXISTING_STAGE_CODE

    print("\n⚠ 需要环境变量 ASSESSMENT_ID / STAGE_CODE / TOKEN")
    print("   请先手动跑过评估+路线推荐，再用对应值运行本脚本。")
    sys.exit(1)


async def test_generate_outline(client: httpx.AsyncClient, token: str,
                                  assessment_id: str, stage_code: str) -> str:
    _print_section("1. 生成大纲 POST /plan/generate")
    headers = {"Authorization": f"Bearer {token}"}
    t0 = time.time()
    r = await client.post(
        f"{BASE_URL}/plan/generate",
        headers=headers,
        json={
            "assessment_id": assessment_id,
            "stage_code": stage_code,
            "user_preference": "想多学工程落地方向",
        },
        timeout=LLM_TIMEOUT,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    data = r.json()
    elapsed = time.time() - t0
    assert "plan_id" in data
    assert "outline" in data
    outline = data["outline"]
    assert 3 <= len(outline["modules"]) <= 10
    assert abs(outline["total_weight"] - 100) < 1
    assert 2 <= outline["estimated_weeks"] <= 24
    print(f"✓ plan_id={data['plan_id']}")
    print(f"✓ modules={len(outline['modules'])} weeks≈{outline['estimated_weeks']} 耗时 {elapsed:.1f}s")
    for i, m in enumerate(outline["modules"]):
        print(f"   {i + 1}. {m['title']} - {m['weight']:.0f}% / {m['est_hours']}h")
    return data["plan_id"]


async def test_confirm_outline(client: httpx.AsyncClient, token: str, plan_id: str) -> dict:
    _print_section("2. 确认大纲并生成路线图 POST /plan/{id}/confirm-outline")
    headers = {"Authorization": f"Bearer {token}"}
    t0 = time.time()
    r = await client.post(
        f"{BASE_URL}/plan/{plan_id}/confirm-outline",
        headers=headers,
        timeout=LLM_TIMEOUT,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    data = r.json()
    elapsed = time.time() - t0
    assert data["status"] == "ready"
    assert data["total_weeks"] >= 2
    assert len(data["weeks"]) == data["total_weeks"]
    week1 = next((w for w in data["weeks"] if w["week_num"] == 1), None)
    assert week1 is not None
    print(f"✓ total_weeks={data['total_weeks']} months={len(data['months'])} 耗时 {elapsed:.1f}s")
    print(f"✓ Week 1 daily_status={week1['daily_status']}")
    return data


async def test_today_tasks(client: httpx.AsyncClient, token: str, plan_id: str) -> list[dict]:
    _print_section("3. 今日任务 GET /plan/{id}/today")
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.get(f"{BASE_URL}/plan/{plan_id}/today", headers=headers)
    assert r.status_code == 200
    data = r.json()
    tasks = data["tasks"]
    print(f"✓ 今日任务 {len(tasks)} 个（daily_limit={data['daily_limit']}）")
    for t in tasks:
        print(f"   - [{t['task_type']}] {t['title']} (+{t['actual_contribution']:.2f}分)")
    return tasks


async def test_complete_task(client: httpx.AsyncClient, token: str, task_id: int) -> dict:
    _print_section("4. 完成任务 POST /plan/task/{id}/complete")
    headers = {"Authorization": f"Bearer {token}"}
    reflection = (
        "本次学习聚焦 Transformer 的自注意力机制，手写了一个简化版 Q/K/V 映射。"
        "实践中发现 scale factor 非常关键，缺少后数值会爆炸。下一步想尝试多头拆分。"
    )
    r = await client.post(
        f"{BASE_URL}/plan/task/{task_id}/complete",
        headers=headers,
        json={"reflection": reflection},
        timeout=60,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    data = r.json()
    assert 0.6 <= data["grade_score"] <= 1.0
    assert data["final_contribution"] > 0
    progress = data["progress"]
    print(f"✓ score={data['grade_score']:.2f} comment='{data['grade_comment']}'")
    print(f"✓ final_contribution=+{data['final_contribution']:.2f}")
    print(f"✓ 进度 {progress['total_pct']:.2f}% ({progress['done_count']}/{progress['total_count']})")
    return data


async def test_progress(client: httpx.AsyncClient, token: str, plan_id: str) -> dict:
    _print_section("5. 进度条 GET /plan/{id}/progress")
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.get(f"{BASE_URL}/plan/{plan_id}/progress", headers=headers)
    assert r.status_code == 200
    data = r.json()
    print(f"✓ total_pct={data['total_pct']:.2f}% potential_pct={data['potential_pct']:.2f}%")
    print(f"✓ 完成率 {data['done_count']}/{data['total_count']}")
    return data


async def test_roadmap(client: httpx.AsyncClient, token: str, plan_id: str) -> dict:
    _print_section("6. 路线图 GET /plan/{id}/roadmap")
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.get(f"{BASE_URL}/plan/{plan_id}/roadmap", headers=headers)
    assert r.status_code == 200
    data = r.json()
    print(f"✓ 共 {data['total_weeks']} 周，{len(data['months'])} 个月")
    for w in data["weeks"][:5]:
        print(f"   Week {w['week_num']}: {w['theme']} [{w['daily_status']}]")
    if data["total_weeks"] > 5:
        print(f"   ... (共 {data['total_weeks']} 周)")
    return data


async def test_more(client: httpx.AsyncClient, token: str, plan_id: str,
                     exclude_ids: list[int]) -> list[dict]:
    _print_section("7. 再来一批 POST /plan/{id}/more")
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.post(
        f"{BASE_URL}/plan/{plan_id}/more",
        headers=headers,
        json={"exclude_ids": exclude_ids, "limit": 3},
    )
    assert r.status_code == 200
    data = r.json()
    print(f"✓ 追加 {len(data['tasks'])} 个任务")
    return data["tasks"]


async def main():
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        token, assessment_id, stage_code = await _setup_or_reuse(client)
        print(f"使用 assessment_id={assessment_id}  stage_code={stage_code}")

        plan_id = await test_generate_outline(client, token, assessment_id, stage_code)
        await test_confirm_outline(client, token, plan_id)
        tasks = await test_today_tasks(client, token, plan_id)
        assert tasks, "今日任务为空"

        first_task_id = tasks[0]["id"]
        await test_complete_task(client, token, first_task_id)
        await test_progress(client, token, plan_id)

        remaining_ids = [t["id"] for t in tasks[1:]]
        await test_more(client, token, plan_id, remaining_ids)

        await test_roadmap(client, token, plan_id)

        _print_section("全部通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
