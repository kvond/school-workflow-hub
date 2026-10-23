"""Trusted-host repairs of known damaged codes, never an answer editor.

The host supplies an exact original/Form proof and complete response-region
manifest for each affected page. This module does not discover identity, decide
which question was assigned, synchronize inventory or authorize assessment.
An explicit in-place restoration can append one inventory-declared missing code
to its exact original response box while preserving every original character.
It cannot remove text, combine response regions, add questions or clear a hold.
The separate exact-ID correction path may replace one whole valid but
undeclared NOTES/DRAFT token with the sole missing ID in the original inventory.
It requires the original paired response and separately authenticated registered
teacher corroboration of the same original objects. It never borrows a task.
There is deliberately no browser endpoint. ``guard`` must reread the host's
original Form/Agenda/ownership and question-set proofs and return their unchanged digests.
Grouped fixed text shapes may use exact original response-panel containment only
with a separate frozen handed-out reference and fresh provenance proof. This is
a preparation contract: the production score_file caller has no immutable
original-reference receipt loader yet, so ordinary assessment of this binding
stays held. Repair-only injected proof is not operational integration.

Dry-run is the default. Apply first saves an uncertainty fence in the existing
private CAS port. Once a Slides request may have started, no failure is retried
or automatically cleared. A verified result proves only the enumerated repairs;
the host must finish the independent full original-question census.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

from . import completion, google, native_teacher_source, response_binding, student_archive
from school_workflow_hub.core.cloud_state import key

FIELDS = 'id,name,mimeType,trashed,version,modifiedTime,parents,owners,permissions'
ID = re.compile(r'[A-Za-z0-9_-]{1,200}')
HASH = re.compile(r'[a-f0-9]{64}')
MAX_OPERATIONS = 20
EXACT_ID_REPLACEMENT = 'replace_exact_undeclared_inventory_code'


class Refused(ValueError):
    """No source write was attempted."""


class Uncertain(RuntimeError):
    """The durable source fence remains closed; inspect, never retry."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def _need(condition, message):
    if not condition:
        raise Refused(message)


def _units(text):
    raw = text.encode('utf-16-le')
    return [raw[n:n + 2] for n in range(0, len(raw), 2)]


def _boundary(text, index):
    _need(type(index) is int and 0 <= index <= len(_units(text)), 'Invalid UTF-16 index.')
    try:
        raw = text.encode('utf-16-le')
        return len(raw[:2 * index].decode('utf-16-le'))
    except UnicodeDecodeError:
        raise Refused('The index splits an original Unicode character.') from None


def _text(element):
    return ''.join(run.get('textRun', {}).get('content', '')
                   for run in element['shape'].get('text', {}).get('textElements', []))


def _reader_text(text):
    # Native Slides represents a soft line break as VT; its exported a:br is
    # intentionally not a text node in completion._text. This is only a
    # cross-reader region comparison, never a source-text transformation.
    return text.replace('\v', '').strip()


def _chars(element):
    """Character and paragraph styling, independent of harmless run splitting."""
    body = element['shape'].get('text', {})
    _need(set(body) == {'textElements'}, 'Unsupported original text container metadata.')
    runs = body['textElements']
    text = _text(element)
    chars, paragraphs = [None] * len(_units(text)), [None] * len(_units(text))
    for run in runs:
        a, b = run.get('startIndex', 0), run.get('endIndex')
        _need(type(a) is int and type(b) is int and 0 <= a < b <= len(chars),
              'Incomplete original text range.')
        if 'textRun' in run:
            _need(set(run) <= {'startIndex', 'endIndex', 'textRun'}
                  and set(run['textRun']) <= {'content', 'style'}, 'Unsupported original text run.')
            value = _units(run['textRun']['content'])
            _need(len(value) == b - a and not any(c is not None for c in chars[a:b]),
                  'Overlapping or incomplete original text.')
            chars[a:b] = [[c.hex(), deepcopy(run['textRun'].get('style', {}))] for c in value]
        elif 'paragraphMarker' in run:
            _need(set(run) <= {'startIndex', 'endIndex', 'paragraphMarker'}, 'Unsupported paragraph metadata.')
            _need(not any(p is not None for p in paragraphs[a:b]), 'Overlapping original paragraphs.')
            paragraphs[a:b] = [deepcopy(run['paragraphMarker']) for _ in range(a, b)]
        else:
            raise Refused('Unsupported auto text or text metadata.')
    _need(chars and all(c is not None for c in chars) and all(p is not None for p in paragraphs),
          'Complete original text and paragraph styles are required.')
    _need([c[0] for c in chars] == [u.hex() for u in _units(text)], 'Original text run order is inconsistent.')
    return [[c, p] for c, p in zip(chars, paragraphs)]


def _code_style_requests(ident, element, at, insertion, remove=()):
    """Restore original adjacent newlines after Google's range expansion.

    UpdateTextStyleRequest may extend its range to adjacent newlines. Only the
    two fields changed for a marker are restored, including clearing a field
    that was originally inherited. A restore can itself extend across a block
    of newlines, so differently styled adjacent blocks are refused in preview.
    All other original style fields and the exact readback comparison remain.
    """
    chars = _chars(element)
    for span in reversed(remove):
        del chars[span['start']:span['end']]
    _need(type(at) is int and 0 <= at <= len(chars), 'Invalid code styling position.')
    size = len(_units(insertion))
    fields = 'fontSize,foregroundColor'
    result = [{'updateTextStyle': {'objectId': ident, 'textRange': {
        'type': 'FIXED_RANGE', 'startIndex': at, 'endIndex': at + size},
        'style': {'fontSize': {'magnitude': 6, 'unit': 'PT'},
                  'foregroundColor': {'opaqueColor': {'rgbColor': {'red': 1, 'green': 1, 'blue': 1}}}},
        'fields': fields}}]
    left, right = at, at
    while left > 0 and chars[left - 1][0][0] == '0a00':
        left -= 1
    while right < len(chars) and chars[right][0][0] == '0a00':
        right += 1
    for start, end, shift in ((left, at, 0), (at, right, size)):
        if start == end:
            continue
        styles = [{k: deepcopy(ch[0][1][k]) for k in fields.split(',') if k in ch[0][1]}
                  for ch in chars[start:end]]
        _need(all(style == styles[0] for style in styles),
              'Differently styled adjacent original newlines cannot be safely restored.')
        result.append({'updateTextStyle': {'objectId': ident, 'textRange': {
            'type': 'FIXED_RANGE', 'startIndex': start + shift, 'endIndex': end + shift},
            'style': styles[0], 'fields': fields}})
    return result


def _elements(native):
    result = {}

    def visit(elements, number, page, parent, depth):
        _need(isinstance(elements, list) and depth <= 20 and len(result) <= 20_000,
              'The original object tree exceeds the bounded group proof.')
        for element in elements:
            _need(len(result) < 20_000, 'The original object tree exceeds the bounded group proof.')
            ident = element.get('objectId')
            _need(isinstance(ident, str) and ID.fullmatch(ident) and ident not in result,
                  'Ambiguous original object identity.')
            matrix = None
            if parent is not None and element.get('transform'):
                try:
                    matrix = native_teacher_source._compose(
                        parent, native_teacher_source._transform(element['transform']))
                except native_teacher_source.Unreadable as error:
                    raise Refused(str(error)) from error
            result[ident] = (number, page, element, matrix)
            if 'elementGroup' in element:
                visit(element['elementGroup'].get('children'), number, page, matrix, depth + 1)

    for number, page in enumerate(native['slides'], 1):
        visit(page.get('pageElements', []), number, page['objectId'], (1, 0, 0, 1, 0, 0), 0)
    return result


def _shape(elements, ident, page):
    _need(ident in elements and elements[ident][1] == page, 'The exact original object is absent.')
    element = elements[ident][2]
    _need('shape' in element and 'table' not in element and 'elementGroup' not in element,
          'Only existing text shapes are supported; tables and groups themselves are refused.')
    _chars(element)
    return element


def _fixed_text(element, role):
    # Text edits deactivate autofit and bake its scale/spacing into existing
    # styles. An omitted/unspecified type can inherit from a placeholder; do
    # not assume it means NONE or learn this only after changing the source.
    autofit = element['shape'].get('shapeProperties', {}).get('autofit', {})
    _need(isinstance(autofit, dict) and autofit.get('autofitType') == 'NONE'
          and type(autofit.get('fontScale', 1)) in (int, float) and autofit.get('fontScale', 1) == 1
          and type(autofit.get('lineSpacingReduction', 0)) in (int, float)
          and autofit.get('lineSpacingReduction', 0) == 0,
          'An auto-fitting %s or unresolved effective autofit could restyle original text; '
          'an explicitly fixed original text shape is required.' % role)


def _declared_inventory(census, parsed_markers, parsed):
    """Validate the declaration itself while allowing its known missing codes."""
    _need(not {'marker_inventory_missing_or_duplicated', 'marker_inventory_not_on_first_slide'}
          .intersection(census['issues']), 'The declared inventory must be unique and on slide 1.')
    line, cycle = census['inventory_text'], census['inventory_cycle']
    # Count assignments before validating values, including blank duplicates.
    # Read every box on the inventory slide: the ordinary inventory reader
    # omits a box containing only an unparseable count such as "NOTES=".
    count_text = '\n'.join(u['text'] for s in parsed['slides'] if s['number'] == 1 for u in s['units'])
    fields = re.findall(r'\b(NOTES|DRAFT|OPTIONAL|BANK_TERMS)=([^\s]*)', count_text)
    _need(len(fields) == len(completion.CATEGORIES)
          and {k for k, _ in fields} == {'NOTES', 'DRAFT', 'OPTIONAL', 'BANK_TERMS'}
          and all(re.fullmatch(r'[0-9]{1,6}', value) for _, value in fields),
          'The declared inventory needs exactly one valid total per category.')
    counts = {('BANK' if k == 'BANK_TERMS' else k): int(value) for k, value in fields}
    declared = {}
    for category, value in completion.ID_LIST.findall(line):
        ids = [' '.join(v.split()) for v in re.split(r'[,\n]', value) if v.strip()]
        if ids == ['-'] and counts[category] == 0:
            ids = []
        _need(category not in declared and len(ids) == len(set(ids))
              and len(ids) == counts[category]
              and all(ident != '-' and completion.MARKER.fullmatch(
                  '[[%s:%s:%s]]' % (category, cycle, ident)) for ident in ids),
              'The declared inventory repeats IDs/lists or its IDs do not match its own counts.')
        declared[category] = ids
    for category in completion.CATEGORIES:
        actual = {m['id'] for m in parsed_markers if m['category'] == category}
        if category not in declared:
            # Existing inventories may omit BANK_IDS. Its complete observed
            # census must then prove that count; no missing ID is invented.
            _need(len(actual) == counts[category],
                  'A missing declared ID list cannot prove the original question count.')
            declared[category] = sorted(actual)
        _need(actual <= set(declared[category]),
              'An existing code is outside the declared inventory.')
    _need(set(declared['DRAFT']) <= set(declared['NOTES']),
          'The declared inventory has a revision without its original Notes ID.')
    return declared


def _box(element, matrix=None):
    def size(value):
        _need(value.get('unit') in ('PT', 'EMU'), 'Unverified native geometry unit.')
        n = value.get('magnitude')
        _need(type(n) in (int, float) and math.isfinite(n), 'Unverified native geometry.')
        return n * (12700 if value['unit'] == 'PT' else 1)
    if matrix is None:
        try:
            matrix = native_teacher_source._transform(element.get('transform', {}))
        except native_teacher_source.Unreadable as error:
            raise Refused(str(error)) from error
    a, b, c, d, x, y = matrix
    _need(b == c == 0 and a > 0 and d > 0,
          'Rotated, sheared or reflected response regions require separate verification.')
    w, h = [size(element.get('size', {}).get(k, {})) for k in ('width', 'height')]
    _need(w > 0 and h > 0, 'The original response region is empty.')
    return x, y, a * w, d * h


def _element_box(elements, ident):
    _need(elements[ident][3] is not None, 'The original shape or ancestor group has no verified transform.')
    return _box(elements[ident][2], elements[ident][3])


def _media(native, token):
    urls = set()
    def visit(value):
        if isinstance(value, dict):
            if value.get('contentUrl'):
                urls.add(value['contentUrl'])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(native)
    _need(len(urls) <= 500, 'The source exceeds the bounded media proof.')
    return {url: student_archive._native_image_proof(url, token) for url in sorted(urls)}


def _canonical(native, images):
    def visit(value):
        if isinstance(value, dict):
            return {k: deepcopy(images[v]) if k == 'contentUrl' and v else visit(v)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v) for v in value]
        return value
    result = visit(native)
    result.pop('revisionId', None)
    return result


def capture(token, file_id):
    """Read full native fields, normal PPTX parser, media and access bookends."""
    _need(isinstance(file_id, str) and ID.fullmatch(file_id), 'One exact original file is required.')
    meta = google.drive_file(token, file_id, FIELDS)
    _need(meta.get('id') == file_id and meta.get('mimeType') == completion.SLIDES
          and meta.get('trashed') is False and meta.get('owners') and meta.get('permissions')
          and meta.get('parents') and str(meta.get('version', '')).isdigit() and meta.get('modifiedTime'),
          'The original identity, location, access and revision must be readable.')
    native = google.native_presentation(token, file_id)
    _need(native.get('presentationId') == file_id and native.get('revisionId')
          and isinstance(native.get('slides'), list) and 1 <= len(native['slides']) <= 200,
          'The complete original presentation is unavailable.')
    images = _media(native, token)
    parsed = completion._read(token, file_id, meta)
    _need(google.drive_file(token, file_id, FIELDS) == meta, 'The source changed during capture.')
    return {'metadata': meta, 'native': native, 'images': images, 'parsed': parsed,
            'native_sha256': digest(_canonical(native, images)), 'metadata_sha256': digest(meta)}


def _scope(proof, file_id):
    fields = {'class', 'week', 'school_year', 'assignment_form_id', 'source_form_id',
              'source_response_id', 'source_file_id', 'submitted_at', 'operator', 'original_proof_sha256',
              'question_set_status', 'question_set_sha256'}
    optional = {'panel_reference_sha256', 'panel_reference_proof_sha256'}
    teacher = {'registered_teacher_sha256', 'registered_teacher_proof_sha256'}
    _need(isinstance(proof, dict) and set(proof) in (fields, fields | optional, fields | teacher)
          and all(isinstance(v, str) and v.strip() for v in proof.values())
          and proof['source_file_id'] == file_id and HASH.fullmatch(proof['original_proof_sha256'])
          and proof['question_set_status'] in ('complete', 'incomplete') and HASH.fullmatch(proof['question_set_sha256']),
          'Exact host-verified original/Form/Agenda proof is required.')
    _need(not optional.intersection(proof) or all(HASH.fullmatch(proof[k]) for k in optional),
          'A host-verified handed-out reference proof is required.')
    _need(not teacher.intersection(proof) or all(HASH.fullmatch(proof[k]) for k in teacher),
          'A host-verified registered teacher/source-role proof is required.')


def _guard(guard, proof):
    fields = ('original_proof_sha256', 'question_set_sha256', 'question_set_status',
              'panel_reference_sha256', 'panel_reference_proof_sha256',
              'registered_teacher_sha256', 'registered_teacher_proof_sha256')
    return guard() == {k: proof[k] for k in fields if k in proof}


def _reference(token, proof, spec, file_id):
    if spec is None:
        _need('panel_reference_sha256' not in proof, 'The proven handed-out reference is missing.')
        return None
    _need(isinstance(spec, dict) and set(spec) == {'file_id', 'revision', 'native_sha256', 'metadata_sha256'}
          and isinstance(spec['file_id'], str) and ID.fullmatch(spec['file_id']) and spec['file_id'] != file_id
          and spec['revision'] and all(isinstance(spec[k], str) and HASH.fullmatch(spec[k])
                                      for k in ('native_sha256', 'metadata_sha256'))
          and digest(spec) == proof.get('panel_reference_sha256') and proof.get('panel_reference_proof_sha256'),
          'The actual handed-out reference must be independently proven and revision-bound.')
    reference = capture(token, spec['file_id'])
    _need(reference['native']['revisionId'] == spec['revision']
          and all(reference[k] == spec[k] for k in ('native_sha256', 'metadata_sha256')),
          'The authenticated handed-out reference changed or is unavailable.')
    reference['authority'] = response_binding.OriginalPanelReference(reference['parsed'],
        source_id=spec['file_id'], revision=spec['revision'], provenance_sha256=proof['panel_reference_proof_sha256'])
    return reference


def _panel_binding(before, reference, op, elements, label):
    _need(reference is not None, 'Panel containment requires the authenticated handed-out reference.')
    _need(before['parsed']['size'] == reference['parsed']['size'],
          'The original and handed-out reference page sizes differ.')
    ident = op['panel_object_id']
    _need(ident in op['response_object_ids'], 'The exact original panel must be in its response manifest.')
    _shape(elements, ident, op['page_id'])
    panel_box = _element_box(elements, ident)
    marker = completion.MARKER.fullmatch(op['marker'])
    code = dict(zip(('category', 'code', 'id'), marker.groups()))
    label_unit = {'text': _text(label), 'box': _element_box(elements, op['label_object_id'])}
    options = [response_binding.panel_binding(code, slide, label_unit, before['parsed']['size'],
                                            completion._plain, completion.MARKER)
               for slide in reference['parsed']['slides']]
    options = [data for data in options if data and any(p['category'] == code['category']
               and response_binding.same_box(p['box'], panel_box, before['parsed']['size']) for p in data['panels'])]
    _need(len(options) == 1, 'The exact original panel has no unique authenticated handed-out source.')
    _need(all(response_binding._contains(panel_box, _element_box(elements, i), before['parsed']['size'])
              for i in op['response_object_ids']), 'A response leaves its exact original panel.')
    _need(label.get('description', '') == '', 'Existing label descriptions must be preserved.')
    return options[0]


def _teacher(token, proof, spec, file_id):
    """Corroborate original objects; never supply a teacher question inventory."""
    if spec is None:
        _need('registered_teacher_sha256' not in proof, 'The registered teacher corroboration is missing.')
        return None
    _need(isinstance(spec, dict) and set(spec) == {'file_id', 'revision', 'native_sha256', 'metadata_sha256'}
          and isinstance(spec['file_id'], str) and ID.fullmatch(spec['file_id']) and spec['file_id'] != file_id
          and isinstance(spec['revision'], str) and spec['revision']
          and all(isinstance(spec[k], str) and HASH.fullmatch(spec[k])
                  for k in ('native_sha256', 'metadata_sha256'))
          and digest(spec) == proof.get('registered_teacher_sha256')
          and proof.get('registered_teacher_proof_sha256'),
          'The exact registered teacher role and revision need independent host proof.')
    teacher = capture(token, spec['file_id'])
    _need(teacher['native']['revisionId'] == spec['revision']
          and all(teacher[k] == spec[k] for k in ('native_sha256', 'metadata_sha256')),
          'The exact registered teacher corroboration changed or is unavailable.')
    return teacher


def _replacement_plan(before, operations, teacher):
    """One exact token, with its denominator established only by the original."""
    fields = {'marker', 'original_marker', 'page_id', 'label_object_id', 'source_object_id',
              'remove', 'response_object_ids', 'page_response_object_ids', 'placement', 'paired_object_id'}
    _need(len(operations) == 1 and isinstance(operations[0], dict) and set(operations[0]) == fields
          and teacher is not None, 'Exact-ID correction needs one typed operation and registered teacher proof.')
    op = operations[0]
    _need(all(len(source['parsed']['slides']) == len(source['native']['slides'])
              for source in (before, teacher)), 'Complete original and registered teacher slide coverage is required.')
    new = completion.MARKER.fullmatch(op['marker']) if isinstance(op['marker'], str) else None
    old = completion.MARKER.fullmatch(op['original_marker']) if isinstance(op['original_marker'], str) else None
    _need(old is not None and new is not None and old.group(1) in ('NOTES', 'DRAFT')
          and old.groups()[:2] == new.groups()[:2] and old.group(3) != new.group(3)
          and all(m.group(3) == m.group(3).strip() for m in (old, new)),
          'Only one whole valid same-category/cycle NOTES/DRAFT ID can be corrected.')
    elements = _elements(before['native'])
    ident, paired, page = op['label_object_id'], op['paired_object_id'], op['page_id']
    _need(op['source_object_id'] == ident and op['response_object_ids'] == [ident]
          and isinstance(op['page_response_object_ids'], list)
          and len(op['page_response_object_ids']) == 2
          and set(op['page_response_object_ids']) == {ident, paired} and ident != paired,
          'Correction requires the exact original response and its distinct paired response.')
    target, pair = _shape(elements, ident, page), _shape(elements, paired, page)
    _fixed_text(target, 'original response')
    text = _text(target)
    _need([m.group(0) for m in completion.MARKER.finditer(text)] == [op['original_marker']]
          and not completion.INVENTORY.search(text), 'The response needs exactly one original erroneous token.')
    opposite = 'DRAFT' if new.group(1) == 'NOTES' else 'NOTES'
    paired_marker = '[[%s:%s:%s]]' % (opposite, new.group(2), new.group(3))
    _need([m.group(0) for m in completion.MARKER.finditer(_text(pair))] == [paired_marker],
          'The same original page must retain exactly one paired opposite-category target.')
    spans = op['remove']
    _need(isinstance(spans, list) and len(spans) == 1 and isinstance(spans[0], dict)
          and set(spans[0]) == {'start', 'end', 'text'}, 'One exact whole-token UTF-16 span is required.')
    span = spans[0]
    a, b = _boundary(text, span['start']), _boundary(text, span['end'])
    _need(a < b and text[a:b] == span['text'] == op['original_marker'],
          'The exact original token range changed; answer characters cannot be removed.')
    census = completion.readiness(before['parsed'])
    _need(census['inventory_cycle'] == new.group(2)
          and census['missing_ids'] == ['%s %s' % (new.group(1), new.group(3))]
          and census['extra_ids'] == ['%s %s' % (old.group(1), old.group(3))]
          and set(census['issues']) <= {'marker_inventory_id_mismatch', 'draft_without_matching_notes'},
          'Only the sole declared missing/undeclared original ID pair may change.')
    codes = completion.markers(before['parsed'])
    tuples = [(m['category'], m['code'], m['id']) for m in codes]
    _need(tuples.count(old.groups()) == 1 and new.groups() not in tuples
          and tuples.count((opposite, new.group(2), new.group(3))) == 1,
          'Original, target or paired codes are duplicated, absent or already repaired.')
    normalized_codes = [dict(m, id=new.group(3)) if (m['category'], m['code'], m['id']) == old.groups()
                        else m for m in codes]
    declared = _declared_inventory(census, normalized_codes, before['parsed'])
    _need(all(old.group(3) not in ids for ids in declared.values()),
          'The erroneous ID belongs to an original declared task.')
    # Exact original object/page plus every other text object on this page
    # corroborates the question. Only the two response texts may differ.
    te = _elements(teacher['native'])
    _need(teacher['native'].get('pageSize') == before['native'].get('pageSize'),
          'Registered teacher/original page sizes differ.')
    original_texts = {i for i, e in elements.items() if e[1] == page and 'shape' in e[2]}
    teacher_texts = {i for i, e in te.items() if e[1] == page and 'shape' in e[2]}
    context = original_texts - {ident, paired}
    _need(original_texts == teacher_texts and context
          and any(_text(elements[i][2]).strip() for i in context)
          and all(_text(elements[i][2]) == _text(te[i][2]) for i in context),
          'The registered teacher page/object question context does not match the original.')
    for object_id, marker in ((ident, op['marker']), (paired, paired_marker)):
        teacher_shape = _shape(te, object_id, page)
        _need([m.group(0) for m in completion.MARKER.finditer(_text(teacher_shape))] == [marker],
              'The exact registered teacher response object does not corroborate the original target.')
    projected = deepcopy(before['parsed'])
    slide = next(s for s in projected['slides'] if s['number'] == elements[ident][0])
    units = [u for u in slide['units'] if not u.get('picture')
             and response_binding.same_box(u.get('box'), _element_box(elements, ident), projected['size'])
             and u['text'].strip() == _reader_text(text)]
    _need(len(units) == 1, 'The exact original token has no unique exported-source region.')
    units[0]['text'] = _reader_text(text[:a] + op['marker'] + text[b:])
    ready = completion.readiness(projected)
    _need(ready['status'] == 'READY' and ready['counts'] == census['counts'],
          'Correction must resolve only this ID defect without changing the original denominator.')
    edits = {ident: {'remove': deepcopy(spans), 'insert': (span['start'], op['marker'])}}
    requests = [{'deleteText': {'objectId': ident, 'textRange': {'type': 'FIXED_RANGE',
                 'startIndex': span['start'], 'endIndex': span['end']}}},
                {'insertText': {'objectId': ident, 'insertionIndex': span['start'], 'text': op['marker']}}]
    requests += _code_style_requests(ident, elements[ident][2], span['start'], op['marker'], spans)
    return requests, edits


def _plan(before, operations, reference=None, registered_teacher=None):
    _need(isinstance(operations, list) and 1 <= len(operations) <= MAX_OPERATIONS,
          'A bounded exact code manifest is required.')
    if any(isinstance(op, dict) and op.get('placement') == EXACT_ID_REPLACEMENT for op in operations):
        _need(reference is None, 'Exact-ID correction cannot mix panel-reference operations.')
        return _replacement_plan(before, operations, registered_teacher)
    _need(registered_teacher is None, 'Registered teacher corroboration is only for exact-ID correction.')
    native = before['native']
    elements, requests, edits, seen, pages = _elements(native), [], {}, set(), {}
    all_text = '\n'.join(_text(e[2]) for e in elements.values() if 'shape' in e[2])
    inventories = list(completion.INVENTORY.finditer(all_text))
    _need(len(inventories) == 1, 'Exactly one unchanged original inventory is required.')
    census = completion.readiness(before['parsed'])
    parsed_markers = completion.markers(before['parsed'])
    parsed_codes = [(m['category'], m['code'], m['id']) for m in parsed_markers]
    _need(len(parsed_codes) == len(set(parsed_codes))
          and all(m['code'] == inventories[0].group(1) for m in parsed_markers),
          'Existing duplicate or wrong-cycle codes need separate verification.')
    parsed_inventories = [m for s in before['parsed']['slides'] for u in s['units']
                          for m in completion.INVENTORY.finditer(u['text'])]
    _need(len(parsed_inventories) == 1, 'The normal source reader found an ambiguous inventory.')
    _declared_inventory(census, parsed_markers, before['parsed'])
    for op in operations:
        fields = {'marker', 'page_id', 'label_object_id', 'source_object_id', 'remove',
                  'response_object_ids', 'page_response_object_ids'}
        _need(isinstance(op, dict) and set(op) in (fields, fields | {'panel_object_id'},
                                                    fields | {'placement'}),
              'Unexpected code-repair operation fields.')
        in_place = op.get('placement') == 'in_place'
        _need('placement' not in op or in_place, 'Unsupported original code placement.')
        marker = completion.MARKER.fullmatch(op['marker']) if isinstance(op['marker'], str) else None
        _need(marker is not None and marker.group(1) in ('NOTES', 'DRAFT')
              and marker.group(2) == inventories[0].group(1) and marker.group(3) == marker.group(3).strip(),
              'Only exact original NOTES/DRAFT codes are supported.')
        _need(op['marker'] not in seen and op['marker'] not in all_text and marker.groups() not in parsed_codes,
              'The code is duplicated or already present.')
        seen.add(op['marker'])
        _need('%s %s' % (marker.group(1), marker.group(3)) in census['missing_ids'],
              'The repair cannot invent inventory entries or change required question counts.')
        label = _shape(elements, op['label_object_id'], op['page_id'])
        _fixed_text(label, 'label')
        response_ids, page_ids = op['response_object_ids'], op['page_response_object_ids']
        _need(isinstance(response_ids, list) and response_ids and isinstance(page_ids, list)
              and set(response_ids) <= set(page_ids) and len(set(page_ids)) == len(page_ids)
              and len(set(response_ids)) == len(response_ids)
              and ((in_place and response_ids == [op['label_object_id']])
                   or (not in_place and op['label_object_id'] not in page_ids)),
              ('An exact original response and complete original response-region manifest are required.'
               if in_place else 'A separate verified label and complete original response-region manifest are required.'))
        for ident in page_ids:
            _shape(elements, ident, op['page_id'])
        _need(op['page_id'] not in pages or pages[op['page_id']] == set(page_ids),
              'Each repaired page needs one consistent complete response-region manifest.')
        pages[op['page_id']] = set(page_ids)
        binding = _panel_binding(before, reference, op, elements, label) if 'panel_object_id' in op else None
        lx, ly, lw, lh = _element_box(elements, op['label_object_id'])
        for ident in ([] if in_place else [op['panel_object_id']] if binding else response_ids):
            x, y, w, _ = _element_box(elements, ident)
            _need(ly + lh <= y + 127 and y - (ly + lh) <= .08 * before['parsed']['size'][1]
                  and max(lx, x) < min(lx + lw, x + w),
                  'The verified code label must be separate and immediately above its response region.')
        _need(not completion.MARKER.search(_text(label)) and not completion.INVENTORY.search(_text(label)),
              'The existing code location already contains a code or inventory.')
        _need(op['label_object_id'] not in edits, 'One label cannot receive multiple repairs.')
        text = _text(label)
        _need(text.endswith('\n') and text.strip(), 'An existing printed label is required.')
        index, insertion = len(_units(text[:-1])), ' ' + op['marker']
        if in_place:
            # Preserve every original character. A malformed token, student
            # writing and paragraph structure are never interpreted or removed
            # by this path. The host proves the original question/object; the
            # normal parser must bind the sole restored code to this exact box.
            _need(op['source_object_id'] is None and op['remove'] == [],
                  'In-place restoration preserves all original text; no removal is allowed.')
        edits[op['label_object_id']] = {'remove': [], 'insert': (index, insertion)}
        if binding:
            description = response_binding.PREFIX + json.dumps(binding, sort_keys=True, separators=(',', ':'))
            edits[op['label_object_id']]['description'] = description
            requests.append({'updatePageElementAltText': {'objectId': op['label_object_id'], 'description': description}})
        requests.append({'insertText': {'objectId': op['label_object_id'], 'insertionIndex': index, 'text': insertion}})
        requests += _code_style_requests(op['label_object_id'], label, index, insertion)
        source, ranges = op['source_object_id'], op['remove']
        _need(isinstance(ranges, list) and len(ranges) <= 2, 'Only bounded exact code fragments may be removed.')
        if source is None:
            _need(not ranges, 'An absent code has no source fragments to remove.')
            continue
        _need(source in response_ids and source not in edits and ranges,
              'The code fragments need one exact original response object.')
        original_shape = _shape(elements, source, op['page_id'])
        _fixed_text(original_shape, 'response')
        original = _text(original_shape)
        fragments, last = [], -1
        for span in ranges:
            _need(isinstance(span, dict) and set(span) == {'start', 'end', 'text'}, 'Invalid exact code fragment.')
            a, b = _boundary(original, span['start']), _boundary(original, span['end'])
            _need(span['start'] >= last and a < b and original[a:b] == span['text']
                  and '\n' not in span['text'] and '\r' not in span['text'],
                  'The code range overlaps, changed, or crosses original paragraphs.')
            fragments.append(span['text']); last = span['end']
        joined = ''.join(fragments)
        if len(fragments) == 1:
            _need(joined != op['marker'] and re.sub(r'[ \t]', '', joined) == op['marker'],
                  'Only malformed code whitespace may be removed as one fragment.')
        else:
            _need(joined == op['marker'] and fragments[0].startswith('[[') and fragments[1].endswith(']]')
                  and ranges[0]['end'] < ranges[1]['start'], 'Split fragments do not form the exact original code.')
        edits[source] = {'remove': deepcopy(ranges), 'insert': None}
        requests += [{'deleteText': {'objectId': source, 'textRange': {'type': 'FIXED_RANGE',
                     'startIndex': s['start'], 'endIndex': s['end']}}} for s in reversed(ranges)]
    return requests, edits


def _preview_bindings(before, operations, edits, reference=None):
    """Predict the real reader's binding before a source write can start."""
    projected = deepcopy(before)
    old, new = _elements(before['native']), _elements(projected['native'])
    for ident, edit in edits.items():
        number, _, original, _ = old[ident]
        text = _text(original)
        slide = next(s for s in projected['parsed']['slides'] if s['number'] == number)
        units = [u for u in slide['units'] if not u.get('picture')
                 and response_binding.same_box(u.get('box'), _element_box(old, ident), projected['parsed']['size'])
                 and u['text'].strip() == _reader_text(text)]
        _need(len(units) == 1, 'The original target has no unique exported-source region.')
        for span in reversed(edit['remove']):
            a, b = _boundary(text, span['start']), _boundary(text, span['end'])
            text = text[:a] + text[b:]
        if edit['insert']:
            at, insertion = edit['insert']
            at = _boundary(text, at)
            text = text[:at] + insertion + text[at:]
        units[0]['text'] = _reader_text(text)
        if 'description' in edit:
            units[0]['binding'] = response_binding.description(edit['description'])
            new[ident][2]['description'] = edit['description']
        # This in-memory projection is only for binding, never a source write
        # or a preservation proof. Apply uses native range edits and readback.
        new[ident][2]['shape']['text'] = {'textElements': [{'textRun': {'content': text}}]}
    _bindings(projected, operations, reference)


def _preserved(before, after, edits):
    old = _canonical(before['native'], before['images'])
    new = _canonical(after['native'], after['images'])
    oe, ne = _elements(old), _elements(new)
    _need(set(oe) == set(ne), 'Original objects changed during repair.')
    for ident, edit in edits.items():
        a, b = oe[ident][2], ne[ident][2]
        old_chars, new_chars = _chars(a), _chars(b)
        for span in reversed(edit['remove']):
            del old_chars[span['start']:span['end']]
        if edit['insert']:
            at, text = edit['insert']
            n = len(_units(text))
            _need(_text(b)[_boundary(_text(b), at):_boundary(_text(b), at + n)] == text,
                  'The exact added code did not read back.')
            _need(all(c[0][1].get('fontSize') == {'magnitude': 6, 'unit': 'PT'}
                      and c[0][1].get('foregroundColor') == {'opaqueColor': {
                          'rgbColor': {'red': 1, 'green': 1, 'blue': 1}}} for c in new_chars[at:at + n]),
                  'The unobtrusive code styling did not read back.')
            del new_chars[at:at + n]
        _need(old_chars == new_chars, 'Original non-code text or its styling changed.')
        if 'description' in edit:
            _need(a.get('description', '') == '' and b.get('description') == edit['description'],
                  'The exact authenticated panel description did not read back.')
            if 'description' in a:
                b['description'] = a['description']
            else:
                b.pop('description', None)
        a['shape'].pop('text', None); b['shape'].pop('text', None)
    _need(old == new, 'Original geometry, media, notes or other native content changed.')
    a, b = deepcopy(before['metadata']), deepcopy(after['metadata'])
    av, bv = int(a.pop('version')), int(b.pop('version'))
    at, bt = a.pop('modifiedTime'), b.pop('modifiedTime')
    _need(bv > av and bt >= at and a == b, 'Original identity, access or location changed.')


def _bindings(after, operations, reference=None):
    """Use the normal exported-source parser and the real response assigner."""
    elements, parsed = _elements(after['native']), after['parsed']
    all_markers = completion.markers(parsed)
    authority = reference['authority'] if reference is not None else None
    pairs = completion._align(parsed, reference['parsed'], panel_reference=authority) if reference is not None else {}
    for op in operations:
        number = elements[op['label_object_id']][0]
        slides = [s for s in parsed['slides'] if s['number'] == number]
        _need(len(slides) == 1, 'The repaired original slide could not be parsed.')
        slide = slides[0]
        def mapped(ident):
            native = elements[ident][2]
            x, y, w, h = _element_box(elements, ident)
            _need(x >= 0 and y >= 0 and x + w <= parsed['size'][0] and y + h <= parsed['size'][1],
                  'The existing label/response must be on the original teaching page.')
            units = [u for u in slide['units'] if not u.get('picture')
                     and response_binding.same_box(u.get('box'), _element_box(elements, ident), parsed['size'])
                     and u['text'].strip() == _reader_text(_text(native))]
            _need(len(units) == 1, 'The exact native response/label has no unique exported-source region.')
            return units[0]
        label = mapped(op['label_object_id'])
        match = completion.MARKER.fullmatch(op['marker'])
        found = [m for m in all_markers if (m['category'], m['code'], m['id']) == match.groups()]
        _need(len(found) == 1 and found[0]['slide'] == number and found[0]['key'] == label['key'],
              'The canonical code is absent, duplicated or attached to another original region.')
        fields = [mapped(i) for i in op['page_response_object_ids']]
        # Structural probe only: include blank original fields too, without
        # converting a placeholder or code into a student completion score.
        written = [(u, 'verified original field') for u in fields]
        markers = [m for m in all_markers if m['slide'] == number]
        has_panel = any(m.get('binding', {}).get('kind') == response_binding.PANEL_KIND for m in markers)
        template = pairs.get(number) if has_panel else slide
        _need(template is not None, 'The actual handed-out panel reference no longer matches.')
        assigned, _, basis = completion._assign(markers, written, template, parsed['size'], student_slide=slide,
                                                panel_reference=authority)
        _need(basis in ('box labels', 'one question on the slide', 'code on the box',
                       'authenticated original response panels'),
              'The actual parser cannot bind these response regions unambiguously.')
        if basis == 'box labels':
            first = [u for u in slide['units'] if re.match(r'\s*(?:your\s+)?first\s+answer\b', u['text'], re.I)]
            revised = [u for u in slide['units'] if re.match(r'\s*(?:your\s+)?revised\s+answer\b', u['text'], re.I)]
            # A response can retain its printed first/revised label too. Its
            # same-role labels must all agree on which side of the response it is.
            _need(first and revised and all(completion._distance(completion._center(u['box']), a['box'])
                      != completion._distance(completion._center(u['box']), b['box'])
                      for u in fields for a in first for b in revised),
                  'The original first/revised response regions have an ambiguous label distance.')
        keys = {u['key'] for u, _ in assigned.get((match.group(1), match.group(3)), [])}
        expected = {mapped(i)['key'] for i in op['response_object_ids']}
        _need(keys == expected and all(sum(u['key'] in {r['key'] for r, _ in items}
                                          for items in assigned.values()) == 1
                                      for u in fields if u['key'] in expected),
              'The actual parser attached the restored code to a different or shared response region.')


def prepare(token, file_id, proof, expected_revision, expected_native_sha256,
            expected_metadata_sha256, operations, *, guard, panel_reference=None, registered_teacher=None):
    _scope(proof, file_id)
    _need(callable(guard) and _guard(guard, proof), 'Original host proof changed or is unavailable.')
    before = capture(token, file_id)
    _need(before['native']['revisionId'] == expected_revision
          and before['native_sha256'] == expected_native_sha256
          and before['metadata_sha256'] == expected_metadata_sha256, 'The original changed since it was inspected.')
    reference = _reference(token, proof, panel_reference, file_id)
    teacher = _teacher(token, proof, registered_teacher, file_id)
    requests, edits = _plan(before, operations, reference, teacher)
    _preview_bindings(before, operations, edits, reference)
    _need(_guard(guard, proof), 'Original Form/Agenda ownership changed during preparation.')
    manifest = {'file_id': file_id, 'proof': deepcopy(proof), 'operations': deepcopy(operations),
                'revision': expected_revision, 'native_sha256': expected_native_sha256,
                'metadata_sha256': expected_metadata_sha256, 'panel_reference': deepcopy(panel_reference)}
    if registered_teacher is not None:
        manifest['registered_teacher'] = deepcopy(registered_teacher)
    return {'status': 'prepared', 'dry_run': True, 'plan_id': digest(manifest), 'manifest': manifest,
            'requests': requests, 'edits': edits, 'before': before, 'reference': reference, 'teacher': teacher,
            'apply_eligible': proof['question_set_status'] == 'complete',
            'full_census_complete': False, 'grading_authorized': False}


def apply(token, write_token, prepared, port, *, guard, dry_run=True):
    manifest = prepared['manifest']
    _need(prepared['plan_id'] == digest(manifest), 'The prepared manifest changed.')
    fresh = prepare(token, manifest['file_id'], manifest['proof'], manifest['revision'],
                    manifest['native_sha256'], manifest['metadata_sha256'], manifest['operations'], guard=guard,
                    panel_reference=manifest['panel_reference'], registered_teacher=manifest.get('registered_teacher'))
    if dry_run:
        return fresh
    _need(manifest['proof']['question_set_status'] == 'complete',
          'The complete retained-question census is unfinished. Partial repair must not arm assessment.')
    _need(write_token and port is not None, 'An authorized writer and private uncertainty fence are required.')
    gate = key('embeddedcoderepair', [manifest['file_id']])
    saved = port.read([gate]).documents[gate]
    _need(not saved or saved.value.get('status') == 'verified',
          'A prior code write is uncertain; inspect its exact original before any further write.')
    attempt = {'status': 'attempting', 'plan_id': fresh['plan_id'], 'manifest': manifest}
    port.commit({gate: (attempt, saved.version if saved else None)})
    claimed = port.read([gate]).documents[gate]
    _need(claimed and claimed.value == attempt, 'The private write fence did not read back.')
    try:
        # One final exact native revision/access bookend after claiming ownership.
        _need(_guard(guard, manifest['proof']), 'Original host proof changed before apply.')
        _need(google.drive_file(token, manifest['file_id'], FIELDS) == fresh['before']['metadata'],
              'The original changed before apply.')
        if fresh['reference'] is not None:
            _need(google.drive_file(token, manifest['panel_reference']['file_id'], FIELDS)
                  == fresh['reference']['metadata'], 'The handed-out reference changed before apply.')
        if fresh['teacher'] is not None:
            _need(google.drive_file(token, manifest['registered_teacher']['file_id'], FIELDS)
                  == fresh['teacher']['metadata'], 'The registered teacher corroboration changed before apply.')
        owned = port.read([gate]).documents[gate]
        _need(owned and owned.version == claimed.version and owned.value == attempt,
              'The private write fence changed before apply.')
        google.slides_batch_update(write_token, manifest['file_id'], fresh['requests'], manifest['revision'])
        after = capture(token, manifest['file_id'])
        _preserved(fresh['before'], after, fresh['edits'])
        reference = _reference(token, manifest['proof'], manifest['panel_reference'], manifest['file_id'])
        teacher = _teacher(token, manifest['proof'], manifest.get('registered_teacher'), manifest['file_id'])
        _bindings(after, manifest['operations'], reference)
        if teacher is not None:
            ready = completion.readiness(after['parsed'])
            _need(ready['status'] == 'READY'
                  and ready['counts'] == completion.readiness(fresh['before']['parsed'])['counts'],
                  'Saved exact-ID correction did not preserve the original complete code census.')
        _need(_guard(guard, manifest['proof']), 'Original proof changed during verification.')
        receipt = {'status': 'verified', 'plan_id': fresh['plan_id'], 'manifest': manifest,
                   'before_native_sha256': fresh['before']['native_sha256'],
                   'after_native_sha256': after['native_sha256'],
                   'after_metadata_sha256': after['metadata_sha256'],
                   'revision': after['native']['revisionId'], 'repaired_codes': len(manifest['operations']),
                   'full_census_complete': False, 'grading_authorized': False}
        try:
            port.commit({gate: (receipt, claimed.version)})
        except Exception:
            # The source has already read back. Reconcile this one private
            # receipt read without retrying either its commit or the source.
            reconciled = port.read([gate]).documents[gate]
            if not reconciled or reconciled.value != receipt:
                raise
        result = port.read([gate]).documents[gate]
        if not result or result.value != receipt:
            raise ValueError('The completion receipt did not read back.')
        return receipt
    except Exception as error:
        raise Uncertain('The exact code repair is unconfirmed; the private fence remains closed. '
                        'Inspect the original source, code locations, preserved answers and readback. '
                        'Do not retry this write. Cause: %s' % str(error)) from error
