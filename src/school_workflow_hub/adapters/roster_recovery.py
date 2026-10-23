"""Explicit one-use admission for a rejected bootstrap, never automatic retry.

Original intent/run/fence evidence is retained. Only the existing coordination
pointer advances, atomically with consumption and a new per-request intent.
"""
from copy import deepcopy
from datetime import timedelta
import math
from time import monotonic

from . import roster_bootstrap as rb, roster_rejection as rejection, roster_report as report
from .roster_sync import ROSTER_SYNC_SCOPE
from ..core.cloud_state import Claim, CloudState, Conflict, StoreUnavailable, bounded_json, identifier, instant, key
from ..core.runs import content_hash

PROOF_SECONDS = 300
STATE_SECONDS = 60
FINAL_SECONDS = 150


def fail(code):
    raise Conflict("roster_bootstrap_recovery_" + code)


def reference(rid, doc):
    if doc is None: fail("required_record_unavailable")
    return {"record_id": rid, "version": doc.version, "sha256": content_hash(doc.value)}


def same(doc, ref):
    return doc is not None and doc.version == ref['version'] and content_hash(doc.value) == ref['sha256']


class RecoveryBootstrap(rb.BootstrapWorker):
    """Trusted-host only. No HTTP writer, schedule, permission or helper change."""
    FINISH_LEASE_SECONDS = 900

    def __init__(self, *args, read_authorization, source_claim, **kwargs):
        super().__init__(*args, **kwargs)
        if not callable(read_authorization) or not isinstance(source_claim, Claim):
            fail('trusted_authorization_required')
        self.read_authorization, self.source_claim = read_authorization, source_claim
        self.original_intent_id = self.intent_key
        self.admission = self.admission_id = self.consumed = None

    def _authority(self, now=None):
        value = deepcopy(self.read_authorization())
        fields = {'schema','authorization_ref','revision','owner','request_id','proposal_sha256',
                  'prior_intent_sha256','rejection_reference','rejection_sha256','max_attempts',
                  'status','starts_at','expires_at'}
        if (not isinstance(value,dict) or set(value)!=fields or value['schema']!='roster-bootstrap-recovery-authorization/v1'
                or value['status']!='authorized' or type(value['max_attempts']) is not int or value['max_attempts']!=1):
            fail('explicit_one_use_authority_required')
        for name in ('authorization_ref','revision','owner','request_id','rejection_reference'):
            identifier(value[name])
        for name in ('proposal_sha256','prior_intent_sha256','rejection_sha256'):
            if not rejection.digest(value[name]): fail('authority_digest_invalid')
        start,end=instant(value['starts_at']),instant(value['expires_at'])
        if not timedelta(0)<end-start<=timedelta(days=1) or (now is not None and not start<=now<end):
            fail('authority_expired_or_future')
        return value

    @staticmethod
    def _authority_window(value, now, required_seconds):
        if (type(required_seconds) is not int or required_seconds < 0
                or not instant(value['starts_at']) <= now
                or now + timedelta(seconds=required_seconds) > instant(value['expires_at'])):
            fail('authority_budget_unavailable')

    def _authority_control_guard(self, expected):
        """Bound lease-control writes separately before any new domain claim."""
        self.before_write(required_seconds=STATE_SECONDS+FINAL_SECONDS)
        started=monotonic()
        current=self._authority()
        observed=self.port.read([self.task_id])
        elapsed=math.ceil(max(0,monotonic()-started))
        if elapsed>FINAL_SECONDS or current!=expected:fail('authority_changed')
        self._authority_window(current,instant(observed.read_time)+timedelta(seconds=elapsed),STATE_SECONDS)

    def prepare_recovery(self, proposal):
        """Read-only preview; a fresh complete pair and exact rejection are required."""
        authority=self._authority()
        state=self.port.read([self.original_intent_id,self.sync_intent_key,self.task_id,authority['rejection_reference']])
        now=instant(state.read_time);self._authority(now)
        old=state.documents[self.original_intent_id];marker=state.documents[self.sync_intent_key]
        task=state.documents[self.task_id];rejected=state.documents[authority['rejection_reference']]
        if old is None or rejected is None or marker is None or task is None:fail('original_history_unavailable')
        if (content_hash(old.value)!=authority['prior_intent_sha256']
                or content_hash(rejected.value)!=authority['rejection_sha256']
                or proposal.get('sha256')!=authority['proposal_sha256']
                or content_hash(proposal.get('plan'))!=proposal['sha256']):fail('authority_scope_changed')
        if task.value.get('active_run') is not None and now<instant(task.value['expires_at']):
            fail('prior_domain_claim_still_active')
        if authority['request_id']==old.value['request_id'] or authority['owner']!=self.source_claim.owner:
            fail('new_owned_request_required')
        proof_reader=rejection.RejectedBootstrap(self.port,self.mirror,read_config=self.read_config,
            read_failure=lambda:deepcopy(rejected.value['native_evidence']),source_claim=self.source_claim,
            before_write=self.before_write)
        current,_=proof_reader._proof()
        fixed=set(current['payload'])-{'current_capability','dependencies','guard_claim'}
        if (set(rejected.value)!=set(current['payload'])
                or any(rejected.value[k]!=current['payload'][k] for k in fixed)
                or current['record_id']!=authority['rejection_reference']):fail('rejection_basis_changed')
        prior_dependencies=rejected.value['dependencies']
        history=self.port.read(list(prior_dependencies))
        if any(not same(history.documents[k],{'record_id':k,**ref})for k,ref in prior_dependencies.items()):
            fail('rejection_history_changed')
        rb.require_editable(current['payload']['current_capability'])
        plan=proposal['plan'];old_plan=old.value['plan']
        if any(plan[k]!=old_plan[k]for k in ('original','targets','titles','config_basis')):
            fail('recovery_destination_scope_changed')
        if (instant(plan['source_before']['report_proof']['captured_at'])
                <=instant(old_plan['source_after']['report_proof']['observed_at'])):
            fail('new_native_pair_required')
        rebuilt=rb.prepare_plan(plan['source_before'],plan['source_after'],config=rb.basis(self.read_config()),
            original=self.mirror.read(),targets=self.targets,titles=self.titles,now=instant(self.port.read([self.task_id]).read_time))
        if rebuilt!=plan or self._authority()!=authority:fail('fresh_preview_changed')
        final=self.port.read(list(state.documents))
        if final.documents!=state.documents:fail('preview_state_changed')
        aid=key('rosterbootstrapadmission',{'prior':reference(self.original_intent_id,old),
              'request':authority['request_id'],'proposal':proposal['sha256']})
        iid=key('rosterbootstraprecoveryintent',{'admission':aid})
        pid=key('rosterbootstrapplan',{'admission':aid,'sha256':proposal['sha256']})
        admitted={'schema':'roster-bootstrap-recovery-admission/v1','state':'authorized',
            'authority':authority,'plan_reference':pid,'plan_sha256':proposal['sha256'],
            'new_intent_reference':iid,'prior_intent':reference(self.original_intent_id,old),
            'rejection':reference(authority['rejection_reference'],rejected),
            'prior_pointer':{'record_id':self.sync_intent_key,'version':marker.version,'value':deepcopy(marker.value)},
            'prior_domain_task':{'record_id':self.task_id,'version':task.version,'value':deepcopy(task.value)},
            'preserved_dependencies':deepcopy(prior_dependencies),'max_native_attempts':1}
        bounded_json(admitted);bounded_json(proposal)
        # Prove the eventual native intent, immutable receipt and verified
        # result envelopes fit before the first authorization state write.
        anticipated=Claim(self.task_id,key('run',{'task':self.task_id,'request':authority['request_id']}),
                          authority['owner'],task.value['fence']+1,proposal['sha256'],'x'*64)
        context={'admission_reference':aid,'authorization_version':'x'*64,'intent_reference':iid,
                 'prior_intent':admitted['prior_intent'],'rejection':admitted['rejection']}
        rb._check_state_size({'state':'pending','claim':anticipated.__dict__,'plan':plan,
                              'sha256':proposal['sha256'],'request_id':authority['request_id'],'recovery':context})
        return {'admission_id':aid,'admission':admitted,'proposal':deepcopy(proposal)}

    def authorize_recovery(self, preview):
        """Persist the explicit envelope; authorization alone never dispatches."""
        if self.prepare_recovery(preview['proposal'])!=preview:fail('prepared_authority_changed')
        aid=preview['admission_id'];pid=preview['admission']['plan_reference']
        absent=self.port.read([aid,pid])
        if any(v is not None for v in absent.documents.values()):fail('admission_already_exists')
        changes={aid:(preview['admission'],None),pid:(preview['proposal'],None)}
        def guard():
            self.before_write(required_seconds=PROOF_SECONDS+STATE_SECONDS)
            started=monotonic()
            if self.prepare_recovery(preview['proposal'])!=preview:fail('final_authority_changed')
            authority=self._authority()
            observed=self.port.read([self.task_id])
            elapsed=math.ceil(max(0,monotonic()-started))
            if elapsed>PROOF_SECONDS:fail('final_proof_timed_out')
            if authority!=preview['admission']['authority']:fail('final_authority_changed')
            self._authority_window(authority,instant(observed.read_time)+timedelta(seconds=elapsed),STATE_SECONDS)
        # Unknown create response is not an instruction to repeat it or execute.
        self.port.commit_guarded(changes,before_write=guard)
        actual=self.port.read([aid,pid])
        if any(actual.documents[k] is None or actual.documents[k].value!=v[0] for k,v in changes.items()):
            raise StoreUnavailable('roster_bootstrap_recovery_authorization_unconfirmed')
        return {'status':'authorized_not_dispatched','admission_reference':aid,'native_writes':0}

    def _load(self, aid):
        doc=self.port.read([aid]).documents[aid]
        if doc is None or doc.value.get('state')!='authorized':fail('admission_unavailable_or_consumed')
        plan=self.port.read([doc.value['plan_reference']]).documents[doc.value['plan_reference']]
        if plan is None or plan.value.get('sha256')!=doc.value['plan_sha256']:fail('authorized_plan_unavailable')
        preview=self.prepare_recovery(plan.value)
        if preview['admission_id']!=aid or preview['admission']!=doc.value:fail('authorized_scope_changed')
        return doc,plan.value

    def execute_recovery(self, aid):
        """A successful consumption response permits one dispatch in this call only."""
        admission_doc,proposal=self._load(aid)
        self.admission_id,self.admission=aid,deepcopy(admission_doc.value)
        self.intent_key=self.admission['new_intent_reference']
        authority=self.admission['authority'];plan=proposal['plan']
        self._authority_control_guard(authority)
        self.claim=self.cloud.claim(self.task_id,owner=authority['owner'],request_id=authority['request_id'],
                                    input_hash=proposal['sha256'],ttl_seconds=900)
        if self.claim.completed:fail('prior_recovery_readback_required')
        context={'admission_reference':aid,'authorization_version':admission_doc.version,
                 'intent_reference':self.intent_key,'prior_intent':self.admission['prior_intent'],
                 'rejection':self.admission['rejection']}
        intent={'state':'pending','claim':self.claim.__dict__,'plan':plan,'sha256':proposal['sha256'],
                'request_id':authority['request_id'],'recovery':context}
        rb._check_state_size(intent);bounded_json(intent)
        marker={'state':'pending','kind':'bootstrap','request_id':intent['request_id'],
                'bootstrap_intent':self.intent_key,'claim':intent['claim']}
        consumed={**self.admission,'state':'consumed','consumed_claim':self.claim.__dict__,
                  'authorized_version':admission_doc.version,'consumed_intent':self.intent_key}
        ids=[aid,self.intent_key,self.sync_intent_key,self.task_id]
        state=self.port.read(ids);task=state.documents[self.task_id]
        if (state.documents[aid]!=admission_doc or state.documents[self.intent_key] is not None
                or state.documents[self.sync_intent_key].version!=self.admission['prior_pointer']['version']
                or state.documents[self.sync_intent_key].value!=self.admission['prior_pointer']['value']):
            fail('consumption_basis_changed')
        CloudState._owns(self.claim,task.value,state.read_time)
        def guard():
            self.before_write(required_seconds=PROOF_SECONDS+STATE_SECONDS)
            started=monotonic()
            self._unconsumed_basis(proposal,admission_doc)
            now=self.port.read(ids)
            if now.documents!=state.documents:fail('consumption_state_changed')
            elapsed=math.ceil(max(0,monotonic()-started))
            CloudState._owns(self.claim,now.documents[self.task_id].value,
                             (instant(now.read_time)+timedelta(seconds=elapsed)).isoformat())
            self._authority_window(self.admission['authority'],instant(now.read_time)+timedelta(seconds=elapsed),STATE_SECONDS)
            if (elapsed>PROOF_SECONDS or (instant(now.documents[self.task_id].value['expires_at'])
                    -instant(now.read_time)).total_seconds()-elapsed<STATE_SECONDS):fail('consumption_budget_unavailable')
        changes={aid:(consumed,admission_doc.version),self.intent_key:(intent,None),
                 self.sync_intent_key:(marker,self.admission['prior_pointer']['version']),
                 self.task_id:(task.value,task.version)}
        try:self.port.commit_guarded(changes,before_write=guard)
        except Exception:
            # A committed-but-lost response is consumed, not safe to dispatch.
            fail('consumption_unconfirmed_readback_only')
        observed=self.port.read(ids)
        if any(observed.documents[k] is None or observed.documents[k].value!=v[0] for k,v in changes.items()):
            fail('consumption_unconfirmed_readback_only')
        self.consumed=consumed
        try:
            actual=self.mirror.apply(plan,self._native_guard)
            rb.verify_destination(plan,actual)
        except Exception:fail('uncertain_native_write_readback_only')
        return self._finish(intent,actual)

    def _unconsumed_basis(self, proposal, admission_doc):
        """After new claim, preserve old history while checking current native basis."""
        a=self.admission;plan=proposal['plan']
        if (self._authority()!=a['authority'] or rb.basis(self.read_config())!=plan['config_basis']
                or self.mirror.read()!=plan['original']):fail('recovery_native_basis_changed')
        rb.require_editable(self.mirror.write_capability())
        refs=[a['prior_intent'],a['rejection']]
        state=self.port.read([r['record_id']for r in refs]+[self.admission_id])
        if any(not same(state.documents[r['record_id']],r)for r in refs)or state.documents[self.admission_id]!=admission_doc:
            fail('recovery_history_changed')
        self._authority(instant(state.read_time))
        for source in ('source_before','source_after'):
            report.validate_proof(plan[source]['report_proof'],instant(state.read_time)+timedelta(seconds=STATE_SECONDS))

    def _native_guard(self, *, required_seconds):
        self._authority_control_guard(self.admission['authority'])
        self.claim=self.cloud.renew(self.claim,ttl_seconds=900)
        self._owned(required_seconds)

    def _owned(self, seconds, *, finished=False):
        """Final exact admission/authority/domain proof after slow shared activity."""
        self.before_write(required_seconds=seconds+FINAL_SECONDS)
        started=monotonic();a=self.admission
        authority=self._authority()
        config=rb.basis(self.read_config());rb.require_editable(self.mirror.write_capability())
        refs=[a['prior_intent'],a['rejection']]
        preserved={k:v for k,v in a['preserved_dependencies'].items()if k not in (self.task_id,self.sync_intent_key)}
        ids=list(dict.fromkeys([self.task_id,self.intent_key,self.sync_intent_key,self.admission_id]
                              +[r['record_id']for r in refs]+list(preserved)))
        state=self.port.read(ids);elapsed=math.ceil(max(0,monotonic()-started))
        now=instant(state.read_time)+timedelta(seconds=elapsed)
        if (elapsed>FINAL_SECONDS or state.documents[self.admission_id] is None
                or state.documents[self.admission_id].value!=self.consumed or authority!=a['authority']
                or any(not same(state.documents[r['record_id']],r)for r in refs)
                or any(not same(state.documents[k],v)for k,v in preserved.items())):
            fail('final_admission_changed')
        self._authority_window(authority,now,seconds)
        intent=state.documents[self.intent_key]
        if (intent is None or intent.value.get('state')!='pending' or intent.value.get('sha256')!=a['plan_sha256']
                or intent.value.get('claim')!=self.consumed['consumed_claim']
                or intent.value.get('recovery',{}).get('admission_reference')!=self.admission_id
                or config!=intent.value['plan']['config_basis']):fail('final_intent_changed')
        expected_marker={'state':'pending','kind':'bootstrap','request_id':intent.value['request_id'],
                         'bootstrap_intent':self.intent_key,'claim':intent.value['claim']}
        if state.documents[self.sync_intent_key] is None or state.documents[self.sync_intent_key].value!=expected_marker:
            fail('final_pointer_changed')
        task=state.documents[self.task_id]
        if task is None:fail('domain_unavailable')
        if finished:
            if (task.value.get('active_run') is not None or task.value.get('owner')!=self.claim.owner
                    or task.value.get('fence')!=self.claim.fence
                    or task.value.get('last_result',{}).get('run_id')!=self.claim.run_id):fail('completed_owner_changed')
        else:CloudState._owns(self.claim,task.value,now.isoformat())
        if (instant(task.value['expires_at'])-now).total_seconds()<seconds:fail('domain_budget_unavailable')
        for source in ('source_before','source_after'):
            report.validate_proof(intent.value['plan'][source]['report_proof'],now+timedelta(seconds=seconds))
        return task

    def reconcile_recovery(self, aid):
        """Only inspect/finalize the exact already-applied new destination; no resend."""
        admission=self.port.read([aid]).documents[aid]
        if admission is None or admission.value.get('state')!='consumed':fail('consumed_admission_required')
        self.admission_id=aid;self.consumed=deepcopy(admission.value)
        self.admission={k:v for k,v in self.consumed.items()if k not in ('consumed_claim','authorized_version','consumed_intent')}
        self.admission['state']='authorized';self.intent_key=self.consumed['consumed_intent']
        doc=self.port.read([self.intent_key]).documents[self.intent_key]
        if doc is None or doc.value.get('recovery',{}).get('admission_reference')!=aid:fail('dynamic_intent_unavailable')
        self.claim=Claim(**doc.value['claim'])
        actual=self.mirror.read();rb.verify_destination(doc.value['plan'],actual)
        if (self.intent_key!=self.admission['new_intent_reference']
                or self.claim.__dict__!=self.consumed['consumed_claim']
                or doc.value['sha256']!=self.admission['plan_sha256']):fail('consumed_identity_changed')
        if doc.value['state']=='verified':
            result=doc.value['result'];receipt=result['receipt']
            expected_marker={'state':'verified','kind':'bootstrap','request_id':doc.value['request_id'],
                'bootstrap_intent':self.intent_key,'claim':doc.value['claim'],'receipt':receipt}
            record_id=key('record',{'task':self.task_id,'run':self.claim.run_id,'name':'roster_bootstrap'})
            state=self.port.read([self.sync_intent_key,record_id,self.claim.run_id])
            stored=state.documents[record_id];run=state.documents[self.claim.run_id]
            if (result!=rb._result(doc.value,receipt) or receipt.get('record_ids')!={'roster_bootstrap':record_id}
                    or state.documents[self.sync_intent_key] is None or state.documents[self.sync_intent_key].value!=expected_marker
                    or stored is None or stored.value.get('run_id')!=self.claim.run_id
                    or stored.value.get('payload')!=rb._record(doc.value['plan'],content_hash(actual),doc.value['recovery'])
                    or run is None or run.value.get('receipt')!=receipt):fail('verified_recovery_readback_changed')
            return deepcopy(result)
        return self._finish(doc.value,actual)
