"""One explicitly prepared child attempt after a proven pre-dispatch stop.

This trusted-host-only contract never clears, replaces or promotes the original
``embeddedcoderepair`` fence. Its child key depends ONLY on that fence's ID and
version; changed identities, plans and evidence cannot purchase another attempt.
Ordinary ``embedded_code_repair.apply`` retains its existing refusal policy.

``read_evidence`` must freshly verify the complete original, exact original
attempt/run, pinned diagnostic and dispatcher ordering proving that no provider
request started, and the supported release of that SAME source claim. Exception
names, unchanged text alone and a caller's cached booleans are not that proof.
The callback returns the stable, detached evidence binding documented below;
the host retains the full authenticated evidence privately. Fresh respondent
identity and Form/Agenda/question proofs remain the separate ``guard`` and
``identity_guard`` responsibilities. No browser/HTTP or automatic retry route is
provided. Any child attempt, including an unconfirmed pre-dispatch child, closes
this admission permanently. It cannot become a parent for another admission.
"""
from copy import deepcopy
import signal
import threading

from . import completion, embedded_code_repair as repair
from .source_operations import SourceOperations
from school_workflow_hub.core.cloud_state import Claim, identifier, key


SCHEMA = 'embedded-code-predispatch-evidence/v1'
ADMISSION = 'embedded-code-one-use-predispatch/v1'
_FIELDS = {'schema', 'file_id', 'prior_gate_id', 'prior_gate_version', 'prior_plan_id',
           'prior_request_id', 'prior_claim', 'prior_run_version', 'prior_run_sha256',
           'diagnostic_sha256', 'reconciliation_sha256', 'release_sha256',
           'release_reason',
           'no_provider_request_started', 'complete_original_unchanged',
           'same_source_claim_released', 'prior_run_receipt_unchanged'}


class _Deadline(BaseException):
    pass


def _binding(read_evidence, manifest):
    repair._need(callable(read_evidence), 'A fresh independently verified pre-dispatch evidence reader is required.')
    value = deepcopy(read_evidence())
    repair._need(isinstance(value, dict) and set(value) == _FIELDS
                 and value['schema'] == SCHEMA and value['file_id'] == manifest['file_id']
                 and all(value[k] is True for k in ('no_provider_request_started', 'complete_original_unchanged',
                                                   'same_source_claim_released', 'prior_run_receipt_unchanged')),
                 'Exact complete no-dispatch/unchanged-source/released-claim evidence is required.')
    for field in ('prior_gate_version', 'prior_request_id', 'prior_run_version'):
        repair._need(isinstance(value[field], str) and bool(value[field]), 'An exact prior state version/request is required.')
    identifier(value['prior_request_id'])
    identifier(value['release_reason'])
    repair._need(all(isinstance(value[k], str) and repair.HASH.fullmatch(value[k])
                     for k in ('prior_plan_id', 'prior_run_sha256', 'diagnostic_sha256',
                               'reconciliation_sha256', 'release_sha256')),
                 'Immutable diagnostic, reconciliation, release and prior-run hashes are required.')
    try:
        claim = Claim(**value['prior_claim'])
        for field in ('task_id', 'run_id', 'owner', 'input_hash'):
            identifier(getattr(claim, field))
        valid = (claim.completed is False and type(claim.fence) is int and claim.fence > 0
                 and claim.run_id == key('run', {'task': claim.task_id, 'request': value['prior_request_id']}))
    except (TypeError, ValueError, KeyError):
        valid = False
    repair._need(valid, 'One exact non-completed prior source claim is required.')
    gate = key('embeddedcoderepair', [manifest['file_id']])
    repair._need(value['prior_gate_id'] == gate, 'Only the canonical original attempt may admit one child.')
    return value


def child_key(prior_gate_id, prior_gate_version):
    """Public for inspection only; neither a new identity nor a plan affects it."""
    return key('embeddedcodepredispatch', [prior_gate_id, prior_gate_version])


def _prior(port, manifest, evidence, *, child_must_be_absent):
    gate = evidence['prior_gate_id']; claim = Claim(**evidence['prior_claim'])
    child = child_key(gate, evidence['prior_gate_version'])
    state = port.read([gate, child, claim.task_id, claim.run_id])
    prior, run, task = (state.documents[k] for k in (gate, claim.run_id, claim.task_id))
    repair._need(prior is not None and prior.version == evidence['prior_gate_version']
                 and prior.value.get('status') == 'attempting'
                 and set(prior.value) == {'status', 'plan_id', 'manifest'}
                 and prior.value['plan_id'] == evidence['prior_plan_id']
                 and repair.digest(prior.value['manifest']) == evidence['prior_plan_id'],
                 'The exact original attempting fence must remain unchanged.')
    old = prior.value['manifest']
    repair._scope(old['proof'], manifest['file_id'])
    repair._need(set(old) == set(manifest) and old['proof']['question_set_status'] == 'complete'
                 and manifest['proof']['question_set_status'] == 'complete'
                 and {k: v for k, v in old.items() if k != 'proof'}
                     == {k: v for k, v in manifest.items() if k != 'proof'}
                 and all(old['proof'][k] == manifest['proof'][k] for k in
                         ('class', 'week', 'school_year', 'assignment_form_id', 'source_form_id',
                          'source_response_id', 'source_file_id', 'submitted_at')),
                 'Only the identical original revision, operations, reference and submission may be re-prepared.')
    repair._need(run is not None and run.version == evidence['prior_run_version']
                 and repair.digest(run.value) == evidence['prior_run_sha256']
                 and all(run.value.get(k) == getattr(claim, k) for k in ('task_id', 'owner', 'input_hash', 'fence'))
                 and run.value.get('receipt') is None and task is not None
                 and task.value.get('active_run') != claim.run_id
                 and type(task.value.get('fence')) is int and task.value['fence'] >= claim.fence,
                 'The exact prior run must remain unchanged and its source claim must already be released.')
    if task.value['fence'] == claim.fence:
        repair._need(task.value.get('owner') == claim.owner and task.value.get('active_run') is None
                     and task.value.get('status') == 'Needs you'
                     and task.value.get('next_action') == evidence['release_reason'],
                     'The original source task has no exact supported release readback.')
    if child_must_be_absent:
        repair._need(state.documents[child] is None, 'This original fence already consumed its one child admission; inspect, never retry.')
    return child, deepcopy(prior), deepcopy(run)


def prepare(token, prepared, port, *, guard, read_evidence):
    """Read-only explicit admission; reuses the full normal source preparation."""
    manifest = deepcopy(prepared['manifest'])
    repair._need(prepared.get('plan_id') == repair.digest(manifest)
                 and 'predispatch_admission' not in manifest, 'One ordinary freshly prepared repair is required.')
    evidence = _binding(read_evidence, manifest)
    child, prior, run = _prior(port, manifest, evidence, child_must_be_absent=True)
    fresh = repair.prepare(token, manifest['file_id'], manifest['proof'], manifest['revision'],
                           manifest['native_sha256'], manifest['metadata_sha256'], manifest['operations'],
                           guard=guard, panel_reference=manifest['panel_reference'],
                           registered_teacher=manifest.get('registered_teacher'))
    repair._need(fresh['manifest'] == manifest and fresh['requests'] == prepared['requests'], 'The reviewed ordinary repair plan changed.')
    repair._need(_binding(read_evidence, manifest) == evidence, 'Fresh pre-dispatch evidence changed during preparation.')
    repair._need(_prior(port, manifest, evidence, child_must_be_absent=True) == (child, prior, run), 'Prior state changed during admission preparation.')
    admission = {'schema': ADMISSION, 'prior_gate_id': evidence['prior_gate_id'],
                 'prior_gate_version': evidence['prior_gate_version'], 'prior_plan_id': evidence['prior_plan_id'],
                 'evidence_sha256': repair.digest(evidence)}
    fresh['manifest']['predispatch_admission'] = admission
    return {**fresh, 'status': 'prepared_one_use_predispatch', 'plan_id': repair.digest(fresh['manifest']),
            'child_key': child, 'predispatch_evidence': evidence, 'dry_run': True}


def apply(token, write_token, prepared, port, *, guard, read_evidence,
          source_operations=None, identity_guard=None, required_seconds=480, dry_run=True):
    """One native POST with normal preservation/readback and a separate child fence.

    The caller supplies a real fresh maintenance SourceOperations claim bound to
    this full admission plan. This function never acquires/releases/renews the
    old claim, alters the old gate, aliases ports or treats a success as grading.
    """
    manifest = deepcopy(prepared['manifest']); admission = manifest.pop('predispatch_admission', None)
    repair._need(prepared.get('plan_id') == repair.digest(prepared['manifest'])
                 and isinstance(admission, dict) and admission.get('schema') == ADMISSION,
                 'An exact separately prepared one-use admission is required.')
    ordinary = {**prepared, 'manifest': manifest, 'plan_id': repair.digest(manifest)}
    fresh = prepare(token, ordinary, port, guard=guard, read_evidence=read_evidence)
    repair._need(fresh['manifest'] == prepared['manifest'] and fresh['plan_id'] == prepared['plan_id']
                 and fresh['child_key'] == prepared.get('child_key') and fresh['requests'] == prepared['requests'],
                 'The exact child admission changed.')
    if dry_run:
        return fresh
    evidence = fresh['predispatch_evidence']; old_claim = Claim(**evidence['prior_claim'])
    ops = source_operations
    repair._need(isinstance(ops, SourceOperations) and ops.port is port and ops.claim is not None
                 and ops.claim.task_id == old_claim.task_id and ops.owner == old_claim.owner
                 and ops.input_hash == fresh['plan_id'] and ops._maintenance is not None
                 and ops._maintenance['operation_scope'] == 'source_repair'
                 and callable(identity_guard) and bool(write_token)
                 and threading.current_thread() is threading.main_thread() and threading.active_count() == 1
                 and type(required_seconds) is int and 30 <= required_seconds <= 870
                 and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
                 'An isolated exact maintenance source owner, fresh identity and bounded unused timer are required.')
    gate, prior, run = _prior(port, manifest, evidence, child_must_be_absent=True)
    attempt = {'status': 'attempting', 'plan_id': fresh['plan_id'], 'manifest': fresh['manifest']}
    successful = False; previous_handler = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise _Deadline()
    try:
        # One create-only CAS. Even a lost response closes this admission; there
        # is no second creation or attempt and the canonical gate is never written.
        port.commit({gate: (attempt, None)})
        claimed = port.read([gate]).documents[gate]
        repair._need(claimed is not None and claimed.value == attempt, 'The child attempting fence did not read back.')
        repair._need(repair._guard(guard, manifest['proof']), 'Fresh original/identity/question proof changed before dispatch.')
        repair._need(repair.google.drive_file(token, manifest['file_id'], repair.FIELDS) == fresh['before']['metadata'], 'The original changed before dispatch.')
        for role in ('reference', 'teacher'):
            if fresh[role] is not None:
                spec = manifest['panel_reference' if role == 'reference' else 'registered_teacher']
                repair._need(repair.google.drive_file(token, spec['file_id'], repair.FIELDS) == fresh[role]['metadata'], 'The independent source changed before dispatch.')
        repair._need(_binding(read_evidence, manifest) == evidence
                     and _prior(port, manifest, evidence, child_must_be_absent=False) == (gate, prior, run),
                     'The old attempt/run or independently verified no-dispatch evidence changed.')
        owned = port.read([gate]).documents[gate]
        repair._need(owned is not None and owned.version == claimed.version and owned.value == attempt, 'The child fence changed before dispatch.')
        identity_guard(required_seconds=required_seconds)
        ops.before_write(required_seconds=required_seconds)
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, required_seconds - 5)
        identity_guard(required_seconds=required_seconds)
        repair.google.slides_batch_update(write_token, manifest['file_id'], fresh['requests'], manifest['revision'])
        identity_guard(); after = repair.capture(token, manifest['file_id']); identity_guard()
        repair._preserved(fresh['before'], after, fresh['edits'])
        reference = repair._reference(token, manifest['proof'], manifest['panel_reference'], manifest['file_id'])
        repair._teacher(token, manifest['proof'], manifest.get('registered_teacher'), manifest['file_id'])
        repair._bindings(after, manifest['operations'], reference)
        before_ready = completion.student_readiness(fresh['before']['parsed'], require_complete_codes=True)
        ready = completion.student_readiness(after['parsed'], require_complete_codes=True)
        repair._need(ready['status'] == 'READY' and all(ready[k] == before_ready[k] for k in ('inventory_cycle', 'inventory_text', 'declared_counts')),
                     'The saved complete original inventory/readiness changed.')
        repair._need(repair._guard(guard, manifest['proof']), 'Original proof changed during saved verification.')
        repair._need(_prior(port, manifest, evidence, child_must_be_absent=False) == (gate, prior, run), 'The preserved prior fence/run changed during readback.')
        receipt = {'status': 'verified', 'plan_id': fresh['plan_id'], 'manifest': fresh['manifest'],
                   'prior_attempt_preserved': True, 'child_key': gate,
                   'before_native_sha256': fresh['before']['native_sha256'], 'after_native_sha256': after['native_sha256'],
                   'after_metadata_sha256': after['metadata_sha256'], 'revision': after['native']['revisionId'],
                   'repaired_codes': len(manifest['operations']), 'full_census_complete': False, 'grading_authorized': False}
        try:
            port.commit({gate: (receipt, claimed.version)})
        except Exception:
            saved = port.read([gate]).documents[gate]
            if saved is None or saved.value != receipt:
                raise
        saved = port.read([gate]).documents[gate]
        repair._need(saved is not None and saved.value == receipt, 'The child completion receipt did not read back.')
        successful = True
        return receipt
    except _Deadline:
        raise repair.Uncertain('The child write/readback deadline expired; preserve both fences and inspect. No further admission is available.') from None
    except Exception as error:
        raise repair.Uncertain('The child repair is unconfirmed; preserve both fences and inspect, never retry. Cause: %s' % error) from error
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if not successful:
            ops.stopped = True
