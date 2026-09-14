from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from school_workflow_hub.adapters.agenda_rows import AgendaLayout
from school_workflow_hub.adapters.submission_pages import collect_submission_window, SubmissionPage
from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.observation_baseline import read_baseline
from school_workflow_hub.workflows.agenda_observer import observe_agenda
from school_workflow_hub.workflows.feedback_pipeline import load_feedback_fixture, plan_feedback_pipeline
from school_workflow_hub.workflows.reporting_pipeline import plan_reporting_pipeline
from school_workflow_hub.workflows.print_packet import render_print_packet
from school_workflow_hub.workflows.daily_notes import plan_daily_notes
from school_workflow_hub.workflows.input_demo import run_input_demo
from school_workflow_hub.core.database import SYNTHETIC_RUNTIME_ROOT
from tests.test_workflow_inputs import agenda, LAYOUT, AS_OF

ROOT = Path(__file__).resolve().parents[1]


class InputIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = initialize_database(Path(self.temp.name) / "fixture.sqlite3")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def observe(self, reader, now=AS_OF):
        return observe_agenda(reader, self.db, spreadsheet_id="synthetic_sheet_0123456789",
            sheet_title="Synthetic Biology", layout=LAYOUT, key=b"s" * 32,
            stream_key="agenda-test", clock=lambda: now)

    def test_read_normalize_checkpoint_then_identical_rerun(self):
        reader = Mock()
        reader.read.return_value = agenda()
        first = self.observe(reader)
        second = self.observe(reader, AS_OF.replace(hour=21))
        self.assertEqual(first.status, "baseline_created")
        self.assertEqual(second.status, "unchanged")
        self.assertFalse(first.change_proven)
        self.assertEqual(len(second.rows), 1)
        reader.read.assert_called_with("synthetic_sheet_0123456789", ("'Synthetic Biology'!A2:E4",))

    def test_failed_read_and_invalid_row_do_not_advance_baseline(self):
        reader = Mock()
        reader.read.return_value = agenda()
        self.observe(reader)
        before = read_baseline(self.db, "agenda-test")
        reader.read.side_effect = RuntimeError("private-source-and-token")
        failed = self.observe(reader, AS_OF.replace(hour=21))
        self.assertEqual(failed.status, "blocked")
        self.assertNotIn("private", repr(failed))
        reader.read.side_effect = None
        reader.read.return_value = agenda(["", "class:synthetic:biology-a", "Synthetic", "2026-09-16", "https://example.invalid/copy"])
        self.assertFalse(self.observe(reader, AS_OF.replace(hour=21)).saved)
        self.assertEqual(before, read_baseline(self.db, "agenda-test"))

    def test_wrong_sheet_title_never_establishes_baseline(self):
        reader = Mock()
        data = agenda()
        data["sheets"][0]["properties"]["title"] = "Wrong configured sheet"
        reader.read.return_value = data
        self.assertFalse(self.observe(reader).saved)
        self.assertIsNone(read_baseline(self.db, "agenda-test"))

    def test_metadata_only_response_cannot_be_mistaken_for_empty_assignments(self):
        reader = Mock()
        data = agenda()
        del data["sheets"][0]["data"]
        reader.read.return_value = data
        self.assertFalse(self.observe(reader).saved)
        self.assertIsNone(read_baseline(self.db, "agenda-test"))

    def test_date_change_proves_change_without_retargeting_lesson(self):
        reader = Mock()
        reader.read.return_value = agenda()
        first = self.observe(reader)
        data = agenda()
        data["sheets"][0]["data"][0]["rowData"][0]["values"][3]["formattedValue"] = "2026-09-18"
        reader.read.return_value = data
        changed = self.observe(reader, AS_OF.replace(hour=21))
        self.assertTrue(changed.change_proven)
        self.assertEqual(first.rows[0].deck_reference, changed.rows[0].deck_reference)
        self.assertEqual(first.rows[0].assignment.stable_id, changed.rows[0].assignment.stable_id)

    def test_submissions_feed_existing_feedback_and_printable_report_without_duplicates(self):
        payload = json.loads((ROOT / "tests/fixtures/feedback_pipeline_cases.json").read_text())
        assignments, observations, request = load_feedback_fixture(payload)
        eligible = tuple(o for o in observations if datetime.fromisoformat(o.submission.observed_at.replace("Z", "+00:00")) <= request.as_of)
        # The fixture has both history and current observations. Collector
        # verifies coverage/identity; the existing pipeline decides latest credit.
        page = SubmissionPage(request.class_stable_id, "synthetic-snapshot", tuple(o.submission for o in eligible))
        collected = collect_submission_window(lambda *args: page, class_stable_id=request.class_stable_id,
            assignment_ids=frozenset(a.stable_id for a in assignments), as_of=request.as_of)
        self.assertTrue(collected.complete)
        keys = {(r.stable_id, r.revision_hash) for r in collected.records}
        approved = tuple(o for o in eligible if (o.submission.stable_id, o.submission.revision_hash) in keys)
        first = plan_feedback_pipeline(self.db, assignments, approved, request)
        report = plan_reporting_pipeline(first, friday_class_ids=(request.class_stable_id,))
        packet = render_print_packet(report.preview)
        second = plan_feedback_pipeline(self.db, assignments, approved, request)
        rerun = render_print_packet(plan_reporting_pipeline(second, friday_class_ids=(request.class_stable_id,)).preview)
        self.assertEqual(packet, rerun)
        self.assertEqual(packet.delivery_state, "not_delivered")
        self.assertNotIn("<script", packet.html)
        self.assertNotIn("src=", packet.html)
        self.assertIn("@media print", packet.html)
        self.assertEqual(second.preview.changes, ())

    def test_notes_and_grading_keep_independent_acceptance_state(self):
        result = plan_daily_notes(({"id": "synthetic-note", "content": "Discuss evidence boundaries",
            "labels": ["biology"], "due": {"date": "2026-09-14"}},), local_day=AS_OF.date(),
            timezone_name="America/New_York", class_labels={"biology": "class:synthetic:biology-a"},
            meeting_class_ids=frozenset({"class:synthetic:biology-a"}))
        self.assertEqual(len(result.notes), 1)
        self.assertEqual(result.external_writes, 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM proposed_actions").fetchone()[0], 0)

    def test_complete_demo_replays_identically_and_preserves_modified_artifact(self):
        SYNTHETIC_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=SYNTHETIC_RUNTIME_ROOT) as folder:
            out = Path(folder)
            first = run_input_demo(out)
            self.assertEqual(first, run_input_demo(out))
            self.assertTrue(first["failed_read_preserves_baseline"])
            self.assertEqual(first["live_source_reads"], 0)
            report = out / "REPORT_PREVIEW.html"
            report.write_text("Teacher preserved test edits")
            with self.assertRaises(FileExistsError):
                run_input_demo(out)
            self.assertEqual(report.read_text(), "Teacher preserved test edits")


if __name__ == "__main__":
    unittest.main()
