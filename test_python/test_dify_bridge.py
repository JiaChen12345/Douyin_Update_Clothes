import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path

import httpx
import yaml
from pydantic import ValidationError
from playwright.async_api import async_playwright

from dify_bridge.api import Settings, create_app
from dify_bridge.models import CreateTask, NeedsHuman, Region, STAGES, VisionResult
from dify_bridge.service import TaskManager
from dify_bridge.vision import Snapshot
from dify.build_workflows import FINAL_CODE, VISION_CODE, build_workflow
from popup_guard_demo import find_edge


KEY = "test-only-browser-key-" + "a" * 40


class FakeRunner:
    def __init__(self, task, log, request_target):
        self.calls = []
        self.closed = False
        self.pause = False

    async def run_stage(self, stage):
        self.calls.append(stage)
        await asyncio.sleep(.03)
        if self.pause:
            self.pause = False
            raise NeedsHuman("请人工关闭当前弹窗")
        return {"stage": stage, "simulation": True}

    async def close(self):
        self.closed = True


class BridgeAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.app = create_app(Settings(KEY, work_dir=self.folder), FakeRunner)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url="http://bridge.test",
                                        headers={"X-Browser-Key": KEY})
        self.manager = self.app.state.manager

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.temporary.cleanup()

    async def create(self, **values):
        payload = {"request_id": uuid.uuid4().hex, "mode": "demo", **values}
        response = await self.client.post("/tasks", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["task_id"]

    async def test_authentication_and_health(self):
        response = await self.client.post("/tasks", headers={"X-Browser-Key": "wrong"}, json={"request_id": "request-123"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(KEY, response.text)
        self.assertEqual((await self.client.get("/health")).status_code, 200)

    async def test_create_is_idempotent_and_payload_cannot_change(self):
        payload = {"request_id": "same-request-123", "mode": "demo"}
        first = await self.client.post("/tasks", json=payload)
        second = await self.client.post("/tasks", json=payload)
        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        changed = await self.client.post("/tasks", json={**payload, "vision_enabled": True})
        self.assertEqual(changed.status_code, 409)

    async def test_platform_live_gate_and_unknown_fields(self):
        for data, status in (({"platform": "淘宝"}, 422), ({"mode": "live"}, 403), ({"allow_live": True}, 422)):
            response = await self.client.post("/tasks", json={"request_id": uuid.uuid4().hex, **data})
            self.assertEqual(response.status_code, status, response.text)
        self.assertIsNone(self.manager.owner)

    async def test_live_requires_supported_source_and_local_directory(self):
        self.manager.allow_live = True
        for data in ({"source_url": "https://example.com/item"},
                     {"source_url": "https://item.taobao.com/item.htm?id=1"},
                     {"source_url": "https://item.taobao.com/item.htm?id=1", "save_directory": "relative"}):
            response = await self.client.post("/tasks", json={"request_id": uuid.uuid4().hex, "mode": "live", **data})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.manager.tasks), 0)

    async def test_step_order_concurrent_retry_and_browser_lease(self):
        identity = await self.create()
        wrong = await self.client.post(f"/tasks/{identity}/advance", json={"stage": "save_draft"})
        self.assertEqual(wrong.status_code, 409)
        await asyncio.gather(*[self.client.post(f"/tasks/{identity}/advance", json={"stage": "prepare"}) for _ in range(3)])
        report = await self.manager.poll(identity, 2)
        self.assertEqual(report["status"], "IDLE")
        runner = self.manager.get(identity).runner
        self.assertEqual(runner.calls, ["prepare"])
        await self.client.post(f"/tasks/{identity}/advance", json={"stage": "prepare"})
        self.assertEqual(runner.calls, ["prepare"])
        second = await self.create()
        busy = await self.client.post(f"/tasks/{second}/advance", json={"stage": "prepare"})
        self.assertEqual(busy.status_code, 409)
        await self.client.post(f"/tasks/{identity}/cancel")
        self.assertTrue(runner.closed)
        self.assertEqual((await self.client.post(f"/tasks/{second}/advance", json={"stage": "prepare"})).status_code, 200)

    async def test_cloud_poll_resume_does_not_restart_running_step(self):
        identity = await self.create()
        await self.client.post(f"/tasks/{identity}/advance", json={"stage": "prepare"})
        await self.client.post(f"/tasks/{identity}/resume")
        await self.manager.poll(identity, 2)
        self.assertEqual(self.manager.get(identity).runner.calls, ["prepare"])

    async def test_human_pause_resume_and_complete_protocol(self):
        identity = await self.create()
        await self.manager.advance(identity, "prepare")
        await self.manager.poll(identity, 2)
        task = self.manager.get(identity)
        runner = task.runner
        runner.pause = True
        await self.manager.advance(identity, "open_creation")
        self.assertEqual((await self.manager.poll(identity, 2))["status"], "WAITING_HUMAN")
        await self.manager.resume(identity)
        for stage, _ in STAGES[1:]:
            await self.manager.advance(identity, stage)
            await self.manager.poll(identity, 2)
        report = self.manager.report(task)
        self.assertEqual(report["status"], "SUCCESS")
        self.assertTrue(report["simulation"])
        self.assertTrue(runner.closed)
        self.assertEqual(runner.calls.count("prepare"), 1)
        record = (task.folder / "task.json").read_text(encoding="utf-8")
        self.assertNotIn(KEY, record)
        restored = TaskManager(self.folder, self.folder)
        self.assertEqual(restored.get(identity).status, "SUCCESS")

    async def test_restart_never_replays_unknown_inflight_work(self):
        identity = await self.create()
        await self.manager.advance(identity, "prepare")
        restored = TaskManager(self.folder, self.folder)
        self.assertEqual(restored.get(identity).status, "INTERRUPTED")
        with self.assertRaisesRegex(RuntimeError, "重启"):
            await restored.resume(identity)

    async def test_cancel_before_worker_start_releases_owner(self):
        identity = await self.create()
        await self.manager.advance(identity, "prepare")
        await self.manager.cancel(identity)
        self.assertIsNone(self.manager.owner)
        self.assertEqual(self.manager.get(identity).status, "CANCELLED")

    async def test_no_arbitrary_screenshot_file_read(self):
        identity = await self.create()
        response = await self.client.get(f"/tasks/{identity}/screenshots/{'a'*32}.png")
        self.assertEqual(response.status_code, 404)


class WorkflowContractTests(unittest.TestCase):
    def test_no_model_workflow_and_vision_file_binding(self):
        plain = build_workflow()
        enhanced = build_workflow(True)
        self.assertFalse(any(n["data"]["type"] == "llm" for n in plain["workflow"]["graph"]["nodes"]))
        for document in (plain, enhanced):
            document = yaml.safe_load(yaml.safe_dump(document, allow_unicode=True))
            graph = document["workflow"]["graph"]
            identities = {node["id"] for node in graph["nodes"]}
            self.assertEqual(len(identities), len(graph["nodes"]))
            for edge in graph["edges"]:
                self.assertIn(edge["source"], identities)
                self.assertIn(edge["target"], identities)
            self.assertEqual(document["workflow"]["environment_variables"][1]["value_type"], "secret")
            for node in graph["nodes"]:
                if node["data"]["type"] == "http-request":
                    self.assertIn("{{#env.BROWSER_API_KEY#}}", node["data"]["headers"])
            vision = [node for node in graph["nodes"] if node["data"]["type"] == "llm"]
            if vision:
                self.assertEqual(vision[0]["data"]["vision"]["configs"]["variable_selector"], ["image", "files"])

    def test_model_output_is_strict_and_snapshot_id_is_not_chosen_by_model(self):
        namespace = {}
        exec(VISION_CODE, namespace)
        result = namespace["main"]('{"found":true,"target":"关闭","x":12,"y":8,"confidence":0.9}', "a"*32, "关闭")
        self.assertEqual(json.loads(result["body"])["screenshot_id"], "a"*32)
        with self.assertRaises(ValueError):
            namespace["main"]('{"found":true,"target":"其他","x":12,"y":8,"confidence":0.9}', "a"*32, "关闭")
        with self.assertRaises(ValueError):
            namespace["main"]('{"found":"true","target":"关闭","x":12,"y":8,"confidence":0.9}', "a"*32, "关闭")

    def test_running_and_simulated_results_never_claim_real_success(self):
        namespace = {}
        exec(FINAL_CODE, namespace)
        running = namespace["main"](json.dumps({"status": "RUNNING", "simulation": False, "task_id": "a"*32}))
        self.assertIn("后端任务仍可查询", running["summary"])
        demo = namespace["main"](json.dumps({"status": "SUCCESS", "simulation": True, "message": "演示完成"}))
        self.assertIn("无真实商品", demo["summary"])

    def test_vision_input_validation(self):
        base = {"screenshot_id": "a"*32, "found": True, "target": "下一步", "x": 10, "y": 20, "confidence": .9}
        for change in ({"found": "true"}, {"confidence": float("nan")}, {"x": float("inf")}):
            with self.assertRaises(ValidationError):
                VisionResult.model_validate({**base, **change})
        with self.assertRaises(ValidationError):
            Region(x=.8, y=0, width=.3, height=.5, normalized=True)


class VisionBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(executable_path=str(find_edge()), headless=True)
        self.context = await self.browser.new_context(viewport={"width": 800, "height": 600}, device_scale_factor=2)
        self.page = await self.context.new_page()
        await self.page.set_content('<body style="margin:0"><button id="target" style="position:absolute;left:120px;top:110px;width:100px;height:50px" onclick="document.body.dataset.clicked=\'yes\'">下一步</button></body>')

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()
        self.temporary.cleanup()

    async def test_crop_coordinates_at_double_device_scale_and_single_consumption(self):
        snapshot = await Snapshot.create(self.page, self.folder, "下一步", Region(x=100, y=80, width=200, height=160))
        target = await snapshot.resolve(VisionResult(screenshot_id=snapshot.id, found=True, target="下一步", x=70, y=55, confidence=.95))
        await target.click()
        self.assertEqual(await self.page.locator("body").get_attribute("data-clicked"), "yes")
        with self.assertRaises(NeedsHuman):
            await target.click()

    async def test_stale_region_wrong_target_and_business_submit_are_rejected(self):
        snapshot = await Snapshot.create(self.page, self.folder, "下一步", None)
        with self.assertRaises(NeedsHuman):
            await snapshot.resolve(VisionResult(screenshot_id=snapshot.id, found=True, target="其他", x=170, y=135, confidence=.95))
        await self.page.locator("#target").evaluate("el => el.style.left='220px'")
        with self.assertRaisesRegex(NeedsHuman, "改变"):
            await snapshot.resolve(VisionResult(screenshot_id=snapshot.id, found=True, target="下一步", x=170, y=135, confidence=.95))
        await self.page.locator("#target").evaluate("el => el.textContent='确认发布'")
        protected = await Snapshot.create(self.page, self.folder, "确认发布", None)
        with self.assertRaises(NeedsHuman):
            await protected.resolve(VisionResult(screenshot_id=protected.id, found=True, target="确认发布", x=270, y=135, confidence=.95))
        self.assertIsNone(await self.page.locator("body").get_attribute("data-clicked"))

    async def test_http_image_transfer_and_recognition_callback_resume_one_click(self):
        page = self.page

        class VisionRunner:
            def __init__(self, task, log, request_target):
                self.request_target = request_target

            async def run_stage(self, stage):
                target = await self.request_target(page, "下一步")
                await target.click()
                if await page.locator("body").get_attribute("data-clicked") != "yes":
                    raise RuntimeError("点击后置条件未满足")
                return {"verified": True}

            async def close(self):
                pass

        app = create_app(Settings(KEY, work_dir=self.folder / "tasks"), VisionRunner)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://bridge.test",
                                        headers={"X-Browser-Key": KEY}) as client:
                response = await client.post("/tasks", json={"request_id": uuid.uuid4().hex,
                    "vision_enabled": True, "roi": {"x": 100, "y": 80, "width": 200, "height": 160}})
                identity = response.json()["task_id"]
                await client.post(f"/tasks/{identity}/advance", json={"stage": "prepare"})
                report = await app.state.manager.poll(identity, 3)
                self.assertEqual(report["status"], "NEEDS_VISION")
                screenshot_id = report["vision"]["screenshot_id"]
                png = await client.get(f"/tasks/{identity}/screenshots/{screenshot_id}.png")
                self.assertEqual(png.status_code, 200)
                self.assertTrue(png.headers["content-type"].startswith("image/png"))
                self.assertTrue(png.content.startswith(b"\x89PNG\r\n\x1a\n"))
                payload = {"screenshot_id": screenshot_id, "found": True, "target": "下一步",
                           "x": 70, "y": 55, "confidence": .95}
                accepted = await client.post(f"/tasks/{identity}/vision", json=payload)
                self.assertTrue(accepted.json()["accepted"])
                report = await app.state.manager.poll(identity, 3)
                self.assertEqual(report["status"], "IDLE")
                self.assertTrue(report["results"]["prepare"]["verified"])
                repeated = await client.post(f"/tasks/{identity}/vision", json=payload)
                self.assertEqual(repeated.status_code, 409)


if __name__ == "__main__":
    unittest.main()
