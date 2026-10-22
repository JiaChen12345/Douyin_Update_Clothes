from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .legacy import DemoRunner, LegacyRunner
from .models import CreateTask, NeedsHuman, STAGES, TERMINAL, VisionResult
from .vision import Snapshot


class BridgeError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Task:
    id: str
    request: CreateTask
    folder: Path
    status: str = "IDLE"
    index: int = 0
    message: str = "等待 Dify 调用第一个步骤"
    results: dict = field(default_factory=dict)
    logs: list = field(default_factory=list)
    runner: object = None
    worker: asyncio.Task | None = None
    snapshot: Snapshot | None = None
    vision_future: asyncio.Future | None = None
    vision_tries: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TaskManager:
    def __init__(self, root: Path, folder: Path, allow_live=False, headless_demo=False, runner_factory=None):
        self.root, self.folder = root, folder
        self.allow_live, self.headless_demo = allow_live, headless_demo
        self.runner_factory = runner_factory
        self.tasks: dict[str, Task] = {}
        self.requests: dict[str, str] = {}
        self.owner: str | None = None
        folder.mkdir(parents=True, exist_ok=True)
        self._restore()

    def _restore(self):
        for path in self.folder.glob("*/task.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                request = CreateTask.model_validate(record["request"])
                identity = record["task_id"]
                if not re.fullmatch(r"[a-f0-9]{32}", identity) or path.parent.name != identity:
                    continue
                task = Task(identity, request, path.parent, results=record.get("results", {}),
                            logs=record.get("logs", []), index=record.get("index", 0))
                task.status = record["status"] if record["status"] in TERMINAL else "INTERRUPTED"
                task.message = record.get("message", "") if task.status in TERMINAL - {"INTERRUPTED"} else "后端已重启，请核查页面状态；不会自动重复执行旧任务"
                self.tasks[identity] = task
                self.requests[request.request_id] = identity
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def _save(self, task: Task):
        record = self.report(task)
        record.update(request=task.request.model_dump(), index=task.index, logs=task.logs[-100:])
        temporary = task.folder / "task.json.tmp"
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(task.folder / "task.json")

    def _log(self, task: Task, message: str):
        task.logs.append({"time": time.time(), "message": message[:1000]})
        self._save(task)

    def get(self, identity: str) -> Task:
        if identity not in self.tasks:
            raise BridgeError(404, "任务不存在")
        return self.tasks[identity]

    def report(self, task: Task) -> dict:
        stage = STAGES[task.index] if task.index < len(STAGES) else ("", "已完成")
        return {"task_id": task.id, "status": task.status, "message": task.message,
                "next_stage": stage[0], "stage_label": stage[1],
                "completed_steps": task.index, "total_steps": len(STAGES),
                "simulation": task.request.mode == "demo", "results": task.results,
                "vision": task.snapshot.public() if task.snapshot and task.status == "NEEDS_VISION" else {},
                "logs": task.logs[-20:]}

    def create(self, request: CreateTask) -> dict:
        if request.platform.strip().casefold() not in {"抖店", "douyin", "doudian"}:
            raise BridgeError(422, "本阶段仅接入抖店；搜款网和17网工作流尚未启用")
        existing = self.requests.get(request.request_id)
        if existing:
            task = self.get(existing)
            if task.request != request:
                raise BridgeError(409, "同一请求编号不能绑定不同的任务内容")
            return self.report(task)
        if len(self.tasks) >= 200:
            raise BridgeError(409, "本地任务记录已达上限，请先整理历史记录")
        if request.mode == "live":
            if not self.allow_live:
                raise BridgeError(403, "后端当前仅允许演示；使用 --allow-live 才能处理真实商品")
            parsed = urlparse(request.source_url)
            host = parsed.hostname or ""
            domains = ("taobao.com", "tmall.com", "vvic.com", "17zwd.com")
            if parsed.scheme not in {"http", "https"} or not any(host == domain or host.endswith("." + domain) for domain in domains):
                raise BridgeError(422, "请提供淘宝、天猫、搜款网或17网的商品链接")
            if not request.save_directory.strip():
                raise BridgeError(422, "请先填写本机素材保存目录")
            directory = Path(request.save_directory)
            if not directory.is_absolute():
                raise BridgeError(422, "保存目录必须是本机绝对路径，不能使用相对目录")
            try:
                directory.mkdir(parents=True, exist_ok=True)
                if not directory.is_dir():
                    raise OSError("不是目录")
            except (OSError, ValueError):
                raise BridgeError(422, "无法创建或访问指定的本机保存目录")
        identity = uuid.uuid4().hex
        folder = self.folder / identity
        folder.mkdir()
        task = Task(identity, request, folder)
        self.tasks[identity] = task
        self.requests[request.request_id] = identity
        self._save(task)
        return self.report(task)

    async def advance(self, identity: str, stage: str) -> dict:
        task = self.get(identity)
        async with task.lock:
            # An HTTP retry for a completed step returns its existing outcome.
            if stage in task.results:
                return self.report(task)
            expected = STAGES[task.index][0] if task.index < len(STAGES) else ""
            if stage != expected:
                raise BridgeError(409, "步骤顺序错误，请重新读取任务状态")
            if task.status == "RUNNING":
                return self.report(task)
            if task.status != "IDLE":
                raise BridgeError(409, "当前任务不能执行新步骤，请先处理暂停或终止状态")
            if self.owner not in {None, identity}:
                raise BridgeError(409, "另一个任务正在使用专用浏览器，请先完成或取消该任务")
            self.owner = identity
            if task.runner is None:
                factory = self.runner_factory
                if factory:
                    task.runner = factory(task, lambda message: self._log(task, message),
                                          lambda page, target: self._request_target(task, page, target))
                else:
                    arguments = (self.root, task.request, task.folder,
                                 lambda message: self._log(task, message),
                                 lambda page, target: self._request_target(task, page, target))
                    task.runner = (DemoRunner(*arguments, headless=self.headless_demo)
                                   if task.request.mode == "demo" else LegacyRunner(*arguments))
            task.status, task.message = "RUNNING", STAGES[task.index][1]
            self._save(task)
            task.worker = asyncio.create_task(self._execute(task, stage))
            return self.report(task)

    async def _execute(self, task: Task, stage: str):
        try:
            task.results[stage] = await task.runner.run_stage(stage)
            task.index += 1
            task.snapshot = None
            if task.index == len(STAGES):
                await self._close_runner(task)
                task.status = "SUCCESS"
                task.message = "本地演示完成，没有创建真实商品" if task.request.mode == "demo" else "已观察到抖店草稿保存成功"
            else:
                task.status, task.message = "IDLE", "当前步骤完成，等待 Dify 调用下一步骤"
        except NeedsHuman as error:
            task.status, task.message = "WAITING_HUMAN", str(error)
        except asyncio.CancelledError:
            task.status, task.message = "CANCELLED", "用户已取消任务"
            await self._close_runner(task)
        except Exception as error:
            task.status = "FAILED"
            message = str(error).splitlines()[0][:400] if str(error) else type(error).__name__
            task.message = "步骤失败：" + re.sub(r"(?i)(token|api[_-]?key|password)=\S+", r"\1=[隐藏]", message)
            await self._close_runner(task)
        finally:
            self._save(task)

    async def poll(self, identity: str, wait_seconds: float = 0) -> dict:
        task = self.get(identity)
        deadline = time.monotonic() + min(max(wait_seconds, 0), 15)
        while task.status == "RUNNING" and time.monotonic() < deadline:
            await asyncio.sleep(.1)
        return self.report(task)

    async def _request_target(self, task: Task, page, target):
        key = f"{task.index}:{target}"
        attempts = task.vision_tries.get(key, 0)
        if attempts >= 2:
            raise NeedsHuman("同一目标已达到两次视觉识别上限，请人工处理")
        task.snapshot = await Snapshot.create(page, task.folder / "screenshots", target, task.request.roi)
        if not task.request.vision_enabled:
            raise NeedsHuman(f"无法可靠定位“{target}”；当前工作流未配置视觉模型，请人工处理后恢复")
        task.vision_tries[key] = attempts + 1
        task.vision_future = asyncio.get_running_loop().create_future()
        task.status, task.message = "NEEDS_VISION", f"等待 Dify 在指定截图中定位“{target}”"
        self._save(task)
        try:
            return await asyncio.wait_for(task.vision_future, timeout=180)
        except asyncio.TimeoutError:
            raise NeedsHuman("等待 Dify 视觉识别超时，请配置模型或人工处理后恢复")
        finally:
            task.vision_future = None

    async def submit_vision(self, identity: str, result: VisionResult) -> dict:
        task = self.get(identity)
        async with task.lock:
            if task.status != "NEEDS_VISION" or task.vision_future is None or task.vision_future.done():
                raise BridgeError(409, "任务没有等待识别结果，不能重复提交")
            try:
                locator = await task.snapshot.resolve(result)
            except NeedsHuman as error:
                task.vision_future.set_exception(error)
                return {"accepted": False, "message": str(error), "task_id": identity}
            task.status, task.message = "RUNNING", "识别结果已校验，继续原操作并检查预期结果"
            task.vision_future.set_result(locator)
            self._save(task)
            return {"accepted": True, "task_id": identity}

    async def resume(self, identity: str) -> dict:
        task = self.get(identity)
        async with task.lock:
            if task.status != "WAITING_HUMAN" and task.status != "INTERRUPTED":
                return self.report(task)  # Continue polling after a cloud timeout; do not restart a step.
            if task.status != "WAITING_HUMAN" or (task.worker and not task.worker.done()):
                raise BridgeError(409, "只有当前进程中等待人工的任务可以恢复；重启任务需先人工核查")
            task.status, task.message = "IDLE", "已收到恢复请求，等待重新执行当前未完成步骤"
            self._save(task)
            return self.report(task)

    async def cancel(self, identity: str) -> dict:
        task = self.get(identity)
        if task.status in TERMINAL:
            return self.report(task)
        if task.worker and not task.worker.done():
            task.worker.cancel()
            await asyncio.gather(task.worker, return_exceptions=True)
        await self._close_runner(task)
        task.status, task.message = "CANCELLED", "用户已取消任务"
        self._save(task)
        return self.report(task)

    async def _close_runner(self, task: Task):
        if task.runner:
            runner, task.runner = task.runner, None
            try:
                await runner.close()
            except Exception:
                task.logs.append({"time": time.time(), "message": "浏览器清理未完成，请在本机核查"})
        if self.owner == task.id:
            self.owner = None

    async def shutdown(self):
        for task in list(self.tasks.values()):
            if task.status not in TERMINAL:
                await self.cancel(task.id)
