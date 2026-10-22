"""Exercise the Dify HTTP protocol against a running local backend, demo only."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from run_dify_bridge import load_environment


async def run(base_url: str, output: Path):
    load_environment()
    key = os.environ.get("BROWSER_API_KEY", "")
    if len(key) < 32:
        raise RuntimeError("请先运行 run-dify-bridge.bat，生成本机工具密钥并启动后端")
    # Local browser credentials must not be forwarded through an environment proxy.
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), headers={"X-Browser-Key": key}, timeout=30, trust_env=False) as client:
        response = await client.post("/tasks", json={"request_id": "smoke-" + uuid.uuid4().hex, "mode": "demo"})
        response.raise_for_status()
        identity = response.json()["task_id"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            response = await client.get(f"/tasks/{identity}", params={"wait_seconds": 15})
            response.raise_for_status()
            report = response.json()
            if report.get("simulation") is not True:
                raise RuntimeError("冒烟测试只允许本地演示，不能操作真实店铺")
            if report["status"] == "IDLE":
                response = await client.post(f"/tasks/{identity}/advance", json={"stage": report["next_stage"]})
                response.raise_for_status()
            elif report["status"] == "RUNNING":
                continue
            elif report["status"] == "SUCCESS":
                if report["completed_steps"] != 14 or report["results"]["save_draft"].get("draft_saved") is not False:
                    raise RuntimeError("演示结果缺少步骤或错误声明真实草稿成功")
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"DIFY_BRIDGE_SMOKE_OK: task_id={identity}, steps=14/14, simulation=true")
                print(f"报告：{output.resolve()}")
                return
            else:
                raise RuntimeError(f"演示暂停或失败：{report['status']}：{report['message']}")
        await client.post(f"/tasks/{identity}/cancel")
        raise RuntimeError("本地演示超时，已取消，不会重复创建任务")


def main():
    parser = argparse.ArgumentParser(description="测试 Dify 抖店后端 HTTP 链路，不连接真实店铺")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=ROOT / ".test-artifacts" / "dify-smoke.json")
    args = parser.parse_args()
    asyncio.run(run(args.base_url, args.output))


if __name__ == "__main__":
    main()
