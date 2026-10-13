"""Bounded refresh of existing writing growth tables and owned chart ranges."""
from copy import deepcopy
from struct import pack, unpack
from urllib.parse import urlparse, parse_qs
from . import google, longitudinal_report as growth, student_archive
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, bounded_json, key
from school_workflow_hub.core.runs import content_hash


class Held(ValueError):
    pass


def _rows(sheet, width):
    from .teaching_report import _sheet_rows
    rows = [list(r[:width]) for r in _sheet_rows(sheet)]
    for row in rows:
        while row and row[-1] == '': row.pop()
    while rows and not rows[-1]: rows.pop()
    return rows


def _source_id(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('drive.google.com', 'docs.google.com'):
        raise Held('A growth evidence link is not an immutable Google source identity.')
    parts = parsed.path.split('/')
    return parts[parts.index('d')+1] if 'd' in parts else parse_qs(parsed.query).get('id', [''])[0]


def _old_evidence(rows, observations, roster):
    """Resolve table sides by original source, category, date, rubric and field."""
    people = {p['student_key']: p for p in roster}
    index = {}
    for o in observations:
        person = people.get(o['student_key'])
        if person is None: continue
        ident = (person['name'], person['section'], o['lesson'], o['date'], o['level'],
                 o['source_url'], o['quote'], o['dimension'], o['rubric_version'], o['stage'])
        index.setdefault(ident, []).append(o)
    matched = {}
    for row in rows[1:]:
        if len(row) != len(growth.STUDENT_HEADER) or row[2] != 'Science writing':
            raise Held('The existing Student growth rows changed; preserve them for review.')
        for lesson, day, level, url, quote in ((6,7,8,13,15), (9,10,11,14,16)):
            if not row[day] and not row[url] and row[level] == 'Not assessed': continue
            ident = (row[0], row[1], row[lesson], row[day], row[level], row[url], row[quote], row[3], row[4], row[5])
            candidates = index.get(ident, [])
            if len(candidates) != 1 or _source_id(row[url]) != candidates[0]['source_file_id']:
                raise Held('A growth row no longer uniquely binds its saved original source, date, level, rubric and field.')
            o = candidates[0]; matched[content_hash(o)] = o
    return list(matched.values())


def _sources(spec):
    basic = spec.get('basicChart', {})
    result = []
    for item in basic.get('domains', []) + basic.get('series', []):
        data = item.get('domain', item.get('series', {}))
        sources = data.get('sourceRange', {}).get('sources', [])
        if len(sources) != 1:
            raise Held('A growth chart has an undeclared or discontiguous source.')
        result.append(sources[0])
    return result


def _references(value):
    if isinstance(value, dict):
        if 'sourceRange' in value: yield from value['sourceRange'].get('sources', [])
        for child in value.values(): yield from _references(child)
    elif isinstance(value, list):
        for child in value: yield from _references(child)


def _chart_spec(chart, sid, start, end, columns, *, line=False):
    spec = deepcopy(chart['spec']); basic = spec.get('basicChart', {})
    sources = _sources(spec)
    if basic.get('chartType') != ('LINE' if line else 'BAR') or basic.get('headerCount') != 1:
        raise Held('An existing growth chart changed its declared chart type or header.')
    if [s.get('startColumnIndex', 0) for s in sources] != columns or any(
            s != {'sheetId': sid, 'startRowIndex': start, 'endRowIndex': end,
                  'startColumnIndex': col, 'endColumnIndex': col+1} for s, col in zip(sources, columns)):
        raise Held('An existing growth chart does not match its exact verified source block.')
    if line:
        axis = [a for a in basic.get('axis', []) if a.get('position') == 'LEFT_AXIS']
        window = axis[0].get('viewWindowOptions', {}) if len(axis) == 1 else {}
        if (basic.get('interpolateNulls', False) is not False or basic.get('lineSmoothing', False) is not False
                or window.get('viewWindowMin') != 1 or window.get('viewWindowMax') != 4
                or window.get('viewWindowMode') != 'EXPLICIT'):
            raise Held('The writing line must retain its explicit 1–4 ordinal axis and unconnected blanks.')
    return spec


def plan(books, analysis, report, observations, roster):
    """Pure plan: no creation, rerating, style edits, title-only joins or I/O."""
    if any(o.get('dimension') not in growth.WRITING or o.get('critical_aspect') for o in observations):
        raise Held('Automatic growth refresh accepts saved writing observations only.')
    if len({(p['name'], p['section']) for p in roster}) != len(roster):
        raise Held('Growth refresh needs uniquely verified current roster names and sections.')
    new = growth.build(observations, roster)
    series, blocks = growth.student_series(observations, roster)
    maps = {ident: {s['properties']['title']: s for s in book['sheets']} for ident, book in books.items()}
    required = {analysis: {'Student growth': growth.STUDENT_HEADER, 'Student figure data': series[0]},
                report: {'Across lessons': growth.CLASS_HEADER, 'Growth figure data': growth.FIGURE_HEADER,
                         'Growth evidence limits': growth.LIMIT_HEADER}}
    for ident, tabs in required.items():
        for title, header in tabs.items():
            if title not in maps[ident] or not _rows(maps[ident][title], len(header)) or _rows(maps[ident][title], len(header))[0] != header:
                raise Held('An existing writing growth tab or its owned columns changed.')
    old_students = _rows(maps[analysis]['Student growth'], len(growth.STUDENT_HEADER))
    old_observations = _old_evidence(old_students, observations, roster)
    old = growth.build(old_observations, roster)
    if old_students != old['tables']['Student growth'] or _rows(maps[report]['Across lessons'], len(growth.CLASS_HEADER)) != old['tables']['Across lessons']:
        raise Held('Existing paired tables do not exactly match saved evidence and the current roster; preserve teacher edits.')
    old_series, old_blocks = growth.student_series(old_observations, roster)
    actual_series = _rows(maps[analysis]['Student figure data'], 6)
    if len(actual_series) != len(old_series):
        raise Held('Existing student source blocks changed; preserve their chart ownership.')
    labels = {}
    for actual, expected in zip(actual_series, old_series):
        if actual[:5] != expected[:5] or len(actual) != len(expected):
            raise Held('Student source values do not match saved writing evidence.')
        if actual != expected:
            day, separator, title = expected[5].partition(' | ')
            if not separator or not actual[5].startswith(day + ' | '):
                raise Held('A student source label changed its original date.')
            display = actual[5].split(' | ', 1)[1]
            if title in labels and labels[title] != display:
                raise Held('One source lesson has ambiguous existing display labels.')
            labels[title] = display
    def display(text):
        for full, short in sorted(labels.items(), key=lambda item: -len(item[0])): text = text.replace(full, short)
        return text
    old_figures = old['tables']['Growth figure data']
    actual_figures = _rows(maps[report]['Growth figure data'], 4)
    if len(actual_figures) != len(old_figures) or any(a[1:] != b[1:] or a[0] != display(b[0]) for a,b in zip(actual_figures[1:], old_figures[1:])):
        raise Held('The class figure sources do not match verified paired observations.')
    tables = {analysis: {'Student growth': new['tables']['Student growth'], 'Student figure data': series},
              report: {title: new['tables'][title] for title in required[report]}}
    # Conceptual evidence limits are dated history, not a new writing judgment.
    prior_limits = _rows(maps[report]['Growth evidence limits'], 3)
    tables[report]['Growth evidence limits'] = [growth.LIMIT_HEADER] + [r for r in prior_limits[1:] if r[0] != 'Science writing'] + [r for r in new['tables']['Growth evidence limits'][1:] if r[0] == 'Science writing']
    for row in series[1:]: row[5] = display(row[5])
    for row in tables[report]['Growth figure data'][1:]: row[0] = display(row[0])
    old_by_range = {(b['start_row'], b['end_row']): b for b in old_blocks}
    new_by_owner = {(b['student_key'], b['rubric_version'], b['title'].rsplit(' · ',1)[1]): b for b in blocks}
    specs, owners, class_charts = {}, set(), 0
    colors = ((.03,.49,.64), (.50,.31,.64), (.09,.51,.43), (.83,.40,.16))
    for ident, book in books.items():
        sid = maps[ident]['Student figure data' if ident == analysis else 'Growth figure data']['properties']['sheetId']
        for sheet in book['sheets']:
            for chart in sheet.get('charts', []):
                if not any(r.get('sheetId') == sid for r in _references(chart.get('spec',{}))): continue
                refs = _sources(chart.get('spec', {}))
                if ident == report:
                    spec = _chart_spec(chart, sid, 0, len(actual_figures), [0,1,2]); start, end = 0, len(tables[report]['Growth figure data']); class_charts += 1
                else:
                    first = refs[0]; old_block = old_by_range.get((first.get('startRowIndex'), first.get('endRowIndex')))
                    if not old_block: raise Held('A student chart source does not bind one verified contiguous block.')
                    owner = (old_block['student_key'], old_block['rubric_version'], old_block['title'].rsplit(' · ',1)[1])
                    block = new_by_owner.get(owner)
                    if not block: raise Held('An existing writing chart lost its current-roster rubric/field owner.')
                    columns = [r.get('startColumnIndex',0) for r in refs]
                    numeric = {col for col in range(1,5) if any(isinstance(r[col], (int,float)) for r in old_series[old_block['start_row']+1:old_block['end_row']])}
                    if columns[0] != 5 or len(set(columns)) != len(columns) or not set(columns[1:]) <= {1,2,3,4} or not numeric <= set(columns[1:]):
                        raise Held('The student chart does not uniquely declare its observed writing dimensions.')
                    spec = _chart_spec(chart, sid, old_block['start_row'], old_block['end_row'], columns, line=True)
                    new_columns = [5] + [col for col in range(1,5) if any(isinstance(r[col], (int,float)) for r in series[block['start_row']+1:block['end_row']])]
                    for col in new_columns[1:]:
                        if col in columns: continue
                        # The reviewed additive planner's v1 dimension palette.
                        # Google Color stores protobuf float32, including readback.
                        item = {'targetAxis':'LEFT_AXIS','pointStyle':{'shape':'CIRCLE','size':5},
                            'colorStyle':{'rgbColor':{k:unpack('f',pack('f',v))[0] for k,v in zip(('red','green','blue'),colors[col-1])}},
                            'series':{'sourceRange':{'sources':[{'sheetId':sid,'startRowIndex':old_block['start_row'], 'endRowIndex':old_block['end_row'],'startColumnIndex':col,'endColumnIndex':col+1}]}}}
                        current = spec['basicChart']['series']
                        index = next((i for i,s in enumerate(current) if s['series']['sourceRange']['sources'][0]['startColumnIndex']>col),len(current))
                        current.insert(index,item)
                    start, end = block['start_row'], block['end_row']; owners.add(owner)
                for source in _sources(spec): source.update(startRowIndex=start, endRowIndex=end)
                specs[ident, chart['chartId']] = spec
    if not class_charts or owners != set(new_by_owner):
        raise Held('Writing growth charts do not uniquely cover the verified current source blocks; review separate rubric/field series.')
    plans = {}
    for ident, changes in tables.items():
        requests = []
        for title, rows in changes.items():
            sheet = maps[ident][title]; props = sheet['properties']; width = len(rows[0]); count = max(len(_rows(sheet,width)), len(rows))
            if count > 10000: raise Held('Growth refresh exceeded its bounded table rectangle.')
            if _rows(sheet,width) == rows: continue
            if any(c.get('textFormatRuns') or c.get('userEnteredValue',{}).get('formulaValue') for data in sheet.get('data',[]) for row in data.get('rowData',[]) for c in row.get('values',[])[:width]):
                raise Held('A growth table contains teacher formulas or rich text; preserve it for review.')
            if props['gridProperties']['rowCount'] < count:
                requests.append({'appendDimension': {'sheetId': props['sheetId'], 'dimension': 'ROWS', 'length': count-props['gridProperties']['rowCount']}})
            requests.append({'updateCells': {'range': {'sheetId': props['sheetId'], 'endRowIndex': count, 'endColumnIndex': width},
                'rows': [{'values': [{'userEnteredValue': {'numberValue': x} if isinstance(x,(int,float)) else {'stringValue': str(x or '')}} for x in row]} for row in rows], 'fields': 'userEnteredValue'}})
        for sheet in books[ident]['sheets']:
            for chart in sheet.get('charts', []):
                spec = specs.get((ident, chart['chartId']))
                if spec is not None and spec != chart['spec']: requests.append({'updateChartSpec': {'chartId': chart['chartId'], 'spec': spec}})
        plans[ident] = {'requests': requests, 'tables': changes, 'specs': {cid: spec for (book,cid),spec in specs.items() if book == ident}}
    return plans


def _protected(book, planned):
    """Bookend everything except the declared values, ranges and row capacity."""
    value = deepcopy(book)
    for sheet in value['sheets']:
        title = sheet['properties']['title']; table = planned['tables'].get(title)
        if table is not None:
            sheet['properties']['gridProperties'].pop('rowCount', None)
            for data in sheet.get('data', []):
                for i, row in enumerate(data.get('rowData', [])):
                    for j, cell in enumerate(row.get('values', [])):
                        if data.get('startColumn',0)+j < len(table[0]):
                            for field in ('userEnteredValue','effectiveValue','formattedValue','hyperlink','effectiveFormat'): cell.pop(field,None)
                    while row.get('values') and not row['values'][-1]: row['values'].pop()
                    if not row.get('values'): row.pop('values',None)
                while data.get('rowData') and not data['rowData'][-1]: data['rowData'].pop()
                if not data.get('rowData'): data.pop('rowData',None)
        for chart in sheet.get('charts', []):
            if chart['chartId'] in planned['specs']: chart.pop('spec',None)
    return value


def _read(token, ident):
    meta = google.drive_file(token, ident, student_archive.FIELDS); student_archive._private(meta)
    if meta.get('mimeType') != 'application/vnd.google-apps.spreadsheet' or not meta.get('capabilities',{}).get('canEdit'):
        raise Held('An existing private growth workbook is not editable by the runtime.')
    return google.get_json('https://sheets.googleapis.com/v4/spreadsheets/'+ident, token, {'includeGridData': 'true'})


def _reservation(config):
    from . import teaching_report as report
    ids = [report._sheet_id(config.get(name)) for name in ('HUB_BIOLOGY_ANALYSIS_URL','HUB_GROWTH_REPORT_URL')]
    port = report.configured_store(config); ident = key('biologygrowthreservation', ids)
    return port, ident, port.read([ident]).documents[ident]


def _expected(books, planned):
    return {ident: {'tables':{title:{'width':len(rows[0]),'sha256':content_hash(rows)} for title,rows in p['tables'].items()},
        'specs':{str(cid):content_hash(spec) for cid,spec in p['specs'].items()},
        'protected':content_hash(_protected(books[ident],p))} for ident,p in planned.items()}


def _confirmed(books, expected):
    for ident, expected_book in expected.items():
        mapping = {s['properties']['title']:s for s in books[ident]['sheets']}
        charts = {str(c['chartId']):c['spec'] for s in books[ident]['sheets'] for c in s.get('charts',[])}
        planned = {'tables':{title:[['']*t['width']] for title,t in expected_book['tables'].items()},'specs':{int(cid):None for cid in expected_book['specs']}}
        if (any(content_hash(_rows(mapping[title],t['width'])) != t['sha256'] for title,t in expected_book['tables'].items())
                or any(content_hash(charts.get(cid)) != digest for cid,digest in expected_book['specs'].items())
                or content_hash(_protected(books[ident],planned)) != expected_book['protected']): return False
    return True


def recover(token, config, renew):
    """Reconcile a prior pending write before ordinary teacher tables can change."""
    from . import teaching_report as report
    try:
        port, ident, doc = _reservation(config)
        if doc is None: return None
        if doc.value.get('schema') != 'biology-growth-reservation/v1':
            raise Held('The private growth-write reservation schema needs review.')
        configured = {report._sheet_id(config.get(name)) for name in ('HUB_BIOLOGY_ANALYSIS_URL','HUB_GROWTH_REPORT_URL')}
        if len(configured) != 2 or not isinstance(doc.value.get('books'),dict) or set(doc.value['books']) != configured:
            raise Held('The growth-write reservation does not bind the two configured private workbooks.')
        if doc.value.get('status') == 'verified': return None
        if doc.value.get('status') != 'pending':
            raise Held('The private growth-write reservation needs review; no growth write was attempted.')
        books = {book:_read(token,book) for book in doc.value['books']}
        if not _confirmed(books,doc.value['books']):
            raise Held('A previous writing-growth write remains unconfirmed. Check the existing growth tables and charts; automatic growth writes stay held.')
        renew(); value = {**doc.value,'status':'verified'}; port.commit({ident:(value,doc.version)})
        if port.read([ident]).documents[ident].value != value: raise Held('Growth recovery private-state readback is unconfirmed.')
        return None
    except (Held, report.Held, student_archive.Held, google.GoogleError, StoreUnavailable, Conflict, KeyError, IndexError, TypeError, ValueError) as error:
        return {'status':'held','problem':str(error),'model_calls':0,'grade_writes':0,'source_writes':0}


def refresh(token, write_token, config, roster, renew):
    """Use the authenticated existing writer; uncertain POST gets one readback."""
    from . import teaching_report as report_module
    try:
        prior = recover(token,config,renew)
        if prior is not None: return prior
        analysis = report_module._sheet_id(config.get('HUB_BIOLOGY_ANALYSIS_URL'))
        report = report_module._sheet_id(config.get('HUB_GROWTH_REPORT_URL'))
        if analysis == report or not write_token: raise Held('Growth refresh needs its two existing private workbook connections.')
        def read(ident): return _read(token,ident)
        observations = report_module.read_observations(config)
        books = {ident: read(ident) for ident in (analysis,report)}
        planned = plan(books, analysis, report, observations, roster)
        if content_hash(report_module.read_observations(config)) != content_hash(observations):
            raise Held('Saved writing evidence changed during growth preflight; no growth write was attempted.')
        fresh = {ident: read(ident) for ident in books}
        if content_hash(fresh) != content_hash(books): raise Held('A growth workbook changed during preflight; preserve teacher edits.')
        reservation = None
        if any(p['requests'] for p in planned.values()):
            port, reservation_id, doc = _reservation(config)
            if doc is not None and doc.value.get('status') != 'verified': raise Held('A prior growth write needs reconciliation before another mutation.')
            reservation = {'schema':'biology-growth-reservation/v1','status':'pending','books':_expected(books,planned)}
            bounded_json(reservation); renew(); port.commit({reservation_id:(reservation,doc.version if doc else None)})
            if port.read([reservation_id]).documents[reservation_id].value != reservation: raise Held('Growth write reservation is not confirmed; no workbook write was attempted.')
        for ident, change in planned.items():
            if not change['requests']: continue
            renew()
            if content_hash(read(ident)) != content_hash(books[ident]) or content_hash(report_module.read_observations(config)) != content_hash(observations):
                raise Held('The growth workbook or saved evidence changed immediately before its write; preserve teacher edits.')
            try:
                google.get_json('https://sheets.googleapis.com/v4/spreadsheets/'+ident+':batchUpdate', write_token, method='POST', body={'requests': change['requests']})
            except google.GoogleError:
                pass  # Never retry a POST, including a lost response.
            after = read(ident)
            mapping = {s['properties']['title']: s for s in after['sheets']}
            charts = {c['chartId']: c['spec'] for s in after['sheets'] for c in s.get('charts',[])}
            if (any(_rows(mapping[title],len(rows[0])) != [list(r) for r in rows] for title,rows in change['tables'].items())
                    or any(charts.get(cid) != spec for cid,spec in change['specs'].items())
                    or _protected(after,change) != _protected(books[ident],change)):
                raise Held('The growth write is unconfirmed or preserved workbook content changed; do not automatically retry it.')
            fresh[ident] = after
        if reservation is not None:
            if not _confirmed(fresh,reservation['books']): raise Held('The complete growth update remains unconfirmed; automatic replay is blocked.')
            doc = port.read([reservation_id]).documents[reservation_id]
            if doc.value != reservation: raise Held('The growth-write reservation changed; automatic replay is blocked.')
            verified = {**reservation,'status':'verified'}; renew(); port.commit({reservation_id:(verified,doc.version)})
            if port.read([reservation_id]).documents[reservation_id].value != verified: raise Held('Growth completion private-state readback is unconfirmed.')
        return {'status': 'verified', 'model_calls': 0, 'grade_writes': 0, 'source_writes': 0}
    except (Held, growth.Held, report_module.Held, student_archive.Held, google.GoogleError, StoreUnavailable, Conflict, KeyError, IndexError, TypeError, ValueError) as error:
        return {'status': 'held', 'problem': str(error), 'model_calls': 0, 'grade_writes': 0, 'source_writes': 0}
