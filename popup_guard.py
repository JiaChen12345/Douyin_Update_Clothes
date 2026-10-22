"""Dismiss incidental overlays without closing login or product workflow dialogs."""
from __future__ import annotations

import asyncio
import re
from typing import Callable
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

from playwright.async_api import BrowserContext, Frame, Locator, Page


CLOSE_TEXT = re.compile(
    r"^\s*(?:关闭(?:弹窗|窗口|广告)?|我知道了|稍后再说|以后再说|暂不体验|close|dismiss|[×✕✖✗x])\s*$",
    re.IGNORECASE,
)
CLOSE_SELECTOR = ", ".join((
    ".ant-modal-close", ".arco-modal-close-btn", ".semi-modal-close",
    ".el-dialog__headerbtn", ".layui-layer-close", ".ecom-modal-close",
    '[aria-label="关闭"]', '[aria-label="Close" i]', '[title="关闭"]',
    '[title="Close" i]', '[class*="modal-close"]', '[class*="popup-close"]',
    '[class*="dialog-close"]',
))
_GUARDS: WeakKeyDictionary = WeakKeyDictionary()

# Inspect only an explicit close control and its popup ancestors. Do not click
# arbitrary top-right coordinates or inspect the entire page as a model input.
POPUP_CONTEXT = r"""el => {
    const visible = node => {
        const style = getComputedStyle(node), box = node.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' &&
               box.width > 0 && box.height > 0 && style.opacity !== '0';
    };
    const closeLabel = (el.getAttribute('aria-label') || el.getAttribute('title') ||
                        el.innerText || '').trim();
    let popup = null;
    for (let node = el.parentElement; node && node !== document.body; node = node.parentElement) {
        const box = node.getBoundingClientRect(), style = getComputedStyle(node);
        if (box.width < 160 || box.height < 60 || !visible(node)) continue;
        const identity = `${node.id} ${node.className}`;
        const semantic = node.matches('dialog, [role="dialog"], [aria-modal="true"]');
        const named = /modal|dialog|popup|pop-up|layui-layer|overlay|advert|promotion/i.test(identity);
        const partial = /header|footer|close|button|mask|backdrop/i.test(identity);
        const floating = ['fixed', 'absolute'].includes(style.position) &&
                         Number.parseInt(style.zIndex, 10) >= 10;
        if (semantic || (!partial && (named || floating))) { popup = node; break; }
    }
    if (!popup) return {safe: false, reason: 'not-popup'};
    const text = (popup.innerText || '').trim();
    const identities = [popup, ...popup.querySelectorAll('[id], [class]')]
        .map(node => `${node.id} ${typeof node.className === 'string' ? node.className : ''}`)
        .join(' ');
    const labels = [...popup.querySelectorAll('img, iframe')]
        .map(node => `${node.getAttribute('alt') || ''} ${node.getAttribute('title') || ''}`)
        .join(' ');
    const protectedText = /登录|登陆|验证码|滑块|安全验证|风险验证|身份验证|人机验证|密码|发布|提交|上架|删除|付款|支付|转账|订单|不可恢复|商品详情|商品创建|确认信息|继续生成|本地上传|上传图片|素材选择|商品规格|库存|价格|批量修改|未保存|保存更改|放弃更改|离开页面|是否|确认|确定要|授权|权限|订阅|购买/;
    if (protectedText.test(text + ' ' + labels) ||
        /login|captcha|verification|security|authorization|upload|editor|confirm/i.test(identities)) {
        return {safe: false, reason: 'protected-dialog'};
    }
    // File inputs can be hidden but still belong to an active upload dialog.
    if ([...popup.querySelectorAll('input, textarea, select, [contenteditable="true"]')]
            .some(node => node.matches('input[type="file"]') || visible(node))) {
        return {safe: false, reason: 'active-form'};
    }
    const incidental = /公告|通知|提醒|活动|广告|推广|福利|限时|新功能|新手|欢迎|升级|教程|引导|体验|定制开发|营销|咨询/.test(text + ' ' + labels);
    const imageOnly = !!popup.querySelector('img') &&
        /^(?:关闭(?:弹窗|窗口|广告)?|close|dismiss|[×✕✖✗x])?$/i.test(text.replace(/\s+/g, ''));
    if (!incidental && !imageOnly) return {safe: false, reason: 'unknown-dialog'};
    // The displayed close control must belong to the popup and be unobstructed.
    const box = el.getBoundingClientRect();
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    if (!hit || !(hit === el || el.contains(hit) || hit.contains(el))) {
        return {safe: false, reason: 'close-obstructed'};
    }
    if (!popup.dataset.douyinPopupGuard) {
        popup.dataset.douyinPopupGuard = `popup-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    return {safe: true, root: popup.dataset.douyinPopupGuard,
            label: closeLabel || '弹窗关闭图标', title: text.slice(0, 100) || '图片推广弹窗'};
}"""


def close_controls(scope: Page | Frame) -> Locator:
    visible = scope.locator(":visible")
    selectors = ", ".join(selector.strip() + ":visible" for selector in CLOSE_SELECTOR.split(","))
    return scope.locator(selectors).or_(
        scope.get_by_role("button", name=CLOSE_TEXT).and_(visible)
    ).or_(scope.get_by_text(CLOSE_TEXT).and_(visible))


class PopupGuard:
    """Task-scoped overlay handling, shared by source pages and Douyin pages."""

    def __init__(self, log: Callable[[str], None] | None = None,
                 stopped: Callable[[], bool] | None = None, max_dismissals: int = 20,
                 allowed_domains: tuple[str, ...] = (
                     "jinritemai.com", "taobao.com", "tmall.com", "vvic.com", "17zwd.com",
                 )):
        self.log = log or (lambda message: None)
        self.stopped = stopped or (lambda: False)
        self.max_dismissals = max_dismissals
        self.allowed_domains = allowed_domains
        self.closed_count = 0
        self.active = False
        self._handling = False
        self._context: BrowserContext | None = None
        self._handlers: list[tuple[Page, Locator]] = []
        self._pages: set[Page] = set()
        self._installation_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._failures: set[str] = set()

    def _allowed(self, page: Page) -> bool:
        if page.url in {"about:blank", "about:srcdoc"}:
            return True
        host = urlparse(page.url).hostname or ""
        return any(host == domain or host.endswith("." + domain) for domain in self.allowed_domains)

    def _schedule(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def finished(result: asyncio.Task) -> None:
            self._tasks.discard(result)
            if not result.cancelled() and result.exception() and self.active:
                self.log("弹窗处理器安装失败，当前页面保留给人工处理")

        task.add_done_callback(finished)

    def _new_page(self, page: Page) -> None:
        if self.active:
            self._schedule(self.install(page))

    async def start(self, context: BrowserContext) -> None:
        self._context = context
        self.active = True
        context.on("page", self._new_page)
        for page in context.pages:
            await self.install(page)

    async def install(self, page: Page) -> None:
        async with self._installation_lock:
            if not self.active or page.is_closed() or page in self._pages:
                return

            async def on_overlay(_locator: Locator) -> None:
                await self.dismiss(page)

            # Playwright handler triggers belong to the main frame. The iframe
            # trigger lets the same guarded inspection handle child-frame ads.
            for trigger in (close_controls(page).first, page.locator("iframe:visible").first):
                await page.add_locator_handler(trigger, on_overlay, no_wait_after=True)
                self._handlers.append((page, trigger))
            self._pages.add(page)
            _GUARDS[page] = self

    async def dismiss(self, page: Page) -> int:
        if (not self.active or self._handling or page.is_closed() or self.stopped()
                or not self._allowed(page) or self.closed_count >= self.max_dismissals):
            return 0
        self._handling = True
        dismissed = 0
        try:
            for _ in range(min(3, self.max_dismissals - self.closed_count)):
                closed = False
                for frame in page.frames:
                    if self.stopped():
                        return dismissed
                    candidates = close_controls(frame)
                    try:
                        count = min(await candidates.count(), 30)
                    except Exception:
                        continue
                    for index in range(count):
                        candidate = candidates.nth(index)
                        info = {}
                        try:
                            info = await candidate.evaluate(POPUP_CONTEXT, timeout=1000)
                            if not info.get("safe") or info["root"] in self._failures:
                                continue
                            root = frame.locator(f'[data-douyin-popup-guard="{info["root"]}"]')
                            await candidate.click(timeout=1200)
                            await root.wait_for(state="hidden", timeout=1200)
                        except Exception:
                            if info.get("safe"):
                                self._failures.add(info["root"])
                                self.log(f"弹窗关闭未确认，保留给人工处理：{info['label']}")
                            continue
                        self.closed_count += 1
                        dismissed += 1
                        self.log(f"已自动关闭弹窗（{info['label']}），确认消失后继续原步骤")
                        closed = True
                        break
                    if closed:
                        break
                if not closed:
                    break
            return dismissed
        finally:
            self._handling = False

    async def stop(self) -> None:
        self.active = False
        if self._context:
            self._context.remove_listener("page", self._new_page)
        for page in self._pages:
            if _GUARDS.get(page) is self:
                del _GUARDS[page]
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        for page, trigger in self._handlers:
            if not page.is_closed():
                try:
                    await page.remove_locator_handler(trigger)
                except Exception:
                    pass
        self._handlers.clear()
        self._pages.clear()


async def dismiss_popups(scope) -> int:
    """Also handle overlays during polling, which does not trigger action handlers."""
    page = scope.page if isinstance(scope, (Locator, Frame)) else scope
    guard = _GUARDS.get(page)
    return await guard.dismiss(page) if guard else 0
