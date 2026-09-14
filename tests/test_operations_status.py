import copy
import unittest
from school_workflow_hub.workflows.operations_status import build_operations_snapshot

STAMP = "2026-09-14T20:00:00Z"


def operation(kind="assignment_links", **changes):
    row = {"kind": kind, "scope": {"class_key": "class:synthetic:biology", "assignment_key": "assignment:synthetic:one", "week_key": "W03"},
           "status": "not_checked", "verification": "unverified", "last_checked_at": None,
           "evidence": [], "next_action": {"label": "Compare Agenda with parent assignment, then Schoology", "kind": "review", "todoist_task_id": "task:synthetic:existing"}}
    row.update(changes)
    return row


def passed(verification="synthetic_test", kind="test_receipt"):
    return {"status": "passed", "verification": verification, "last_checked_at": STAMP,
            "evidence": [{"kind": kind, "reference": "receipt:synthetic:one", "observed_at": STAMP, "summary": "Fictional test receipt"}],
            "next_action": None}


class OperationsStatusTests(unittest.TestCase):
    def build(self, rows=(), **kwargs):
        return build_operations_snapshot(rows, generated_at=STAMP, **kwargs)

    def test_unconnected_status_does_not_invent_checks_or_enable_saving(self):
        result = self.build([operation()])
        self.assertIsNone(result["operations"][0]["last_checked_at"])
        self.assertFalse(result["automatic_sync"]["active"])
        self.assertFalse(result["edit_saving_supported"])
        self.assertFalse(result["work_queue"]["create_tasks"])

    def test_test_pass_remains_test_and_cannot_activate_sync(self):
        result = self.build([operation(**passed())], connection_state="preview_only")
        self.assertEqual(result["operations"][0]["verification"], "synthetic_test")
        self.assertFalse(result["automatic_sync"]["active"])
        with self.assertRaises(ValueError):
            self.build(sync_check=passed(), connection_state="connected_live")

    def test_read_only_live_pass_cannot_activate_sync(self):
        row = operation(**passed("live_read", "source_snapshot"))
        self.assertFalse(self.build([row], connection_state="connected_readonly")["automatic_sync"]["active"])

    def test_sync_requires_explicit_live_end_to_end_readback_and_live_connection(self):
        sync = passed("live_end_to_end", "readback")
        self.assertFalse(self.build(sync_check=sync, connection_state="connected_readonly")["automatic_sync"]["active"])
        self.assertTrue(self.build(sync_check=sync, connection_state="connected_live")["automatic_sync"]["active"])

    def test_missing_evidence_timestamps_and_next_actions_are_rejected(self):
        for row in (operation(status="passed"), operation(status="needs_review", next_action=None),
                    operation(**dict(passed(), evidence=[])), operation(**dict(passed(), last_checked_at="2026-09-14T20:00:00"))):
            with self.assertRaises(ValueError):
                self.build([row])

    def test_stable_identity_and_existing_task_mapping_are_preserved(self):
        original = operation()
        before = copy.deepcopy(original)
        pending = self.build([original])["operations"][0]
        checked = self.build([operation(**passed())])["operations"][0]
        self.assertEqual(original, before)
        self.assertEqual(pending["id"], checked["id"])
        self.assertEqual(pending["next_action"]["todoist_task_id"], "task:synthetic:existing")
        with self.assertRaises(ValueError):
            self.build([original, original])

    def test_class_roster_scope_and_future_check_rules(self):
        roster = operation("roster_changes")
        roster["scope"]["assignment_key"] = None
        self.assertEqual(len(self.build([roster])["operations"]), 1)
        future = operation(**dict(passed(), last_checked_at="2026-09-15T20:00:00Z"))
        with self.assertRaises(ValueError):
            self.build([future])

    def test_malformed_inputs_and_unsupported_end_to_end_claim_are_rejected(self):
        for check in ([], {"status": []}, dict(passed(), active=True)):
            with self.assertRaises(ValueError):
                self.build(sync_check=check)
        for row in (operation(kind=[]), operation(status=[]),
                    operation(**passed("live_end_to_end", "source_snapshot")),
                    operation(**dict(passed(), status="failed", last_checked_at=None))):
            with self.assertRaises(ValueError):
                self.build([row])
