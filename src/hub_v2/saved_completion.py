"""Read-only views of existing completion results, never another assessor.

Existing scoring requests may retain a bounded summary in the existing private
CAS store. Recording is best effort and cannot change a Helper result. Readers
fresh-check source metadata and the current assignment/roster basis; they never
export a deck, calculate a score, or turn missing evidence into zero.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import math
import re
from threading import Lock, Thread

from . import completion, google
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import bounded_json, key
from school_workflow_hub.core.runs import content_hash

SCHEMA = 'hub-saved-completion/v1'
POLICY = 'saved-completion-summary/2026-10-21/v1'
_config = ContextVar('saved_completion_config', default=None)
_pending = ContextVar('saved_completion_pending', default=None)
_queue_lock = Lock()
_queue = {}
_worker = None
MAX_SOURCES = 300
MAX_QUEUED = 600
_UNSET = object()
_ID = re.compile(r'[A-Za-z0-9_-]{10,200}')
_TRANSIENT = {'grade_readiness', 'combined_assessment', 'scoring_students',
              'roster_check', 'read_timeouts'}
_POLICY_SETTINGS = (
    'HUB_SCHOOL_EMAIL_DOMAINS', 'HUB_EMAIL_USERNAME_PREFIX',
    'HUB_ROSTER_SPREADSHEET_ID', 'HUB_ROSTER_TABS', 'HUB_ROSTER_COLUMN_LAYOUTS',
    'HUB_FORM_TEACHER_SOURCES', 'HUB_COMBINED_ASSESSMENTS',
    'HUB_ASSIGNMENT_EXCLUSIONS', 'HUB_TURNIN_FORM_ID', 'HUB_IC_ASSIGNMENT_MAP',
    'HUB_INTAKE_DECK_ASSIGNMENTS', 'HUB_ASSIGNMENT_FORM_IDS',
    'HUB_SOURCE_HISTORY_DISPOSITIONS', 'K_REVISION',
)
_MESSAGES = {
    'not_graded': 'No saved completion result is available for this exact work. The dashboard does not run grading.',
    'stale': 'The source, lesson, roster or grading context changed. A current result is not verified.',
    'unavailable': 'The saved completion result could not be verified. No score is inferred.',
}


@contextmanager
def recording(config, *, defer=False):
    """Pass explicit request configuration into copied scoring-worker contexts."""
    token = _config.set(config)
    pending = _pending.get()
    owner = pending is None
    pending = pending if pending is not None else ({}, Lock())
    pending_token = _pending.set(pending)
    try:
        yield
    finally:
        if owner:
            if defer:
                _enqueue(config, pending[0])
            else:
                _flush(config, pending[0])
        _pending.reset(pending_token)
        _config.reset(token)


def _enqueue(config, values):
    """Optional display caching never waits for CAS/auth on the Helper request.

    One daemon processes at most forty records per batch; pending records are
    bounded and coalesced. Cloud Run shutdown may drop this optional cache.
    Readers then say no saved result rather than inferring a grade.
    """
    global _worker
    if not values:
        return
    try:
        state_config = {name: config[name] for name in (
            'HUB_STATE_BACKEND', 'HUB_STATE_SERVICE_ACCOUNT', 'HUB_STATE_PROJECT', 'HUB_STATE_DATABASE')
            if name in config}
        namespace = content_hash(state_config)
        with _queue_lock:
            for ident, value in values.items():
                queue_key = (namespace, ident)
                if queue_key in _queue or len(_queue) < MAX_QUEUED:
                    _queue[queue_key] = (state_config, value)
            if _worker is None and _queue:
                _worker = Thread(target=_drain, name='hub-display-cache', daemon=True)
                _worker.start()
    except Exception:
        pass


def _drain():
    global _worker
    while True:
        with _queue_lock:
            if not _queue:
                _worker = None
                return
            namespace = next(iter(_queue))[0]
            batch = [key for key in _queue if key[0] == namespace][:40]
            values, config = {}, None
            for queue_key in batch:
                config, value = _queue.pop(queue_key)
                values[queue_key[1]] = value
        _flush(config, values)


def _flush(config, values):
    """One bounded batch; failures never retry or change a completed operation."""
    if not values:
        return
    if len(values) > 40:
        items = list(values.items())
        for offset in range(0, len(items), 40):
            _flush(config, dict(items[offset:offset + 40]))
        return
    try:
        port = configured_store(config)
        snapshot = port.read(list(values))
        changes = {}
        for ident, value in values.items():
            old = snapshot.documents[ident]
            if old and all(old.value.get(name) == value[name] for name in ('scope', 'basis', 'provenance', 'result')):
                continue
            changes[ident] = ({**value, 'saved_at': snapshot.read_time}, old.version if old else None)
        if changes:
            port.commit(changes)
    except Exception:
        pass


def _plain(value):
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if k not in _TRANSIENT}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, set):
        return sorted((_plain(v) for v in value), key=repr)
    return value


def _scope(agenda, class_key, week, form_id, file_id):
    agenda_id = getattr(agenda, 'spreadsheet_id', '')
    if (class_key not in ('biology', 'forensics', 'anatomy')
            or not all(isinstance(v, str) and _ID.fullmatch(v) for v in (agenda_id, form_id, file_id))
            or not isinstance(week, str) or not 1 <= len(week) <= 100):
        raise ValueError('scope')
    return {'agenda': agenda_id, 'class': class_key, 'week': week,
            'form': form_id, 'file': file_id}


def _combined_scope(config, agenda, scope):
    if not config.get('HUB_COMBINED_ASSESSMENTS'):
        return None
    from . import combined_assessment as combined
    return combined.definition(config, agenda, scope['class'], scope['week'], scope['form'])


def _basis(config, agenda, scope, found):
    if (not isinstance(found, dict) or found.get('problems') or found.get('other_files')
            or found.get('assessment_problem') or found.get('review_unavailable')
            or (found.get('week_policy') or {}).get('problem')):
        raise ValueError('coverage')
    forms = [form for lesson in found.get('lessons', []) for form in lesson.get('forms', [])
             if form.get('form_id') == scope['form']]
    if len(forms) != 1 or forms[0].get('problem'):
        raise ValueError('form')
    form = forms[0]
    if (form.get('grade_policy') or {}).get('excluded'):
        raise ValueError('unsupported')
    rows = form.get('students', []) + form.get('late', [])
    aggregate = _combined_scope(config, agenda, scope) is not None
    selected = [row for row in rows if row.get('matched') is True and
                any(file.get('id') == scope['file']
                    for snapshot in ([row] + row.get('earlier', []) if aggregate else [row])
                    for file in snapshot.get('files', []))]
    if len(selected) != 1 or selected[0].get('selection_problem'):
        raise ValueError('selection')
    if any(row.get('selection_problem') for row in rows):
        raise ValueError('selection')
    lessons = agenda.lessons(scope['class'], scope['week'])
    basis = {'policy': POLICY, 'scope': scope,
             'agenda': _plain(lessons), 'work': _plain(found),
             'settings': {name: config.get(name, '') for name in _POLICY_SETTINGS}}
    bounded_json(basis)
    return content_hash(basis)


def _summary(result):
    if (not isinstance(result, dict) or result.get('problem') or result.get('note')
            or not isinstance(result.get('counts'), dict)):
        raise ValueError('result')
    counts = {}
    for category in ('NOTES', 'BANK', 'DRAFT'):
        value = result['counts'].get(category)
        if not isinstance(value, dict):
            raise ValueError('counts')
        done, required = value.get('done'), value.get('required')
        if (type(done) is not int or type(required) is not int
                or not 0 <= done <= required <= 10000):
            raise ValueError('counts')
        counts[category] = {'done': done, 'required': required}
    out = {'counts': counts}
    for name in ('notes_score', 'revision_score'):
        value = result.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or not 0 <= value <= 10):
            raise ValueError('score')
        out[name] = value
    for name in ('code', 'scoring_basis', 'file_updated', 'template_id', 'template_updated'):
        value = result.get(name, '')
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError('field')
        out[name] = value
    if result.get('component_coverage') is not None:
        coverage = result['component_coverage']
        if (not isinstance(coverage, dict) or type(coverage.get('both_verified')) is not bool
                or any(not isinstance(coverage.get(name), list) or len(coverage[name]) > 4
                       or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', str(v)) for v in coverage[name])
                       for name in ('represented', 'missing'))
                or not isinstance(coverage.get('text'), str) or len(coverage['text']) > 1000):
            raise ValueError('coverage')
        out['component_coverage'] = deepcopy(coverage)
    # No answers, quotations, teacher prose or student names enter this cache.
    return out


def _combined_provenance(config, agenda, scope, found, result):
    from . import combined_assessment as combined
    declared = combined.definition(config, agenda, scope['class'], scope['week'], scope['form'])
    proof = result.get('aggregate_provenance')
    if (not declared or not isinstance(proof, dict) or proof.get('scope') != declared
            or proof.get('scope_digest') != combined.digest(declared)):
        raise ValueError('combined scope')
    sources = proof.get('sources')
    if not isinstance(sources, list) or not 1 <= len(sources) <= 4:
        raise ValueError('combined sources')
    teachers = {entry['cycle']: entry['teacher_id'] for entry in declared['components']}
    captures, cycles = [], set()
    for component in sources:
        cycle = component.get('cycle')
        source, teacher = component.get('source'), component.get('teacher')
        if cycle not in teachers or cycle in cycles or not isinstance(source, dict) or not isinstance(teacher, dict):
            raise ValueError('combined component')
        cycles.add(cycle)
        if teacher.get('file_id') != teachers[cycle]:
            raise ValueError('combined teacher')
        for role, value in (('source', source), ('teacher', teacher)):
            if (not _ID.fullmatch(str(value.get('file_id', ''))) or not value.get('version')
                    or not value.get('modified_at')
                    or not re.fullmatch(r'[a-f0-9]{64}', str(value.get('metadata_sha256', '')))):
                raise ValueError('combined capture')
            captures.append({'role': role, 'cycle': cycle, 'file': value['file_id'],
                'version': value['version'], 'modified_at': value['modified_at'],
                'metadata_sha256': value['metadata_sha256']})
    if scope['file'] not in {capture['file'] for capture in captures if capture['role'] == 'source'}:
        raise ValueError('combined anchor')
    return {'kind': 'combined', 'declaration': declared, 'captures': captures}


def _provenance(result, file_id):
    matches = [(source, cached) for source, cached in list(completion._results.items())
               if source[0] == file_id and source[1] == result.get('file_updated')
               and source[2] and cached.get('template', ('',))[0] == result.get('template_id')
               and _summary({**cached.get('result', {}), 'file_updated': source[1]}) == _summary(result)]
    if len(matches) != 1:
        raise ValueError('provenance')
    source, cached = matches[0]
    teacher = cached['template']
    if len(teacher) != 3 or not teacher[2]:
        raise ValueError('teacher')
    return {'source': list(source), 'teacher': list(teacher),
            'student_coding_required': cached.get('student_coding_required')}


def retain(token, agenda, class_key, week, form_id, file_id, found, result):
    """Best effort: return the original result exactly, including on store failure."""
    config = _config.get()
    if config is None or config.get('HUB_STATE_BACKEND') != 'firestore':
        return result
    try:
        summary = _summary(result)
        scope = _scope(agenda, class_key, week, form_id, file_id)
        provenance = (_combined_provenance(config, agenda, scope, found, result)
                      if result.get('aggregate_provenance') else _provenance(result, file_id))
        pending = _pending.get()
        if pending is None:
            return result
        aliases = ({capture['file'] for capture in provenance['captures'] if capture['role'] == 'source'}
                   if provenance.get('kind') == 'combined' else {file_id})
        computed_at = datetime.now(timezone.utc).isoformat()
        for alias in aliases:
            alias_scope = {**scope, 'file': alias}
            ident = key('savedcompletion', alias_scope)
            value = {'schema': SCHEMA, 'scope': alias_scope, 'basis': _basis(config, agenda, alias_scope, found),
                     'provenance': provenance, 'result': summary, 'computed_at': computed_at}
            bounded_json(value)
            with pending[1]:
                if ident in pending[0] or len(pending[0]) < MAX_SOURCES:
                    pending[0][ident] = value
    except Exception:
        pass
    return result


def _missing(file_id, status, saved_at=None):
    return {'file_id': file_id, 'status': status, 'saved_at': saved_at,
            'problem': _MESSAGES[status]}


def _fresh_metadata(token, capture, reader=None):
    if (not isinstance(capture, list) or len(capture) != 3
            or not all(isinstance(value, str) and value for value in capture)
            or not _ID.fullmatch(capture[0])):
        raise ValueError('metadata')
    meta = (reader or google.drive_file)(token, capture[0], google.FILE_FIELDS + ',version')
    if (meta.get('id') != capture[0] or meta.get('trashed') is not False
            or not meta.get('mimeType') or not isinstance(meta.get('parents'), list)):
        raise ValueError('metadata')
    return meta


def _verify_combined(token, config, agenda, scope, proof, reader=None):
    from . import combined_assessment as combined
    declaration = combined.definition(config, agenda, scope['class'], scope['week'], scope['form'])
    captures = proof.get('captures')
    if (proof.get('declaration') != declaration or not isinstance(captures, list)
            or not 2 <= len(captures) <= 8 or len(captures) % 2):
        return False
    wanted = {entry['cycle']: entry['teacher_id'] for entry in declaration['components']}
    seen, metadata = set(), {}
    def current(fid):
        if reader is None:
            return combined._source_metadata(token, fid)
        value = reader(token, fid, combined.SOURCE_FIELDS)
        if (value.get('id') != fid or value.get('trashed') is not False
                or value.get('mimeType') not in (completion.SLIDES, google.PPTX) or not value.get('version')):
            raise ValueError('combined metadata')
        return value
    for capture in captures:
        role, cycle, fid = capture.get('role'), capture.get('cycle'), capture.get('file')
        if (role not in ('source', 'teacher') or cycle not in wanted or (role, cycle) in seen
                or (role == 'teacher' and fid != wanted[cycle])):
            return False
        seen.add((role, cycle))
        meta = current(fid)
        metadata[(role, cycle)] = meta
        if (str(meta.get('version')) != capture.get('version')
                or meta.get('modifiedTime', '') != capture.get('modified_at')
                or combined.digest(meta) != capture.get('metadata_sha256')):
            return False
    cycles = {cycle for _, cycle in seen}
    if seen != {(role, cycle) for role in ('source', 'teacher') for cycle in cycles}:
        return False
    if scope['file'] not in {capture['file'] for capture in captures if capture['role'] == 'source'}:
        return False
    for capture in captures:
        if current(capture['file']) != metadata[(capture['role'], capture['cycle'])]:
            return False
    return True


def read(token, config, agenda, class_key, week, form_id, file_id, found, *,
         _document=_UNSET, _metadata_reader=None):
    """Return only a verified saved summary; metadata reads never score/export."""
    if config.get('HUB_STATE_BACKEND') != 'firestore':
        return _missing(file_id, 'unavailable')
    saved_at = None
    try:
        scope = _scope(agenda, class_key, week, form_id, file_id)
        doc = (_document if _document is not _UNSET else
               configured_store(config).read([key('savedcompletion', scope)]).documents[key('savedcompletion', scope)])
        if doc is None:
            return _missing(file_id, 'not_graded')
        saved = doc.value
        if saved.get('schema') != SCHEMA or saved.get('scope') != scope:
            raise ValueError('record')
        stamp = saved.get('saved_at')
        if (not isinstance(stamp, str) or len(stamp) > 60
                or datetime.fromisoformat(stamp.replace('Z', '+00:00')).tzinfo is None):
            raise ValueError('time')
        saved_at = stamp
        if saved.get('basis') != _basis(config, agenda, scope, found):
            return _missing(file_id, 'stale', saved_at)
        summary = _summary(saved.get('result'))
        captures = saved.get('provenance') or {}
        if captures.get('kind') == 'combined':
            if not _verify_combined(token, config, agenda, scope, captures, _metadata_reader):
                return _missing(file_id, 'stale', saved_at)
            return {**deepcopy(summary), 'file_id': file_id, 'status': 'saved',
                    'saved_at': saved_at, 'computed_at': saved.get('computed_at')}
        metadata = {}
        for name in ('source', 'teacher'):
            capture = captures.get(name)
            meta = _fresh_metadata(token, capture, _metadata_reader)
            metadata[name] = meta
            if [meta['id'], meta.get('modifiedTime', ''), str(meta.get('version') or '')] != capture:
                return _missing(file_id, 'stale', saved_at)
        # Bracket metadata access: a concurrently replaced source is not current.
        for name in ('source', 'teacher'):
            capture = captures[name]
            meta = _fresh_metadata(token, capture, _metadata_reader)
            if (meta != metadata[name]
                    or [meta['id'], meta.get('modifiedTime', ''), str(meta.get('version') or '')] != capture):
                return _missing(file_id, 'stale', saved_at)
        if captures['source'][0] != file_id or captures['teacher'][0] != summary['template_id']:
            raise ValueError('capture')
        return {**deepcopy(summary), 'file_id': file_id, 'status': 'saved',
                'saved_at': saved_at, 'computed_at': saved.get('computed_at')}
    except Exception:
        return _missing(file_id, 'unavailable', saved_at)


def read_many(token, config, agenda, class_key, week, form_id, found):
    """One verified discovery serves the whole Form; bounded CAS/metadata reads.

    Metadata is shared only inside this batch and independently reread after
    every summary has been checked. No source bytes, readiness or assessment
    are needed to display already-computed values.
    """
    response = {'schema_version': 'hub-saved-completion-list/v1', 'status': 'unavailable',
                'checked_at': datetime.now(timezone.utc).isoformat(), 'results': {}}
    if config.get('HUB_STATE_BACKEND') != 'firestore':
        return response
    try:
        if (not isinstance(found, dict) or found.get('problems') or found.get('other_files')
                or found.get('assessment_problem') or found.get('review_unavailable')
                or (found.get('week_policy') or {}).get('problem')):
            return response
        forms = [form for lesson in found.get('lessons', []) for form in lesson.get('forms', [])
                 if form.get('form_id') == form_id]
        if (len(forms) != 1 or forms[0].get('problem')
                or (forms[0].get('grade_policy') or {}).get('problem')
                or (forms[0].get('grade_policy') or {}).get('excluded')):
            return response
        scope = {'class': class_key, 'week': week, 'form': form_id}
        aggregate = _combined_scope(config, agenda, scope) is not None
        files = sorted({file['id'] for row in forms[0].get('students', []) + forms[0].get('late', [])
                        for snapshot in ([row] + row.get('earlier', []) if aggregate else [row])
                        for file in snapshot.get('files', []) if _ID.fullmatch(str(file.get('id', '')))})
        if len(files) > MAX_SOURCES:
            response['problem'] = 'This assignment exceeds the bounded saved-result read. No displayed score is inferred.'
            return response
        scopes = {fid: _scope(agenda, class_key, week, form_id, fid) for fid in files}
        ids = {fid: key('savedcompletion', scope) for fid, scope in scopes.items()}
        port, documents = configured_store(config), {}
        for offset in range(0, len(files), 40):
            expected = [ids[fid] for fid in files[offset:offset + 40]]
            snapshot = port.read(expected)
            if set(snapshot.documents) != set(expected):
                return response
            documents.update(snapshot.documents)
        metadata = {}
        def reader(read_token, fid, fields):
            meta_key = (fid, fields)
            if meta_key not in metadata:
                metadata[meta_key] = google.drive_file(read_token, fid, fields)
            return deepcopy(metadata[meta_key])
        results = {fid: read(token, config, agenda, class_key, week, form_id, fid, found,
                            _document=documents[ids[fid]], _metadata_reader=reader) for fid in files}
        for (fid, fields), before in metadata.items():
            if google.drive_file(token, fid, fields) != before:
                response['results'] = {fid: _missing(fid, 'stale') for fid in files}
                return response
        response.update(status='available', results=results)
        return response
    except Exception:
        return response
