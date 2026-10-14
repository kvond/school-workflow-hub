"""Private, review-only acknowledgements of exact previously verified originals.

Only a trusted host imports immutable receipts. This module never chooses a
revision, accepts a Form route, assesses work, or writes a school record. An
acknowledged history notice is not a current IC certification or a grade gate.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import re
from zoneinfo import ZoneInfo
from urllib.parse import quote

from . import completion, google, work
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, bounded_json, instant, key
from school_workflow_hub.core.runs import content_hash

CONFIG = 'HUB_SOURCE_HISTORY_DISPOSITIONS'
SCHEMA = 'hub-source-history-disposition/v1'
META = 'id,name,mimeType,trashed,modifiedTime,version,parents,owners,properties,md5Checksum,size'
KINDS = frozenset(('source_history_unordered', 'source_route_resolved', 'source_week_mismatch'))
BASES = frozenset(('manual_saved_history', 'maximum_saved_as_of'))
HASH = re.compile(r'[a-f0-9]{64}\Z')
ID = re.compile(r'[A-Za-z0-9_-]{10,200}\Z')
HISTORY_REASONS = frozenset((
    'Submission history has files without a verified original Form event; confirm the current revision.',
    "The current file's turn-in event cannot be ordered against its replacement; confirm the current revision.",
    'Different submissions have the same latest turn-in time; confirm which revision to score.'))


def issue_kind(reason):
    # Identity conflicts, missing latest uploads, future events and unavailable
    # originals are intentionally not eligible history acknowledgements.
    return 'source_history_unordered' if reason in HISTORY_REASONS else 'source_issue'


def _text(value, limit=200):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and all(ord(c) >= 32 for c in value)


def _hash(value):
    return isinstance(value, str) and bool(HASH.fullmatch(value))


def receipt_key(value):
    return key('sourcehistorydisposition', {k: value[k] for k in value if k != 'record_key'})


def validate(value):
    """Validate the host's complete immutable capture, not browser assertions."""
    bounded_json(value)
    fields = {'schema', 'record_key', 'scope', 'identity', 'roster_sha256', 'candidate_sha256',
              'issue_kind', 'issue_sha256', 'sources', 'basis', 'native_saved', 'instruction_sha256',
              'operator', 'verified_at'}
    if (not isinstance(value, dict) or set(value) != fields or value['schema'] != SCHEMA
            or value['record_key'] != receipt_key(value) or value['issue_kind'] not in KINDS
            or value['basis'] not in BASES or not _text(value['operator'])
            or not re.fullmatch(r'[^@\s]+@[^@\s]+', value['operator'])):
        raise ValueError('source history receipt')
    scope = value['scope']
    if (not isinstance(scope, dict) or set(scope) != {'agenda_id', 'school_year', 'class_key', 'week', 'form_id', 'context_sha256'}
            or any(not ID.fullmatch(str(scope.get(k) or '')) for k in ('agenda_id', 'form_id'))
            or not re.fullmatch(r'\d{4}-\d{2}', str(scope.get('school_year') or ''))
            or not _text(scope.get('class_key')) or not _text(scope.get('week'), 100)):
        raise ValueError('source history scope')
    identity = value['identity']
    if (not isinstance(identity, dict) or set(identity) - {'student_id'} != {'first', 'last', 'section', 'school_email', 'section_id'}
            or any(not _text(identity.get(k)) for k in identity)
            or not re.fullmatch(r'[^@\s]+@[^@\s]+', identity['school_email'])
            or any(not re.fullmatch(r'[0-9]{1,30}', identity[k]) for k in ('section_id',) + (('student_id',) if 'student_id' in identity else ()))):
        raise ValueError('source history canonical identity')
    if any(not _hash(value[k]) for k in ('roster_sha256', 'candidate_sha256', 'issue_sha256', 'instruction_sha256')) or not _hash(scope['context_sha256']):
        raise ValueError('source history evidence hashes')
    sources = value['sources']
    if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise ValueError('source history originals')
    seen = set()
    for source in sources:
        if (not isinstance(source, dict) or set(source) != {'file_id', 'metadata', 'metadata_sha256', 'content_sha256', 'identity_sha256', 'identity_attribution', 'original_event'}
                or not ID.fullmatch(str(source['file_id'])) or source['file_id'] in seen
                or any(not _hash(source[k]) for k in ('metadata_sha256', 'content_sha256', 'identity_sha256'))):
            raise ValueError('source history original proof')
        seen.add(source['file_id'])
        md = source['metadata']
        if (not isinstance(md, dict) or md.get('id') != source['file_id'] or md.get('trashed') is not False
                or md.get('mimeType') not in (completion.SLIDES, google.PPTX)
                or not _text(md.get('name'), 1000) or not _text(md.get('version'), 100)
                or not _text(md.get('modifiedTime'), 100) or content_hash(md) != source['metadata_sha256']):
            raise ValueError('source history exact metadata')
        attribution = source['identity_attribution']
        expected_attribution = {'method': 'teacher_authorized_exact_original', 'file_id': source['file_id'],
            'metadata_sha256': source['metadata_sha256'], 'instruction_sha256': value['instruction_sha256'],
            **{k: identity[k] for k in ('first', 'last', 'section', 'school_email')}}
        if attribution != expected_attribution or content_hash(attribution) != source['identity_sha256']:
            raise ValueError('source history exact-original allocation attribution')
        event = source['original_event']
        allowed = {'source_form_id', 'source_response_id', 'source_submitted_at'}
        if not isinstance(event, dict) or set(event) - allowed:
            raise ValueError('source history original event')
        for k, v in event.items():
            if k == 'source_submitted_at':
                instant(v)
            elif not _text(v):
                raise ValueError('source history original event')
    native = value['native_saved']
    if (not isinstance(native, dict) or set(native) - {'student_id'} != {'verified_at', 'save_verified_at', 'evidence_sha256', 'section_id', 'components', 'targets'}
            or not _hash(native['evidence_sha256']) or native.get('student_id') != identity.get('student_id')
            or native['section_id'] != identity['section_id']):
        raise ValueError('source history native identity')
    verified = instant(value['verified_at'])
    native_at = instant(native['verified_at'])
    if not instant(native['save_verified_at']) <= native_at <= verified:
        raise ValueError('source history native verification time')
    from . import morning_entry
    single_manual = (value['basis'] == 'manual_saved_history'
                     and isinstance(native['targets'], list) and len(native['targets']) == 1)
    if not isinstance(native['targets'], list) or (len(native['targets']) != 2 and not single_manual):
        raise ValueError('source history native targets')
    for target in native['targets']:
        morning_entry._target(target)
    components = native['components']
    if (not isinstance(components, list)
            or (not single_manual and (len(components) != 2 or {c.get('column') for c in components} != {'notes', 'revision'}))
            or (single_manual and (len(components) != 1 or not isinstance(components[0], dict) or components[0].get('column') != native['targets'][0]['column']))):
        raise ValueError('source history exact saved components')
    for component in components:
        expected = {'column', 'assignment_id', 'score_group', 'term_id', 'calendar_id', 'structure_id', 'school_id', 'task_id', 'year', 'points', 'value', 'saved'}
        if (set(component) != expected or component['saved'] is not True
                or any(not re.fullmatch(r'[0-9]{1,30}', str(component.get(k) or ''))
                       for k in expected - {'column', 'points', 'value', 'saved'})
                or any(isinstance(component[k], bool) or not isinstance(component[k], (float, int)) or not math.isfinite(component[k]) for k in ('points', 'value'))
                or component['points'] != 10 or not 0 <= component['value'] <= 10):
            raise ValueError('source history native component')
        if value['basis'] == 'maximum_saved_as_of' and component['value'] != 10:
            raise ValueError('source history maximum evidence')
    if len({c['assignment_id'] for c in components}) != len(components):
        raise ValueError('source history duplicate assignment')
    return deepcopy(value)


def current_basis(agenda, config, current_scope, found, row, roster_snapshot):
    """Exact current review operands; no selected revision or grade is inferred."""
    roster, problems = roster_snapshot
    if problems or not row.get('matched') or any(not _text(row.get(k)) for k in ('first', 'last', 'section')):
        raise ValueError('source history current identity unavailable')
    cls = current_scope['class_key']
    matches = [r for r in roster.get(cls, []) if all(r.get(k) == row[k] for k in ('first', 'last', 'section'))]
    if len(matches) != 1:
        raise ValueError('source history current roster identity ambiguous')
    forms = [f for l in found.get('lessons', []) for f in l.get('forms', []) if f.get('form_id') == current_scope['form_id']]
    if len(forms) != 1:
        raise ValueError('source history current Form ambiguous')
    form = forms[0]
    same = lambda r: all(r.get(k) == row[k] for k in ('first', 'last', 'section'))
    copies = [r for r in form.get('students', []) + form.get('late', []) if same(r)]
    originals = {}
    candidates = []
    for copy in copies:
        for snapshot in [copy] + copy.get('earlier', []):
            files = snapshot.get('files', [])
            candidates.append({k: deepcopy(snapshot.get(k)) for k in ('source', 'email', 'submitted_at', 'matched_by', 'late')})
            candidates[-1]['files'] = deepcopy(files)
            for upload in files:
                ident = upload.get('id')
                if ident:
                    originals[ident] = {k: upload[k] for k in ('source_form_id', 'source_response_id', 'source_submitted_at') if k in upload}
    elsewhere = [item for item in form.get('elsewhere', []) if item.get('who') == '%s, %s' % (row['last'], row['first']) and item.get('section') == row['section']]
    candidates.extend(deepcopy(elsewhere))
    from .review_context import source_link
    for item in elsewhere:
        link = source_link(item.get('url'))
        if link:
            originals.setdefault(link['file_id'], {})
    lessons = agenda.lessons(cls, current_scope['week'])
    if sum(f.get('form_id') == current_scope['form_id'] for l in lessons for f in l.get('forms', [])) != 1:
        raise ValueError('source history current Agenda scope ambiguous')
    maps = json.loads(config.get('HUB_IC_ASSIGNMENT_MAP', '[]'))
    if not isinstance(maps, list):
        raise ValueError('source history current target mapping unavailable')
    mapped = [t for m in maps if m.get('class_key') == cls and m.get('form_id') == current_scope['form_id']
              for t in m.get('targets', []) if t.get('section') == row['section']]
    context = {'scope': current_scope, 'lessons': lessons,
               'IC_assignment_map': [m for m in maps if m.get('class_key') == cls and m.get('form_id') == current_scope['form_id']],
               'teacher_sources': work.form_teacher_sources(config, agenda, cls, current_scope['week'], current_scope['form_id'])}
    legacy_context = deepcopy(context)
    # resource_type is a derived link-rendering hint added after the first
    # receipts. Keep both representations without relaxing any source operand.
    for source in legacy_context['scope'].get('sources', []):
        source.pop('resource_type', None)
    context_hash = content_hash(context)
    return {'scope': {'agenda_id': agenda.spreadsheet_id, 'school_year': work._school_year(agenda, current_scope['week']),
                     'class_key': cls, 'week': current_scope['week'], 'form_id': current_scope['form_id'],
                     'context_sha256': context_hash},
            'scope_compatibility': {'current_context_sha256': context_hash,
                                    'without_resource_type_sha256': content_hash(legacy_context)},
            'identity': {k: row[k] for k in ('first', 'last', 'section')},
            'email': str(row.get('email') or '').strip().lower(), 'roster_sha256': content_hash(roster_snapshot),
            'candidate_sha256': content_hash(candidates), 'original_events': originals, 'mapped_targets': mapped, 'expected_targets': mapped,
            'native_instruction_sha256': None}


def verify_current(value, basis, detail, metadata, *, now=None):
    value = validate(value)
    now = now or datetime.now(timezone.utc)
    compatibility = basis.get('scope_compatibility') or {}
    same_scope = value['scope'] == basis['scope'] or (
        compatibility.get('current_context_sha256') == basis['scope']['context_sha256']
        and value['scope'] == {**basis['scope'], 'context_sha256': compatibility.get('without_resource_type_sha256')})
    if (not same_scope or any(value['identity'][k] != v for k, v in basis['identity'].items())
            or (basis['email'] and value['identity']['school_email'].lower() != basis['email'])
            or value['roster_sha256'] != basis['roster_sha256'] or value['candidate_sha256'] != basis['candidate_sha256']
            or value['issue_kind'] != detail.get('issue_kind') or value['issue_sha256'] != content_hash(detail['reason'])
            or instant(value['verified_at']) > now):
        raise ValueError('source history current operands changed')
    source_ids = {s['file_id'] for s in detail['sources']}
    if {s['file_id'] for s in value['sources']} != source_ids or not source_ids:
        raise ValueError('source history source set changed')
    for source in value['sources']:
        fid = source['file_id']
        if (metadata.get(fid) != source['metadata'] or fid not in basis['original_events']
                or source['original_event'] != basis['original_events'][fid]):
            raise ValueError('source history source fingerprint or event changed')
    from . import morning_entry
    targets = basis['expected_targets']
    single_manual = value['basis'] == 'manual_saved_history' and len(targets) == 1
    if (basis['native_instruction_sha256'] != value['instruction_sha256']
            or targets != value['native_saved']['targets']
            or (not single_manual and (len(targets) != 2 or {t.get('column') for t in targets} != {'notes', 'revision'}))):
        raise ValueError('source history has no exact independently declared native targets')
    if single_manual and targets != basis['mapped_targets']:
        raise ValueError('source history single component has no exact current partial mapping')
    for target in targets:
        morning_entry._target(target)
        if target['section'] != value['identity']['section'] or target['section_id'] != value['identity']['section_id']:
            raise ValueError('source history native target identity changed')
    for component in value['native_saved']['components']:
        target = next(t for t in targets if t['column'] == component['column'])
        if any(str(component[k]) != target[k] for k in
               ('assignment_id', 'score_group', 'term_id', 'calendar_id', 'structure_id', 'school_id', 'task_id', 'year')):
            raise ValueError('source history native destination mapping changed')
    if value['basis'] == 'maximum_saved_as_of':
        if targets != basis['mapped_targets']:
            raise ValueError('source history maximum has no exact current component mapping')
        verified = instant(value['native_saved']['verified_at'])
        zone = ZoneInfo('America/New_York')
        if (not verified <= now <= verified + timedelta(minutes=30)
                or verified.astimezone(zone).date() != now.astimezone(zone).date()):
            raise ValueError('source history maximum readback is no longer fresh')
    return value


def import_verified(port, value, basis, detail, metadata, *, now=None):
    """Trusted-host CAS only. No school action, retry loop or browser endpoint."""
    value = verify_current(value, basis, detail, metadata, now=now)
    ident = value['record_key']
    existing = port.read([ident]).documents.get(ident)
    if existing:
        if existing.value != value:
            raise Conflict('source history immutable record conflict')
        return ident
    port.commit({ident: (value, None)})
    actual = port.read([ident]).documents.get(ident)
    if not actual or actual.value != value:
        raise StoreUnavailable('source history import not confirmed; reread the same key')
    return ident


def _source_metadata(token, file_id, fields=META):
    """Read the provider shape used by immutable source fingerprints."""
    if fields != META or not ID.fullmatch(str(file_id)):
        raise ValueError('source history exact metadata read')
    return google.get_json('https://www.googleapis.com/drive/v3/files/' + quote(file_id, safe=''),
                           token, {'fields': META, 'supportsAllDrives': 'true'}, timeout=10)


def apply(token, config, agenda, current_scope, found, result, roster_snapshot):
    """Read exact configured acknowledgements; never clear a grading hold."""
    raw = config.get(CONFIG)
    if not raw:
        return result
    out = deepcopy(result)
    out['retained_history'] = []
    try:
        entries = json.loads(raw)
        if (not isinstance(entries, list) or len(entries) > 200
                or any(not isinstance(e, dict) or set(e) != {'class_key', 'week', 'form_id', 'record_key', 'native_targets', 'instruction_sha256'} for e in entries)):
            raise ValueError('source history configuration')
        selected_entries = [e for e in entries if (e['class_key'], e['week'], e['form_id']) ==
                    (current_scope['class_key'], current_scope['week'], current_scope['form_id'])]
        selected = [e['record_key'] for e in selected_entries]
        pointers = {e['record_key']: e for e in selected_entries}
        if not selected:
            return result
        if len(selected) > 40 or len(selected) != len(set(selected)) or any(not re.fullmatch(r'sourcehistorydisposition_[a-f0-9]{64}', str(k)) for k in selected):
            raise ValueError('source history bounded exact keys')
        values = configured_store(config).read(selected).documents
        form = next(f for l in found['lessons'] for f in l['forms'] if f['form_id'] == current_scope['form_id'])
        rows = form.get('students', []) + form.get('late', []) + form.get('missing', []) + form.get('excused', [])
        remaining, reads = [], {}
        for detail in out['notices']:
            person = detail.get('student') or {}
            if detail.get('issue_kind') not in KINDS or person.get('verified') is not True or detail.get('affected_scope') != 'student':
                remaining.append(detail); continue
            matches = [r for r in rows if all(r.get(k) == person.get(k) for k in ('first', 'last', 'section'))]
            if len(matches) != 1:
                remaining.append(detail); continue
            row = {**matches[0], 'matched': True}
            basis = current_basis(agenda, config, current_scope, found, row, roster_snapshot)
            verified = None
            for ident in selected:
                doc = values.get(ident)
                if not doc or not isinstance(doc.value, dict) or doc.value.get('record_key') != ident or doc.value.get('issue_kind') != detail['issue_kind']:
                    continue
                try:
                    ids = [s['file_id'] for s in detail['sources']]
                    for fid in ids:
                        if fid not in reads:
                            before = _source_metadata(token, fid, META)
                            after = _source_metadata(token, fid, META)
                            if before != after:
                                raise ValueError('source history metadata changed during review')
                            reads[fid] = before
                    supplied = {**basis, 'expected_targets': pointers[ident]['native_targets'],
                                'native_instruction_sha256': pointers[ident]['instruction_sha256']}
                    verified = verify_current(doc.value, supplied, detail, reads)
                except (ValueError, TypeError, KeyError):
                    continue
                break
            if not verified:
                remaining.append(detail); continue
            retained = {k: deepcopy(v) for k, v in detail.items() if k != 'next_action'}
            at = verified['native_saved']['verified_at']
            retained.update(kind='retained_history', verified_at=at, basis=verified['basis'],
                reason=('Saved entry verified %s; source history retained.' if verified['basis'] == 'manual_saved_history'
                        else 'Both 10-point components were verified at their maximum %s; no new entry was needed as of that read. Source history retained.') % at,
                source_basis={'kind': 'private_source_history_disposition', 'record_key': verified['record_key'],
                              'receipt_sha256': content_hash(verified)})
            out['retained_history'].append(retained)
        if out['retained_history'] and any(_source_metadata(token, fid, META) != before for fid, before in reads.items()):
            raise ValueError('source history final source bookend changed')
        out['notices'] = remaining
        if out['retained_history'] and not remaining:
            out['status'] = 'history_retained'
        return out
    except (google.GoogleError, StoreUnavailable) as error:
        from .review_context import _unavailable
        return _unavailable(current_scope, 'Previously verified source history could not be read: ' + str(error))
    except (ValueError, TypeError, KeyError, StopIteration):
        # Partial acknowledgement/availability failures never hide a source
        # issue or fabricate missing-work/identity questions.
        original = deepcopy(result)
        original['limitations'].append('Previously verified source history could not be checked in this read; current source notices remain visible.')
        return original
