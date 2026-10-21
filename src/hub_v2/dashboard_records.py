"""Read-only dashboard links and bounded, existing operational run records.

Opening this view does not load lessons, assess work, build a packet, or write.
Configured destinations establish links, never execution or completion.
"""
from datetime import datetime
import re
from urllib.parse import quote, urlsplit

from . import google, morning

SCHEMA = 'hub-dashboard-records/v1'
ID = r'[A-Za-z0-9_-]{10,200}'
MAX_ROWS, MAX_COLUMNS, HEADER_ROWS, DISPLAY_RUNS = 500, 30, 20, 20
MAX_GRID_ROWS = 2000
STATUSES = {'running', 'completed', 'partial', 'blocked', 'failed', 'uncertain', 'cancelled'}
LINKS = (
    ('work_index', 'Student work index', 'HUB_STUDENT_WORK_INDEX_URL', 'sheet'),
    ('feedback_checklist', 'Feedback checklist', 'HUB_FEEDBACK_CHECKLIST_URL', 'sheet'),
    ('growth_report', 'Biology growth and DTGSS', 'HUB_GROWTH_REPORT_URL', 'sheet'),
    ('biology_analysis', 'Biology analysis', 'HUB_BIOLOGY_ANALYSIS_URL', 'sheet'),
    ('system_notes', 'System notes in Slack', 'HUB_SYSTEM_NOTES_SLACK_URL', 'slack'),
    ('operations', 'System overview and run history', 'HUB_OPERATIONS_URL', 'sheet'),
    ('model_trials', 'Models, rubrics and trials', 'HUB_MODEL_TRIALS_URL', 'sheet'),
    ('model_trials_folder', 'Model and rubric files', 'HUB_MODEL_TRIALS_FOLDER_URL', 'folder'),
)


def _url(value, kind):
    if not isinstance(value, str) or len(value) > 1000:
        return None
    patterns = {
        'sheet': r'https://docs\.google\.com/spreadsheets/d/' + ID + r'/edit(?:\?gid=\d{1,20})?(?:#gid=\d{1,20})?',
        'folder': r'https://drive\.google\.com/drive/(?:u/[0-9]+/)?folders/' + ID + r'/?',
        'slack': r'https://app\.slack\.com/client/T[A-Z0-9]+/C[A-Z0-9]+',
        'slack_thread': r'https://[a-z0-9][a-z0-9-]{0,62}\.slack\.com/archives/C[A-Z0-9]+/p\d{16}',
        'document': r'https://docs\.google\.com/(?:document|presentation)/d/' + ID + r'/edit',
        'file': r'https://drive\.google\.com/file/d/' + ID + r'/view',
        'issue': r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:issues|pull)/\d+',
    }
    return value if re.fullmatch(patterns[kind], value) else None


def _evidence_url(value):
    for kind in ('sheet', 'folder', 'slack', 'slack_thread', 'document', 'file', 'issue'):
        found = _url(value, kind)
        if found:
            return found
    return None


def _text(value, limit=500):
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(c) < 32 for c in value)
            or re.search(r'(?i)(?:token|secret|password|api[_ -]?key)\s*[:=]', value)):
        raise ValueError('invalid_run_field')
    return value.strip()


def _timestamp(value):
    if not value:
        return None
    value = _text(value, 60)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('timestamp_timezone_required')
    return parsed.isoformat()


def _count(value):
    if value in ('', None):
        return None
    if isinstance(value, str) and re.fullmatch(r'\d{1,6}', value):
        return int(value)
    # A prose description of completed or unfinished scope is not a count.
    return None


def _recorded_timestamp(value):
    # A recorded/observed time is distinct from execution start or finish.
    # Older prose/local-time cells do not establish an ISO timestamp.
    try:
        return _timestamp(value)
    except (ValueError, TypeError):
        return None


def _header(value):
    return re.sub(r'[^a-z0-9]+', '_', str(value).strip().lower()).strip('_')


ALIASES = {
    'id': {'run_id', 'attempt_id'},
    'work': {'work', 'purpose', 'work_purpose', 'work_bucket', 'workflow'},
    'status': {'status', 'outcome', 'actual_outcome'},
    'started_at': {'start', 'started', 'started_at', 'start_time'},
    'finished_at': {'end', 'finished', 'finished_at', 'end_time'},
    'summary': {'did', 'actual_result', 'result', 'summary', 'what_completed'},
    'completed': {'completed', 'completed_count'},
    'remaining': {'remaining', 'remaining_count', 'unfinished', 'unfinished_scope'},
    'unfinished': {'unfinished', 'unfinished_scope', 'remaining_scope'},
    'next_action': {'next', 'next_action'},
    'owner': {'owner', 'next_owner'},
    'recorded_at': {'recorded_at', 'recorded_et', 'recorded_date_time_et', 'recorded_time'},
    'output': {'results', 'results_link', 'output_link', 'output_links'},
    'details': {'details', 'details_link', 'evidence_link', 'evidence_details_link'},
}


def _operations(token, url):
    sheet_id = urlsplit(url).path.split('/')[3]
    metadata = google.get_json('https://sheets.googleapis.com/v4/spreadsheets/' + quote(sheet_id), token,
        {'fields': 'sheets(properties(title,gridProperties(rowCount,columnCount)))'})
    sheets = metadata.get('sheets')
    if not isinstance(sheets, list) or len(sheets) > 100:
        raise ValueError('run_tab_unavailable')
    matches = [item.get('properties', {}) for item in sheets if isinstance(item, dict)
               and _header(item.get('properties', {}).get('title', '')) in {'run_history', 'runs'}]
    if len(matches) != 1:
        raise ValueError('run_tab_unavailable')
    properties = matches[0]
    height, width = (properties.get('gridProperties', {}).get(name) for name in ('rowCount', 'columnCount'))
    if (type(height) is not int or not 1 <= height <= MAX_GRID_ROWS
            or type(width) is not int or not 1 <= width <= MAX_COLUMNS):
        raise ValueError('run_grid_unavailable')
    column = chr(64 + width) if width <= 26 else 'A' + chr(64 + width - 26)
    tab = "'%s'" % properties['title'].replace("'", "''")
    # Read the entire bounded grid, including sparse rows: a blank sentinel
    # cannot establish that a later run does not exist below a partial window.
    rows = google.sheet_values(token, sheet_id, tab + '!A1:' + column + str(height))
    if (not isinstance(rows, list) or len(rows) > height
            or any(not isinstance(row, list) or len(row) > MAX_COLUMNS for row in rows)
            or sum(len(str(cell)) for row in rows for cell in row) > 200_000):
        raise ValueError('run_read_incomplete')
    candidates = []
    for index, row in enumerate(rows[:HEADER_ROWS]):
        headers = [_header(cell) for cell in row]
        mapping = {}
        for name, aliases in ALIASES.items():
            positions = [n for n, value in enumerate(headers) if value in aliases]
            if len(positions) > 1:
                raise ValueError('ambiguous_run_header')
            if positions:
                mapping[name] = positions[0]
        if {'id', 'work', 'status'} <= set(mapping):
            candidates.append((index, mapping))
    if len(candidates) != 1:
        raise ValueError('run_header_unavailable')
    header, mapping = candidates[0]
    result, seen = [], set()
    for row in rows[header + 1:]:
        if not any(str(cell).strip() for cell in row):
            continue
        if len(result) >= MAX_ROWS:
            raise ValueError('run_read_incomplete')
        def cell(name):
            index = mapping.get(name, len(row))
            return row[index] if index < len(row) else ''
        ident, work = _text(cell('id'), 120), _text(cell('work'), 160)
        status = _text(cell('status'), 30).lower()
        if not ident or not work or ident in seen or status not in STATUSES:
            raise ValueError('invalid_run_record')
        seen.add(ident)
        links = []
        for name, label in (('output', 'Results'), ('details', 'Evidence')):
            link = _evidence_url(cell(name))
            if link:
                links.append({'label': label, 'url': link})
        result.append({'id': ident, 'work': work, 'status': status,
                       'started_at': _timestamp(cell('started_at')),
                       'finished_at': _timestamp(cell('finished_at')),
                       'summary': _text(cell('summary')),
                       'completed': _count(cell('completed')),
                       'remaining': _count(cell('remaining')),
                       'unfinished': _text(cell('unfinished'), 1000),
                       'next_action': _text(cell('next_action'), 500),
                       'owner': _text(cell('owner'), 120),
                       'recorded_at': _recorded_timestamp(cell('recorded_at')), 'links': links})
    return result[-DISPLAY_RUNS:][::-1]


def _helper(token, config):
    reports = morning.reports(token, config)
    if not isinstance(reports, list) or len(reports) > 10:
        raise ValueError('invalid_helper_receipts')
    result = []
    uncertain = {'readback_incomplete', 'save_unconfirmed', 'readback_mismatch', 'uncertain_blocked'}
    for report in reports:
        morning.validate_report(report)
        items = report['items']
        codes = {item['code'] for item in items} | {report['code']}
        if codes & uncertain:
            status = 'uncertain'
        elif report['code'] == 'running':
            status = 'running'
        elif report['code'] == 'finished':
            status = 'completed' if codes <= {'finished', 'read_back', 'nothing_to_enter', 'excluded_week'} else 'partial'
        else:
            status = 'blocked'
        completed = sum(item['code'] in {'read_back', 'nothing_to_enter'} for item in items)
        remaining = len(items) - completed - sum(item['code'] == 'excluded_week' for item in items)
        result.append({'id': report['run_id'], 'work': 'Helper score entry', 'status': status,
                       # Receipts retain the day, not exact start/finish timestamps.
                       'started_at': None, 'finished_at': None,
                       'summary': 'Saved helper receipt for ' + report['day'] + ': ' + report['code'].replace('_', ' ') + '.',
                       'completed': completed if items else None,
                       'remaining': remaining if items else None,
                       'unfinished': '', 'next_action': '', 'owner': '', 'recorded_at': None, 'links': []})
    return result


def records(config):
    agenda_id = config.get('HUB_AGENDA_SPREADSHEET_ID', '')
    agenda = ('https://docs.google.com/spreadsheets/d/' + agenda_id + '/edit'
              if isinstance(agenda_id, str) and re.fullmatch(ID, agenda_id) else None)
    destinations = [{'key': 'agenda', 'label': 'Agenda and lessons', 'url': agenda, 'configured': bool(agenda)}]
    for name, label, setting, kind in LINKS:
        url = _url(config.get(setting), kind)
        destinations.append({'key': name, 'label': label, 'url': url, 'configured': bool(url)})
    sources = {'operations': 'not_configured', 'helper': 'not_configured'}
    source_items = {'operations': [], 'helper': []}
    operations = next(item['url'] for item in destinations if item['key'] == 'operations')
    ledger_id = config.get('HUB_SCORES_ENTERED_SPREADSHEET_ID', '')
    helper = isinstance(ledger_id, str) and bool(re.fullmatch(ID, ledger_id))
    if operations or helper:
        try:
            token = google.service_token(config)
        except Exception:
            for name, enabled in (('operations', operations), ('helper', helper)):
                if enabled:
                    sources[name] = 'unavailable'
        else:
            for name, enabled, read in (
                    ('operations', operations, lambda: _operations(token, operations)),
                    ('helper', helper, lambda: _helper(token, config))):
                if not enabled:
                    continue
                try:
                    source_items[name] = read()
                    sources[name] = 'available'
                except Exception:
                    sources[name] = 'unavailable'
    state = ('unavailable' if 'unavailable' in sources.values() else
             'available' if 'available' in sources.values() else 'not_configured')
    # Keep both sources represented when a populated operations sheet has more
    # rows than the compact view. Each source's existing order is retained.
    items = source_items['helper'][:10] + source_items['operations'][:10]
    return {'schema_version': SCHEMA, 'records': destinations,
            'runs': {'status': state, 'items': items[:DISPLAY_RUNS], 'sources': sources}}
