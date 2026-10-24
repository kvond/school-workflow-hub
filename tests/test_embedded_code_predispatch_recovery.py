"""Fictional exact no-dispatch recovery; real CAS/SourceOperations, no providers."""
from copy import deepcopy
from datetime import datetime, timezone
import signal
import unittest
from unittest.mock import patch

from hub_v2 import embedded_code_repair as repair, embedded_code_predispatch_recovery as recovery
from hub_v2.source_operations import SourceOperations
from school_workflow_hub.core.cloud_state import Document, key
from tests import test_embedded_code_repair as fixtures
from tests.test_embedded_code_append_newline_style import expanded_write


class PredispatchRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.RepairTests.setUp(self)
        self.port.now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        self.operation = self.source.operations()[2]
        self.operation.update(placement='in_place', label_object_id='BellResponse')
        # This original already has its other two codes. Only BELL is missing.
        objects = repair._elements(self.source.native)
        for ident, text in [('InitialResponse', '[[NOTES:C09:Q1]] Original first answer 😀.'),
                            ('RevisedResponse', '[[DRAFT:C09:Q1]] Original revised answer 🌿.')]:
            objects[ident][2]['shape']['text'] = fixtures.shape(ident, text, 0)['shape']['text']
        chars = repair._chars(objects['BellResponse'][2])
        chars[-1][0][1].update(fontSize={'magnitude': 13, 'unit': 'PT'},
                             foregroundColor={'opaqueColor': {'rgbColor': {'green': .7}}})
        objects['BellResponse'][2]['shape']['text']['textElements'] = fixtures.text_elements(chars)
        patcher = patch.object(repair.google, 'slides_batch_update', side_effect=lambda *a: expanded_write(self.source, *a))
        patcher.start(); self.addCleanup(patcher.stop)
        self.original = self.ordinary()
        self.old_request = 'fictional-prior-request'
        self.old_ops = self.acquire(self.original['plan_id'], self.old_request)
        self.old_claim = self.old_ops.claim
        self.old_gate = key('embeddedcoderepair', [fixtures.FILE])
        self.port.commit({self.old_gate: ({'status': 'attempting', 'plan_id': self.original['plan_id'],
                                         'manifest': deepcopy(self.original['manifest'])}, None)})
        self.old_ops.release('predispatch_source_inspected')
        self.old_doc = deepcopy(self.port.rows[self.old_gate]); self.old_run = deepcopy(self.port.rows[self.old_claim.run_id])
        self.evidence = {'schema': recovery.SCHEMA, 'file_id': fixtures.FILE,
                         'prior_gate_id': self.old_gate, 'prior_gate_version': self.old_doc.version,
                         'prior_plan_id': self.original['plan_id'], 'prior_request_id': self.old_request,
                         'prior_claim': self.old_claim.__dict__, 'prior_run_version': self.old_run.version,
                         'prior_run_sha256': repair.digest(self.old_run.value),
                         'diagnostic_sha256': 'c' * 64, 'reconciliation_sha256': 'd' * 64,
                         'release_sha256': 'e' * 64, 'release_reason': 'predispatch_source_inspected',
                         'no_provider_request_started': True, 'complete_original_unchanged': True,
                         'same_source_claim_released': True, 'prior_run_receipt_unchanged': True}
        self.evidence_reads = 0; self.identity_checks = []; self.activity_calls = 0
        # A genuinely new identity observation changes the fresh proof, not the
        # original file/operation or the sole child identity.
        self.proof.update(original_proof_sha256='f' * 64, question_set_sha256='1' * 64)
        self.guard_result = {k: self.proof[k] for k in self.guard_result}

    def ordinary(self):
        return fixtures.RepairTests.plan(self, [self.operation])

    def authority(self, plan, request):
        return {'schema': 'school-source-maintenance/v1', 'authorization_ref': 'fictional-decision',
                'authorization_revision': 'fictional-revision', 'status': 'authorized',
                'source_scope': 'fictional-source-scope', 'owner': 'fictional-coordinator',
                'request_id': request, 'input_hash': plan, 'operation_scope': 'source_repair',
                'starts_at': '2026-10-23T04:00:00Z', 'expires_at': '2026-10-24T02:00:00Z'}

    def acquire(self, plan, request='fictional-new-request'):
        value = self.authority(plan, request)
        def activity():
            self.activity_calls = getattr(self, 'activity_calls', 0) + 1
        return SourceOperations.acquire(self.port, source_scope=value['source_scope'], owner=value['owner'],
            request_id=request, input_hash=plan, check_activity=activity, ttl_seconds=900,
            maintenance_authorization=value, read_maintenance_authorization=lambda ref: deepcopy(value),
            operation_scope='source_repair')

    def read_evidence(self):
        self.evidence_reads += 1
        return deepcopy(self.evidence)

    def plan(self):
        return recovery.prepare('read', self.ordinary(), self.port, guard=self.guard, read_evidence=self.read_evidence)

    def identity(self, **kwargs):
        self.identity_checks.append(kwargs.get('required_seconds'))

    def apply(self, plan=None, ops=None, **kwargs):
        plan = plan or self.plan(); ops = ops or self.acquire(plan['plan_id'])
        self.last_ops = ops
        return recovery.apply('read', 'write', plan, self.port, guard=self.guard, read_evidence=self.read_evidence,
                              source_operations=ops, identity_guard=self.identity, dry_run=False, **kwargs)

    def assert_old_preserved(self):
        self.assertEqual(self.port.rows[self.old_gate], self.old_doc)
        self.assertEqual(self.port.rows[self.old_claim.run_id], self.old_run)

    def test_prepare_is_read_only_and_fresh_identity_cannot_change_child_key(self):
        before = deepcopy(self.port.rows); first = self.plan()
        self.proof.update(original_proof_sha256='2' * 64, question_set_sha256='3' * 64)
        self.guard_result = {k: self.proof[k] for k in self.guard_result}
        second = self.plan()
        self.assertNotEqual(first['plan_id'], second['plan_id']); self.assertEqual(first['child_key'], second['child_key'])
        self.assertEqual(first['child_key'], recovery.child_key(self.old_gate, self.old_doc.version))
        self.assertEqual(self.port.rows, before); self.assertEqual(self.source.writes, 0)

    def test_one_native_post_preserves_original_and_prior_history_with_real_source_owner(self):
        before = repair.capture('read', fixtures.FILE); plan = self.plan(); ops = self.acquire(plan['plan_id'])
        calls = self.activity_calls; out = self.apply(plan, ops)
        self.assertEqual(out['status'], 'verified'); self.assertEqual(self.source.writes, 1)
        self.assertGreater(self.activity_calls, calls); self.assertGreater(ops.claim.fence, self.old_claim.fence)
        self.assertEqual(self.identity_checks[:2], [480, 480]); self.assert_old_preserved()
        self.assertEqual(self.port.rows[plan['child_key']].value, out)
        repair._preserved(before, repair.capture('read', fixtures.FILE), plan['edits'])
        self.assertFalse(out['grading_authorized']); self.assertFalse(out['full_census_complete'])

    def test_ordinary_writer_still_refuses_original_uncertainty_fence(self):
        with self.assertRaises(repair.Refused):
            repair.apply('read', 'write', self.ordinary(), self.port, guard=self.guard, dry_run=False)
        self.assertEqual(self.source.writes, 0); self.assert_old_preserved()

    def test_existing_child_even_verified_always_consumes_only_admission(self):
        plan = self.plan(); child = plan['child_key']
        for status in ('attempting', 'verified'):
            self.port.rows[child] = Document({'status': status}, 'closed-child')
            with self.subTest(status=status), self.assertRaises(repair.Refused): self.plan()
        self.assertEqual(self.source.writes, 0); self.assert_old_preserved()

    def test_child_cannot_be_used_as_parent(self):
        plan = self.plan(); self.evidence['prior_gate_id'] = plan['child_key']
        with self.assertRaisesRegex(repair.Refused, 'canonical original'): self.plan()
        self.assertEqual(self.source.writes, 0); self.assert_old_preserved()

    def test_missing_ambiguous_or_unproven_stage_evidence_refuses(self):
        original = deepcopy(self.evidence)
        for field, value in [('schema', 'ordinary-retry'), ('no_provider_request_started', False),
                             ('complete_original_unchanged', False), ('same_source_claim_released', False),
                             ('prior_run_receipt_unchanged', False), ('diagnostic_sha256', 'unverified')]:
            self.evidence = deepcopy(original); self.evidence[field] = value
            with self.subTest(field=field), self.assertRaises(repair.Refused): self.plan()
        self.evidence = deepcopy(original); self.evidence['extra_retry_permission'] = True
        with self.assertRaises(repair.Refused): self.plan()
        self.assertEqual(self.source.writes, 0); self.assert_old_preserved()

    def test_changed_original_attempt_version_or_manifest_cannot_admit(self):
        for field, value in [('version', 'new-version'), ('plan_id', '0' * 64), ('status', 'verified')]:
            self.port.rows[self.old_gate] = deepcopy(self.old_doc)
            if field == 'version': self.port.rows[self.old_gate] = Document(deepcopy(self.old_doc.value), value)
            else: self.port.rows[self.old_gate].value[field] = value
            with self.subTest(field=field), self.assertRaises(repair.Refused): self.plan()
        self.assertEqual(self.source.writes, 0)

    def test_unreleased_old_source_claim_or_changed_run_never_adopted(self):
        task = self.port.rows[self.old_claim.task_id]; saved = deepcopy(task.value)
        task.value['active_run'] = self.old_claim.run_id
        with self.assertRaises(repair.Refused): self.plan()
        task.value.clear(); task.value.update(saved)
        self.port.rows[self.old_claim.run_id].value['receipt'] = {'status': 'invented'}
        with self.assertRaises(repair.Refused): self.plan()
        self.assertEqual(self.source.writes, 0)

    def test_changed_source_revision_or_different_operation_cannot_be_admitted(self):
        self.source.meta['version'] = '9'
        with self.assertRaises(repair.Refused): self.plan()
        self.assertEqual(self.source.writes, 0); self.assert_old_preserved()

    def test_tampered_prepared_native_requests_cannot_be_sent(self):
        plan = self.plan(); plan['requests'][0]['insertText']['text'] += 'unreviewed'
        with self.assertRaises(repair.Refused): self.apply(plan)
        self.assertEqual(self.source.writes, 0); self.assert_old_preserved()

    def test_explicit_dry_run_never_claims_child_or_writes(self):
        plan = self.plan(); before = deepcopy(self.port.rows)
        out = recovery.apply('read', None, plan, self.port, guard=self.guard, read_evidence=self.read_evidence)
        self.assertTrue(out['dry_run']); self.assertEqual(self.port.rows, before); self.assertEqual(self.source.writes, 0)

    def test_fake_wrong_owner_or_different_plan_source_guard_refuses(self):
        plan = self.plan()
        with self.assertRaises(repair.Refused): self.apply(plan, ops=object())
        ops = self.acquire('another-plan')
        with self.assertRaises(repair.Refused): self.apply(plan, ops)
        self.assertNotIn(plan['child_key'], self.port.rows); self.assertEqual(self.source.writes, 0)

    def test_final_activity_hold_keeps_both_fences_and_cannot_be_readmitted(self):
        plan = self.plan(); ops = self.acquire(plan['plan_id'])
        ops.check_activity = lambda: (_ for _ in ()).throw(RuntimeError('actual activity hold'))
        with self.assertRaises(repair.Uncertain): self.apply(plan, ops)
        self.assertEqual(self.source.writes, 0); self.assertEqual(self.port.rows[plan['child_key']].value['status'], 'attempting')
        self.assert_old_preserved()
        with self.assertRaises(repair.Refused): self.plan()

    def test_unknown_native_response_keeps_both_fences_no_retry(self):
        plan = self.plan()
        def lost(*args):
            expanded_write(self.source, *args)
            raise OSError('unknown provider response')
        with patch.object(repair.google, 'slides_batch_update', side_effect=lost):
            with self.assertRaises(repair.Uncertain): self.apply(plan)
        self.assertEqual(self.source.writes, 1); self.assert_old_preserved()
        self.assertEqual(self.port.rows[plan['child_key']].value['status'], 'attempting')

    def test_lost_child_creation_response_does_not_retry_cas_or_source(self):
        plan = self.plan(); ops = self.acquire(plan['plan_id']); self.port.fail_after_commit = True
        with self.assertRaises(repair.Uncertain): self.apply(plan, ops)
        self.assertIn(plan['child_key'], self.port.rows); self.assertEqual(self.source.writes, 0); self.assert_old_preserved()

    def test_original_style_or_acl_drift_after_post_closes_child(self):
        def changed(source): source.native['notesMaster']['changed'] = True
        self.source.after_write = changed; plan = self.plan()
        with self.assertRaises(repair.Uncertain): self.apply(plan)
        self.assertEqual(self.source.writes, 1); self.assert_old_preserved()
        self.assertEqual(self.port.rows[plan['child_key']].value['status'], 'attempting')

    def test_lost_verified_receipt_response_is_read_back_without_second_post(self):
        def after(source): self.port.fail_after_commit = True
        self.source.after_write = after; out = self.apply()
        self.assertEqual(out['status'], 'verified'); self.assertEqual(self.source.writes, 1); self.assert_old_preserved()

    def test_whole_operation_deadline_closes_child_and_restores_timer(self):
        plan = self.plan()
        with patch.object(repair.google, 'slides_batch_update', side_effect=recovery._Deadline()):
            with self.assertRaises(repair.Uncertain): self.apply(plan)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0)); self.assert_old_preserved()
        self.assertEqual(self.port.rows[plan['child_key']].value['status'], 'attempting')
