"""Shared source ownership uses fictional CAS state and trusted test callbacks."""
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from hub_v2.source_operations import SourceOperations, Held
from school_workflow_hub.core.cloud_state import Busy, LeaseLost, StoreUnavailable
from test_cloud_state import MemoryCAS


class SourceOperationsTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.port.now = datetime(2026, 10, 22, 10, 30, tzinfo=timezone.utc)
        self.activity = Mock(return_value=None)

    def acquire(self, *, owner="roster-worker", request_id="request-1", activity=None):
        return SourceOperations.acquire(self.port, source_scope="fictional-shared-school-sources",
            owner=owner, request_id=request_id, input_hash="fictional-input",
            check_activity=activity or self.activity)

    def test_different_participating_workers_share_one_claim(self):
        first = self.acquire()
        with self.assertRaises(Busy):
            self.acquire(owner="archive-worker", request_id="request-2")
        first.before_write(required_seconds=30)
        self.assertEqual(first.claim.fence, 1)

    def test_release_readback_then_new_owner_advances_fence(self):
        first = self.acquire()
        self.assertEqual(first.release()["scope"], "participating_preparation_workers")
        second = self.acquire(owner="archive-worker", request_id="request-2")
        self.assertEqual(second.claim.fence, 2)
        with self.assertRaises(Held):
            first.before_write(required_seconds=30)

    def test_expired_owner_cannot_write_or_release_after_takeover(self):
        first = self.acquire()
        self.port.advance(121)
        second = self.acquire(owner="archive-worker", request_id="request-2")
        with self.assertRaises(LeaseLost):
            first.before_write(required_seconds=30)
        with self.assertRaises(LeaseLost):
            first.release()
        second.before_write(required_seconds=30)

    def test_lost_renewal_response_recovers_same_fence_without_school_action(self):
        first = self.acquire()
        self.port.advance(20)
        self.port.fail_after_commit = True
        first.before_write(required_seconds=30)
        self.assertEqual(first.claim.fence, 1)
        self.assertFalse(first.stopped)
        self.assertGreater(first.claim.expires_at, "2026-10-22T10:32:00+00:00")

    def test_unavailable_recovery_stops_the_operation(self):
        first = self.acquire()
        first.cloud.renew = Mock(side_effect=StoreUnavailable("uncertain"))
        first.cloud.claim = Mock(side_effect=StoreUnavailable("still_unavailable"))
        with self.assertRaises(StoreUnavailable):
            first.before_write(required_seconds=30)
        with self.assertRaises(Held):
            first.before_write(required_seconds=30)

    def test_each_write_requires_fresh_host_activity_evidence(self):
        first = self.acquire()
        first.before_write(required_seconds=30)
        self.activity.side_effect = RuntimeError("PRIVATE activity detail")
        with self.assertRaisesRegex(Held, "source_activity_verification_unavailable"):
            first.before_write(required_seconds=30)
        self.assertEqual(self.activity.call_count, 3)
        self.activity.side_effect = None
        with self.assertRaises(Held):
            first.before_write(required_seconds=30)

    def test_missing_or_boolean_activity_check_is_not_ownership_evidence(self):
        for callback in (None, True, Mock(return_value=False), Mock(return_value=True)):
            with self.subTest(callback=callback):
                with self.assertRaises(Held):
                    SourceOperations.acquire(self.port, source_scope="fictional-sources", owner="worker",
                        request_id="request-x", input_hash="input", check_activity=callback)
        self.assertEqual(self.port.rows, {})

    def test_helper_frozen_inputs_are_refused_before_claim_or_write(self):
        self.activity.side_effect = Held("helper_inputs_frozen")
        with self.assertRaisesRegex(Held, "helper_inputs_frozen"):
            self.acquire()
        self.assertEqual(self.port.rows, {})
        self.activity.side_effect = None
        first = self.acquire()
        self.activity.side_effect = Held("helper_inputs_frozen")
        with self.assertRaisesRegex(Held, "helper_inputs_frozen"):
            first.before_write(required_seconds=30)

    def test_server_clock_cutoff_and_finish_margin_stop_new_writes(self):
        for hour, minute, second in ((10, 59, 31), (11, 0, 0), (12, 0, 0)):
            self.port.now = datetime(2026, 10, 22, hour, minute, second, tzinfo=timezone.utc)
            with self.assertRaisesRegex(Held, "source_mutation_window_closed"):
                self.acquire()
        self.assertEqual(self.port.rows, {})

    def test_lease_is_bounded_by_cutoff_and_clock_is_rechecked_after_activity(self):
        self.port.now = datetime(2026, 10, 22, 10, 59, 0, tzinfo=timezone.utc)
        first = self.acquire()
        self.assertLessEqual(first.claim.expires_at, "2026-10-22T11:00:00+00:00")
        self.activity.side_effect = lambda: self.port.advance(31)
        with self.assertRaises(Held):
            first.before_write(required_seconds=30)

    def test_standard_time_and_weekends_use_eastern_calendar(self):
        self.port.now = datetime(2026, 11, 5, 11, 30, tzinfo=timezone.utc)
        first = self.acquire()
        first.before_write(required_seconds=30)
        self.port.now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
        with self.assertRaises(Held):
            first.before_write(required_seconds=30)
        self.port.now = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
        with self.assertRaises(Held):
            self.acquire(request_id="request-weekend")

    def test_lost_release_response_is_read_back_without_releasing_again(self):
        first = self.acquire()
        self.port.fail_after_commit = True
        first.release()
        self.assertTrue(first.released)
        self.acquire(owner="archive-worker", request_id="request-2").before_write(required_seconds=30)

    def test_complete_write_budget_refuses_45_second_write_with_40_seconds_left(self):
        self.port.now = datetime(2026, 10, 22, 10, 59, 20, tzinfo=timezone.utc)
        first = self.acquire()
        action = Mock()
        with self.assertRaisesRegex(Held, "source_mutation_window_closed"):
            first.before_write(required_seconds=45)
            action()
        action.assert_not_called()

    def test_still_valid_29_second_lease_can_renew_before_write(self):
        first = self.acquire()
        self.port.advance(91)
        first.before_write(required_seconds=170)
        expires = datetime.fromisoformat(first.claim.expires_at)
        self.assertGreater((expires - self.port.now).total_seconds(), 170)

    def test_budget_is_required_bounded_and_not_a_browser_boolean(self):
        first = self.acquire()
        with self.assertRaises(TypeError):
            first.before_write()
        for invalid in (None, False, True, 0, -1, 1.5, 871, 900, "120"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(Held, "source_write_budget_invalid"):
                    first.before_write(required_seconds=invalid)
        first.before_write(required_seconds=120)

    def test_lost_renewal_does_not_accept_old_lease_without_complete_write_budget(self):
        first = self.acquire()
        self.port.advance(91)
        # A failed commit leaves the old 29-second lease. Reading the same
        # fence recovers identity, but cannot authorize a 45-second operation.
        first.cloud.renew = Mock(side_effect=StoreUnavailable("not_committed"))
        with self.assertRaisesRegex(Held, "source_lease_too_short"):
            first.before_write(required_seconds=45)

    def test_time_spent_renewing_is_included_in_final_cutoff_reservation(self):
        self.port.now = datetime(2026, 10, 22, 10, 57, 0, tzinfo=timezone.utc)
        first = self.acquire()
        renew = first.cloud.renew
        def delayed(*args, **kwargs):
            result = renew(*args, **kwargs)
            self.port.advance(70)
            return result
        first.cloud.renew = delayed
        with self.assertRaisesRegex(Held, "source_mutation_window_closed"):
            first.before_write(required_seconds=120)

    def test_delayed_state_response_is_conservatively_budgeted(self):
        self.port.now = datetime(2026, 10, 22, 10, 58, 40, tzinfo=timezone.utc)
        first = self.acquire()
        # Server says80s to cutoff, but the snapshot response took40s.
        with patch("hub_v2.source_operations.monotonic", side_effect=[100.0, 140.0]):
            with self.assertRaisesRegex(Held, "source_mutation_window_closed"):
                first.before_write(required_seconds=45)
