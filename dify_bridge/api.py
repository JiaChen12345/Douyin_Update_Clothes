from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader

from .models import AdvanceTask, CreateTask, VisionResult
from .service import BridgeError, TaskManager

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    api_key: str
    root: Path = ROOT
    work_dir: Path | None = None
    allow_live: bool = False
    headless_demo: bool = False

    @classmethod
    def from_environment(cls):
        key = os.environ.get("BROWSER_API_KEY", "")
        if len(key) < 32:
            raise RuntimeError("请先运行 python run_dify_bridge.py --init，或设置至少32字符的 BROWSER_API_KEY")
        return cls(key, work_dir=Path(os.environ.get("BROWSER_WORK_DIR", ROOT / "work" / "dify")))


def create_app(settings: Settings | None = None, runner_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        chosen = settings or Settings.from_environment()
        if len(chosen.api_key) < 32:
            raise RuntimeError("浏览器服务密钥长度不足")
        application.state.settings = chosen
        application.state.manager = TaskManager(
            chosen.root, chosen.work_dir or chosen.root / "work" / "dify",
            chosen.allow_live, chosen.headless_demo, runner_factory)
        try:
            yield
        finally:
            await application.state.manager.shutdown()

    application = FastAPI(title="抖店本地浏览器工具", version="1.0.0", lifespan=lifespan,
                          description="Dify 编排现有抖店步骤；本服务仅保存草稿。截图和本地路径均由本机管理。")
    key_header = APIKeyHeader(name="X-Browser-Key", auto_error=False)

    async def authorized(key: str | None = Security(key_header)):
        if not key or not secrets.compare_digest(key.encode("utf-8"), application.state.settings.api_key.encode("utf-8")):
            raise HTTPException(401, "浏览器工具鉴权失败")

    protected = [Depends(authorized)]

    @application.exception_handler(BridgeError)
    async def bridge_error_handler(_request, error: BridgeError):
        return JSONResponse(status_code=error.status, content={"error": str(error)})

    @application.get("/health", operation_id="browser_service_health")
    async def health():
        return {"service": "douyin-browser-bridge", "ready": True}

    @application.post("/tasks", dependencies=protected, operation_id="create_douyin_task")
    async def create_task(request: CreateTask):
        return application.state.manager.create(request)

    @application.get("/tasks/{task_id}", dependencies=protected, operation_id="get_douyin_task")
    async def get_task(task_id: str, wait_seconds: float = Query(0, ge=0, le=15)):
        return await application.state.manager.poll(task_id, wait_seconds)

    @application.post("/tasks/{task_id}/advance", dependencies=protected, operation_id="run_next_douyin_step")
    async def advance_task(task_id: str, request: AdvanceTask):
        return await application.state.manager.advance(task_id, request.stage)

    @application.post("/tasks/{task_id}/resume", dependencies=protected, operation_id="resume_douyin_task")
    async def resume_task(task_id: str):
        return await application.state.manager.resume(task_id)

    @application.post("/tasks/{task_id}/cancel", dependencies=protected, operation_id="cancel_douyin_task")
    async def cancel_task(task_id: str):
        return await application.state.manager.cancel(task_id)

    @application.get("/tasks/{task_id}/screenshots/{screenshot_id}.png", dependencies=protected,
                     operation_id="get_current_target_screenshot", response_class=FileResponse,
                     responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}})
    async def screenshot(task_id: str, screenshot_id: str):
        task = application.state.manager.get(task_id)
        if not task.snapshot or task.snapshot.id != screenshot_id:
            raise HTTPException(404, "当前任务没有该截图")
        return FileResponse(task.snapshot.path, media_type="image/png", filename=f"target-{screenshot_id}.png",
                            headers={"Cache-Control": "no-store"})

    @application.post("/tasks/{task_id}/vision", dependencies=protected, operation_id="submit_target_location")
    async def submit_target(task_id: str, request: VisionResult):
        return await application.state.manager.submit_vision(task_id, request)

    return application


app = create_app()
