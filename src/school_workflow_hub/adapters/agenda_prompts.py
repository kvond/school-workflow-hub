"""Read canonical teaching prompts from the existing Agenda, with direct links."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from .hosted_agenda import authenticated_reader, ConnectionError
from .dated_agenda import grid, value
from .google_sheets import SheetsReadError

TAB = 'Workflow Prompts'
ROWS = {'completion': (6, 'Completion — Prompt 1'), 'feedback': (7, 'Feedback — Prompt 2'),
        'summary': (8, 'Class summary — Prompt 3')}


def read_prompts(assertion, config=None):
    reader, source = authenticated_reader(assertion, config)
    try:
        data = reader.read(source, ("'Workflow Prompts'!A1:B10",))
    except SheetsReadError as error:
        raise ConnectionError("agenda_prompts_" + str(error)) from None
    cells = grid(data, TAB)
    if value(cells, 1, 1) != 'Workflow Prompts':
        raise ConnectionError('agenda_prompts_layout_changed')
    props = next(s['properties'] for s in data['sheets'] if s['properties']['title'] == TAB)
    if type(props.get('sheetId')) is not int:
        raise ConnectionError('agenda_prompts_layout_changed')
    prompts, links, hashes = {}, {}, {}
    for key, (row, label) in ROWS.items():
        text = value(cells, row, 2)
        if value(cells, row, 1) != label or not text.strip():
            raise ConnectionError('agenda_prompts_layout_changed')
        prompts[key] = text
        hashes[key] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        links[key] = f'https://docs.google.com/spreadsheets/d/{source}/edit#gid={props["sheetId"]}&range=B{row}'
    try:
        bundled = json.loads((Path(__file__).resolve().parents[3] / 'web/teacher-prompts.json').read_text())
    except (OSError, ValueError):
        raise ConnectionError('prompt_reference_unavailable') from None
    matches = {k: v == bundled['prompts'].get(k) for k, v in prompts.items()}
    return {'schema_version': 'hub-agenda-prompts/v1', 'source': 'Agenda · Workflow Prompts',
            'status': 'source_read', 'prompts': prompts, 'links': links, 'sha256': hashes,
            'version_label': value(cells, 3, 2), 'change_rule': value(cells, 2, 2),
            'bundled_matches': matches, 'alignment_review_needed': not all(matches.values()),
            'checked_at': datetime.now(timezone.utc).isoformat(), 'external_writes': 0}
