"""Private readback details and attributed manual verification, append-only."""
import json
import uuid
from datetime import datetime, timezone
from . import google, ledger

KIND = 'Morning recovery'


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('recovery')
    common = {'event_id', 'run_id', 'type'}
    fields = {'note'} if value.get('type') == 'verified' else {'assignment', 'section', 'column', 'code', 'names'}
    if set(value) != common | fields or value.get('type') not in ('verified', 'readback'):
        raise ValueError('recovery')
    for key in ('event_id', 'run_id'):
        if not isinstance(value[key], str) or str(uuid.UUID(value[key])) != value[key]:
            raise ValueError('recovery')
    def text(s, limit):
        return isinstance(s, str) and 0 < len(s.strip()) <= limit and not any(ord(c) < 32 for c in s)
    if value['type'] == 'verified':
        if not text(value['note'], 500):
            raise ValueError('recovery')
    else:
        if (not text(value['assignment'], 200) or not text(value['section'], 100)
                or value['column'] not in ('notes', 'revision')
                or value['code'] not in ('readback_incomplete', 'readback_mismatch', 'save_unconfirmed')
                or not isinstance(value['names'], list) or len(value['names']) > 300
                or not all(text(name, 200) for name in value['names'])):
            raise ValueError('recovery')
    return value


def records(token, config):
    sheet = ledger.find(token, config)
    if not sheet:
        return []
    tab = ledger._first_tab(token, sheet['id'])
    rows = google.sheet_values(token, sheet['id'], f'{tab}!A1:G{ledger.MAX_ROWS}') if tab else []
    result = []
    for row in rows:
        if len(row) < 7 or row[1] != KIND:
            continue
        try:
            item = json.loads(row[6])
            validate({k: v for k, v in item.items() if k not in ('by', 'at')})
            if not isinstance(item.get('by'), str) or not isinstance(item.get('at'), str):
                continue
            result.append(item)
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return result[-100:][::-1]


def record(token, write_token, config, payload, actor):
    value = validate(payload)
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("verified actor required")
    for old in records(token, config):
        if old['event_id'] == value['event_id']:
            if {k: v for k, v in old.items() if k not in ('by', 'at')} != value or old['by'] != actor:
                raise ValueError('recovery identity conflict')
            return old
    sheet = ledger.find(token, config)
    if not sheet:
        raise google.GoogleError('The Scores entered sheet is unavailable.')
    tab = ledger._first_tab(token, sheet['id'])
    if not tab:
        raise google.GoogleError('The Scores entered sheet has no tab.')
    item = {**value, 'by': actor, 'at': datetime.now(timezone.utc).isoformat()}
    encoded = json.dumps(item, separators=(',', ':'))
    if len(encoded) > 45000:
        raise ValueError('recovery too large')
    google.append_rows(write_token, sheet['id'], tab + '!A1', [[ledger.eastern_now(), KIND, '', '',
        'Manual verification' if value['type'] == 'verified' else value['assignment'], value['run_id'], encoded]])
    if item not in records(token, config):
        raise google.GoogleError('The recovery record could not be read back.')
    return item
