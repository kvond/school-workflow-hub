from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import feedback_delivery
from school_workflow_hub.core.cloud_state import Conflict, Document, Snapshot


class MemoryCAS:
    def __init__(self):
        self.rows = {}
        self.sequence = 0
        self.now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)

    def read(self, ids):
        return Snapshot({i: deepcopy(self.rows.get(i)) for i in ids}, self.now.isoformat())

    def commit(self, changes):
        for ident, (_, expected) in changes.items():
            prior = self.rows.get(ident)
            if (prior.version if prior else None) != expected:
                raise Conflict("state_version_conflict")
        self.sequence += 1
        for ident, (value, _) in changes.items():
            self.rows[ident] = Document(deepcopy(value), str(self.sequence))


def row(record_id="record-1", *, status="assessed", feedback="Useful feedback."):
    return {
        "record_id": record_id,
        "class_key": "forensics",
        "assignment_key": "forensics:w06",
        "assignment_title": "Searching the Scene",
        "section_id": "A2",
        "section_name": "A2 Forensics",
        "display_name": "Ana R.",
        "learner_key": "learner-ana",
        "source_sha256": "a" * 64,
        "source_file_id": "FILE123",
        "checked_at": "2026-10-05T11:00:00-04:00",
        "feedback": feedback,
        "status": status,
        "hold_reason": "",
    }


class FeedbackDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.patch = patch.object(feedback_delivery, "configured_store", return_value=self.port)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_saved_feedback_becomes_ready_once_and_is_idempotent(self):
        first = feedback_delivery.ensure_ready(row(), {})
        second = feedback_delivery.ensure_ready(row(), {})
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["record_id"], "record-1")
        self.assertEqual(len(self.port.rows), 1)

    def test_print_and_handout_advance_without_erasing_history(self):
        feedback_delivery.ensure_ready(row(), {})
        printed = feedback_delivery.mark(row(), "printed", {})
        self.assertEqual(printed["status"], "printed")
        self.assertTrue(printed["printed_at"])
        printed_again = feedback_delivery.mark(row(), "printed", {})
        self.assertEqual(printed_again["printed_at"], printed["printed_at"])
        handed = feedback_delivery.mark(row(), "handed_out", {})
        self.assertEqual(handed["status"], "handed_out")
        self.assertTrue(handed["handed_out_at"])
        self.assertEqual(handed["printed_at"], printed["printed_at"])

    def test_new_assessment_record_is_a_new_ready_version(self):
        feedback_delivery.ensure_ready(row("record-1"), {})
        feedback_delivery.mark(row("record-1"), "handed_out", {})
        newer = row("record-2")
        newer["source_sha256"] = "b" * 64
        state = feedback_delivery.ensure_ready(newer, {})
        self.assertEqual(state["status"], "ready")
        states = feedback_delivery.states([row("record-1"), newer], {})
        self.assertEqual({item["record_id"]: item["status"] for item in states},
                         {"record-1": "handed_out", "record-2": "ready"})

    def test_legacy_saved_feedback_projects_as_ready_without_writing_on_get(self):
        projected = feedback_delivery.states([row()], {})
        self.assertEqual(projected[0]["status"], "ready")
        self.assertEqual(self.port.rows, {})

    def test_queue_excludes_superseded_held_or_feedbackless_records(self):
        current = row("current")
        superseded = row("old", status="superseded")
        held = row("held")
        held["hold_reason"] = "needs review"
        blank = row("blank", feedback="")
        queue = feedback_delivery.queue([current, superseded, held, blank], {})
        self.assertEqual([item["record_id"] for item in queue["ready"]], ["current"])
        self.assertEqual(queue["printed"], [])
        self.assertEqual(queue["handed_out"], [])

    def test_invalid_action_does_not_write(self):
        with self.assertRaises(ValueError):
            feedback_delivery.mark(row(), "delete", {})
        self.assertEqual(self.port.rows, {})


if __name__ == "__main__":
    unittest.main()
