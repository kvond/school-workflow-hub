from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from school_workflow_hub.adapters.guarded_platform import GuardedPlatformAdapter, LocalSyntheticPlatformAdapter, SyntheticInterruption
from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.permissions import PermissionDenied, PermissionPolicy
from school_workflow_hub.core.runs import create_or_get_run
from school_workflow_hub.core.spine import AuthorizationDenied, AuthorizationRequest, issue_one_time_approval


class GuardedPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.connection = initialize_database(Path(self.temp.name) / "synthetic.sqlite3")
        self.addCleanup(self.connection.close)
        self.run = create_or_get_run(self.connection, "platform-test", {"fixture": "synthetic-one"})
        self.target = "synthetic:page-one"
        self.before = {"title": "Synthetic old", "deck_reference": "https://example.invalid/slides/one/copy"}
        self.after = dict(self.before, title="Synthetic revised")
        self.fake = LocalSyntheticPlatformAdapter(self.connection, {self.target: self.before})
        self.policy = PermissionPolicy.from_allowed(["local.read", "local.create"])

    def plan(self):
        return self.fake.plan(self.run.id, self.fake.snapshot(self.target), self.after)

    def approval(self, plan, nonce="fixture-approval"):
        request = AuthorizationRequest("operator:synthetic", "local.create", "local_synthetic_platform", plan.target_id, plan.plan_hash)
        grant = issue_one_time_approval(self.connection, run_id=plan.run_id, request=request, grant_nonce=nonce, expires_at="2099-01-01T00:00:00+00:00")
        return dict(grant_id=grant.id, request=request, policy=self.policy)

    def test_real_mutation_is_hard_denied_even_with_external_permission(self):
        policy = PermissionPolicy(frozenset({"external.update", "destructive"}), allow_external_writes=True)
        for system in ("schoology", "infinite_campus"):
            adapter = GuardedPlatformAdapter(system)
            self.assertEqual(adapter.inspect(self.target).status, "not_configured")
            self.assertEqual(adapter.snapshot(self.target).status, "not_configured")
            for mutation in (adapter.apply, adapter.rollback):
                with self.assertRaises(PermissionDenied):
                    mutation(None, grant_id="synthetic:grant", request=None, policy=policy)

    def test_missing_and_mismatched_approval_cannot_mutate_fake(self):
        plan = self.plan()
        approved = self.approval(plan)
        with self.assertRaises(AuthorizationDenied):
            self.fake.apply(plan, **dict(approved, grant_id="synthetic:missing"))
        with self.assertRaises(AuthorizationDenied):
            self.fake.apply(plan, **dict(approved, request=replace(approved["request"], target_stable_id="synthetic:other")))
        self.assertEqual(self.fake.inspect(self.target).data["fields"], self.before)
        self.assertEqual(self.fake.local_apply_count, 0)

    def test_exact_approval_apply_readback_repeat_and_rollback(self):
        plan = self.plan()
        approved = self.approval(plan)
        self.assertEqual(self.fake.readback(plan).status, "not_applied")
        self.assertEqual(self.fake.apply(plan, **approved).status, "simulated")
        self.assertEqual(self.fake.readback(plan).status, "verified")
        self.assertEqual(self.fake.apply(plan, **approved).status, "no_op")
        self.assertEqual(self.fake.local_apply_count, 1)
        rollback = self.fake.plan_rollback(self.run.id, plan)
        with self.assertRaises(AuthorizationDenied):
            self.fake.rollback(rollback, **approved)
        rollback_approval = self.approval(rollback, "fixture-rollback-approval")
        self.assertEqual(self.fake.rollback(rollback, **rollback_approval).status, "simulated")
        self.assertEqual(self.fake.readback(rollback).status, "verified")
        self.assertEqual(self.fake.inspect(self.target).data["fields"], self.before)
        self.assertEqual(self.fake.rollback(rollback, **rollback_approval).status, "no_op")
        self.assertEqual(self.fake.external_writes, 0)
        self.assertEqual(self.fake.network_calls, 0)

    def test_retry_before_and_after_partial_failure_does_not_reapply(self):
        for stage in ("before_apply", "after_apply"):
            with self.subTest(stage=stage):
                fake = LocalSyntheticPlatformAdapter(self.connection, {self.target: self.before})
                run = create_or_get_run(self.connection, "partial-test", {"stage": stage})
                plan = fake.plan(run.id, fake.snapshot(self.target), self.after)
                approved = self.approval(plan, "approval-" + stage)
                fake.inject_failure_once(stage)
                with self.assertRaises(SyntheticInterruption):
                    fake.apply(plan, **approved)
                retry = fake.apply(plan, **approved)
                self.assertIn(retry.status, {"simulated", "no_op"})
                self.assertEqual(fake.readback(plan).status, "verified")
                self.assertEqual(fake.local_apply_count, 1)
                self.assertEqual(fake.apply(plan, **approved).status, "no_op")
                self.assertEqual(fake.local_apply_count, 1)

    def test_approval_cannot_cross_runs_or_survive_plan_tampering(self):
        plan = self.plan()
        approved = self.approval(plan)
        second_run = create_or_get_run(self.connection, "platform-test", {"fixture": "synthetic-two"})
        other = self.fake.plan(second_run.id, self.fake.snapshot(self.target), self.after)
        with self.assertRaises(AuthorizationDenied):
            self.fake.apply(other, **approved)
        with self.assertRaises(AuthorizationDenied):
            self.fake.apply(replace(other, after=(("title", "Synthetic forged"),)), **approved)
        self.assertEqual(self.fake.local_apply_count, 0)

    def test_unavailable_login_and_real_targets_fail_closed(self):
        fake = LocalSyntheticPlatformAdapter(self.connection, {self.target: self.before}, login_available=False)
        self.assertEqual(fake.inspect(self.target).status, "login_unavailable")
        with self.assertRaises(ValueError):
            fake.snapshot(self.target)
        with self.assertRaises(ValueError):
            LocalSyntheticPlatformAdapter(self.connection, {"unverified-live-target": self.before})
        with self.assertRaises(ValueError):
            LocalSyntheticPlatformAdapter(self.connection, {self.target: {"real_grade": "not-allowed"}})

    def test_replanning_identical_input_reuses_local_work_item(self):
        first = self.plan()
        second = self.plan()
        self.assertEqual(first, second)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM work_items").fetchone()[0], 1)

    def test_changed_target_stops_stale_plan_and_rollback(self):
        old_plan = self.plan()
        other_plan = self.fake.plan(self.run.id, self.fake.snapshot(self.target), dict(self.before, title="Synthetic other"))
        self.fake.apply(other_plan, **self.approval(other_plan, "other-approval"))
        with self.assertRaises(ValueError):
            self.fake.apply(old_plan, **self.approval(old_plan, "old-approval"))
        rollback = self.fake.plan_rollback(self.run.id, other_plan)
        latest = self.fake.plan(self.run.id, self.fake.snapshot(self.target), dict(self.before, title="Synthetic newest"))
        self.fake.apply(latest, **self.approval(latest, "newest-approval"))
        with self.assertRaises(ValueError):
            self.fake.rollback(rollback, **self.approval(rollback, "stale-rollback"))


if __name__ == "__main__":
    unittest.main()
