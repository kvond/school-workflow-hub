"""A private outbox for the existing teacher worker's school Gmail connector.

The Hub never holds a Gmail credential or sends mail. Only genuinely new saved
individual versions enter this ledger. One incremental PDF covers them once;
Printed/Handed, assessments and source files are never changed here.
"""
import base64
import hashlib
import re
import secrets
from datetime import datetime, timezone
from email import policy as mail_policy
from email.parser import BytesParser
from email.utils import getaddresses

from . import daily_feedback_packet as daily, google, native_feedback, student_archive
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, bounded_json, key
from school_workflow_hub.core.runs import content_hash

SCHEMA = 'hub-print-ready-email/v1'
TAG = 'hubPrintReadyEmailBatch'
MAX_EVENTS, MAX_BATCH, CAS_CHUNK = 600, 200, 30


class Held(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp
    except (ValueError, TypeError, AttributeError):
        raise Held('Email eligibility requires an exact saved timestamp with a timezone.') from None


def _policy_id(config):
    root, _ = student_archive._configuration(config)
    return key('printemailpolicy', student_archive._scope_root(config, root))


def _read(port, ident):
    return port.read([ident]).documents[ident]


def _commit(port, changes):
    """One CAS attempt, recovered only by complete exact readback."""
    try:
        port.commit({ident: (value, old.version if old else None) for ident, (value, old) in changes.items()})
    except (Conflict, StoreUnavailable):
        found = port.read(list(changes)).documents
        if any(not found[ident] or found[ident].value != value for ident, (value, _) in changes.items()):
            raise
    found = port.read(list(changes)).documents
    if any(not found[ident] or found[ident].value != value for ident, (value, _) in changes.items()):
        raise StoreUnavailable('print_email_commit_unconfirmed')
    return found


def _policy(config):
    port = configured_store(config)
    doc = _read(port, _policy_id(config))
    if doc:
        v = doc.value
        if (v.get('schema') != SCHEMA or v.get('status') != 'active'
                or not isinstance(v.get('events'), dict) or len(v['events']) > MAX_EVENTS
                or not isinstance(v.get('baseline_files'), list) or not isinstance(v.get('baseline_reports'), list)
                or not isinstance(v.get('recipient'), str)):
            raise StoreUnavailable('print_email_policy_unverified')
        instant(v['activated_at'])
    return port, doc


def _actor(config, recipient, actor):
    allowed = {x.strip().lower() for x in config.get('HUB_ALLOWED_EMAILS', '').split(',') if x.strip()}
    if (not isinstance(recipient, str) or not re.fullmatch(r'[^@\s]+@[^@\s]+', recipient)
            or recipient != recipient.lower() or actor != recipient or recipient not in allowed):
        raise Held('Only the explicitly configured teacher school recipient may administer this outbox.')


def _catalog(token, config):
    found = native_feedback._feedback_files(token, config)
    files = found.get('files')
    if (found.get('configured') is not True or found.get('problem') or not isinstance(files, list)
            or len(files) > 10_000 or len({x.get('id') for x in files}) != len(files)):
        raise Held('The complete verified private print catalog is unavailable; no email selection was made.')
    return sorted(files, key=lambda x: x['id'])


def _baseline(token, config):
    from .feedback_delivery import source_identities
    files = _catalog(token, config)
    def capture():
        values, unknown, metadata = {}, [], []
        for start in range(0, len(files), 400):
            part, held, before = source_identities(token, files[start:start + 400])
            values.update(part); unknown.extend(held); metadata.extend(before)
        return values, unknown, metadata
    values, unknown, before = capture()
    if unknown or _catalog(token, config) != files:
        raise Held('The historical email baseline is incomplete or changed. No email was activated.')
    _, unknown_after, after = capture()
    if unknown_after or before != after:
        raise Held('The historical email baseline changed during its metadata bookend.')
    return files, content_hash(values)


def activate(token, config, recipient, *, actor, manual_seed=None, initial_baseline=None):
    """Trusted teacher activation; existing files/versions are excluded forever."""
    _actor(config, recipient, actor)
    port, existing = _policy(config)
    if existing:
        if existing.value['recipient'] != recipient:
            raise Held('An existing email recipient cannot be changed by another activation.')
        if manual_seed is not None:
            if not isinstance(manual_seed, dict):
                raise Held('The exact previously verified manual-send seed is required.')
            seeded = existing.value['events'].get(manual_seed.get('feedback_id'))
            doc = _read(port, seeded) if seeded else None
            if (not doc or doc.value.get('status') != 'sent'
                    or doc.value.get('manual_seed') is not True
                    or any(doc.value.get(k) != manual_seed.get(k) for k in ('feedback_id', 'file_id', 'pdf_sha256', 'student_key'))
                    or doc.value.get('course') != manual_seed.get('class_key')
                    or doc.value.get('gmail_message_id') != manual_seed.get('receipt', {}).get('gmail_message_id')):
                raise Held('The existing activation lacks this exact verified manual-send seed; do not assume it was imported.')
        return status(config)
    files, baseline_hash = _baseline(token, config)
    cutoff = now()
    if initial_baseline is not None:
        initial = initial_baseline
        # These context-only fields were added to the catalog projection; they
        # do not change any source, saved identity, revision or selection rule.
        def operands(rows):
            return [{k: v for k, v in row.items() if k not in ('student_key', 'student_section', 'assignment')} for row in rows]
        if (not isinstance(initial, dict) or initial.get('schema') != 'hub-print-ready-email-initial-baseline/v1'
                or initial.get('complete_bookended') is not True or not isinstance(initial.get('files'), list)
                or operands(initial['files']) != operands(files)
                or initial.get('baseline_sha256') != baseline_hash
                or instant(initial.get('observed_at')) > instant(cutoff)):
            raise Held('The initial catalog and activation bookend differ. Preserve the initial baseline and review genuinely new documents; do not silently exclude them.')
        cutoff = initial['observed_at']
    value = {'schema': SCHEMA, 'status': 'active', 'recipient': recipient, 'activated_at': cutoff,
        'activation_recorded_at': now(), 'initial_catalog_bookend_exact': initial_baseline is not None,
        'baseline_files': [x['id'] for x in files],
        'baseline_reports': sorted({x['feedback_id'] for x in files if x.get('feedback_id')}),
        'baseline_sha256': baseline_hash, 'events': {}, 'active_batch': None, 'sent_total': 0}
    changes = {}
    if manual_seed is not None:
        seed = manual_seed
        expected = {'feedback_id', 'file_id', 'pdf_sha256', 'class_key', 'student_key', 'receipt', 'raw_base64'}
        receipt = seed.get('receipt') if isinstance(seed, dict) else None
        if (not isinstance(seed, dict) or set(seed) != expected or not isinstance(receipt, dict)
                or not re.fullmatch(r'feedbacksource_[a-f0-9]{64}', str(seed['feedback_id']))
                or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', str(seed['file_id']))
                or not re.fullmatch(r'[a-f0-9]{64}', str(seed['pdf_sha256']))
                or seed['class_key'] not in native_feedback.FEEDBACK_FOLDERS or not seed['student_key']
                or receipt.get('verified_sent_and_inbox') is not True or receipt.get('send_count') != 1
                or receipt.get('to') != recipient or receipt.get('from') != recipient
                or 'SENT' not in receipt.get('gmail_labels', [])
                or receipt.get('attachment_sha256') != seed['pdf_sha256']
                or receipt.get('source_drive_file_id') != seed['file_id']
                or not re.fullmatch(r'[a-fA-F0-9]{8,64}', str(receipt.get('gmail_message_id', '')))):
            raise Held('The explicit already-sent seed needs its exact verified school message, PDF and logical report binding.')
        proof = sent_receipt({'recipient': recipient, 'subject': receipt['subject'],
            'name': receipt['attachment_filename'], 'pdf_sha256': seed['pdf_sha256']},
            receipt['gmail_message_id'], receipt['gmail_labels'], seed['raw_base64'])
        ident = key('printemailreport', [_policy_id(config), recipient, seed['feedback_id']])
        event = {'schema': SCHEMA, 'id': ident, 'status': 'sent', 'feedback_id': seed['feedback_id'],
            'file_id': seed['file_id'], 'pdf_sha256': seed['pdf_sha256'], 'course': seed['class_key'],
            'student_key': seed['student_key'], 'manual_seed': True, 'gmail_message_id': receipt['gmail_message_id'],
            'receipt_sha256': content_hash(receipt), 'sent_readback': proof, 'confirmed_at': now()}
        value['events'][seed['feedback_id']] = ident
        changes[ident] = (event, None)
    bounded_json(value)
    changes[_policy_id(config)] = (value, None)
    _commit(port, changes)
    return status(config)


def _report(token, item):
    if (item.get('archived') is not True or item.get('mime') != daily.PDF
            or not re.fullmatch(r'(feedbackdraft|feedbacksource|currentcontract)_[a-f0-9]{64}', str(item.get('feedback_id', '')))
            or not re.fullmatch(r'[a-f0-9]{64}', str(item.get('pdf_sha256', '')))):
        raise Held('A new print document needs a verified immutable individual report and exact PDF digest.')
    before = google.drive_file(token, item['id'], daily.FIELDS)
    student_archive._private(before)
    if any((before.get('properties') or {}).get(tag) for tag in (daily.TAG, TAG)):
        raise Held('A system daily/email packet cannot enter its own individual-report outbox.')
    raw = google.printable_pdf_bytes(token, item['id'], daily.PDF)
    after = google.drive_file(token, item['id'], daily.FIELDS)
    student_archive._private(after)
    if (before != after or before.get('id') != item['id'] or before.get('mimeType') != daily.PDF
            or hashlib.sha256(raw).hexdigest() != item['pdf_sha256']
            or hashlib.md5(raw).hexdigest() != before.get('md5Checksum')
            or len(raw) != int(before.get('size', -1))):
        raise Held('A report changed or lacks complete private PDF readback. No email was prepared.')
    return {'file_id': item['id'], 'feedback_id': item['feedback_id'], 'course': item['course'],
        'mime': daily.PDF, 'name': item['name'], 'saved_at': item['saved_at'],
        'student': item.get('student_name'), 'student_key': item.get('student_key'),
        'section': item.get('student_section'), 'lesson': item.get('assignment'),
        'feedback_date': item['feedback_day'], 'created_at': item['created_at'],
        'pdf_sha256': item['pdf_sha256'], 'revision': daily._revision(before), 'parents': before.get('parents')}


def _fresh(item):
    if item.get('report_type') == 'missing_work_contract':
        if item.get('contract_status') != 'open':
            raise Held('This current work contract is closed or superseded; it is not eligible for email.')
        checked = instant(item.get('contract_checked_at'))
        expires = instant(item.get('contract_evidence_expires_at'))
        current = instant(now())
        if not checked <= current < expires:
            raise Held('This current work contract expired; refresh its complete source check before emailing it.')


def reconcile(token, config, *, only=None):
    """Recover ready output events without dates, pending-print flags or rerating."""
    port, policy = _policy(config)
    if not policy:
        return {'status': 'unconfigured', 'queued': 0}
    value = {**policy.value, 'events': dict(policy.value['events'])}
    files, candidates, held = _catalog(token, config), [], []
    by_report = {}
    for item in files:
        report_id = item.get('feedback_id')
        if (not report_id or (only is not None and report_id not in only)
                or item['id'] in value['baseline_files'] or report_id in value['baseline_reports']):
            continue
        if not item.get('saved_at') or instant(item['saved_at']) <= instant(value['activated_at']):
            continue
        try:
            _fresh(item)
        except Held as error:
            held.append({'feedback_id': report_id, 'course': item['course'], 'name': item.get('name'),
                'student': item.get('student_name'), 'lesson': item.get('assignment'), 'reason': str(error)})
            continue
        previous = by_report.get(report_id)
        if previous and (previous.get('pdf_sha256') != item.get('pdf_sha256') or previous['course'] != item['course']):
            raise Held('One logical report has conflicting current copies; no email was selected.')
        by_report.setdefault(report_id, item)
    for report_id, item in by_report.items():
        ident = key('printemailreport', [_policy_id(config), value['recipient'], report_id])
        old = _read(port, ident)
        if old:
            if (old.value.get('schema') != SCHEMA or old.value.get('feedback_id') != report_id
                    or old.value.get('course') != item['course'] or old.value.get('pdf_sha256') != item.get('pdf_sha256')):
                raise Held('An existing email event has different immutable report bytes. Preserve it for review.')
            if old.value['status'] != 'sent' and value['events'].get(report_id) != ident:
                raise StoreUnavailable('print_email_event_index_unverified')
            continue
        if len(value['events']) + len(candidates) >= MAX_EVENTS:
            raise Held('The email ledger reached its explicit capacity; genuinely new documents remain unqueued for review, not idle.')
        source = _report(token, item)
        event = {'schema': SCHEMA, 'id': ident, 'status': 'ready', 'feedback_id': report_id,
            'file_id': item['id'], 'pdf_sha256': source['pdf_sha256'], 'course': item['course'],
            'source': source, 'queued_at': now()}
        candidates.append((ident, event))
    if _catalog(token, config) != files:
        raise Held('The print catalog changed during outbox selection; no email event was committed.')
    for start in range(0, len(candidates), CAS_CHUNK):
        chunk = candidates[start:start + CAS_CHUNK]
        changes = {ident: (event, None) for ident, event in chunk}
        value = {**value, 'events': dict(value['events'])}
        value['events'].update({event['feedback_id']: ident for ident, event in chunk})
        changes[_policy_id(config)] = (value, policy)
        policy = _commit(port, changes)[_policy_id(config)]
    return {'status': 'verified', 'queued': len(candidates), 'held_count': len(held), 'held': held}


def notify_ready(token, config, feedback_ids):
    """A non-disruptive hook after complete semantic filing/publication."""
    try:
        return reconcile(token, config, only=feedback_ids)
    except (Held, native_feedback.Held, student_archive.Held, daily.Held, google.GoogleError, Conflict, StoreUnavailable) as error:
        return {'status': 'held', 'problem': str(error), 'email_sent': False}


def _batch(config, ident):
    if not re.fullmatch(r'printemailbatch_[a-f0-9]{64}', str(ident)):
        raise Held('Choose one exact email batch.')
    port, policy = _policy(config)
    doc = _read(port, ident)
    if not policy or not doc or doc.value.get('schema') != SCHEMA or doc.value.get('id') != ident:
        raise Held('This email batch is not verified in the active outbox.')
    if doc.value.get('recipient') != policy.value['recipient']:
        raise Held('The email batch recipient changed.')
    return port, policy, doc


def _packet(token, config, batch):
    folder = daily._location(token, config)
    meta = google.drive_file(token, batch['drive_file_id'], daily.FIELDS)
    student_archive._private(meta, parent=folder['id'])
    if (meta.get('mimeType') != daily.PDF or (meta.get('properties') or {}).get(TAG) != batch['id']
            or meta.get('md5Checksum') != batch['pdf_md5'] or int(meta.get('size', -1)) != batch['size']):
        raise Held('The incremental email PDF did not match its private immutable batch.')
    raw = google.printable_pdf_bytes(token, batch['drive_file_id'], daily.PDF)
    if hashlib.sha256(raw).hexdigest() != batch['pdf_sha256'] or google.drive_file(token, batch['drive_file_id'], daily.FIELDS) != meta:
        raise Held('The incremental email PDF changed during readback.')
    return raw


def _current(token, config, batch):
    files = _catalog(token, config)
    selected = []
    for source in batch['manifest']:
        matching = [x for x in files if x.get('feedback_id') == source['feedback_id']
                    and x['course'] == source['course'] and x.get('pdf_sha256') == source['pdf_sha256']]
        if not matching:
            raise Held('An email source is no longer current, or its contract expired/closed; no send was authorized.')
        item = next((x for x in matching if x['id'] == source['file_id']), matching[0])
        _fresh(item)
        proof = _report(token, item)
        if any(proof[k] != source[k] for k in ('feedback_id', 'course', 'saved_at', 'feedback_date', 'pdf_sha256')):
            raise Held('An email source changed its logical assessment identity.')
        if (proof['parents'] != source['parents']
                or any(proof['revision'][k] != source['revision'][k] for k in ('modifiedTime', 'md5Checksum', 'size'))):
            raise Held('An email source changed its private location or content metadata; preserve the prepared version.')
        selected.append(item)
    if _catalog(token, config) != files:
        raise Held('The current print catalog changed before the email fence.')
    for item in selected:
        _fresh(item)
    return selected


def _reply(batch):
    return {**{k: batch[k] for k in ('id', 'status', 'recipient', 'subject', 'drive_file_id', 'name',
        'pdf_sha256', 'size', 'pages', 'report_count', 'manifest', 'expires_at')},
        'pdf_url': '/v2/api/feedback/email/pdf?id=' + batch['id'],
        'claim_token': batch.get('claim_token'), 'send_reserved_at': batch.get('send_reserved_at')}


def _retire_prepared(token, config, port, policy, old):
    """Release valid unsent sources only while CAS proves Gmail was never fenced."""
    if old.value['status'] != 'prepared':
        raise Held('A fenced or uncertain email can never be recomputed.')
    files = _catalog(token, config)
    changes, invalid = {}, 0
    for event_id, source in zip(old.value['event_ids'], old.value['manifest']):
        event = _read(port, event_id)
        if event and event.value['status'] == 'held' and event.value.get('retirement_batch_id') == old.value['id']:
            invalid += 1
            continue
        if not event or event.value['status'] != 'ready':
            raise StoreUnavailable('print_email_prepared_event_unverified')
        matching = [x for x in files if x.get('feedback_id') == source['feedback_id']
            and x['course'] == source['course'] and x.get('pdf_sha256') == source['pdf_sha256']]
        problem = None
        if not matching:
            problem = 'The exact report is closed, superseded or no longer current.'
        else:
            try:
                _fresh(matching[0])
            except Held as error:
                problem = str(error)
        if problem:
            invalid += 1
            changes[event_id] = ({**event.value, 'status': 'held', 'eligibility_problem': problem,
                'retirement_batch_id': old.value['id']}, event)
    if not invalid:
        raise Held('The prepared email needs its precise source/readback issue resolved before sending.')
    value = {**old.value, 'status': 'retired_before_send', 'retired_at': now(), 'invalid_source_count': invalid}
    # Invalidations are bounded; the batch fence is released only after each
    # held event has exact readback. A concurrent begin_send wins or loses CAS.
    for start in range(0, len(changes), CAS_CHUNK):
        chunk = dict(list(changes.items())[start:start + CAS_CHUNK])
        chunk[old.value['id']] = (old.value, old)
        chunk[_policy_id(config)] = (policy.value, policy)
        found = _commit(port, chunk)
        old, policy = found[old.value['id']], found[_policy_id(config)]
    _commit(port, {value['id']: (value, old), _policy_id(config): ({**policy.value, 'active_batch': None}, policy)})


def prepare(token, write_token, config, *, actor):
    port, policy = _policy(config)
    if not policy:
        raise Held('School email delivery needs its explicit historical baseline activation first.')
    _actor(config, policy.value['recipient'], actor)
    reconciliation = reconcile(token, config)
    port, policy = _policy(config)
    if policy.value['active_batch']:
        _, _, old = _batch(config, policy.value['active_batch'])
        if old.value['status'] == 'creating':
            # Read-only reconciliation: an uncertain upload is never replayed.
            _packet(token, config, old.value)
            ready = {**old.value, 'status': 'prepared'}
            _commit(port, {old.value['id']: (ready, old)})
            return _reply(ready)
        if old.value['status'] == 'prepared':
            try:
                _current(token, config, old.value)
            except Held:
                _retire_prepared(token, config, port, policy, old)
                return prepare(token, write_token, config, actor=actor)
            _packet(token, config, old.value)
            return _reply(old.value)
        raise Held('A prior email send is reserved or uncertain. Read back its exact school Sent message; never resend.')
    ready = []
    for ident in policy.value['events'].values():
        event = _read(port, ident)
        if not event or event.value.get('schema') != SCHEMA:
            raise StoreUnavailable('print_email_event_unverified')
        if event.value['status'] == 'ready':
            ready.append(event)
    ready.sort(key=lambda x: (x.value['source']['saved_at'], x.value['course'], x.value['feedback_id']))
    if not ready:
        return {'status': 'idle', 'report_count': 0, 'email_sent': False,
            'held_count': reconciliation['held_count'], 'held': reconciliation['held']}
    files = _catalog(token, config)
    eligible = []
    for event in ready:
        matches = [x for x in files if x.get('feedback_id') == event.value['feedback_id']
            and x.get('pdf_sha256') == event.value['pdf_sha256'] and x['course'] == event.value['course']]
        problem = None
        if not matches:
            problem = 'The report is closed, superseded or no longer current.'
        else:
            try:
                _fresh(matches[0])
            except Held as error:
                problem = str(error)
        if problem:
            _commit(port, {event.value['id']: ({**event.value, 'status': 'held', 'eligibility_problem': problem}, event)})
        else:
            eligible.append(event)
    ready = eligible
    if not ready:
        return {'status': 'held', 'report_count': 0, 'held_count': len(policy.value['events']), 'email_sent': False}
    chosen, size = [], 0
    for event in ready[:MAX_BATCH]:
        length = int(event.value['source']['revision']['size'])
        if length > daily.MAX_BYTES:
            raise Held('A new individual PDF exceeds the bounded email packet size.')
        if size + length > daily.MAX_BYTES:
            break
        chosen.append(event); size += length
    provisional = {'manifest': [x.value['source'] for x in chosen]}
    sources = _current(token, config, provisional)
    manifest = [_report(token, x) for x in sources]
    day = daily.feedback_date(now())
    raw, pages, hashes = daily._assemble(token, sources, manifest, day)
    if any(hashes[x['file_id']] != x['pdf_sha256'] for x in manifest):
        raise Held('The incremental packet contains unexpected source bytes.')
    folder = daily._location(token, config, write=True)
    ident = key('printemailbatch', [_policy_id(config), policy.value['recipient'], [x.value['id'] for x in chosen]])
    batch = {'schema': SCHEMA, 'id': ident, 'status': 'creating', 'recipient': policy.value['recipient'],
        'subject': 'SchoolHub print-ready forms - ' + day + ' - ' + ident[-12:],
        'name': day + ' - New print-ready forms - ' + ident[-12:] + '.pdf',
        'manifest': manifest, 'event_ids': [x.value['id'] for x in chosen],
        'report_count': len(chosen), 'pages': pages, 'size': len(raw),
        'pdf_sha256': hashlib.sha256(raw).hexdigest(), 'pdf_md5': hashlib.md5(raw).hexdigest(),
        'drive_file_id': google.archive_file_id(write_token), 'prepared_at': now()}
    deadlines = [x['contract_evidence_expires_at'] for x in sources if x.get('contract_evidence_expires_at')]
    batch['expires_at'] = min(deadlines, key=instant) if deadlines else None
    changes = {ident: (batch, None), _policy_id(config): ({**policy.value, 'active_batch': ident}, policy)}
    _commit(port, changes)
    try:
        google.archive_create(write_token, {'id': batch['drive_file_id'], 'name': batch['name'], 'mimeType': daily.PDF,
            'parents': [folder['id']], 'properties': {TAG: ident}}, raw)
    except google.GoogleError:
        pass  # Exact readback only; never retry this upload or allocate another ID.
    _packet(token, config, batch); _current(token, config, batch)
    old = _read(port, ident)
    ready_batch = {**batch, 'status': 'prepared'}
    _commit(port, {ident: (ready_batch, old)})
    return _reply(ready_batch)


def begin_send(token, config, ident, *, actor):
    port, policy, old = _batch(config, ident)
    _actor(config, policy.value['recipient'], actor)
    if policy.value['active_batch'] != ident or old.value['status'] != 'prepared':
        raise Held('This send was already fenced or is not prepared. Do not call Gmail again.')
    _current(token, config, old.value); _packet(token, config, old.value)
    if old.value['expires_at'] and instant(now()) >= instant(old.value['expires_at']):
        raise Held('The current contract expired during packet readback; no Gmail send was fenced.')
    value = {**old.value, 'status': 'send_reserved', 'send_reserved_at': now(), 'claim_token': secrets.token_hex(32)}
    _commit(port, {ident: (value, old), _policy_id(config): (policy.value, policy)})
    return {**_reply(value), 'send_allowed_once': True}


def sent_receipt(batch, message_id, labels, raw_base64):
    """Validate the connector's actual raw SENT readback, never a send 200 alone."""
    if not re.fullmatch(r'[a-fA-F0-9]{8,64}', str(message_id)) or not isinstance(labels, list) or 'SENT' not in labels:
        raise Held('The actual school Sent message is required to confirm email delivery.')
    try:
        raw = base64.urlsafe_b64decode(raw_base64 + '=' * (-len(raw_base64) % 4))
        if not 1 <= len(raw) <= 20_000_000:
            raise ValueError()
        message = BytesParser(policy=mail_policy.default).parsebytes(raw)
        for field in ('To', 'From', 'Subject'):
            if len(message.get_all(field, [])) != 1:
                raise ValueError()
        if (message.get('Cc') or message.get('Bcc')
                or [x[1].lower() for x in getaddresses(message.get_all('To', []))] != [batch['recipient']]
                or [x[1].lower() for x in getaddresses(message.get_all('From', []))] != [batch['recipient']]
                or str(message['Subject']) != batch['subject']):
            raise ValueError()
        attachments = [x for x in message.walk() if x.get_content_disposition() == 'attachment'
                       or x.get_filename() or x.get_content_type() == daily.PDF]
        if (len(attachments) != 1 or attachments[0].get_content_type() != daily.PDF
                or attachments[0].get_filename() != batch['name']
                or hashlib.sha256(attachments[0].get_payload(decode=True)).hexdigest() != batch['pdf_sha256']):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError):
        raise Held('The Sent message recipient, batch subject or decoded PDF differs from the exact prepared email.') from None
    return {'gmail_message_id': message_id, 'labels': sorted(labels), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
        'attachment_sha256': batch['pdf_sha256'], 'verified_at': now()}


def confirm_sent(config, ident, claim_token, message_id, labels, raw_base64, *, actor):
    port, policy, old = _batch(config, ident)
    _actor(config, policy.value['recipient'], actor)
    if not isinstance(claim_token, str) or claim_token != old.value.get('claim_token'):
        raise Held('Confirm only the exact private send claim returned to this worker.')
    receipt = sent_receipt(old.value, message_id, labels, raw_base64)
    if old.value['status'] in ('sent', 'sent_unrecorded'):
        if old.value['receipt']['gmail_message_id'] != message_id or old.value['receipt']['raw_sha256'] != receipt['raw_sha256']:
            raise Held('This batch already has a different exact Sent receipt.')
        if old.value['status'] == 'sent':
            return {**_reply(old.value), 'cached': True}
    if old.value['status'] not in ('send_reserved', 'uncertain', 'sent_unrecorded') or policy.value['active_batch'] != ident:
        raise Held('Only the previously fenced exact Gmail send may be confirmed.')
    value = {**old.value, 'status': 'sent_unrecorded', 'receipt': receipt}
    old = _commit(port, {ident: (value, old)})[ident]
    changes = {}
    for event_id in old.value['event_ids']:
        event = _read(port, event_id)
        if not event or event.value['status'] not in ('ready', 'sent'):
            raise StoreUnavailable('print_email_reserved_event_unverified')
        if event.value['status'] == 'sent':
            if event.value.get('batch_id') != ident or event.value.get('gmail_message_id') != message_id:
                raise StoreUnavailable('print_email_sent_event_conflict')
            continue
        changes[event_id] = ({**event.value, 'status': 'sent', 'batch_id': ident, 'gmail_message_id': message_id}, event)
    for start in range(0, len(changes), CAS_CHUNK):
        _commit(port, dict(list(changes.items())[start:start + CAS_CHUNK]))
    current = _read(port, ident)
    if current.value.get('receipt', {}).get('raw_sha256') != receipt['raw_sha256']:
        raise StoreUnavailable('print_email_sent_receipt_conflict')
    if current.value['status'] == 'sent':
        return {**_reply(current.value), 'cached': True}
    policy = _read(port, _policy_id(config))
    if not policy or policy.value.get('active_batch') != ident:
        raise Held('A newer email batch owns the active fence; the older Sent confirmation cannot clear it.')
    value = {**current.value, 'status': 'sent'}
    remaining = {ref: event_id for ref, event_id in policy.value['events'].items() if event_id not in value['event_ids']}
    _commit(port, {ident: (value, current), _policy_id(config): ({**policy.value, 'active_batch': None,
        'events': remaining, 'sent_total': policy.value.get('sent_total', 0) + value['report_count']}, policy)})
    return _reply(value)


def hold_uncertain(config, ident, claim_token, *, actor):
    port, policy, old = _batch(config, ident)
    _actor(config, policy.value['recipient'], actor)
    if claim_token != old.value.get('claim_token'):
        raise Held('Hold only the exact private send claim returned to this worker.')
    if old.value['status'] not in ('send_reserved', 'uncertain'):
        raise Held('Only a fenced provider send may be marked uncertain.')
    value = {**old.value, 'status': 'uncertain'}
    _commit(port, {ident: (value, old)})
    return _reply(value)


def status(config):
    port, policy = _policy(config)
    if not policy:
        return {'schema': SCHEMA, 'configured': False, 'status': 'unconfigured'}
    counts = {'ready': 0, 'reserved': 0, 'sent': 0, 'held': 0, 'uncertain': 0}
    counts['sent'] = policy.value.get('sent_total', 0)
    active = _read(port, policy.value['active_batch']) if policy.value['active_batch'] else None
    reserved = set(active.value['event_ids']) if active else set()
    seeds = []
    for ident in policy.value['events'].values():
        doc = _read(port, ident)
        if not doc or doc.value.get('schema') != SCHEMA or doc.value.get('status') not in counts:
            raise StoreUnavailable('print_email_event_unverified')
        slot = doc.value['status']
        if slot == 'ready' and ident in reserved:
            slot = 'uncertain' if active.value['status'] in ('send_reserved', 'uncertain') else 'reserved'
        counts[slot] += 1
        if doc.value.get('manual_seed') is True:
            seeds.append({k: doc.value[k] for k in ('feedback_id', 'file_id', 'pdf_sha256', 'gmail_message_id',
                'status', 'receipt_sha256')})
    return {'schema': SCHEMA, 'configured': True, 'status': 'active', 'recipient': policy.value['recipient'],
        'activated_at': policy.value['activated_at'], 'historical_file_count': len(policy.value['baseline_files']),
        'baseline_sha256': policy.value['baseline_sha256'], 'active_batch': policy.value['active_batch'],
        'counts': counts, 'active_batch_details': _reply(active.value) if active else None,
        'manual_seed_details': seeds,
        'sender': 'existing_weekday_worker_school_gmail', 'printed_or_handed_changed': False}


def packet_pdf(token, config, ident):
    _, _, batch = _batch(config, ident)
    return _packet(token, config, batch.value), batch.value['name']
