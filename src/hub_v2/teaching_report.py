"""Evidence-bound Biology reporting; no grading, provider calls or source writes."""
from collections import Counter
from datetime import date
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import uuid
from . import completion
from . import google, student_archive
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import CloudState, Conflict, StoreUnavailable, bounded_json, key
from school_workflow_hub.core.runs import content_hash

LEVELS = ('Beginning', 'Developing', 'Proficient', 'Extending')
DIMENSIONS = ('Uses lesson terms accurately to explain an idea',
              'Constructs a conceptual answer that explains a relationship or mechanism',
              'Supports the claim with specific evidence',
              'Explains how the evidence supports the claim')
DIMENSION_KEYS = dict(zip(DIMENSIONS, ('vocabulary', 'conceptual', 'evidence', 'reasoning')))


class Held(ValueError):
    pass


def response_items(student, template):
    """Extract attributable answer boxes against printed reference, without scoring."""
    pairs = completion._align(student, template)
    codes = completion.markers(student)
    result = []
    for slide in student['slides']:
        reference = pairs.get(slide['number'])
        if reference is None:
            continue
        markers = [m for m in codes if m['slide'] == slide['number']]
        if not markers:
            markers = [m for m in completion.markers(template) if m['slide'] == reference['number']]
        written, exact = completion._written(slide, reference, student['size'])
        assigned, _, basis = completion._assign(markers, written, reference, student['size'])
        for (category, ident), values in assigned.items():
            answer = ' '.join(exact.get(unit['key'], words) for unit, words in values).strip()
            if category in ('DRAFT', 'NOTES', 'BANK'):
                result.append({'question_id': ident, 'stage': category, 'slide': slide['number'],
                    'question': completion.slide_title(reference), 'answer': answer, 'extraction_basis': basis})
    return result


def latest_observations(observations, active_students):
    """One dated observation per active student/lesson/dimension; conflicts hold."""
    active = set(active_students)
    selected = {}
    for item in observations:
        if item['student_key'] not in active:
            continue
        try:
            date.fromisoformat(item['date'])
        except (ValueError, TypeError):
            raise Held('A report observation lacks its verified source date.') from None
        if item.get('level') not in LEVELS + ('Evidence unavailable', 'Not assessed'):
            raise Held('A report observation has an unknown rubric level.')
        if item['level'] in LEVELS and (not item.get('quote') or not item.get('source_url')):
            raise Held('A rated observation needs its actual excerpt and source link.')
        identity = (item['student_key'], item['lesson_key'], item['dimension'])
        prior = selected.get(identity)
        if prior is None or item['date'] > prior['date']:
            selected[identity] = item
        elif item['date'] == prior['date'] and item != prior:
            same_file = item.get('source_file_id') and item.get('source_file_id') == prior.get('source_file_id')
            revisions = (str(item.get('source_revision', '')), str(prior.get('source_revision', '')))
            if same_file and all(v.isdecimal() for v in revisions) and revisions[0] != revisions[1]:
                if int(revisions[0]) > int(revisions[1]):
                    selected[identity] = item
            else:
                raise Held('Two observations disagree for the same student, lesson, dimension and date.')
    return list(selected.values())


def class_measures(observations, active_students, scopes):
    """Count each active person once; missing assessments never become Beginning."""
    active = tuple(active_students)
    if not active or len(active) != len(set(active)):
        raise Held('Class reporting needs a unique nonempty current roster.')
    selected = latest_observations(observations, active)
    output = []
    for scope in scopes:
        matching = [o for o in selected if o['lesson_key'] == scope['lesson_key'] and o['dimension'] == scope['dimension']]
        counts = Counter(o['level'] for o in matching)
        rated = sum(counts[level] for level in LEVELS)
        unavailable = counts['Evidence unavailable']
        unassessed = len(active) - rated - unavailable
        if unassessed < 0:
            raise Held('Class counts exceed the verified roster.')
        output.append({**scope, 'active': len(active), **{level: counts[level] for level in LEVELS},
            'rated': rated, 'evidence_unavailable': unavailable, 'not_assessed': unassessed,
            'proficient_plus': counts['Proficient'] + counts['Extending'],
            'coverage': rated / len(active),
            'proficient_plus_class': (counts['Proficient'] + counts['Extending']) / len(active),
            'proficient_plus_rated': (counts['Proficient'] + counts['Extending']) / rated if rated else None})
    return output


def native_observations(saved, student_key, source_date, *, lesson_key,
                        date_basis='form_receipt', submitted_at=None):
    """Normalize already validated, saved native feedback; never generate or rerate.

    The caller supplies an independently verified archive identity and original
    Form/source date. An old report without dimensions remains unassessed. Its
    conceptual aspects are not substituted for writing observations.
    """
    if not saved.get('saved_at'):
        raise Held('Unsaved feedback cannot contribute to teacher reporting.')
    if not student_key or not lesson_key or saved.get('scope', {}).get('class') != 'biology':
        raise Held('Teacher reporting needs the verified Biology student and lesson identity.')
    try:
        date.fromisoformat(source_date)
        assessed = datetime.fromisoformat(saved['saved_at']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
    except (ValueError, TypeError):
        raise Held('Teacher reporting needs the original work date and actual saved assessment date.') from None
    rows = saved.get('report', {}).get('teacher_dimensions')
    if rows is None:
        return []
    if not isinstance(rows, list) or len(rows) != len(DIMENSIONS):
        raise Held('The saved report does not contain four distinct writing dimensions.')
    source = saved.get('source', {}).get('file', {})
    if not source.get('id') or not source.get('version'):
        raise Held('The saved report lacks its immutable original source revision.')
    result = []
    for dimension, item in zip(DIMENSIONS, rows):
        if (item.get('dimension') != dimension or item.get('level') not in LEVELS + ('Evidence unavailable',)
                or item.get('stage') not in ('NOTES', 'DRAFT', 'BANK', '')
                or len(item.get('quote', '').split()) > 25
                or (item['level'] in LEVELS and (not item.get('quote') or not item.get('question_id')
                                               or not item.get('stage') or not item.get('explanation')))):
            raise Held('The saved writing observation needs its exact dimension, answer excerpt and context.')
        result.append({'student_key': student_key, 'lesson_key': lesson_key,
                       'dimension': dimension, 'dimension_key': DIMENSION_KEYS[dimension],
                       'date': source_date, 'assessed_at': assessed, 'level': item['level'],
                       'quote': item['quote'], 'question_id': item['question_id'], 'stage': item['stage'],
                       'explanation': item['explanation'], 'source_file_id': source['id'],
                       'source_revision': str(source['version']),
                       'source_url': 'https://drive.google.com/file/d/' + source['id'] + '/view',
                       'lesson': saved.get('assignment', {}).get('assignment_title') or lesson_key,
                       'reference_url': saved.get('assignment', {}).get('source_url', ''),
                       'source_date_basis': date_basis, 'submitted_at': submitted_at,
                       'saved_feedback_id': saved.get('id'),
                       'review_status': 'Source interpretation for teacher review'})
    return result


def _sheet_id(url):
    match = re.fullmatch(r'https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{10,200})(?:/[^\s]*)?', str(url or ''))
    if not match:
        raise Held('The Biology report needs its verified Google Sheets connection.')
    return match[1]


def _dataset_key(config):
    return key('biologyreportdataset', _sheet_id(config.get('HUB_BIOLOGY_ANALYSIS_URL')))


def save_observations(config, observations, *, baseline=False):
    """Persist an immutable reviewed-source batch in existing private CAS state.

    A baseline is seeded once; future batches come only from saved, validated
    native feedback. Same saved identity cannot be overwritten with new evidence.
    No provider, student file, source or grade is written here.
    """
    if not observations:
        return {'status': 'unchanged', 'observations': 0}
    if not isinstance(observations, list) or len(observations) > 1000:
        raise Held('This Biology reporting batch exceeds its bounded evidence scope.')
    active = list(dict.fromkeys(o['student_key'] for o in observations))
    latest_observations(observations, active)
    if any(o.get('dimension') not in DIMENSIONS or not o.get('source_file_id')
           or not o.get('source_revision') or not o.get('assessed_at') for o in observations):
        raise Held('Writing observations need their exact dimension, source revision and assessment date.')
    dataset = _dataset_key(config)
    identities = sorted({o.get('saved_feedback_id') for o in observations if o.get('saved_feedback_id')})
    if not baseline and (len(identities) != 1 or len(observations) != 4):
        raise Held('A future writing batch must bind four dimensions to one saved feedback identity.')
    batch = key('biologyreportbaseline' if baseline else 'biologyreportobservation', [dataset, identities])
    value = {'schema': 'hub-biology-observations/v1', 'baseline': baseline,
             'observations': observations, 'sha256': content_hash(observations)}
    bounded_json(value)
    port = configured_store(config); cloud = CloudState(port)
    task = cloud.create_task({'workflow': 'biology-reporting', 'class_key': 'biology', 'assignment_key': dataset})
    request = uuid.uuid4().hex
    claim = cloud.claim(task, owner='biology-reporting', request_id=request, input_hash=content_hash([batch, request]))
    before = port.read([dataset, batch]).documents
    prior, index = before[batch], before[dataset]
    if prior and prior.value != value:
        raise Held('A saved writing batch changed; its original evidence is retained for review.')
    index_value = index.value if index else {'schema': 'hub-biology-report-index/v1', 'batches': [], 'baseline': None}
    if index_value.get('schema') != 'hub-biology-report-index/v1' or len(index_value.get('batches', [])) >= 1000:
        raise Held('The private Biology reporting index needs checking before refresh.')
    if baseline and index_value.get('baseline') not in (None, batch):
        raise Held('The original Biology writing baseline cannot be replaced.')
    updated = {**index_value, 'batches': list(dict.fromkeys(index_value['batches'] + [batch])),
               'baseline': batch if baseline else index_value.get('baseline')}
    claim = cloud.renew(claim)
    changes = {}
    if not prior: changes[batch] = (value, None)
    if updated != index_value or index is None: changes[dataset] = (updated, index.version if index else None)
    if changes:
        try:
            port.commit(changes)
        except (Conflict, StoreUnavailable):
            actual = port.read(list(changes)).documents
            if any(actual[i] is None or actual[i].value != v for i, (v, _) in changes.items()):
                raise
    actual = port.read([dataset, batch]).documents
    if actual[batch].value != value or actual[dataset].value != updated:
        raise Held('The private writing evidence could not be confirmed after saving.')
    result = {'status': 'verified', 'batch_key': batch, 'observations': len(observations)}
    cloud.finish(claim, records={'biology_reporting': result})
    return result


def read_observations(config):
    port = configured_store(config); ident = _dataset_key(config)
    index = port.read([ident]).documents[ident]
    if not index or index.value.get('schema') != 'hub-biology-report-index/v1' or not index.value.get('baseline'):
        raise Held('The Biology writing baseline has not been verified in private storage yet.')
    ids = index.value.get('batches', [])
    if len(ids) > 1000 or len(set(ids)) != len(ids) or index.value['baseline'] not in ids:
        raise Held('The private Biology reporting index needs checking.')
    result = []
    for start in range(0, len(ids), 50):
        records = port.read(ids[start:start + 50]).documents
        for i in ids[start:start + 50]:
            doc = records[i]
            if (not doc or doc.value.get('schema') != 'hub-biology-observations/v1'
                    or content_hash(doc.value.get('observations')) != doc.value.get('sha256')):
                raise Held('A private writing batch no longer matches its saved evidence.')
            result.extend(doc.value['observations'])
    return result


WRITING_HEADER = ['Student', 'Section', 'Lesson', 'Work / source date', 'Prepared / assessed', 'Dimension',
                  'Level', 'Actual source excerpt', 'Question ID', 'Field', 'Slide',
                  'Interpretation for teacher review', 'Original work', 'Teacher lesson', 'Date basis',
                  'Source revision', 'Review status']
MEASURE_HEADER = ['Lesson', 'Dimension', 'Current Biology class', 'Beginning', 'Developing', 'Proficient',
                  'Extending', 'Evidence unavailable', 'Not assessed', 'Rated students',
                  'Proficient + Extending', 'P+ / class', 'P+ / rated', 'Rated / class',
                  'Work date range', 'Assessment date', 'Evidence type']


def writing_tables(observations, active_students):
    """Create full current-roster tables; never count a response more than once."""
    if (not active_students or len(active_students) > 200
            or len({s['student_key'] for s in active_students}) != len(active_students)
            or len({(s['name'], s['section']) for s in active_students}) != len(active_students)):
        raise Held('Biology reporting needs the complete, uniquely verified current roster.')
    active = [s['student_key'] for s in active_students]
    selected = latest_observations(observations, active)
    lessons = {}
    for o in selected:
        lessons[o['lesson_key']] = o.get('lesson') or o['lesson_key']
    scopes = [{'lesson_key': ident, 'lesson': title, 'dimension': d}
              for ident, title in sorted(lessons.items(), key=lambda i: i[1]) for d in DIMENSIONS]
    lookup = {(o['student_key'], o['lesson_key'], o['dimension']): o for o in selected}
    detail = [WRITING_HEADER]
    for student in sorted(active_students, key=lambda s: (s['section'], s['name'])):
        for scope in scopes:
            o = lookup.get((student['student_key'], scope['lesson_key'], scope['dimension']))
            detail.append([student['name'], student['section'], scope['lesson'], o['date'] if o else '',
                o.get('assessed_at', '') if o else '', scope['dimension'], o['level'] if o else 'Not assessed',
                o.get('quote', '') if o else '', o.get('question_id', '') if o else '', o.get('stage', '') if o else '',
                (o.get('slide') or '') if o else '', o.get('explanation', '') if o else 'No interpretation in the located source set.',
                o.get('source_url', '') if o else '', o.get('reference_url', '') if o else '',
                o.get('source_date_basis', '') if o else '', o.get('source_revision', '') if o else '',
                'Source interpretation for teacher review' if o else 'Not assessed'])
    measures = [MEASURE_HEADER]
    for m in class_measures(selected, active, scopes):
        rows = [o for o in selected if o['lesson_key'] == m['lesson_key'] and o['dimension'] == m['dimension']]
        dates = sorted(o['date'] for o in rows); assessed = sorted(o.get('assessed_at', '') for o in rows)
        measures.append([m['lesson'], m['dimension'], m['active'], *[m[level] for level in LEVELS],
            m['evidence_unavailable'], m['not_assessed'], m['rated'], m['proficient_plus'],
            m['proficient_plus_class'], m['proficient_plus_rated'] if m['proficient_plus_rated'] is not None else '',
            m['coverage'], dates[0] + ' to ' + dates[-1], assessed[-1], 'Direct source interpretation; teacher review'])
    return {'Student evidence': detail, 'Whole class': measures}


def _sheet_rows(resource):
    result = []
    for data in resource.get('data', []):
        for row in data.get('rowData', []):
            values = [c.get('effectiveValue', c.get('userEnteredValue', {})).get('stringValue',
                c.get('effectiveValue', c.get('userEnteredValue', {})).get('numberValue',
                c.get('effectiveValue', c.get('userEnteredValue', {})).get('boolValue', '')))
                for c in row.get('values', [])]
            while values and values[-1] == '': values.pop()
            result.append(values)
    while result and not result[-1]: result.pop()
    return result


def refresh_writing_tables(token, write_token, config, active_students):
    dataset = _dataset_key(config)
    port = configured_store(config); cloud = CloudState(port)
    task = cloud.create_task({'workflow': 'biology-reporting', 'class_key': 'biology', 'assignment_key': dataset})
    request = uuid.uuid4().hex
    claim = cloud.claim(task, owner='biology-report-refresh', request_id=request, input_hash=content_hash(request))
    def renew():
        nonlocal claim
        claim = cloud.renew(claim)
    result = _refresh_writing_tables(token, write_token, config, active_students, renew)
    renew()
    cloud.finish(claim, records={'biology_reporting': result})
    return result


def _refresh_writing_tables(token, write_token, config, active_students, renew):
    """Refresh existing teacher-only tables; preserve Teaching plan and all notes.

    The host supplies its fresh verified roster. There are no provider calls,
    student-file actions, score writes or delivery-state changes. Both private
    workbooks must be readable before the first bounded, idempotent table write.
    """
    observations = read_observations(config)
    tables = writing_tables(observations, active_students)
    analysis = _sheet_id(config.get('HUB_BIOLOGY_ANALYSIS_URL'))
    report = _sheet_id(config.get('HUB_GROWTH_REPORT_URL'))
    if analysis == report or not write_token:
        raise Held('The two private Biology reports need their existing runtime write connection.')
    books = {}
    for ident in (analysis, report):
        meta = google.drive_file(token, ident, student_archive.FIELDS)
        student_archive._private(meta)
        if meta.get('mimeType') != 'application/vnd.google-apps.spreadsheet' or not meta.get('capabilities', {}).get('canEdit'):
            raise Held('A private Biology report is not editable by the existing runtime.')
        books[ident] = google.get_json('https://sheets.googleapis.com/v4/spreadsheets/' + ident, token, {'includeGridData': 'true'})
    amap = {s['properties']['title']: s for s in books[analysis]['sheets']}
    rmap = {s['properties']['title']: s for s in books[report]['sheets']}
    if not {'Student evidence', 'Whole class'} <= set(amap) or not {'Report', 'Lesson measures', 'Teaching plan', 'Guide'} <= set(rmap):
        raise Held('The Biology report tabs changed; refresh needs their verified layout.')
    prior = _sheet_rows(rmap['Lesson measures'])
    if not prior or prior[0] != MEASURE_HEADER:
        raise Held('The class report measure columns changed; preserve them for teacher review.')
    historic = [row for row in prior[1:] if len(row) >= 17 and row[16] != 'Direct source interpretation; teacher review']
    if any(row[2] != len(active_students) for row in historic):
        raise Held('The saved conceptual totals need their current-roster projection refreshed. Preserve the dated report until that is confirmed.')
    summary = _sheet_rows(rmap['Report'])
    if len(summary) < 8 or summary[7] != MEASURE_HEADER:
        raise Held('The Biology class summary layout changed; preserve it for teacher review.')
    merged = historic + tables['Whole class'][1:]
    current = latest_observations(observations, [s['student_key'] for s in active_students])
    summary[0][1] = 'Prepared ' + max(o['assessed_at'] for o in current)
    summary[1][1] = len(active_students)
    summary[2][1] = len({o['student_key'] for o in current})
    summary[3][1] = len({(o['student_key'], o['lesson_key']) for o in current})
    summary[4][0] = 'Original source versions read in initial review'
    replacements = {analysis: tables, report: {'Lesson measures': [MEASURE_HEADER] + merged, 'Report': summary[:8] + merged}}
    def normalize(rows):
        rows = [list(r) for r in rows]
        for r in rows:
            while r and r[-1] == '': r.pop()
        while rows and not rows[-1]: rows.pop()
        return rows
    result = {'status': 'verified', 'class_key': 'biology', 'active': len(active_students),
              'dimension_rows': len(tables['Whole class']) - 1, 'model_calls': 0, 'grade_writes': 0}
    for ident, changes in replacements.items():
        mapping = amap if ident == analysis else rmap
        requests = []
        for title, table in changes.items():
            sheet = mapping[title]; props = sheet['properties']; sid = props['sheetId']
            if normalize(_sheet_rows(sheet)) == normalize(table): continue
            count = max(len(_sheet_rows(sheet)), len(table)); cols = 17
            if count > 10000: raise Held('The Biology analysis exceeded its bounded student table size.')
            if props['gridProperties']['rowCount'] < count:
                requests.append({'appendDimension': {'sheetId': sid, 'dimension': 'ROWS', 'length': count - props['gridProperties']['rowCount']}})
            requests.append({'updateCells': {'range': {'sheetId': sid, 'endRowIndex': count, 'endColumnIndex': cols},
                'rows': [{'values': [{'userEnteredValue': {'boolValue': x} if isinstance(x, bool) else
                    {'numberValue': x} if isinstance(x, (int, float)) else {'stringValue': str(x or '')}} for x in row]} for row in table],
                'fields': 'userEnteredValue'}})
        if requests:
            # If the response is lost, verify the exact cell values once. Never blindly retry a POST.
            try:
                renew()
                google.get_json('https://sheets.googleapis.com/v4/spreadsheets/' + ident + ':batchUpdate', write_token,
                                method='POST', body={'requests': requests})
            except google.GoogleError:
                pass
        fresh = google.get_json('https://sheets.googleapis.com/v4/spreadsheets/' + ident, token, {'includeGridData': 'true'})
        fmap = {s['properties']['title']: s for s in fresh['sheets']}
        if any(normalize(_sheet_rows(fmap[title])) != normalize(table) for title, table in changes.items()):
            raise Held('The Biology report update is not confirmed. Saved feedback and original work remain available.')
        student_archive._private(google.drive_file(token, ident, student_archive.FIELDS))
    return result
