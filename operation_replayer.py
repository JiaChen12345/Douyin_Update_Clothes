"""Semantic Playwright replay engine for recorded draft operations."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable

from operation_recorder import is_forbidden_action
from recording_failures import FailureTracker
from recording_models import LocatorSpec, RecordingStep, ReplayResult, StepResult
from recording_store import RecordingStore


class OperationReplayer:
    def __init__(self, store: RecordingStore, failures: FailureTracker, log: Callable[[str], None] | None = None,
                 stopped: Callable[[], bool] | None = None):
        self.store = store
        self.failures = failures
        self.log = log or (lambda _message: None)
        self.stopped = stopped or (lambda: False)

    async def replay(self, page, site: str, category: str, task_data: dict[str, Any]) -> ReplayResult:
        profile = self.store.load(site, category)
        task_data = dict(task_data)
        task_data["_upload_offsets"] = {}
        result = ReplayResult(True, False)
        for step in profile.steps:
            if self.stopped():
                return ReplayResult(False, False, result.steps, result.warnings, step.id, "用户已停止")
            step_result = await self._execute_with_retries(page, step, task_data)
            result.steps.append(step_result)
            if step_result.success:
                self.failures.success(site, category, step.id)
                result.draft_saved = result.draft_saved or step.action == "save_draft"
                continue
            notice = self.failures.failure(site, category, step.id, step_result.reason)
            if notice:
                self.log("RECORDING_RERECORD_REQUIRED " + json.dumps(notice, ensure_ascii=False))
            if step.critical:
                result.success = False; result.failed_step = step.id; result.failure_reason = step_result.reason
                return result
            warning = f"{step.name}：{step_result.reason}"
            result.warnings.append(warning); self.log(f"警告：{warning}，继续后续步骤")
        result.success = result.draft_saved
        if not result.draft_saved:
            result.failure_reason = "录制流程未确认保存草稿"
        return result

    async def _execute_with_retries(self, page, step: RecordingStep, data: dict[str, Any]) -> StepResult:
        last = "未知错误"
        for attempt in range(1, step.retries + 1):
            try:
                await self.wait_stable(page, step.timeout_ms)
                strategy = await self.execute_step(page, step, data)
                await self.validate_step(page, step)
                self.log(f"[{step.id}] {step.name} 成功（{strategy}）")
                return StepResult(step.id, True, strategy)
            except Exception as exc:
                last = str(exc); self.log(f"[{step.id}] {step.name} 第{attempt}/{step.retries}次失败：{last}")
        return StepResult(step.id, False, reason=last, skipped=not step.critical)

    async def wait_stable(self, page, timeout_ms: int) -> None:
        if self.stopped():
            raise RuntimeError("用户已停止")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 10000))
        except Exception:
            pass
        previous = None
        for _ in range(5):
            if self.stopped(): raise RuntimeError("用户已停止")
            current = await page.evaluate("document.getElementsByTagName('*').length")
            if current == previous: return
            previous = current
            await asyncio.sleep(0.2)

    async def resolve(self, page, spec: LocatorSpec, timeout_ms: int):
        candidates: list[tuple[str, Any]] = []
        if spec.role and (spec.name or spec.text):
            candidates.append(("role", page.get_by_role(spec.role, name=spec.name or spec.text, exact=True)))
        if spec.label:
            candidates.append(("label", page.get_by_label(spec.label, exact=True)))
        if spec.text:
            candidates.append(("text", page.get_by_text(spec.text, exact=True)))
        attrs = spec.attributes
        for key in ("data-testid", "data-id", "id", "name", "placeholder"):
            if attrs.get(key):
                escaped = str(attrs[key]).replace('"', '\\"')
                candidates.append((f"attribute:{key}", page.locator(f'[{key}="{escaped}"]')))
        for strategy, locator in candidates:
            try:
                first = locator.first
                await first.wait_for(state="visible", timeout=min(timeout_ms, 2500))
                if await first.is_enabled(): return first, strategy
            except Exception:
                continue
        if spec.x_ratio is not None and spec.y_ratio is not None:
            return None, "coordinate"
        raise RuntimeError("无法通过语义、结构或稳定属性定位控件")

    async def execute_step(self, page, step: RecordingStep, data: dict[str, Any]) -> str:
        if step.action in {"navigate", "wait"}:
            return step.action
        locator, strategy = await self.resolve(page, step.locator, step.timeout_ms)
        if strategy == "coordinate":
            viewport = await page.evaluate("({width:innerWidth,height:innerHeight})")
            await page.mouse.click(viewport["width"] * step.locator.x_ratio, viewport["height"] * step.locator.y_ratio)
            return strategy
        text = " ".join(filter(None, [step.locator.name, step.locator.text, step.locator.label]))
        if is_forbidden_action(text) and step.action != "save_draft":
            raise RuntimeError(f"安全拦截：禁止执行正式发布操作“{text}”")
        value = data.get(step.variable) if step.variable else step.value
        if step.action in {"click", "save_draft"}:
            await locator.click(timeout=step.timeout_ms)
        elif step.action == "fill":
            await locator.fill(str(value or ""), timeout=step.timeout_ms)
        elif step.action == "select":
            try: await locator.select_option(str(value), timeout=step.timeout_ms)
            except Exception: await locator.click(timeout=step.timeout_ms)
        elif step.action == "scroll":
            if locator: await locator.scroll_into_view_if_needed(timeout=step.timeout_ms)
        elif step.action == "upload":
            files = await self._upload_files(locator, step.variable, value, data)
            if not files and step.critical: raise RuntimeError(f"缺少上传素材：{step.variable}")
            if files: await locator.set_input_files(files, timeout=step.timeout_ms)
        else:
            raise RuntimeError(f"不支持的回放动作：{step.action}")
        return strategy

    @staticmethod
    def _files(value: Any) -> list[str]:
        if value is None: return []
        values = value if isinstance(value, (list, tuple)) else [value]
        return [str(Path(item)) for item in values if Path(item).is_file()]

    async def _upload_files(self, locator, variable: str, value: Any, data: dict[str, Any]) -> list[str]:
        files = self._files(value)
        if not files:
            return []
        multiple = await locator.get_attribute("multiple")
        if multiple is not None:
            return files
        offsets = data.setdefault("_upload_offsets", {})
        index = int(offsets.get(variable, 0))
        offsets[variable] = index + 1
        return [files[index % len(files)]]

    async def validate_step(self, page, step: RecordingStep) -> None:
        kind, value = step.validation.kind, step.validation.value
        if kind in {"none", "upload_changed"}:
            if kind == "upload_changed": await asyncio.sleep(0.3)
            return
        if kind == "url_contains" and value not in page.url:
            raise RuntimeError(f"页面未进入预期地址：{value}")
        if kind in {"text_visible", "draft_saved"}:
            texts = [value] if value else ["保存成功", "草稿保存成功", "已保存至草稿"]
            for text in texts:
                try:
                    await page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=step.validation.timeout_ms)
                    return
                except Exception:
                    continue
            raise RuntimeError("未检测到草稿保存成功状态")
