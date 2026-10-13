"""Feedback-only evidence for an already authenticated imported worksheet.

Trusted filing workers supply the separately reviewed page/response transcript,
its frozen digest and the actual rendered image bytes. This is not a browser
payload endpoint or an OCR/identity verifier. No provider, model, grade, storage,
PDF authoring or Drive calls occur here. Ratings require exact authored quotes;
printed prompts, research references and picture-only marks are not writing.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import re
from zoneinfo import ZoneInfo

from . import student_archive as archive
from .native_feedback import LEVELS, TEACHER_DIMENSIONS
from school_workflow_hub.core.runs import content_hash

SCHEMA = 'imported-worksheet-writing-evidence/v1'
RUBRIC = 'science-writing-four-dimensions/v1'
KEYS = ('vocabulary', 'conceptual', 'evidence', 'reasoning')
LABELS = ('Use science words precisely', 'Explain the scientific relationship',
          'Use specific evidence', 'Explain why your evidence supports the claim')


def need(ok, why):
    if not ok:
        raise archive.Held(why)


def text(value, maximum):
    need(isinstance(value, str) and len(value) <= maximum
         and not any(ord(c) < 32 and c not in '\n\t\r' for c in value), 'The reviewed worksheet text is unreadable or too long.')
    return value


def source_location(definition, page):
    """Reviewed human location; original object IDs remain private provenance."""
    label = definition.get('student_facing_location', 'Page %s - written response' % page)
    need(isinstance(label, str) and 1 <= len(label) <= 200
         and not any(ord(c) < 32 for c in label)
         and re.match(r'^Page %s(?:\b)' % page, label)
         and not re.search(r'\bg[0-9a-f]+_[0-9_]+\b', label), 'A reviewed readable page/question location is required.')
    return label.replace('—', '-').replace('–', '-')


def evidence(record, student, before, after, original_bytes, review, *,
             expected_review_sha256, rendered_page_bytes, teacher_reference_sha256,
             expected_page_count):
    """Pure exact-source projection from trusted, individually reviewed regions.

    The supplied expected review/teacher digests must come from the trusted
    worker's authenticated approval, never from an uploaded assessment payload.
    A review is necessary even when PDF extraction/OCR yields some text: those
    strings alone cannot separate printed headings from authored responses.
    """
    need(before == after, 'The imported original changed during evidence capture.')
    archive._verify_imported_original(before, record)
    need(record.get('status') == 'verified' and record.get('student_key') == student.get('student_key')
         and record.get('parent') == student.get('drive_file_id')
         and record.get('class_key') == student.get('class_key', record.get('class_key'))
         and record.get('class_key') == (record.get('source_context') or {}).get('class_key')
         and student.get('status') == 'verified', 'This worksheet does not belong to the verified existing student.')
    need(isinstance(original_bytes, bytes) and hashlib.sha256(original_bytes).hexdigest() == record['copied_content_proof']['sha256']
         and hashlib.md5(original_bytes).hexdigest() == before.get('md5Checksum')
         and len(original_bytes) == int(before['size']), 'The actual worksheet bytes do not match the imported original.')
    need((record['mime'] == archive.PDF and original_bytes.startswith(b'%PDF-'))
         or (record['mime'] == 'image/png' and original_bytes.startswith(b'\x89PNG\r\n\x1a\n')), 'Unsupported actual worksheet format.')
    need(isinstance(review, dict) and re.fullmatch('[a-f0-9]{64}', expected_review_sha256 or '')
         and content_hash(review) == expected_review_sha256, 'The separately approved response review changed.')
    revision = str(before['version']); provenance = record['import_provenance']
    need(review.get('schema') == SCHEMA and review.get('status') == 'TEACHER_VERIFIED_AUTHORED_RESPONSE_REGIONS'
         and review.get('record_key') == record['record_key'] and review.get('student_key') == student['student_key']
         and review.get('source_file_id') == before['id'] and review.get('source_revision') == revision
         and review.get('source_sha256') == record['copied_content_proof']['sha256'], 'Response review is for another source, student or revision.')
    teacher = review.get('teacher') or {}
    need(re.fullmatch('[a-f0-9]{64}', teacher_reference_sha256 or '')
         and content_hash(teacher) == teacher_reference_sha256
         and re.fullmatch('[a-f0-9]{64}', teacher.get('source_sha256') or '')
         and teacher.get('id') == provenance['worksheet_teacher_id']
         and teacher.get('role') == provenance['worksheet_role']
         and isinstance(teacher.get('revision'), str) and teacher['revision'], 'The reviewed original worksheet reference/role changed.')
    pages = review.get('pages'); fields = review.get('fields'); definitions = teacher.get('fields')
    need(type(expected_page_count) is int and 1 <= expected_page_count <= 100
         and review.get('page_count') == expected_page_count
         and isinstance(pages, list) and len(pages) == expected_page_count
         and [p.get('page') for p in pages] == list(range(1, len(pages) + 1))
         and isinstance(rendered_page_bytes, dict) and set(rendered_page_bytes) == set(range(1, len(pages) + 1)), 'All actual worksheet pages must be rendered and reviewed once.')
    for page in pages:
        raw = rendered_page_bytes[page['page']]
        need(isinstance(raw, bytes) and raw.startswith(b'\x89PNG\r\n\x1a\n')
             and hashlib.sha256(raw).hexdigest() == page.get('sha256')
             and page.get('source_sha256') == review['source_sha256']
             and page.get('personally_reviewed') is True
             and type(page.get('width')) is int and type(page.get('height')) is int
             and 0 < page['width'] <= 10000 and 0 < page['height'] <= 10000, 'A worksheet render/source/review link is not verified.')
    if record['mime'] == 'image/png':
        need(len(pages) == 1 and rendered_page_bytes[1] == original_bytes, 'The PNG review must use the unchanged original image.')
    need(isinstance(fields, list) and isinstance(definitions, list) and 1 <= len(fields) <= 100
         and len(fields) == len(definitions) and len({f.get('id') for f in fields}) == len(fields)
         and [f.get('id') for f in fields] == [d.get('id') for d in definitions], 'Every exact worksheet region needs one distinct reviewed field.')
    answers = []
    for field, definition in zip(fields, definitions):
        need(field.get('page') == definition.get('page') and field.get('box') == definition.get('box')
             and type(field.get('page')) is int and 1 <= field['page'] <= len(pages), 'The response was moved to a different question or page.')
        box = definition.get('box')
        need(isinstance(box, list) and len(box) == 4 and all(type(x) in (int, float) for x in box)
             and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1, 'The original response region is unsupported.')
        kind = definition.get('kind'); answer = text(field.get('answer'), 20000)
        need(kind in {'written', 'numeric', 'choice', 'picture', 'identity', 'supplied'}
             and field.get('classification') in {'authored', 'unavailable', 'excluded'}, 'The response attribution is unsupported.')
        if kind not in {'written', 'numeric'}:
            need(field['classification'] == 'excluded' and not answer, 'Printed material, identity or a graphic cannot become a writing quotation.')
            continue
        provided = definition.get('provided_text')
        need(isinstance(provided, list) and all(isinstance(x, str) for x in provided), 'Original printed text must be distinguished from writing.')
        need(field['classification'] in {'authored', 'unavailable'}
             and bool(answer.strip()) == (field['classification'] == 'authored')
             and not (answer.strip() and any(answer.strip() == x.strip() for x in provided if x.strip())), 'The field contains only a printed prompt or unavailable writing.')
        answers.append({'id': text(field['id'], 120), 'page': field['page'], 'box': deepcopy(box),
                        'answer': answer, 'available': field['classification'] == 'authored',
                        'source_location': source_location(definition, field['page'])})
    return {'schema': SCHEMA, 'record_key': record['record_key'], 'source_kind': archive.IMPORTED_ORIGINAL,
        'student': {k: student[k] for k in ('name', 'section', 'student_key')}, 'class_key': record['class_key'],
        'student_folder_id': student['drive_file_id'], 'source_file_id': before['id'], 'source_revision': revision,
        'source_modified_at': before['modifiedTime'], 'source_sha256': review['source_sha256'], 'review_sha256': expected_review_sha256,
        'teacher': deepcopy(teacher), 'teacher_descriptor_sha256': teacher_reference_sha256,
        'approved_response_review': deepcopy(review),
        'original_page_count': expected_page_count, 'rendered_pages': deepcopy(pages), 'answers': answers,
        'assignment': record['assignment'], 'source_context': deepcopy(record['source_context']),
        'source_date': record['date'], 'date_basis': record['date_basis'], 'import_provenance': deepcopy(provenance),
        'source_metadata': deepcopy(before), 'rubric_version': RUBRIC,
        'form_event': None, 'submitted_at': None, 'grade_or_completion_assessment': False}


def report(evidence, dimensions, *, prepared_date, saved_at, target_title, target_text, cer_practice=None):
    """Validate teacher-reviewed four-dimension observations; author no PDF."""
    need(evidence.get('schema') == SCHEMA and evidence.get('rubric_version') == RUBRIC, 'Verified imported response evidence required.')
    need(isinstance(dimensions, list) and len(dimensions) == 4, 'Each existing writing dimension needs one observation.')
    need(datetime.strptime(prepared_date, '%Y-%m-%d').date().isoformat() == prepared_date
         and prepared_date >= evidence['source_date'], 'Feedback preparation date cannot replace or precede the actual submission date.')
    prepared = datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
    need(prepared.tzinfo is not None and prepared.astimezone(ZoneInfo('America/New_York')).date().isoformat() == prepared_date,
         'The actual feedback preparation timestamp must match its separate local date.')
    title, challenge = text(target_title, 160), text(target_text, 700)
    need(title.strip() and challenge.strip() and challenge.count('?') == 1, 'Feedback needs one meaningful thinking question without its answer.')
    answers = {a['id']: a for a in evidence['answers']};rows = [];shown = []
    for row, dimension, key, label in zip(dimensions, TEACHER_DIMENSIONS, KEYS, LABELS):
        need(isinstance(row, dict) and set(row) == {'dimension', 'level', 'question_id', 'quote', 'explanation'}
             and row['dimension'] == dimension and row['level'] in LEVELS, 'The existing four-dimension rubric changed.')
        quote, why = text(row['quote'], 400), text(row['explanation'], 600)
        need(why.strip() and len(quote.split()) <= 25, 'A writing observation needs a concise explanation and exact short example.')
        answer = answers.get(row['question_id'])
        if row['level'] == 'Evidence unavailable':
            need(not quote and (not row['question_id'] or answer is not None), 'Unavailable evidence cannot have an invented quote or field.')
        else:
            need(answer is not None and answer['available'] and quote.strip() and quote in answer['answer'], 'A quotation is not in this exact authored response field.')
            supplied = next(f['provided_text'] for f in evidence['teacher']['fields'] if f['id'] == answer['id'])
            need(' '.join(quote.split()) not in {' '.join(t.split()) for t in supplied if t.strip()},
                 'A printed prompt cannot be used as evidence of authored writing.')
        observation = {**deepcopy(row), 'student_key': evidence['student']['student_key'], 'section': evidence['student']['section'],
            'source_kind': archive.IMPORTED_ORIGINAL, 'source_record_key': evidence['record_key'],
            'source_file_id': evidence['source_file_id'], 'source_revision': evidence['source_revision'],
            'source_modified_at': evidence['source_modified_at'], 'source_sha256': evidence['source_sha256'],
            'source_review_sha256': evidence['review_sha256'], 'date': evidence['source_date'], 'date_basis': evidence['date_basis'],
            'lesson': evidence['assignment'], 'submitted_at': None, 'page': answer['page'] if answer else None}
        rows.append(observation)
        shown.append({'key': key, 'label': label, 'level': row['level'], 'example': quote, 'explanation': why,
                      'lesson': evidence['assignment'], 'source_date': evidence['source_date'], 'question_id': row['question_id'],
                      'source_location': answer['source_location'] if answer else 'No attributable written response'})
    value = {'schema': 'science-writing/v1', 'report_type': 'science_writing', 'class_key': evidence['class_key'],
        'prepared_at': prepared_date, 'saved_at': saved_at,
        'student': {**evidence['student'], 'student_folder_id': evidence['student_folder_id']},
        'lessons': [{'title': evidence['assignment'], 'source_date': evidence['source_date'],
                     'source_file_id': evidence['source_file_id'], 'source_revision': evidence['source_revision'],
                     'source_url': archive._url(evidence['source_file_id']), 'date_basis': evidence['date_basis']}],
        'dimensions': shown, 'target': {'title': title, 'text': challenge},
        'note': 'This describes the writing in your listed worksheet, not a grade. Submission and preparation dates are separate.',
        'provenance': {'source_observation_schema': SCHEMA, 'rubric_version': RUBRIC, 'source_observations': rows,
                       'teacher_review_status': 'Source-reviewed writing observations prepared for teacher review',
                       'imported_source_evidence': deepcopy(evidence)}}
    if cer_practice is not None:
        # New teacher-directed writing reports use the same actual author
        # pipeline; original saved reports still reproduce byte-for-byte.
        from .science_writing_display import context
        value['presentation_context'] = context(value, cer_practice)
    value['report_source_sha256'] = content_hash(value);value['id'] = 'feedbacksource_' + value['report_source_sha256']
    return value


def validate_saved_report(value, *, expected_review_sha256, expected_teacher_reference_sha256):
    """Reproduce a trusted worker's report before existing archive filing.

    This validates the source-bound transcript/observation contract, not the
    human attribution itself. There is no public route accepting review JSON.
    """
    capture = (value.get('provenance') or {}).get('imported_source_evidence')
    need(isinstance(capture, dict) and capture.get('schema') == SCHEMA
         and re.fullmatch('[a-f0-9]{64}', capture.get('review_sha256') or '')
         and capture.get('grade_or_completion_assessment') is False
         and capture.get('form_event') is None and capture.get('submitted_at') is None,
         'A separately reviewed imported worksheet capture is required.')
    review = capture.get('approved_response_review')
    need(re.fullmatch('[a-f0-9]{64}', expected_review_sha256 or '')
         and capture['review_sha256'] == expected_review_sha256
         and content_hash(review) == expected_review_sha256,
         'The separately authenticated worksheet response review changed.')
    need(re.fullmatch('[a-f0-9]{64}', expected_teacher_reference_sha256 or '')
         and capture.get('teacher_descriptor_sha256') == expected_teacher_reference_sha256
         and content_hash(capture.get('teacher')) == expected_teacher_reference_sha256
         and review.get('teacher') == capture['teacher'],
         'The approved printed worksheet/reference descriptor changed.')
    need(review.get('schema') == SCHEMA and review.get('status') == 'TEACHER_VERIFIED_AUTHORED_RESPONSE_REGIONS'
         and all(review.get(k) == capture.get(k) for k in ('record_key', 'source_file_id', 'source_revision', 'source_sha256'))
         and review.get('student_key') == capture['student']['student_key']
         and review.get('pages') == capture.get('rendered_pages')
         and type(review.get('page_count')) is int
         and review['page_count'] == capture.get('original_page_count') == len(capture['rendered_pages']),
         'The report does not use that exact reviewed source and page evidence.')
    answers = [{'id': field['id'], 'page': field['page'], 'box': field['box'], 'answer': field['answer'],
                'available': field['classification'] == 'authored',
                'source_location': source_location(definition, field['page'])}
               for field, definition in zip(review['fields'], review['teacher']['fields'])
               if definition['kind'] in {'written', 'numeric'}]
    need(capture.get('answers') == answers, 'The report writing differs from the authenticated response review.')
    rows = (value.get('provenance') or {}).get('source_observations')
    need(isinstance(rows, list) and len(rows) == 4, 'The imported report needs the same four observations.')
    dimensions = [{k: r.get(k) for k in ('dimension', 'level', 'question_id', 'quote', 'explanation')} for r in rows]
    target = value.get('target') or {}
    display = (value.get('presentation_context') or {}).get('writing_display')
    practice = None
    if display is not None:
        from .science_writing_display import validate
        validate(value)
        practice = {k: v for k, v in display['cer'].items() if k != 'lesson_reference'}
    expected = report(capture, dimensions, prepared_date=value.get('prepared_at'), saved_at=value.get('saved_at'),
                      target_title=target.get('title'), target_text=target.get('text'), cer_practice=practice)
    actual = {k: v for k, v in value.items() if k not in {'pdf_path', 'pdf_bytes', 'pdf_sha256'}}
    need(actual == expected, 'The saved imported report no longer reproduces its exact source observations.')
    return capture
