"""Immutable, dated all-class feedback PDFs, using existing verified reports only.

No assessment/provider call, source move, grade write or delivery-state change.
One manifest reserves one Drive ID before upload; uncertain creation is read
back and held, never retried as another file. Older packet versions remain.
"""
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

from . import google, student_archive
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

FOLDER_CONFIG = 'HUB_FEEDBACK_DAILY_PACKETS_FOLDER'
SCHEMA = 'hub-daily-feedback-packet/v1'
PDF = 'application/pdf'
TAG = 'hubDailyFeedbackPacket'
FIELDS = 'id,name,mimeType,parents,trashed,permissions,capabilities,createdTime,modifiedTime,version,md5Checksum,size,properties,driveId'
MAX_FILES, MAX_BYTES, MAX_PAGES = 300, 10_000_000, 1500


class Held(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def date_value(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise Held('Choose one valid feedback date.') from None


def feedback_date(stamp):
    try:
        value = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError()
        return value.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    except (ValueError, TypeError, AttributeError):
        raise Held('The feedback needs its original saved date verified.') from None


def enabled(config):
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{10,200}', str(config.get(FOLDER_CONFIG) or '')))


def _location(token, config, write=False):
    if not enabled(config):
        raise Held('The daily feedback documents are not connected to a private packet folder yet.')
    ident = config[FOLDER_CONFIG]
    try:
        folder = student_archive._private(google.drive_file(token, ident, FIELDS), folder=True, write=write)
        current, seen = folder, {ident}
        for _ in range(12):
            if current['id'] == config.get('HUB_SUBMISSIONS_ROOT_FOLDER'):
                raise Held('Daily feedback documents must stay outside the Turn-In source folders.')
            parents = current.get('parents') or []
            if not parents:
                return folder
            if len(parents) != 1 or parents[0] in seen:
                raise Held('The daily feedback folder ancestry needs checking.')
            seen.add(parents[0])
            current = google.drive_file(token, parents[0], 'id,parents,trashed')
            if current.get('trashed') is not False:
                raise Held('The daily feedback folder has an unavailable parent.')
        raise Held('The daily feedback folder ancestry could not be fully checked.')
    except student_archive.Held as error:
        raise Held('The daily feedback folder needs verified private access: ' + str(error)) from None


def _read(port, ident):
    return port.read([ident]).documents[ident]


def _put(port, ident, value, old=None):
    try:
        port.commit({ident: (value, old.version if old else None)})
    except (Conflict, StoreUnavailable):
        actual = _read(port, ident)
        if not actual or actual.value != value:
            raise
    actual = _read(port, ident)
    if not actual or actual.value != value:
        raise StoreUnavailable('daily_feedback_readback_unconfirmed')
    return actual


def _revision(meta):
    if meta.get('trashed') is not False or not meta.get('version') or not meta.get('modifiedTime'):
        raise Held('A feedback source needs its current Drive revision verified.')
    return {field: meta.get(field) for field in ('version', 'modifiedTime', 'md5Checksum', 'size')}


def _check_file(token, folder, record):
    try:
        meta = student_archive._private(google.drive_file(token, record['drive_file_id'], FIELDS), parent=folder['id'])
    except student_archive.Held as error:
        raise Held('The saved daily feedback document needs checking: ' + str(error)) from None
    props = meta.get('properties') or {}
    if (meta.get('mimeType') != PDF or props.get(TAG) != record['id']
            or props.get('manifestHash') != record['manifest_hash'] or props.get('feedbackDate') != record['date']
            or meta.get('md5Checksum') != record['pdf_md5'] or int(meta.get('size', -1)) != record['size']):
        raise Held('The saved daily feedback PDF did not match its immutable manifest and readback. Preserve it for review.')
    return meta


def _reply(record, cached=False):
    return {'schema': SCHEMA, 'configured': True, 'status': 'ready', 'id': record['id'], 'date': record['date'],
            'name': record['name'], 'source_count': len(record['manifest']), 'pages': record['pages'],
            'individual_reports': sum(bool(item.get('feedback_id')) for item in record['manifest']),
            'legacy_packets': sum(not item.get('feedback_id') for item in record['manifest']),
            'classes': list(dict.fromkeys(item['course'] for item in record['manifest'])),
            'manifest_hash': record['manifest_hash'], 'compiled_at': record['compiled_at'],
            'source_file_ids': [item['file_id'] for item in record['manifest']],
            'source_revisions': {item['file_id']: item['revision'] for item in record['manifest']},
            'source_pdf_sha256': dict(record.get('source_pdf_sha256') or {}),
            'source_identities': {item['file_id']: {field: item.get(field) for field in
                ('course', 'mime', 'feedback_id', 'included_feedback_ids', 'feedback_date', 'saved_at', 'created_at')}
                for item in record['manifest']},
            'report_counts': record.get('report_counts'),
            'drive_file_id': record['drive_file_id'], 'url': 'https://drive.google.com/open?id=' + record['drive_file_id'],
            'pdf_url': '/v2/api/feedback/daily/pdf?id=' + record['id'], 'cached': cached,
            'delivery_changed': False}


def _point(port, folder_id, record, observed_at):
    ident = key('dailyfeedbackdates', folder_id)
    for _ in range(3):
        old = _read(port, ident)
        dates = dict((old.value if old else {}).get('dates', {}))
        prior = dates.get(record['date'])
        if prior and prior['observed_at'] > observed_at:
            return
        dates[record['date']] = {'id': record['id'], 'observed_at': observed_at}
        if len(dates) > 500:
            raise Held('The daily feedback date index needs its size checked.')
        try:
            _put(port, ident, {'schema': SCHEMA, 'dates': dates}, old)
            return
        except Conflict:
            continue
    raise Held('A newer daily feedback update is finishing. Reload its saved document before retrying.')


def matches_sources(token, packet, sources):
    """Match revisions, or prove that a private PDF has exactly the saved bytes."""
    revisions = packet.get('source_revisions')
    if not isinstance(revisions, dict):
        return None
    if set(revisions) != {item['id'] for item in sources}:
        return False
    identities = packet.get('source_identities')
    if isinstance(identities, dict):
        for item in sources:
            identity = {'course': item.get('course'), 'mime': item.get('mime'),
                'feedback_id': item.get('feedback_id'), 'included_feedback_ids': item.get('feedback_ids') or [],
                'feedback_date': item.get('feedback_day', item.get('created_day')),
                'saved_at': item.get('saved_at'), 'created_at': item.get('created_at')}
            if identities.get(item['id']) != identity:
                return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        actual = list(pool.map(lambda item: google.drive_file(token, item['id'], FIELDS), sources))
    for meta in actual:
        try:
            student_archive._private(meta)
        except student_archive.Held as error:
            raise Held('A current feedback source needs verified private access: ' + str(error)) from None
    changed = []
    for item, meta in zip(sources, actual):
        if meta.get('id') != item['id'] or meta.get('mimeType') != item['mime']:
            return False
        if _revision(meta) != revisions[item['id']]:
            changed.append((item, meta))
    if not changed:
        return True
    hashes = packet.get('source_pdf_sha256')
    if not isinstance(identities, dict) or not isinstance(hashes, dict):
        return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        return all(pool.map(lambda pair: _same_saved_pdf(token, pair[0], pair[1],
            revisions[pair[0]['id']], hashes.get(pair[0]['id'])), changed))


def _same_saved_pdf(token, item, before, revision, digest):
    """Version-only drift never grants content, date, identity or location authority."""
    if (item.get('mime') != PDF or not re.fullmatch(r'[a-f0-9]{64}', str(digest or ''))
            or not re.fullmatch(r'[a-f0-9]{32}', str(revision.get('md5Checksum') or ''))):
        return False
    content_revision = {k: v for k, v in revision.items() if k != 'version'}
    if {k: v for k, v in _revision(before).items() if k != 'version'} != content_revision:
        return False
    try:
        size = int(revision['size'])
    except (KeyError, ValueError, TypeError):
        return False
    if not 0 < size <= MAX_BYTES:
        return False
    location = item.get('student_folder_url') or item.get('course_folder_url') or ''
    parent = re.fullmatch(r'https://drive\.google\.com/drive/folders/([A-Za-z0-9_-]{10,200})', location)
    if not parent or before.get('parents') != [parent[1]]:
        return False
    try:
        parent_before = student_archive._private(google.drive_file(token, parent[1], FIELDS), folder=True)
        if parent_before['id'] != parent[1]:
            return False
        raw = google.printable_pdf_bytes(token, item['id'], PDF)
        after = google.drive_file(token, item['id'], FIELDS)
        student_archive._private(after, parent=parent[1])
        parent_after = student_archive._private(google.drive_file(token, parent[1], FIELDS), folder=True)
    except student_archive.Held as error:
        raise Held('A current feedback PDF needs verified private access: ' + str(error)) from None
    return (isinstance(raw, bytes) and len(raw) == size and raw.startswith(b'%PDF')
            and hashlib.sha256(raw).hexdigest() == digest
            and hashlib.md5(raw).hexdigest() == revision['md5Checksum']
            and after.get('id') == item['id'] and after.get('mimeType') == PDF
            and parent_after['id'] == parent[1] and parent_after.get('parents') == parent_before.get('parents')
            and after.get('properties') == before.get('properties')
            and {k: v for k, v in _revision(after).items() if k != 'version'} == content_revision)


def _assemble(token, sources, manifest, day):
    from pypdf import PdfReader, PdfWriter
    writer, total, pages, source_hashes = PdfWriter(), 0, 0, {}
    for item, entry in zip(sources, manifest):
        raw = google.printable_pdf_bytes(token, item['id'], item['mime'])
        total += len(raw)
        if total > MAX_BYTES:
            raise Held('This daily feedback packet is too large. Its original reports remain saved.')
        after = google.drive_file(token, item['id'], FIELDS)
        if _revision(after) != entry['revision']:
            raise Held('A feedback source changed during packet preparation. No daily document was saved.')
        if item['mime'] == PDF and entry['revision'].get('md5Checksum') != hashlib.md5(raw).hexdigest():
            raise Held('One feedback PDF did not match its verified source bytes.')
        try:
            reader = PdfReader(BytesIO(raw), strict=True)
            if not reader.pages:
                raise ValueError()
            pages += len(reader.pages)
            if pages > MAX_PAGES:
                raise ValueError()
            writer.append(reader, import_outline=False)
        except Exception:
            raise Held('One feedback source has unreadable or excessive pages. No daily document was saved.') from None
        source_hashes[item['id']] = hashlib.sha256(raw).hexdigest()
    writer.add_metadata({'/Title': day + ' — Daily student feedback', '/Subject': 'Existing student feedback across classes; source dates preserved'})
    output = BytesIO(); writer.write(output); pdf = output.getvalue()
    if len(pdf) > MAX_BYTES:
        raise Held('This daily feedback PDF exceeds the private packet size limit.')
    return pdf, pages, source_hashes


def build(token, write_token, config, date_et):
    """Finalize an exact feedback day. Changed source manifests get a new immutable version."""
    day, observed = date_value(date_et), now()
    if not token or not write_token:
        raise Held('Saving the daily feedback document needs the Hub’s authorized Drive writer.')
    folder = _location(token, config, write=True)
    from . import native_feedback
    try:
        sources = native_feedback.daily_sources(token, config, day)
    except native_feedback.Held as error:
        raise Held(str(error)) from None
    if not sources:
        raise Held('No verified saved feedback belongs to ' + day + '. Older feedback was not relabelled as this date.')
    if len(sources) > MAX_FILES:
        raise Held('This feedback day exceeds the bounded document limit. Its saved sources remain intact.')
    order = {name: index for index, name in enumerate(native_feedback.FEEDBACK_FOLDERS)}
    sources = sorted(sources, key=lambda item: (order[item['course']], item['created_at'], item['name'].casefold(), item['id']))
    with ThreadPoolExecutor(max_workers=4) as pool:
        metadata = list(pool.map(lambda item: google.drive_file(token, item['id'], FIELDS), sources))
    manifest = []
    for item, meta in zip(sources, metadata):
        try:
            student_archive._private(meta)
        except student_archive.Held as error:
            raise Held('A daily feedback source needs verified private access: ' + str(error)) from None
        if meta.get('id') != item['id'] or meta.get('mimeType') != item['mime']:
            raise Held('One feedback source changed its identity or type. Refresh before finalizing.')
        if item.get('proven_revision') and item['proven_revision'] != _revision(meta):
            raise Held('An audited class packet changed after its student-only boundaries were verified.')
        manifest.append({'file_id': item['id'], 'name': item['name'], 'course': item['course'], 'mime': item['mime'],
            'feedback_id': item.get('feedback_id'), 'included_feedback_ids': item.get('feedback_ids') or [],
            'feedback_date': day, 'saved_at': item.get('saved_at'), 'created_at': item['created_at'],
            'revision': _revision(meta)})
    digest = content_hash(manifest)
    ident = key('dailyfeedbackpacket', [folder['id'], day, digest])
    port, old = configured_store(config), None
    old = _read(port, ident)
    if old:
        record = old.value
        if record.get('schema') != SCHEMA or record.get('manifest') != manifest:
            raise Held('The daily feedback manifest could not be confirmed.')
    else:
        pdf, pages, source_hashes = _assemble(token, sources, manifest, day)
        record = {'schema': SCHEMA, 'id': ident, 'status': 'reserved', 'date': day,
            'name': day + ' - Daily student feedback - ' + digest[:10] + '.pdf',
            'manifest': manifest, 'manifest_hash': digest, 'source_pdf_sha256': source_hashes,
            'drive_file_id': google.archive_file_id(write_token), 'folder_id': folder['id'],
            'destination_drive_id': folder.get('driveId'),
            'pdf_sha256': hashlib.sha256(pdf).hexdigest(), 'pdf_md5': hashlib.md5(pdf).hexdigest(),
            'size': len(pdf), 'pages': pages, 'observed_at': observed, 'compiled_at': now()}
        # Display metadata leaves the source manifest and older snapshots intact.
        record['report_counts'] = {name: sum(item.get('report_type') == name for item in sources)
            for name in ('science_writing', 'missing_work_contract', 'critical_aspects')}
        record['report_counts']['other_sources'] = len(sources) - sum(record['report_counts'].values())
        try:
            old = _put(port, ident, record)
        except Conflict:
            old = _read(port, ident)
            if not old or old.value.get('pdf_sha256') != record['pdf_sha256']:
                raise Held('This exact daily feedback packet is already being prepared. Reload before retrying.') from None
            record = old.value
    try:
        meta = _check_file(token, folder, record)
    except google.GoogleError as error:
        quota_moved = (record.get('status') == 'known_failure' and record.get('failure_kind') == 'quota'
                       and folder.get('driveId') and folder['driveId'] != record.get('destination_drive_id'))
        if error.status != 404 or (record['status'] != 'reserved' and not quota_moved):
            raise Held('An earlier daily packet upload was not confirmed. Check its reserved document; no new upload was attempted.') from None
        if 'pdf' not in locals():
            # "reserved" proves the upload has not started. Re-exporting an
            # unchanged source is safe here; a Google Doc export can have new
            # PDF timestamps even though its revision is unchanged. Replace
            # only the unuploaded byte receipt, retaining the reserved ID.
            pdf, pages, source_hashes = _assemble(token, sources, manifest, day)
            record = {**record, 'pdf_sha256': hashlib.sha256(pdf).hexdigest(), 'pdf_md5': hashlib.md5(pdf).hexdigest(),
                      'size': len(pdf), 'pages': pages, 'source_pdf_sha256': source_hashes, 'compiled_at': now()}
            old = _put(port, ident, record, old)
        record = {**record, 'status': 'creating', 'destination_drive_id': folder.get('driveId')}
        old = _put(port, ident, record, old)
        creation_error = None
        try:
            google.archive_create(write_token, {'id': record['drive_file_id'], 'name': record['name'], 'mimeType': PDF,
                'parents': [folder['id']], 'properties': {TAG: ident, 'manifestHash': digest, 'feedbackDate': day}}, pdf)
        except google.GoogleError as error:
            creation_error = str(error)
        try:
            meta = _check_file(token, folder, record)
        except google.GoogleError as read_error:
            if read_error.status == 404 and creation_error and 'storage quota' in creation_error.lower():
                record = {**record, 'status': 'known_failure', 'failure_kind': 'quota'}
                _put(port, ident, record, old)
                raise Held(creation_error + ' The daily document stays held until its destination is verified in a different actual Shared Drive.') from None
            raise Held(creation_error or 'Drive did not confirm the daily packet upload. Check its reserved file before retrying.') from None
    if record['status'] != 'verified':
        record = {**record, 'status': 'verified', 'drive_created_at': meta.get('createdTime')}
        _put(port, ident, record, old)
    _point(port, folder['id'], record, observed)
    return _reply(record, cached=old.value.get('status') == 'verified')


def status(token, config, date_et=None):
    if not enabled(config):
        return {'schema': SCHEMA, 'configured': False, 'status': 'unconfigured', 'date': date_et,
                'problem': 'Daily feedback documents are not connected to their private packet folder yet.'}
    folder = _location(token, config)
    port = configured_store(config)
    index = _read(port, key('dailyfeedbackdates', folder['id']))
    dates = (index.value if index else {}).get('dates', {})
    day = date_value(date_et) if date_et else max(dates, default=datetime.now(ZoneInfo('America/New_York')).date().isoformat())
    ref = dates.get(day)
    if not ref:
        return {'schema': SCHEMA, 'configured': True, 'status': 'not_saved', 'date': day,
                'problem': 'The daily feedback document for ' + day + ' has not been finalized.'}
    stored = _read(port, ref['id'])
    if not stored or stored.value.get('status') != 'verified' or stored.value.get('folder_id') != folder['id']:
        raise Held('The daily feedback report record is incomplete. Preserve its earlier versions for review.')
    _check_file(token, folder, stored.value)
    history = []
    for start in range(0, len(dates), 40):
        days = sorted(dates, reverse=True)[start:start + 40]
        documents = port.read([dates[day]['id'] for day in days]).documents
        for day in days:
            item = documents.get(dates[day]['id'])
            if (item and item.value.get('schema') == SCHEMA and item.value.get('status') == 'verified'
                    and item.value.get('date') == day and item.value.get('folder_id') == folder['id']):
                reply = _reply(item.value, cached=True)
                history.append({field: reply[field] for field in ('id', 'date', 'pages', 'pdf_url', 'classes')})
    return {**_reply(stored.value, cached=True), 'available_dates': history}


def pdf(token, config, ident):
    if not isinstance(ident, str) or not re.fullmatch(r'dailyfeedbackpacket_[a-f0-9]{64}', ident):
        raise Held('Choose one saved daily feedback document.')
    folder = _location(token, config)
    stored = _read(configured_store(config), ident)
    if not stored or stored.value.get('status') != 'verified' or stored.value.get('folder_id') != folder['id']:
        raise Held('This daily feedback document is not a confirmed private snapshot.')
    record = stored.value
    _check_file(token, folder, record)
    raw = google.printable_pdf_bytes(token, record['drive_file_id'], PDF)
    if hashlib.sha256(raw).hexdigest() != record['pdf_sha256']:
        raise Held('The saved daily feedback PDF changed. Its original manifest remains preserved.')
    return raw, record['name']
