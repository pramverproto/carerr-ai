"""
测试脚本：通过 /invoke 接口调用 assessment_agent，完成一次完整评估。
用法：
    python test/test_assessment_agent.py
"""

import json
import time
import requests

BASE_URL = "http://localhost:8000"
CANDIDATE_ID = 1  # 周启航，DB 中 id=1


def run_assessment():
    print(f"[1/1] 调用 assessment_agent（candidate_id={CANDIDATE_ID}）...")
    t0 = time.perf_counter()

    resp = requests.post(
        f"{BASE_URL}/invoke",
        json={
            "agent_name": "assessment_agent",
            "task": f"请对 candidate_id={CANDIDATE_ID} 的候选人进行完整能力评估，生成报告并入库。",
        },
        timeout=600,  # 评估+报告生成耗时较长，设置10分钟超时
    )
    elapsed = time.perf_counter() - t0

    if resp.status_code != 200:
        print(f"❌ 请求失败 HTTP {resp.status_code}:")
        print(resp.text)
        return

    data = resp.json()
    print(f"\n✅ 完成，总耗时 {elapsed:.1f}s")
    print(f"agent_name : {data.get('agent_name')}")
    print(f"elapsed_ms : {data.get('elapsed_ms')} ms")
    print(f"\nAgent 输出：\n{data.get('result')}")


if __name__ == "__main__":
    run_assessment()
