"""Python implementation of the Douyin draft automation workflow.

The browser is deliberately visible and the final action is restricted to
"save draft".  Login and security challenges remain human-controlled.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlparse

from openpyxl import Workbook, load_workbook
from playwright.async_api import BrowserContext, Locator, Page, async_playwright

from operation_recorder import OperationRecorder
from operation_replayer import OperationReplayer
from recording_failures import FailureTracker
from recording_store import RecordingStore
from popup_guard import PopupGuard, dismiss_popups


ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
VERSION = "2026.08.19.49"
COLUMNS = ["网址", "站点", "状态", "抖店1状态", "抖店2状态", "当前步骤", "开始时间", "完成时间", "素材目录", "失败原因", "重试次数"]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
LogCallback = Callable[[str], None]
QUEUE_LOCK = threading.Lock()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def safe_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value))[:100]


def task_id(url: str, row_number: int) -> str:
    parsed = urlparse(url)
    leaf = next((part for part in reversed(parsed.path.split("/")) if part), parsed.hostname or "task")
    return f"{row_number:04d}-{safe_name(parsed.hostname or 'site')}-{safe_name(leaf)}"


def collect_images(root: Path, keyword: str) -> list[Path]:
    root = root.resolve()
    result: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        relative_parents = path.relative_to(root).parts[:-1]
        if any(keyword in part for part in relative_parents) or keyword in path.stem:
            result.append(path.resolve())
    return sorted(result, key=lambda item: natural_key(str(item.relative_to(root))))


def collect_videos(root: Path) -> list[Path]:
    root = root.resolve()
    result = [path.resolve() for path in root.rglob("*")
              if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS]
    return sorted(result, key=lambda item: natural_key(str(item.relative_to(root))))


def verify_zip(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("压缩包不存在或为空")
    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"不是有效 ZIP 文件：{path.name}")
    with zipfile.ZipFile(path) as archive:
        if not archive.infolist():
            raise RuntimeError("压缩包内没有文件")


def extract_zip_safe(path: Path, destination: Path) -> Path:
    verify_zip(path)
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            target = (root / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise RuntimeError(f"压缩包包含不安全路径：{member.filename}") from exc
        archive.extractall(root)
    return root


def load_config(project_root: Path = ROOT) -> dict:
    config_path = project_root / "config.json"
    example_path = project_root / "config.example.json"
    if not config_path.exists():
        shutil.copy2(example_path, config_path)
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    config.setdefault("browserProfileDir", config.get("chromeProfileDir", "./edge-profile"))
    config.setdefault("browserProfileDir2", "")
    config.setdefault("browserExecutable", config.get("chromeExecutable"))
    config.setdefault("browserExtensionDir", "")
    config.setdefault("autoClosePopups", True)
    mode = str(config.get("storeMode", "dual")).strip().lower()
    config["storeMode"] = mode if mode in {"single", "dual"} else "dual"
    config.setdefault("queuePath", str(project_root / "task_queue.json"))
    for key in ("excelPath", "queuePath", "workDir", "browserProfileDir", "browserProfileDir2", "fixedWashLabelImage"):
        if key not in config:
            continue
        if not str(config[key]).strip():
            continue
        config[key] = str((project_root / config[key]).resolve()) if not Path(config[key]).is_absolute() else str(Path(config[key]))
    if not str(config.get("browserProfileDir2") or "").strip():
        primary_profile = Path(config["browserProfileDir"])
        config["browserProfileDir2"] = str(primary_profile.with_name(primary_profile.name + "-shop2"))
    wash_image = Path(config.get("fixedWashLabelImage") or "")
    bundled_wash = project_root / "assets" / "wash-lable.jpg"
    if not wash_image.is_file() and bundled_wash.is_file():
        config["fixedWashLabelImage"] = str(bundled_wash.resolve())

    executable = Path(config.get("browserExecutable") or "")
    if not executable.is_file():
        candidates = [
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
        ]
        executable = next((candidate for candidate in candidates if candidate.is_file()), executable)
        config["browserExecutable"] = str(executable)
    if not executable.is_file():
        raise RuntimeError(f"找不到 Microsoft Edge：{executable or '(未配置)'}")

    extension_value = str(config.get("browserExtensionDir") or "").strip()
    if extension_value:
        extension = Path(extension_value)
        if not extension.is_absolute():
            extension = (project_root / extension).resolve()
        config["browserExtensionDir"] = str(extension)
    return config


def extension_manifest_dir(value: str | Path) -> Path:
    """Resolve an unpacked browser extension directory containing manifest.json."""
    extension = Path(value).expanduser().resolve()
    if (extension / "manifest.json").is_file():
        return extension
    if extension.is_dir():
        candidates = [item.parent for item in extension.glob("*/manifest.json") if item.is_file()]
        if candidates:
            return sorted(candidates, key=lambda item: natural_key(item.name))[-1]
    raise RuntimeError(f"扩展目录中找不到 manifest.json：{extension}")


class ExcelStore:
    def __init__(self, path: Path):
        self.path = path
        self.pending_path = path.with_name(f"{path.stem}.pending.xlsx")
        self.lock = threading.Lock()

    def load(self) -> None:
        source = self.path
        if self.pending_path.exists() and (not source.exists() or self.pending_path.stat().st_mtime > source.stat().st_mtime):
            source = self.pending_path
        if not source.exists():
            book = Workbook()
            sheet = book.active
            sheet.title = "任务"
            sheet.append(COLUMNS)
            self.book, self.sheet = book, sheet
            self.save()
            raise RuntimeError(f"已创建任务表 {self.path}。请在“网址”列填写网址后重新运行。")
        self.book = load_workbook(source)
        self.sheet = self.book.worksheets[0]
        headers = {str(cell.value or "").strip(): index for index, cell in enumerate(self.sheet[1], 1)}
        if "网址" not in headers:
            raise RuntimeError("Excel 第一行缺少“网址”列")
        for name in COLUMNS:
            if name not in headers:
                column = self.sheet.max_column + 1
                self.sheet.cell(1, column, name)
                headers[name] = column
        self.headers = headers

    def tasks(self) -> list[dict]:
        tasks = []
        for row_number in range(2, self.sheet.max_row + 1):
            cell = self.sheet.cell(row_number, self.headers["网址"])
            raw = cell.hyperlink.target if cell.hyperlink else cell.value
            url = str(raw or "").strip()
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RuntimeError(f"Excel 第 {row_number} 行不是有效 http/https 网址：{url}")
            status = str(self.sheet.cell(row_number, self.headers["状态"]).value or "待处理").strip()
            store1_status = str(self.sheet.cell(row_number, self.headers["抖店1状态"]).value or "").strip()
            store2_status = str(self.sheet.cell(row_number, self.headers["抖店2状态"]).value or "").strip()
            tasks.append({
                "row_number": row_number,
                "url": url,
                "status": status,
                "store1_status": store1_status,
                "store2_status": store2_status,
            })
        return tasks

    def delete_completed_rows(self, store_mode: str = "dual") -> int:
        """Delete rows after the selected store set has saved the draft successfully."""
        with self.lock:
            completed_rows = []
            for row_number in range(2, self.sheet.max_row + 1):
                store1_status = str(
                    self.sheet.cell(row_number, self.headers["抖店1状态"]).value or ""
                ).strip()
                store2_status = str(
                    self.sheet.cell(row_number, self.headers["抖店2状态"]).value or ""
                ).strip()
                complete = store1_status == "草稿成功"
                if store_mode == "dual":
                    complete = complete and store2_status == "草稿成功"
                if complete:
                    completed_rows.append(row_number)

            for row_number in reversed(completed_rows):
                self.sheet.delete_rows(row_number, 1)

            if completed_rows:
                self.save()
            return len(completed_rows)

    def update(self, row_number: int, values: dict) -> None:
        with self.lock:
            for name, value in values.items():
                self.sheet.cell(row_number, self.headers[name], value)
            self.save()

    def save(self) -> None:
        temporary = self.path.with_name(f"{self.path.name}.tmp.xlsx")
        self.book.save(temporary)

        # Only protect the replace of the main workbook.  In the old version the
        # pending-file cleanup lived inside the same try block; if that cleanup
        # raised PermissionError after the temporary file had already been moved,
        # the except block tried to move the now-missing temporary file again and
        # produced WinError 2.
        try:
            os.replace(temporary, self.path)
        except PermissionError:
            # Excel may currently have tasks.xlsx open.  Preserve the newest
            # workbook as tasks.pending.xlsx and let load() pick it up next time.
            os.replace(temporary, self.pending_path)
            return

        try:
            self.pending_path.unlink(missing_ok=True)
        except PermissionError:
            # The main workbook has already been saved successfully.  A stale
            # pending file can safely be left for a later run to clean up.
            pass


def normalize_url(value: str) -> str:
    parsed = urlparse(str(value).strip())
    return parsed._replace(fragment="").geturl().rstrip("/")


def append_urls(path: Path, urls: list[str], category: str = "tshirt", destination: str = "douyin") -> tuple[int, list[str]]:
    """Persist GUI-submitted URLs in the JSON task queue."""
    valid, invalid = [], []
    for raw in urls:
        url = normalize_url(raw)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            invalid.append(raw)
        elif url not in valid:
            valid.append(url)
    with QUEUE_LOCK:
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        existing = {normalize_url(str(item.get("url", ""))) for item in entries}
        added = 0
        for url in valid:
            if url in existing:
                continue
            entries.append({"url": url, "category": category, "destination": destination,
                            "status": "待处理", "current_step": "", "warnings": [],
                            "failure_reason": "", "store1": "", "store2": "",
                            "added_at": now_text(), "updated_at": now_text()})
            existing.add(url); added += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return added, invalid


def load_queue_metadata(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    entries = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for item in entries:
        url = normalize_url(str(item.get("url", "")))
        if not url:
            continue
        item.setdefault("category", "tshirt")
        item.setdefault("destination", "douyin")
        item.setdefault("warnings", [])
        result[url] = item
    return result


def update_queue_task(path: Path, url: str, values: dict) -> None:
    if not path.exists(): return
    with QUEUE_LOCK:
        entries = json.loads(path.read_text(encoding="utf-8"))
        normalized = normalize_url(url)
        for item in entries:
            if normalize_url(str(item.get("url", ""))) == normalized:
                item.update(values); item["updated_at"] = now_text(); break
        temporary = path.with_suffix(".tmp.json")
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)


def import_append_tasks(main_store: ExcelStore, append_path: Path) -> int:
    """Import pending JSON queue URLs into the legacy in-memory task workbook."""
    if not append_path.exists():
        return 0
    entries = json.loads(append_path.read_text(encoding="utf-8"))
    existing = {normalize_url(task["url"]) for task in main_store.tasks()}
    imported = 0
    for item in entries:
        url = normalize_url(str(item.get("url", "")))
        status = str(item.get("status", "待处理")).strip()
        if not url or status not in {"待处理", "处理中", "失败"} or url in existing:
            continue
        main_store.sheet.append([url, "", "待处理", "", "", "", "", "", "", "", 0])
        item["status"] = "已导入"; existing.add(url); imported += 1
    if imported:
        main_store.save(); append_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return imported


async def first_visible(scope, texts: Iterable[str], exact: bool = True) -> Locator | None:
    await dismiss_popups(scope)
    for text in texts:
        candidates = [scope.get_by_role("button", name=text, exact=exact), scope.get_by_role("link", name=text, exact=exact), scope.get_by_text(text, exact=exact)]
        for candidate in candidates:
            first = candidate.first
            try:
                if await first.is_visible():
                    return first
            except Exception:
                pass
        if exact:
            fuzzy = scope.get_by_text(text, exact=False).first
            try:
                if await fuzzy.is_visible():
                    return fuzzy
            except Exception:
                pass
    return None


async def click_text(scope, texts: list[str], timeout_ms: int = 30_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        found = await first_visible(scope, texts)
        if found:
            await found.click()
            return
        await asyncio.sleep(.5)
    raise RuntimeError(f"找不到按钮或链接：{' / '.join(texts)}")


async def first_visible_deep(page: Page, texts: Iterable[str]) -> Locator | None:
    """Find extension-injected controls in every page and child frame."""
    pages = [candidate for candidate in page.context.pages if not candidate.is_closed()]
    if page not in pages:
        pages.insert(0, page)
    for candidate_page in reversed(pages):
        for scope in [candidate_page, *candidate_page.frames]:
            found = await first_visible(scope, texts)
            if found:
                return found
    return None


async def click_text_deep(page: Page, texts: list[str], timeout_ms: int = 30_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        found = await first_visible_deep(page, texts)
        if found:
            await found.click(force=True)
            return
        await asyncio.sleep(.5)
    raise RuntimeError(f"找不到扩展按钮或链接：{' / '.join(texts)}（已检查主页面及 iframe）")


async def find_page(context: BrowserContext, texts: list[str]) -> Page | None:
    for page in reversed(context.pages):
        if not page.is_closed() and await first_visible(page, texts):
            return page
    return None


async def wait_page(context: BrowserContext, texts: list[str], timeout_ms: int) -> Page:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        page = await find_page(context, texts)
        if page:
            await page.bring_to_front()
            return page
        await asyncio.sleep(.5)
    raise RuntimeError(f"所有窗口中都找不到：{' / '.join(texts)}")


async def click_and_follow(page: Page, locator: Locator) -> Page:
    try:
        async with page.context.expect_page(timeout=3000) as event:
            await locator.click()
        result = await event.value
        await result.wait_for_load_state("domcontentloaded", timeout=30_000)
        return result
    except Exception:
        return page


async def detect_challenge(page: Page) -> bool:
    body = await page.locator("body").inner_text(timeout=3000) if not page.is_closed() else ""
    return bool(re.search(r"请登录|登录后|登录抖店|验证码|滑块|安全验证|风险验证|异常访问", body))


async def wait_page_stable(page: Page, timeout_ms: int = 20_000) -> None:
    """Wait for a dynamic product page to settle before locating controls."""
    await dismiss_popups(page)
    deadline = time.monotonic() + timeout_ms / 1000
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 10_000))
    except Exception:
        pass
    try:
        remaining = max(1_000, int((deadline - time.monotonic()) * 1000))
        await page.wait_for_load_state("networkidle", timeout=min(remaining, 8_000))
    except Exception:
        # Analytics and live widgets may keep networkidle from occurring.
        pass

    previous = None
    stable_samples = 0
    while time.monotonic() < deadline and stable_samples < 3:
        try:
            snapshot = await page.evaluate("""() => ({
                ready: document.readyState,
                height: document.documentElement.scrollHeight,
                textLength: (document.body?.innerText || '').length
            })""")
            if snapshot == previous and snapshot.get("ready") == "complete":
                stable_samples += 1
            else:
                stable_samples = 0
            previous = snapshot
        except Exception:
            stable_samples = 0
        await asyncio.sleep(.5)


class Adapter:
    name = ""
    domains: tuple[str, ...] = ()

    def __init__(self, config: dict):
        self.config = config

    def matches(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        return any(host == domain or host.endswith("." + domain) for domain in self.domains)

    async def save_download(self, download, directory: Path, fallback: str) -> Path:
        name = download.suggested_filename or fallback
        if "." not in Path(name).name:
            name += Path(fallback).suffix or ".zip"
        path = directory / safe_name(name)
        await download.save_as(path)
        if path.suffix.lower() == ".zip":
            verify_zip(path)
        return path

    async def save_zip_download(self, download, directory: Path, fallback: str) -> Path:
        """Save a download and require ZIP content, regardless of filename."""
        path = await self.save_download(download, directory, fallback)
        if path.suffix.lower() != ".zip":
            try:
                verify_zip(path)
            except Exception as error:
                raise RuntimeError(f"下载文件不是有效 ZIP：{path.name}") from error
            renamed = path.with_suffix(".zip")
            path.replace(renamed)
            path = renamed
        return path


class VvicAdapter(Adapter):
    name, domains = "搜款网", ("vvic.com",)

    async def download(self, page: Page, url: str, directory: Path) -> Path:
        await page.goto(url, wait_until="domcontentloaded", timeout=self.config["timeouts"]["navigationMs"])
        await wait_page_stable(page)
        combined = await first_visible(page, ["图片/视频下载", "图片／视频下载"])
        if combined:
            # Newer VVIC page: open the resource chooser, keep its default
            # selections, then confirm the download.
            await combined.click(force=True)
            confirm = await self._wait_download_confirm(page, 30_000)
            async with page.expect_download(timeout=self.config["timeouts"]["downloadMs"]) as event:
                await confirm.click(force=True)
            first = await self.save_download(await event.value, directory, "搜款网素材.zip")
            if first.suffix.lower() == ".zip":
                return first
            # A combined selection may emit the video first and the image ZIP
            # second. Keep the video file and capture the following ZIP.
            async with page.expect_download(timeout=self.config["timeouts"]["downloadMs"]) as next_event:
                pass
            return await self.save_zip_download(await next_event.value, directory, "搜款网素材.zip")

        simple = await first_visible(page, ["图片下载"])
        if not simple:
            raise RuntimeError("搜款网页面找不到“图片下载”或“图片/视频下载”入口")

        # v48-compatible simple page: one click directly produces a browser
        # download. Keep the full configured timeout; expect_download returns
        # immediately when the event occurs. save_download accepts an
        # extensionless UUID filename, appends .zip, and verifies ZIP content.
        async with page.expect_download(timeout=self.config["timeouts"]["downloadMs"]) as event:
            await simple.click(force=True)
        return await self.save_zip_download(await event.value, directory, "搜款网素材.zip")

    async def _wait_download_confirm(self, page: Page, timeout_ms: int) -> Locator:
        """Find the confirmation button even when VVIC renders a custom popup/iframe."""
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            found = await first_visible_deep(page, ["立即下载", "开始下载"])
            if found:
                return found
            # Some releases render the modal action as a plain div instead of
            # a button/role=button. Scan visible controls by their text as a
            # fallback, while excluding the product-page entry button.
            for candidate_page in reversed([item for item in page.context.pages if not item.is_closed()]):
                for scope in [candidate_page, *candidate_page.frames]:
                    candidates = scope.locator("button:visible, [role='button']:visible, a:visible, div:visible")
                    for index in range(min(await candidates.count(), 300)):
                        candidate = candidates.nth(index)
                        try:
                            text = re.sub(r"\s+", "", (await candidate.inner_text()).strip())
                            if text in {"立即下载", "开始下载", "确认下载"}:
                                return candidate
                        except Exception:
                            continue
            await asyncio.sleep(.3)
        raise RuntimeError("点击下载入口后未捕获直接下载，也未发现二次确认按钮")

class Zwd17Adapter(Adapter):
    name, domains = "17网", ("17zwd.com",)

    async def download(self, page: Page, url: str, directory: Path) -> Path:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.config["timeouts"]["navigationMs"],
        )
        await wait_page_stable(page)

        await click_text(page, ["下载图片/视频", "下载视频/图片"])

        dialog = page.get_by_role("dialog").last
        scope = dialog if await dialog.is_visible() else page

        card_labels = scope.get_by_text(re.compile(r"下载$"), exact=False)
        card_clicked = 0

        for index in range(await card_labels.count()):
            card = card_labels.nth(index)
            text = (await card.inner_text()).strip()

            if text in {"立即下载", "下载"} or not await card.is_visible():
                continue

            try:
                if await card.is_enabled():
                    await card.click(force=True)
                    card_clicked += 1
            except Exception:
                continue

        if card_clicked:
            await click_text(scope, ["立即下载"])

            progress_deadline = (
                time.monotonic()
                + self.config["timeouts"]["downloadMs"] / 1000
            )

            while time.monotonic() < progress_deadline:
                progress_scope = page.get_by_role("dialog").last
                target_scope = (
                    progress_scope
                    if await progress_scope.is_visible()
                    else page
                )

                body = await target_scope.inner_text()
                download_button = await first_visible(
                    target_scope,
                    ["下载"],
                )

                if (
                    re.search(r"(?:100\s*%|100%)", body)
                    and download_button
                    and await download_button.is_enabled()
                ):
                    async with page.expect_download(
                        timeout=self.config["timeouts"]["downloadMs"]
                    ) as event:
                        await download_button.click(force=True)

                    return await self.save_zip_download(
                        await event.value,
                        directory,
                        "17网素材.zip",
                    )

                await asyncio.sleep(1)

            raise RuntimeError("素材打包未达到100%，等待下载超时")

        # =========================================================
        # checkbox 下载模式
        # =========================================================

        # 给弹窗中的素材选项足够时间完成动态渲染
        await asyncio.sleep(3)

        # 两轮扫描。
        # 每一轮都重新获取 checkbox，
        # 防止第一轮操作后 Vue / React 更新 DOM。
        for round_index in range(2):

            boxes = scope.locator('input[type="checkbox"]')
            count = await boxes.count()

            for index in range(count):
                box = boxes.nth(index)

                try:
                    # 只处理：
                    # 1. 当前可见
                    # 2. 当前可选择
                    # 3. 当前未勾选
                    if not await box.is_visible():
                        continue

                    if not await box.is_enabled():
                        continue

                    if await box.is_checked():
                        continue

                    # 第一种方式：直接使用 Playwright check
                    try:
                        await box.check(force=True)

                    except Exception:
                        # 如果网站自定义 checkbox 导致 check() 失败，
                        # 尝试点击对应 label 或父元素。
                        label = box.locator(
                            'xpath=ancestor::label[1]'
                        )

                        target = (
                            label
                            if await label.count()
                            else box.locator('xpath=..')
                        )

                        await target.click(force=True)

                    # 给前端一点时间更新 checkbox 状态
                    await asyncio.sleep(0.2)

                    # 第一次操作后仍然没有选中，
                    # 再点击一次父节点作为兜底。
                    if not await box.is_checked():
                        try:
                            await box.locator(
                                'xpath=..'
                            ).click(force=True)
                        except Exception:
                            pass

                        await asyncio.sleep(0.2)

                except Exception:
                    # 某个 checkbox 在操作过程中如果被 DOM 重绘，
                    # 不让整个流程失败。
                    # 第二轮会重新扫描新的 DOM。
                    continue

            # 第一轮结束后等待页面状态更新，
            # 第二轮重新扫描所有 checkbox。
            if round_index == 0:
                await asyncio.sleep(1)

        # =========================================================
        # 最终校验
        # =========================================================

        # 再重新获取一次 checkbox，
        # 不继续使用之前的 locator/count 状态。
        boxes = scope.locator('input[type="checkbox"]')

        unchecked = []

        for index in range(await boxes.count()):
            box = boxes.nth(index)

            try:
                if (
                    await box.is_visible()
                    and await box.is_enabled()
                    and not await box.is_checked()
                ):
                    unchecked.append(index + 1)

            except Exception:
                # 如果恰好发生 DOM 重绘，
                # 忽略该节点。
                continue

        # 如果两轮之后仍然存在“可选但未勾选”的素材，
        # 禁止继续下载，避免产生不完整素材包。
        if unchecked:
            raise RuntimeError(
                f"17网素材选项未全部勾选，"
                f"仍有可选但未勾选的复选框：{unchecked}"
            )

        # 所有当前可选 checkbox 已确认勾选
        await click_text(scope, ["立即下载"])

        deadline = (
            time.monotonic()
            + self.config["timeouts"]["downloadMs"] / 1000
        )

        while time.monotonic() < deadline:
            # 下载过程中弹窗可能发生变化，
            # 所以重新获取当前 dialog。
            current_dialog = page.get_by_role("dialog").last

            target_scope = (
                current_dialog
                if await current_dialog.is_visible()
                else page
            )

            body = await target_scope.inner_text()

            button = await first_visible(
                target_scope,
                ["下载"],
            )

            if (
                button
                and await button.is_enabled()
                and not re.search(
                    r"素材打包中|正在打包",
                    body,
                )
            ):
                async with page.expect_download(
                    timeout=self.config["timeouts"]["downloadMs"]
                ) as event:
                    await button.click(force=True)

                return await self.save_zip_download(
                    await event.value,
                    directory,
                    "17网素材.zip",
                )

            await asyncio.sleep(1)

        raise RuntimeError("等待素材打包超时")


class TaobaoAdapter(Adapter):
    name, domains = "淘宝", ("taobao.com", "tmall.com")

    async def download(self, page: Page, url: str, directory: Path) -> Path:
        await page.goto(url, wait_until="domcontentloaded", timeout=self.config["timeouts"]["navigationMs"])
        await wait_page_stable(page)
        # “手机端主图视频SKU” is injected by the 至尊宝 extension, not by
        # Taobao itself.  Wait for the extension's service worker and content
        # script before trying to click it; injection can lag behind DOM load.
        extension_deadline = time.monotonic() + 90
        extension_worker_seen = False
        while time.monotonic() < extension_deadline:
            extension_worker_seen = extension_worker_seen or any(
                worker.url.startswith("chrome-extension://")
                for worker in page.context.service_workers
            )
            if await first_visible_deep(page, ["手机端主图视频SKU"]):
                break
            await asyncio.sleep(.5)
        else:
            if not extension_worker_seen:
                raise RuntimeError("未检测到至尊宝浏览器扩展：请在专用 Edge 中安装并启用扩展后重试")
            raise RuntimeError("至尊宝扩展已加载，但淘宝页未注入“手机端主图视频SKU”按钮；请刷新淘宝商品页后重试")
        await click_text_deep(page, ["手机端主图视频SKU"], 5_000)
        await asyncio.sleep(10)

        # The browser extension opens “资源一键下载”. Its dropdown menu is
        # rendered in a portal and can take a moment to mount. Re-open the
        # menu when the first click only focused the button.
        menu_ready = False
        for attempt in range(3):
            batch = None
            batch_deadline = time.monotonic() + 60
            while time.monotonic() < batch_deadline:
                batch = await first_visible_deep(page, ["批量下载"])
                if batch:
                    break
                await asyncio.sleep(.5)
            if not batch:
                raise RuntimeError("已进入资源下载窗口，但等待60秒仍找不到“批量下载”按钮")
            await batch.hover(force=True)
            await batch.click(force=True)
            for _ in range(20):
                if await first_visible_deep(page, ["单文件夹"]):
                    menu_ready = True
                    break
                await asyncio.sleep(.25)
            if menu_ready:
                break
            await page.keyboard.press("Escape")
            await asyncio.sleep(.5)
        if not menu_ready:
            raise RuntimeError("已点击“批量下载”，但下拉菜单未展开；请在淘宝页手动展开后重试")
        async with page.expect_download(timeout=self.config["timeouts"]["downloadMs"]) as event:
            await click_text_deep(page, ["单文件夹"], 30_000)
        return await self.save_zip_download(await event.value, directory, "淘宝素材.zip")


class DouyinDraftCreator:
    def __init__(self, config: dict, stop_event: threading.Event, log: LogCallback):
        self.config, self.stop_event, self.log = config, stop_event, log
        self.warnings: list[str] = []
        self.s = config["selectors"]["douyin"]

    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise InterruptedError("用户已停止任务")

    async def wait_human(self, page: Page, reason: str) -> Page:
        self.log(f"等待人工：{reason}。请在浏览器完成登录/验证，程序会自动继续。")
        deadline = time.monotonic() + 1800
        login_clicked = False
        while time.monotonic() < deadline:
            self.check_stop()
            for candidate in reversed(page.context.pages):
                if "jinritemai.com" not in candidate.url:
                    continue
                if await first_visible(candidate, self.s["createProductTexts"]):
                    return candidate
                if not await detect_challenge(candidate) and await first_visible(candidate, self.s["productMenuTexts"]):
                    return candidate
                if not login_clicked:
                    login = await first_visible(candidate, ["登录抖店"])
                    if login:
                        page = await click_and_follow(candidate, login)
                        login_clicked = True
                        self.log("已自动点击“登录抖店”；请完成人工扫码或安全验证")
                        break
            await asyncio.sleep(2)
        raise RuntimeError("等待人工登录/验证超时")

    async def create(self, page: Page, main: list[Path], videos: list[Path], checkpoint: Callable[[str], None]) -> Page:
        checkpoint("打开抖店商品创建")
        await page.goto(self.config["douyinHome"], wait_until="domcontentloaded", timeout=self.config["timeouts"]["navigationMs"])
        await wait_page_stable(page)
        if await detect_challenge(page):
            page = await self.wait_human(page, "登录已失效或页面要求安全验证")
        page = await self.open_creation(page)
        checkpoint("上传主图")
        page = await wait_page(page.context, self.s["localUploadTexts"], self.config["timeouts"]["navigationMs"])
        await self.upload(page, self.s["localUploadTexts"], main)
        await self.wait_uploads(page)
        button = await self.require_visible(page, self.s["nextGenerateTexts"], 60_000)
        page = await click_and_follow(page, button)
        page = await wait_page(page.context, self.s["confirmGenerateTexts"], self.config["timeouts"]["navigationMs"])
        checkpoint("确认AI生成")
        button = await self.require_visible(page, self.s["confirmGenerateTexts"], 60_000)
        page = await click_and_follow(page, button)
        page = await self.wait_ai(page)
        if videos:
            checkpoint("上传主图视频")
            await self.upload_main_video(page, videos[0])
        checkpoint("上传水洗标/吊牌图")
        await self.upload_wash_label(page)
        skip_age = await self.detect_skip_age_category(page)
        if skip_age:
            checkpoint("背心/吊带类目跳过适用年龄")
        else:
            checkpoint("选择适用年龄")
            await self.optional_step(page, "适用年龄", lambda: self.select_applicable_age(page))
        checkpoint("填写价格库存选项")
        await self.optional_step(page, "价格库存选项", lambda: self.configure_shipping(page))
        checkpoint("批量设置库存")
        await self.optional_step(page, "批量设置库存", lambda: self.configure_batch_stock(page))
        knitwear = await self.detect_knitwear_category(page)
        checkpoint("选择尺码大小")
        await self.optional_step(page, "选择尺码大小", lambda: self.select_size_specifications(page, knitwear=knitwear))
        checkpoint("填写尺码备注")
        await self.optional_step(page, "填写尺码备注", lambda: self.fill_size_remarks(page, knitwear=knitwear))
        checkpoint("填写尺码表")
        await self.optional_step(page, "填写尺码表", lambda: self.fill_size_table(page, knitwear=knitwear))
        checkpoint("保存草稿")
        if any(re.search(r"发布|提交审核|上架", text) for text in self.s["saveDraftTexts"]):
            raise RuntimeError("安全阻断：保存操作配置包含发布类文字")
        await click_text(page, self.s["saveDraftTexts"])
        await self.require_visible(page, self.s["successTexts"], 30_000, exact=False)
        return page

    async def optional_step(self, page: Page, name: str, operation) -> bool:
        """Try a non-critical form step twice, then continue with a warning."""
        last_error = None
        for attempt in range(2):
            try:
                await operation()
                return True
            except InterruptedError:
                raise
            except Exception as error:
                last_error = error
                self.log(f"{name}第 {attempt + 1}/2 次失败：{error}")
                if attempt == 0:
                    await asyncio.sleep(.8)
        self.log(f"警告：{name}连续两次失败，继续后续步骤：{last_error}")
        self.warnings.append(f"{name}：{last_error}")
        return False

    async def require_visible(self, scope, texts: list[str], timeout_ms: int, exact: bool = True) -> Locator:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            self.check_stop()
            found = await first_visible(scope, texts, exact=exact)
            if found:
                return found
            await asyncio.sleep(.5)
        raise RuntimeError(f"找不到：{' / '.join(texts)}")

    async def open_creation(self, page: Page) -> Page:
        deadline = time.monotonic() + self.config["timeouts"]["navigationMs"] / 1000
        while time.monotonic() < deadline:
            self.check_stop()
            create = await first_visible(page, self.s["createProductTexts"])
            if create:
                page = await click_and_follow(page, create)
                return await wait_page(page.context, self.s["localUploadTexts"], self.config["timeouts"]["navigationMs"])
            menu = await first_visible(page, self.s["productMenuTexts"])
            if menu:
                page = await click_and_follow(page, menu)
            await asyncio.sleep(1)
        raise RuntimeError("抖店首页已打开，但找不到“商品创建”")

    async def upload(self, scope, texts: list[str], files: list[Path]) -> None:
        button = await self.require_visible(scope, texts, 30_000)
        page = scope.page if isinstance(scope, Locator) else scope
        self.log(f"准备上传 {len(files)} 张图片：{page.url}")

        # Some releases keep a hidden file input mounted before the button is
        # clicked. Using it directly is more reliable than waiting for a chooser.
        input_selector = 'input[type="file"][multiple]' if len(files) > 1 else 'input[type="file"]'
        direct = scope.locator(input_selector)
        if await direct.count():
            await direct.last.set_input_files([str(path) for path in files])
            return
        try:
            async with page.expect_file_chooser(timeout=15_000) as event:
                await button.click()
            chooser = await event.value
            if len(files) == 1 or chooser.is_multiple():
                await chooser.set_files([str(path) for path in files])
            else:
                # Some uploaders expose a single-file chooser repeatedly.
                await chooser.set_files(str(files[0]))
                for path in files[1:]:
                    next_button = await self.require_visible(scope, texts, 15_000)
                    async with page.expect_file_chooser(timeout=15_000) as next_event:
                        await next_button.click()
                    next_chooser = await next_event.value
                    await next_chooser.set_files(str(path))
            return
        except Exception:
            pass

        # The upload control may open a popup, navigate, or mount its input in
        # an iframe. Search the complete persistent browser context.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            for candidate in reversed(page.context.pages):
                if candidate.is_closed():
                    continue
                for frame in candidate.frames:
                    inputs = frame.locator(input_selector)
                    if await inputs.count():
                        await inputs.last.set_input_files([str(path) for path in files])
                        await candidate.bring_to_front()
                        return
            await asyncio.sleep(.5)
        windows = []
        for candidate in page.context.pages:
            if not candidate.is_closed():
                windows.append({"url": candidate.url, "title": await candidate.title()})
        raise RuntimeError(
            "上传按钮未打开文件选择框，所有窗口和 iframe 中也未找到文件输入框；"
            f"窗口={windows}"
        )

    async def wait_uploads(self, page: Page) -> None:
        deadline = time.monotonic() + self.config["timeouts"]["uploadMs"] / 1000
        while time.monotonic() < deadline:
            self.check_stop()
            body = await page.locator("body").inner_text()
            if re.search(r"上传失败|重新上传", body):
                raise RuntimeError("页面报告图片上传失败")
            if not re.search(r"上传中|正在上传|处理中", body):
                await asyncio.sleep(1.5)
                return
            await asyncio.sleep(1)
        raise RuntimeError("等待图片上传完成超时")

    async def wait_ai(self, page: Page) -> Page:
        deadline = time.monotonic() + self.config["timeouts"]["aiGenerationMs"] / 1000
        while time.monotonic() < deadline:
            self.check_stop()
            if await detect_challenge(page):
                page = await self.wait_human(page, "AI 生成期间出现安全验证")
            if await first_visible(page, self.s["detailSectionTexts"]):
                return page
            body = await page.locator("body").inner_text()
            if re.search(r"生成失败|生成异常", body):
                raise RuntimeError("官方 AI 生成失败")
            await asyncio.sleep(1.5)
        raise RuntimeError("等待官方 AI 生成超时")

    async def upload_wash_label(self, page: Page) -> None:
        image_value = self.config.get("fixedWashLabelImage")
        if not image_value:
            raise RuntimeError("config.json 未配置 fixedWashLabelImage")
        image = Path(image_value)
        if not image.is_file():
            raise RuntimeError(f"固定水洗标/吊牌图片不存在：{image}")

        # Use an exact field label. The AI assistant sidebar also contains the
        # words “水洗标/吊牌图” and must never be treated as the upload field.
        title = None
        section = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and section is None:
            for label_text in self.s.get("washLabelTexts", ["水洗标/吊牌图"]):
                labels = page.get_by_text(label_text, exact=True)
                for index in range(await labels.count()):
                    candidate = labels.nth(index)
                    if not await candidate.is_visible():
                        continue
                    candidate_section = candidate.locator(
                        'xpath=ancestor::*[.//*[normalize-space(.)="上传图片"]][1]'
                    )
                    if not await candidate_section.count():
                        continue
                    candidate_upload = candidate_section.get_by_text("上传图片", exact=True).first
                    if await candidate_upload.is_visible():
                        title, section = candidate, candidate_section
                        break
                if section is not None:
                    break
            if section is None:
                await asyncio.sleep(.5)
        if title is None or section is None:
            raise RuntimeError("找不到表单中精确的“水洗标/吊牌图 → 上传图片”字段")
        upload_area = await first_visible(section, ["上传图片"])
        if not upload_area:
            raise RuntimeError("找到“水洗标/吊牌图”区域，但找不到“＋ 上传图片”点击区域")
        await upload_area.scroll_into_view_if_needed()
        before_images = await section.locator("img:visible").count()
        self.log(f"点击水洗标“上传图片”，准备选择本地文件：{image.resolve()}")
        try:
            async with page.expect_file_chooser(timeout=10_000) as event:
                await upload_area.click(force=True)
            chooser = await event.value
            await chooser.set_files(str(image.resolve()))
        except Exception as chooser_error:
            # The click is mandatory; some versions do not emit filechooser
            # but mount a local file input immediately after that click.
            inputs = section.locator('input[type="file"]')
            if not await inputs.count():
                raise RuntimeError(
                    "已点击水洗标“上传图片”，但未弹出本地文件选择器，"
                    f"也未找到文件输入框：{chooser_error}"
                ) from chooser_error
            await inputs.last.set_input_files(str(image.resolve()))
        await self.wait_uploads(page)

        # Verify only inside this exact form field. Never use images/counters
        # from the AI assistant or another form section as success evidence.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            after_images = await section.locator("img:visible").count()
            if after_images > before_images:
                self.log(f"固定水洗标/吊牌图上传成功：{image.name}")
                return
            await asyncio.sleep(.5)
        raise RuntimeError(
            "已在正确的水洗标字段选择本地图片，但该字段的缩略图数量没有增加，平台未确认上传"
        )

    async def upload_main_video(self, page: Page, video: Path) -> None:
        if not video.is_file():
            raise RuntimeError(f"主图视频不存在：{video}")

        all_inputs = page.locator('input[type="file"]')
        for index in range(await all_inputs.count()):
            candidate = all_inputs.nth(index)
            accept = (await candidate.get_attribute("accept") or "").lower()
            if "video" in accept or any(ext in accept for ext in VIDEO_EXTENSIONS):
                self.log(f"找到视频上传字段，准备选择本地视频：{video.resolve()}")
                await candidate.set_input_files(str(video.resolve()))
                await self.wait_uploads(page)
                self.log(f"主图视频上传成功：{video.name}")
                return

        section = None
        upload_area = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and section is None:
            labels = page.get_by_text("主图视频", exact=True)
            for index in range(await labels.count()):
                candidate = labels.nth(index)
                if not await candidate.is_visible():
                    continue
                candidate_section = candidate.locator(
                    'xpath=ancestor::*[.//input[@type="file"] '
                    'or .//*[normalize-space(.)="主图视频"]][1]'
                )
                if not await candidate_section.count():
                    continue
                choices = candidate_section.get_by_text("主图视频", exact=True)
                if await choices.count() >= 1:
                    possible = choices.last
                    if await possible.is_visible():
                        section, upload_area = candidate_section, possible
                        break
            if section is None:
                await asyncio.sleep(.5)
        if section is None or upload_area is None:
            raise RuntimeError("找不到表单中精确的“主图视频 → ＋ 主图视频”上传字段")

        await upload_area.scroll_into_view_if_needed()
        before_media = (await section.locator("video:visible").count()
                        + await section.locator("img:visible").count())
        self.log(f"点击“主图视频”上传区域，准备选择本地视频：{video.resolve()}")
        try:
            async with page.expect_file_chooser(timeout=10_000) as event:
                await upload_area.click(force=True)
            chooser = await event.value
            await chooser.set_files(str(video.resolve()))
        except Exception as chooser_error:
            inputs = None
            for candidate_page in reversed([p for p in page.context.pages if not p.is_closed()]):
                for frame in candidate_page.frames:
                    frame_inputs = frame.locator('input[type="file"]')
                    for input_index in range(await frame_inputs.count()):
                        candidate_input = frame_inputs.nth(input_index)
                        accept = (await candidate_input.get_attribute("accept") or "").lower()
                        if "video" in accept or any(ext in accept for ext in VIDEO_EXTENSIONS):
                            inputs = candidate_input
                            break
                    if inputs is not None:
                        break
                if inputs is not None:
                    break
            if inputs is None:
                raise RuntimeError(
                    "已点击主图视频上传区域，但未弹出本地文件选择器，"
                    f"也未找到文件输入框：{chooser_error}"
                ) from chooser_error
            await inputs.set_input_files(str(video.resolve()))
        await self.wait_uploads(page)
        self.log(f"主图视频上传成功：{video.name}")

    async def choose_labeled_option(self, page: Page, text: str) -> None:
        """Select a radio/checkbox by its visible label without toggling it off."""
        label = page.get_by_text(text, exact=True).last
        if not await label.is_visible():
            label = page.get_by_text(text, exact=False).last
        if not await label.is_visible():
            raise RuntimeError(f"找不到选项：{text}")

        checkable = label.locator(
            'xpath=(ancestor-or-self::label[1]//input[@type="radio" or @type="checkbox"] '
            '| ancestor::*[1]//input[@type="radio" or @type="checkbox"])[1]'
        )
        if await checkable.count():
            try:
                if await checkable.is_checked():
                    return
            except Exception:
                pass
        await label.scroll_into_view_if_needed()
        await label.click(force=True)
        await asyncio.sleep(.25)

    async def select_applicable_age(self, page: Page) -> None:
        label = await self.require_visible(page, ["适用年龄"], 30_000)
        await label.scroll_into_view_if_needed()
        field = label.locator(
            'xpath=ancestor::*[(count(.//*[@role="combobox"])=1 '
            'or count(.//input[@placeholder="请选择"])=1)][1]'
        )
        control = field.get_by_role("combobox").first
        if not await control.count():
            control = field.locator('input[placeholder="请选择"]').first
        if not await control.count():
            control = label.locator('xpath=following::*[normalize-space(.)="请选择"][1]')
        if not await control.count():
            raise RuntimeError("找到“适用年龄”，但找不到对应下拉列表")
        if not await field.count():
            field = control.locator("xpath=ancestor::*[1]")
        await control.click(force=True)
        target = "18岁以上"
        option = await first_visible(page, [target])
        if not option:
            create = await first_visible(page, ["创建类型", "新建", "创建"])
            if not create:
                raise RuntimeError("适用年龄中没有“18岁以上”，且找不到新建年龄入口")
            await create.click(force=True)
            dialog = page.get_by_role("dialog").last
            scope = dialog if await dialog.is_visible() else page
            editor = scope.locator('input:visible').last
            if not await editor.count():
                raise RuntimeError("已打开新建适用年龄，但找不到输入框")
            await editor.fill(target)
            confirm = await first_visible(scope, ["确定", "保存", "完成"])
            if confirm:
                await confirm.click(force=True)
            else:
                editor_box = await editor.bounding_box()
                if not editor_box:
                    raise RuntimeError("新建适用年龄输入框无法定位绿色确认按钮")
                await page.mouse.click(
                    editor_box["x"] + editor_box["width"] + 18,
                    editor_box["y"] + editor_box["height"] / 2,
                )
            await asyncio.sleep(.5)
            option = await self.require_visible(page, [target], 15_000)
        await option.click(force=True)
        await asyncio.sleep(.3)
        field_text = await field.inner_text()
        input_value = ""
        if await control.evaluate("element => element.tagName === 'INPUT'"):
            input_value = await control.input_value()
        if target not in field_text and target not in input_value:
            raise RuntimeError(f"已点击适用年龄“{target}”，但页面未显示选中值")
        self.log(f"其他属性已选择：适用年龄 {target}")

    async def detect_skip_age_category(self, page: Page) -> bool:
        """Return true for categories where the platform age field must remain untouched."""
        patterns = ("背心/吊带", "背心／吊带")
        for label_text in ("商品类目", "商品类别", "类目"):
            labels = page.get_by_text(label_text, exact=True)
            for index in range(await labels.count()):
                label = labels.nth(index)
                if not await label.is_visible():
                    continue
                scope = label.locator("xpath=ancestor::*[1]")
                text = await scope.inner_text()
                normalized = re.sub(r"\s+", "", text)
                if any(pattern in normalized for pattern in patterns):
                    self.log(f"识别商品类目：{text.strip()}；跳过适用年龄设置")
                    return True
        body = await page.locator("body").inner_text()
        match = re.search(r"(?:商品类目|商品类别|类目)[\s\S]{0,120}", body)
        if match and any(pattern in re.sub(r"\s+", "", match.group(0)) for pattern in patterns):
            self.log("识别商品类目为背心/吊带；跳过适用年龄设置")
            return True
        return False

    async def configure_shipping(self, page: Page) -> None:
        await self.choose_labeled_option(page, "现货预售混合")
        await self.choose_labeled_option(page, "48小时")
        await self.choose_labeled_option(page, "7天内")
        await self.choose_labeled_option(page, "付款减库存")
        self.log("价格库存选项已填写：现货预售混合 / 48小时 / 7天内 / 付款减库存")

    async def configure_batch_stock(self, page: Page) -> None:
        batch = None
        stock_scope = None
        stock_page = page
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and batch is None:
            for candidate in reversed(page.context.pages):
                if candidate.is_closed():
                    continue
                for frame in candidate.frames:
                    found = await first_visible(frame, ["批量设置"])
                    if found:
                        batch, stock_scope, stock_page = found, frame, candidate
                        break
                if batch is not None:
                    break
            if batch is None:
                await asyncio.sleep(.5)
        if batch is None or stock_scope is None:
            windows = [{"url": item.url, "title": await item.title()}
                       for item in page.context.pages if not item.is_closed()]
            raise RuntimeError(f"所有窗口和 iframe 中都找不到“批量设置”；窗口={windows}")
        await stock_page.bring_to_front()
        await batch.scroll_into_view_if_needed()
        spot = stock_scope.get_by_placeholder("现货库存", exact=True).last
        presale = stock_scope.get_by_placeholder("预售库存", exact=True).last
        if not await spot.is_visible():
            raise RuntimeError("价格与库存批量设置栏中找不到“现货库存”输入框")
        if not await presale.is_visible():
            raise RuntimeError("价格与库存批量设置栏中找不到“预售库存”输入框")
        await self.fill_form_input(spot, "0", "现货库存")
        await self.fill_form_input(presale, "10000", "预售库存")
        await batch.click(force=True)
        await asyncio.sleep(.5)
        self.log("库存已批量设置：现货库存 0 / 预售库存 10000")

    async def fill_size_table(self, page: Page, knitwear: bool = False) -> None:
        values = ({"均码": ("150", "175", "90", "125")} if knitwear else {
            "S": ("150", "175", "80", "95"),
            "M": ("150", "175", "95", "110"),
            "L": ("150", "175", "110", "120"),
            "XL": ("150", "175", "120", "135"),
        })
        title = await self.require_visible(page, ["尺码表"], 30_000)
        await title.scroll_into_view_if_needed()
        section = title.locator(
            'xpath=ancestor::*[.//table or .//*[normalize-space(.)="添加行"]][1]'
        )

        # This must happen before editing any height/weight values; otherwise
        # the form exposes only one value field instead of min/max fields.
        await self.ensure_size_interval_modes(section)
        await asyncio.sleep(.5)

        if knitwear:
            # In the knitwear/one-size UI, "均码" may be rendered as plain text
            # instead of the first disabled input.  The previous generic parser
            # therefore read the first height value (for example 150/166) as the
            # size name and tried to delete that row.
            rows = section.locator("tr")
            target_row = None

            for index in range(await rows.count()):
                row = rows.nth(index)
                try:
                    row_text = (await row.inner_text()).strip()
                    inputs = row.locator(
                        'input:not([type="checkbox"]):not([type="radio"]):visible'
                    )
                    if "均码" in row_text and await inputs.count() >= 4:
                        target_row = row
                        break
                except Exception:
                    continue

            # Some releases do not expose the word "均码" inside the table row.
            # If there is only one data row with at least four value inputs, that
            # row is the selected one-size row and is safe to use.
            if target_row is None:
                candidates = []
                for index in range(await rows.count()):
                    row = rows.nth(index)
                    try:
                        inputs = row.locator(
                            'input:not([type="checkbox"]):not([type="radio"]):visible'
                        )
                        if await inputs.count() >= 4:
                            candidates.append(row)
                    except Exception:
                        continue
                if len(candidates) == 1:
                    target_row = candidates[0]

            if target_row is None:
                raise RuntimeError("针织衫选择均码后，尺码表中无法定位唯一的均码数据行")

            inputs = target_row.locator(
                'input:not([type="checkbox"]):not([type="radio"]):visible'
            )
            count = await inputs.count()
            if count < 4:
                raise RuntimeError(f"针织衫均码行输入框少于 4 个，实际找到 {count} 个")

            # If the first input itself contains the size name, skip it.  If it
            # already contains a numeric height value, it is the first range field.
            start_index = 0
            try:
                first_value = (await inputs.nth(0).input_value()).strip()
                if first_value and not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", first_value):
                    start_index = 1
            except Exception:
                pass

            if count < start_index + 4:
                raise RuntimeError(
                    f"针织衫均码行可填写区间字段不足 4 个：总输入框 {count} 个，起始索引 {start_index}"
                )

            for offset, value in enumerate(values["均码"]):
                field = inputs.nth(start_index + offset)
                await self.fill_form_input(field, value, f"均码第 {offset + 1} 个区间值")

            self.log("针织衫尺码表已填写：均码 / 身高 150-175 / 体重 90-125")
            return

        # Non-knitwear keeps the existing S/M/L/XL workflow.  These rows expose
        # the generated size name as the first input.
        rows = await self._editable_size_rows(section)
        seen: set[str] = set()
        for row in reversed(rows):
            size = await self._row_size(row)
            if size in values and size not in seen:
                seen.add(size)
                continue
            delete = row.get_by_text("删除", exact=True).last
            if not await delete.is_visible():
                raise RuntimeError(f"尺码表包含不需要的尺码“{size or '(空)'}”，但找不到删除操作")
            await delete.click(force=True)
            self.log(f"已点击尺码 {size or '(空)'} 同行的删除按钮")
            await asyncio.sleep(.25)

        row_map = await self._size_row_map(section)
        missing_existing = [size for size in ("S", "M") if size not in row_map]
        if missing_existing:
            raise RuntimeError(f"删除 XS 后尺码表缺少：{'、'.join(missing_existing)}")

        for wanted in ("L", "XL"):
            row_map = await self._size_row_map(section)
            if wanted in row_map:
                self.log(f"尺码 {wanted} 已存在，跳过添加行")
                continue
            add = await first_visible(section, ["添加行"])
            if not add:
                raise RuntimeError(f"尺码表缺少 {wanted}，且找不到“添加行”按钮")
            await add.click(force=True)
            self.log(f"已点击添加行，准备填写尺码 {wanted}")

            blank = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and blank is None:
                rows = await self._editable_size_rows(section)
                for candidate in rows:
                    if not await self._row_size(candidate):
                        blank = candidate
                        break
                if blank is None:
                    await asyncio.sleep(.2)

            if blank is None:
                raise RuntimeError(f"点击添加行后未找到用于填写 {wanted} 的空白行")

            size_input = blank.locator(
                'input:not([type="checkbox"]):not([type="radio"]):visible'
            ).first
            if await size_input.is_editable():
                await size_input.fill(wanted)
            else:
                await self.fill_form_input(size_input, wanted, f"新增尺码 {wanted}")
            await asyncio.sleep(.3)
            actual = (await size_input.input_value()).strip().upper()
            if actual != wanted:
                raise RuntimeError(
                    f"新增尺码写入失败：期望 {wanted}，页面实际为 {actual or '(空)'}"
                )
            self.log(f"新增尺码 {wanted} 已写入并校验")

        row_map = await self._size_row_map(section)
        for size, range_values in values.items():
            row = row_map.get(size)
            if row is None:
                raise RuntimeError(f"尺码表中未找到 {size} 行")
            inputs = row.locator(
                'input:not([type="checkbox"]):not([type="radio"]):visible'
            )
            if await inputs.count() < 5:
                raise RuntimeError(f"尺码 {size} 行输入框少于 5 个")
            for input_index, value in enumerate(range_values, start=1):
                field = inputs.nth(input_index)
                await self.fill_form_input(field, value, f"尺码 {size} 第 {input_index} 个区间值")

        self.log("尺码表已填写：S/M/L/XL 身高与体重区间")

    async def detect_knitwear_category(self, page: Page) -> bool:
        for label_text in ("商品类目", "商品类别", "类目"):
            labels = page.get_by_text(label_text, exact=True)
            for index in range(await labels.count()):
                label = labels.nth(index)
                if not await label.is_visible():
                    continue
                field = label.locator("xpath=ancestor::*[1]")
                if "针织衫" in await field.inner_text():
                    self.log("识别商品类别：针织衫，启用均码流程")
                    return True
        body = await page.locator("body").inner_text()
        if re.search(r"(?:商品类目|商品类别|类目)[\s\S]{0,100}针织衫", body):
            self.log("识别商品类别：针织衫，启用均码流程")
            return True
        self.log("商品类别不是针织衫，继续使用中国码 S/M/L/XL 流程")
        return False

    async def select_size_specifications(self, page: Page, knitwear: bool = False) -> None:
        family = "均码" if knitwear else "中国码"
        sizes = ("均码",) if knitwear else ("S", "M", "L", "XL")

        label = await self.require_visible(page, ["尺码大小"], 30_000)
        await label.scroll_into_view_if_needed()
        # Search only the nearest visible card containing the label. Never use
        # an unbounded following::input fallback: the page also has a lower
        # 尺码助手/模板 dropdown that can otherwise be selected by mistake.
        field = None
        control = None
        for level in range(1, 7):
            candidate = label.locator(f"xpath=ancestor::*[{level}]")
            if not await candidate.count() or not await candidate.is_visible():
                continue
            candidate_text = (await candidate.inner_text()).strip()
            if "模板" in candidate_text and "尺码大小" not in candidate_text:
                continue
            candidate_control = candidate.get_by_role("combobox").first
            if not await candidate_control.count():
                candidate_control = candidate.locator('input[placeholder*="尺码"], input[placeholder="请选择"]:visible').first
            if await candidate_control.count() and await candidate_control.is_visible():
                field, control = candidate, candidate_control
                break
        if field is None or control is None:
            raise RuntimeError("找到‘尺码大小’，但在同一规格卡片内找不到对应下拉列表")

        placeholder = ""
        try:
            placeholder = await control.get_attribute("placeholder") or ""
        except Exception:
            pass
        if placeholder and "模板" in placeholder:
            raise RuntimeError(f"尺码大小定位命中了模板控件：{placeholder}")

        await control.click(force=True)
        await asyncio.sleep(.5)

        # 第一级：选择左侧尺码体系。
        # “均码”页面里会同时出现左侧“均码”和右侧子复选框“均码”，
        # 因此这里明确选择“不属于 checkbox 的那个均码”，避免点错层级。
        family_option = None
        family_deadline = time.monotonic() + 15
        while time.monotonic() < family_deadline:
            candidates = page.get_by_text(family, exact=True)
            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                try:
                    if not await candidate.is_visible():
                        continue
                    associated_checkbox = candidate.locator(
                        'xpath=(ancestor-or-self::label[1]//input[@type="checkbox"] '
                        '| ancestor::*[1]//input[@type="checkbox"])[1]'
                    )
                    if await associated_checkbox.count():
                        continue
                    family_option = candidate
                    break
                except Exception:
                    continue
            if family_option is not None:
                break
            await asyncio.sleep(.3)

        if family_option is None:
            raise RuntimeError(f"尺码大小下拉列表中找不到左侧‘{family}’分类")

        await family_option.click(force=True)
        self.log(f"尺码大小已选择一级分类：{family}")
        await asyncio.sleep(.6)

        # 第二级：勾选右侧真正的尺码复选框。
        # 针织衫必须执行“左侧均码 -> 右侧均码 checkbox”两步。
        for size in sizes:
            selected = False
            size_deadline = time.monotonic() + 15

            while time.monotonic() < size_deadline and not selected:
                size_labels = page.get_by_text(size, exact=True)

                for index in range(await size_labels.count()):
                    size_label = size_labels.nth(index)
                    try:
                        if not await size_label.is_visible():
                            continue

                        checkbox = size_label.locator(
                            'xpath=(ancestor-or-self::label[1]//input[@type="checkbox"] '
                            '| ancestor::*[1]//input[@type="checkbox"] '
                            '| ancestor::*[.//input[@type="checkbox"]][1]//input[@type="checkbox"])[1]'
                        )
                        if not await checkbox.count():
                            # Some VVIC/抖店 releases render the checkbox as a
                            # custom div without a native input. Clicking the
                            # exact size label is the supported fallback.
                            await size_label.click(force=True)
                            await asyncio.sleep(.25)
                            owner = size_label.locator("xpath=ancestor::*[1]")
                            state = " ".join(filter(None, [
                                await owner.get_attribute("class"),
                                await owner.get_attribute("aria-checked"),
                                await owner.get_attribute("data-state"),
                            ])).lower()
                            if re.search(r"checked|selected|active|true", state) or not await size_label.is_visible():
                                selected = True
                                self.log(f"尺码子选项已点击：{size}")
                            continue
                        if not await checkbox.is_visible():
                            continue

                        if not await checkbox.is_checked():
                            try:
                                await checkbox.check(force=True)
                            except Exception:
                                await size_label.click(force=True)

                        await asyncio.sleep(.25)
                        if await checkbox.is_checked():
                            selected = True
                            self.log(f"尺码子选项已勾选：{size}")
                            break
                    except Exception:
                        continue

                if not selected:
                    await asyncio.sleep(.3)

            if not selected:
                raise RuntimeError(f"{family}列表中的子复选框‘{size}’未能勾选")

        # 第三级：勾选完成后再找“确定(数量)”按钮。
        # 不再只依赖 role=button，因为抖店某些版本会把它渲染成普通元素。
        confirm = None
        confirm_deadline = time.monotonic() + 20
        confirm_pattern = re.compile(r"^\s*确定(?:\s*\(\s*\d+\s*\))?\s*$")

        while time.monotonic() < confirm_deadline:
            buttons = page.get_by_role("button", name=re.compile(r"确定"))
            for index in range(await buttons.count()):
                candidate = buttons.nth(index)
                try:
                    if await candidate.is_visible():
                        candidate_text = (await candidate.inner_text()).strip()
                        if confirm_pattern.match(candidate_text):
                            confirm = candidate
                            break
                except Exception:
                    continue

            if confirm is not None:
                break

            texts = page.get_by_text(re.compile(r"确定"), exact=False)
            for index in range(await texts.count()):
                candidate = texts.nth(index)
                try:
                    if not await candidate.is_visible():
                        continue
                    candidate_text = (await candidate.inner_text()).strip()
                    if confirm_pattern.match(candidate_text):
                        confirm = candidate
                        break
                except Exception:
                    continue

            if confirm is not None:
                break
            await asyncio.sleep(.4)

        if confirm is None:
            raise RuntimeError(f"已勾选 {family} 子选项，但找不到‘确定’按钮")

        await confirm.click(force=True)
        await asyncio.sleep(.6)
        self.log(f"尺码大小已选择：{family} / {' / '.join(sizes)}")

    async def fill_size_remarks(self, page: Page, knitwear: bool = False) -> None:
        """Fill the remark next to each selected size."""
        remark_values = (
            {"均码": "80-125"}
            if knitwear
            else {
                "S": "80-95",
                "M": "95-110",
                "L": "110-120",
                "XL": "120-135",
            }
        )

        label = await self.require_visible(page, ["尺码大小"], 30_000)
        await label.scroll_into_view_if_needed()

        # 尺码确认后，抖店会异步生成已选尺码与右侧“备注”输入框。
        # 不依赖固定排列位置，而是按实际尺码值找到同一项中的备注框。
        expected_remark_count = len(remark_values)
        deadline = time.monotonic() + 15
        section = None
        while time.monotonic() < deadline:
            candidate = label.locator(
                'xpath=ancestor::*['
                './/input[@placeholder="备注"] '
                'or .//input[contains(@placeholder,"备注")]'
                '][1]'
            )
            try:
                remark_count = await candidate.locator('input[placeholder*="备注"]:visible').count()
                if await candidate.count() and remark_count >= expected_remark_count:
                    section = candidate
                    break
            except Exception:
                pass
            await asyncio.sleep(.3)

        if section is None:
            expected_sizes = "/".join(remark_values)
            raise RuntimeError(f"选择 {expected_sizes} 后，等待尺码备注输入框生成超时")

        for size, remark in remark_values.items():
            target_size_input = None
            inputs = section.locator('input:visible')
            for index in range(await inputs.count()):
                candidate = inputs.nth(index)
                try:
                    if "备注" in (await candidate.get_attribute("placeholder") or ""):
                        continue
                    value = (await candidate.input_value()).strip()
                    if value.upper() == size.upper():
                        target_size_input = candidate
                        break
                except Exception:
                    continue

            if target_size_input is None:
                raise RuntimeError(f"尺码大小区域中找不到已选择的尺码 {size}")

            # 找到同时包含该尺码框和备注框的最近祖先节点。
            item = target_size_input.locator(
                'xpath=ancestor::*['
                './/input[@placeholder="备注"] '
                'or .//input[contains(@placeholder,"备注")]'
                '][1]'
            )
            remark_input = item.locator('input[placeholder="备注"], input[placeholder*="备注"]').first
            if not await remark_input.count() or not await remark_input.is_visible():
                raise RuntimeError(f"找到尺码 {size}，但找不到对应的备注输入框")

            await remark_input.fill(remark)
            await asyncio.sleep(.2)
            actual = (await remark_input.input_value()).strip()
            if actual != remark:
                raise RuntimeError(
                    f"尺码 {size} 备注填写失败：期望 {remark}，页面实际为 {actual or '(空)'}"
                )
            self.log(f"尺码备注已填写：{size} = {remark}")

        # 最终整体复核，避免前端重绘导致前面已填写的备注被清空。
        for size, expected in remark_values.items():
            found = False
            inputs = section.locator('input:visible')
            for index in range(await inputs.count()):
                candidate = inputs.nth(index)
                try:
                    value = (await candidate.input_value()).strip()
                    if value.upper() != size.upper():
                        continue
                    item = candidate.locator(
                        'xpath=ancestor::*['
                        './/input[@placeholder="备注"] '
                        'or .//input[contains(@placeholder,"备注")]'
                        '][1]'
                    )
                    remark_input = item.locator('input[placeholder="备注"], input[placeholder*="备注"]').first
                    if await remark_input.count() and (await remark_input.input_value()).strip() == expected:
                        found = True
                        break
                except Exception:
                    continue
            if not found:
                raise RuntimeError(f"尺码 {size} 备注最终校验失败，期望值：{expected}")

        summary = " / ".join(f"{size}={remark}" for size, remark in remark_values.items())
        self.log(f"尺码备注填写完成：{summary}")

    async def fill_form_input(self, field: Locator, value: str, field_name: str) -> None:
        """Fill a normal input, or update a framework-disabled one natively."""
        if await field.is_editable():
            await field.fill(value)
        else:
            await field.evaluate(
                """(element, value) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'value').set;
                    element.disabled = false;
                    element.readOnly = false;
                    element.removeAttribute('disabled');
                    element.removeAttribute('readonly');
                    setter.call(element, value);
                    element.dispatchEvent(new InputEvent('input', {
                        bubbles: true, inputType: 'insertText', data: value
                    }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    element.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
                }""",
                value,
            )
        await asyncio.sleep(.1)
        actual = (await field.input_value()).strip()
        if actual != value:
            raise RuntimeError(f"{field_name}写入失败：期望 {value}，页面实际为 {actual or '(空)'}")

    async def ensure_size_interval_modes(self, section: Locator) -> None:
        labels = section.get_by_text("区间值", exact=True)
        count = await labels.count()
        if count < 2:
            raise RuntimeError(f"尺码表只找到 {count} 个“区间值”选项，预期身高和体重各一个")
        for index in range(2):
            label = labels.nth(index)
            checkbox = label.locator(
                'xpath=(ancestor-or-self::label[1]//input[@type="checkbox"] '
                '| ancestor::*[1]//input[@type="checkbox"])[1]'
            )
            if await checkbox.count():
                if not await checkbox.is_checked():
                    try:
                        await checkbox.check(force=True)
                    except Exception:
                        await label.click(force=True)
                if not await checkbox.is_checked():
                    raise RuntimeError(f"第 {index + 1} 个“区间值”复选框未能勾选")
            else:
                # Some releases render a custom checkbox without a native
                # input. Click its visible label and verify selected styling.
                await label.click(force=True)
                await asyncio.sleep(.2)
                owner = label.locator("xpath=ancestor::*[1]")
                state = " ".join(filter(None, [
                    await owner.get_attribute("class"),
                    await owner.get_attribute("aria-checked"),
                    await owner.get_attribute("data-state"),
                ])).lower()
                if not re.search(r"checked|selected|active|true", state):
                    raise RuntimeError(f"第 {index + 1} 个自定义“区间值”选项无法确认已勾选")
        self.log("已先勾选身高、体重两个“区间值”选项")

    async def _editable_size_rows(self, section: Locator) -> list[Locator]:
        result: list[Locator] = []
        rows = section.locator("tr")
        for index in range(await rows.count()):
            row = rows.nth(index)
            inputs = row.locator('input:not([type="checkbox"]):not([type="radio"]):visible')
            if await inputs.count() >= 5:
                result.append(row)
        return result

    async def _row_size(self, row: Locator) -> str:
        first = row.locator('input:not([type="checkbox"]):not([type="radio"]):visible').first
        return (await first.input_value()).strip().upper()

    async def _size_row_map(self, section: Locator) -> dict[str, Locator]:
        result: dict[str, Locator] = {}
        for row in await self._editable_size_rows(section):
            size = await self._row_size(row)
            if size:
                result[size] = row
        return result

    async def detail_section(self, page: Page) -> Locator:
        title = await first_visible(page, self.s["detailSectionTexts"])
        if not title:
            raise RuntimeError("找不到“商品详情”区域")
        return title.locator('xpath=ancestor::*[.//input[@type="file"] or .//button][1]')

    async def delete_detail_images(self, section: Locator) -> None:
        """Delete every generated detail image.

        Douyin's current trash control is an icon-only hover overlay.  It has
        no stable accessible name/class, so text selectors alone cannot find
        it.  We first try semantic selectors and then click the bottom-right
        icon position inside the hovered image card (as shown by the UI).
        """
        page = section.page
        for _ in range(200):
            image, count = await self._first_detail_thumbnail(section)
            if image is None:
                return
            await image.scroll_into_view_if_needed()
            box = await image.bounding_box()
            if not box:
                raise RuntimeError("详情图存在但无法取得图片位置")
            await image.hover(force=True)
            await asyncio.sleep(.25)  # wait for the dark hover toolbar

            clicked = False
            semantic = section.locator(
                '[aria-label*="删除"], [title*="删除"], '
                'button:has-text("删除"), [data-testid*="delete"], '
                '[class*="delete"], [class*="trash"]'
            )
            for index in range(await semantic.count()):
                candidate = semantic.nth(index)
                try:
                    candidate_box = await candidate.bounding_box()
                    if (await candidate.is_visible() and candidate_box
                            and self._box_inside(candidate_box, box)):
                        await candidate.click(force=True)
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                # The icon-only toolbar places the trash can at bottom-right.
                # Clicking by the image's own box remains resolution-independent.
                await page.mouse.click(box["x"] + box["width"] - 22,
                                       box["y"] + box["height"] - 18)

            dialog = page.get_by_role("dialog").last
            try:
                if await dialog.is_visible():
                    confirm_scope = dialog
                else:
                    confirm_scope = page
            except Exception:
                confirm_scope = page
            confirm = await first_visible(confirm_scope, ["确认", "确定"])
            if confirm:
                await confirm.click(force=True)

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                _, remaining = await self._first_detail_thumbnail(section)
                if remaining < count:
                    self.log(f"已删除自动生成详情图，剩余 {remaining} 张")
                    break
                await asyncio.sleep(.2)
            else:
                raise RuntimeError("已点击详情图右下角垃圾桶，但图片数量未减少")
        raise RuntimeError("详情图数量异常，已停止删除")

    async def _first_detail_thumbnail(self, section: Locator) -> tuple[Locator | None, int]:
        """Return the first visible, card-sized image and total thumbnail count."""
        images = section.locator("img:visible")
        matches: list[Locator] = []
        for index in range(await images.count()):
            image = images.nth(index)
            try:
                box = await image.bounding_box()
                # Ignore tiny editor icons, emoji and loading indicators.
                if box and box["width"] >= 70 and box["height"] >= 70:
                    matches.append(image)
            except Exception:
                continue
        return (matches[0] if matches else None, len(matches))

    @staticmethod
    def _box_inside(candidate: dict, image: dict) -> bool:
        center_x = candidate["x"] + candidate["width"] / 2
        center_y = candidate["y"] + candidate["height"] / 2
        return (image["x"] <= center_x <= image["x"] + image["width"]
                and image["y"] <= center_y <= image["y"] + image["height"])


class AutomationRunner:
    def __init__(self, project_root: Path = ROOT, log: LogCallback = print, stop_event: threading.Event | None = None):
        self.root, self.log = project_root, log
        self.stop_event = stop_event or threading.Event()

    def _browser_args(self, config: dict) -> list[str]:
        browser_args = ["--start-maximized"]
        extension_value = str(config.get("browserExtensionDir") or "").strip()
        if extension_value:
            extension_dir = extension_manifest_dir(extension_value)
            browser_args.extend([
                f"--disable-extensions-except={extension_dir}",
                f"--load-extension={extension_dir}",
            ])
        return browser_args

    def recording_store(self) -> RecordingStore:
        bundled = self.root / "recordings"
        writable = self.root / "recordings"
        try:
            writable.mkdir(parents=True, exist_ok=True)
            probe = writable / ".write-test"
            probe.write_text("ok", encoding="utf-8"); probe.unlink()
        except OSError:
            local = Path(os.environ.get("LOCALAPPDATA", str(self.root))) / "DouyinDraftAutomation" / "recordings"
            writable = local
        return RecordingStore(bundled, writable)

    async def record_operations(self, site: str, category: str, commands) -> None:
        """Run an interactive recorder controlled by a thread-safe GUI command queue."""
        if site not in {"vvic", "zwd17"}:
            raise RuntimeError("抖店使用原有流程，不需要录制")
        config = load_config(self.root)
        home = "https://www.vvic.com/" if site == "vvic" else "https://www.17zwd.com/"
        async with async_playwright() as playwright:
            context = await self._launch_context(playwright, config, config["browserProfileDir"])
            recorder = OperationRecorder(self.recording_store(), self.log)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(home, wait_until="domcontentloaded", timeout=config["timeouts"]["navigationMs"])
                await recorder.start(page, site, category)
                self.log(f"录制已开始：{site}/{category}。请在浏览器操作，最后只点击保存草稿。")
                while not self.stop_event.is_set():
                    try:
                        command = commands.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(.2); continue
                    if command == "pause":
                        await recorder.pause(); self.log("录制已暂停")
                    elif command == "resume":
                        await recorder.resume(); self.log("录制已继续")
                    elif command == "cancel":
                        await recorder.cancel(); self.log("录制已取消，旧文件未改变"); return
                    elif command == "finish":
                        profile = await recorder.finish()
                        self.log(f"录制已保存：{site}/{category}，共 {len(profile.steps)} 步")
                        return
            finally:
                await context.close()

    async def _launch_context(self, playwright, config: dict, profile_dir: str) -> BrowserContext:
        return await playwright.chromium.launch_persistent_context(
            profile_dir,
            executable_path=config["browserExecutable"],
            headless=False,
            accept_downloads=True,
            no_viewport=True,
            args=self._browser_args(config),
            ignore_default_args=["--disable-extensions"],
        )

    async def _login_profile(
        self,
        playwright,
        config: dict,
        profile_dir: str,
        store_name: str,
        include_source_sites: bool,
    ) -> None:
        context = await self._launch_context(playwright, config, profile_dir)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(config["douyinHome"], wait_until="domcontentloaded")
            if include_source_sites:
                self.log(
                    f"{store_name}专用 Edge 已打开。请登录 17网、搜款网、淘宝和{store_name}，"
                    "并安装/启用淘宝素材插件；完成后关闭浏览器。"
                )
            else:
                self.log(f"{store_name}专用 Edge 已打开。请登录{store_name}；完成后关闭浏览器。")
            while context.pages and not self.stop_event.is_set():
                await asyncio.sleep(1)
        finally:
            try:
                await context.close()
            except Exception:
                pass

    async def run(self, login_only: bool = False) -> None:
        config = load_config(self.root)
        self.log(f"Python 自动化版本 {VERSION}（{'单抖店' if config['storeMode'] == 'single' else '双抖店'}）")
        Path(config["workDir"]).mkdir(parents=True, exist_ok=True)

        async with async_playwright() as playwright:
            if login_only:
                await self._login_profile(
                    playwright,
                    config,
                    config["browserProfileDir"],
                    "抖店1",
                    include_source_sites=True,
                )
                if config["storeMode"] == "dual" and not self.stop_event.is_set():
                    await self._login_profile(
                        playwright,
                        config,
                        config["browserProfileDir2"],
                        "抖店2",
                        include_source_sites=False,
                    )
                return

            store = ExcelStore(Path(config["excelPath"]))
            try:
                store.load()
            except RuntimeError as error:
                # A fresh installation may have no tasks.xlsx; the GUI queue
                # is now the normal entry point, so initialize an empty legacy
                # workbook instead of requiring manual Excel editing.
                if "已创建任务表" not in str(error):
                    raise
                store.load()
            append_path = Path(config["queuePath"])
            imported = import_append_tasks(store, append_path)
            if imported:
                self.log(f"已导入追加任务 {imported} 条。")
            metadata = load_queue_metadata(append_path)
            tasks = store.tasks()
            for task in tasks:
                task.update(metadata.get(normalize_url(task["url"]), {}))
            if not tasks:
                self.log("没有待处理任务。")
                return

            adapters = [Zwd17Adapter(config), VvicAdapter(config), TaobaoAdapter(config)]
            store_configs = [("抖店1", "抖店1状态", "store1_status", config["browserProfileDir"])]
            if config["storeMode"] == "dual":
                store_configs.append(("抖店2", "抖店2状态", "store2_status", config["browserProfileDir2"]))

            appended_imported = 0
            for store_name, status_column, task_status_key, profile_dir in store_configs:
                if self.stop_event.is_set():
                    self.log("任务已停止")
                    break

                metadata = load_queue_metadata(append_path)
                current_tasks = store.tasks()
                for task in current_tasks:
                    task.update(metadata.get(normalize_url(task["url"]), {}))
                pending_tasks = [
                    task for task in current_tasks
                    if task.get(task_status_key) != "草稿成功"
                    and not (store_name == "抖店2" and task.get("destination", "douyin") != "douyin")
                ]
                if not pending_tasks:
                    self.log(f"{store_name}没有待处理任务。")
                    continue

                self.log(f"开始执行{store_name}，待处理 {len(pending_tasks)} 条任务。")
                context = await self._launch_context(playwright, config, profile_dir)
                try:
                    for task in pending_tasks:
                        if self.stop_event.is_set():
                            self.log("任务已停止")
                            break
                        await self.process_task(
                            task,
                            store,
                            context,
                            config,
                            adapters,
                            store_name=store_name,
                            status_column=status_column,
                        )
                finally:
                    await context.close()

                # URLs submitted while this store was running are imported only
                # after the current browser batch finishes, so the active task is
                # never interrupted. They are processed on the next store pass.
                imported = import_append_tasks(store, append_path)
                appended_imported += imported
                if imported:
                    self.log(f"当前批次结束，已接收追加任务 {imported} 条。")

            # Do not delete rows during processing, because deleting one Excel row
            # would invalidate the row_number values of the remaining in-memory tasks.
            # Delete all fully completed rows together, from bottom to top.
            deleted = store.delete_completed_rows(store_mode=config["storeMode"])
            if deleted:
                self.log(f"所选抖店均已完成，已从 Excel 自动删除 {deleted} 条任务。")
            else:
                self.log("本轮没有同时完成两个抖店的任务，Excel 未删除任务。")
            if appended_imported and not self.stop_event.is_set():
                self.log("追加任务已加入主队列，继续处理追加任务。")
                await self.run(login_only=False)

    async def process_task(
        self,
        task: dict,
        store: ExcelStore,
        context: BrowserContext,
        config: dict,
        adapters: list[Adapter],
        store_name: str,
        status_column: str,
    ) -> None:
        destination = task.get("destination", "douyin")
        category = task.get("category", "tshirt")
        if destination != "douyin":
            store_name = {"vvic": "搜款网", "zwd17": "17网"}.get(destination, destination)
            if "taobao.com" not in task["url"] and "tmall.com" not in task["url"]:
                raise RuntimeError(f"{store_name}目标只支持淘宝/天猫来源链接")
        adapter = next((item for item in adapters if item.matches(task["url"])), None)
        if not adapter:
            raise RuntimeError(f"不支持的素材网站：{urlparse(task['url']).hostname}")

        task_dir = Path(config["workDir"]) / task_id(task["url"], task["row_number"])
        downloads, extracted = task_dir / "downloads", task_dir / "extracted"
        evidence = task_dir / "evidence" / store_name
        for directory in (downloads, evidence):
            directory.mkdir(parents=True, exist_ok=True)
        log_file = task_dir / "run.log"

        def log(message: str) -> None:
            line = f"[{now_text()}] [{store_name}] {message}"
            self.log(line)
            with log_file.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")

        def update(values: dict) -> None:
            store.update(task["row_number"], values)
            queue_values = {}
            if "状态" in values: queue_values["status"] = values["状态"]
            if "当前步骤" in values: queue_values["current_step"] = values["当前步骤"]
            if "失败原因" in values: queue_values["failure_reason"] = values["失败原因"]
            if queue_values: update_queue_task(Path(config["queuePath"]), task["url"], queue_values)

        baseline_pages = set(context.pages)
        popup_guard = None
        page = await context.new_page()
        retries = 0
        update({
            "站点": adapter.name,
            "状态": f"{store_name}处理中",
            status_column: "处理中",
            "当前步骤": f"{store_name}：准备素材",
            "开始时间": now_text(),
            "完成时间": "",
            "素材目录": str(task_dir),
            "失败原因": "",
            "重试次数": 0,
        })

        try:
            if destination == "douyin" and config.get("autoClosePopups", True):
                popup_guard = PopupGuard(log, self.stop_event.is_set)
                await popup_guard.start(context)
            archives = sorted(
                (path for path in downloads.glob("*.zip") if path.is_file()),
                key=lambda path: (
                    0 if re.search(r"基础图片|商品图片|主图|图片", path.name) else 1,
                    natural_key(path.name),
                ),
            )
            source_marker = downloads / "source-url.txt"
            recorded_url = source_marker.read_text(encoding="utf-8").strip() if source_marker.is_file() else ""
            archive = next((path for path in archives if _valid_zip(path)), None)
            if recorded_url != task["url"]:
                archive = None

            downloaded_fresh = False
            if not archive:
                last_error: Exception | None = None
                for attempt in range(config.get("retryCount", 2) + 1):
                    try:
                        log(f"下载尝试 {attempt + 1}/{config.get('retryCount', 2) + 1}")
                        archive = await adapter.download(page, task["url"], downloads)
                        source_marker.write_text(task["url"] + "\n", encoding="utf-8")
                        downloaded_fresh = True
                        break
                    except Exception as error:
                        last_error, retries = error, attempt + 1
                        update({"重试次数": retries})
                        log(f"下载失败：{error}")
                if not archive:
                    raise last_error or RuntimeError("下载失败")
            else:
                log("复用已经下载完成的素材包，不重复下载。")

            if downloaded_fresh and extracted.exists():
                extracted_resolved = extracted.resolve()
                task_resolved = task_dir.resolve()
                if task_resolved not in extracted_resolved.parents:
                    raise RuntimeError("拒绝清理任务目录外的旧解压目录")
                shutil.rmtree(extracted_resolved)

            if not extracted.exists():
                extract_zip_safe(archive, extracted)

            source_title = ""
            try:
                source_title = (await page.title()).split("-")[0].strip()
            except Exception:
                pass
            source_title = source_title or f"淘宝商品-{task_id(task['url'], task['row_number'])}"
            main = collect_images(extracted, "主图")
            main_source = "主图"
            if not main:
                main = collect_images(extracted, "SKU") or collect_images(extracted, "sku")
                main_source = "SKU"
            if not main:
                main = collect_images(extracted, "详情")
                main_source = "详情"
            videos = collect_videos(extracted)
            details = collect_images(extracted, "详情")
            if not main:
                raise RuntimeError("解压素材中没有找到主图、SKU图或详情图")
            if main_source != "主图":
                log(f"未找到主图，已使用第一张{main_source}图作为临时主图：{main[0].name}")
            if videos:
                log(f"检测到 {len(videos)} 个视频，将上传第一个主图视频：{videos[0].name}")

            update({
                "状态": f"{store_name}处理中",
                "当前步骤": f"{store_name}：素材检查完成",
                "素材目录": str(extracted),
            })

            workflow_warnings: list[str] = []
            if destination == "douyin":
                creator = DouyinDraftCreator(config, self.stop_event, log)
                page = await creator.create(
                    page, main, videos,
                    lambda step: update({"状态": f"{store_name}处理中", "当前步骤": f"{store_name}：{step}"}),
                )
                workflow_warnings = creator.warnings
            elif destination in {"vvic", "zwd17"}:
                tracker = FailureTracker(self.root / "recording_state.json")
                replayer = OperationReplayer(self.recording_store(), tracker, log, self.stop_event.is_set)
                replay = await replayer.replay(page, destination, category, {
                    "url": task["url"], "site": destination, "category": category,
                    "title": source_title, "main_images": main[:5], "videos": videos[:1],
                    "detail_images": details,
                    "wash_label": [Path(config["fixedWashLabelImage"])] if config.get("fixedWashLabelImage") else [],
                    "work_dir": extracted,
                })
                workflow_warnings = replay.warnings
                if not replay.success:
                    raise RuntimeError(f"录制回放失败 [{replay.failed_step}]：{replay.failure_reason}")
            else:
                raise RuntimeError(f"未知目的站点：{destination}")

            shot = evidence / "草稿保存成功.png"
            await page.screenshot(path=shot, full_page=True)
            update({
                status_column: "草稿成功",
                "状态": f"{store_name}完成",
                "当前步骤": f"{store_name}：完成",
                "完成时间": now_text(),
                "失败原因": "；".join(f"警告：{warning}" for warning in workflow_warnings),
                "重试次数": retries,
            })
            if workflow_warnings:
                log(f"草稿保存成功，但有可跳过步骤警告：{'；'.join(workflow_warnings)}")
            else:
                log(f"草稿保存成功：{shot}")

        except Exception as error:
            workflow = (
                await find_page(context, ["批量设置"])
                or await find_page(context, ["尺码表"])
                or await find_page(context, ["商品详情"])
            )
            if workflow:
                page = workflow

            shot = evidence / f"失败-{int(time.time())}.png"
            if not page.is_closed():
                try:
                    await page.screenshot(path=shot, full_page=True)
                except Exception:
                    pass

            status = "已停止" if isinstance(error, InterruptedError) else "失败"
            update({
                status_column: status,
                "状态": f"{store_name}{status}",
                "当前步骤": f"{store_name}：{status}",
                "完成时间": now_text(),
                "失败原因": f"{store_name}：{error}",
                "重试次数": retries,
            })
            log(f"任务{status}：[{category}][{destination}] {task['url']}：{error}")
        finally:
            if popup_guard:
                await popup_guard.stop()
            await self.cleanup_task_pages(context, baseline_pages)

    async def cleanup_task_pages(self, context: BrowserContext, baseline_pages: set[Page]) -> None:
        """Close pages opened by one task, including extension popups and downloads."""
        for candidate in list(context.pages):
            if candidate in baseline_pages or candidate.is_closed():
                continue
            try:
                await candidate.close()
            except Exception:
                pass


def _valid_zip(path: Path) -> bool:
    try:
        verify_zip(path)
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="抖店草稿自动上款（Python）")
    parser.add_argument("--login", action="store_true", help="打开专用 Edge 登录环境")
    parser.add_argument("--check", action="store_true", help="仅检查配置和依赖")
    args = parser.parse_args()
    try:
        load_config(ROOT)
        if args.check:
            print("PYTHON_AUTOMATION_OK")
            return 0
        asyncio.run(AutomationRunner().run(login_only=args.login))
        return 0
    except KeyboardInterrupt:
        print("已停止")
        return 130
    except Exception as error:
        print(f"运行失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
