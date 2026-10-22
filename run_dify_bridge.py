"""Start one local browser worker; no browser/account secrets are printed."""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env.dify.local"


def initialize():
    if ENV_PATH.exists():
        print(f"配置已存在，未覆盖：{ENV_PATH}")
        return
    key = secrets.token_urlsafe(36)
    with ENV_PATH.open("x", encoding="utf-8") as stream:
        stream.write("# 仅用于本机浏览器工具，不是 Dify 平台的 API Key。不要发送到聊天。\n")
        stream.write(f"BROWSER_API_KEY={key}\n")
    print(f"本机密钥已生成：{ENV_PATH}；请在 Dify 的 Secret 环境变量中配置同一个值。")


def load_environment():
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in {"BROWSER_API_KEY", "BROWSER_WORK_DIR"}:
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


def main():
    parser = argparse.ArgumentParser(description="Dify 抖店本地浏览器工具服务")
    parser.add_argument("--init", action="store_true", help="生成本机工具密钥，不覆盖已有配置")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-live", action="store_true", help="允许真实抖店商品任务；仍只保存草稿")
    parser.add_argument("--headless-demo", action="store_true", help="本地演示使用无头浏览器")
    args = parser.parse_args()
    if args.init:
        initialize()
        return
    load_environment()
    from dify_bridge.api import Settings, create_app
    import uvicorn
    settings = Settings.from_environment()
    settings.allow_live, settings.headless_demo = args.allow_live, args.headless_demo
    if sys.platform == "win32":
        # Playwright's driver requires subprocess-capable Proactor on Windows.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    print("模式：真实商品（仅保存草稿）" if args.allow_live else "模式：仅本地演示，不连接店铺")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, loop="asyncio", workers=1)


if __name__ == "__main__":
    main()
