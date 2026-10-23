"""Synthetic rejected native batch; no school data or live requests."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from hub_v2.google import GoogleError
from school_workflow_hub.adapters import roster_bootstrap as rb, roster_rejection as rejection
from school_workflow_hub.core.cloud_state import CloudState, Conflict, Document, LeaseLost, StoreUnavailable
from school_workflow_hub.core.runs import content_hash
from tests import test_roster_bootstrap as fixtures


class RejectionTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.BootstrapTests('runTest'); fixture.setUp()
        self.f = fixture; self.port = fixture.port; self.cloud = CloudState(self.port)
        source_task = self.cloud.create_task(rejection.SOURCE_SCOPE)
        self.source = self.cloud.claim(source_task, owner='coordinator', request_id='bootstrap-1',
                                       input_hash='source-input-1', ttl_seconds=900)
        self.proposal = fixture.worker.prepare()
        fixture.mirror.apply = Mock(side_effect=GoogleError('provider rejected', status=403))
        with self.assertRaisesRegex(Exception, 'uncertain_native_write'):
            fixture.apply(self.proposal)
        self.historical_cap = {**fixture.mirror.write_capability(), 'can_edit': False}
        self.current_cap = deepcopy(self.historical_cap)
        fixture.mirror.write_capability = lambda: deepcopy(self.current_cap)
        self.failure = {'schema': 'roster-bootstrap-rejection-evidence/v1', 'observer': 'coordinator',
            'trace_reference': 'trace-1', 'trace_sha256': '1'*64, 'attempt_reference': 'attempt-1',
            'attempt_sha256': '2'*64, 'native_code_sha256': '3'*64,
            'proposal_sha256': self.proposal['sha256'], 'native_operation': 'spreadsheets.batchUpdate',
            'status': 403, 'provider_reason': None, 'original_source_claim': self.source.__dict__,
            'historical_capability': self.historical_cap, 'historical_capability_reference': 'capability-1',
            'historical_capability_sha256': '4'*64}
        self.guard = Mock()
        self.recorder = rejection.RejectedBootstrap(self.port, fixture.mirror,
            read_config=lambda: deepcopy(fixture.config), read_failure=lambda: deepcopy(self.failure),
            source_claim=self.source, before_write=self.guard)

    def record(self):
        return self.recorder.record_rejected_bootstrap(self.recorder.prepare())

    def test_immutable_diagnostic_preserves_every_pending_record_and_never_retries(self):
        original = deepcopy(self.port.rows)
        prepared = self.recorder.prepare()
        result = self.recorder.record_rejected_bootstrap(prepared)
        self.assertEqual(result['status'], 'verified_rejection_record')
        self.assertFalse(result['retry_allowed']); self.assertFalse(result['activation_allowed'])
        self.assertEqual(len(self.port.rows), len(original)+1)
        self.assertEqual({k:self.port.rows[k] for k in original}, original)
        self.f.mirror.apply.assert_called_once()
        self.guard.assert_called_once_with(required_seconds=rejection.RESERVATION_SECONDS)
        self.assertEqual(self.port.rows[result['record_id']].value['native_batch_sha256'],
                         content_hash(rb.native_requests(self.proposal['plan'])))
        before = deepcopy(self.port.rows)
        self.assertEqual(self.recorder.record_rejected_bootstrap(prepared), result)
        self.assertEqual(before, self.port.rows)
        with self.assertRaisesRegex(Exception, 'existing_intent'):
            self.f.apply(self.proposal)

    def test_expired_original_domain_is_preserved_history_not_reacquired(self):
        self.port.advance(301)
        original_domain = deepcopy(self.port.rows[self.f.worker.task_id])
        self.record()
        self.assertEqual(self.port.rows[self.f.worker.task_id], original_domain)

    def test_fresh_current_edit_permission_does_not_erase_historical_denial(self):
        self.current_cap['can_edit'] = True
        result = self.record()
        value = self.port.rows[result['record_id']].value
        self.assertTrue(value['current_capability']['can_edit'])
        self.assertFalse(value['native_evidence']['historical_capability']['can_edit'])
        self.assertFalse(value['retry_allowed'])

    def test_changed_original_or_any_new_target_refuses_without_sidecar(self):
        original = deepcopy(self.f.mirror.data)
        for mode in ('cell', 'target'):
            self.f.mirror.data = deepcopy(original)
            if mode == 'cell': self.f.mirror.data['sheets']['1']['cells']['1:3']['userEnteredValue'] = {'formulaValue':'=2+2'}
            else: self.f.mirror.data['sheets']['101'] = deepcopy(original['sheets']['1'])
            prior = deepcopy(self.port.rows)
            with self.assertRaisesRegex(Conflict, 'original_or_config_changed'): self.record()
            self.assertEqual(self.port.rows, prior)

    def test_missing_or_non403_direct_trace_is_not_reclassified(self):
        original = deepcopy(self.failure)
        for field, value in [('status', 429), ('status', '403'), ('native_operation','read'),
                             ('trace_sha256',''), ('provider_reason','guessed'), ('observer','other')]:
            self.failure = {**deepcopy(original), field:value}
            with self.subTest(field=field), self.assertRaises((Conflict, ValueError)): self.record()
        self.f.mirror.apply.assert_called_once()

    def test_capability_identity_missing_fields_and_nonbool_hold(self):
        original = deepcopy(self.current_cap)
        for cap in ({}, {**original,'identity':'other'}, {**original,'can_edit':'false'}, {**original,'workbook':'elsewhere'}):
            self.current_cap = cap
            with self.subTest(cap=cap), self.assertRaisesRegex(Conflict, 'capability_unverified'): self.record()
        self.current_cap = original
        self.failure['historical_capability']['can_edit'] = True
        with self.assertRaisesRegex(Conflict, 'historical_denial_required'): self.record()

    def test_domain_owner_or_intent_change_holds(self):
        original = deepcopy(self.port.rows)
        for rid, field, value in [(self.f.worker.task_id, 'owner','other'),
                                  (self.f.worker.intent_key,'state','verified'),
                                  (self.f.worker.sync_intent_key,'state','verified')]:
            self.port.rows = deepcopy(original)
            prior = self.port.rows[rid]
            self.port.rows[rid] = Document({**prior.value,field:value},prior.version)
            with self.subTest(field=field), self.assertRaises(Conflict): self.record()

    def test_foreign_active_source_claim_or_expiry_refuses(self):
        self.port.advance(901)
        with self.assertRaises(LeaseLost): self.record()
        self.cloud.claim(self.source.task_id, owner='other', request_id='other-source', input_hash='other', ttl_seconds=900)
        with self.assertRaises(LeaseLost): self.record()

    def test_final_dependency_race_has_zero_sidecar_writes(self):
        prepared = self.recorder.prepare()
        def takeover(**kwargs):
            doc = self.port.rows[self.f.worker.task_id]
            self.port.rows[self.f.worker.task_id] = Document({**doc.value,'owner':'other'},'new-version')
        self.guard.side_effect = takeover
        with self.assertRaises(Conflict): self.recorder.record_rejected_bootstrap(prepared)
        self.assertNotIn(prepared['record_id'],self.port.rows)

    def test_slow_guard_or_final_read_cannot_authorize_expired_source(self):
        prepared = self.recorder.prepare()
        self.guard.side_effect = lambda **kwargs:self.port.advance(901)
        with self.assertRaises(LeaseLost): self.recorder.record_rejected_bootstrap(prepared)
        self.assertNotIn(prepared['record_id'],self.port.rows)

    def test_post_commit_race_leaves_unaccepted_sidecar_and_pending_barriers(self):
        prepared = self.recorder.prepare(); original_commit = self.port.commit_guarded
        def race(changes, **kwargs):
            original_commit(changes, **kwargs)
            self.f.mirror.data['sheets']['1']['cells']['1:3']['userEnteredValue'] = {'formulaValue':'=9+9'}
        self.port.commit_guarded = race
        with self.assertRaises(Conflict): self.recorder.record_rejected_bootstrap(prepared)
        self.assertIn(prepared['record_id'],self.port.rows)
        self.assertEqual(self.port.rows[self.f.worker.intent_key].value['state'],'pending')
        self.assertEqual(self.port.rows[self.f.worker.sync_intent_key].value['state'],'pending')
        self.f.mirror.apply.assert_called_once()

    def test_lost_commit_response_reads_back_once_without_repeat(self):
        self.port.fail_after_commit = True
        result = self.record()
        self.assertEqual(result['status'],'verified_rejection_record')
        self.guard.assert_called_once()
        self.f.mirror.apply.assert_called_once()

    def test_failed_read_never_proves_rejection(self):
        self.f.mirror.read = Mock(side_effect=StoreUnavailable('unavailable'))
        prior = deepcopy(self.port.rows)
        with self.assertRaises(StoreUnavailable): self.record()
        self.assertEqual(prior,self.port.rows)


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.BootstrapTests('runTest'); self.f.setUp()
        self.native = rb.SheetsBootstrap(self.f.config)

    def test_real_reader_uses_exact_workbook_and_writer_capability(self):
        with patch('hub_v2.google.service_token',return_value='fictional-token') as token, \
             patch('hub_v2.google.drive_file',return_value={'id':self.native.workbook,'capabilities':{'canEdit':False}}) as read:
            value = self.native.write_capability()
        token.assert_called_once_with(self.f.config,write=True)
        read.assert_called_once_with('fictional-token',self.native.workbook,'id,capabilities(canEdit)')
        self.assertFalse(value['can_edit'])

    def test_uneditable_or_unavailable_capability_stops_before_domain_intent(self):
        proposal = self.f.worker.prepare()
        for value in ({'can_edit':False}, {}, {'can_edit':'true'}):
            self.f.mirror.write_capability = lambda: value
            with self.assertRaisesRegex(Exception,'writer_not_editable'):self.f.apply(proposal)
            self.assertEqual(self.f.port.rows,{})
            self.assertEqual(self.f.mirror.writes,0)
        self.f.mirror.write_capability = Mock(side_effect=GoogleError('unavailable',status=503))
        with self.assertRaises(GoogleError):self.f.apply(proposal)
        self.assertEqual(self.f.port.rows,{})

    def test_native_builder_rechecks_capability_before_the_final_guard_or_post(self):
        plan = self.f.worker.prepare()['plan']
        with patch.object(self.native,'read',return_value=plan['original']), \
             patch('hub_v2.google.service_token',return_value='fictional-token'), \
             patch.object(self.native,'write_capability',return_value={'can_edit':False}), \
             patch('hub_v2.google.batch_update') as post:
            with self.assertRaisesRegex(Exception,'writer_not_editable'):self.native.apply(plan,self.f.guard)
        post.assert_not_called();self.f.guard.assert_not_called()
