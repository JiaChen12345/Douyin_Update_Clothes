import tempfile
import unittest
from pathlib import Path

from operation_recorder import is_forbidden_action
from recording_failures import FailureTracker


class OperationReplayerTests(unittest.TestCase):
    def test_failure_threshold_and_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = FailureTracker(Path(temp) / "state.json")
            self.assertIsNone(tracker.failure("zwd17", "knitwear", "s003", "missing"))
            self.assertIsNone(tracker.failure("zwd17", "knitwear", "s003", "missing"))
            self.assertEqual(tracker.failure("zwd17", "knitwear", "s003", "missing")["count"], 3)
            tracker.success("zwd17", "knitwear", "s003")
            self.assertIsNone(tracker.failure("zwd17", "knitwear", "s003", "missing"))

    def test_security_rule(self):
        self.assertTrue(is_forbidden_action("提交审核"))
        self.assertFalse(is_forbidden_action("保存草稿 2"))


if __name__ == "__main__":
    unittest.main()
