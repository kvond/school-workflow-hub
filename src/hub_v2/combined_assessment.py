"""Explicit multi-deck Form assessments; no title inference or source writes.

The private host declaration binds an exact year/Agenda/class/week/Form/folder
to a small set of cycle -> teacher IDs. Original Form histories stay intact.
Only authenticated school-email uploads can supply a component. Counts, not
rounded component grades, are pooled. Missing components are disclosed and
never supply a fabricated denominator or absence zero.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
import hashlib
import json
import re
from threading import Lock

from . import completion, google, work

CONFIG = 'HUB_COMBINED_ASSESSMENTS'
FIELDS = {'school_year', 'agenda_id', 'class_key', 'week', 'form_id',
          'lesson_folder_id', 'components'}
ID = re.compile(r'[A-Za-z0-9_-]{10,200}')
MAX_SOURCES = 200
SOURCE_WORKERS = 4


class Held(ValueError):
    pass


SOURCE_FIELDS = google.FILE_FIELDS + ',version,md5Checksum,size,headRevisionId'
_request_captures = ContextVar('combined_request_captures', default=None)


@contextmanager
def _source_results(reader, file_ids):
    """Ordered independent reads; join all workers before returning or refusing.

    Each job owns its copied context while sharing only this request's guarded
    captures. Google's transport remains thread-local, never a shared Session.
    """
    with ThreadPoolExecutor(max_workers=SOURCE_WORKERS) as pool:
        jobs = [(copy_context(), fid) for fid in file_ids]
        yield pool.map(lambda job: job[0].run(reader, job[1]), jobs)


class _RequestCaptures:
    """Successful immutable captures shared only by this request's workers."""
    def __init__(self):
        self.lock = Lock()
        self.source_locks = {}
        self.values = {}
        self.token = None

    def read(self, token, fid):
        with self.lock:
            if self.token is None:
                self.token = token
            if token != self.token:
                raise Held('The source reader identity changed during this request.')
            if fid not in self.source_locks and len(self.source_locks) >= MAX_SOURCES + 4:
                raise Held('This combined request has too many sources to verify.')
            source_lock = self.source_locks.setdefault(fid, Lock())
        with source_lock:
            before = _source_metadata(token, fid)
            cached = self.values.get(fid)
            if cached is not None:
                if before != cached['metadata']:
                    raise Held('A combined-assignment source changed during this request.')
                if before['mimeType'] == completion.SLIDES or before.get('md5Checksum'):
                    after = _source_metadata(token, fid)
                    if after != before:
                        raise Held('A combined-assignment source changed while it was being read.')
                    return deepcopy(cached)
            captured = _capture_source(token, fid, before)
            # Binary source bytes are reusable only with the provider checksum.
            # Without it, every use retains the original full-byte read guard.
            self.values[fid] = deepcopy(captured)
            return captured

    def verify_bookend(self):
        captured = list(self.values.items())
        with _source_results(lambda fid: _source_metadata(self.token, fid),
                             [fid for fid, _ in captured]) as results:
            for (_, value), metadata in zip(captured, results):
                if metadata != value['metadata']:
                    raise Held('A combined-assignment source changed before the request finished.')


@contextmanager
def source_request():
    """No capture survives a request; each reuse and final return fresh-checks metadata."""
    if _request_captures.get() is not None:
        yield
        return
    captures = _RequestCaptures()
    context = _request_captures.set(captures)
    try:
        yield
        captures.verify_bookend()
    finally:
        _request_captures.reset(context)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode()).hexdigest()


def definition(config, agenda, cls, week, fid):
    """Resolve a private declaration against exact current Agenda links."""
    try:
        entries = work._scoped_config(config, CONFIG, FIELDS)
        scopes = set()
        for entry in entries:
            scope = tuple(entry[k] for k in ('school_year', 'agenda_id', 'class_key', 'week', 'form_id'))
            if (scope in scopes or entry['class_key'] not in agenda.classes
                    or any(not ID.fullmatch(str(entry[k])) for k in ('agenda_id', 'form_id', 'lesson_folder_id'))
                    or not isinstance(entry['week'], str) or not 1 <= len(entry['week']) <= 100
                    or not isinstance(entry['components'], list) or not 2 <= len(entry['components']) <= 4):
                raise ValueError()
            scopes.add(scope)
            cycles, teachers = set(), set()
            for component in entry['components']:
                if (not isinstance(component, dict) or set(component) != {'cycle', 'teacher_id', 'label'}
                        or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', str(component['cycle']))
                        or not ID.fullmatch(str(component['teacher_id']))
                        or not isinstance(component['label'], str) or not 1 <= len(component['label']) <= 100
                        or any(ord(c) < 32 for c in component['label'])
                        or component['cycle'] in cycles or component['teacher_id'] in teachers):
                    raise ValueError()
                cycles.add(component['cycle']); teachers.add(component['teacher_id'])
            # Photosynthesis's complete-code exception cannot be replaced by an
            # aggregate declaration, even one containing a different subject.
            if entry['class_key'] != 'forensics':
                raise ValueError()
        hits = [entry for entry in entries if entry['agenda_id'] == agenda.spreadsheet_id
                and entry['class_key'] == cls and entry['week'] == week and entry['form_id'] == fid]
        if not hits:
            return None
        year = work._school_year(agenda, week)
        hits = [entry for entry in hits if entry['school_year'] == year]
        if not hits:
            if year is None:
                raise ValueError()
            return None
        if len(hits) != 1:
            raise ValueError()
        entry = hits[0]
        lessons = [lesson for lesson in agenda.lessons(cls, week)
                   if any(form.get('form_id') == fid for form in lesson['forms'])]
        if len(lessons) != 1:
            raise ValueError()
        materials = lessons[0]['materials']
        folders = {m.group(1) for material in materials if material.get('label') == 'Lesson folder'
                   if (m := re.search(r'/folders/([A-Za-z0-9_-]+)', material.get('url', '')))}
        teachers = {m.group(1) for material in materials
                    if (m := re.search(r'/presentation/d/([A-Za-z0-9_-]+)', material.get('url', '')))}
        if folders != {entry['lesson_folder_id']} or not {c['teacher_id'] for c in entry['components']} <= teachers:
            raise ValueError()
        return deepcopy(entry)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Held('The combined assignment needs its exact Form, Agenda folder and cycle-to-teacher mapping checked.') from None


def _source_metadata(token, fid):
    before = google.drive_file(token, fid, SOURCE_FIELDS)
    if (before.get('id') != fid or before.get('trashed') is not False
            or before.get('mimeType') not in (completion.SLIDES, google.PPTX)
            or not before.get('version')):
        raise Held('An exact combined-assignment source is unavailable or has no verified revision.')
    return before


def _capture_source(token, fid, before):
    raw = google.deck_bytes(token, fid, before['mimeType'])
    if before['mimeType'] == google.PPTX and before.get('md5Checksum'):
        if hashlib.md5(raw).hexdigest() != before['md5Checksum']:
            raise Held('A combined Office source does not match its current checksum.')
        if before.get('size') is not None and str(len(raw)) != str(before['size']):
            raise Held('A combined Office source does not match its current byte size.')
    deck = completion.read_deck(raw)
    after = _source_metadata(token, fid)
    if before != after:
        raise Held('A combined-assignment source changed while it was being read; reload Student work.')
    return {'metadata': before, 'sha256': hashlib.sha256(raw).hexdigest(), 'deck': deck}


def read_source(token, fid):
    """Bracket source bytes; reuse only within a fresh-checked request scope."""
    captures = _request_captures.get()
    return (captures.read(token, fid) if captures is not None
            else _capture_source(token, fid, _source_metadata(token, fid)))


def source_basis(capture):
    # Native Slides exports may regenerate ZIP bytes without changing the
    # original source. Bind their complete parsed deck plus exact metadata;
    # retain capture['sha256'] as the raw export audit. Office bytes are source.
    mime = capture['metadata']['mimeType']
    if mime not in (completion.SLIDES, google.PPTX):
        raise Held('An exact combined-assignment source has an unsupported source type.')
    content_sha = digest(capture['deck']) if mime == completion.SLIDES else capture['sha256']
    return {'file_id': capture['metadata']['id'], 'version': str(capture['metadata']['version']),
            'modified_at': capture['metadata'].get('modifiedTime', ''), 'sha256': content_sha,
            'hash_basis': 'parsed_completion_deck' if mime == completion.SLIDES else 'original_file_bytes',
            'metadata_sha256': digest(capture['metadata'])}


def classify(capture, teachers):
    """Actual marker identity plus exact registered teacher, never filenames."""
    deck = capture['deck']
    cycles = {marker['code'] for marker in completion.markers(deck)}
    cycles.update(match.group(1) for slide in deck['slides'] for unit in slide['units']
                  for match in completion.INVENTORY.finditer(unit['text']))
    if len(cycles) != 1 or next(iter(cycles)) not in teachers:
        raise Held('An uploaded deck has unknown or conflicting component codes; confirm its lesson.')
    cycle = next(iter(cycles))
    checked = completion.student_readiness(deck, require_complete_codes=False)
    if checked['status'] != 'READY':
        raise Held(checked['problem'])
    if completion.markers(deck) and completion.coverage(deck, teachers[cycle]['deck']) < .8:
        raise Held('An uploaded component does not match its exact registered teacher source.')
    return cycle


def select_rows(form, scope, sources, *, include_late=False, now=None):
    """Pure latest authenticated upload per cycle, with immutable raw history."""
    now = now or datetime.now(timezone.utc)
    cutoff = work.when((form.get('window') or {}).get('at'))
    wanted = [c['cycle'] for c in scope['components']]
    rows, owners = [], {}
    for row in form.get('students', []) + form.get('late', []):
        if row.get('matched') is not True:
            continue
        value = deepcopy(row)
        try:
            if row.get('selection_problem'):
                raise Held(row['selection_problem'])
            events = {}
            for snapshot in [row] + row.get('earlier', []):
                files = snapshot.get('files', [])
                at = work.when(snapshot.get('submitted_at'))
                if (snapshot.get('source') != 'form' or snapshot.get('matched_by') != 'email'
                        or not row.get('email') or snapshot.get('email', '').lower() != row['email'].lower()
                        or not at or at > now or not files):
                    raise Held('Submission history needs an original school-email Form event for every component.')
                receipts = set()
                for upload in files:
                    if (upload.get('source_form_id') != scope['form_id'] or not upload.get('source_response_id')
                            or work.when(upload.get('source_submitted_at')) != at
                            or upload.get('from_folder') is not False or upload.get('from_shared') is not False
                            or upload.get('trashed') is not False or upload.get('other_week') or upload.get('problem')
                            or upload.get('mime') not in (completion.SLIDES, google.PPTX)
                            or upload.get('id') not in sources):
                        raise Held('A component has unverified intake, access or assignment history; confirm its original upload.')
                    receipts.add((upload['source_form_id'], upload['source_response_id']))
                    owners.setdefault(upload['id'], set()).add((row['section'], row['email'].lower(),
                                                               work.norm(row['last']), work.norm(row['first'])))
                if len(receipts) != 1:
                    raise Held('An upload combines different original Form receipts; confirm its components.')
                if not include_late and ((cutoff and at > cutoff) or snapshot.get('late')):
                    continue
                for upload in files:
                    cycle = sources[upload['id']]['cycle']
                    if cycle not in wanted:
                        raise Held('An uploaded deck belongs to a different component.')
                    events.setdefault(cycle, []).append((at, upload, snapshot))
            selected = []
            for cycle in wanted:
                options = events.get(cycle, [])
                if not options:
                    continue
                newest = max(at for at, _, _ in options)
                latest = [item for item in options if item[0] == newest]
                if len({upload['id'] for _, upload, _ in latest}) != 1:
                    raise Held('Several files have the same latest component turn-in time; confirm which copy to score.')
                at, upload, snapshot = latest[0]
                selected.append({'cycle': cycle, 'file': deepcopy(upload), 'submitted_at': snapshot['submitted_at'],
                                 'late': bool((cutoff and at > cutoff) or snapshot.get('late')),
                                 'source_response_id': upload['source_response_id'],
                                 'source_form_id': upload['source_form_id'], **sources[upload['id']]['basis']})
            if not selected:
                continue
            value.update(files=[s['file'] for s in selected], combined_components=selected,
                         late=any(s['late'] for s in selected),
                         submitted_at=max(selected, key=lambda s: work.when(s['submitted_at']))['submitted_at'])
        except Held as error:
            value['selection_problem'] = str(error)
        rows.append(value)
    conflicts = {fid for fid, identities in owners.items() if len(identities) != 1}
    for row in rows:
        if any(upload.get('id') in conflicts for snapshot in [row] + row.get('earlier', [])
               for upload in snapshot.get('files', [])):
            row['selection_problem'] = 'The same component file is attributed to different students; confirm its identity.'
    return rows


def bind_form(token, form, scope):
    """Save source-derived selection bases in the read-only work response."""
    state = {'scope': deepcopy(scope), 'scope_digest': digest(scope), 'sources': {}, 'teachers': {},
             'current': [], 'on_time': [], 'problem': None}
    try:
        teachers = {}
        for component in scope['components']:
            capture = read_source(token, component['teacher_id'])
            checked = completion.teacher_readiness(capture['deck'], revision=capture['metadata']['version'],
                                                   require_complete_codes=False)
            if checked['status'] != 'READY' or checked['inventory_cycle'] != component['cycle']:
                raise Held('An exact combined-assignment teacher source is not ready or has the wrong cycle.')
            teachers[component['cycle']] = capture
            state['teachers'][component['cycle']] = source_basis(capture)
        ids = {upload['id'] for row in form.get('students', []) + form.get('late', [])
               if row.get('matched') is True for snapshot in [row] + row.get('earlier', [])
               for upload in snapshot.get('files', []) if upload.get('id')}
        if len(ids) > MAX_SOURCES:
            raise Held('This combined assignment has too many source copies to verify in one read.')
        ordered_ids = sorted(ids)
        with _source_results(lambda fid: read_source(token, fid), ordered_ids) as results:
            for fid, capture in zip(ordered_ids, results):
                state['sources'][fid] = {'cycle': classify(capture, teachers), 'basis': source_basis(capture)}
        state['on_time'] = select_rows(form, scope, state['sources'])
        state['current'] = select_rows(form, scope, state['sources'], include_late=True)
    except (Held, google.GoogleError, completion.Unreadable) as error:
        state['problem'] = str(error)
    form['combined_assessment'] = state
    form['scoring_students'] = deepcopy(state['on_time'])
    if state['problem']:
        form['grade_readiness'] = {'status': 'NOT_READY', 'code': 'coverage_incomplete', 'problem': state['problem']}


def candidates(form, *, include_late=False):
    state = form['combined_assessment']
    if state.get('problem'):
        return [{**row, 'selection_problem': state['problem']} for row in form.get('students', []) + form.get('late', [])]
    return deepcopy(state['current' if include_late else 'on_time'])


def feedback_rows(form):
    """Only the selected on-time original of each cycle, with its own receipt.

    The raw current/earlier histories are never promoted or changed. A file
    superseded within its cycle is not an eligible writing-report source.
    """
    rows = []
    for candidate in candidates(form):
        if candidate.get('selection_problem'):
            rows.append(candidate)
            continue
        for component in candidate.get('combined_components', []):
            rows.append({**deepcopy(candidate), 'files': [deepcopy(component['file'])],
                         'submitted_at': component['submitted_at'], 'late': component['late']})
    return rows


def verify_sources(token, form, row):
    """Current source content and metadata must match the bound selection."""
    state = form['combined_assessment']
    if state.get('problem') or state.get('scope_digest') != digest(state['scope']) or row.get('selection_problem'):
        raise Held(state.get('problem') or row.get('selection_problem') or 'Combined-assignment scope changed.')
    teachers, students = {}, {}
    for component in state['scope']['components']:
        cycle = component['cycle']
        capture = read_source(token, component['teacher_id'])
        if source_basis(capture) != state['teachers'][cycle]:
            raise Held('A combined teacher source changed; reload Student work before entry.')
        checked = completion.teacher_readiness(capture['deck'], require_complete_codes=False)
        if checked['status'] != 'READY' or checked['inventory_cycle'] != cycle:
            raise Held('A combined teacher source is not ready for completion scoring.')
        teachers[cycle] = capture
    selected = row.get('combined_components') or []
    if not selected or len({s['cycle'] for s in selected}) != len(selected):
        raise Held('The combined component selection is missing or repeated.')
    for selected_source in selected:
        fid = selected_source['file_id']
        capture = read_source(token, fid)
        if (source_basis(capture) != state['sources'][fid]['basis']
                or classify(capture, teachers) != selected_source['cycle']):
            raise Held('A selected component changed; reload Student work before scoring or entry.')
        students[selected_source['cycle']] = capture
    return teachers, students


def pooled(scope, components):
    """One rounding of pooled, meaningful actual coded-box counts."""
    counts = {category: {'done': 0, 'required': 0} for category in completion.CATEGORIES}
    items, uncoded, notes = [], [], []
    represented = []
    for component in components:
        cycle, result = component['cycle'], component['result']
        represented.append(cycle)
        if result.get('problem') or result.get('note'):
            notes.append('%s: %s' % (component['label'], result.get('problem') or result['note']))
        for category in completion.CATEGORIES:
            for field in ('done', 'required'):
                counts[category][field] += result['counts'][category][field]
        items.extend({**item, 'cycle': cycle, 'id': cycle + ':' + item['id'],
                      'source_file_id': component['source']['file_id']} for item in result['items'])
        uncoded.extend({**item, 'cycle': cycle, 'source_file_id': component['source']['file_id']}
                       for item in result['uncoded'])
    missing = [c['cycle'] for c in scope['components'] if c['cycle'] not in represented]
    labels = {c['cycle']: c['label'] for c in scope['components']}
    text = ('Both lesson components are represented.' if not missing else
            'Partial coverage: counted %s; no verified %s component. Missing work adds no invented zero or codes.' %
            (', '.join(labels[c] for c in represented), ', '.join(labels[c] for c in missing)))
    return {'code': 'combined', 'inventory': 'found_codes', 'scoring_basis': 'found_codes',
            'notes_score': None if notes else completion.score(counts['NOTES']['done'] + counts['BANK']['done'],
                                                             counts['NOTES']['required'] + counts['BANK']['required']),
            'revision_score': None if notes else completion.score(counts['DRAFT']['done'], counts['DRAFT']['required']),
            'counts': counts, 'items': items, 'uncoded': uncoded, 'note': ' '.join(notes) or None,
            'compared': True, 'matched_by': ['exact registered component teachers'],
            'missing_code_warning': text, 'component_coverage': {'represented': represented, 'missing': missing,
            'both_verified': not missing, 'text': text}, 'components': components,
            'aggregate_provenance': {'scope_digest': digest(scope), 'scope': deepcopy(scope),
                                     'sources': [{k: v for k, v in component.items() if k != 'result'}
                                                 for component in components]}, 'external_writes': 0}


def score_row(token, form, row, *, component_file_id=None):
    try:
        teachers, students = verify_sources(token, form, row)
        scope, components = form['combined_assessment']['scope'], []
        for selected in row['combined_components']:
            if component_file_id and selected['file_id'] != component_file_id:
                continue
            cycle = selected['cycle']
            declared = next(c for c in scope['components'] if c['cycle'] == cycle)
            components.append({'cycle': cycle, 'label': declared['label'],
                'source': {k: v for k, v in selected.items() if k != 'file'},
                'teacher': source_basis(teachers[cycle]),
                'result': completion.assess(students[cycle]['deck'], teachers[cycle]['deck'], found_codes_only=True)})
        if component_file_id:
            # A per-file writing report must not borrow the other deck's
            # revisions, teacher, source timestamp or assessment identity.
            if len(components) != 1:
                raise Held('The feedback source is not one selected component of this assignment.')
            component = components[0]
            return {**component['result'], 'template_id': declared['teacher_id'],
                    'template': teachers[cycle]['metadata'].get('name', ''),
                    'template_updated': teachers[cycle]['metadata'].get('modifiedTime', ''),
                    'file_updated': students[cycle]['metadata'].get('modifiedTime', ''),
                    'component_provenance': {k: v for k, v in component.items() if k != 'result'}}
        return pooled(scope, components)
    except (Held, google.GoogleError, completion.Unreadable) as error:
        return {'code': 'coverage_incomplete', 'problem': str(error),
                **({'provider_status': error.status, 'provider_reason': error.reason}
                   if isinstance(error, google.GoogleError) else {})}
