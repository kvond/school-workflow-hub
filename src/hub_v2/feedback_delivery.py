"""Teacher-attested delivery of exact verified feedback versions, never a date filter."""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import daily_feedback_packet as daily, google, student_archive
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, bounded_json, key

SCHEMA = 'hub-feedback-delivery/v1'
MAX_REPORTS = 400
MAX_CAPTURES = 40
INDEX = key('feedbackdeliveryindex', SCHEMA)
FIELDS = daily.FIELDS


class Held(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def eastern_day(value):
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.tzinfo is None:
            raise ValueError()
        return instant.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    except (ValueError, TypeError, AttributeError):
        raise Held('A source timestamp lacks a verified timezone.') from None


def identity(item, revision):
    """The same operands are available from current sources and immutable packet manifests."""
    if (not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', str(item.get('id', '')))
            or item.get('course') not in ('biology', 'forensics', 'anatomy')
            or item.get('mime') not in ('application/pdf', 'application/vnd.google-apps.document')):
        raise Held('A report lacks an exact supported source identity.')
    day = item.get('feedback_day') or item.get('created_day')
    daily.date_value(day)
    if not isinstance(revision, dict) or not revision.get('version') or not revision.get('modifiedTime'):
        raise Held('A report lacks its exact source revision.')
    eastern_day(revision['modifiedTime'])
    if item.get('mime') == 'application/pdf' and (not revision.get('md5Checksum') or not revision.get('size')):
        raise Held('A PDF lacks its verified content revision.')
    pdf_hash = item.get('pdf_sha256') or item.get('audited_source_pdf_sha256')
    if pdf_hash is not None and not re.fullmatch(r'[a-f0-9]{64}', str(pdf_hash)):
        raise Held('A report has an unverified PDF digest.')
    feedback_ids = item.get('feedback_ids') or []
    if not isinstance(feedback_ids, list) or len(feedback_ids) != len(set(feedback_ids)):
        raise Held('A class packet has uncertain report membership.')
    return {'file_id': item['id'], 'course': item['course'], 'mime': item['mime'],
        'feedback_id': item.get('feedback_id'), 'included_feedback_ids': sorted(feedback_ids),
        'feedback_date': day, 'saved_at': item.get('saved_at'), 'created_at': item.get('created_at'),
        'pdf_sha256': pdf_hash,
        'revision': {name: revision.get(name) for name in ('version', 'modifiedTime', 'md5Checksum', 'size')}}


def packet_identities(packet):
    ids, revisions, sources = packet.get('source_file_ids'), packet.get('source_revisions'), packet.get('source_identities')
    if (not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
            or not isinstance(revisions, dict) or set(revisions) != set(ids)
            or not isinstance(sources, dict) or set(sources) != set(ids)):
        raise Held('The saved packet lacks an exact source manifest.')
    values = []
    for ident in ids:
        source = sources[ident]
        values.append(identity({'id': ident, 'course': source.get('course'), 'mime': source.get('mime'),
            'feedback_id': source.get('feedback_id'), 'feedback_ids': source.get('included_feedback_ids'),
            'feedback_day': source.get('feedback_date'), 'saved_at': source.get('saved_at'),
            'created_at': source.get('created_at'), 'pdf_sha256': (packet.get('source_pdf_sha256') or {}).get(ident)}, revisions[ident]))
    return values


def _metadata(token, item):
    meta = google.drive_file(token, item['id'], FIELDS)
    student_archive._private(meta)
    if meta.get('id') != item['id'] or meta.get('mimeType') != item['mime']:
        raise Held('A feedback source no longer matches its verified file.')
    return meta


def source_identities(token, files, *, through=None):
    if len(files) > MAX_REPORTS or len({item['id'] for item in files}) != len(files):
        raise Held('The current report list exceeds its bounded unique capture limit.')
    with ThreadPoolExecutor(max_workers=4) as pool:
        metadata = list(pool.map(lambda item: _metadata(token, item), files))
    values, unknown = {}, []
    for item, meta in zip(files, metadata):
        try:
            revision = daily._revision(meta)
            if item.get('archived') is not True and item.get('student_only') is not True:
                raise Held('A legacy packet lacks verified student-only report contents.')
            if through and eastern_day(revision['modifiedTime']) > through:
                earlier_immutable_pdf = (item.get('archived') is True and item.get('mime') == 'application/pdf'
                    and re.fullmatch(r'[a-f0-9]{64}', str(item.get('pdf_sha256') or ''))
                    and item.get('drive_md5') == revision.get('md5Checksum')
                    and isinstance(item.get('saved_at'), str) and eastern_day(item['saved_at']) <= through)
                if not earlier_immutable_pdf:
                    raise Held('The current content version was changed after the printed cutoff.')
            values[item['id']] = identity(item, revision)
        except (Held, daily.Held, ValueError):
            unknown.append({'file_id': item['id'], 'reason': 'Exact report/content version is not verified; remains pending.'})
    return values, unknown, metadata


def prepare(token, config, through, handed_packet_id=None):
    from . import native_feedback
    day = daily.date_value(through)
    if handed_packet_id is not None and (not isinstance(handed_packet_id, str)
            or not re.fullmatch(r'dailyfeedbackpacket_[a-f0-9]{64}', handed_packet_id)):
        raise Held('Handed intent must be null or one exact saved packet reference.')
    if day > datetime.now(ZoneInfo('America/New_York')).date().isoformat():
        raise Held('Choose a delivery cutoff that has already occurred.')
    def catalog():
        found = native_feedback._feedback_files(token, config)
        if not found.get('configured') or found.get('problem'):
            raise Held('The verified private feedback catalog is unavailable.')
        return sorted([item for item in found['files'] if
            (item.get('feedback_day') or item.get('created_day') or '9999-99-99') <= day], key=lambda item: item['id'])
    files = catalog()
    values, unknown, before = source_identities(token, files, through=day)
    packet = None
    handed = []
    if handed_packet_id:
        if not re.fullmatch(r'dailyfeedbackpacket_[a-f0-9]{64}', handed_packet_id):
            raise Held('Choose an exact saved daily packet.')
        packet = daily.delivery_packet(token, config, handed_packet_id)
        if packet.get('status') != 'ready' or packet.get('id') != handed_packet_id or daily.date_value(packet.get('date')) > day:
            raise Held('The chosen packet must be an exact verified packet dated on or before the cutoff.')
        if (packet.get('date') != '2026-10-11' or packet.get('source_count') != 5
                or packet.get('individual_reports') != 5 or packet.get('legacy_packets') != 0
                or packet.get('report_counts') != {'science_writing': 5, 'missing_work_contract': 0,
                    'critical_aspects': 0, 'other_sources': 0}):
            raise Held('Handed capture is limited to the exact five-report Oct 11 science writing packet.')
        handed = packet_identities(packet)
        if any(values.get(value['file_id']) != value for value in handed):
            raise Held('A handed packet source changed or is absent from the current verified report versions.')
    if catalog() != files:
        raise Held('The feedback catalog changed during delivery review. Prepare a fresh review.')
    _, _, after = source_identities(token, files, through=day)
    if before != after:
        raise Held('A report changed during delivery review. No delivery was recorded.')
    snapshot = {'schema': SCHEMA, 'through': day, 'printed': sorted(values.values(), key=lambda value: value['file_id']),
        'handed': sorted(handed, key=lambda value: value['file_id']), 'unknown': unknown,
        'metadata_sha256': {item['id']: digest(meta) for item, meta in zip(files, before)}, 'handed_packet': {'id': packet['id'], 'date': packet['date'], 'manifest_hash': packet['manifest_hash']}
            if packet else None}
    bounded_json(snapshot)
    return snapshot


def review_reply(snapshot):
    return {'schema': SCHEMA, 'status': 'review', 'through': snapshot['through'],
        'snapshot_sha256': digest(snapshot), 'printed_count': len(snapshot['printed']),
        'handed_count': len(snapshot['handed']), 'unknown_count': len(snapshot['unknown']),
        'handed_packet_id': (snapshot.get('handed_packet') or {}).get('id'),
        'limitations': ['Unverified versions remain pending. This is a one-time capture of the reports now present.']}


def _index(port):
    doc = port.read([INDEX]).documents[INDEX]
    if not doc:
        return None, []
    value = doc.value
    if not isinstance(value, dict):
        raise StoreUnavailable('delivery_index_unverified')
    refs = value.get('captures')
    if (value.get('schema') != SCHEMA or not isinstance(refs, list) or len(refs) > MAX_CAPTURES
            or len(refs) != len(set(refs)) or any(not re.fullmatch(r'feedbackdelivery_[a-f0-9]{64}', ref) for ref in refs)):
        raise StoreUnavailable('delivery_index_unverified')
    return doc, refs


def confirm(token, config, through, handed_packet_id, snapshot_sha256, printed_attested, handed_attested, *, actor):
    if (actor not in {email.strip().lower() for email in config.get('HUB_ALLOWED_EMAILS', '').split(',') if email.strip()}
            or printed_attested is not True or handed_attested is not bool(handed_packet_id)):
        raise Held('Confirm Printed and Handed separately for the exact reviewed reports.')
    if not re.fullmatch(r'[a-f0-9]{64}', str(snapshot_sha256)):
        raise Held('Prepare the exact delivery review first.')
    snapshot = prepare(token, config, through, handed_packet_id)
    if digest(snapshot) != snapshot_sha256 or not snapshot['printed']:
        raise Held('The report snapshot changed or is empty. No delivery was recorded. Prepare it again.')
    port = configured_store(config)
    ident = key('feedbackdelivery', snapshot_sha256)
    old, refs = _index(port)
    existing = port.read([ident]).documents[ident]
    if existing:
        valid_capture(ident, existing.value, config)
        if existing.value.get('snapshot') != snapshot or ident not in refs:
            raise StoreUnavailable('delivery_capture_unverified')
        return {'schema': SCHEMA, 'status': 'recorded', 'capture_id': ident, 'snapshot_sha256': snapshot_sha256,
            'printed_count': len(snapshot['printed']), 'handed_count': len(snapshot['handed']), 'cached': True}
    if len(refs) >= MAX_CAPTURES:
        raise Held('The bounded delivery history needs review before another capture.')
    record = {'schema': SCHEMA, 'status': 'teacher_attested', 'recorded_at': datetime.now(timezone.utc).isoformat(),
        'snapshot_sha256': snapshot_sha256, 'snapshot': snapshot,
        'printed_attested': True, 'handed_attested': bool(handed_packet_id), 'teacher': actor}
    index = {'schema': SCHEMA, 'captures': refs + [ident]}
    try:
        port.commit({ident: (record, None), INDEX: (index, old.version if old else None)})
    except (Conflict, StoreUnavailable):
        # One atomic attempt; only exact readback can resolve an uncertain result.
        actual = port.read([ident, INDEX]).documents
        if not actual[ident] or actual[ident].value != record or not actual[INDEX] or actual[INDEX].value != index:
            raise StoreUnavailable('delivery_capture_unconfirmed') from None
    actual = port.read([ident, INDEX]).documents
    if not actual[ident] or actual[ident].value != record or not actual[INDEX] or actual[INDEX].value != index:
        raise StoreUnavailable('delivery_capture_unconfirmed')
    valid_capture(ident, actual[ident].value, config)
    return {'schema': SCHEMA, 'status': 'recorded', 'capture_id': ident, 'snapshot_sha256': snapshot_sha256,
        'printed_count': len(snapshot['printed']), 'handed_count': len(snapshot['handed']), 'cached': False}


def valid_capture(ref, value, config):
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA
            or value.get('status') != 'teacher_attested' or value.get('printed_attested') is not True
            or not isinstance(value.get('recorded_at'), str) or not isinstance(value.get('teacher'), str)
            or '@' not in value['teacher']):
        raise StoreUnavailable('delivery_capture_unverified')
    allowed = {email.strip().lower() for email in config.get('HUB_ALLOWED_EMAILS', '').split(',') if email.strip()}
    if value['teacher'] not in allowed:
        raise StoreUnavailable('delivery_actor_unverified')
    try:
        observed = datetime.fromisoformat(value['recorded_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None or observed > datetime.now(timezone.utc):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise StoreUnavailable('delivery_timestamp_unverified') from None
    snapshot = value.get('snapshot')
    if (not isinstance(snapshot, dict) or snapshot.get('schema') != SCHEMA
            or digest(snapshot) != value.get('snapshot_sha256') or key('feedbackdelivery', digest(snapshot)) != ref
            or not isinstance(snapshot.get('printed'), list) or not isinstance(snapshot.get('handed'), list)):
        raise StoreUnavailable('delivery_capture_unverified')
    if (set(value) != {'schema', 'status', 'recorded_at', 'snapshot_sha256', 'snapshot',
            'printed_attested', 'handed_attested', 'teacher'} or set(snapshot) != {
            'schema', 'through', 'printed', 'handed', 'unknown', 'metadata_sha256', 'handed_packet'}
            or any(not isinstance(item, dict) for item in snapshot['printed'] + snapshot['handed'])):
        raise StoreUnavailable('delivery_capture_unverified')
    printed = snapshot['printed']; handed = snapshot['handed']
    hashes = snapshot.get('metadata_sha256')
    if (not isinstance(hashes, dict) or any(not re.fullmatch(r'[a-f0-9]{64}', str(v)) for v in hashes.values())
            or not isinstance(snapshot.get('unknown'), list)):
        raise StoreUnavailable('delivery_capture_unverified')
    daily.date_value(snapshot.get('through'))
    if any(not isinstance(item, dict) or not isinstance(item.get('reason'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', str(item.get('file_id'))) for item in snapshot['unknown']):
        raise StoreUnavailable('delivery_capture_unverified')
    unknown_ids = [item['file_id'] for item in snapshot['unknown']]
    ids = [item.get('file_id') for item in printed]
    if (len(unknown_ids) != len(set(unknown_ids)) or set(ids) & set(unknown_ids)
            or set(hashes) != set(ids) | set(unknown_ids)):
        raise StoreUnavailable('delivery_capture_unverified')
    if not printed or len(printed) > MAX_REPORTS or len(ids) != len(set(ids)):
        raise StoreUnavailable('delivery_capture_unverified')
    for item in printed:
        normalized = identity({'id': item.get('file_id'), 'course': item.get('course'), 'mime': item.get('mime'),
            'feedback_id': item.get('feedback_id'), 'feedback_ids': item.get('included_feedback_ids'),
            'feedback_day': item.get('feedback_date'), 'saved_at': item.get('saved_at'),
            'created_at': item.get('created_at'), 'pdf_sha256': item.get('pdf_sha256')}, item.get('revision'))
        if normalized != item or item['feedback_date'] > snapshot['through']:
            raise StoreUnavailable('delivery_capture_unverified')
    if (value.get('handed_attested') is not bool(handed) or any(item not in printed for item in handed)
            or len({item['file_id'] for item in handed}) != len(handed)):
        raise StoreUnavailable('delivery_capture_unverified')
    packet = snapshot.get('handed_packet')
    if handed and (len(handed) != 5 or not isinstance(packet, dict) or packet.get('date') != '2026-10-11'
            or not re.fullmatch(r'dailyfeedbackpacket_[a-f0-9]{64}', str(packet.get('id')))
            or not re.fullmatch(r'[a-f0-9]{64}', str(packet.get('manifest_hash')))
            or set(packet) != {'id', 'date', 'manifest_hash'} or packet['date'] > snapshot['through']):
        raise StoreUnavailable('delivery_capture_unverified')
    if not handed and packet is not None:
        raise StoreUnavailable('delivery_capture_unverified')
    return snapshot


def project(config, values, unknown=None):
    """Read-only exact-version projection. Storage failures leave every report pending."""
    all_ids = list(values)
    fallback = {'status': 'unavailable', 'printed_file_ids': [], 'handed_file_ids': [],
        'pending_file_ids': all_ids + list(unknown or []),
        'problem': 'Delivery records could not be verified. Reports remain pending; saved PDFs can still be reprinted.'}
    try:
        port = configured_store(config)
        _, refs = _index(port)
        docs = port.read(refs).documents if refs else {}
        printed, handed = set(), set()
        for ref in refs:
            doc = docs[ref]
            if not doc:
                raise StoreUnavailable('delivery_capture_unverified')
            snapshot = valid_capture(ref, doc.value, config)
            for name, target in (('printed', printed), ('handed', handed)):
                for value in snapshot.get(name, []):
                    ident = value.get('file_id')
                    if ident in values and value == values[ident]:
                        target.add(ident)
        return {'status': 'verified', 'printed_file_ids': sorted(printed), 'handed_file_ids': sorted(handed),
            'pending_file_ids': [ident for ident in all_ids if ident not in printed] + list(unknown or [])}
    except (StoreUnavailable, Conflict, ValueError, KeyError, TypeError):
        return fallback


def current_projection(token, config, files):
    try:
        # With no completed captures there is nothing to suppress; avoid an unnecessary metadata census.
        _, refs = _index(configured_store(config))
        if not refs:
            return {'status': 'verified', 'printed_file_ids': [], 'handed_file_ids': [],
                'pending_file_ids': [item['id'] for item in files]}
        values, unknown, _ = source_identities(token, files)
        return project(config, values, [item['file_id'] for item in unknown])
    except (Held, daily.Held, google.GoogleError, student_archive.Held, StoreUnavailable, Conflict, ValueError, KeyError, TypeError):
        return {'status': 'unavailable', 'printed_file_ids': [], 'handed_file_ids': [],
            'pending_file_ids': [item['id'] for item in files],
            'problem': 'Current report delivery versions could not be verified. Every report remains pending.'}


def packet_projection(config, packet):
    try:
        values = {item['file_id']: item for item in packet_identities(packet)}
        return project(config, values)
    except (Held, daily.Held, ValueError, KeyError, TypeError):
        return {'status': 'unavailable', 'printed_file_ids': [], 'handed_file_ids': [],
            'pending_file_ids': packet.get('source_file_ids') or [],
            'problem': 'The saved packet delivery identities could not be verified.'}
