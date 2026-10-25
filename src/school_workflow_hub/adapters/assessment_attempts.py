"""Immutable Save input evidence, durably read back before a claim can start.

This adapter only retains an already authorized request in private CAS state.
It never claims a run, writes grades, recovers ownership or dispatches Save.
"""
from copy import deepcopy
import re

from ..core.cloud_state import Conflict, StoreUnavailable, bounded_json, instant, key
from ..core.runs import content_hash
from ..workflows import assessment_records as records


SCHEMA = 'hub-assessment-attempt/v1'
FIELDS = {'schema', 'task_id', 'run_id', 'input_hash', 'assessment_record_id',
          'request', 'captured_source', 'created_at'}
SOURCE_FIELDS = {'file_id', 'sha256', 'version', 'modified_at'}
SOURCE_EXTRA = {'raw_sha256', 'metadata_sha256'}


def _identifier(kind, value):
    if not isinstance(value, str) or not re.fullmatch(kind + r'_[a-f0-9]{64}', value):
        raise ValueError('invalid_assessment_attempt')


def _inputs(task_id, request, source):
    from . import assessment_review as review
    _identifier('task', task_id)
    if (not isinstance(request, dict) or set(request) not in (
            {'request_id', 'packet'}, {'request_id', 'packet', 'review_basis'})
            or not isinstance(request.get('request_id'), str)
            or not re.fullmatch(r'[A-Za-z0-9-]{16,64}', request['request_id'])
            or not isinstance(request.get('packet'), dict)
            or set(request['packet']) != records.PACKET_KEYS
            or ('review_basis' in request and not isinstance(request['review_basis'], dict))
            or not isinstance(source, dict) or not SOURCE_FIELDS <= set(source) <= SOURCE_FIELDS | SOURCE_EXTRA):
        raise ValueError('invalid_assessment_attempt')
    try:
        packet = request['packet']
        if task_id != key('task', review.task_scope(packet)):
            raise ValueError('invalid_assessment_attempt')
        packet_source = packet['source']
        # Ordinary Save historically accepts identical text after metadata-only
        # changes. Retain both metadata versions; reviewed successors keep their
        # stricter existing source guard in the caller.
        if (not isinstance(packet_source, dict)
                or any(source.get(name) != packet_source.get(name) for name in ('file_id', 'sha256'))
                or not isinstance(source['file_id'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', source['file_id'])
                or not isinstance(source['version'], str) or len(source['version']) > 200
                or any(not isinstance(source[name], str) or not re.fullmatch(r'[a-f0-9]{64}', source[name])
                       for name in {'sha256'} | (set(source) & SOURCE_EXTRA))):
            raise ValueError('invalid_assessment_attempt')
        instant(source['modified_at'])
        return (key('run', {'task': task_id, 'request': request['request_id']}),
                content_hash(packet), records.record_id(packet))
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError('invalid_assessment_attempt') from None


def _validate(value, task_id, run_id):
    try:
        if (not isinstance(value, dict) or set(value) != FIELDS
                or value['schema'] != SCHEMA or value['task_id'] != task_id or value['run_id'] != run_id):
            raise ValueError()
        expected_run, digest, record_id = _inputs(task_id, value['request'], value['captured_source'])
        if expected_run != run_id or value['input_hash'] != digest or value['assessment_record_id'] != record_id:
            raise ValueError()
        instant(value['created_at'])
        bounded_json(value)
    except (KeyError, TypeError, AttributeError, ValueError):
        raise Conflict('assessment_attempt_conflict') from None
    return deepcopy(value)


def _record_id(task_id, run_id):
    _identifier('task', task_id)
    _identifier('run', run_id)
    return key('assessmentattempt', {'task': task_id, 'run': run_id})


def read(port, task_id, run_id):
    """Read detached immutable evidence; absence does not reconstruct a request."""
    record_id = _record_id(task_id, run_id)
    doc = port.read([record_id]).documents[record_id]
    return None if doc is None else _validate(doc.value, task_id, run_id)


def _same_input(actual, expected):
    return ({k: v for k, v in actual.items() if k != 'created_at'}
            == {k: v for k, v in expected.items() if k != 'created_at'})


def record(port, task_id, request, captured_source):
    """Create once and verify readback before the caller starts its claim.

Identical replays retain the first server timestamp. There is one mutation
attempt; an uncertain acknowledgement is resolved by exact readback only.
"""
    run_id, digest, assessment_id = _inputs(task_id, request, captured_source)
    record_id = _record_id(task_id, run_id)
    snapshot = port.read([record_id])
    envelope = {'schema': SCHEMA, 'task_id': task_id, 'run_id': run_id,
                'input_hash': digest, 'assessment_record_id': assessment_id,
                'request': deepcopy(request), 'captured_source': deepcopy(captured_source),
                'created_at': snapshot.read_time}
    instant(envelope['created_at'])
    bounded_json(envelope)
    prior = snapshot.documents[record_id]
    if prior is not None:
        actual = _validate(prior.value, task_id, run_id)
        if not _same_input(actual, envelope):
            raise Conflict('assessment_attempt_conflict')
        return actual
    try:
        port.commit({record_id: (envelope, None)})
    except Conflict:
        actual = read(port, task_id, run_id)
        if actual is None or not _same_input(actual, envelope):
            raise Conflict('assessment_attempt_conflict') from None
        return actual
    except StoreUnavailable:
        actual = read(port, task_id, run_id)
        if actual != envelope:
            raise StoreUnavailable('assessment_attempt_readback_unconfirmed') from None
        return actual
    actual = read(port, task_id, run_id)
    if actual != envelope:
        raise StoreUnavailable('assessment_attempt_readback_unconfirmed')
    return actual
