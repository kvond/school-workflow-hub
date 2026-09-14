from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from school_workflow_hub.core.database import SYNTHETIC_RUNTIME_ROOT
from school_workflow_hub.workflows.system_demo import run_system_preview
from school_workflow_hub.workflows import system_demo


ROOT = Path(__file__).resolve().parents[1]


class SystemEndToEndTests(unittest.TestCase):
    def setUp(self):
        SYNTHETIC_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="system-e2e-", dir=SYNTHETIC_RUNTIME_ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "output"

    def test_all_module_outputs_share_assignment_and_authoritative_date(self):
        receipt = run_system_preview(ROOT / "config/example.json", self.out)
        data = json.loads(Path(receipt["preview"]).read_text())
        proposals = data["interconnection"]["changes"]
        keys = {(p["target_system"], p["target_id"], p["field"]) for p in proposals}
        self.assertEqual(len(keys), len(proposals))
        agenda_due = next(p["proposed_value"] for p in proposals if p["target_system"] == "infinite_campus" and p["field"] == "due_date")
        assignment_key = proposals[0]["assignment_key"]
        feedback = [r for r in data["feedback"]["records"] if r["assignment"]["stable_id"] == assignment_key]
        self.assertTrue(feedback)
        self.assertTrue(all(r["assignment"]["intended_due_date"] == agenda_due for r in feedback))
        self.assertEqual(data["external_writes"], 0)
        self.assertEqual(data["live_model_calls"], 0)
        self.assertTrue(all(not job["enabled"] for job in data["schedules"]))
        self.assertTrue(Path(receipt["reports"]).is_file())
        self.assertEqual(len(data["shared_review_queue"]), receipt["ledger_counts"]["review_items"])
        self.assertIn("Shared review queue", Path(receipt["reports"]).read_text())
        self.assertEqual(data["link_audit"]["occurrences"][0]["status"], "incorrect")
        before = (self.out / "state.sqlite3").read_bytes()
        self.assertEqual(receipt, run_system_preview(ROOT / "config/example.json", self.out))
        self.assertEqual(before, (self.out / "state.sqlite3").read_bytes())

    def test_partial_report_failure_resumes_without_duplicate_proposals(self):
        original = system_demo._write_once
        def fail_summary(path, text):
            if path.name == "PREVIEW.md":
                raise OSError("synthetic report interruption")
            return original(path, text)
        with patch.object(system_demo, "_write_once", side_effect=fail_summary):
            with self.assertRaises(OSError):
                run_system_preview(ROOT / "config/example.json", self.out)
        with sqlite3.connect(self.out / "state.sqlite3") as connection:
            before = connection.execute("SELECT count(*) FROM proposed_actions").fetchone()[0]
        receipt = run_system_preview(ROOT / "config/example.json", self.out)
        self.assertEqual(receipt["ledger_counts"]["proposed_actions"], before)

    def test_same_count_ledger_tampering_requires_reconciliation(self):
        run_system_preview(ROOT / "config/example.json", self.out)
        with sqlite3.connect(self.out / "state.sqlite3") as connection:
            connection.execute("UPDATE proposed_actions SET state='superseded'")
        with self.assertRaises(ValueError):
            run_system_preview(ROOT / "config/example.json", self.out)

    def test_interrupted_file_write_never_publishes_a_partial_artifact(self):
        with patch.object(system_demo.os, "fsync", side_effect=OSError("synthetic flush failure")):
            with self.assertRaises(OSError):
                run_system_preview(ROOT / "config/example.json", self.out)
        self.assertFalse((self.out / "preview.json").exists())
        self.assertFalse(list(self.out.glob(".preview-pending-*")))
        receipt = run_system_preview(ROOT / "config/example.json", self.out)
        self.assertEqual(receipt["status"], "synthetic_preview_ready")
        self.assertEqual(receipt, run_system_preview(ROOT / "config/example.json", self.out))

    def test_edited_report_packet_is_preserved_and_not_certified(self):
        receipt = run_system_preview(ROOT / "config/example.json", self.out)
        reports = Path(receipt["reports"])
        reports.write_text("Teacher synthetic review edit\n")
        with self.assertRaises(ValueError):
            run_system_preview(ROOT / "config/example.json", self.out)
        self.assertEqual(reports.read_text(), "Teacher synthetic review edit\n")
