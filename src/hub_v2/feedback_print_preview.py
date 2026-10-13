"""Private, immutable HTTPS print previews of exact existing feedback versions.

These records are not daily documents or delivery receipts. Only existing
verified print sources are selected; no model, Drive writer or grade writer is
used. PDF chunks use the Hub's configured private state and absent-only CAS.
"""
import base64
import hashlib
import re
from datetime import datetime, timezone
from io import BytesIO

from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, key

from . import native_feedback

SCHEMA = 'hub-feedback-print-preview/v1'
CHUNK_SCHEMA = 'hub-feedback-print-preview-chunk/v1'
MAX_BYTES, MAX_PAGES, MAX_FILES = 25_000_000, 1500, 100
CHUNK_BYTES, BATCH = 120_000, 20
PREVIEW_ID = re.compile(r'feedbackprintpreview_[a-f0-9]{64}')
CHUNK_ID = re.compile(r'feedbackpreviewchunk_[a-f0-9]{64}')
FILE_ID = re.compile(r'[A-Za-z0-9_-]{10,200}')


class Held(ValueError):
    pass


def _files(values):
    if (not isinstance(values, list) or not 1 <= len(values) <= MAX_FILES
            or any(not isinstance(value, str) or not FILE_ID.fullmatch(value) for value in values)
            or len(set(values)) != len(values)):
        raise Held('Choose one bounded list of exact feedback versions for the print preview.')
    return sorted(values)


def _put_exact(port, values):
    """Never overwrite state or repeat an uncertain write; read back the same IDs."""
    ids = list(values)
    before = port.read(ids)
    changes = {}
    for ident, value in values.items():
        old = before.documents[ident]
        if old and old.value != value:
            raise Held('The private print preview did not match its immutable record.')
        if not old:
            changes[ident] = (value, None)
    if changes:
        try:
            port.commit(changes)
        except (Conflict, StoreUnavailable):
            # The only recovery is an exact read. It cannot create another PDF
            # or repeat a grade, provider call or external school write.
            pass
    after = port.read(ids)
    if any(not after.documents[ident] or after.documents[ident].value != value
           for ident, value in values.items()):
        raise StoreUnavailable('print_preview_readback_unconfirmed')


def _reply(record, cached=False):
    return {'schema': SCHEMA, 'status': 'ready', 'id': record['id'],
        'preview_only': True, 'daily_document_saved': False, 'delivery_changed': False,
        'name': record['name'], 'source_count': len(record['basis']['source_file_ids']),
        'pages': record['basis']['pages'], 'cached': cached,
        'pdf_url': '/v2/api/feedback/preview/pdf?id=' + record['id']}


def prepare(token, config, file_ids):
    ids = _files(file_ids)
    # Check private-state availability before reading/assembling source PDFs.
    port = configured_store(config)
    raw, _ = native_feedback.print_today(token, config, ids)
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES or not raw.startswith(b'%PDF'):
        raise Held('The print preview was empty or exceeded its private size limit.')
    try:
        from pypdf import PdfReader
        pages = len(PdfReader(BytesIO(raw), strict=True).pages)
        if not 1 <= pages <= MAX_PAGES:
            raise ValueError()
    except Exception:
        raise Held('The print preview had unreadable or excessive pages.') from None
    basis = {'source_file_ids': ids, 'pdf_sha256': hashlib.sha256(raw).hexdigest(),
        'size': len(raw), 'pages': pages}
    ident = key('feedbackprintpreview', [SCHEMA, basis])
    prior = port.read([ident]).documents[ident]
    chunks = {}
    for index, start in enumerate(range(0, len(raw), CHUNK_BYTES)):
        chunk = raw[start:start + CHUNK_BYTES]
        digest = hashlib.sha256(chunk).hexdigest()
        chunk_id = key('feedbackpreviewchunk', [ident, index, digest])
        chunks[chunk_id] = {'schema': CHUNK_SCHEMA, 'preview_id': ident, 'index': index,
            'sha256': digest, 'bytes_b64': base64.b64encode(chunk).decode('ascii')}
    record = {'schema': SCHEMA, 'status': 'ready', 'id': ident, 'preview_only': True,
        'daily_document_saved': False, 'delivery_changed': False, 'basis': basis,
        'name': 'Feedback-print-preview-' + ident[-12:] + '.pdf',
        'chunk_ids': list(chunks), 'compiled_at': datetime.now(timezone.utc).isoformat()}
    if prior:
        if {k: v for k, v in prior.value.items() if k != 'compiled_at'} != {
                k: v for k, v in record.items() if k != 'compiled_at'}:
            raise Held('The existing private print preview could not be verified.')
        record = prior.value
    chunk_ids = list(chunks)
    for start in range(0, len(chunks), BATCH):
        _put_exact(port, {ident: chunks[ident] for ident in chunk_ids[start:start + BATCH]})
    _put_exact(port, {ident: record})
    # Read back reconstructed bytes before advertising a ready HTTPS URL.
    actual, _ = pdf(config, ident, port=port)
    if actual != raw:
        raise StoreUnavailable('print_preview_pdf_readback_unconfirmed')
    return _reply(record, cached=prior is not None)


def pdf(config, ident, *, port=None):
    if not isinstance(ident, str) or not PREVIEW_ID.fullmatch(ident):
        raise Held('Choose one private feedback print preview.')
    port = port or configured_store(config)
    stored = port.read([ident]).documents[ident]
    if not stored:
        raise Held('This private print preview is unavailable. Prepare its exact feedback versions again.')
    record = stored.value
    try:
        basis = record['basis']
        ids = _files(basis['source_file_ids'])
        size, pages, digest = basis['size'], basis['pages'], basis['pdf_sha256']
        chunks = record['chunk_ids']
        if (record['schema'] != SCHEMA or record['id'] != ident or record['status'] != 'ready'
                or record['preview_only'] is not True or record['daily_document_saved'] is not False
                or record['delivery_changed'] is not False or ids != basis['source_file_ids']
                or set(basis) != {'source_file_ids', 'size', 'pages', 'pdf_sha256'}
                or type(size) is not int or not 0 < size <= MAX_BYTES
                or type(pages) is not int or not 1 <= pages <= MAX_PAGES
                or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)
                or key('feedbackprintpreview', [SCHEMA, basis]) != ident
                or record['name'] != 'Feedback-print-preview-' + ident[-12:] + '.pdf'
                or not isinstance(chunks, list) or len(chunks) != (size + CHUNK_BYTES - 1) // CHUNK_BYTES
                or any(not isinstance(value, str) or not CHUNK_ID.fullmatch(value) for value in chunks)
                or len(set(chunks)) != len(chunks)):
            raise ValueError()
        output = bytearray()
        for start in range(0, len(chunks), BATCH):
            snapshot = port.read(chunks[start:start + BATCH])
            for index in range(start, min(start + BATCH, len(chunks))):
                document = snapshot.documents[chunks[index]]
                if not document:
                    raise ValueError()
                value = document.value
                encoded = value['bytes_b64']
                if not isinstance(encoded, str) or len(encoded) > 4 * ((CHUNK_BYTES + 2) // 3):
                    raise ValueError()
                chunk = base64.b64decode(encoded, validate=True)
                expected_size = min(CHUNK_BYTES, size - index * CHUNK_BYTES)
                chunk_digest = hashlib.sha256(chunk).hexdigest()
                if (value['schema'] != CHUNK_SCHEMA or value['preview_id'] != ident
                        or type(value['index']) is not int or value['index'] != index
                        or len(chunk) != expected_size or value['sha256'] != chunk_digest
                        or key('feedbackpreviewchunk', [ident, index, chunk_digest]) != chunks[index]):
                    raise ValueError()
                output.extend(chunk)
        raw = bytes(output)
        if len(raw) != size or not raw.startswith(b'%PDF') or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise Held('The private print preview bytes could not be verified. No PDF was returned.') from None
    return raw, record['name']
