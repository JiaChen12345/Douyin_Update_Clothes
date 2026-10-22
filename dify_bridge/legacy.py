"""Expose the existing fixed Douyin steps without giving an LLM the workflow."""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from playwright.async_api import async_playwright

from douyin_automation_dual_store_v49 import (
    AutomationRunner, DouyinDraftCreator, TaobaoAdapter, VvicAdapter, Zwd17Adapter,
    click_and_follow, click_text, collect_images, collect_videos, detect_challenge,
    extract_zip_safe, first_visible, load_config, verify_zip, wait_page, wait_page_stable,
)
from operation_recorder import is_forbidden_action
from popup_guard import PopupGuard, dismiss_popups
from popup_guard_demo import DEMO_HTML, find_edge
from .models import NeedsHuman


class BridgeCreator(DouyinDraftCreator):
    def __init__(self, runner):
        super().__init__(runner.config, runner.stop_event, runner.log)
        self.runner = runner

    async def wait_human(self, page, reason):
        self.runner.page = page
        raise NeedsHuman(reason + "；请在本机浏览器处理后恢复同一任务")

    async def require_visible(self, scope, texts, timeout_ms, exact=True):
        # An absent control is the only reason to call the vision workflow.
        for _ in range(min(16, max(1, timeout_ms // 500))):
            self.check_stop()
            await dismiss_popups(scope)
            for text in texts:
                candidates = scope.get_by_text(text, exact=exact).and_(scope.locator(":visible"))
                count = await candidates.count()
                if count == 1:
                    return candidates.first
                if count > 1:
                    raise NeedsHuman(f"出现多个“{text}”，请明确目标区域后继续")
            await asyncio.sleep(.5)
        return await self.runner.request_target(scope.page if hasattr(scope, "page") else scope, texts[0])

    async def optional_step(self, page, name, operation):
        for attempt in range(2):
            try:
                await operation()
                return True
            except (NeedsHuman, InterruptedError):
                raise
            except Exception as error:
                if attempt == 1:
                    self.warnings.append(f"{name}：{error}")
                    self.log(f"可选步骤失败：{name}；详情保存在本地日志")
                    return False
                await asyncio.sleep(.5)


class LegacyRunner:
    def __init__(self, root: Path, request, task_dir: Path, log, request_target):
        self.root, self.request, self.task_dir = root, request, task_dir
        self.log, self.request_target = log, request_target
        self.stop_event = threading.Event()
        self.config = None
        self.playwright = self.context = self.guard = self.page = None
        self.main = []
        self.videos = []
        self.creator = None
        self.knitwear = False

    async def start(self):
        self.config = load_config(self.root)
        self.playwright = await async_playwright().start()
        try:
            self.context = await AutomationRunner(self.root)._launch_context(
                self.playwright, self.config, self.config["browserProfileDir"])
        except Exception:
            await self.playwright.stop()
            self.playwright = None
            raise NeedsHuman("无法启动专用 Edge，请先关闭使用同一登录目录的旧自动化浏览器")
        self.guard = PopupGuard(self.log, self.stop_event.is_set)
        await self.guard.start(self.context)
        self.page = await self.context.new_page()
        await self.guard.install(self.page)
        self.creator = BridgeCreator(self)

    async def run_stage(self, stage: str):
        if self.page is None:
            await self.start()
        page, creator = self.page, self.creator
        creator.check_stop()
        if stage == "prepare":
            directory = Path(self.request.save_directory) / self.task_dir.name
            directory.mkdir(parents=True, exist_ok=True)
            archives = sorted(directory.glob("*.zip"))
            archive = None
            for candidate in archives:
                try:
                    verify_zip(candidate)
                    archive = candidate
                    break
                except RuntimeError:
                    continue
            if archive is None:
                adapters = [TaobaoAdapter(self.config), VvicAdapter(self.config), Zwd17Adapter(self.config)]
                adapter = next(item for item in adapters if item.matches(self.request.source_url))
                archive = await adapter.download(page, self.request.source_url, directory)
            extracted = directory / "images"
            extract_zip_safe(archive, extracted)
            self.main = (collect_images(extracted, "主图") or collect_images(extracted, "SKU") or
                         collect_images(extracted, "sku") or collect_images(extracted, "详情"))[:5]
            self.videos = collect_videos(extracted)[:1]
            if not self.main:
                raise NeedsHuman("素材包中没有可用商品图片，请在本地检查下载文件")
            return {"saved_directory": str(directory.resolve()), "images": [str(p) for p in self.main],
                    "image_count": len(self.main), "archive": str(archive)}
        if stage == "open_creation":
            await page.goto(self.config["douyinHome"], wait_until="domcontentloaded",
                            timeout=self.config["timeouts"]["navigationMs"])
            await wait_page_stable(page)
            if await detect_challenge(page):
                raise NeedsHuman("请在本机完成抖店登录或安全验证，再恢复任务")
            create = await first_visible(page, creator.s["createProductTexts"])
            if not create:
                menu = await creator.require_visible(page, creator.s["productMenuTexts"], 15000)
                page = await click_and_follow(page, menu)
                create = await creator.require_visible(page, creator.s["createProductTexts"], 15000)
            page = await click_and_follow(page, create)
            self.page = await wait_page(self.context, creator.s["localUploadTexts"],
                                        self.config["timeouts"]["navigationMs"])
        elif stage == "upload_main":
            await creator.upload(page, creator.s["localUploadTexts"], self.main)
            await creator.wait_uploads(page)
        elif stage == "generate":
            confirm = await first_visible(page, creator.s["confirmGenerateTexts"])
            if not confirm:
                button = await creator.require_visible(page, creator.s["nextGenerateTexts"], 60000)
                page = await click_and_follow(page, button)
                self.page = page
                confirm = await creator.require_visible(page, creator.s["confirmGenerateTexts"], 60000)
            self.page = await click_and_follow(page, confirm)
        elif stage == "wait_ai":
            self.page = await creator.wait_ai(page)
        elif stage == "upload_video":
            if self.videos:
                await creator.upload_main_video(page, self.videos[0])
            else:
                return {"skipped": True, "reason": "商品没有视频"}
        elif stage == "wash_label":
            await creator.upload_wash_label(page)
        elif stage == "attributes":
            if not await creator.detect_skip_age_category(page):
                await creator.optional_step(page, "适用年龄", lambda: creator.select_applicable_age(page))
        elif stage == "shipping":
            await creator.optional_step(page, "价格库存选项", lambda: creator.configure_shipping(page))
        elif stage == "stock":
            await creator.optional_step(page, "批量设置库存", lambda: creator.configure_batch_stock(page))
        elif stage == "sizes":
            self.knitwear = await creator.detect_knitwear_category(page)
            await creator.optional_step(page, "选择尺码大小", lambda: creator.select_size_specifications(page, self.knitwear))
        elif stage == "size_remarks":
            await creator.optional_step(page, "填写尺码备注", lambda: creator.fill_size_remarks(page, self.knitwear))
        elif stage == "size_table":
            await creator.optional_step(page, "填写尺码表", lambda: creator.fill_size_table(page, self.knitwear))
        elif stage == "save_draft":
            labels = creator.s["saveDraftTexts"]
            if any(is_forbidden_action(text) or "上架" in text for text in labels):
                raise NeedsHuman("保存草稿配置包含正式发布动作，已阻断")
            button = await creator.require_visible(page, labels, 30000)
            await button.click()
            await creator.require_visible(page, creator.s["successTexts"], 30000, exact=False)
            evidence = self.task_dir / "draft-saved.png"
            await page.screenshot(path=str(evidence), full_page=True)
            return {"draft_saved": True, "evidence": str(evidence), "warnings": creator.warnings}
        return {"stage": stage, "warnings": creator.warnings}

    async def close(self):
        self.stop_event.set()
        if self.guard:
            await self.guard.stop()
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()


class DemoRunner:
    """Same task protocol and popup handler, with a clean local browser only."""
    def __init__(self, root, request, task_dir, log, request_target, headless=False):
        self.task_dir, self.log, self.request_target = task_dir, log, request_target
        self.headless = headless
        self.playwright = self.browser = self.context = self.guard = self.page = None
        self.count = 0

    async def run_stage(self, stage):
        if self.page is None:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(executable_path=str(find_edge()), headless=self.headless)
            self.context = await self.browser.new_context(viewport={"width": 1200, "height": 820})
            self.guard = PopupGuard(self.log)
            await self.guard.start(self.context)
            self.page = await self.context.new_page()
            await self.guard.install(self.page)
            await self.page.set_content(DEMO_HTML)
        self.count += 1
        await self.page.evaluate("kind => showPopup(kind)", "bottom" if self.count % 2 else "cross")
        target = "保存草稿（模拟）" if stage == "save_draft" else "下一步"
        await click_text(self.page, [target], timeout_ms=5000)
        if await self.page.locator("#overlay").count():
            raise RuntimeError("本地演示弹窗没有关闭")
        result = {"simulation": True, "stage": stage, "popups_closed": self.guard.closed_count}
        if stage == "save_draft":
            if not (await self.page.locator("#status").inner_text()).startswith("演示完成"):
                raise RuntimeError("演示后置条件未满足")
            await self.page.screenshot(path=str(self.task_dir / "demo-completed.png"))
            result["draft_saved"] = False
            result["simulated_draft_saved"] = True
        return result

    async def close(self):
        if self.guard:
            await self.guard.stop()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
