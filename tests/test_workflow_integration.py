from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.pipeline import PreviewStep, RetryablePreviewError, run_preview_steps
from school_workflow_hub.core.safe_logging import safe_json
from school_workflow_hub.core.runs import create_or_get_run
from school_workflow_hub.core.sources import record_source_revision
from school_workflow_hub.core.workflow import EvidenceRef, PreviewChange, WorkflowIssue, WorkflowPreview, persist_preview, combine_previews


def sample_preview():
    return WorkflowPreview("synthetic_integration", changes=(PreviewChange(
        "change:synthetic:due", "assignment:synthetic:one", "infinite_campus", "ic:synthetic:one",
        "due_date", "2026-09-14", "2026-09-16",
        (EvidenceRef("agenda", "agenda:synthetic:one", "v1", "synthetic://agenda/one"),),
        1.0, True,
    ),), issues=(WorkflowIssue("student_route_unverified", "warning"),),
        artifacts=({"kind": "synthetic_report", "status": "preview"},))


class IntegratedPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = initialize_database(Path(self.tmp.name) / "test.sqlite3")
        self.addCleanup(self.conn.close)

    def dump(self):
        return "\n".join(self.conn.iterdump())

    def test_identical_complete_run_changes_no_records(self):
        first = persist_preview(self.conn, sample_preview())
        before = self.dump()
        self.assertEqual(first, persist_preview(self.conn, sample_preview()))
        self.assertEqual(before, self.dump())

    def test_changed_source_supersedes_old_proposal(self):
        first = persist_preview(self.conn, sample_preview())
        original = sample_preview().changes[0]
        changed = replace(original, stable_id="change:synthetic:due2", proposed_value="2026-09-18",
                          evidence=(replace(original.evidence[0], revision="v2"),))
        persist_preview(self.conn, WorkflowPreview("synthetic_integration", (changed,)))
        state = self.conn.execute("SELECT state FROM proposed_actions WHERE id = ?", (first["action_ids"][0],)).fetchone()[0]
        self.assertEqual(state, "superseded")

    def test_old_completed_preview_cannot_reactivate_historical_evidence(self):
        original = sample_preview()
        persist_preview(self.conn, original)
        changed = replace(original.changes[0], proposed_value="2026-09-18",
                          evidence=(replace(original.changes[0].evidence[0], revision="v2"),))
        persist_preview(self.conn, replace(original, changes=(changed,)))
        before = self.dump()
        with self.assertRaises(ValueError):
            persist_preview(self.conn, original)
        self.assertEqual(before, self.dump())

    def test_existing_revision_cannot_claim_another_reference(self):
        run = create_or_get_run(self.conn, "source_fixture", {})
        source = record_source_revision(self.conn, "agenda", "agenda:synthetic:one", {}, "synthetic://agenda/original", run.id)
        change = replace(sample_preview().changes[0], evidence=(EvidenceRef("agenda", "agenda:synthetic:one", source.id, "synthetic://agenda/imposter"),))
        before = self.dump()
        with self.assertRaises(ValueError):
            persist_preview(self.conn, WorkflowPreview("invalid", (change,)))
        self.assertEqual(before, self.dump())

    def test_changed_completed_proposal_requires_reconciliation(self):
        receipt = persist_preview(self.conn, sample_preview())
        self.conn.execute("UPDATE proposed_actions SET state='superseded' WHERE id=?", (receipt["action_ids"][0],))
        self.conn.commit()
        before = self.dump()
        with self.assertRaises(ValueError):
            persist_preview(self.conn, sample_preview())
        self.assertEqual(before, self.dump())

    def test_unknown_unsafe_duplicate_noop_and_self_approval_denied_before_storage(self):
        original = sample_preview().changes[0]
        cases = [replace(original, target_id="UNVERIFIED"),
                 replace(original, proposed_value=original.old_value),
                 replace(original, approval_status="approved"),
                 replace(original, confidence=float("nan")),
                 replace(original, proposed_value={"student_name": "Synthetic Person"}),
                 replace(original, proposed_value="https://docs.google.com/presentation/d/private/edit")]
        for change in cases:
            with self.subTest(change=change.stable_id):
                with self.assertRaises(ValueError):
                    persist_preview(self.conn, WorkflowPreview("invalid", (change,)))
        with self.assertRaises(ValueError):
            persist_preview(self.conn, WorkflowPreview("invalid", (original, original)))
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM runs").fetchone()[0])

    def test_partial_failure_resume_skips_completed_and_full_rerun_noop(self):
        calls = [0, 0]
        def first():
            calls[0] += 1
            return sample_preview()
        def second():
            calls[1] += 1
            if calls[1] == 1:
                raise RetryablePreviewError("credential=must-never-log")
            return WorkflowPreview("second_preview")
        steps = (PreviewStep("agenda", first), PreviewStep("reports", second, ("agenda",)))
        initial = run_preview_steps(self.conn, workflow="complete_proof", inputs={"fixture": "one"}, steps=steps)
        self.assertEqual("blocked", initial["status"])
        resumed = run_preview_steps(self.conn, workflow="complete_proof", inputs={"fixture": "one"}, steps=steps)
        self.assertEqual("completed", resumed["status"])
        self.assertEqual([1, 2], calls)
        before = self.dump()
        again = run_preview_steps(self.conn, workflow="complete_proof", inputs={"fixture": "one"}, steps=steps)
        self.assertEqual(resumed, again)
        self.assertEqual(before, self.dump())
        self.assertNotIn("must-never-log", before)

    def test_permanent_failure_not_retried_and_downstream_not_called(self):
        calls = []
        def bad():
            calls.append("bad")
            raise ValueError("private-input")
        steps = (PreviewStep("bad", bad), PreviewStep("next", lambda: calls.append("next"), ("bad",)))
        for _ in range(2):
            run_preview_steps(self.conn, workflow="permanent", inputs={}, steps=steps)
        self.assertEqual(calls, ["bad"])

    def test_bounded_retry_exhaustion(self):
        calls = []
        def bad():
            calls.append(1)
            raise RetryablePreviewError("transient")
        for _ in range(5):
            run_preview_steps(self.conn, workflow="retry", inputs={}, steps=(PreviewStep("bad", bad),), max_attempts=2)
        self.assertEqual(len(calls), 2)

    def test_dependency_cycle_rejected(self):
        with self.assertRaises(ValueError):
            run_preview_steps(self.conn, workflow="cycle", inputs={}, steps=(PreviewStep("a", sample_preview, ("b",)),))

    def test_private_values_redacted_from_logs(self):
        rendered = safe_json({"email": "synthetic@example.invalid", "event": "https://docs.google.com/document/d/private/edit", "student_name": "Fixture Name"})
        self.assertNotIn("Fixture Name", rendered)
        self.assertNotIn("private/edit", rendered)
        self.assertNotIn("synthetic@", rendered)

    def test_matching_cross_module_changes_coalesce_and_conflicts_queue(self):
        first = sample_preview()
        second = replace(first, workflow="other_planner")
        merged = combine_previews("combined", (first, second))
        self.assertEqual(len(merged.changes), 1)
        second = replace(second, changes=(replace(second.changes[0], proposed_value="2026-09-19"),))
        conflict = combine_previews("combined", (first, second))
        self.assertEqual(conflict.changes, ())
        self.assertIn("conflicting_workflow_proposals", [issue.code for issue in conflict.issues])
