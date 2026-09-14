from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from school_workflow_hub.core.models import EnrollmentRecord
from school_workflow_hub.core.workflow import validate_preview
from school_workflow_hub.workflows.roster_reconciliation import (
    RosterEntry, RosterSnapshot, plan_roster_reconciliation,
)


NOW = datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)
CLASS = "class:synthetic:forensics-a1"


def entry(learner: str, status: str = "active", external: str | None = None) -> RosterEntry:
    return RosterEntry(EnrollmentRecord(CLASS, "learner:synthetic:" + learner, external), status)


def snapshot(*entries: RosterEntry, revision: str = "source") -> RosterSnapshot:
    return RosterSnapshot(CLASS, "roster-revision:synthetic:" + revision, NOW, True, len(entries), entries)


class RosterReconciliationTests(unittest.TestCase):
    def plan(self, source: RosterSnapshot, grading: RosterSnapshot):
        result = plan_roster_reconciliation(source, grading, observed_at=NOW)
        validate_preview(result)
        return result

    def assert_blocked(self, source: RosterSnapshot, grading: RosterSnapshot, code: str):
        result = self.plan(source, grading)
        self.assertEqual(result.changes, ())
        self.assertEqual(result.issues[0].code, code)
        self.assertEqual(result.issues[0].severity, "blocked")
        self.assertEqual(result.external_writes, 0)

    def test_add_reactivate_withdraw_and_retain_in_one_class(self):
        source = snapshot(entry("new"), entry("returning"), entry("current"))
        grading = snapshot(entry("returning", "withdrawn"), entry("current"), entry("leaving"),
                           entry("old", "withdrawn"), revision="grading")
        before = (source, grading)
        result = self.plan(source, grading)
        self.assertEqual(len(result.changes), 3)
        operations = {p["learner_key"]: p["operation"] for p in result.artifacts[0]["membership_proposals"]}
        self.assertEqual(operations, {"learner:synthetic:new": "add", "learner:synthetic:returning": "reactivate",
                                      "learner:synthetic:leaving": "withdraw"})
        self.assertTrue(all(c.field == "enrollment_status" and c.reversible for c in result.changes))
        self.assertTrue(all(c.approval_status == "pending" and c.status == "proposed" for c in result.changes))
        self.assertEqual((source, grading), before)
        self.assertEqual(result.artifacts[0]["grade_history_policy"], "retain_all_existing_grade_history")
        self.assertEqual(result.external_writes, 0)
        self.assertEqual(result.live_model_calls, 0)

    def test_no_op_when_current_and_retired_statuses_match(self):
        result = self.plan(snapshot(entry("a")), snapshot(entry("a"), entry("b", "withdrawn")))
        self.assertEqual(result.changes, ())
        self.assertEqual(result.issues, ())

    def test_replay_and_input_order_are_deterministic(self):
        source = snapshot(entry("b"), entry("a"))
        grading = snapshot(entry("c"), revision="grading")
        self.assertEqual(self.plan(source, grading), self.plan(source, grading))
        self.assertEqual(self.plan(source, grading), self.plan(replace(source, entries=tuple(reversed(source.entries))), grading))

    def test_partial_and_count_mismatch_block_all_changes(self):
        for bad, code in ((replace(snapshot(), complete=False), "incomplete_roster_snapshot"),
                          (replace(snapshot(), complete=1), "incomplete_roster_snapshot"),
                          (replace(snapshot(), expected_count=1), "roster_count_mismatch"),
                          (replace(snapshot(), expected_count=False), "roster_count_mismatch")):
            with self.subTest(code=code):
                self.assert_blocked(bad, snapshot(entry("a")), code)
                self.assert_blocked(snapshot(entry("a")), bad, code)

    def test_stale_future_and_naive_snapshots_block(self):
        for observed, code in ((NOW - timedelta(hours=24, seconds=1), "roster_snapshot_not_current"),
                               (NOW + timedelta(seconds=1), "roster_snapshot_not_current"),
                               (NOW.replace(tzinfo=None), "invalid_roster_timestamp")):
            with self.subTest(observed=observed):
                bad = replace(snapshot(), observed_at=observed)
                self.assert_blocked(bad, snapshot(), code)
                self.assert_blocked(snapshot(), bad, code)
        self.assertFalse(self.plan(replace(snapshot(), observed_at=NOW - timedelta(hours=24)), snapshot()).issues)
        result = plan_roster_reconciliation(snapshot(), snapshot(), observed_at=NOW.replace(tzinfo=None))
        self.assertEqual(result.issues[0].code, "invalid_roster_observation_time")

    def test_duplicate_learner_and_external_identity_block(self):
        self.assert_blocked(snapshot(entry("a"), entry("a")), snapshot(), "duplicate_learner_identity")
        self.assert_blocked(snapshot(), snapshot(entry("a"), entry("a", "withdrawn")), "duplicate_learner_identity")
        external = "enrollment:synthetic:one"
        self.assert_blocked(snapshot(entry("a", external=external), entry("b", external=external)),
                            snapshot(), "ambiguous_enrollment_identity")
        self.assert_blocked(snapshot(entry("a", external=external)), snapshot(entry("b", external=external)),
                            "conflicting_enrollment_identity")

    def test_unverified_real_or_wildcard_ids_do_not_echo(self):
        for key in ("UNVERIFIED", "*", "student-real-001", "someone@example.invalid"):
            with self.subTest(key=key):
                bad = snapshot(RosterEntry(EnrollmentRecord(CLASS, key)))
                result = self.plan(bad, snapshot())
                self.assertEqual(result.issues[0].code, "non_fixture_roster_identity")
                self.assertNotIn(key, repr(result))

    def test_mixed_or_mismatching_class_is_blocked(self):
        other = "class:synthetic:anatomy-b1"
        self.assert_blocked(snapshot(RosterEntry(EnrollmentRecord(other, "learner:synthetic:a"))),
                            snapshot(), "mixed_class_roster")
        self.assert_blocked(snapshot(), replace(snapshot(), class_stable_id=other), "roster_class_mismatch")

    def test_ambiguous_status_and_historical_source_are_blocked(self):
        self.assert_blocked(snapshot(), snapshot(entry("a", "unknown")), "ambiguous_enrollment_status")
        self.assert_blocked(snapshot(entry("a", "withdrawn")), snapshot(), "source_roster_not_active_only")

    def test_empty_complete_source_proposes_only_withdrawal_not_delete(self):
        result = self.plan(snapshot(), snapshot(entry("a")))
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].proposed_value, "withdrawn")
        self.assertEqual(result.artifacts[0]["membership_policy"], "status_only_never_delete")

    def test_applied_status_fixture_makes_next_plan_empty(self):
        source = snapshot(entry("a"), entry("b"))
        first = self.plan(source, snapshot(entry("a", "withdrawn"), entry("c")))
        self.assertEqual(len(first.changes), 3)
        # A fictional post-review snapshot, not execution of these proposals.
        after = snapshot(entry("a"), entry("b"), entry("c", "withdrawn"), revision="grading-after")
        self.assertEqual(self.plan(source, after).changes, ())

    def test_snapshot_limit_and_invalid_shape_are_denied(self):
        self.assert_blocked(snapshot(*(entry(str(i)) for i in range(501))), snapshot(), "roster_snapshot_limit_exceeded")
        self.assert_blocked(replace(snapshot(), entries=[]), snapshot(), "roster_count_mismatch")

