"""One legacy pre-write UI failure: current proof plus historical attestation.

The exact attempted packet survives; its request UUID, original review basis,
original baseline fingerprint and raw HTTP status do not. None are invented.
GET is read-only. POST closes only the exact expired no-write attempt and never
claims, retries Save, changes a workbook or invokes a model.
"""
from copy import deepcopy
import re

from . import assessment_recovery as prior, assessment_review as review
from . import assessment_save as saving, assessment_target
from ..core.cloud_state import Conflict, StoreUnavailable, bounded_json, instant, key
from ..core.runs import content_hash
from ..workflows import assessment_records as records

SCHEMA = 'hub-assessment-legacy-recovery/v1'
CHALLENGE_SCHEMA = 'hub-assessment-legacy-challenge/v1'
EVIDENCE_SCHEMA = 'hub-assessment-legacy-ui-evidence/v1'
RECEIPT_SCHEMA = 'hub-assessment-legacy-recovery-receipt/v1'
EVIDENCE_FIELDS = {'schema', 'observation', 'evidence_ref', 'attempt_started_at',
    'first_absence_at', 'baseline_unchanged_attested', 'no_native_write_attested'}
CHALLENGE_FIELDS = {'schema', 'record_id', 'task_id', 'run_id', 'fence', 'input_hash',
    'task_version', 'run_version', 'task_sha256', 'run_sha256',
    'current_baseline_sha256', 'current_source', 'workbook_sha256', 'headers_sha256'}
RECEIPT_FIELDS = {'schema', 'record_id', 'task_id', 'run_id', 'fence', 'input_hash',
    'assessment_record_id', 'baseline_record_id', 'actor', 'request_sha256', 'verified_at',
    'original_run_sha256', 'task_before_sha256', 'previous_recovery',
    'original_review_basis_available', 'original_baseline_sha256', 'current_baseline_sha256',
    'current_source', 'native_absence_sha256', 'schema_proof', 'historical_evidence',
    'historical_evidence_classification', 'grade_writes'}


def _evidence(evidence):
    if (not isinstance(evidence, dict) or set(evidence) != EVIDENCE_FIELDS
            or evidence['schema'] != EVIDENCE_SCHEMA or evidence['observation'] != 'missing_report_columns_ui'
            or evidence['baseline_unchanged_attested'] is not True or evidence['no_native_write_attested'] is not True
            or not isinstance(evidence['evidence_ref'], str) or not 1 <= len(evidence['evidence_ref']) <= 400
            or not evidence['evidence_ref'].strip() or any(ord(c) < 32 or ord(c) == 127 for c in evidence['evidence_ref'])):
        raise ValueError('assessment_legacy_ui_attestation_required')
    try:
        instant(evidence['attempt_started_at']); instant(evidence['first_absence_at'])
    except (ValueError, TypeError):
        raise ValueError('assessment_legacy_ui_attestation_required') from None


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _actor(assertion, config):
    from hub_v2 import google
    try:
        actor = google.verify_teacher(assertion, config)
    except google.SignInRequired:
        raise saving.ConnectionError('sign_in_required') from None
    saving.read_records(assertion, config)
    return actor


def _record_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value):
        raise ValueError('invalid_assessment_legacy_recovery')
    return value


def _context(assertion, record_id, config):
    book = review.read_workbook(config)
    base = review.baseline(book, record_id, active=False)
    row = base['submission']
    assignments = [a for _, a in book['Assignments']['rows']
                   if a.get('assignment_key') == row.get('assignment_key')]
    if len(assignments) != 1 or assignments[0].get('class_key') not in {'biology', 'forensics', 'anatomy'}:
        raise Conflict('assessment_legacy_target_ambiguous')
    target = {name: row.get(name, '') for name in ('assignment_key', 'section_id', 'section_name')}
    target.update(class_key=assignments[0]['class_key'], file_id=assessment_target.file_id(row), identity={})
    assessment_target.read_target(assertion, target, config)
    target['identity'] = {name: row.get(name, '') for name in records.IDENTITY_KEYS}
    return target, book, base


def _ids(task_id, run_id, digest):
    if (not isinstance(run_id, str) or not re.fullmatch(r'run_[a-f0-9]{64}', run_id)
            or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)):
        raise Conflict('assessment_legacy_run_unverified')
    return (task_id, run_id, key('completed', {'task': task_id, 'input_hash': digest}),
            key('assessmentlegacyrecovery', {'task': task_id, 'run': run_id}))


def _pending(snapshot, ids, scope, packet=None):
    task_id, run_id, completed_id, _ = ids
    task_doc, run_doc = snapshot.documents[task_id], snapshot.documents[run_id]
    if task_doc is None or run_doc is None or snapshot.documents[completed_id] is not None:
        raise Conflict('assessment_legacy_run_unverified')
    task, run = task_doc.value, run_doc.value
    if _ids(task_id, run_id, run.get('input_hash')) != ids:
        raise Conflict('assessment_legacy_run_changed')
    if (task.get('scope') != scope or task.get('status') != 'Working' or task.get('active_run') != run_id
            or run.get('task_id') != task_id or not isinstance(task.get('owner'), str) or not task['owner']
            or task.get('owner') != run.get('owner') or type(task.get('fence')) is not int
            or task['fence'] < 1 or type(run.get('fence')) is not int or task['fence'] != run['fence']
            or 'receipt' not in run or run['receipt'] is not None or run.get('reconciliation') is not None):
        raise Conflict('assessment_legacy_run_changed')
    if packet is not None and (run.get('input_hash') != content_hash(packet)
            or task.get('input_hash', run['input_hash']) != run['input_hash']):
        raise Conflict('assessment_legacy_packet_mismatch')
    try:
        now = instant(snapshot.read_time)
        if now < instant(task['expires_at']) or now < instant(run['expires_at']):
            raise Conflict('assessment_legacy_run_not_expired')
        instant(run['started_at'])
    except (ValueError, TypeError, KeyError):
        raise Conflict('assessment_legacy_time_unverified') from None
    return task_doc, run_doc


def _closed(doc, run_doc, task_id, run_id, recovery_id, request_hash=None):
    if doc is None:
        return None
    value = doc.value
    old_run = deepcopy(run_doc.value) if run_doc else {}
    old_run.pop('reconciliation', None)
    if (not isinstance(value, dict) or set(value) != RECEIPT_FIELDS
            or value.get('schema') != RECEIPT_SCHEMA or value.get('record_id') != recovery_id
            or value.get('task_id') != task_id or value.get('run_id') != run_id
            or run_doc is None or run_doc.value.get('task_id') != task_id
            or value.get('original_run_sha256') != content_hash(old_run)
            or run_doc.value.get('reconciliation') != {'record_id': recovery_id, 'status': 'verified_no_write',
                                                       'receipt_sha256': content_hash(value)}
            or run_doc.value.get('receipt') is not None or value.get('fence') != old_run.get('fence')
            or value.get('input_hash') != old_run.get('input_hash')
            or type(value.get('original_review_basis_available')) is not bool
            or (not _digest(value.get('original_baseline_sha256')) if value.get('original_review_basis_available')
                else value.get('original_baseline_sha256') is not None)
            or any(not _digest(value.get(name)) for name in ('input_hash', 'request_sha256',
                'original_run_sha256', 'task_before_sha256', 'current_baseline_sha256', 'native_absence_sha256'))
            or value.get('historical_evidence_classification') != 'operator_attested_ui_observation_and_no_write'
            or type(value.get('grade_writes')) is not int or value['grade_writes'] != 0
            or request_hash is not None and value.get('request_sha256') != request_hash):
        raise Conflict('assessment_legacy_receipt_conflict')
    try:
        _record_id(value['baseline_record_id']); _record_id(value['assessment_record_id'])
        _evidence(value['historical_evidence'])
        if (not isinstance(value['actor'], str) or not 1 <= len(value['actor']) <= 400
                or instant(value['historical_evidence']['attempt_started_at']) != instant(old_run['started_at'])
                or not instant(old_run['started_at']) <= instant(value['historical_evidence']['first_absence_at']) <= instant(value['verified_at'])
                or not isinstance(value['current_source'], dict)
                or not _digest(value['current_source'].get('sha256'))
                or value['schema_proof'].get('outcome') != 'ready'
                or value['schema_proof'].get('missing_columns')
                or not _digest(value['schema_proof'].get('headers_sha256'))):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Conflict('assessment_legacy_receipt_conflict') from None
    return value


def _reply(receipt, *, already=True):
    return {'schema': SCHEMA, 'status': 'already_reconciled' if already else 'reconciled',
            'grade_writes': 0, 'readback_verified': True, 'receipt': deepcopy(receipt)}


def _challenge(ids, task_doc, run_doc, base, capture, book):
    return {'schema': CHALLENGE_SCHEMA, 'record_id': base['submission']['record_id'],
        'task_id': ids[0], 'run_id': ids[1], 'fence': run_doc.value['fence'],
        'input_hash': run_doc.value['input_hash'], 'task_version': task_doc.version,
        'run_version': run_doc.version, 'task_sha256': content_hash(task_doc.value),
        'run_sha256': content_hash(run_doc.value), 'current_baseline_sha256': content_hash(base),
        'current_source': review.source_binding(capture), 'workbook_sha256': content_hash(book),
        'headers_sha256': content_hash({title: book[title]['headers'] for title in ('Submissions', 'Answers')})}


def inspect(assertion, record_id, config, *, run_id=None):
    """Read the selected operation and current proof; no private-state writes."""
    record_id = _record_id(record_id)
    _actor(assertion, config)
    target, book, base = _context(assertion, record_id, config)
    scope = review.task_scope(target); task_id = key('task', scope)
    port = saving.configured_store(config)
    first = port.read([task_id]); task_doc = first.documents[task_id]
    if task_doc is None:
        raise Conflict('assessment_legacy_no_pending_save')
    task = task_doc.value
    if run_id is None:
        run_id = task.get('active_run') or (task.get('last_recovery') or {}).get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch(r'run_[a-f0-9]{64}', run_id):
        raise Conflict('assessment_legacy_no_pending_save')
    run_doc = port.read([run_id]).documents[run_id]
    if run_doc is None or run_doc.value.get('task_id') != task_id:
        raise Conflict('assessment_legacy_run_unverified')
    ids = _ids(task_id, run_id, run_doc.value.get('input_hash'))
    snapshot = port.read(list(ids))
    closed = _closed(snapshot.documents[ids[3]], snapshot.documents[run_id], task_id, run_id, ids[3])
    if closed:
        if closed.get('baseline_record_id') != record_id:
            raise Conflict('assessment_legacy_recovery_not_for_record')
        return _reply(closed)
    task_doc, run_doc = _pending(snapshot, ids, scope)
    if base['submission'].get('status') != 'assessed' or base['submission'].get('superseded_by'):
        raise Conflict('assessment_legacy_baseline_changed')
    capture = saving.capture(assertion, target['file_id'], config)
    after_book = review.read_workbook(config)
    after_capture = saving.capture(assertion, target['file_id'], config)
    final = port.read(list(ids))
    final_task, final_run = _pending(final, ids, scope)
    if (book != after_book or review.source_binding(capture) != review.source_binding(after_capture)
            or task_doc != final_task or run_doc != final_run):
        raise Conflict('assessment_legacy_evidence_changed')
    # New callers retain the whole envelope privately before claim. Legacy
    # attempts simply return None: the missing UUID is never guessed.
    from . import assessment_attempts
    retained = assessment_attempts.read(port, task_id, run_id)
    if retained and (retained['input_hash'] != run_doc.value['input_hash']
            or retained['run_id'] != run_id or retained['task_id'] != task_id):
        raise Conflict('assessment_legacy_retained_attempt_conflict')
    retained_basis = retained['request'].get('review_basis') if retained else None
    if retained and 'review_basis' in retained['request']:
        review.validate_successor(retained['request']['packet'], retained_basis, after_book, after_capture)
    return {'schema': SCHEMA, 'status': 'legacy_pending_review',
        'challenge': _challenge(ids, task_doc, run_doc, base, after_capture, after_book),
        'run': {name: run_doc.value[name] for name in ('started_at', 'expires_at')},
        'retained_attempt': deepcopy(retained['request']) if retained else None,
        'original_review_basis_available': retained_basis is not None,
        'original_baseline_sha256': retained_basis.get('record_sha256') if isinstance(retained_basis, dict) else None,
        'historical_evidence_classification': 'operator_attestation_required',
        'grade_writes': 0, 'external_writes': 0, 'live_model_calls': 0}


def _payload(payload):
    if (not isinstance(payload, dict) or set(payload) != {'mode', 'record_id', 'packet', 'challenge', 'historical_evidence'}
            or payload['mode'] != 'legacy_ui_prewrite' or not isinstance(payload['packet'], dict)
            or not isinstance(payload['challenge'], dict) or set(payload['challenge']) != CHALLENGE_FIELDS
            or payload['challenge']['schema'] != CHALLENGE_SCHEMA
            or payload['challenge']['record_id'] != payload['record_id']):
        raise ValueError('invalid_assessment_legacy_recovery')
    _record_id(payload['record_id'])
    _evidence(payload['historical_evidence'])
    bounded_json(payload)
    return deepcopy(payload)


def reconcile(assertion, payload, config):
    payload = _payload(payload)
    actor = _actor(assertion, config)
    packet, expected, evidence = payload['packet'], payload['challenge'], payload['historical_evidence']
    assessment_target.read_target(assertion, packet, config)
    scope = review.task_scope(packet); task_id = key('task', scope)
    ids = _ids(task_id, expected['run_id'], content_hash(packet))
    if expected['task_id'] != task_id or expected['input_hash'] != content_hash(packet):
        raise Conflict('assessment_legacy_packet_mismatch')
    request_hash = content_hash({'payload': payload, 'actor': actor})
    port = saving.configured_store(config)
    snapshot = port.read(list(ids))
    closed = _closed(snapshot.documents[ids[3]], snapshot.documents[ids[1]], task_id, ids[1], ids[3], request_hash)
    if closed:
        return _reply(closed)
    task_doc, run_doc = _pending(snapshot, ids, scope, packet)
    try:
        if (instant(evidence['attempt_started_at']) != instant(run_doc.value['started_at'])
                or not instant(run_doc.value['started_at']) <= instant(evidence['first_absence_at']) <= instant(snapshot.read_time)):
            raise Conflict('assessment_legacy_attestation_time_mismatch')
    except (ValueError, TypeError):
        raise Conflict('assessment_legacy_attestation_time_mismatch') from None
    target, book, base = _context(assertion, payload['record_id'], config)
    if review.task_scope(target) != scope:
        raise Conflict('assessment_legacy_target_ambiguous')
    capture = saving.capture(assertion, target['file_id'], config)
    current_basis = {'mode': review.FRESH_MODE, 'record_id': payload['record_id'],
        'record_sha256': content_hash(base), 'source': review.source_binding(capture),
        'packet_sha256': content_hash(packet)}
    native_base, attempted_id, schema = prior._native(packet, current_basis, book, capture)
    from . import assessment_attempts
    retained = assessment_attempts.read(port, task_id, ids[1])
    retained_basis = None
    if retained:
        if (retained['input_hash'] != run_doc.value['input_hash'] or retained['request']['packet'] != packet
                or retained['assessment_record_id'] != attempted_id):
            raise Conflict('assessment_legacy_retained_attempt_conflict')
        if 'review_basis' in retained['request']:
            retained_basis = retained['request']['review_basis']
            review.validate_successor(packet, retained_basis, book, capture)
    if content_hash(expected) != content_hash(_challenge(ids, task_doc, run_doc, base, capture, book)):
        raise Conflict('assessment_legacy_challenge_changed')
    after_book = review.read_workbook(config)
    after_capture = saving.capture(assertion, target['file_id'], config)
    after_base, after_id, after_schema = prior._native(packet, current_basis, after_book, after_capture)
    if retained_basis is not None:
        review.validate_successor(packet, retained_basis, after_book, after_capture)
    final = port.read(list(ids)); final_task, final_run = _pending(final, ids, scope, packet)
    if (book != after_book or review.source_binding(capture) != review.source_binding(after_capture)
            or base != native_base or base != after_base or attempted_id != after_id or schema != after_schema
            or task_doc != final_task or run_doc != final_run):
        raise Conflict('assessment_legacy_evidence_changed')
    receipt = {'schema': RECEIPT_SCHEMA, 'record_id': ids[3], 'task_id': task_id, 'run_id': ids[1],
        'fence': run_doc.value['fence'], 'input_hash': run_doc.value['input_hash'],
        'assessment_record_id': attempted_id, 'baseline_record_id': payload['record_id'],
        'actor': actor, 'request_sha256': request_hash, 'verified_at': final.read_time,
        'original_run_sha256': content_hash(run_doc.value), 'task_before_sha256': content_hash(task_doc.value),
        'previous_recovery': deepcopy(task_doc.value.get('last_recovery')),
        'original_review_basis_available': retained_basis is not None,
        'original_baseline_sha256': retained_basis['record_sha256'] if retained_basis is not None else None,
        'current_baseline_sha256': content_hash(base), 'current_source': review.source_binding(after_capture),
        'native_absence_sha256': content_hash({'workbook': after_book, 'absent_record_id': attempted_id}),
        'schema_proof': schema, 'historical_evidence': evidence,
        'historical_evidence_classification': 'operator_attested_ui_observation_and_no_write',
        'grade_writes': 0}
    bounded_json(receipt)
    task = {**deepcopy(task_doc.value), 'active_run': None, 'status': 'Needs you',
        'next_action': 'verified_prewrite_failure_recovered',
        'last_recovery': {'record_id': ids[3], 'run_id': ids[1]}}
    run = {**deepcopy(run_doc.value), 'reconciliation': {'record_id': ids[3], 'status': 'verified_no_write',
                                                      'receipt_sha256': content_hash(receipt)}}
    changes = {task_id: (task, task_doc.version), ids[1]: (run, run_doc.version), ids[3]: (receipt, None)}
    try:
        port.commit(changes)
    except (Conflict, StoreUnavailable):
        actual = port.read(list(changes)).documents
        if any(actual[name] is None or actual[name].value != value for name, (value, _) in changes.items()):
            raise
    actual = port.read(list(changes)).documents
    if any(actual[name] is None or actual[name].value != value for name, (value, _) in changes.items()):
        raise StoreUnavailable('assessment_legacy_readback_unconfirmed')
    return _reply(receipt, already=False)
