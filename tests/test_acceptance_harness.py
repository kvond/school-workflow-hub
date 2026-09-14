import json
from pathlib import Path
import tempfile
import unittest

from scripts.acceptance_preview import prepare_acceptance
from school_workflow_hub.core.database import SYNTHETIC_RUNTIME_ROOT


ROOT = Path(__file__).resolve().parents[1]


class AcceptanceHarnessTests(unittest.TestCase):
    def setUp(self):
        SYNTHETIC_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="acceptance-test-", dir=SYNTHETIC_RUNTIME_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "preview"

    def test_complete_packet_is_synthetic_replayable_and_keeps_live_tests_pending(self):
        first = prepare_acceptance(ROOT / "config/example.json", self.out)
        second = prepare_acceptance(ROOT / "config/example.json", self.out)
        self.assertEqual(first, second)
        self.assertEqual(first["real_system_acceptance"], "not_run")
        self.assertEqual(first["live_readers"], "not_configured")
        self.assertTrue(first["identical_rerun_no_duplicates"])
        self.assertEqual(first["external_writes"], 0)
        self.assertEqual(first["live_model_calls"], 0)
        self.assertEqual([row["number"] for row in first["tests"]], list(range(1, 9)))
        self.assertEqual({row["decision"] for row in first["tests"]}, {"pending"})
        self.assertEqual({row["result"] for row in first["tests"]}, {"not_run"})
        self.assertEqual({row["section"] for row in first["tests"]}, {"must_test", "if_time", "optional_canary"})
        self.assertTrue((self.out / "preview.json").is_file())
        self.assertTrue((self.out / "acceptance-checklist.json").is_file())

    def test_teacher_decisions_are_never_overwritten(self):
        prepare_acceptance(ROOT / "config/example.json", self.out)
        path = self.out / "acceptance-checklist.json"
        packet = json.loads(path.read_text())
        packet["tests"][0]["decision"] = "tweak"
        path.write_text(json.dumps(packet), encoding="utf-8")
        with self.assertRaises(FileExistsError):
            prepare_acceptance(ROOT / "config/example.json", self.out)
        self.assertEqual(json.loads(path.read_text())["tests"][0]["decision"], "tweak")

    def test_unsafe_output_location_is_rejected_before_work(self):
        with self.assertRaises(ValueError):
            prepare_acceptance(ROOT / "config/example.json", ROOT / "acceptance-output")
        self.assertFalse((ROOT / "acceptance-output").exists())


if __name__ == "__main__":
    unittest.main()
