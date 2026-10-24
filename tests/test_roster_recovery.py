"""Explicit synthetic admission; no credentials, native school rows or APIs."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import Mock, patch
from school_workflow_hub.adapters import roster_bootstrap as rb, roster_recovery as recovery
from school_workflow_hub.core.cloud_state import CloudState, Conflict, Document, StoreUnavailable
from tests import test_roster_rejection as rejection_fixtures
from tests.test_roster_bootstrap import Workbook
from school_workflow_hub.core.runs import content_hash


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        f=rejection_fixtures.RejectionTests('runTest');f.setUp();self.rejection=f
        self.f=f.f;self.port=f.port;self.cloud=CloudState(self.port)
        self.port.advance(301)
        rejected=f.record();self.rejected_id=rejected['record_id']
        self.original_intent=deepcopy(self.port.rows[self.f.worker.intent_key])
        self.original_marker=deepcopy(self.port.rows[self.f.worker.sync_intent_key])
        self.old_run_id=self.original_intent.value['claim']['run_id']
        self.old_run=deepcopy(self.port.rows[self.old_run_id])
        self.f.mirror.apply=Workbook.apply.__get__(self.f.mirror,Workbook)
        f.current_cap['can_edit']=True
        self.f.captures=[self.f.capture('fresh-first',8),self.f.capture('fresh-second',3)]
        self.proposal=self.f.worker.prepare()
        self.authority={'schema':'roster-bootstrap-recovery-authorization/v1','authorization_ref':'explicit-root-1',
            'revision':'revision-1','owner':'coordinator','request_id':'new-explicit-bootstrap-2',
            'proposal_sha256':self.proposal['sha256'],'prior_intent_sha256':content_hash(self.original_intent.value),
            'rejection_reference':self.rejected_id,'rejection_sha256':content_hash(self.port.rows[self.rejected_id].value),
            'max_attempts':1,'status':'authorized','starts_at':(self.port.now-timedelta(minutes=1)).isoformat(),
            'expires_at':(self.port.now+timedelta(minutes=10)).isoformat()}
        self.guard=Mock()
        self.worker=recovery.RecoveryBootstrap(self.port,self.f.reader,self.f.mirror,
            read_config=lambda:deepcopy(self.f.config),targets=self.f.targets,titles=self.f.titles,
            before_write=self.guard,source_claim=f.source,read_authorization=lambda:deepcopy(self.authority))

    def authorize(self):
        preview=self.worker.prepare_recovery(self.proposal)
        result=self.worker.authorize_recovery(preview)
        self.assertEqual(result['status'],'authorized_not_dispatched')
        self.assertEqual(self.f.mirror.writes,0)
        return preview['admission_id']

    def test_explicit_consumption_permits_one_new_batch_preserving_old_evidence(self):
        aid=self.authorize();result=self.worker.execute_recovery(aid)
        self.assertEqual(result['status'],'verified_not_activated')
        self.assertEqual(self.f.mirror.writes,1)
        self.assertEqual(self.port.rows[self.f.worker.intent_key],self.original_intent)
        self.assertEqual(self.port.rows[self.old_run_id],self.old_run)
        self.assertEqual(self.port.rows[aid].value['prior_pointer']['value'],self.original_marker.value)
        self.assertEqual(self.port.rows[aid].value['prior_pointer']['version'],self.original_marker.version)
        self.assertEqual(self.port.rows[aid].value['state'],'consumed')
        self.assertEqual(result['recovery']['intent_reference'],self.worker.intent_key)
        record=self.port.rows[result['receipt']['record_ids']['roster_bootstrap']].value
        self.assertEqual(record['payload']['recovery'],result['recovery'])
        with self.assertRaisesRegex(Conflict,'consumed'):self.worker.execute_recovery(aid)
        self.assertEqual(self.worker.reconcile_recovery(aid),result)
        self.assertEqual(self.f.mirror.writes,1)

    def test_access_repair_alone_never_authorizes_or_bypasses_old_barrier(self):
        with self.assertRaisesRegex(Exception,'existing_intent'):self.f.apply(self.proposal)
        self.authority['status']='revoked'
        with self.assertRaisesRegex(Conflict,'authority'):self.authorize()
        self.assertEqual(self.f.mirror.writes,0)

    def test_wrong_request_expired_authority_stale_sources_and_changed_cells_hold(self):
        original=deepcopy(self.authority)
        for field,value in [('request_id','bootstrap-1'),('max_attempts',2),('owner','other'),
                            ('expires_at',(self.port.now-timedelta(seconds=1)).isoformat())]:
            self.authority={**original,field:value}
            with self.subTest(field=field),self.assertRaises(Conflict):self.authorize()
        self.authority=original;self.port.advance(901)
        with self.assertRaises(Exception):self.authorize()
        self.assertEqual(self.f.mirror.writes,0)

    def test_lost_consumption_response_never_dispatches_even_when_saved(self):
        aid=self.authorize();original=self.port.commit_guarded
        def lose(changes,**kwargs):
            if aid in changes and changes[aid][0]['state']=='consumed':self.port.fail_after_commit=True
            return original(changes,**kwargs)
        self.port.commit_guarded=lose
        with self.assertRaisesRegex(Conflict,'consumption_unconfirmed'):self.worker.execute_recovery(aid)
        self.assertEqual(self.port.rows[aid].value['state'],'consumed')
        self.assertEqual(self.f.mirror.writes,0)
        with self.assertRaisesRegex(Conflict,'consumed'):self.worker.execute_recovery(aid)
        with self.assertRaisesRegex(Exception,'readback_scope_mismatch'):self.worker.reconcile_recovery(aid)
        self.assertEqual(self.port.rows[self.f.worker.intent_key],self.original_intent)

    def test_uncertain_native_response_only_reads_new_destination(self):
        aid=self.authorize();self.f.mirror.lose_response=True
        with self.assertRaisesRegex(Conflict,'uncertain_native'):self.worker.execute_recovery(aid)
        self.assertEqual(self.f.mirror.writes,1)
        result=self.worker.reconcile_recovery(aid)
        self.assertEqual(result['status'],'verified_not_activated')
        self.assertEqual(self.f.mirror.writes,1)
        with self.assertRaisesRegex(Exception,'readback_mismatch'):self.f.worker.reconcile()

    def test_authorization_changed_after_admission_does_not_dispatch(self):
        aid=self.authorize();self.authority['revision']='another'
        with self.assertRaises(Conflict):self.worker.execute_recovery(aid)
        self.assertEqual(self.f.mirror.writes,0)

    def test_pointer_race_before_consumption_refuses(self):
        aid=self.authorize();old=self.port.rows[self.f.worker.sync_intent_key]
        self.port.rows[self.f.worker.sync_intent_key]=Document({**old.value,'state':'changed'},'changed-version')
        with self.assertRaises(Exception):self.worker.execute_recovery(aid)
        self.assertEqual(self.f.mirror.writes,0)

    def test_ordinary_writer_stays_blocked_after_consumption(self):
        from school_workflow_hub.adapters.roster_worker import RosterWorker
        from tests.test_roster_worker import Mirror
        aid=self.authorize();self.f.mirror.bad_readback=True
        with self.assertRaises(Conflict):self.worker.execute_recovery(aid)
        other=RosterWorker(self.port,Mock(read_complete=Mock()),Mirror({}),self.f.targets,before_write=Mock())
        with self.assertRaisesRegex(Exception,'uncertain_write_requires_readback'):
            other.run(owner='another',request_id='ordinary-attempt')


    def test_actual_source_operations_guards_recovery_and_helper_activity(self):
        from hub_v2.source_operations import SourceOperations, Held
        idle=[True]
        def activity():
            if not idle[0]:raise Held('helper_inputs_frozen')
        ops=SourceOperations.acquire(self.port,source_scope='school-morning-preparation-and-repair',
            owner='coordinator',request_id='bootstrap-1',input_hash='source-input-1',check_activity=activity)
        self.worker.source_claim=ops.claim;self.worker.before_write=ops.before_write
        aid=self.authorize()
        result=self.worker.execute_recovery(aid)
        self.assertEqual(result['status'],'verified_not_activated')
        self.assertEqual(self.f.mirror.writes,1)
        self.setUp()
        ops=SourceOperations.acquire(self.port,source_scope='school-morning-preparation-and-repair',
            owner='coordinator',request_id='bootstrap-1',input_hash='source-input-1',check_activity=activity)
        self.worker.source_claim=ops.claim;self.worker.before_write=ops.before_write
        aid=self.authorize();idle[0]=False
        with self.assertRaises(Held):self.worker.execute_recovery(aid)
        self.assertEqual(self.f.mirror.writes,0)
        self.assertEqual(self.port.rows[aid].value['state'],'authorized')

    def test_real_firestore_adapter_cas_preserves_original_and_consumes_once(self):
        from school_workflow_hub.adapters.firestore_state import FirestoreState
        from tests.test_roster_firestore_integration import NativeStateTransport
        class NativePort(FirestoreState):
            def __init__(self):
                self.wire=NativeStateTransport()
                super().__init__('fictional-hub',token_provider=lambda:'synthetic',transport=self.wire)
            @property
            def now(self):return self.wire.now
            @now.setter
            def now(self,value):self.wire.now=value
            def advance(self,seconds):self.wire.now+=timedelta(seconds=seconds)
            @property
            def rows(self):
                return {name[len(self.prefix):]:Document(deepcopy(value),version)
                        for name,(value,version)in self.wire.rows.items()}
        with patch('tests.test_roster_bootstrap.GuardedCAS',NativePort):self.setUp()
        aid=self.authorize();result=self.worker.execute_recovery(aid)
        self.assertEqual(result['status'],'verified_not_activated')
        self.assertEqual(self.port.rows[self.f.worker.intent_key],self.original_intent)
        consumption=[body for op,body in self.port.wire.calls if op=='commit'
            and any(w['update']['name']==self.port.prefix+aid
                and '"state":"consumed"'in w['update']['fields']['payload']['stringValue']for w in body['writes'])]
        self.assertEqual(len(consumption),1)
        self.assertEqual(len(consumption[0]['writes']),4)
        self.assertEqual({w['update']['name']for w in consumption[0]['writes']},
            {self.port.prefix+k for k in (aid,self.worker.intent_key,self.worker.sync_intent_key,self.worker.task_id)})
        with self.assertRaisesRegex(Conflict,'consumed'):self.worker.execute_recovery(aid)
        self.assertEqual(self.f.mirror.writes,1)

    def test_verified_reconcile_refuses_changed_dynamic_pointer(self):
        aid=self.authorize();self.worker.execute_recovery(aid)
        pointer=self.port.rows[self.worker.sync_intent_key]
        self.port.rows[self.worker.sync_intent_key]=Document({**pointer.value,'bootstrap_intent':self.f.worker.intent_key},'changed')
        with self.assertRaisesRegex(Conflict,'readback_changed'):self.worker.reconcile_recovery(aid)
        self.assertEqual(self.f.mirror.writes,1)

    def test_authority_revoked_during_final_shared_observation_stops_native(self):
        aid=self.authorize()
        def guard(**kwargs):
            if kwargs.get('required_seconds')==170+recovery.FINAL_SECONDS:self.authority['status']='revoked'
        self.worker.before_write=guard
        with self.assertRaisesRegex(Conflict,'uncertain_native'):self.worker.execute_recovery(aid)
        self.assertEqual(self.f.mirror.writes,0)
        self.assertEqual(self.port.rows[aid].value['state'],'consumed')

    def test_domain_expiry_or_takeover_during_final_guard_stops_native(self):
        for mode in ('expiry','takeover'):
            self.setUp();aid=self.authorize()
            def guard(**kwargs):
                if kwargs.get('required_seconds')==170+recovery.FINAL_SECONDS:
                    if mode=='expiry':self.port.advance(901)
                    else:
                        task=self.port.rows[self.worker.task_id]
                        self.port.rows[self.worker.task_id]=Document({**task.value,'owner':'foreign','fence':99},'foreign-version')
            self.worker.before_write=guard
            with self.subTest(mode=mode),self.assertRaisesRegex(Conflict,'uncertain_native'):
                self.worker.execute_recovery(aid)
            self.assertEqual(self.f.mirror.writes,0)
            self.assertEqual(self.port.rows[aid].value['state'],'consumed')

    def test_permission_removed_after_authorization_has_zero_native_writes(self):
        aid=self.authorize();self.rejection.current_cap['can_edit']=False
        with self.assertRaisesRegex(Exception,'writer_not_editable'):self.worker.execute_recovery(aid)
        self.assertEqual(self.f.mirror.writes,0)
        self.assertEqual(self.port.rows[aid].value['state'],'authorized')

    def test_ten_second_authority_cannot_authorize_state_or_native_write(self):
        self.authority['expires_at']=(self.port.now+timedelta(seconds=10)).isoformat()
        preview=self.worker.prepare_recovery(self.proposal);before=deepcopy(self.port.rows)
        with self.assertRaisesRegex(Conflict,'authority_budget_unavailable'):
            self.worker.authorize_recovery(preview)
        self.assertEqual(self.port.rows,before);self.assertEqual(self.f.mirror.writes,0)

    def test_native_budget_boundary_keeps_consumed_barrier_if_one_second_short(self):
        for remaining in (170,171):
            self.setUp();self.authority['expires_at']=(self.port.now+timedelta(seconds=remaining)).isoformat()
            aid=self.authorize()
            if remaining==170:
                with self.assertRaisesRegex(Conflict,'uncertain_native'):self.worker.execute_recovery(aid)
                self.assertEqual(self.f.mirror.writes,0)
                self.assertEqual(self.port.rows[aid].value['state'],'consumed')
                self.assertEqual(self.port.rows[self.worker.intent_key].value['state'],'pending')
                self.assertEqual(self.port.rows[self.f.worker.intent_key],self.original_intent)
                with self.assertRaisesRegex(Conflict,'consumed'):self.worker.execute_recovery(aid)
            else:
                self.assertEqual(self.worker.execute_recovery(aid)['status'],'verified_not_activated')
                self.assertEqual(self.f.mirror.writes,1)

    def test_slow_authorization_proof_reserves_state_readback_before_cas(self):
        self.authority['expires_at']=(self.port.now+timedelta(seconds=120)).isoformat()
        preview=self.worker.prepare_recovery(self.proposal);before=deepcopy(self.port.rows)
        def slow(**kwargs):self.port.advance(61)
        self.worker.before_write=slow
        with self.assertRaisesRegex(Conflict,'authority_budget_unavailable'):
            self.worker.authorize_recovery(preview)
        self.assertEqual(self.port.rows,before);self.assertEqual(self.f.mirror.writes,0)

    def test_slow_consumption_proof_keeps_original_pending_pointer(self):
        self.authority['expires_at']=(self.port.now+timedelta(seconds=240)).isoformat();aid=self.authorize()
        def slow(**kwargs):
            if kwargs['required_seconds']==recovery.PROOF_SECONDS+recovery.STATE_SECONDS:self.port.advance(181)
        self.worker.before_write=slow
        with self.assertRaisesRegex(Conflict,'consumption_unconfirmed'):self.worker.execute_recovery(aid)
        self.assertEqual(self.port.rows[aid].value['state'],'authorized')
        self.assertEqual(self.port.rows[self.worker.sync_intent_key],self.original_marker)
        self.assertNotIn(self.worker.intent_key,self.port.rows);self.assertEqual(self.f.mirror.writes,0)

    def test_slow_final_native_read_requires_full_remaining_authority(self):
        self.authority['expires_at']=(self.port.now+timedelta(seconds=200)).isoformat();aid=self.authorize()
        native=[False];cap=self.f.mirror.write_capability
        def guard(**kwargs):
            if kwargs['required_seconds']==170+recovery.FINAL_SECONDS:native[0]=True
        def delayed_capability():
            if native[0]:self.port.advance(31)
            return cap()
        self.worker.before_write=guard;self.f.mirror.write_capability=delayed_capability
        with self.assertRaisesRegex(Conflict,'uncertain_native'):self.worker.execute_recovery(aid)
        self.assertEqual(self.f.mirror.writes,0);self.assertEqual(self.port.rows[aid].value['state'],'consumed')
        self.assertEqual(self.port.rows[self.worker.intent_key].value['state'],'pending')

    def test_final_elapsed_time_cannot_extend_authority_with_static_server_time(self):
        self.authority['expires_at']=(self.port.now+timedelta(seconds=200)).isoformat();aid=self.authorize()
        tick=[0.0];cap=self.f.mirror.write_capability;native=[False]
        def guard(**kwargs):
            if kwargs['required_seconds']==170+recovery.FINAL_SECONDS:native[0]=True
        def delayed_capability():
            if native[0]:tick[0]+=31
            return cap()
        self.worker.before_write=guard;self.f.mirror.write_capability=delayed_capability
        with patch.object(recovery,'monotonic',side_effect=lambda:tick[0]):
            with self.assertRaisesRegex(Conflict,'uncertain_native'):self.worker.execute_recovery(aid)
        self.assertEqual(self.f.mirror.writes,0);self.assertEqual(self.port.rows[aid].value['state'],'consumed')

    def test_delayed_actual_claim_read_cannot_advance_domain_after_expiry(self):
        aid=self.authorize();before=deepcopy(self.port.rows);read=self.port.read
        def slow(ids):
            result=read(ids)
            if len(ids)==3 and self.worker.task_id in ids and any(k.startswith('completed_') for k in ids):
                self.port.advance(601)
            return result
        self.port.read=slow
        with self.assertRaises(Conflict):self.worker.execute_recovery(aid)
        self.assertEqual(before,self.port.rows);self.assertEqual(self.f.mirror.writes,0)

    def test_delayed_actual_claim_auth_stops_at_commit_and_latches(self):
        aid=self.authorize();before=deepcopy(self.port.rows);commit=self.port.commit_guarded
        def delayed_auth(changes,**kwargs):
            if self.worker.task_id in changes and len(changes)==2:self.port.advance(601)
            return commit(changes,**kwargs)
        self.port.commit_guarded=delayed_auth
        with self.assertRaises(Conflict):self.worker.execute_recovery(aid)
        self.assertEqual(before,self.port.rows);self.assertEqual(self.f.mirror.writes,0)
        # A failed dispatch guard is latched, even if a later read would look current.
        self.port.advance(-601)
        with self.assertRaises(Conflict):self.worker.execute_recovery(aid)
        self.assertEqual(before,self.port.rows)

    def test_claim_cas_contention_rechecks_authority_before_retry_commit(self):
        aid=self.authorize();before=deepcopy(self.port.rows);commit=self.port.commit_guarded;attempts=[0]
        def contention(changes,**kwargs):
            if self.worker.task_id in changes and len(changes)==2:
                attempts[0]+=1
                if attempts[0]==1:
                    kwargs['before_write']();self.port.advance(601)
                    raise Conflict('synthetic_cas_contention')
            return commit(changes,**kwargs)
        self.port.commit_guarded=contention
        with self.assertRaises(Conflict):self.worker.execute_recovery(aid)
        self.assertEqual(before,self.port.rows);self.assertEqual(self.f.mirror.writes,0)
        self.assertEqual(attempts[0],2)

    def test_renew_auth_delay_preserves_consumed_barrier_and_existing_lease(self):
        aid=self.authorize();commit=self.port.commit_guarded;lease_before=[]
        def slow_renew(changes,**kwargs):
            if set(changes)=={self.worker.task_id}:
                lease_before.append(deepcopy(self.port.rows[self.worker.task_id]));self.port.advance(601)
            return commit(changes,**kwargs)
        self.port.commit_guarded=slow_renew
        with self.assertRaisesRegex(Conflict,'uncertain_native'):self.worker.execute_recovery(aid)
        self.assertEqual(self.port.rows[aid].value['state'],'consumed')
        self.assertEqual(len(lease_before),1);self.assertEqual(self.port.rows[self.worker.task_id],lease_before[0])
        self.assertEqual(self.f.mirror.writes,0)
