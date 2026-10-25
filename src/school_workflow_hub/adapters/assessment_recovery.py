"""Teacher-attested closure of one verified pre-write assessment failure.

Only private CAS state changes here. This operation neither retries Save nor
claims, renews, adopts or finishes the old run. The historical HTTP failure is
an explicit authenticated teacher attestation, not invented server evidence.
"""
from copy import deepcopy
import re

from . import assessment_save as saving, assessment_review as review, assessment_target
from ..core.cloud_state import Conflict, StoreUnavailable, bounded_json, instant, key
from ..core.runs import content_hash
from ..workflows import assessment_records

FAILURE_SCHEMA = 'hub-assessment-prewrite-failure/v1'
RECOVERY_SCHEMA = 'hub-assessment-prewrite-recovery/v1'
FAILURE_FIELDS = {'schema', 'run_id', 'fence', 'http_status', 'error_code',
                  'evidence_ref', 'prewrite_failure_attested'}
RECOVERY_FIELDS = {'schema', 'record_id', 'task_id', 'run_id', 'fence', 'input_hash',
    'assessment_record_id', 'proof_sha256', 'actor', 'authorization_basis', 'attestation_classification',
    'failure_receipt', 'verified_at', 'native_absence_sha256', 'original_record_sha256',
    'original_run_sha256', 'schema_proof', 'source', 'grade_writes'}


def _payload(payload):
    if (not isinstance(payload, dict)
            or set(payload) != {'request_id', 'packet', 'review_basis', 'failure_receipt'}
            or not isinstance(payload['packet'], dict) or not isinstance(payload['review_basis'], dict)):
        raise ValueError('invalid_assessment_recovery')
    saving._request_id(payload['request_id'])
    proof = payload['failure_receipt']
    if (not isinstance(proof, dict) or set(proof) != FAILURE_FIELDS
            or proof['schema'] != FAILURE_SCHEMA or type(proof['http_status']) is not int
            or proof['http_status'] not in (400, 409) or proof['error_code'] != 'assessment_column_missing'
            or proof['prewrite_failure_attested'] is not True
            or type(proof['fence']) is not int or proof['fence'] < 1
            or not isinstance(proof['run_id'], str) or not re.fullmatch(r'run_[a-f0-9]{64}', proof['run_id'])
            or not isinstance(proof['evidence_ref'], str) or not 1 <= len(proof['evidence_ref']) <= 400
            or not proof['evidence_ref'].strip() or any(ord(c) < 32 or ord(c) == 127 for c in proof['evidence_ref'])):
        raise ValueError('verified_prewrite_failure_required')
    review.basis_fields(payload['review_basis'])
    return deepcopy(payload)


def _ids(payload):
    task_id = key('task', review.task_scope(payload['packet']))
    run_id = key('run', {'task': task_id, 'request': payload['request_id']})
    if payload['failure_receipt']['run_id'] != run_id:
        raise Conflict('assessment_recovery_run_mismatch')
    digest = content_hash(payload['packet'])
    return task_id, run_id, key('completed', {'task': task_id, 'input_hash': digest}), key(
        'assessmentprewriterecovery', {'task': task_id, 'run': run_id})


def _reply(receipt, already=False):
    return {'status': 'already_reconciled' if already else 'reconciled',
            'readback_verified': True, 'grade_writes': 0, 'receipt': deepcopy(receipt)}


def _closed(doc, run, proof_hash, recovery_id, run_id):
    if doc is None:
        return None
    receipt = doc.value
    original_run = deepcopy(run.value) if run is not None else {}
    original_run.pop('reconciliation', None)
    if (set(receipt) != RECOVERY_FIELDS or receipt.get('schema') != RECOVERY_SCHEMA or receipt.get('record_id') != recovery_id
            or receipt.get('run_id') != run_id or receipt.get('original_run_sha256') != content_hash(original_run)
            or receipt.get('proof_sha256') != proof_hash or type(receipt.get('grade_writes')) is not int or receipt['grade_writes'] != 0
            or run is None or run.value.get('reconciliation') != {
                'record_id': recovery_id, 'status': 'verified_no_write'}
            or run.value.get('receipt') is not None or run.value.get('task_id') != receipt.get('task_id')
            or run.value.get('fence') != receipt.get('fence') or run.value.get('input_hash') != receipt.get('input_hash')):
        raise Conflict('assessment_recovery_proof_conflict')
    return receipt


def _pending(snapshot, ids, payload):
    task_id, run_id, completed_id, _ = ids
    task_doc, run_doc = snapshot.documents[task_id], snapshot.documents[run_id]
    if task_doc is None or run_doc is None or snapshot.documents[completed_id] is not None:
        raise Conflict('assessment_recovery_run_unverified')
    task, run = task_doc.value, run_doc.value
    fence, digest = payload['failure_receipt']['fence'], content_hash(payload['packet'])
    if (task.get('scope') != review.task_scope(payload['packet']) or task.get('active_run') != run_id
            or task.get('status') != 'Working' or run.get('task_id') != task_id
            or not isinstance(task.get('owner'), str) or not task['owner']
            or task.get('owner') != run.get('owner')
            or type(task.get('fence')) is not int or type(run.get('fence')) is not int
            or task['fence'] != fence or run['fence'] != fence
            or run.get('input_hash') != digest or task.get('input_hash', digest) != digest
            or 'receipt' not in run or run['receipt'] is not None or run.get('reconciliation') is not None):
        raise Conflict('assessment_recovery_run_changed')
    try:
        now = instant(snapshot.read_time)
        if now < instant(task['expires_at']) or now < instant(run['expires_at']):
            raise Conflict('assessment_recovery_run_not_expired')
    except (KeyError, TypeError, ValueError):
        raise Conflict('assessment_recovery_expiry_unverified') from None
    return task_doc, run_doc


def _native(packet, basis, workbook, capture):
    assessment_records.validate_packet(packet, capture['text'])
    source = review.source_binding(capture)
    if any(packet['source'].get(name) != source[name] for name in ('file_id', 'sha256', 'version', 'modified_at')):
        raise Conflict('submission_changed_reassess')
    original = review.validate_successor(packet, basis, workbook, capture)
    record_id = assessment_records.record_id(packet)
    if any(row.get('record_id') == record_id for title in ('Submissions', 'Answers')
           for _, row in workbook[title]['rows']):
        raise Conflict('assessment_recovery_native_record_present')
    rows = assessment_records.build_rows(packet, assessment_records.completion(packet), capture,
        now=saving._now(), prior_score_exact=original['submission'].get('content_score_exact', ''),
        prior_hold_reason=original['submission'].get('hold_reason', ''))
    schema = saving.schema_compatibility(workbook, rows)
    if schema.get('outcome') != 'ready':
        raise Conflict('assessment_recovery_schema_not_ready')
    assessment_records.aligned_cells(workbook['Submissions']['headers'], rows['submission'], numeric=saving.NUMERIC)
    for answer in rows['answers']:
        assessment_records.aligned_cells(workbook['Answers']['headers'], answer)
    return original, record_id, schema


def reconcile(assertion, payload, config):
    """Close one exact expired no-write attempt; never dispatch another Save."""
    payload = _payload(payload)
    # The existing reader authenticates before private state/source access.
    from hub_v2 import google
    try:
        actor = google.verify_teacher(assertion, config)
    except google.SignInRequired:
        raise saving.ConnectionError('sign_in_required') from None
    saving.read_records(assertion, config)
    assessment_target.read_target(assertion, payload['packet'], config)
    ids = _ids(payload)
    task_id, run_id, completed_id, recovery_id = ids
    port = saving.configured_store(config)
    proof_hash = content_hash({'payload': payload, 'actor': actor})
    first = port.read(list(ids))
    closed = _closed(first.documents[recovery_id], first.documents[run_id], proof_hash, recovery_id, run_id)
    if closed is not None:
        return _reply(closed, already=True)
    task_doc, run_doc = _pending(first, ids, payload)

    packet, basis = payload['packet'], payload['review_basis']
    source_before = saving.capture(assertion, packet['source']['file_id'], config)
    workbook_before = review.read_workbook(config)
    original, record_id, schema = _native(packet, basis, workbook_before, source_before)
    # Both complete native grids are read, including Answers. Partial append or
    # concurrent baseline/header/source changes remain uncertain, never cleared.
    source_after = saving.capture(assertion, packet['source']['file_id'], config)
    final = port.read(list(ids))
    closed = _closed(final.documents[recovery_id], final.documents[run_id], proof_hash, recovery_id, run_id)
    if closed is not None:
        return _reply(closed, already=True)
    final_task, final_run = _pending(final, ids, payload)
    if final_task != task_doc or final_run != run_doc:
        raise Conflict('assessment_recovery_run_changed')
    workbook_after = review.read_workbook(config)
    source_final = saving.capture(assertion, packet['source']['file_id'], config)
    final_original, final_record, final_schema = _native(packet, basis, workbook_after, source_final)
    if (workbook_after != workbook_before or review.source_binding(source_after) != review.source_binding(source_before)
            or review.source_binding(source_final) != review.source_binding(source_after)
            or original != final_original or record_id != final_record or schema != final_schema):
        raise Conflict('assessment_recovery_native_changed')
    receipt = {'schema': RECOVERY_SCHEMA, 'record_id': recovery_id, 'task_id': task_id, 'run_id': run_id,
        'fence': task_doc.value['fence'], 'input_hash': content_hash(packet), 'assessment_record_id': record_id,
        'proof_sha256': proof_hash, 'actor': actor, 'authorization_basis': 'authenticated_teacher_attestation',
        'attestation_classification': 'operator_attested_prewrite_failure',
        'failure_receipt': deepcopy(payload['failure_receipt']), 'verified_at': final.read_time,
        'native_absence_sha256': content_hash({'workbook': workbook_after, 'absent_record_id': record_id}),
        'original_record_sha256': content_hash(original), 'original_run_sha256': content_hash(run_doc.value), 'schema_proof': schema,
        'source': review.source_binding(source_final), 'grade_writes': 0}
    bounded_json(receipt)
    task = {**deepcopy(task_doc.value), 'active_run': None, 'status': 'Needs you',
            'next_action': 'verified_prewrite_failure_recovered'}
    run = {**deepcopy(run_doc.value), 'reconciliation': {'record_id': recovery_id, 'status': 'verified_no_write'}}
    changes = {task_id: (task, task_doc.version), run_id: (run, run_doc.version), recovery_id: (receipt, None)}
    try:
        port.commit(changes)
    except (Conflict, StoreUnavailable):
        # Exactly one mutation attempt. Lost replies are read back, never
        # replayed, and a concurrent newer task cannot be mistaken for closure.
        actual = port.read([task_id, run_id, recovery_id]).documents
        if any(actual[name] is None or actual[name].value != value for name, (value, _) in changes.items()):
            raise
    actual = port.read([task_id, run_id, recovery_id]).documents
    if any(actual[name] is None or actual[name].value != value for name, (value, _) in changes.items()):
        raise StoreUnavailable('assessment_recovery_readback_unconfirmed')
    return _reply(receipt)
