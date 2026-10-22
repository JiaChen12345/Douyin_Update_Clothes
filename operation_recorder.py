"""Browser-side semantic operation recorder for marketplace draft forms."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from recording_models import LocatorSpec, RecordingProfile, RecordingStep, ValidationSpec
from recording_store import RecordingStore

FORBIDDEN_ACTIONS = ("确认发布", "立即发布", "正式发布", "提交审核", "发布")
ALLOWED_ACTIONS = ("保存草稿", "存草稿")
ENTRY_ACTIONS = ("发布商品", "发布新款")
VARIABLE_VALUES = {
    "商品标题": "title",
    "主图": "main_images",
    "商品主图": "main_images",
    "视频": "videos",
    "详情图": "detail_images",
    "商品详情": "detail_images",
    "吊牌": "wash_label",
    "洗水标": "wash_label",
}


def is_forbidden_action(text: str) -> bool:
    normalized = "".join(str(text or "").split())
    if any(word in normalized for word in ALLOWED_ACTIONS):
        return False
    if any(normalized == word for word in ENTRY_ACTIONS):
        return False
    return any(word in normalized for word in FORBIDDEN_ACTIONS)


RECORDER_SCRIPT = r"""
(() => {
  if (window.__draftRecorder) return;
  const state = {active: false, paused: false, events: [], lastScroll: 0};
  const clean = value => (value || '').toString().replace(/\s+/g, ' ').trim().slice(0, 200);
  const targetInfo = el => {
    const rect = el.getBoundingClientRect();
    const attrs = {};
    ['id','name','type','multiple','accept','placeholder','data-testid','data-id','aria-label'].forEach(k => {
      const v = el.getAttribute && el.getAttribute(k); if (v) attrs[k] = clean(v);
    });
    const ancestors = [];
    let parent = el.parentElement;
    for (let i=0; parent && i<3; i++, parent=parent.parentElement) {
      ancestors.push({tag: parent.tagName.toLowerCase(), text: clean(parent.innerText).slice(0,80), id: parent.id || ''});
    }
    const label = el.labels && el.labels[0] ? clean(el.labels[0].innerText) : '';
    return {role: el.getAttribute('role') || '', name: clean(el.getAttribute('aria-label')),
      text: clean(el.innerText || el.value), label, attributes: attrs, ancestors,
      x_ratio: (rect.left + rect.width/2) / Math.max(innerWidth,1),
      y_ratio: (rect.top + rect.height/2) / Math.max(innerHeight,1)};
  };
  const push = (action, el, value='') => {
    if (!state.active || state.paused) return;
    const payload={action, locator: targetInfo(el), value, url: location.href, at: Date.now()};
    state.events.push(payload);
    if (window.__recordDraftEvent) window.__recordDraftEvent(payload);
  };
  document.addEventListener('click', e => {
    const text = clean(e.target.closest('button,a,[role=button]')?.innerText || e.target.innerText);
    const allowed = text.includes('保存草稿') || text.includes('存草稿');
    const entry = text === '发布商品' || text === '发布新款';
    const forbidden = !allowed && !entry && /(确认发布|立即发布|正式发布|提交审核|^发布$)/.test(text);
    if (forbidden) { e.preventDefault(); e.stopImmediatePropagation(); push('blocked', e.target, text); return; }
    push(text.includes('保存草稿') ? 'save_draft' : 'click', e.target);
  }, true);
  document.addEventListener('input', e => push('fill', e.target, e.target.value || ''), true);
  document.addEventListener('change', e => {
    if (e.target.type === 'file') push('upload', e.target, Array.from(e.target.files || []).map(f=>f.name));
    else push('select', e.target, e.target.value || (e.target.checked ? 'checked' : ''));
  }, true);
  document.addEventListener('scroll', e => {
    const now=Date.now(); if (now-state.lastScroll<250) return; state.lastScroll=now;
    push('scroll', e.target === document ? document.documentElement : e.target, {x:scrollX,y:scrollY});
  }, true);
  window.__draftRecorder = {
    start(){state.active=true;state.paused=false;state.events=[]}, pause(){state.paused=true},
    resume(){state.paused=false}, stop(){state.active=false}, drain(){return state.events.splice(0)},
    status(){return {active:state.active,paused:state.paused,count:state.events.length}}
  };
})();
"""


@dataclass
class RecorderSnapshot:
    state: str
    step_count: int
    latest: str = ""


class OperationRecorder:
    def __init__(self, store: RecordingStore, notify: Callable[[str], None] | None = None):
        self.store = store
        self.notify = notify or (lambda _message: None)
        self.page = None
        self.site = ""
        self.category = ""
        self.state = "idle"
        self.raw_events: list[dict[str, Any]] = []

    async def start(self, page, site: str, category: str) -> None:
        RecordingStore.filename(site, category)
        self.page, self.site, self.category = page, site, category
        self.raw_events = []
        async def receive(_source, payload):
            if self.state == "recording":
                self.raw_events.append(payload)
        await page.context.expose_binding("__recordDraftEvent", receive)
        await page.context.add_init_script(RECORDER_SCRIPT + "\nwindow.__draftRecorder.start();")
        await page.evaluate(RECORDER_SCRIPT)
        await page.evaluate("window.__draftRecorder.start()")
        self.state = "recording"

    async def pause(self) -> None:
        self._require("recording")
        await self._drain(); await self.page.evaluate("window.__draftRecorder.pause()")
        self.state = "paused"

    async def resume(self) -> None:
        self._require("paused")
        await self.page.evaluate("window.__draftRecorder.resume()")
        self.state = "recording"

    async def cancel(self) -> None:
        if self.page and self.state in {"recording", "paused"}:
            await self.page.evaluate("window.__draftRecorder.stop()")
        self.raw_events = []; self.state = "idle"

    async def finish(self) -> RecordingProfile:
        if self.state not in {"recording", "paused"}:
            raise RuntimeError("当前没有正在进行的录制")
        await self._drain(); await self.page.evaluate("window.__draftRecorder.stop()")
        profile = RecordingProfile(self.site, self.category, self.standardize(self.raw_events),
                                   viewport=await self.page.evaluate("({width:innerWidth,height:innerHeight})"),
                                   page_markers=[await self.page.title()])
        profile.validate()
        self.store.save(profile); self.state = "idle"
        return profile

    async def snapshot(self) -> RecorderSnapshot:
        await self._drain()
        latest = self.raw_events[-1].get("action", "") if self.raw_events else ""
        return RecorderSnapshot(self.state, len(self.standardize(self.raw_events)), latest)

    async def _drain(self) -> None:
        # Events are streamed through the context binding so navigation cannot
        # discard them. Yield once to let pending binding callbacks complete.
        await asyncio.sleep(0)

    def _require(self, state: str) -> None:
        if self.state != state:
            raise RuntimeError(f"录制状态不允许此操作：{self.state}")

    @staticmethod
    def standardize(events: list[dict[str, Any]]) -> list[RecordingStep]:
        result: list[RecordingStep] = []
        for event in events:
            action = event.get("action", "")
            if action == "blocked":
                continue
            locator = LocatorSpec(**{k: event.get("locator", {}).get(k) for k in LocatorSpec.__dataclass_fields__
                                     if event.get("locator", {}).get(k) is not None})
            ancestor_text = " ".join(str(item.get("text", "")) for item in locator.ancestors)
            text = " ".join(filter(None, [locator.label, locator.name, locator.text, ancestor_text]))
            if action == "fill" and locator.attributes.get("type") == "file":
                continue
            variable = infer_upload_variable(text) if action == "upload" else next(
                (value for key, value in VARIABLE_VALUES.items() if key in text), "")
            value = event.get("value")
            if action == "fill" and variable == "title":
                value = None
            if action == "upload":
                variable = variable or "main_images"
                value = None
            if result and action == "fill" and result[-1].action == "fill" and result[-1].locator == locator:
                result[-1].value = value; continue
            if result and action == "scroll" and result[-1].action == "scroll":
                result[-1].value = value; continue
            critical = action == "save_draft" or (action == "upload" and variable == "main_images") or any(word in text for word in ENTRY_ACTIONS)
            validation = ValidationSpec("draft_saved" if action == "save_draft" else "upload_changed" if action == "upload" else "none")
            result.append(RecordingStep(f"s{len(result)+1:03d}", text[:60] or action, action, locator,
                                        value, variable, critical, 2, 15000, validation))
        return result


def infer_upload_variable(context: str) -> str:
    """Classify a file input by its surrounding form section, never its demo filename."""
    text = "".join(str(context or "").split()).lower()
    if any(word in text for word in ("详情信息", "商品详情", "快捷生成详情页", "详情图")):
        return "detail_images"
    if any(word in text for word in ("上传吊牌", "吊牌图", "洗水标", "耐久性标签")):
        return "wash_label"
    if any(word in text for word in ("主图视频", "上传视频")) or "video" in text:
        return "videos"
    if any(word in text for word in ("1:1主图", "商品主图", "封面图", "白底图", "上传图片")):
        return "main_images"
    return ""
