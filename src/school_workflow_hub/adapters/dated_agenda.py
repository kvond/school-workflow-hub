"""Bounded, read-only dated view of the existing weekly Agenda.

Coordinates are provenance, never save targets or stable assignment identities.
No raw source data is persisted. The current Dashboard's manual overrides win.
"""
from datetime import date, datetime, timedelta, timezone
import re
from urllib.parse import parse_qs, urlsplit
from .hosted_agenda import authenticated_reader, ConnectionError

COURSES = (("biology", "🌿 Biology"), ("forensics", "🔬 Forensics"), ("anatomy", "🫀 A&P"))


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
    """Validated Google Form link plus its prefilled text values."""
    target = link(raw)
    if not target:
        return None, ()
    try:
        url = urlsplit(target)
        if url.hostname != 'docs.google.com' or not re.fullmatch(
                r'/forms/d/(?:e/)?[A-Za-z0-9_-]{20,256}/(?:viewform|edit)', url.path):
            return None, ()
        query = parse_qs(url.query, max_num_fields=20, keep_blank_values=False)
        values = []
        for key, items in query.items():
            if not re.fullmatch(r'entry\.[0-9]+', key):
                continue
            for item in items:
                if isinstance(item, str) and 0 < len(item) <= 200 and item not in values:
                    values.append(item)
        return target, tuple(values)
    except (ValueError, TypeError):
        return None, ()


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
        form_url, prefilled = turnin_form(value(cells,row,21))
        # The Form link is now the durable submission mapping. Bind it to the
        # exact prefilled assignment value; a stale TurnIn Map label cannot
        # silently redirect one lesson into another assignment's responses.
        if assignment_label and assignment_label not in prefilled:
            form_url = None
        elif not assignment_label and len(prefilled) == 1:
            assignment_label = prefilled[0]
        if not assignment_label or assignment_label not in prefilled:
            form_url = None
        lessons.append({'class_key':key,'title':value(cells,row,3),'plan':plan,'notes':notes,
            'teacher_slides':teacher,'turnin_folder':turnin_folder,'turnin_form':form_url,
            'assignment_label':assignment_label,'links':resources,'origin':origin,'dashboard_text':extra,
            'weekly_plan':value(cells,row,col) if col else '',
            'todo':value(cells,row,10),'prep':value(cells,row,11),
            'source_reference':f'{title}!A{row}:U{row}'})
    return {'schema_version':'hub-agenda-day/v1','status':'source_read','date':selected,
        'week':week,'closures':value(weeks,wr,5),'lessons':lessons,
        'checked_at':datetime.now(timezone.utc).isoformat(),'save_enabled':False,
        'run_enabled':False,'external_writes':0}
