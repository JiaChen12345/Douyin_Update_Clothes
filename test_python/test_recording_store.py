import json
import tempfile
import unittest
from pathlib import Path

from recording_models import LocatorSpec, RecordingProfile, RecordingStep
from recording_store import RecordingStore


def sample(site="vvic", category="tshirt"):
    return RecordingProfile(site=site, category=category, steps=[
        RecordingStep("s001", "上传主图", "upload", variable="main_images", critical=True),
        RecordingStep("s002", "保存草稿", "save_draft", LocatorSpec(text="保存草稿"), critical=True),
    ])


class RecordingStoreTests(unittest.TestCase):
    def test_roundtrip_and_status(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RecordingStore(Path(temp))
            path = store.save(sample())
            self.assertTrue(path.is_file())
            self.assertEqual(store.load("vvic", "tshirt").steps[0].variable, "main_images")
            self.assertTrue(store.status("vvic", "tshirt").compatible)

    def test_user_recording_precedes_bundled(self):
        with tempfile.TemporaryDirectory() as bundled, tempfile.TemporaryDirectory() as user:
            bundled_store = RecordingStore(Path(bundled))
            base = sample(); base.recorded_at = "old"; bundled_store.save(base)
            store = RecordingStore(Path(bundled), Path(user))
            newer = sample(); newer.recorded_at = "new"; store.save(newer)
            self.assertEqual(store.load("vvic", "tshirt").recorded_at, "new")

    def test_corrupt_recording_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "vvic_tshirt.json").write_text(json.dumps({"site": "vvic"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                RecordingStore(root).load("vvic", "tshirt")

    def test_cancelled_temp_does_not_replace_old(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RecordingStore(Path(temp))
            old = store.save(sample()).read_bytes()
            (Path(temp) / "vvic_tshirt.json.tmp").write_text("broken", encoding="utf-8")
            self.assertEqual((Path(temp) / "vvic_tshirt.json").read_bytes(), old)


if __name__ == "__main__":
    unittest.main()
