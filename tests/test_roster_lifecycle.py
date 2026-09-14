from __future__ import annotations

from datetime import datetime, timezone
import unittest

from school_workflow_hub.workflows.roster_lifecycle import plan_roster_lifecycle


class RosterLifecycleTestCase(unittest.TestCase):
    def test_pause_is_a_review_not_an_activated_schedule_change(self) -> None:
        decision = plan_roster_lifecycle(
            observed_at=datetime(2026, 11, 15, 14, 0, tzinfo=timezone.utc),
            workflow_stable_id="weekly-roster-sync:2026-2027",
        )
        self.assertEqual(decision.status, "review_due")
        self.assertEqual(decision.review_kind, "pause_weekly_roster_sync")
        self.assertFalse(decision.activation_performed)
        self.assertEqual(decision.external_writes, 0)
        self.assertIn("did not change", decision.instruction)

    def test_restart_review_uses_new_york_date_and_remains_manual(self) -> None:
        decision = plan_roster_lifecycle(
            observed_at=datetime(2027, 8, 15, 13, 0, tzinfo=timezone.utc),
            workflow_stable_id="weekly-roster-sync:2026-2027",
        )
        self.assertEqual(decision.review_kind, "restart_weekly_roster_sync")
        self.assertIn("2027-08-15", decision.due_at)
        self.assertFalse(decision.activation_performed)

    def test_before_pause_returns_the_fixed_future_review(self) -> None:
        decision = plan_roster_lifecycle(
            observed_at=datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc),
            workflow_stable_id="weekly-roster-sync:2026-2027",
        )
        self.assertEqual(decision.status, "review_scheduled")
        self.assertEqual(decision.review_kind, "pause_weekly_roster_sync")
        self.assertIn("2026-11-15", decision.due_at)

    def test_naive_or_broad_identity_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            plan_roster_lifecycle(
                observed_at=datetime(2026, 11, 15, 9, 0),
                workflow_stable_id="weekly-roster-sync:2026-2027",
            )
        with self.assertRaises(ValueError):
            plan_roster_lifecycle(
                observed_at=datetime(2026, 11, 15, 14, 0, tzinfo=timezone.utc),
                workflow_stable_id="*",
            )
