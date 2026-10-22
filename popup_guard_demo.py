"""Visible, local-only demonstration of closing popups and resuming old steps."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

from douyin_automation_dual_store_v49 import click_text
from popup_guard import PopupGuard


DEMO_HTML = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>抖店流程：弹窗关闭实时演示（本地测试）</title><style>
body{font-family:'Microsoft YaHei',sans-serif;margin:0;background:#f3f5f9;color:#20314c}
main{max-width:850px;margin:50px auto;background:white;padding:36px;border-radius:14px}
h1{font-size:25px}p{line-height:1.9}button{border:0;border-radius:8px;padding:13px 24px;background:#245ee8;color:white;font:inherit;cursor:pointer}
#status{font-size:18px;color:#087741}li{padding:10px}
.overlay{position:fixed;inset:0;background:#10182888;z-index:100;display:flex;align-items:center;justify-content:center}
.popup-card{position:relative;width:570px;background:white;border-radius:14px;padding:36px;text-align:center;box-shadow:0 20px 80px #10182866}
.poster{background:linear-gradient(130deg,#ecf5ff,#b8d7ff);padding:55px 25px;border-radius:10px;font-size:32px;color:#1559bf}
.bottom-close{margin-top:22px;background:#69758b}.arco-modal-close-btn{position:absolute;right:12px;top:10px;background:transparent;color:#49566d;font-size:26px;padding:3px 9px}
</style><main><h1>弹窗关闭 → 继续原操作</h1><p>这是本地浏览器演示，不连接店铺，不上传或发布商品。</p>
<ol><li id="one">步骤 1：处理图片推广弹窗，继续“下一步”</li><li id="two">步骤 2：处理右上角 × 弹窗，继续“保存草稿”模拟按钮</li></ol>
<button id="next" onclick="document.getElementById('one').textContent='✓ 步骤 1 已继续执行'">下一步</button>
<button id="draft" onclick="document.getElementById('two').textContent='✓ 步骤 2 已继续执行';document.getElementById('status').textContent='演示完成：两个弹窗已关闭，后续操作已执行。'">保存草稿（模拟）</button>
<p id="status">等待演示开始</p></main>
<script>
function showPopup(kind){
 const overlay=document.createElement('div');overlay.className='overlay';overlay.id='overlay';
 const close=kind==='bottom'?'<button class="bottom-close" onclick="document.getElementById(\'overlay\').remove()">关闭</button>':'<button class="arco-modal-close-btn" aria-label="关闭" onclick="document.getElementById(\'overlay\').remove()">×</button>';
 overlay.innerHTML='<div class="popup-card" role="dialog"><div class="poster">欢迎体验新功能</div><p>图片推广与新功能介绍</p>'+close+'</div>';
 document.body.appendChild(overlay);
}
</script></html>"""


def find_edge() -> Path:
    for base in (os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", "")):
        executable = Path(base) / "Microsoft/Edge/Application/msedge.exe"
        if executable.is_file():
            return executable
    raise RuntimeError("演示需要已安装 Microsoft Edge")


async def run_demo(headless: bool = False, keep_open: bool = True, output: Path | None = None) -> None:
    if output:
        output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(find_edge()), headless=headless)
        options = {"viewport": {"width": 1200, "height": 820}}
        if output:
            options["record_video_dir"] = str(output)
            options["record_video_size"] = {"width": 1200, "height": 820}
        context = await browser.new_context(**options)
        guard = PopupGuard(print)
        await guard.start(context)
        page = await context.new_page()
        await guard.install(page)
        await page.set_content(DEMO_HTML)
        try:
            for index, (kind, target) in enumerate((
                ("bottom", "下一步"), ("cross", "保存草稿（模拟）"),
            ), start=1):
                await page.evaluate("kind => showPopup(kind)", kind)
                if output:
                    await page.screenshot(path=str(output / f"{index}-弹窗出现.png"))
                await asyncio.sleep(.5 if headless else 2)
                await click_text(page, [target], timeout_ms=5000)
                if await page.locator("#overlay").count():
                    raise RuntimeError("弹窗尚未消失，演示失败")
                await asyncio.sleep(.3 if headless else 1)
            if guard.closed_count != 2:
                raise RuntimeError(f"应关闭2个弹窗，实际关闭{guard.closed_count}个")
            result = await page.locator("#status").inner_text()
            if not result.startswith("演示完成"):
                raise RuntimeError(f"后续步骤未完成：{result}")
            print("POPUP_DEMO_OK: " + result, flush=True)
            if output:
                await page.screenshot(path=str(output / "3-继续执行成功.png"))
            if keep_open and not headless:
                print("演示页面保持打开；关闭该窗口结束演示。", flush=True)
                while not page.is_closed():
                    await asyncio.sleep(.5)
        finally:
            await guard.stop()
            await context.close()
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="弹窗关闭与继续执行的本地实时演示")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--exit", action="store_true", help="演示成功后退出")
    parser.add_argument("--output", type=Path, help="保存前后截图和浏览器录像")
    args = parser.parse_args()
    asyncio.run(run_demo(args.headless, not args.exit, args.output))


if __name__ == "__main__":
    main()
