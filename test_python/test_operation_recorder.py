import unittest

from operation_recorder import OperationRecorder, is_forbidden_action


class OperationRecorderTests(unittest.TestCase):
    def test_publish_is_blocked_but_draft_is_allowed(self):
        self.assertTrue(is_forbidden_action("确认发布"))
        self.assertTrue(is_forbidden_action("立即发布商品"))
        self.assertFalse(is_forbidden_action("发布商品"))
        self.assertFalse(is_forbidden_action("发布新款"))
        self.assertFalse(is_forbidden_action("保存草稿"))

    def test_input_events_merge_and_title_becomes_variable(self):
        locator = {"text": "", "label": "商品标题", "attributes": {"name": "title"}}
        steps = OperationRecorder.standardize([
            {"action": "fill", "locator": locator, "value": "旧"},
            {"action": "fill", "locator": locator, "value": "示范标题"},
            {"action": "save_draft", "locator": {"text": "保存草稿"}},
        ])
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].variable, "title")
        self.assertIsNone(steps[0].value)

    def test_upload_defaults_to_dynamic_main_images(self):
        steps = OperationRecorder.standardize([
            {"action": "upload", "locator": {"attributes": {"type": "file"}}, "value": ["demo.jpg"]}
        ])
        self.assertEqual(steps[0].variable, "main_images")
        self.assertNotEqual(steps[0].value, r"C:\\demo.jpg")

    def test_upload_sections_map_to_task_materials(self):
        events = [
            {"action": "upload", "locator": {"text": "主图视频"}, "value": ["demo.mp4"]},
            {"action": "upload", "locator": {"text": "上传吊牌"}, "value": ["demo.jpg"]},
            {"action": "upload", "locator": {"text": "快捷生成详情页"}, "value": ["demo.jpg"]},
        ]
        self.assertEqual([step.variable for step in OperationRecorder.standardize(events)],
                         ["videos", "wash_label", "detail_images"])

    def test_file_input_fill_event_is_ignored(self):
        steps = OperationRecorder.standardize([
            {"action": "fill", "locator": {"attributes": {"type": "file"}}, "value": r"C:\\fakepath\\demo.jpg"}
        ])
        self.assertEqual(steps, [])


if __name__ == "__main__":
    unittest.main()
