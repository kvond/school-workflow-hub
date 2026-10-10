"""Read-only technical-repair classification in the existing private CAS store.

Only a trusted host operator can place these attested receipts. There is no
browser receipt writer, request-supplied proof, semantic hash, source mutation,
or report migration here. Inverse verification restores only the enumerated
code metadata changes; every other native field must equal the actual before
capture. A repair that changes assessment bindings requires correction review.
"""
import base64
from copy import deepcopy
import hashlib
import json
import math
import re
import zlib

from . import completion, work
from school_workflow_hub.core.cloud_state import StoreUnavailable, bounded_json, instant, key
from school_workflow_hub.core.runs import content_hash

SCHEMA = 'hub-verified-technical-source-repair/v1'
MAX_NATIVE_BYTES = 4_000_000
FORM_FIELDS = {'source_file_id', 'source_form_id', 'source_response_id', 'assignment_form_id', 'submitted_at'}
HASH = re.compile(r'[a-f0-9]{64}')
ID = re.compile(r'[A-Za-z0-9_-]{1,200}')


class Held(ValueError):
    pass


def receipt_key(scope, previous, current):
    # Fixed exact-capture key, not a mutable per-student "latest repair" record.
    # Host importers must create once using CAS expected-version=None.
    return key('technicalrepair', [{k: scope[k] for k in ('class', 'form', 'file')}] +
               [{k: source.get(k) for k in ('file', 'sha256', 'raw_sha256', 'metadata_sha256')}
                for source in (previous, current)])


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode()).hexdigest()


def analysis_basis(inputs, model):
    # Exact current assessed evidence, not a marker-stripped source hash.
    return content_hash({'aspects': inputs['aspects'], 'answers': inputs['answers'],
                         'person': inputs['person'], 'assignment': inputs['assignment'],
                         'reference': inputs['reference']['sha256'], 'prompt': inputs['prompt'],
                         'model': model})


def _form(receipt, scope):
    if (not isinstance(receipt, dict) or set(receipt) != FORM_FIELDS
            or any(not isinstance(receipt[k], str) or not ID.fullmatch(receipt[k])
                   for k in FORM_FIELDS - {'submitted_at'})
            or receipt['source_file_id'] != scope['file'] or receipt['assignment_form_id'] != scope['form']
            or work.when(receipt['submitted_at']) is None):
        raise ValueError('same-file Form provenance unavailable')
    return receipt


def _native(item, file_id):
    packed = base64.b64decode(item['native_raw_base64'], validate=True)
    if item.get('native_raw_encoding') == 'zlib':
        inflater = zlib.decompressobj()
        raw = inflater.decompress(packed, MAX_NATIVE_BYTES + 1)
        if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
            raise ValueError('incomplete, concatenated or oversized native capture')
    elif item.get('native_raw_encoding') == 'identity':
        raw = packed
    else:
        raise ValueError('unsupported native capture encoding')
    if not raw or len(raw) > MAX_NATIVE_BYTES or hashlib.sha256(raw).hexdigest() != item['native_raw_sha256']:
        raise ValueError('native raw capture mismatch')
    native = json.loads(raw)
    if (not isinstance(native, dict) or native.get('presentationId') != file_id
            or not isinstance(native.get('revisionId'), str) or not native['revisionId']
            or not isinstance(native.get('slides'), list) or not 1 <= len(native['slides']) <= 200):
        raise ValueError('native source identity unavailable')
    pages = [s['objectId'] for s in native['slides']]
    if len(set(pages)) != len(pages):
        raise ValueError('duplicate native page')
    return native, pages


def _elements(native):
    result = {}
    def visit(elements):
        for element in elements:
            ident = element['objectId']
            if not isinstance(ident, str) or not ID.fullmatch(ident) or ident in result:
                raise ValueError('ambiguous element identity')
            result[ident] = element
            visit(element.get('elementGroup', {}).get('children', []))
    for slide in native['slides']:
        visit(slide.get('pageElements', []))
    return result


def _codes(text):
    if not isinstance(text, str) or not text or len(text) > 12_000:
        return False
    remainder = completion.MARKER.sub('', completion.INVENTORY.sub('', text)).strip()
    # Inventory lines contain code identities/counts only, never lesson prose.
    for line in remainder.splitlines():
        if not re.fullmatch(r'(?:NOTES|BANK|DRAFT|OPTIONAL)(?:_IDS)?=[A-Za-z0-9_ /,;=-]{1,3000}', line.strip()):
            return False
    return bool(completion.MARKER.search(text) or completion.INVENTORY.search(text))


def _off_canvas_code(element, native):
    if (set(element) - {'objectId', 'size', 'transform', 'shape', 'title', 'description'}
            or element.get('shape', {}).get('shapeType') != 'TEXT_BOX'):
        raise ValueError('unsupported added code element')
    shape = element['shape']
    text = ''.join(t.get('textRun', {}).get('content', '')
                   for t in shape.get('text', {}).get('textElements', []))
    if not _codes(text) or any(k not in {'shapeType', 'shapeProperties', 'text'} for k in shape):
        raise ValueError('added element is not code-only text')
    if any(element.get(name) and not _codes(element[name]) for name in ('title', 'description')):
        raise ValueError('added element has unsupported non-code metadata')
    transform = element['transform']
    if (transform.get('scaleX', 1) != 1 or transform.get('scaleY', 1) != 1
            or transform.get('shearX', 0) != 0 or transform.get('shearY', 0) != 0):
        raise ValueError('unsupported code geometry')
    def pt(size):
        magnitude, unit = size['magnitude'], size['unit']
        if isinstance(magnitude, bool) or not isinstance(magnitude, (int, float)) or not math.isfinite(magnitude):
            raise ValueError('invalid code geometry')
        if unit == 'PT':
            return magnitude
        if unit == 'EMU':
            return magnitude / 12700
        raise ValueError('unsupported code geometry')
    x = pt({'magnitude': transform.get('translateX', 0), 'unit': transform['unit']})
    y = pt({'magnitude': transform.get('translateY', 0), 'unit': transform['unit']})
    width, height = (pt(element['size'][k]) for k in ('width', 'height'))
    page_width, page_height = (pt(native['pageSize'][k]) for k in ('width', 'height'))
    if not (0 < width <= 2000 and 0 < height <= 2000 and
            (x + width <= 0 or y + height <= 0 or x >= page_width or y >= page_height)):
        raise ValueError('added code is not wholly outside the teaching page')


def _inverse(before, after, operations):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 200:
        raise ValueError('bounded inverse operations unavailable')
    original, actual = _elements(before), _elements(after)
    restored, seen = deepcopy(after), set()
    targets = _elements(restored)
    for op in operations:
        ident = op['object_id']
        if ident in seen:
            raise ValueError('duplicate inverse operation')
        seen.add(ident)
        if op['kind'] == 'code_alt_text':
            if set(op) != {'kind', 'object_id', 'before', 'after'} or ident not in original or ident not in actual:
                raise ValueError('code metadata identity mismatch')
            old, new = op['before'], op['after']
            if (not isinstance(old, dict) or not isinstance(new, dict)
                    or set(old) - {'title', 'description'} or set(new) - {'title', 'description'}
                    or old == new):
                raise ValueError('unsupported metadata operation')
            for name in ('title', 'description'):
                if ({name: original[ident][name]} if name in original[ident] else {}) != ({name: old[name]} if name in old else {}):
                    raise ValueError('metadata before mismatch')
                if ({name: actual[ident][name]} if name in actual[ident] else {}) != ({name: new[name]} if name in new else {}):
                    raise ValueError('metadata after mismatch')
                if old.get(name) != new.get(name):
                    prior, current = old.get(name, ''), new.get(name, '')
                    appended = current[len(prior):].strip() if isinstance(current, str) and isinstance(prior, str) and current.startswith(prior) else ''
                    if not (_codes(appended) or (_codes(prior) and _codes(current))):
                        raise ValueError('metadata change contains more than embedded codes')
                targets[ident].pop(name, None)
                if name in old:
                    targets[ident][name] = old[name]
        elif op['kind'] == 'add_off_canvas_codes':
            if set(op) != {'kind', 'object_id', 'page_id'} or ident in original or ident not in actual:
                raise ValueError('new code identity mismatch')
            _off_canvas_code(actual[ident], after)
            slides = [s for s in restored['slides'] if s['objectId'] == op['page_id']]
            if len(slides) != 1 or ident not in {e['objectId'] for e in slides[0].get('pageElements', [])}:
                raise ValueError('added code page mismatch')
            slides[0]['pageElements'] = [e for e in slides[0]['pageElements'] if e['objectId'] != ident]
        else:
            raise ValueError('unsupported technical operation')
    expected = deepcopy(before)
    expected.pop('revisionId'); restored.pop('revisionId')
    if restored != expected:
        raise ValueError('inverse proof changed teaching content, answers, style, geometry or media')


def verify(receipt, scope, previous, current, form_receipt):
    """Validate actual private proof, never browser/user-provided assertions."""
    bounded_json(receipt)  # Keep the existing shared-state 250KB limit unchanged.
    if (not isinstance(receipt, dict)
            or receipt.get('schema') != SCHEMA or receipt.get('record_key') != receipt_key(scope, previous, current)
            or receipt.get('scope') != {k: scope[k] for k in ('class', 'form', 'file')}):
        raise ValueError('private repair scope mismatch')
    if _form(receipt['submission_receipt'], scope) != _form(form_receipt, scope):
        raise ValueError('repair belongs to another Form event')
    before, after = receipt['before'], receipt['after']
    for item, captured in ((before, previous), (after, current)):
        meta = item['metadata']
        required = {'id', 'mimeType', 'name', 'version', 'modifiedTime', 'trashed', 'parents', 'owners', 'permissions'}
        if (not required.issubset(meta) or meta['id'] != scope['file'] or meta['trashed'] is not False
                or meta['mimeType'] != completion.SLIDES or not meta['owners'] or not meta['permissions']):
            raise ValueError('source access and location proof incomplete')
        binding = item['capture']
        if (binding['file'] != captured['file'] or binding['sha256'] != captured['sha256']
                or any(not HASH.fullmatch(binding.get(k, '')) for k in ('sha256', 'raw_sha256', 'metadata_sha256'))
                or binding['metadata_sha256'] != json_hash(meta)
                or captured['file'].get('id') != scope['file']
                or captured['file'].get('version') != meta['version']
                or captured['file'].get('modified_at') != meta['modifiedTime']
                or captured['file'].get('mime_type') != meta['mimeType']):
            raise ValueError('exact source capture mismatch')
        for field in ('raw_sha256', 'metadata_sha256'):
            if captured.get(field) is not None and captured[field] != binding[field]:
                raise ValueError('raw source capture mismatch')
    if not current.get('raw_sha256') or not current.get('metadata_sha256'):
        raise ValueError('fresh raw capture unavailable')
    old_meta, new_meta = deepcopy(before['metadata']), deepcopy(after['metadata'])
    old_version, new_version = old_meta.pop('version'), new_meta.pop('version')
    old_time, new_time = old_meta.pop('modifiedTime'), new_meta.pop('modifiedTime')
    if int(new_version) <= int(old_version) or instant(new_time) < instant(old_time) or old_meta != new_meta:
        raise ValueError('source identity, access or location changed')
    old_native, old_pages = _native(before, scope['file'])
    new_native, new_pages = _native(after, scope['file'])
    if old_pages != new_pages:
        raise ValueError('teaching pages changed')
    _inverse(old_native, new_native, receipt['operations'])
    attestation = receipt['attestation']
    if (set(attestation) != {'kind', 'operator', 'verified_at', 'pages'}
            or attestation['kind'] != 'private_host_source_readback'
            or not re.fullmatch(r'[^@\s]{1,200}@[^@\s]{1,200}', attestation['operator'])
            or instant(attestation['verified_at']) < instant(new_time)
            or not isinstance(attestation['pages'], list)
            or [p.get('page_id') for p in attestation['pages']] != old_pages):
        raise ValueError('trusted all-page source readback unavailable')
    for page in attestation['pages']:
        if (set(page) != {'page_id', 'before_render_sha256', 'after_render_sha256'}
                or any(not HASH.fullmatch(page[k]) for k in ('before_render_sha256', 'after_render_sha256'))):
            raise ValueError('actual visual review capture unavailable')
    return {'record_key': receipt['record_key'], 'receipt_sha256': json_hash(receipt),
            'original_submitted_at': form_receipt['submitted_at']}


def classify(port, scope, previous, inputs, current_basis):
    """Changed work from the same Form event is held unless provenance proves it.

    A later authenticated Form response follows the ordinary freshness path.
    No prior raw source is relabelled, no report/growth point is persisted, and
    uncertainty never falls back to automatic regeneration.
    """
    current, current_form = inputs['source'], inputs.get('submission_receipt')
    try:
        unchanged = (previous['source'] == {k: current[k] for k in previous['source']})
        if unchanged:
            return {'classification': 'unchanged', 'eligible': True}
        fresh_form = _form(current_form, scope)
        old_form = _form(previous.get('submission_receipt'), scope)
        if fresh_form != old_form:
            if (fresh_form['source_form_id'] == old_form['source_form_id']
                    and work.when(fresh_form['submitted_at']) > work.when(old_form['submitted_at'])):
                return {'classification': 'actual_student_submission_change', 'eligible': True}
            raise ValueError('ambiguous changed Form event')
        ident = receipt_key(scope, previous['source'], current)
        stored = port.read([ident]).documents[ident]
        if not stored:
            raise ValueError('trusted repair receipt unavailable')
        proof = verify(stored.value, scope, previous['source'], current, fresh_form)
        confirmed = port.read([ident]).documents[ident]
        if not confirmed or confirmed.version != stored.version or confirmed.value != stored.value:
            raise ValueError('private attestation changed during verification')
        same_basis = (previous.get('analysis_basis_sha256') == current_basis
                      and all(previous['source'].get(k) for k in ('raw_sha256', 'metadata_sha256'))
                      and previous.get('saved_at'))
        return {**proof, 'classification': 'same_submission_code_repair' if same_basis else 'corrected_analysis_review',
                'eligible': False, 'reuse_saved': bool(same_basis), 'saved_feedback_id': previous['id']}
    except (ValueError, TypeError, KeyError, AttributeError, StoreUnavailable, OverflowError, RecursionError, zlib.error):
        return {'classification': 'unknown', 'eligible': False, 'reuse_saved': False,
                'problem': 'This same-submission source change needs its trusted technical-repair provenance or a new verified Form submission. Saved feedback remains available.'}
