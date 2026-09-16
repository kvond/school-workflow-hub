"""Teacher-directed weekly plan/note saves with fresh comparison and readback.

Sheet coordinates are resolved on the server on every request. Direct edits in
Sheets cannot participate in the Firestore lock: avoid editing the same cell in
Sheets while a dashboard save is in progress. No formula cells are overwritten.
"""
from datetime import date
import re
import uuid

from .hosted_agenda import authenticated_reader, service_token, ConnectionError
from .dated_agenda import read_day, grid, COURSES
from .firestore_state import configured_store
from ..core.cloud_state import CloudState, Conflict
from ..core.runs import content_hash

WRITE_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'


def batch(source, token, requests):
    import requests as http
    try:
        with http.Session() as session:
            session.trust_env = False
            response = session.post('https://sheets.googleapis.com/v4/spreadsheets/' + source + ':batchUpdate',
                headers={'Authorization': 'Bearer ' + token}, json={'requests': requests},
                timeout=(5, 20), allow_redirects=False)
            if response.status_code != 200:
                raise ConnectionError('agenda_editor_access_required')
            return response.json()
    except ConnectionError:
        raise
    except Exception:
        raise ConnectionError('agenda_save_response_uncertain') from None


def target(assertion, day, course, config):
    if course not in dict(COURSES):
        raise ValueError('invalid_course')
    result = read_day(assertion, day, config)
    lesson = next(l for l in result['lessons'] if l['class_key'] == course)
    if lesson['origin'] != 'Weekly Agenda':
        raise ConnectionError('agenda_override_needs_review')
    title = dict(COURSES)[course]
    match = re.fullmatch(re.escape(title) + r'!A(\d+):R\1', lesson['source_reference'])
    if not match:
        raise ConnectionError('agenda_layout_changed')
    row = int(match[1])
    days = (1, 3, 4) if course == 'anatomy' else (0, 2, 4)
    weekday = date.fromisoformat(day).weekday()
    if weekday not in days:
        raise ConnectionError('agenda_day_not_writable')
    col = 4 + days.index(weekday) * 2
    reader, source = authenticated_reader(assertion, config)
    data = reader.read(source, (f"'{title}'!A{row}:R{row}",))
    sheet = next(s for s in data['sheets'] if s['properties']['title'] == title)
    cells = grid(data, title)
    columns = {'title': 3, 'plan': col, 'notes': col + 1}
    values = {}
    for name, c in columns.items():
        cell = cells.get((row, c), {})
        if cell.get('userEnteredValue', {}).get('formulaValue'):
            raise ConnectionError('agenda_formula_not_writable')
        values[name] = cell.get('formattedValue', '')
        if values[name] != lesson[name]:
            raise Conflict('agenda_changed_reload')
    identity = {'source': source, 'sheet': sheet['properties']['sheetId'], 'row': row,
                'columns': columns, 'week': result['week'], 'date': day}
    return identity, values, content_hash({'target': identity, 'values': values})


def capability(assertion, day, course, config):
    identity, values, revision = target(assertion, day, course, config)
    # Empty batch verifies edit authorization without changing lesson cells.
    token = service_token(config['HUB_READER_SERVICE_ACCOUNT'], WRITE_SCOPE)
    batch(identity['source'], token, [])
    CloudState(configured_store(config)).read_task('task_' + '0' * 64)
    return {'schema_version': 'hub-agenda-save/v1', 'save_enabled': True,
            'revision': revision, 'values': values, 'date': day, 'class_key': course}


def save(assertion, payload, config):
    if (not isinstance(payload, dict) or set(payload) != {'date', 'class_key', 'revision', 'values', 'request_id'}
            or not isinstance(payload['values'], dict) or set(payload['values']) != {'title', 'plan', 'notes'}
            or any(not isinstance(v, str) or len(v) > 20000 for v in payload['values'].values())
            or not isinstance(payload['request_id'], str)
            or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', payload['request_id'])
            or not isinstance(payload['revision'], str) or not re.fullmatch(r'[a-f0-9]{64}', payload['revision'])):
        raise ValueError('invalid_agenda_save')
    day, course, desired = payload['date'], payload['class_key'], payload['values']
    identity, _, _ = target(assertion, day, course, config)
    token = service_token(config['HUB_READER_SERVICE_ACCOUNT'], WRITE_SCOPE)
    store = CloudState(configured_store(config))
    task = store.create_task({'workflow': 'agenda-save', 'class_key': content_hash(identity['source']),
                              'assignment_key': 'workbook'})
    claim = store.claim(task, owner='save-' + uuid.uuid4().hex, request_id=payload['request_id'],
                        input_hash=content_hash(payload), ttl_seconds=900)
    current_identity, current, revision = target(assertion, day, course, config)
    if current_identity != identity:
        raise Conflict('agenda_changed_reload')
    if claim.completed:
        if current != desired:
            raise Conflict('saved_then_changed_reload')
        return {'status': 'saved', 'revision': revision, 'values': current, 'readback_verified': True}
    if revision != payload['revision'] and current != desired:
        store.release_for_review(claim, 'agenda_changed_reload')
        raise Conflict('agenda_changed_reload')
    changes = []
    for name, column in identity['columns'].items():
        if current[name] != desired[name]:
            changes.append({'updateCells': {'start': {'sheetId': identity['sheet'],
                'rowIndex': identity['row'] - 1, 'columnIndex': column - 1},
                'rows': [{'values': [{'userEnteredValue': {'stringValue': desired[name]}}]}],
                'fields': 'userEnteredValue'}})
    if changes:
        store.renew(claim, 900)
        batch(identity['source'], token, changes)
    after_identity, after, revision = target(assertion, day, course, config)
    if after_identity != identity or after != desired:
        raise ConnectionError('agenda_readback_failed')
    store.finish(claim, records={'receipt': {'revision': revision, 'readback_verified': True}})
    return {'status': 'saved', 'revision': revision, 'values': after, 'readback_verified': True}
