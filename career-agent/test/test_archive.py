"""
test/test_archive.py — 档案（Archive）接口测试脚本。

覆盖点：
  1. GET /archive/list — 返回评估列表，包含 career_count / plan_count
  2. GET /archive/{id}/detail — 返回完整档案（profile / dimensions / careers / plans）
  3. GET /archive/milestones — 返回里程碑列表
  4. DELETE /archive/{id} — 级联删除评估及关联数据
  5. 边界：不存在的 assessment_id 返回 404

用法：
  cd career-agent
  .venv/bin/python test/test_archive.py

前提：后端已启动（默认 http://localhost:8000），数据库中至少有一条 assessment_jobs 记录。
"""

import sys
import requests

BASE = "http://localhost:8000"


def test_list():
    """测试 1：GET /archive/list"""
    r = requests.get(f"{BASE}/archive/list")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert "assessments" in data
    assert isinstance(data["assessments"], list)

    if len(data["assessments"]) > 0:
        item = data["assessments"][0]
        required_keys = {"assessment_id", "name", "current_title", "education", "status", "created_at", "career_count", "plan_count"}
        assert required_keys.issubset(item.keys()), f"missing keys: {required_keys - item.keys()}"
        assert isinstance(item["career_count"], int)
        assert isinstance(item["plan_count"], int)

    print(f"✓ 测试 1：/archive/list 返回 {len(data['assessments'])} 条记录")
    return data["assessments"]


def test_detail(assessment_id: str):
    """测试 2：GET /archive/{id}/detail"""
    r = requests.get(f"{BASE}/archive/{assessment_id}/detail")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    data = r.json()

    assert data["assessment_id"] == assessment_id
    assert "profile" in data
    assert "dimensions" in data
    assert "careers" in data
    assert "plans" in data

    # profile 应包含基本字段
    p = data["profile"]
    assert "name" in p
    assert "skills" in p
    assert isinstance(p["skills"], list)
    assert "experiences" in p
    assert isinstance(p["experiences"], list)

    # dimensions 应是 dict
    assert isinstance(data["dimensions"], dict)

    # careers / plans 应是 list
    assert isinstance(data["careers"], list)
    assert isinstance(data["plans"], list)

    # plans 中每项应有 total_tasks / completed_tasks
    for plan in data["plans"]:
        assert "total_tasks" in plan
        assert "completed_tasks" in plan
        assert isinstance(plan["total_tasks"], int)
        assert isinstance(plan["completed_tasks"], int)

    print(f"✓ 测试 2：/archive/{assessment_id}/detail 返回 profile/dimensions/careers/plans")
    print(f"    profile.name={p['name']}, dims={list(data['dimensions'].keys())}, "
          f"careers={len(data['careers'])}, plans={len(data['plans'])}")
    return data


def test_detail_404():
    """测试 3：不存在的 assessment_id 返回 404"""
    r = requests.get(f"{BASE}/archive/nonexistent_id_12345/detail")
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"
    print("✓ 测试 3：不存在的 assessment_id 正确返回 404")


def test_milestones():
    """测试 4：GET /archive/milestones"""
    r = requests.get(f"{BASE}/archive/milestones")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert "milestones" in data
    assert isinstance(data["milestones"], list)

    for m in data["milestones"][:3]:
        assert "type" in m
        assert m["type"] in ("assessment", "career_plan", "task_completed", "week_completed")
        assert "title" in m
        assert "description" in m

    print(f"✓ 测试 4：/archive/milestones 返回 {len(data['milestones'])} 条里程碑")
    if data["milestones"]:
        m = data["milestones"][0]
        print(f"    最新里程碑: [{m['type']}] {m['title']} - {m['description']}")


def test_delete_404():
    """测试 5：删除不存在的 assessment_id 返回 404"""
    r = requests.delete(f"{BASE}/archive/nonexistent_id_12345")
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"
    print("✓ 测试 5：删除不存在的 assessment_id 正确返回 404")


def main():
    print("=" * 60)
    print("  Archive 接口测试")
    print("=" * 60)

    assessments = test_list()
    test_detail_404()
    test_milestones()
    test_delete_404()

    if assessments:
        # 用第一条记录测试 detail
        test_detail(assessments[0]["assessment_id"])
    else:
        print("⚠ 数据库中无评估记录，跳过 detail 测试")

    # 注意：不在自动测试中执行真实删除，避免误删数据
    # 如需测试删除，可手动执行：
    #   requests.delete(f"{BASE}/archive/{assessment_id}")
    print()
    print("全部测试通过 ✓")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        sys.exit(1)
    except requests.ConnectionError:
        print(f"\n✗ 无法连接到 {BASE}，请确保后端已启动")
        sys.exit(1)
