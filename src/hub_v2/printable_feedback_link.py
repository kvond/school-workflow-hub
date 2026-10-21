"""A verified spreadsheet-feedback snapshot, independent of feedback generation.

Publication is a trusted-host operation, not an HTTP write endpoint. Reads never
assess work, refresh an index, build packets, or change delivery state.
"""
import hashlib
import io
import re
from datetime import date

from pypdf import PdfReader
from . import google, student_archive
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

SCHEMA = 'hub-spreadsheet-feedback-link/v1'
FIELDS = student_archive.FIELDS


def _id(config):
    agenda = config.get('HUB_AGENDA_SPREADSHEET_ID', '')
    if not isinstance(agenda, str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', agenda):
        raise StoreUnavailable('The feedback print link needs the configured Agenda.')
    return key('spreadsheetfeedbacklink', agenda)


def _file(token, config, ident, parent):
    meta = student_archive._private(google.drive_file(token, ident, FIELDS), parent=parent)
    if meta.get('mimeType') != 'application/pdf':
        raise student_archive.Held('The published feedback snapshot is not a PDF.')
    root, _ = student_archive._configuration(config)
    current, seen = parent, {ident}
    for _ in range(12):
        if current in seen or current == config.get('HUB_SUBMISSIONS_ROOT_FOLDER'):
            raise student_archive.Held('The feedback print location needs checking.')
        seen.add(current)
        folder = student_archive._private(google.drive_file(token, current, FIELDS), folder=True)
        if current == root:
            return meta
        parents = folder.get('parents') or []
        if len(parents) != 1:
            break
        current = parents[0]
    raise student_archive.Held('The feedback PDF is outside the verified private records tree.')


def _revision(meta):
    fields = ('id', 'version', 'modifiedTime', 'md5Checksum', 'size', 'parents')
    if any(meta.get(k) is None for k in fields):
        raise student_archive.Held('The feedback PDF revision is unavailable.')
    return {k: meta[k] for k in fields}


def publish(token, config, *, file_id, parent_id, spreadsheet_id, manifest, revision):
    """Verify complete saved bytes, then CAS-publish the link and immutable history."""
    if not all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_-]{10,200}', x)
               for x in (file_id, parent_id, spreadsheet_id)):
        raise ValueError('Choose exact PDF, folder and spreadsheet IDs.')
    if not isinstance(revision, str) or not re.fullmatch(r'(?:[a-f0-9]{64})?', revision):
        raise ValueError('Use the last-read publication revision.')
    if not isinstance(manifest, dict) or set(manifest) != {
            'date', 'sha256', 'pages', 'reports', 'students', 'report_type_gaps', 'source_holds', 'coverage'}:
        raise ValueError('Use a complete, counts-only packet manifest.')
    try:
        if date.fromisoformat(manifest['date']).isoformat() != manifest['date']:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Use the verified snapshot date.') from None
    if (manifest['coverage'] not in {'partial_verified_reports', 'complete_verified_reports'}
            or not isinstance(manifest['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', manifest['sha256'])
            or any(type(manifest[k]) is not int or manifest[k] < 0
                   for k in ('pages', 'reports', 'students', 'report_type_gaps', 'source_holds'))
            or not all(manifest[k] for k in ('pages', 'reports', 'students'))
            or (manifest['coverage'] == 'complete_verified_reports'
                and (manifest['report_type_gaps'] or manifest['source_holds']))):
        raise ValueError('Packet coverage is incomplete or inconsistent.')
    before = _file(token, config, file_id, parent_id)
    raw = google.printable_pdf_bytes(token, file_id, 'application/pdf')
    if (hashlib.sha256(raw).hexdigest() != manifest['sha256']
            or hashlib.md5(raw).hexdigest() != before['md5Checksum']
            or len(raw) != int(before['size']) or len(PdfReader(io.BytesIO(raw)).pages) != manifest['pages']):
        raise student_archive.Held('The saved feedback PDF does not match its reviewed manifest.')
    after = _file(token, config, file_id, parent_id)
    if before != after:
        raise student_archive.Held('The feedback PDF changed during publication.')
    ident, port = _id(config), configured_store(config)
    snapshot = port.read([ident]); old = snapshot.documents[ident]
    if (content_hash(old.value) if old else '') != revision:
        raise Conflict('The feedback print link changed; read the current publication before retrying.')
    value = {'schema': SCHEMA, 'file_id': file_id, 'parent_id': parent_id,
             'spreadsheet_id': spreadsheet_id, 'manifest': dict(manifest),
             'file_revision': _revision(after), 'name': after['name'], 'published_at': snapshot.read_time}
    if old and all(old.value.get(k) == v for k, v in value.items() if k != 'published_at'):
        return read(token, config)
    history = key('spreadsheetfeedbacklinkhistory', [ident, content_hash(value)])
    port.commit({ident: (value, old.version if old else None), history: (value, None)})
    saved = port.read([ident, history]).documents
    if any(saved[k] is None or saved[k].value != value for k in (ident, history)):
        raise StoreUnavailable('The feedback print link save is unconfirmed.')
    return read(token, config)


def read(token, config):
    doc = configured_store(config).read([_id(config)]).documents[_id(config)]
    if not doc:
        return {'configured': False, 'status': 'unpublished', 'revision': ''}
    value = doc.value
    try:
        manifest = value.get('manifest')
        if (value.get('schema') != SCHEMA or not isinstance(manifest, dict)
                or set(manifest) != {'date', 'sha256', 'pages', 'reports', 'students', 'report_type_gaps', 'source_holds', 'coverage'}
                or manifest.get('coverage') not in {'partial_verified_reports', 'complete_verified_reports'}
                or any(type(manifest.get(k)) is not int or manifest[k] < 0
                       for k in ('pages', 'reports', 'students', 'report_type_gaps', 'source_holds'))
                or not all(isinstance(value.get(k), str) and re.fullmatch(r'[A-Za-z0-9_-]{10,200}', value[k])
                           for k in ('file_id', 'parent_id', 'spreadsheet_id'))):
            raise student_archive.Held('The feedback print publication needs checking.')
        meta = _file(token, config, value['file_id'], value['parent_id'])
        if _revision(meta) != value['file_revision']:
            raise student_archive.Held('The saved feedback PDF changed after publication. Preserve it and republish after verification.')
        return {'configured': True, 'status': 'ready', 'revision': content_hash(value),
                'url': 'https://drive.google.com/file/d/' + value['file_id'] + '/view',
                'spreadsheet_url': 'https://docs.google.com/spreadsheets/d/' + value['spreadsheet_id'] + '/edit',
                'name': value['name'], 'published_at': value['published_at'], **value['manifest']}
    except (student_archive.Held, google.GoogleError, KeyError, TypeError, ValueError) as error:
        return {'configured': True, 'status': 'held', 'revision': content_hash(value),
                'problem': str(error) if isinstance(error, (student_archive.Held, google.GoogleError))
                else 'The feedback print publication needs checking.'}
