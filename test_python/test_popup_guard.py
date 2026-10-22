"""Real-browser checks for dismissing overlays while retaining workflow dialogs."""
import asyncio
import html
import os
import unittest
from pathlib import Path

from playwright.async_api import async_playwright

from douyin_automation_dual_store_v49 import click_text, first_visible
from popup_guard import PopupGuard


EDGE = next((path for path in (
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
) if path.is_file()), None)

STYLE = """<style>
body{font-family:Arial;margin:30px}button{padding:12px;cursor:pointer}
.overlay{position:fixed;inset:0;background:#0006;z-index:99;display:flex;align-items:center;justify-content:center}
.popup-card{position:relative;width:520px;min-height:180px;padding:30px;background:white;border-radius:10px}
.popup-card .arco-modal-close-btn{position:absolute;top:8px;right:8px}
</style>"""
BASE = STYLE + '<button id="next" onclick="document.getElementById(\'result\').textContent=\'continued\'">下一步</button><p id="result"></p>'


def overlay(text="欢迎体验新功能", label="关闭", identity="overlay", button_class="", body="", onclick=None):
    click = onclick if onclick is not None else f"document.getElementById('{identity}').remove()"
    return (f'<div class="overlay" id="{identity}"><div class="popup-card" role="dialog">'
            f'<header class="modal-header">{text}</header>{body}'
            f'<button class="{button_class}" onclick="{click}">{label}</button></div></div>')


@unittest.skipUnless(EDGE, "Microsoft Edge is required for popup integration checks")
class PopupGuardBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(executable_path=str(EDGE), headless=True)
        self.context = await self.browser.new_context(viewport={"width": 1000, "height": 720})
        self.messages = []
        self.guard = PopupGuard(self.messages.append)
        await self.guard.start(self.context)
        self.page = await self.context.new_page()
        await self.guard.install(self.page)

    async def asyncTearDown(self):
        await self.guard.stop()
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def test_text_close_then_original_step_continues(self):
        await self.page.set_content(BASE + overlay())
        await click_text(self.page, ["下一步"], timeout_ms=3000)
        self.assertEqual(await self.page.locator("#result").inner_text(), "continued")
        self.assertEqual(self.guard.closed_count, 1)
        self.assertIn("确认消失后继续原步骤", self.messages[0])

    async def test_top_right_icon_and_hidden_control(self):
        await self.page.set_content(BASE + '<button style="display:none">关闭</button>' +
                                    overlay(label="×", button_class="arco-modal-close-btn"))
        await self.page.get_by_role("button", name="下一步").click(timeout=3000)
        self.assertEqual(self.guard.closed_count, 1)
        self.assertEqual(await self.page.locator("#result").inner_text(), "continued")

    async def test_icon_with_no_text_uses_close_class(self):
        await self.page.set_content(BASE + overlay(label="", button_class="arco-modal-close-btn"))
        await click_text(self.page, ["下一步"], timeout_ms=3000)
        self.assertEqual(self.guard.closed_count, 1)

    async def test_popup_arrives_mid_workflow(self):
        await self.page.set_content(BASE)
        self.assertIsNotNone(await first_visible(self.page, ["下一步"]))
        await self.page.evaluate("markup => setTimeout(() => document.body.insertAdjacentHTML('beforeend', markup), 30)", overlay())
        await asyncio.sleep(.1)
        # This is a direct existing-style click, not an explicit dismissal call.
        await self.page.get_by_role("button", name="下一步").click(timeout=3000)
        self.assertEqual(self.guard.closed_count, 1)
        self.assertEqual(await self.page.locator("#result").inner_text(), "continued")

    async def test_stacked_popups_close_from_top(self):
        await self.page.set_content(BASE + overlay(identity="first") + overlay(identity="second"))
        await click_text(self.page, ["下一步"], timeout_ms=4000)
        self.assertEqual(self.guard.closed_count, 2)
        self.assertEqual(await self.page.locator("#result").inner_text(), "continued")

    async def test_protected_and_unknown_dialogs_stay_open(self):
        cases = (
            ("请扫码登录", ""), ("安全验证：请输入验证码", ""),
            ("确认发布商品", ""), ("是否放弃未保存内容", ""),
            ("本地上传图片", '<input type="file" style="display:none">'),
            ("欢迎体验新功能", '<input placeholder="商品标题">'),
            ("请选择素材", ""),
            ("欢迎体验新功能", '<p>确认删除商品，此操作不可恢复</p>'),
        )
        for title, body in cases:
            with self.subTest(title=title, body=body):
                await self.page.set_content(BASE + overlay(title, body=body))
                self.assertEqual(await self.guard.dismiss(self.page), 0)
                self.assertTrue(await self.page.locator("#overlay").is_visible())
        self.assertEqual(self.guard.closed_count, 0)

    async def test_workflow_input_inside_protected_dialog_still_works(self):
        await self.page.set_content(BASE + overlay("本地上传图片", body='<input aria-label="商品标题">'))
        await self.page.get_by_role("textbox", name="商品标题").fill("当前商品", timeout=3000)
        self.assertEqual(await self.page.get_by_role("textbox", name="商品标题").input_value(), "当前商品")
        self.assertEqual(self.guard.closed_count, 0)

    async def test_image_advertisement_with_bottom_close(self):
        image = '<img alt="" src="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'300\' height=\'120\'%3E%3Crect width=\'300\' height=\'120\' fill=\'blue\'/%3E%3C/svg%3E">'
        await self.page.set_content(BASE + overlay(text="", body=image))
        await click_text(self.page, ["下一步"], timeout_ms=3000)
        self.assertEqual(self.guard.closed_count, 1)

    async def test_normal_page_close_is_not_clicked(self):
        await self.page.set_content(BASE + '<button id="ordinary" onclick="this.remove()">关闭</button>')
        self.assertEqual(await self.guard.dismiss(self.page), 0)
        self.assertTrue(await self.page.locator("#ordinary").is_visible())

    async def test_close_without_disappearance_is_not_success(self):
        await self.page.set_content(BASE + overlay(onclick="void 0"))
        self.assertEqual(await self.guard.dismiss(self.page), 0)
        self.assertEqual(self.guard.closed_count, 0)
        self.assertTrue(await self.page.locator("#overlay").is_visible())
        self.assertTrue(any("未确认" in message for message in self.messages))

    async def test_iframe_popup(self):
        embedded = html.escape(BASE + overlay(), quote=True)
        await self.page.set_content(f'<iframe title="素材窗口" style="width:900px;height:600px" srcdoc="{embedded}"></iframe>')
        frame = self.page.frames[-1]
        await frame.get_by_role("button", name="下一步").click(timeout=4000)
        self.assertEqual(self.guard.closed_count, 1)
        self.assertEqual(await frame.locator("#result").inner_text(), "continued")

    async def test_stopped_guard_does_not_click(self):
        await self.page.set_content(BASE + overlay())
        self.guard.stopped = lambda: True
        self.assertEqual(await self.guard.dismiss(self.page), 0)
        self.assertTrue(await self.page.locator("#overlay").is_visible())

    async def test_dismissal_limit(self):
        self.guard.max_dismissals = 1
        await self.page.set_content(BASE + overlay(identity="first") + overlay(identity="second"))
        self.assertEqual(await self.guard.dismiss(self.page), 1)
        self.assertEqual(await self.guard.dismiss(self.page), 0)
        self.assertEqual(await self.page.locator(".overlay").count(), 1)

    async def test_other_domain_is_not_automatically_controlled(self):
        await self.page.route("https://example.com/**", lambda route: route.fulfill(body=BASE + overlay(), content_type="text/html"))
        await self.page.goto("https://example.com/")
        self.assertEqual(await self.guard.dismiss(self.page), 0)
        self.assertTrue(await self.page.locator("#overlay").is_visible())


if __name__ == "__main__":
    unittest.main()
