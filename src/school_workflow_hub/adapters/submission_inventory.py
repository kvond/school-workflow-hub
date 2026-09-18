"""Authenticated, bounded Drive inventory. File presence is not assessment.

Reads only the lesson folder resolved from the live Agenda. Never follows
shortcuts or accepts a browser-provided folder ID. No student state is written.
"""
from datetime import datetime, timezone
import re
from .dated_agenda import read_day
from .hosted_agenda import ConnectionError, service_token
from ..core.cloud_state import content_hash

FOLDER = 'application/vnd.google-apps.folder'
FORM = 'application/vnd.google-apps.form'
SHORTCUT = 'application/vnd.google-apps.shortcut'
FIELDS = 'id,name,mimeType,trashed,parents,modifiedTime,version'


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


def collect(reader, folder_id, *, course, week):
    root = reader.metadata(folder_id)
    if root.get('trashed') is not False or root.get('mimeType') != FOLDER:
        raise ConnectionError('submission_folder_inactive')
    # Reject stale course-level mappings; require a specific lesson folder.
    if not lesson_folder_name(root.get('name', ''), course, week):
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


def read_inventory(assertion, day, class_key, config):
    if class_key not in {'biology', 'forensics', 'anatomy'}:
        raise ConnectionError('invalid_submission_scope')
    agenda = read_day(assertion, day, config)
    lesson = next(l for l in agenda['lessons'] if l['class_key'] == class_key)
    match = re.fullmatch(r'https://drive.google.com/drive/folders/([A-Za-z0-9_-]{20,128})', lesson.get('turnin_folder') or '')
    if not match:
        raise ConnectionError('submission_lesson_mapping_required')
    reader = DriveReader(service_token(config.get('HUB_READER_SERVICE_ACCOUNT', ''),
                                     'https://www.googleapis.com/auth/drive.metadata.readonly'))
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
