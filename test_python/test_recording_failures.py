import tempfile
import unittest
from pathlib import Path

from recording_failures import FailureTracker


class FailureTrackerTests(unittest.TestCase):
    def test_third_failure_notifies_and_success_resets(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = FailureTracker(Path(temp) / "state.json")
            self.assertIsNone(tracker.failure("vvic", "tshirt", "s1", "x"))
            self.assertIsNone(tracker.failure("vvic", "tshirt", "s1", "x"))
            self.assertIsNotNone(tracker.failure("vvic", "tshirt", "s1", "x"))
            tracker.success("vvic", "tshirt", "s1")
            self.assertIsNone(tracker.failure("vvic", "tshirt", "s1", "x"))

    def test_clear_profile_keeps_other_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = FailureTracker(Path(temp) / "state.json", threshold=1)
            tracker.failure("vvic", "tshirt", "s1", "x")
            tracker.failure("zwd17", "tshirt", "s1", "x")
            tracker.clear_profile("vvic", "tshirt")
            self.assertNotIn("vvic:tshirt:s1", tracker.data["failures"])
            self.assertIn("zwd17:tshirt:s1", tracker.data["failures"])


if __name__ == "__main__":
    unittest.main()
