from __future__ import annotations

import hashlib
import io
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from playwright.async_api import Locator, Page

from operation_recorder import is_forbidden_action
from popup_guard import CLOSE_TEXT, POPUP_CONTEXT
from .models import NeedsHuman, Region, VisionResult


def normalized_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value).casefold()


async def page_state(page: Page) -> dict:
    return await page.evaluate("""() => {
        window.__difyBridgeDocumentId ||= `${performance.timeOrigin}-${Math.random()}`;
        return {url: location.href, width: innerWidth, height: innerHeight,
                scrollX, scrollY, pixelRatio: devicePixelRatio,
                documentId: window.__difyBridgeDocumentId};
    }""")


def crop_box(region: Region | None, state: dict) -> tuple[int, int, int, int]:
    width, height = state["width"], state["height"]
    if region is None:
        return 0, 0, width, height
    scale_x, scale_y = (width, height) if region.normalized else (1, 1)
    x, y = int(region.x * scale_x), int(region.y * scale_y)
    right, bottom = int((region.x + region.width) * scale_x), int((region.y + region.height) * scale_y)
    if right > width or bottom > height or right <= x or bottom <= y:
        raise NeedsHuman("圈选区域超出当前浏览器视口，请重新指定区域")
    return x, y, right, bottom


async def capture_pixels(page: Page, box: tuple[int, int, int, int]) -> tuple[bytes, bytes]:
    screenshot = await page.screenshot(scale="css", animations="disabled", caret="hide")
    with Image.open(io.BytesIO(screenshot)) as original:
        region = original.crop(box).convert("RGB")
        output = io.BytesIO()
        region.save(output, format="PNG")
        return output.getvalue(), hashlib.sha256(region.tobytes()).digest()


@dataclass
class Snapshot:
    id: str
    page: Page
    path: Path
    target: str
    state: dict
    box: tuple[int, int, int, int]
    fingerprint: bytes
    created: float
    consumed: bool = False

    @classmethod
    async def create(cls, page: Page, folder: Path, target: str, roi: Region | None):
        state = await page_state(page)
        box = crop_box(roi, state)
        png, fingerprint = await capture_pixels(page, box)
        if await page_state(page) != state:
            raise NeedsHuman("截图期间页面发生变化，请重新截图")
        folder.mkdir(parents=True, exist_ok=True)
        identity = uuid.uuid4().hex
        path = folder / f"{identity}.png"
        path.write_bytes(png)
        return cls(identity, page, path, target, state, box, fingerprint, time.monotonic())

    def public(self) -> dict:
        x, y, right, bottom = self.box
        return {"screenshot_id": self.id, "target": self.target, "width": right - x,
                "height": bottom - y, "offset_x": x, "offset_y": y,
                "coordinate_system": "crop_css_pixels", "expires_after_seconds": 90}

    async def fresh(self) -> None:
        if self.consumed or time.monotonic() - self.created > 90:
            raise NeedsHuman("截图已使用或已过期，请重新定位")
        if self.page.is_closed() or await page_state(self.page) != self.state:
            raise NeedsHuman("页面地址、滚动位置或视口已经改变，请重新定位")
        _, current = await capture_pixels(self.page, self.box)
        if current != self.fingerprint:
            raise NeedsHuman("目标区域画面已经改变，请重新截图定位")

    async def resolve(self, result: VisionResult, minimum_confidence: float = .85):
        if result.screenshot_id != self.id:
            raise NeedsHuman("识别结果不属于当前截图")
        if normalized_text(result.target) != normalized_text(self.target):
            raise NeedsHuman("识别目标与请求目标不一致")
        if not result.found or result.confidence < minimum_confidence:
            raise NeedsHuman("目标未找到或置信度不足，请人工确认")
        width, height = self.box[2] - self.box[0], self.box[3] - self.box[1]
        if not (0 <= result.x < width and 0 <= result.y < height):
            raise NeedsHuman("识别坐标超出本次目标区域")
        if is_forbidden_action(self.target):
            raise NeedsHuman("此接入仅保存草稿，不执行正式发布或提交审核")
        await self.fresh()
        x, y = self.box[0] + result.x, self.box[1] + result.y
        marker = uuid.uuid4().hex
        info = await self.page.evaluate("""({x,y,marker}) => {
            let hit = document.elementFromPoint(x,y);
            if (!hit || hit.tagName === 'IFRAME') return null;
            while (hit.shadowRoot && hit.shadowRoot.elementFromPoint(x,y)) {
                const inner = hit.shadowRoot.elementFromPoint(x,y);
                if (inner === hit) break; hit = inner;
            }
            const el = hit.closest('button,a,input,[role="button"],[role="option"]') || hit;
            el.setAttribute('data-dify-target', marker);
            return {label: (el.getAttribute('aria-label') || el.innerText || '').trim(),
                    type: el.getAttribute('type') || '', tag: el.tagName};
        }""", {"x": x, "y": y, "marker": marker})
        if not info:
            raise NeedsHuman("目标区域无法安全检查，请使用 Locator 或人工处理")
        locator = self.page.locator(f'[data-dify-target="{marker}"]')
        if is_forbidden_action(info["label"]) or re.search(r"登录|验证码|支付|付款|删除|授权", info["label"]):
            raise NeedsHuman("当前目标涉及登录验证或业务提交，不自动执行")
        if CLOSE_TEXT.fullmatch(self.target):
            context = await locator.evaluate(POPUP_CONTEXT)
            if not context.get("safe"):
                raise NeedsHuman("该关闭按钮不属于可自动关闭的普通弹窗")
        elif info["label"] and normalized_text(self.target) not in normalized_text(info["label"]):
            raise NeedsHuman("当前位置的控件文字与识别目标不一致")
        return VisualLocator(self, locator, x, y)


class VisualLocator:
    """Use the model's exact point; delegate ancestor queries to the actual hit node."""
    def __init__(self, snapshot: Snapshot, locator: Locator, x: float, y: float):
        self.snapshot, self.locator, self.x, self.y = snapshot, locator, x, y
        self.page = snapshot.page

    def __getattr__(self, name):
        return getattr(self.locator, name)

    async def click(self, **kwargs):
        await self.snapshot.fresh()
        # Consume before emitting input: a lost response must not repeat a click.
        self.snapshot.consumed = True
        await self.page.mouse.click(self.x, self.y)

    async def scroll_into_view_if_needed(self, **kwargs):
        await self.snapshot.fresh()  # Already in the captured viewport; do not scroll.
