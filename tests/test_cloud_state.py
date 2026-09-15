from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event, Lock
import unittest

from school_workflow_hub.core.cloud_state import (
    Busy, Claim, CloudState, Conflict, Document, LeaseLost, Snapshot, StoreUnavailable,
)


class MemoryCAS:
    """Disposable server model: shared CAS state, independent clients, server time."""
    def __init__(self):
        self.rows, self.sequence = {}, 0
        self.now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        self.lock = Lock()
        self.fail_after_commit = False

    def read(self, ids):
        with self.lock:
            return Snapshot({i: deepcopy(self.rows.get(i)) for i in ids}, self.now.isoformat())

    def commit(self, changes):
        with self.lock:
            for i, (_, expected) in changes.items():
                prior = self.rows.get(i)
                if (prior.version if prior else None) != expected:
                    raise Conflict("state_version_conflict")
            self.sequence += 1
            for i, (value, _) in changes.items():
                self.rows[i] = Document(deepcopy(value), str(self.sequence))
            if self.fail_after_commit:
                self.fail_after_commit = False
                raise StoreUnavailable("uncertain_response")

    def advance(self, seconds):
        with self.lock:
            self.now += timedelta(seconds=seconds)


class CloudStateTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.store = CloudState(self.port)
        self.scope = {"workflow": "completion", "class_key": "fictional-bio", "assignment_key": "practice-02"}
        self.task = self.store.create_task(self.scope)

    def claim(self, request="request-1", owner="worker-a", input_hash="input-v1"):
        return self.store.claim(self.task, owner=owner, request_id=request, input_hash=input_hash, ttl_seconds=30)

    def baseline(self, revision="revision-1"):
        return {"scope": "source-v1", "source_revision": revision,
                "observed_at": self.port.now.isoformat(), "fingerprint": revision}

    def test_task_identity_is_stable_and_separates_assignments(self):
        self.assertEqual(self.task, CloudState(self.port).create_task(self.scope))
        self.assertNotEqual(self.task, self.store.create_task({**self.scope, "assignment_key": "practice-03"}))

    def test_independent_concurrent_clients_have_exactly_one_owner(self):
        def attempt(i):
            try:
                return CloudState(self.port).claim(self.task, owner="worker-"+str(i),
                    request_id="request-"+str(i), input_hash="v1")
            except Busy:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(isinstance(x, Claim) for x in outcomes), 1)

    def test_two_workers_reading_the_same_unclaimed_version_still_have_one_owner(self):
        gate, original = Barrier(2), self.port.read
        def read(ids):
            snapshot = original(ids)
            if len(ids) == 3 and snapshot.documents[self.task].value["active_run"] is None:
                gate.wait(timeout=2)
            return snapshot
        self.port.read = read
        def attempt(i):
            try:
                return self.claim("request-"+str(i), "worker-"+str(i))
            except Busy:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(isinstance(x, Claim) for x in outcomes), 1)

    def test_retry_of_same_claim_recovers_same_fence(self):
        first = self.claim()
        self.assertEqual(first, self.claim())
        self.assertEqual(first.fence, 1)

    def test_request_key_cannot_be_reused_for_other_input_or_owner(self):
        self.claim()
        for owner, digest in (("worker-b", "input-v1"), ("worker-a", "input-v2")):
            with self.assertRaises(Conflict):
                self.store.claim(self.task, owner=owner, request_id="request-1", input_hash=digest)

    def test_uncertain_claim_commit_is_recoverable_without_second_run(self):
        self.port.fail_after_commit = True
        with self.assertRaises(StoreUnavailable):
            self.claim()
        claim = self.claim()
        self.assertEqual(claim.fence, 1)
        self.assertEqual(len(self.port.rows), 2)

    def test_expired_owner_cannot_publish_after_takeover(self):
        old = self.claim()
        self.port.advance(31)
        new = self.claim("request-2", "worker-b")
        self.assertEqual(new.fence, old.fence + 1)
        with self.assertRaises(LeaseLost):
            self.store.finish(old, records={"result": {"fictional": True}})
        with self.assertRaises(LeaseLost):
            self.store.renew(old)
        self.assertEqual(self.store.read_task(self.task)["owner"], "worker-b")

    def test_old_worker_paused_after_read_cannot_commit_over_new_owner(self):
        old = self.claim()
        ready, resume, original = Event(), Event(), self.port.commit
        def commit(changes):
            task = changes.get(self.task, ({}, None))[0]
            if task.get("status") == "Needs you" and task.get("fence") == old.fence:
                ready.set()
                if not resume.wait(timeout=2):
                    raise AssertionError("test worker did not resume")
            return original(changes)
        self.port.commit = commit
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.store.finish, old, records={"result": {"fictional": True}})
            self.assertTrue(ready.wait(timeout=2))
            self.port.advance(31)
            new = self.claim("request-2", "worker-b")
            resume.set()
            with self.assertRaises(Conflict):
                pending.result(timeout=2)
        self.assertEqual(self.store.read_task(self.task)["fence"], new.fence)
        self.assertFalse(any(k.startswith("record_") for k in self.port.rows))

    def test_expired_request_does_not_silently_reacquire(self):
        self.claim()
        self.port.advance(31)
        with self.assertRaises(LeaseLost):
            self.claim()
        self.assertEqual(self.store.read_task(self.task)["status"], "Needs you")

    def test_heartbeat_extends_lease_without_changing_fence(self):
        claim = self.claim()
        self.port.advance(20)
        renewed = self.store.renew(claim, 60)
        self.port.advance(20)
        with self.assertRaises(Busy):
            self.claim("other-request", "worker-b")
        self.assertEqual(claim.fence, renewed.fence)
        self.assertEqual(self.claim().expires_at, renewed.expires_at)

    def test_result_and_baseline_survive_a_new_client_and_exact_retry(self):
        claim, baseline = self.claim(), self.baseline()
        records = {"completion": {"score": 10, "provenance": "synthetic"}}
        receipt = self.store.finish(claim, records=records, baseline=baseline, complete_scan=True)
        restarted = CloudState(self.port)
        self.assertEqual(restarted.read_task(self.task)["baseline"]["fingerprint"], "revision-1")
        self.assertEqual(restarted.finish(claim, records=records, baseline=baseline, complete_scan=True), receipt)
        self.assertTrue(self.claim().completed)
        self.assertEqual(len(self.port.rows), 4)
        self.assertEqual(restarted.read_task(self.task)["status"], "Needs you")

    def test_uncertain_result_commit_recovers_receipt_and_does_not_duplicate(self):
        claim = self.claim()
        self.port.fail_after_commit = True
        with self.assertRaises(StoreUnavailable):
            self.store.finish(claim, records={"result": {"test": 1}})
        receipt = self.store.finish(claim, records={"result": {"test": 1}})
        self.assertEqual(len(self.port.rows), 4)
        self.assertEqual(receipt["run_id"], claim.run_id)
        with self.assertRaises(Conflict):
            self.store.finish(claim, records={"result": {"test": 2}})

    def test_retry_checks_actual_stored_result_not_only_receipt(self):
        claim = self.claim()
        receipt = self.store.finish(claim, records={"result": {"test": 1}})
        del self.port.rows[receipt["record_ids"]["result"]]
        with self.assertRaises(StoreUnavailable):
            self.store.finish(claim, records={"result": {"test": 1}})

    def test_partial_scan_cannot_replace_baseline(self):
        first = self.claim()
        self.store.finish(first, records={"result": {}}, baseline=self.baseline(), complete_scan=True)
        baseline = self.store.read_task(self.task)["baseline"]
        second = self.claim("request-2", input_hash="input-v2")
        with self.assertRaises(ValueError):
            self.store.finish(second, records={"partial": {}}, baseline=self.baseline(), complete_scan=False)
        self.store.release_for_review(second, "partial_submission_history")
        self.assertEqual(self.store.read_task(self.task)["baseline"], baseline)
        with self.assertRaises(LeaseLost):
            self.store.finish(second, records={"result": {}})

    def test_stale_checkpoint_scope_and_time_cannot_overwrite_good_baseline(self):
        first = self.claim()
        self.store.finish(first, records={"result": {}}, baseline=self.baseline(), complete_scan=True)
        token = self.store.read_task(self.task)["baseline"]["checkpoint_token"]
        second = self.claim("request-2", input_hash="input-v2")
        with self.assertRaises(Conflict):
            self.store.finish(second, records={"r": {}}, baseline=self.baseline(),
                              complete_scan=True, expected_checkpoint="wrong")
        with self.assertRaises(Conflict):
            self.store.finish(second, records={"r": {}}, baseline=self.baseline(),
                              complete_scan=True, expected_checkpoint=token)
        self.port.advance(1)
        with self.assertRaises(Conflict):
            self.store.finish(second, records={"r": {}}, baseline={**self.baseline(), "scope": "other"},
                              complete_scan=True, expected_checkpoint=token)

    def test_forged_owner_cannot_release_another_workers_task(self):
        claim = self.claim()
        forged = Claim(claim.task_id, claim.run_id, "other", claim.fence, claim.input_hash, claim.expires_at)
        with self.assertRaises(LeaseLost):
            self.store.release_for_review(forged, "failure")

    def test_new_request_for_same_completed_input_reuses_existing_result(self):
        first = self.claim()
        self.store.finish(first, records={"result": {"fictional": True}})
        count = len(self.port.rows)
        repeated = self.claim("new-request", "worker-b")
        self.assertTrue(repeated.completed)
        self.assertEqual(repeated.run_id, first.run_id)
        self.assertEqual(len(self.port.rows), count)

    def test_nonfinite_or_oversized_values_rejected_before_write(self):
        claim = self.claim()
        for value in ({"bad": float("nan")}, {"huge": "x" * 260000}):
            with self.assertRaises(ValueError):
                self.store.finish(claim, records={"result": value})
        self.assertEqual(len(self.port.rows), 2)

    def test_cas_failure_is_atomic(self):
        before = deepcopy(self.port.rows)
        with self.assertRaises(Conflict):
            self.port.commit({"unrelated": ({"must_not_exist": True}, None),
                              self.task: ({}, "wrong-version")})
        self.assertEqual(before, self.port.rows)
