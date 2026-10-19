"""Exact, private teacher-material receipts; never infer material from email.

Only a trusted host can register a content-reviewed artifact. No HTTP route
accepts these receipts. Discovery rechecks the original event and revision and
removes only that file from student grouping, retaining it as information.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import re

from . import google
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, bounded_json, instant, key
from school_workflow_hub.core.runs import content_hash

SCHEMA = 'hub-teacher-material/v1'
META = 'id,name,mimeType,trashed,version,modifiedTime,parents,md5Checksum,size'
SLIDES = 'application/vnd.google-apps.presentation'
ID = re.compile(r'[A-Za-z0-9_-]{10,200}\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')


class Unverified(ValueError):
    pass


def teachers(config):
    return {x.strip().lower() for x in config.get('HUB_ALLOWED_EMAILS', '').split(',') if x.strip()}


def scope(agenda, config, cls, week, form_id):
    from . import work
    lessons = [l for l in agenda.lessons(cls, week) if any(f.get('form_id') == form_id for f in l.get('forms', []))]
    if len(lessons) != 1 or sum(f.get('form_id') == form_id for f in lessons[0]['forms']) != 1:
        raise Unverified('Exact teacher-material Agenda binding is unavailable.')
    year = work._school_year(agenda, week)
    if not year:
        raise Unverified('Teacher-material school year is unavailable.')
    return {'agenda_id': agenda.spreadsheet_id, 'class_key': cls, 'week': week, 'school_year': year,
            'form_id': form_id, 'context_sha256': content_hash({'lessons': lessons,
                'teacher_sources': work.form_teacher_sources(config, agenda, cls, week, form_id)})}


def index_key(current_scope, response_id, file_id):
    return key('teachermaterialindex', {'agenda_id': current_scope['agenda_id'], 'class_key': current_scope['class_key'],
                                  'form_id': current_scope['form_id'], 'response_id': response_id, 'file_id': file_id})


def record_key(proof):
    return key('teachermaterial', proof)


def _event(token, form_id, response_id, file_id, config):
    # The single-response endpoint does not truncate a paginated Form scan.
    event = google.get_json('https://forms.googleapis.com/v1/forms/%s/responses/%s' % (form_id, response_id), token)
    if (event.get('responseId') != response_id or str(event.get('respondentEmail') or '').lower() not in teachers(config)):
        raise Unverified('The exact original teacher Form event is not verified.')
    when = event.get('lastSubmittedTime') or event.get('createTime')
    instant(when)
    files = [f for a in event.get('answers', {}).values() for f in a.get('fileUploadAnswers', {}).get('answers', [])]
    if sum(f.get('fileId') == file_id for f in files) != 1:
        raise Unverified('The original response does not declare this exact artifact once.')
    return {'response_id': response_id, 'respondent': event['respondentEmail'].lower(),
            'submitted_at': when, 'event_sha256': content_hash(event)}


def capture(token, config, agenda, cls, week, form_id, response_id, file_id):
    """Read the exact proof for host content review; performs no write."""
    if any(not ID.fullmatch(str(x)) for x in (form_id, response_id, file_id)):
        raise Unverified('Invalid teacher-material source identity.')
    current_scope = scope(agenda, config, cls, week, form_id)
    event = _event(token, form_id, response_id, file_id, config)
    md = google.drive_file(token, file_id, META)
    if (md.get('id') != file_id or md.get('trashed') is not False or md.get('mimeType') not in (SLIDES, google.PPTX)
            or not md.get('version') or not md.get('modifiedTime') or not md.get('name')):
        raise Unverified('Teacher-material metadata/revision is incomplete or unavailable.')
    if md['mimeType'] == SLIDES:
        # Slides revisionId and image contentUrl are temporary API values.
        # Reuse the full archive proof, including decoded image content, and
        # bind it to the stable Drive revision bookends below.
        from . import student_archive
        try:
            digest = student_archive._native_presentation_proof(token, file_id)['sha256']
        except student_archive.Held:
            raise Unverified('Teacher-material native content is incomplete.') from None
    else:
        data = google.deck_bytes(token, file_id, md['mimeType'])
        if not data:
            raise Unverified('Teacher-material content is empty or unavailable.')
        digest = hashlib.sha256(data).hexdigest()
    from . import agenda as agenda_module
    fresh_agenda = agenda_module.load(token, agenda.spreadsheet_id)
    if (scope(fresh_agenda, config, cls, week, form_id) != current_scope
            or google.drive_file(token, file_id, META) != md or _event(token, form_id, response_id, file_id, config) != event):
        raise Unverified('Teacher material changed during its verification.')
    return {'scope': current_scope, 'event': event, 'file_id': file_id, 'metadata': md, 'content_sha256': digest}


def validate(value, config):
    bounded_json(value)
    fields = {'schema', 'record_key', 'proof', 'classification', 'evidence', 'instruction_sha256', 'operator', 'verified_at'}
    if (not isinstance(value, dict) or set(value) != fields or value['schema'] != SCHEMA
            or value['classification'] != 'verified_teacher_material'
            or value['operator'] not in teachers(config) or not HASH.fullmatch(str(value['instruction_sha256']))
            or not isinstance(value['evidence'], str) or not 20 <= len(value['evidence']) <= 4000
            or instant(value['verified_at']) > datetime.now(timezone.utc)):
        raise Unverified('Teacher-material receipt is not a complete host verification.')
    proof = value['proof']
    if (not isinstance(proof, dict) or set(proof) != {'scope', 'event', 'file_id', 'metadata', 'content_sha256'}
            or not HASH.fullmatch(str(proof['content_sha256']))
            or proof['event'].get('respondent') not in teachers(config)
            or value['record_key'] != record_key(proof)):
        raise Unverified('Teacher-material receipt does not name one exact artifact.')
    return value


def import_verified(token, config, agenda, value, *, port=None):
    """Trusted-host immutable CAS, with current original proof and readback."""
    validate(value, config)
    proof = value['proof']; s = proof['scope']
    fresh = capture(token, config, agenda, s['class_key'], s['week'], s['form_id'], proof['event']['response_id'], proof['file_id'])
    if fresh != proof:
        raise Unverified('Teacher-material proof changed since content review.')
    port = port if port is not None else configured_store(config)
    ident = value['record_key']
    index_id = index_key(s, proof['event']['response_id'], proof['file_id'])
    docs = port.read([ident,index_id]).documents
    old, index = docs.get(ident), docs.get(index_id)
    if old and old.value != value:
        raise Conflict('Teacher-material immutable receipt already exists with different proof.')
    keys = list((index.value if index else {}).get('records', []))
    if any(not isinstance(x,str) or not x.startswith('teachermaterial_') for x in keys) or len(keys)>50:
        raise Unverified('Teacher-material index is invalid.')
    expected_index = {'schema':SCHEMA,'records':list(dict.fromkeys(keys+[ident]))}
    if len(expected_index['records']) > 50:
        raise Unverified('Teacher-material history is full; preserve it before registering another revision.')
    changes = {} if old else {ident:(value,None)}
    if not index or index.value != expected_index:
        changes[index_id] = (expected_index,index.version if index else None)
    if changes:
        port.commit(changes)
    saved = port.read([ident,index_id]).documents
    if (not saved.get(ident) or saved[ident].value != value
            or not saved.get(index_id) or saved[index_id].value != expected_index):
        raise StoreUnavailable('Teacher-material registration readback is unconfirmed.')
    return ident


def classify_entries(token, config, agenda, class_key, entries):
    """Stage all verified per-file removals, then apply them to these local rows.

    Missing or changed proof preserves the ordinary source checks. Provider or
    state failures preserve every row and return an availability problem.
    """
    if config.get('HUB_STATE_BACKEND') != 'firestore':
        return None
    plans = []
    try:
        port = configured_store(config)
        for entry in entries:
            form_id = entry.get('form', {}).get('form_id') or entry.get('form_id')
            for submitted in entry['found']:
                if submitted.get('source') != 'form' or submitted.get('email') not in teachers(config):
                    continue
                for upload in submitted.get('files', []):
                    response_id, file_id = submitted.get('key'), upload.get('id')
                    if any(not ID.fullmatch(str(x)) for x in (form_id, response_id, file_id)):
                        continue
                    current_scope = scope(agenda, config, class_key, entry['week'], form_id)
                    index_id = index_key(current_scope, response_id, file_id)
                    index = port.read([index_id]).documents.get(index_id)
                    if not index:
                        continue
                    keys = index.value.get('records', [])
                    if (index.value.get('schema') != SCHEMA or not isinstance(keys,list) or not 1<=len(keys)<=50
                            or any(not isinstance(x,str) or not x.startswith('teachermaterial_') for x in keys)):
                        raise StoreUnavailable('Invalid teacher-material index.')
                    try:
                        proof = capture(token, config, agenda, class_key, entry['week'], form_id, response_id, file_id)
                        ident = record_key(proof)
                        if ident not in keys:
                            continue
                        stored = port.read([ident]).documents.get(ident)
                        if not stored:
                            raise StoreUnavailable('Indexed teacher-material proof is unavailable.')
                        value = validate(stored.value, config)
                        if (proof != value['proof'] or proof['event']['respondent'] != submitted['email']
                                or proof['event']['submitted_at'] != submitted.get('submitted_at')):
                            continue
                    except (Unverified, ValueError, KeyError, TypeError):
                        # A registered proof that cannot be checked is an
                        # availability failure, never an identity question.
                        raise StoreUnavailable('Registered teacher-material proof cannot be verified.') from None
                    plans.append((entry, submitted, upload, ident, proof))
        if plans:
            # A later artifact can take time to read. Recheck all earlier
            # staged originals before excluding any of them from this batch.
            from . import agenda as agenda_module
            fresh_agenda = agenda_module.load(token, agenda.spreadsheet_id)
            for entry, submitted, upload, ident, proof in plans:
                s = proof['scope']; event = proof['event']
                if (scope(fresh_agenda, config, class_key, entry['week'], s['form_id']) != s
                        or google.drive_file(token, proof['file_id'], META) != proof['metadata']
                        or _event(token, s['form_id'], event['response_id'], proof['file_id'], config) != event):
                    raise Unverified('Teacher material changed before the batch was applied.')
    except (google.GoogleError, StoreUnavailable, Unverified, ValueError, KeyError, TypeError):
        return 'The exact teacher-material verification is unavailable. Originals are preserved; no identity decision or grade is inferred.'
    for entry, submitted, upload, ident, proof in plans:
        # Never remove a whole response: mixed teacher/student uploads survive.
        submitted['files'].remove(upload)
        if entry.get('current'):
            info = deepcopy(submitted)
            info.update(files=[{**upload, 'url': 'https://drive.google.com/open?id=' + upload['id']}],
                        teacher_upload=True, verification_record=ident, earlier=[])
            entry['form'].setdefault('teacher_uploads', []).append(info)
        if not submitted['files']:
            entry['found'].remove(submitted)
    return None
