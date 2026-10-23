"""Retain one proven teacher PPTX after an otherwise unchanged trash transition.

Trusted-host preparation/publication only. This is not a new teacher-material
classification, source repair, student exclusion policy, or HTTP writer.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import re
from time import monotonic

from . import agenda as agenda_module, google, teacher_material as tm
from .source_operations import SourceOperations
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, bounded_json, instant, key
from school_workflow_hub.core.runs import content_hash

SCHEMA = 'hub-teacher-material-history/v1'
INTENT_SCHEMA = 'hub-teacher-material-history-intent/v1'
FINAL_CHECK_SECONDS = 180
COMMIT_READBACK_SECONDS = 120
META_FIELDS = set(tm.META.split(','))


def record_key(prior_record_key, metadata):
    return key('teachermaterialhistory', {'prior_record_key': prior_record_key, 'metadata': metadata})


def intent_key(ident):
    return key('teachermaterialhistoryintent', {'record_key': ident})


def binding_key(prior_key):
    return key('teachermaterialhistorybinding', {'prior_record_key': prior_key})


def _binding(value):
    return {k: value[k] for k in ('schema', 'record_key', 'prior_record_key')}


def _transition(prior, current):
    """Only this exact binary artifact, with no other metadata differences."""
    if (not isinstance(prior, dict) or not isinstance(current, dict)
            or set(prior) != META_FIELDS or set(current) != META_FIELDS
            or prior['trashed'] is not False or current['trashed'] is not True
            or prior['mimeType'] != google.PPTX
            or any(current[k] != prior[k] for k in META_FIELDS - {'version', 'trashed'})
            or any(not isinstance(m['version'], str) or not re.fullmatch(r'[1-9][0-9]*', m['version'])
                   for m in (prior, current))
            or int(current['version']) <= int(prior['version'])
            or not isinstance(prior['size'], str) or not re.fullmatch(r'[1-9][0-9]*', prior['size'])
            or not 0 < int(prior['size']) <= google.MAX_DECK_BYTES
            or not isinstance(prior['md5Checksum'], str) or not re.fullmatch(r'[a-f0-9]{32}', prior['md5Checksum'])
            or not isinstance(prior['parents'], list) or len(prior['parents']) != 1
            or not tm.ID.fullmatch(str(prior['parents'][0]))
            or not tm.ID.fullmatch(str(prior['id'])) or not isinstance(prior['name'], str) or not prior['name']):
        raise tm.Unverified('The original is not an otherwise unchanged teacher-PPTX trash transition.')
    instant(prior['modifiedTime'])


def _same_documents(port, basis):
    current = port.read(list(basis)).documents
    if any(current.get(k) != doc for k, doc in basis.items()):
        raise tm.Unverified('Teacher-material history or its original receipt changed during verification.')


def bookend(token, config, agenda, proof, basis, *, port):
    """Fresh native/context/state checks; never applies staged classification."""
    s, event = proof['scope'], proof['event']
    fresh = agenda_module.load(token, agenda.spreadsheet_id)
    if (tm.scope(fresh, config, s['class_key'], s['week'], s['form_id']) != s
            or google.drive_file(token, proof['file_id'], tm.META) != proof['metadata']
            or tm._event(token, s['form_id'], event['response_id'], proof['file_id'], config) != event):
        raise tm.Unverified('The historical teacher original or its exact Agenda/Form binding changed.')
    _same_documents(port, basis)


def _capture(token, config, agenda, prior_key, *, port):
    old = port.read([prior_key]).documents.get(prior_key)
    if old is None:
        raise tm.Unverified('The original immutable teacher-material receipt is unavailable.')
    receipt = tm.validate(old.value, config)
    if receipt['record_key'] != prior_key:
        raise tm.Unverified('The original teacher-material receipt key does not match.')
    previous = receipt['proof']; s = previous['scope']; event = previous['event']
    index_id = tm.index_key(s, event['response_id'], previous['file_id'])
    index = port.read([index_id]).documents.get(index_id)
    records = (index.value if index else {}).get('records')
    if (not index or index.value.get('schema') != tm.SCHEMA or not isinstance(records, list)
            or not 1 <= len(records) <= 50 or len(set(records)) != len(records)
            or prior_key not in records or any(not isinstance(v, str) or not v.startswith('teachermaterial_') for v in records)):
        raise tm.Unverified('The original teacher-material index cannot be verified.')
    basis = {prior_key: old, index_id: index}
    fresh = agenda_module.load(token, agenda.spreadsheet_id)
    if (tm.scope(fresh, config, s['class_key'], s['week'], s['form_id']) != s
            or tm._event(token, s['form_id'], event['response_id'], previous['file_id'], config) != event):
        raise tm.Unverified('The original teacher-material context or Form event changed.')
    md = google.drive_file(token, previous['file_id'], tm.META)
    if md.get('id') != previous['file_id']:
        raise tm.Unverified('Historical teacher-material metadata names a different original.')
    _transition(previous['metadata'], md)
    data = google.deck_bytes(token, previous['file_id'], google.PPTX)
    if (not data or len(data) != int(md['size']) or hashlib.md5(data).hexdigest() != md['md5Checksum']
            or hashlib.sha256(data).hexdigest() != previous['content_sha256']):
        raise tm.Unverified('The complete historical teacher-PPTX bytes do not match the original receipt.')
    proof = {**deepcopy(previous), 'metadata': md}
    bookend(token, config, agenda, proof, basis, port=port)
    return proof, basis, receipt


def validate(value, config):
    bounded_json(value)
    fields = {'schema', 'record_key', 'prior_record_key', 'prior_record_sha256', 'proof',
              'operator', 'instruction_sha256', 'evidence', 'verified_at'}
    if (not isinstance(value, dict) or set(value) != fields or value['schema'] != SCHEMA
            or not isinstance(value['prior_record_key'], str)
            or not re.fullmatch(r'teachermaterial_[a-f0-9]{64}', value['prior_record_key'])
            or any(not tm.HASH.fullmatch(str(value[k])) for k in ('prior_record_sha256', 'instruction_sha256'))
            or value['operator'] not in tm.teachers(config)
            or not isinstance(value['evidence'], str) or not 20 <= len(value['evidence']) <= 4000
            or instant(value['verified_at']) > datetime.now(timezone.utc)):
        raise tm.Unverified('Historical teacher-material disposition needs complete attributed proof.')
    p = value['proof']
    if (not isinstance(p, dict) or set(p) != {'scope', 'event', 'file_id', 'metadata', 'content_sha256'}
            or not all(isinstance(p[k], dict) for k in ('scope', 'event', 'metadata'))
            or not tm.HASH.fullmatch(str(p['content_sha256'])) or p['metadata'].get('id') != p['file_id']
            or value['record_key'] != record_key(value['prior_record_key'], p['metadata'])):
        raise tm.Unverified('Historical teacher-material disposition does not bind one exact original.')
    return value


def prepare(token, config, agenda, prior_record_key, *, operator, instruction_sha256, evidence, port=None):
    """Read-only proposal; no receipt, intent, source or claim is written."""
    port = port if port is not None else configured_store(config)
    proof, _, receipt = _capture(token, config, agenda, prior_record_key, port=port)
    value = {'schema': SCHEMA, 'record_key': record_key(prior_record_key, proof['metadata']),
             'prior_record_key': prior_record_key, 'prior_record_sha256': content_hash(receipt),
             'proof': proof, 'operator': operator, 'instruction_sha256': instruction_sha256,
             'evidence': evidence, 'verified_at': datetime.now(timezone.utc).isoformat()}
    return validate(value, config)


def _verified_capture(token, config, agenda, value, port):
    proof, basis, prior = _capture(token, config, agenda, value['prior_record_key'], port=port)
    if proof != value['proof'] or content_hash(prior) != value['prior_record_sha256']:
        raise tm.Unverified('Historical teacher-material proof changed after preparation.')
    return basis


def _write_guard(token, config, agenda, value, basis, port, guard):
    # Firestore calls this AFTER acquiring its write token. Source checks can be
    # slow, so reserve their complete bounded interval in addition to the CAS
    # and immediate readback. The final raw owner read cannot renew a lost lease.
    guard.before_write(required_seconds=FINAL_CHECK_SECONDS + COMMIT_READBACK_SECONDS)
    started = monotonic()
    bookend(token, config, agenda, value['proof'], basis, port=port)
    read_started = monotonic()
    snapshot = port.read([guard.task_id])
    finished = monotonic()
    if finished - started > FINAL_CHECK_SECONDS:
        raise tm.Unverified('Final historical-source verification exceeded its reserved window.')
    now = instant(snapshot.read_time) + timedelta(seconds=max(0, finished - read_started))
    doc = snapshot.documents.get(guard.task_id)
    if doc is None:
        raise StoreUnavailable('Source preparation ownership is unavailable.')
    guard.cloud._owns(guard.claim, doc.value, now.isoformat())
    if (instant(doc.value['expires_at']) - now).total_seconds() <= COMMIT_READBACK_SECONDS:
        raise tm.Unverified('Historical disposition cannot finish under the current source ownership window.')
    # Retain this exact raw owner/fence read, then re-read the guard's original
    # time/authorization policy. This final check cannot renew a lost lease.
    guard.validate_write_budget(required_seconds=COMMIT_READBACK_SECONDS)
    if monotonic() - started > FINAL_CHECK_SECONDS:
        raise tm.Unverified('Final historical-source verification exceeded its reserved window.')


def import_verified(token, config, agenda, value, *, guard, port=None):
    """Publish once through the actual shared guard; uncertain writes never retry.

    A pending immutable intent blocks later writes for this exact disposition.
    A completed intent/receipt can only be re-read, never rewritten. The
    original receipt and original index are always read-only.
    """
    validate(value, config)
    if not isinstance(guard, SourceOperations):
        raise tm.Unverified('The existing shared source-operations guard is required.')
    if guard.input_hash != content_hash(value):
        raise tm.Unverified('The shared source claim must bind this exact prepared disposition.')
    port = guard.port if port is None else port
    if port is not guard.port or not callable(getattr(port, 'commit_guarded', None)):
        raise tm.Unverified('The existing guarded state adapter and shared ownership store are required.')
    ident = value['record_key']; intent_id = intent_key(ident)
    binding_id = binding_key(value['prior_record_key'])
    snapshot = port.read([ident, intent_id, binding_id]); docs = snapshot.documents
    expected = {'schema': INTENT_SCHEMA, 'record_key': ident, 'value_sha256': content_hash(value),
                'operator': value['operator'], 'state': 'verified'}
    if docs.get(ident):
        if (docs[ident].value != value or not docs.get(intent_id) or docs[intent_id].value != expected
                or not docs.get(binding_id) or docs[binding_id].value != _binding(value)):
            raise Conflict('Historical teacher-material publication conflicts with its immutable receipt.')
        _verified_capture(token, config, agenda, value, port)
        _same_documents(port, docs)
        return ident
    if docs.get(intent_id):
        raise StoreUnavailable('Historical teacher-material publication is uncertain; inspect its exact receipt, do not retry.')
    if docs.get(binding_id):
        raise Conflict('An immutable historical disposition already binds this original receipt.')
    age = (instant(snapshot.read_time) - instant(value['verified_at'])).total_seconds()
    if not 0 <= age <= 900:
        raise tm.Unverified('Historical teacher-material preparation is stale or future-dated.')
    basis = _verified_capture(token, config, agenda, value, port)
    pending = {**expected, 'state': 'pending'}
    try:
        port.commit_guarded({intent_id: (pending, None)}, before_write=lambda: _write_guard(
            token, config, agenda, value, basis, port, guard))
    except Exception:
        guard.stopped = True
        raise
    saved = port.read([ident, intent_id, binding_id]).documents
    if saved.get(ident) or saved.get(binding_id) or not saved.get(intent_id) or saved[intent_id].value != pending:
        raise StoreUnavailable('Historical teacher-material intent readback is unconfirmed; do not retry.')
    basis = _verified_capture(token, config, agenda, value, port)
    basis.update(saved)
    try:
        port.commit_guarded({ident: (value, None), binding_id: (_binding(value), None),
                            intent_id: (expected, saved[intent_id].version)},
            before_write=lambda: _write_guard(token, config, agenda, value, basis, port, guard))
    except Exception:
        guard.stopped = True
        raise
    final = port.read([ident, intent_id, binding_id]).documents
    if (not final.get(ident) or final[ident].value != value
            or not final.get(intent_id) or final[intent_id].value != expected
            or not final.get(binding_id) or final[binding_id].value != _binding(value)):
        raise StoreUnavailable('Historical teacher-material receipt readback is unconfirmed; do not retry.')
    _verified_capture(token, config, agenda, value, port)
    _same_documents(port, final)
    return ident


def resolve(token, config, agenda, current_scope, response_id, file_id, prior_keys, *, port):
    """Read the immutable binding, so later transitions cannot inherit it."""
    matches = []
    for prior_key in prior_keys:
        binding_id = binding_key(prior_key)
        binding = port.read([binding_id]).documents.get(binding_id)
        if not binding:
            continue
        bound = binding.value
        if (set(bound) != {'schema', 'record_key', 'prior_record_key'} or bound['schema'] != SCHEMA
                or bound['prior_record_key'] != prior_key
                or not re.fullmatch(r'teachermaterialhistory_[a-f0-9]{64}', str(bound['record_key']))):
            raise tm.Unverified('Historical teacher-material binding is incomplete.')
        ident = bound['record_key']
        intent_id = intent_key(ident)
        docs = port.read([ident, intent_id]).documents
        stored = docs.get(ident)
        if not stored:
            raise StoreUnavailable('The bound historical teacher-material disposition is unavailable.')
        value = validate(stored.value, config); p = value['proof']
        if (p['scope'] != current_scope or p['event']['response_id'] != response_id or p['file_id'] != file_id
                or value['prior_record_key'] != prior_key or _binding(value) != bound):
            raise tm.Unverified('The saved teacher-material disposition has a different source binding.')
        expected = {'schema': INTENT_SCHEMA, 'record_key': ident, 'value_sha256': content_hash(value),
                    'operator': value['operator'], 'state': 'verified'}
        if not docs.get(intent_id) or docs[intent_id].value != expected:
            raise StoreUnavailable('Historical teacher-material publication is not verified.')
        basis = _verified_capture(token, config, agenda, value, port)
        basis.update(docs)
        basis[binding_id] = binding
        bookend(token, config, agenda, p, basis, port=port)
        matches.append((value, basis))
    if not matches:
        return None
    if len(matches) != 1:
        raise tm.Unverified('Exactly one verified historical teacher-material disposition is required.')
    return matches[0]
