"""Private exact-source teaching roles for read-only readiness, not assessment.

Only a trusted host imports a teacher's decision. No HTTP writer or scheduler.
A record never changes Forms, assignment ownership, grades or source pointers.
"""
import re
from . import completion, google, work
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, bounded_json, key
from school_workflow_hub.core.runs import content_hash

SCHEMA = 'hub-readiness-source-role/v1'
FIELDS = google.FILE_FIELDS + ',version'
META = ('id', 'name', 'mimeType', 'trashed', 'modifiedTime', 'version', 'parents')
HASH = re.compile(r'[a-f0-9]{64}\Z')


def _scope(token, agenda, cls, week, row, source, config, *, verify_membership=True):
    lessons = [l for l in agenda.lessons(cls, week) if l.get('row') == row]
    year = work._school_year(agenda, week)
    if len(lessons) != 1 or not year or not isinstance(row, int) or isinstance(row, bool):
        raise ValueError('The exact current Agenda source scope is unavailable.')
    lesson = lessons[0]
    linked = {m.group(1) for item in lesson['materials']
              if (m := re.search(r'/(?:d|folders)/([A-Za-z0-9_-]{10,200})(?:/|$|\?)', item.get('url', '')))}
    membership = []
    if source not in linked and verify_membership:
        folders = {m.group(1) for item in lesson['materials']
                   if (m := re.search(r'/folders/([A-Za-z0-9_-]{10,200})(?:/|$|\?)', item.get('url', '')))}
        for folder in sorted(folders):
            proof = capture(token, folder)
            if source in proof['member_ids']:
                membership.append({'folder': folder, 'proof_sha256': content_hash(proof)})
        if len(membership) != 1:
            raise ValueError('The source is not one exact current Agenda material or direct folder member.')
    return {'agenda': agenda.spreadsheet_id, 'school_year': year, 'class': cls, 'week': week,
            'row': row, 'source': source, 'membership': membership, 'policy_sha256': content_hash({k: config.get(k) for k in (
                'HUB_FORM_TEACHER_SOURCES', 'HUB_INTAKE_DECK_ASSIGNMENTS', 'HUB_ASSIGNMENT_FORM_IDS',
                'HUB_WEEK_EXCLUSIONS', 'HUB_ASSIGNMENT_EXCLUSIONS')}), 'lesson_sha256': content_hash({
                'forms': lesson['forms'], 'materials': lesson['materials'], 'topic': lesson.get('topic', '')})}


def _ident(scope):
    return key('readinesssourcerole', {k: v for k, v in scope.items() if k not in ('lesson_sha256', 'policy_sha256', 'membership')})


def _meta(token, source):
    value = google.drive_file(token, source, FIELDS)
    if (value.get('id') != source or value.get('trashed') is not False
            or not value.get('mimeType') or not value.get('version') or not value.get('modifiedTime')):
        raise ValueError('The exact source metadata is incomplete, retired or unavailable.')
    return {k: value.get(k) for k in META}


def capture(token, source):
    """Current source metadata and complete bounded folder membership bookend."""
    before = _meta(token, source)
    members = None
    if before['mimeType'] == google.FOLDER:
        items, complete = google.folder_listing(token, source)
        if not complete or len(items) > 256:
            raise ValueError('The exact paper folder listing is incomplete or too large.')
        if any(not i.get('id') or i.get('trashed') is not False or not i.get('modifiedTime') for i in items):
            raise ValueError('The paper folder member metadata is incomplete.')
        members = sorted(items, key=lambda i: i['id'])
        if len({i['id'] for i in members}) != len(members):
            raise ValueError('The paper folder members are ambiguous.')
        after_items, complete = google.folder_listing(token, source)
        if not complete or sorted(after_items, key=lambda i: i['id']) != members:
            raise ValueError('The exact paper folder changed during this read.')
    content = None
    if before['mimeType'] in (completion.SLIDES, google.PPTX):
        consumed, deck = completion._teacher_template(token, source)
        if any(consumed.get(k) != before.get(k) for k in META):
            raise ValueError('The consumed source differs from its exact verified metadata.')
        content = content_hash(deck)
    if _meta(token, source) != before:
        raise ValueError('The exact source changed during this read.')
    return {'metadata': before, 'content_sha256': content, 'members_sha256': content_hash(members),
            'member_ids': [i['id'] for i in members] if members is not None else []}


def _axes(assessment, modality):
    if assessment not in (None, 'ungraded') or modality not in (None, 'paper') or not (assessment or modality):
        raise ValueError('Use only explicit ungraded and/or paper roles; paper does not imply ungraded.')
    return {'assessment': assessment, 'modality': modality}


def import_verified(token, config, agenda, cls, week, row, source, *, assessment=None, modality=None,
                    instruction_sha256, actor, expected_revision='', port=None):
    """Trusted-host-only CAS import after explicit teacher direction and live reads."""
    axes = _axes(assessment, modality)
    if not isinstance(instruction_sha256, str) or not HASH.fullmatch(instruction_sha256) or not isinstance(actor, str) or not actor.strip():
        raise ValueError('An attributed teacher instruction is required.')
    scope = _scope(token, agenda, cls, week, row, source, config)
    proof = capture(token, source)
    port = port if port is not None else configured_store(config)
    ident = _ident(scope)
    snapshot = port.read([ident]); old = snapshot.documents[ident]
    if (content_hash(old.value) if old else '') != expected_revision:
        raise Conflict('The private source role changed; reread before importing.')
    value = {'schema': SCHEMA, 'scope': scope, 'proof': proof, 'axes': axes,
             'instruction_sha256': instruction_sha256, 'actor': actor, 'verified_at': snapshot.read_time}
    bounded_json(value)
    history = key('readinesssourcerolehistory', [ident, content_hash(value)])
    port.commit({ident: (value, old.version if old else None), history: (value, None)})
    checked = port.read([ident, history]).documents
    if any(checked[k] is None or checked[k].value != value for k in (ident, history)):
        raise StoreUnavailable('The source-role save is unconfirmed; do not automatically retry.')
    return {'revision': content_hash(value), 'axes': axes}


def read(token, config, agenda, cls, week, row, source, *, port=None):
    """Absence/staleness/failure retains ordinary checks; it never grants a waiver."""
    try:
        scope = _scope(token, agenda, cls, week, row, source, config, verify_membership=False)
        port = port if port is not None else configured_store(config)
        doc = port.read([_ident(scope)]).documents[_ident(scope)]
        if doc is None:
            return None
        scope = _scope(token, agenda, cls, week, row, source, config)
        value = doc.value
        axes = _axes(**value['axes'])
        if (set(value) != {'schema', 'scope', 'proof', 'axes', 'instruction_sha256', 'actor', 'verified_at'}
                or value['schema'] != SCHEMA or value['scope'] != scope
                or not HASH.fullmatch(value['instruction_sha256']) or not isinstance(value['actor'], str) or not value['actor'].strip()
                or not isinstance(value['verified_at'], str)
                or value['proof'] != capture(token, source)):
            return None
        return {'axes': axes, 'revision': content_hash(value), 'metadata': value['proof']['metadata'],
                'member_ids': value['proof']['member_ids']}
    except (ValueError, TypeError, KeyError, google.GoogleError, completion.Unreadable, StoreUnavailable):
        return None


def result(role, source):
    label = 'Paper activity — VT codes not required' if role['axes']['modality'] == 'paper' else 'Ungraded assignment — VT codes not required'
    return {'status': 'NOT_REQUIRED', 'label': label, 'issues': [], 'counts': {}, 'problem': None,
            'deck_id': source, 'student_copies_checked': False, 'source_role': role['axes'],
            'action': 'Keep the existing material and assignment connections. This is not grading or full lesson release.',
            '_teacher_source_metadata': role['metadata']}
