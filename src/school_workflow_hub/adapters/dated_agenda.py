"""Bounded, read-only dated view of the existing weekly Agenda.

Coordinates are provenance, never save targets or stable assignment identities.
No raw source data is persisted. The current Dashboard's manual overrides win.
"""
from datetime import date, datetime, timedelta, timezone
import json
import os
import re
from urllib.parse import parse_qs, urlsplit
from .agenda_rows import cell_links
from .hosted_agenda import authenticated_reader, ConnectionError

COURSES = (("biology", "🌿 Biology"), ("forensics", "🔬 Forensics"), ("anatomy", "🫀 A&P"))
DECK_URL = re.compile(r"https://docs\.google\.com/presentation/d/([A-Za-z0-9_-]{20,128})/(?:copy|edit|view|preview)(?:[?#].*)?")
MAX_ASSIGNMENT_SOURCE_LINKS = 32


def grid(data, title):
    sheets = [s for s in data['sheets'] if s['properties']['title'] == title]
    if len(sheets) != 1:
        raise ConnectionError('agenda_layout_changed')
    cells = {}
    for block in sheets[0].get('data', []):
        for r, row in enumerate(block.get('rowData', []), block.get('startRow', 0) + 1):
            for c, cell in enumerate(row.get('values', []), block.get('startColumn', 0) + 1):
                if (r, c) in cells:
                    raise ConnectionError('agenda_layout_changed')
                cells[r, c] = cell
    return cells


def value(cells, row, col):
    text = cells.get((row, col), {}).get('formattedValue', '')
    if not isinstance(text, str) or len(text) > 20000:
        raise ConnectionError('agenda_layout_changed')
    return text


def link(raw):
    try:
        url = urlsplit(raw)
        if url.scheme == 'https' and url.hostname and not url.username and not url.password and not re.search(r'[\s\x00-\x1f]', raw):
            return raw
    except ValueError:
        pass
    return None


def turnin_form(raw):
    """Validated Google Form link, prefilled values, and editable/API Form ID.

    Assignment-specific Forms use /forms/d/<form_id>/viewform and need no
    synthetic assignment question. The older shared router uses /forms/d/e/...
    plus a prefilled assignment value; that remains supported during migration.
    """
    target = link(raw)
    if not target:
        return None, (), None
    try:
        url = urlsplit(target)
        match = re.fullmatch(
            r'/forms/d/(?:(e)/)?([A-Za-z0-9_-]{20,256})/(?:viewform|edit)', url.path)
        if url.hostname != 'docs.google.com' or not match:
            return None, (), None
        query = parse_qs(url.query, max_num_fields=20, keep_blank_values=False)
        values = []
        for key, items in query.items():
            if not re.fullmatch(r'entry\.[0-9]+', key):
                continue
            for item in items:
                if isinstance(item, str) and 0 < len(item) <= 200 and item not in values:
                    values.append(item)
        api_id = None if match.group(1) == 'e' else match.group(2)
        return target, tuple(values), api_id
    except (ValueError, TypeError):
        return None, (), None


def form_cell_links(cell):
    """Read every observed Form destination without choosing by its label."""
    try:
        links = cell_links(cell)
        forms = [turnin_form(url)[0] for url in links]
        if not all(forms):
            return [], 'assignment_form_turn_in_link_invalid'
        return forms, ''
    except (ValueError, TypeError, AttributeError):
        return [], 'assignment_form_turn_in_link_invalid'


def assignment_source_links(cells, row, deck_bindings):
    """P is one primary deck; O may name several explicitly bound decks.

    Repeated URLs for the same source are one lesson. An ordinary unbound O
    resource remains a resource. A multi-link O cell with unknown or invalid
    destinations is incomplete evidence, never permission to infer assignments.
    """
    sources, issues, seen = [], [], set()
    for column in (16, 15):
        try:
            cell = cells.get((row, column), {})
            try:
                urls = cell_links(cell)
            except ValueError as error:
                if str(error) != 'computed_agenda_link_requires_review':
                    raise
                # The live P/O formulas derive their displayed URLs from Q/S.
                # Read every observed effective destination, never evaluate the
                # formula or treat its unevaluated branches as source evidence.
                effective = dict(cell)
                effective['userEnteredValue'] = {key: val for key, val in cell.get('userEnteredValue', {}).items()
                                                  if key != 'formulaValue'}
                urls = cell_links(effective)
        except (ValueError, TypeError, AttributeError):
            issues.append(column)
            continue
        if len(urls) > MAX_ASSIGNMENT_SOURCE_LINKS:
            issues.append(column)
            continue
        matches = [(raw, DECK_URL.fullmatch(raw)) for raw in urls]
        ids = {match[1] for _, match in matches if match}
        if column == 16 and urls and (len(ids) != 1 or any(not match for _, match in matches)):
            issues.append(column)
            continue
        if column == 15 and len(urls) > 1 and any(
                not match or match[1] not in deck_bindings for _, match in matches):
            issues.append(column)
        for raw, match in matches:
            if not match or (column == 15 and match[1] not in deck_bindings):
                continue
            if match[1] not in seen:
                sources.append({'column': column, 'source_url': raw, 'source_deck_id': match[1]})
                seen.add(match[1])
    return sources, issues


def _form_link_label(cell, target):
    """Rich-text labels are display only; Sheets offsets use UTF-16 units."""
    text = cell.get('formattedValue', '')
    encoded = text.encode('utf-16-le')
    runs = cell.get('textFormatRuns', [])
    pieces = []
    try:
        offsets = [run.get('startIndex', 0) for run in runs] + [len(encoded) // 2]
        if any(type(n) is not int or n < 0 for n in offsets) or offsets != sorted(offsets):
            return ''
        for index, run in enumerate(runs):
            uri = run.get('format', {}).get('link', {}).get('uri', '')
            if turnin_form(uri)[2] == target:
                pieces.append(encoded[offsets[index] * 2:offsets[index + 1] * 2].decode('utf-16-le'))
        return ' '.join(' '.join(pieces).split())[:300]
    except (ValueError, TypeError, AttributeError, UnicodeError):
        return ''


def explicit_assignment_forms(cells, row, title, entries, deck_bindings, form_bindings):
    """One weekly row may expose separate, explicitly bound assignment Forms.

    None means the ordinary single-link rules still apply. Multiple named
    links never bind by text or order. All current P/O decks need exact private
    bindings, T must expose precisely their distinct direct-ID Forms, and U
    remains the primary P Form. Published IDs cannot stand in for API IDs.
    """
    visible, visible_error = form_cell_links(cells.get((row, 20), {}))
    sources, source_issues = assignment_source_links(cells, row, deck_bindings)
    keys = [entry['assignment_key'] for entry in entries]
    if (not source_issues and len(visible) <= 1
            and not (len(set(keys)) > 1 and all(key in form_bindings for key in keys))):
        return None
    hidden, hidden_error = form_cell_links(cells.get((row, 21), {}))
    result = {'forms': [], 'visible_urls': visible, 'raw_url': hidden[0] if len(hidden) == 1 else '',
              'error': ''}
    expected = [form_bindings.get(key) for key in keys]
    primary = next((source['source_deck_id'] for source in sources if source['column'] == 16), None)
    if source_issues:
        result['error'] = 'assignment_form_source_links_need_review'
    elif visible_error or hidden_error:
        result['error'] = visible_error or hidden_error
    elif cells.get((row, 20), {}).get('userEnteredValue', {}).get('formulaValue'):
        result['error'] = 'assignment_form_turn_in_link_invalid'
    elif len(set(keys)) < 2:
        result['error'] = 'assignment_form_turn_in_links_disagree'
    elif (re.sub(r'[^a-z ]', '', value(cells, 3, 20).lower()).strip() != 'turn it in'
          or value(cells, 3, 21) != '_turnInURL'):
        result['error'] = 'assignment_form_layout_changed'
    elif (len(keys) != len(set(keys)) or not primary
          or [entry['source_deck_id'] for entry in entries] != [source['source_deck_id'] for source in sources]
          or entries[0]['source_deck_id'] != primary or any(
            deck_bindings.get(entry['source_deck_id']) != entry['assignment_key'] for entry in entries)
          or any(not isinstance(fid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', fid)
                 for fid in expected)):
        result['error'] = 'assignment_form_mapping_required'
    elif len(set(expected)) != len(expected) or any(
            sum(value == fid for value in form_bindings.values()) != 1 for fid in expected):
        result['error'] = 'assignment_form_reused'
    elif any(not turnin_form(url)[2] for url in visible + hidden):
        result['error'] = 'assignment_form_api_id_required'
    elif any(not urlsplit(url).path.endswith('/viewform') for url in visible + hidden):
        result['error'] = 'assignment_form_turn_in_link_invalid'
    elif (len(visible) != len(expected) or {turnin_form(url)[2] for url in visible} != set(expected)
          or len(hidden) != 1 or turnin_form(hidden[0])[2] != expected[0]):
        result['error'] = 'assignment_form_turn_in_links_disagree'
    else:
        by_id = {turnin_form(url)[2]: url for url in visible}
        for index, entry in enumerate(entries):
            fid = expected[index]
            result['forms'].append({'assignment_key': entry['assignment_key'],
                'assignment_title': _form_link_label(cells.get((row, 20), {}), fid)
                                    or entry.get('assignment_title', 'Submit assignment'),
                'url': by_id[fid], 'form_id': fid, 'primary': index == 0,
                'source_reference': f'{title}!T{row}',
                'source_references': [f'{title}!T{row}'] + ([f'{title}!U{row}'] if index == 0 else [])})
    return result


def _day_assignment_forms(cells, row, title, course, config):
    """Display the same explicit bindings without loading the grading registry."""
    try:
        mappings = []
        for name in ('HUB_INTAKE_DECK_ASSIGNMENTS', 'HUB_ASSIGNMENT_FORM_IDS'):
            raw = config.get(name, {})
            mappings.append(json.loads(raw) if isinstance(raw, str) else raw)
        decks, forms = mappings
        if not all(isinstance(mapping, dict) for mapping in mappings):
            raise ValueError()
        entries = []
        sources, _ = assignment_source_links(cells, row, decks)
        for source in sources:
            key = decks.get(source['source_deck_id'])
            if isinstance(key, str) and re.fullmatch(re.escape(course) + r':[A-Za-z0-9:_-]{1,128}', key):
                entries.append({'assignment_key': key, 'source_deck_id': source['source_deck_id']})
        return explicit_assignment_forms(cells, row, title, entries, decks, forms)
    except (ValueError, TypeError, AttributeError):
        return {'forms': [], 'error': 'assignment_form_mapping_invalid'}


def source_date(text):
    try:
        return datetime.strptime(text, '%m/%d/%Y').date()
    except ValueError:
        raise ConnectionError('agenda_layout_changed') from None


def read_day(assertion, selected, config=None):
    try:
        day = date.fromisoformat(selected)
        if day.isoformat() != selected:
            raise ValueError()
    except (ValueError, TypeError):
        raise ConnectionError('invalid_date') from None
    config = os.environ if config is None else config
    reader, source = authenticated_reader(assertion, config)
    metadata = reader.read(source)
    titles = {s['properties']['title'] for s in metadata['sheets']}
    if not {'🗂 Weeks', '📅 Dashboard', *(title for _, title in COURSES)} <= titles:
        raise ConnectionError('agenda_layout_changed')
    mapping = reader.read(source, ("'🗂 Weeks'!A2:G43", "'📅 Dashboard'!A6:F9"))
    weeks, dashboard = grid(mapping, '🗂 Weeks'), grid(mapping, '📅 Dashboard')
    if [value(weeks, 2, c) for c in (2,3,4)] != ['course row','week','Monday']:
        raise ConnectionError('agenda_layout_changed')
    matches = []
    for r in range(3,44):
        if not value(weeks, r, 4):
            continue
        start = source_date(value(weeks, r, 4))
        if start <= day < start + timedelta(days=7):
            matches.append(r)
    if len(matches) != 1:
        raise ConnectionError('date_not_mapped')
    wr = matches[0]
    try:
        row = int(value(weeks, wr, 2))
        if not 4 <= row <= 60:
            raise ValueError()
    except ValueError:
        raise ConnectionError('agenda_layout_changed') from None
    week = value(weeks, wr, 3)
    current = value(weeks, wr, 4) == value(weeks, 5, 7)
    ranges = tuple("'%s'!A3:U%d" % (title, row) for _, title in COURSES)
    if '🗂 TurnIn Map' in titles:
        ranges += ("'🗂 TurnIn Map'!A1:D160",)
    data = reader.read(source, ranges)
    turnins = grid(data, '🗂 TurnIn Map') if '🗂 TurnIn Map' in titles else {}
    turnin_headers = [value(turnins,1,c) for c in (1,2,3,4)] == ['assignment key','target folder id','course','week']
    lessons = []
    weekday = ('Mon','Tue','Wed','Thu','Fri','Sat','Sun')[day.weekday()]
    for key, title in COURSES:
        cells = grid(data, title)
        if value(cells, row, 1) != week or [value(cells,3,c) for c in (3,16,17,18,21)] != ['Topic','_studentURL','_teacherURL','_folderURL','_turnInURL']:
            raise ConnectionError('agenda_layout_changed')
        expected_days = ('Tue', 'Thu', 'Fri') if key == 'anatomy' else ('Mon', 'Wed', 'Fri')
        if [value(cells,3,c) for c in (4,6,8)] != [d + ' — plan' for d in expected_days]:
            raise ConnectionError('agenda_layout_changed')
        cols = [c for c in (4,6,8) if value(cells,3,c) == weekday + ' — plan']
        if len(cols) > 1:
            raise ConnectionError('agenda_layout_changed')
        col = cols[0] if cols else None
        plan, notes = (value(cells,row,col),value(cells,row,col+1)) if col else ('','')
        origin = 'Weekly Agenda' if col else 'No weekly day column'
        extra = ''
        if current and day.weekday() < 5:
            drs = [r for r in (7,8,9) if value(dashboard,r,1) == title]
            if len(drs) != 1 or value(dashboard,6,day.weekday()+2).split() != [weekday, day.strftime('%m/%d')]:
                raise ConnectionError('agenda_layout_changed')
            dc = dashboard.get((drs[0],day.weekday()+2),{})
            # Formula display is retained alongside the separated weekly fields;
            # literal Dashboard text is an explicit override for this date.
            shown = dc.get('formattedValue','')
            if not dc.get('userEnteredValue',{}).get('formulaValue'):
                if shown and shown != '—':
                    plan, origin = shown, 'Dashboard entry'
                elif not col:
                    origin = 'No entry on Dashboard for this date'
            else:
                extra = shown
                expected = plan + ('\n📝 ' + notes if notes else '')
                if shown.strip() != expected.strip():
                    plan, origin = shown, 'Dashboard display differs from weekly fields'
        teacher = link(value(cells,row,17))
        if teacher and not re.match(r'^https://docs\.google\.com/presentation/d/[A-Za-z0-9_-]+/edit(?:[?#]|$)', teacher):
            teacher = None
        resources = []
        for c,label in ((16,'Student Slides'),(18,'Lesson folder'),(15,'Additional lesson material')):
            cell = cells.get((row,c),{})
            target = link(cell.get('hyperlink','')) or link(value(cells,row,c))
            if target:
                resources.append({'label':label,'url':target})
        targets = [r for r in range(2,161) if turnin_headers and value(turnins,r,3) == title and value(turnins,r,4) == week]
        turnin_folder, assignment_label = None, None
        if len(targets) == 1:
            folder_id = value(turnins,targets[0],2)
            if re.fullmatch(r'[A-Za-z0-9_-]{20,128}',folder_id):
                turnin_folder = 'https://drive.google.com/drive/folders/' + folder_id
            assignment_label = value(turnins,targets[0],1) or None
        form_url, prefilled, form_id = turnin_form(value(cells,row,21))
        # Preferred model: one assignment row points at its own editable/API
        # Form ID, so the TurnIn Map supplies the stable assignment label and
        # every response in that Form belongs to that assignment. The older
        # shared published router is accepted only when its prefilled assignment
        # value exactly matches the map.
        if form_id:
            if not assignment_label:
                form_url = None
        else:
            if assignment_label and assignment_label not in prefilled:
                form_url = None
            elif not assignment_label and len(prefilled) == 1:
                assignment_label = prefilled[0]
            if not assignment_label or assignment_label not in prefilled:
                form_url = None
        explicit_forms = _day_assignment_forms(cells, row, title, key, config)
        form_links = (explicit_forms['forms'] if explicit_forms is not None else
                      ([{'assignment_key': '', 'assignment_title': assignment_label or 'Turn in',
                         'url': form_url, 'form_id': form_id, 'primary': True,
                         'source_reference': f'{title}!U{row}'}] if form_url else []))
        lessons.append({'class_key':key,'title':value(cells,row,3),'plan':plan,'notes':notes,
            'teacher_slides':teacher,'turnin_folder':turnin_folder,'turnin_form':form_url,
            'turnin_form_id':form_id if form_url else None,
            'turnin_forms':form_links,
            'turnin_forms_review_reason':explicit_forms['error'] if explicit_forms else '',
            'assignment_label':assignment_label,'links':resources,'origin':origin,'dashboard_text':extra,
            'weekly_plan':value(cells,row,col) if col else '',
            'todo':value(cells,row,10),'prep':value(cells,row,11),
            'source_reference':f'{title}!A{row}:U{row}'})
    return {'schema_version':'hub-agenda-day/v1','status':'source_read','date':selected,
        'week':week,'closures':value(weeks,wr,5),'lessons':lessons,
        'checked_at':datetime.now(timezone.utc).isoformat(),'save_enabled':False,
        'run_enabled':False,'external_writes':0}
