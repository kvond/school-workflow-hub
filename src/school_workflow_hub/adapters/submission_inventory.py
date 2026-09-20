"""Authenticated, bounded Drive inventory. File presence is not assessment.

Reads only the lesson folder resolved from the live Agenda. Never follows
shortcuts or accepts a browser-provided folder ID. No student state is written.
"""
from datetime import datetime, timezone
import re
from .dated_agenda import read_day
from .grading_records import read_records
from .hosted_agenda import ConnectionError, service_token
from ..core.cloud_state import content_hash

FOLDER = 'application/vnd.google-apps.folder'
FORM = 'application/vnd.google-apps.form'
SHORTCUT = 'application/vnd.google-apps.shortcut'
FIELDS = 'id,name,mimeType,trashed,parents,modifiedTime,version'
# drive.metadata.readonly returns a file's name but withholds `parents`, so
# folder resolution silently collects nothing and surfaces as a sharing error
# the teacher cannot act on. Use the read-only content scope assessment_save
# already reads with.
DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'


class DriveReader:
    def __init__(self, token):
        import requests
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers['Authorization'] = 'Bearer ' + token

    def get(self, path, params):
        try:
            response = self.session.get('https://www.googleapis.com/drive/v3/' + path,
                                        params=params, timeout=(5, 15), allow_redirects=False)
            if response.status_code in (403, 404):
                raise ConnectionError('submission_folder_access_required')
            if response.status_code != 200:
                raise ConnectionError('submission_source_unavailable')
            return response.json()
        except ConnectionError:
            raise
        except Exception:
            raise ConnectionError('submission_source_unavailable') from None

    def metadata(self, file_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', file_id):
            raise ConnectionError('invalid_submission_folder')
        return self.get('files/' + file_id, {'fields': FIELDS, 'supportsAllDrives': 'true'})

    def children(self, file_id, cursor):
        params = {'q': "'" + file_id + "' in parents and trashed = false",
                  'fields': 'nextPageToken,incompleteSearch,files(' + FIELDS + ')',
                  'pageSize': 100, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
        if cursor:
            params['pageToken'] = cursor
        return self.get('files', params)


# The teacher's own folder naming: A&P is the anatomy course label in Drive.
COURSE_ALIASES = {'Anatomy': ('Anatomy', 'A&P')}


def lesson_folder_name(name, course, week):
    """A specific turn-in folder for this course, never a whole-course folder.

    The lesson binding itself comes from the Agenda TurnIn Map row, which is
    already matched on the exact lesson title and week. This name check is
    defense in depth: the right course (including the teacher's own aliases),
    a Turn In-specific folder, and either the same week label or a cycle label,
    because one cycle's turn-in folder deliberately serves several weeks.
    """
    dash = r'\s+[—–-]\s+'
    aliases = '|'.join(re.escape(a) for a in COURSE_ALIASES.get(course, (course,)))
    if not re.match('(?:' + aliases + ')' + dash, name) or 'Turn In' not in name:
        return False
    labeled = re.search(r'\b(?:Wk|Week)\s*0?(\d{1,2})\b', name)
    if labeled:
        agenda = re.search(r'\b(?:Wk|Week)\s*0?(\d{1,2})\b', week or '')
        return bool(agenda) and labeled[1] == agenda[1]
    return bool(re.search(r'\bCycle\s*\d{1,2}\b', name))


def collect(reader, folder_id, *, course, week, skip_name_guard=False):
    root = reader.metadata(folder_id)
    if root.get('trashed') is not False or root.get('mimeType') != FOLDER:
        raise ConnectionError('submission_folder_inactive')
    # Reject stale course-level mappings; require a specific lesson folder.
    # The lesson-name path derives the folder from the assignment's own
    # submission files (ground truth), so the Agenda-week name guard — which
    # only catches wrong-date mappings — does not apply and is skipped.
    if not skip_name_guard and not lesson_folder_name(root.get('name', ''), course, week):
        raise ConnectionError('submission_lesson_mapping_required')
    queue = [(folder_id, 0)]
    seen, files, forms, pages = set(), [], [], 0
    while queue:
        parent, depth = queue.pop(0)
        if parent in seen or len(seen) >= 12 or depth > 4:
            raise ConnectionError('submission_scan_incomplete')
        seen.add(parent)
        cursor, cursors = None, set()
        while True:
            pages += 1
            if pages > 24:
                raise ConnectionError('submission_scan_incomplete')
            page = reader.children(parent, cursor)
            if page.get('incompleteSearch') is True or not isinstance(page.get('files'), list):
                raise ConnectionError('submission_scan_incomplete')
            for item in page['files']:
                if parent not in item.get('parents', []) or item.get('trashed') is not False:
                    raise ConnectionError('submission_scan_incomplete')
                if item.get('mimeType') == FOLDER:
                    queue.append((item['id'], depth + 1))
                elif item.get('mimeType') == SHORTCUT:
                    raise ConnectionError('submission_shortcut_needs_review')
                elif item.get('mimeType') == FORM:
                    forms.append(item['id'])
                else:
                    if any(f['id'] == item['id'] for f in files) or len(files) >= 500:
                        raise ConnectionError('submission_scan_incomplete')
                    files.append(item)
            cursor = page.get('nextPageToken')
            if not cursor:
                break
            if cursor in cursors:
                raise ConnectionError('submission_scan_incomplete')
            cursors.add(cursor)
    return root, sorted(files, key=lambda f: f['id']), len(seen), len(forms)


CLASS_COURSE = {'biology': 'Biology', 'forensics': 'Forensics', 'anatomy': 'Anatomy'}
MAX_SOURCE_FILES = 200
MAX_LESSON_FOLDERS = 6


def _lesson_source_files(records, class_key, assignment_key):
    ids = []
    for row in records.get('submissions', []):
        if row.get('class_key') != class_key or row.get('assignment_key') != assignment_key:
            continue
        fid = str(row.get('source_file_id') or '')
        if re.fullmatch(r'[A-Za-z0-9_-]{20,128}', fid) and fid not in ids:
            ids.append(fid)
        if len(ids) >= MAX_SOURCE_FILES:
            break
    return ids


def resolve_lesson_folders(reader, records, class_key, assignment_key):
    """Distinct, live parent folders of this assignment's known submissions.

    Ground truth: an assignment's turn-in sources are wherever its already
    recorded submission files live — a turn-in folder, a Form's file-responses
    folder, or an earlier-cycle folder. Returns the distinct parent folders so
    the scan covers every verified source, never a folder guessed from a date.
    Files the runtime reader cannot currently see are skipped (their sharing is
    a separate, surfaced dependency), not treated as a hard failure.
    """
    file_ids = _lesson_source_files(records, class_key, assignment_key)
    if not file_ids:
        raise ConnectionError('lesson_has_no_recorded_submissions')
    folders, unreadable = [], 0
    for fid in file_ids:
        try:
            meta = reader.metadata(fid)
        except ConnectionError:
            unreadable += 1
            continue
        for parent in meta.get('parents', []) or []:
            if parent not in folders:
                folders.append(parent)
        if len(folders) > MAX_LESSON_FOLDERS:
            raise ConnectionError('lesson_sources_too_scattered')
    if not folders:
        raise ConnectionError('submission_folder_access_required')
    return folders, unreadable


def read_lesson_inventory(assertion, class_key, assignment_key, config):
    """Inventory a lesson's submissions by lesson name (no date), by scanning the
    union of the folders that its recorded submission files actually live in."""
    if class_key not in CLASS_COURSE:
        raise ConnectionError('invalid_submission_scope')
    if not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', assignment_key or ''):
        raise ConnectionError('invalid_submission_scope')
    records = read_records(assertion, config)
    reader = DriveReader(service_token(config.get('HUB_READER_SERVICE_ACCOUNT', ''), DRIVE_SCOPE))
    course = CLASS_COURSE[class_key]
    folders, unreadable = resolve_lesson_folders(reader, records, class_key, assignment_key)
    assessed = {str(r.get('source_file_id') or '') for r in records.get('submissions', [])
                if r.get('class_key') == class_key and r.get('assignment_key') == assignment_key
                and r.get('status') in {'assessed', 'roster_matched'}}
    seen_ids, files, total_folders, total_forms, names = set(), [], 0, 0, []
    for folder_id in folders:
        root, found, checked, forms = collect(reader, folder_id, course=course, week=None,
                                              skip_name_guard=True)
        names.append(root.get('name', ''))
        total_folders += checked
        total_forms += forms
        for f in found:
            if f['id'] in seen_ids:
                continue
            seen_ids.add(f['id'])
            files.append(f)
    files.sort(key=lambda f: f['id'])
    return {'schema_version': 'hub-submission-inventory/v1', 'class_key': class_key,
            'assignment_key': assignment_key, 'folder_name': ' + '.join(names),
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'source_revision': content_hash({'assignment': assignment_key, 'files': files}),
            'folders_checked': total_folders, 'forms_found': total_forms,
            'sources_unreadable': unreadable,
            'files': [{'file_id': f['id'], 'name': f['name'],
                       'url': 'https://drive.google.com/file/d/' + f['id'] + '/view',
                       'modified_at': f.get('modifiedTime'), 'version': f.get('version'),
                       'status': 'present',
                       'stage': 'assessed' if f['id'] in assessed else 'awaiting_assessment'}
                      for f in files],
            'coverage': 'visible_files_only', 'assessment_status': 'not_assessed',
            'roster_verified': False, 'external_writes': 0}


def read_inventory(assertion, day, class_key, config):
    if class_key not in {'biology', 'forensics', 'anatomy'}:
        raise ConnectionError('invalid_submission_scope')
    agenda = read_day(assertion, day, config)
    lesson = next(l for l in agenda['lessons'] if l['class_key'] == class_key)
    match = re.fullmatch(r'https://drive.google.com/drive/folders/([A-Za-z0-9_-]{20,128})', lesson.get('turnin_folder') or '')
    if not match:
        raise ConnectionError('submission_lesson_mapping_required')
    reader = DriveReader(service_token(config.get('HUB_READER_SERVICE_ACCOUNT', ''), DRIVE_SCOPE))
    course = {'biology': 'Biology', 'forensics': 'Forensics', 'anatomy': 'Anatomy'}[class_key]
    root, files, folders, forms = collect(reader, match[1], course=course, week=agenda['week'])
    fresh = read_day(assertion, day, config)
    if next(l for l in fresh['lessons'] if l['class_key'] == class_key) != lesson:
        raise ConnectionError('submission_agenda_changed')
    return {'schema_version': 'hub-submission-inventory/v1', 'date': day, 'class_key': class_key,
            'assignment': lesson['assignment_label'], 'folder_name': root['name'],
            'folder_url': lesson['turnin_folder'], 'checked_at': datetime.now(timezone.utc).isoformat(),
            'source_revision': content_hash({'lesson': lesson, 'files': files}),
            'folders_checked': folders, 'forms_found': forms,
            'files': [{'name': f['name'], 'url': 'https://drive.google.com/file/d/' + f['id'] + '/view',
                       'modified_at': f.get('modifiedTime'), 'version': f.get('version')} for f in files],
            'coverage': 'visible_files_only', 'assessment_status': 'not_assessed',
            'roster_verified': False, 'external_writes': 0}
