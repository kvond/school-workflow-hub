"""Append-only Helper observations, never grade entry or current IC certification.

Prepared source evidence comes from the existing server list build. Results are
the same Helper's point-in-time panel observations, not independently verified
IC reads. A started ledger append is only reconciled by readback, never retried.
"""
from copy import deepcopy
import math
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import google, ledger, morning_entry, roster
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, bounded_json, instant, key
from school_workflow_hub.core.runs import content_hash


SCHEMA = 'hub-morning-student-observations/v1'
BASIS_SCHEMA = 'hub-morning-observation-basis/v1'
PANEL_SCHEMA = 'hub-morning-panel-observation/v1'
STATE_SCHEMA = 'hub-morning-observation-event/v1'
ROW_SCHEMA = 'hub-morning-student-observation-row/v1'
KIND = 'Morning student observation'
FIELDS = {'schema', 'phase', 'run_id', 'list', 'scope', 'revision', 'target', 'basis', 'observation'}
LIST_FIELDS = {'schema', 'list_id', 'class_key', 'class_name', 'week', 'form_id', 'assignment',
               'form_label', 'status', 'status_text', 'sent_at', 'rows'}
SOURCE_FIELDS = {'file_id', 'version', 'modified_at', 'source_form_id',
                 'source_response_id', 'source_submitted_at'}
OBSERVATION_CODES = frozenset(('ic_changed', 'nothing_to_enter', 'typed_values_differ',
    'readback_incomplete', 'read_back', 'readback_mismatch', 'typing_stopped', 'ic_unreadable', 'save_unconfirmed'))
REASON_CODES = frozenset(('no_hub_match', 'ambiguous_hub_match', 'no_score_prepared',
    'exempt_preserved', 'new_student_exemption_needed', 'source_needs_check',
    'component_not_applicable', 'ic_non_numeric', 'same_score', 'higher_ic_score',
    'ic_flag', 'blank_fill', 'lower_score_raise'))
READBACKS = frozenset(('not_observed', 'saved_panel_checked', 'reloaded_match', 'reloaded_mismatch', 'unavailable'))
ROW_FIELDS = {'hub_row_index', 'ic_name', 'label', 'planned_action', 'reason_code', 'before',
              'proposed', 'before_flags', 'fill_attempted', 'fill_returned', 'readback',
              'after', 'after_flags', 'observed_at'}


class Refused(ValueError):
    def __init__(self, code='student_observation_invalid'):
        super().__init__(code)
        self.code = code


def _require(ok, code='student_observation_invalid'):
    if not ok:
        raise Refused(code)


def _text(value, limit=200, *, empty=False):
    return (isinstance(value, str) and len(value) <= limit
            and (empty or bool(value.strip())) and all(ord(c) >= 32 and ord(c) != 127 for c in value))


def _hash(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[a-f0-9]{64}', value))


def _time(value):
    _require(_text(value, 40))
    try:
        return instant(value)
    except ValueError:
        raise Refused() from None


def _number(value, *, proposed=False):
    return value is None or (type(value) in (int, float) and math.isfinite(value)
        and value >= 0 and (not proposed or value <= 10))


def _same_score(left, right):
    return left is right if left is None or right is None else abs(left - right) < 1e-9


def _basis(value, listed, revision):
    _require(isinstance(value, dict) and set(value) == {'schema', 'revision', 'rows'}
             and value['schema'] == BASIS_SCHEMA and value['revision'] == revision
             and isinstance(value['rows'], list) and len(value['rows']) == len(listed['rows']))
    for index, (basis, row) in enumerate(zip(value['rows'], listed['rows'])):
        _require(isinstance(basis, dict) and set(basis) == {'row_index', 'identity', 'sources'}
                 and type(basis['row_index']) is int and basis['row_index'] == index)
        identity = basis['identity']
        _require(isinstance(identity, dict) and set(identity) == {'first', 'last', 'section', 'match_basis'}
                 and all(identity[k] == row[k] for k in ('first', 'last', 'section'))
                 and _text(identity['match_basis'], 100))
        _require(isinstance(basis['sources'], list) and len(basis['sources']) <= 20)
        for source in basis['sources']:
            _require(isinstance(source, dict) and set(source) == SOURCE_FIELDS
                     and isinstance(source['file_id'], str)
                     and bool(re.fullmatch(r'[A-Za-z0-9_-]{10,200}', source['file_id'])))
            for field in SOURCE_FIELDS - {'file_id'}:
                _require(source[field] is None or _text(source[field], 200))
            for field in ('modified_at', 'source_submitted_at'):
                if source[field] is not None:
                    _time(source[field])


def _common(payload):
    _require(isinstance(payload, dict) and set(payload) == FIELDS
             and payload['schema'] == SCHEMA and payload['phase'] in ('prepared', 'result')
             and morning_entry._uuid(payload['run_id']) and _hash(payload['revision']))
    selected, listed = payload['scope'], payload['list']
    _require(isinstance(selected, dict) and set(selected) == {'class', 'week', 'form'}
             and selected['class'] in ('biology', 'forensics', 'anatomy')
             and _text(selected['week'], 100) and isinstance(selected['form'], str)
             and bool(re.fullmatch(r'[A-Za-z0-9_-]{10,100}', selected['form'])))
    _require(isinstance(listed, dict) and set(listed) == LIST_FIELDS
             and listed['schema'] == 'hub-ic-list/v1' and morning_entry._uuid(listed['list_id'])
             and (listed['class_key'], listed['week'], listed['form_id'])
                 == (selected['class'], selected['week'], selected['form'])
             and listed['status'] in ('before', 'open', 'closed'))
    for name in ('class_name', 'assignment', 'form_label'):
        _require(_text(listed[name], 200, empty=True))
    _require(_text(listed['status_text'], 400, empty=True))
    _time(listed['sent_at'])
    _require(isinstance(listed['rows'], list) and 1 <= len(listed['rows']) <= 300)
    seen = set()
    for row in listed['rows']:
        _require(isinstance(row, dict) and set(row) == {'last', 'first', 'section', 'kind', 'notes', 'revision', 'reason'}
                 and _text(row['last'], 100) and _text(row['first'], 100, empty=True)
                 and _text(row['section'], 100) and _text(row['reason'], 200, empty=True)
                 and row['kind'] in ('score', 'zero', 'excused', 'check', 'none')
                 and all(_number(row[k], proposed=True) for k in ('notes', 'revision')))
        _require((row['kind'] != 'zero' or row['notes'] == row['revision'] == 0)
                 and (row['kind'] != 'score' or row['notes'] is not None or row['revision'] is not None)
                 and (row['kind'] in ('score', 'zero') or row['notes'] is row['revision'] is None))
        identity = (roster.plain(row['last']), roster.plain(row['first']), row['section'])
        _require(identity not in seen)
        seen.add(identity)
    try:
        morning_entry._target(payload['target'])
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Refused() from None
    _require(any(r['section'] == payload['target']['section'] and r['kind'] in ('score', 'zero')
                 and r[payload['target']['column']] is not None for r in listed['rows']))
    _basis(payload['basis'], listed, payload['revision'])
    try:
        bounded_json(payload)
    except ValueError:
        raise Refused() from None


def validate_prepared(payload, current, *, now=None):
    """Compare to a newly built source list; keep the client's original UUID/time."""
    _common(payload)
    _require(payload['phase'] == 'prepared' and payload['observation'] is None)
    _require(isinstance(current, dict) and current.get('code') == 'ready', 'source_changed')
    fresh = current.get('list')
    _require(isinstance(fresh, dict) and set(fresh) == set(payload['list'])
             and all(fresh[k] == payload['list'][k] for k in fresh if k not in ('list_id', 'sent_at'))
             and current.get('revision') == payload['revision']
             and current.get('observation_basis') == payload['basis']
             and payload['target'] in current.get('targets', []), 'source_changed')
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    _require(-60 <= (now - _time(payload['list']['sent_at'])).total_seconds()
             <= morning_entry.TTL_SECONDS, 'list_expired')
    return deepcopy(payload)


def _name_keys(first, last):
    return {roster.compact(f + ' ' + last) for f in roster.first_names(first)}


def _matches(listed, observations):
    keys = [_name_keys(r['first'], r['last']) for r in listed['rows']]
    candidates = []
    for item in observations:
        last, comma, first = item['ic_name'].partition(',')
        names = _name_keys(first, last) if comma else {roster.compact(last)}
        candidates.append([i for i, mine in enumerate(keys) if mine & names])
    claims = {i: sum(i in found for found in candidates) for i in range(len(keys))}
    return [(found[0] if len(found) == 1 and claims[found[0]] == 1 else None,
             'ambiguous_hub_match' if found else 'no_hub_match') for found in candidates]


def _observation(payload, *, now=None):
    panel = payload['observation']
    _require(isinstance(panel, dict) and set(panel) == {'schema', 'started_at', 'finished_at',
        'save_attempted', 'save_confirmed', 'readback_status', 'code', 'rows'}
        and panel['schema'] == PANEL_SCHEMA and panel['code'] in OBSERVATION_CODES
        and type(panel['save_attempted']) is bool and type(panel['save_confirmed']) is bool
        and (not panel['save_confirmed'] or panel['save_attempted'])
        and panel['readback_status'] in ('not_attempted', 'complete', 'incomplete')
        and isinstance(panel['rows'], list) and len(panel['rows']) <= 300)
    started, finished = _time(panel['started_at']), _time(panel['finished_at'])
    _require(_time(payload['list']['sent_at']) <= started <= finished)
    # Match the existing list protocol's one-minute client clock tolerance.
    _require(now is None or (finished - now).total_seconds() <= 60)
    labels = set()
    for item in panel['rows']:
        _require(isinstance(item, dict) and set(item) == ROW_FIELDS
            and (item['hub_row_index'] is None or type(item['hub_row_index']) is int)
            and _text(item['ic_name'], 300) and _text(item['label'], 1000)
            and item['label'] not in labels and item['planned_action'] in ('fill', 'raise', 'keep', 'leave', 'skip')
            and item['reason_code'] in REASON_CODES and item['readback'] in READBACKS
            and type(item['fill_attempted']) is bool and type(item['fill_returned']) is bool
            and (not item['fill_returned'] or item['fill_attempted'])
            and _number(item['before']) and _number(item['after']) and _number(item['proposed'], proposed=True)
            and isinstance(item['before_flags'], list) and len(item['before_flags']) <= 300
            and all(_text(f, 100) for f in item['before_flags'])
            and (item['after_flags'] is None or isinstance(item['after_flags'], list)
                 and len(item['after_flags']) <= 300 and all(_text(f, 100) for f in item['after_flags'])))
        labels.add(item['label'])
        _require(item['label'] == payload['target']['label_code']
                 + ' MP: Marking Period Score input for ' + item['ic_name'])
        if item['readback'] in ('not_observed', 'unavailable'):
            _require(item['after'] is None and item['after_flags'] is None and item['observed_at'] is None)
        else:
            _require(item['after_flags'] is not None and started <= _time(item['observed_at']) <= finished)
            _require(item['after_flags'] == item['before_flags'])
        if item['readback'] == 'saved_panel_checked':
            _require(panel['code'] == 'nothing_to_enter' and not panel['save_attempted']
                     and panel['readback_status'] == 'not_attempted')
        if item['readback'] in ('reloaded_match', 'reloaded_mismatch'):
            _require(panel['save_attempted'] and panel['save_confirmed'])
        if item['fill_attempted']:
            _require(item['planned_action'] in ('fill', 'raise'))
    matches = _matches(payload['list'], panel['rows'])
    for item, (index, unknown_reason) in zip(panel['rows'], matches):
        _require(item['hub_row_index'] == index)
        if index is None:
            _require(item['planned_action'] == 'leave' and item['reason_code'] == unknown_reason
                     and item['proposed'] is None and not item['fill_attempted'])
            continue
        hub = payload['list']['rows'][index]
        _require(hub['section'] == payload['target']['section'])
        action, reason = item['planned_action'], item['reason_code']
        expected = hub[payload['target']['column']]
        if hub['kind'] == 'none':
            _require(action == 'skip' and reason == 'no_score_prepared' and item['proposed'] is None)
        elif hub['kind'] == 'excused':
            exempt = 'Exempt' in item['before_flags']
            _require((action, reason) == (('keep', 'exempt_preserved') if exempt else
                     ('leave', 'new_student_exemption_needed')) and item['proposed'] is None)
        elif hub['kind'] == 'check':
            _require(action == 'leave' and reason == 'source_needs_check' and item['proposed'] is None)
        elif expected is None:
            _require(action == 'skip' and reason == 'component_not_applicable' and item['proposed'] is None)
        else:
            _require(item['proposed'] == expected)
            if action == 'keep':
                _require(item['before'] is not None and item['before'] >= expected - 1e-9
                         and reason == ('higher_ic_score' if item['before'] > expected else 'same_score'))
            elif action == 'leave':
                _require((reason == 'ic_non_numeric' and item['before'] is None)
                         or (reason == 'ic_flag' and bool(item['before_flags'])))
            else:
                _require(action in ('fill', 'raise'))
        if item['planned_action'] in ('fill', 'raise'):
            _require(hub['kind'] in ('score', 'zero') and expected is not None and item['proposed'] == expected)
            _require((item['planned_action'] == 'fill' and item['before'] is None and item['reason_code'] == 'blank_fill')
                     or (item['planned_action'] == 'raise' and item['before'] is not None
                         and item['before'] < expected - 1e-9 and item['reason_code'] == 'lower_score_raise'))
            _require(not item['before_flags'] or hub['kind'] == 'zero'
                     and all(f == 'Missing' for f in item['before_flags']))
        else:
            _require(not item['fill_attempted'] and not item['fill_returned'])
        if item['readback'] == 'reloaded_match' and item['fill_attempted']:
            _require(item['fill_returned'] and _same_score(item['after'], item['proposed']))
        if item['readback'] == 'reloaded_mismatch' and item['fill_attempted']:
            _require(not _same_score(item['after'], item['proposed']))
        if item['readback'] in ('saved_panel_checked', 'reloaded_match') and not item['fill_attempted']:
            _require(_same_score(item['after'], item['before']) and item['after_flags'] == item['before_flags'])
    changes = [r for r in panel['rows'] if r['planned_action'] in ('fill', 'raise')]
    for index, item in enumerate(changes):
        _require(not item['fill_attempted'] or all(r['fill_returned'] for r in changes[:index]))
    _require(not panel['save_attempted'] or bool(changes)
             and all(r['fill_attempted'] and r['fill_returned'] for r in changes))
    _require(panel['save_attempted'] == (panel['code'] in
             ('read_back', 'readback_mismatch', 'readback_incomplete', 'save_unconfirmed')))
    if panel['readback_status'] == 'not_attempted':
        _require(all(r['readback'] in ('not_observed', 'saved_panel_checked') for r in panel['rows']))
    if panel['readback_status'] == 'incomplete':
        _require(all(r['readback'] == 'unavailable' for r in panel['rows']))
    if panel['readback_status'] == 'complete':
        _require(panel['save_confirmed'] and bool(panel['rows'])
                 and all(r['readback'] in ('reloaded_match', 'reloaded_mismatch') for r in panel['rows']))
    if panel['readback_status'] == 'incomplete':
        _require(panel['save_confirmed'])
    if panel['code'] == 'read_back':
        _require(panel['save_confirmed'] and panel['readback_status'] == 'complete'
                 and all(r['readback'] == 'reloaded_match' for r in panel['rows']))
    if panel['code'] == 'nothing_to_enter':
        _require(not panel['save_attempted'] and panel['readback_status'] == 'not_attempted'
                 and not changes
                 and all(not r['fill_attempted'] and r['readback'] == 'saved_panel_checked' for r in panel['rows']))
    if panel['code'] == 'readback_mismatch':
        _require(panel['readback_status'] == 'complete'
                 and any(r['readback'] == 'reloaded_mismatch' for r in panel['rows']))
    if panel['code'] == 'readback_incomplete':
        _require(panel['readback_status'] == 'incomplete')
    if panel['code'] == 'save_unconfirmed':
        _require(panel['readback_status'] == ('incomplete' if panel['save_confirmed'] else 'not_attempted'))
    if panel['code'] == 'ic_changed':
        _require(all(not r['fill_attempted'] and r['readback'] == 'not_observed' for r in panel['rows']))
    return panel


def event_id(payload, phase=None):
    """Fixed protocol identity: a changed payload conflicts, never gains a key."""
    return key('studentobservation', {'run': payload['run_id'], 'list': payload['list']['list_id'],
                                     'target': payload['target'], 'phase': phase or payload['phase']})


def _ledger_rows(payload, actor, created_at, ident):
    listed, target = payload['list'], payload['target']
    digest = content_hash(payload)
    stamp = instant(created_at).astimezone(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M')
    metadata = {k: deepcopy(v) for k, v in listed.items() if k != 'rows'}
    items = ([{'hub_row_index': r['row_index']} for r in payload['basis']['rows']
              if r['identity']['section'] == target['section']] if payload['observation'] is None
             else payload['observation']['rows'])
    # A stopped panel with zero readable rows is still one factual observation.
    items = items or [None]
    output = []
    for sequence, item in enumerate(items):
        index = item.get('hub_row_index') if item else None
        value = {'schema': ROW_SCHEMA, 'event_id': ident, 'sequence': sequence,
            'phase': payload['phase'], 'run_id': payload['run_id'], 'list_id': listed['list_id'],
            'scope': payload['scope'], 'revision': payload['revision'], 'target': target,
            'payload_sha256': digest, 'actor': actor, 'recorded_at': created_at,
            'list_context': metadata,
            'basis_row': payload['basis']['rows'][index] if index is not None else None,
            'hub_row': listed['rows'][index] if index is not None else None,
            'observation': item if payload['observation'] is not None else None,
            'panel': {k: v for k, v in payload['observation'].items() if k != 'rows'} if payload['observation'] is not None else None,
            'provenance': ('prepared_source_list' if payload['phase'] == 'prepared' else
                           'observation_reply_unavailable' if payload['observation'] is None else 'helper_panel_observation'),
            'source_freshness': 'verified_at_prepare_only', 'server_verified_ic': False}
        encoded = bounded_json(value)
        _require(len(encoded) <= 45000, 'student_observation_too_large')
        output.append([stamp, KIND, listed['class_name'], listed['week'], listed['assignment'], listed['form_id'], encoded])
    return output


def _ack(payload, ident, count):
    return {'recorded': True, 'event_id': ident, 'phase': payload['phase'], 'row_count': count,
            'payload_sha256': content_hash(payload), 'run_id': payload['run_id'],
            'list_id': payload['list']['list_id'], 'revision': payload['revision'],
            'target': deepcopy(payload['target'])}


def _state(value, ident):
    fields = {'schema', 'event_id', 'payload', 'payload_sha256', 'actor', 'created_at',
              'attempt_id', 'state', 'destination', 'ledger_rows'}
    _require(isinstance(value, dict) and set(value) in (fields, fields | {'confirmed_at'})
             and value['schema'] == STATE_SCHEMA and value['event_id'] == ident
             and value['state'] in ('append_started', 'recorded')
             and morning_entry._uuid(value['attempt_id']), 'student_observation_state_conflict')
    _common(value['payload'])
    if value['payload']['phase'] == 'prepared':
        _require(value['payload']['observation'] is None, 'student_observation_state_conflict')
    elif value['payload']['observation'] is not None:
        _observation(value['payload'], now=_time(value['created_at']))
    _require(event_id(value['payload']) == ident and content_hash(value['payload']) == value['payload_sha256']
             and _text(value['actor'], 200) and isinstance(value['destination'], dict)
             and set(value['destination']) == {'sheet', 'tab'}
             and all(_text(v, 300) for v in value['destination'].values())
             and _ledger_rows(value['payload'], value['actor'], value['created_at'], ident) == value['ledger_rows'],
             'student_observation_state_conflict')
    if value['state'] == 'recorded':
        _require('confirmed_at' in value, 'student_observation_state_conflict')
        _time(value['confirmed_at'])
    return value


def _history(token, destination):
    rows = google.sheet_values(token, destination['sheet'], f"{destination['tab']}!A1:G{ledger.MAX_ROWS + 1}")
    _require(isinstance(rows, list) and len(rows) <= ledger.MAX_ROWS, 'student_observation_history_full')
    return rows


def _readback(rows, value):
    found = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2 or row[1] != KIND:
            continue
        _require(len(row) == 7, 'student_observation_history_unverified')
        import json
        try:
            item = json.loads(row[6])
        except (ValueError, TypeError):
            raise Refused('student_observation_history_unverified') from None
        _require(isinstance(item, dict) and item.get('schema') == ROW_SCHEMA
                 and isinstance(item.get('event_id'), str), 'student_observation_history_unverified')
        if item['event_id'] == value['event_id']:
            found.append(row)
    _require(not found or (len(found) == len(value['ledger_rows'])
             and all(found.count(row) == 1 for row in value['ledger_rows'])),
             'student_observation_append_uncertain')
    return bool(found)


def _commit(port, changes, ident, expected):
    """One CAS attempt; a lost acknowledgement is checked at the exact key."""
    try:
        port.commit(changes)
    except (Conflict, StoreUnavailable):
        actual = port.read([ident]).documents[ident]
        _require(actual is not None and actual.value == expected, 'student_observation_append_uncertain')
    actual = port.read([ident]).documents[ident]
    _require(actual is not None and actual.value == expected, 'student_observation_append_uncertain')
    return actual


def record(token, write_token, config, payload, actor, *, current=None, port=None):
    """Optional private audit only; no grade ports, claim adoption, or IC retry."""
    _common(payload)
    _require(_text(actor, 200))
    port = port if port is not None else configured_store(config)
    ident = event_id(payload)
    snapshot = port.read([ident])
    old = snapshot.documents[ident]
    if old is not None:
        value = _state(old.value, ident)
        _require(value['payload'] == payload and value['actor'] == actor, 'student_observation_conflict')
    else:
        if payload['phase'] == 'prepared':
            validate_prepared(payload, current, now=instant(snapshot.read_time))
        else:
            if payload['observation'] is not None:
                _observation(payload, now=instant(snapshot.read_time))
            prepared_id = event_id(payload, 'prepared')
            prepared = port.read([prepared_id]).documents[prepared_id]
            _require(prepared is not None, 'student_observation_prepared_missing')
            prepared = _state(prepared.value, prepared_id)
            original = {**payload, 'phase': 'prepared', 'observation': None}
            _require(prepared['payload'] == original and prepared['actor'] == actor, 'student_observation_conflict')
        sheet = ledger.find(token, config)
        tab = ledger._first_tab(token, sheet['id']) if sheet else None
        _require(sheet is not None and tab is not None, 'student_observation_ledger_unavailable')
        value = {'schema': STATE_SCHEMA, 'event_id': ident, 'payload': deepcopy(payload),
            'payload_sha256': content_hash(payload), 'actor': actor, 'created_at': snapshot.read_time,
            'attempt_id': str(uuid4()), 'state': 'append_started',
            'destination': {'sheet': sheet['id'], 'tab': tab},
            'ledger_rows': _ledger_rows(payload, actor, snapshot.read_time, ident)}
        bounded_json(value)
        rows = _history(token, value['destination'])
        _require(not _readback(rows, value), 'student_observation_state_conflict')
        additions = ([] if rows else [ledger.HEADER]) + value['ledger_rows']
        _require(len(rows) + len(additions) <= ledger.MAX_ROWS, 'student_observation_history_full')
        old = _commit(port, {ident: (value, None)}, ident, value)
        # Only this invocation's nonce may dispatch. A concurrent/restarted
        # caller finding append_started takes the readback-only path below.
        try:
            google.append_rows(write_token, sheet['id'], tab + '!A1', additions)
        except google.GoogleError:
            pass  # The provider might have committed; never repeat append.
    value = _state(old.value, ident)
    _require(_readback(_history(token, value['destination']), value), 'student_observation_append_uncertain')
    if value['state'] != 'recorded':
        confirmed = {**value, 'state': 'recorded', 'confirmed_at': port.read([ident]).read_time}
        _commit(port, {ident: (confirmed, old.version)}, ident, confirmed)
    return _ack(payload, ident, len(value['ledger_rows']))
