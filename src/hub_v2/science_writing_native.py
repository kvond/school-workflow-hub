"""Deterministic student writing feedback from saved native observations only."""
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import re

from . import google, student_archive as archive
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

VERSION = 'native-science-writing/v1'
RUBRIC = 'science-writing-four-dimensions/v1'
PUBLICATION_DAY_BASIS = 'native-writing-first-drive-created-at/v1'
KEYS = ('vocabulary', 'conceptual', 'evidence', 'reasoning')
LABELS = ('Science vocabulary', 'Constructing an answer', 'Specific evidence', 'Explaining evidence')


def ensure_saved(token, write_token, config, native_feedback_id):
    """Trusted morning/backfill entry point for one existing saved CA identity.

    Load its immutable dimensions from CAS; never generate, rerate or save CA.
    Legacy feedback without four saved dimensions returns a separate hold.
    The host may rebuild the existing daily packet after its bounded batch.
    """
    from . import native_feedback as native
    try:
        saved = native.get_saved(config, native_feedback_id).value
    except (native.Held, StoreUnavailable, Conflict) as error:
        return {'status': 'held', 'native_feedback_id': native_feedback_id, 'problem': str(error)}
    return file_saved(token, write_token, config, saved)


def _need(condition, message):
    if not condition:
        raise archive.Held(message)


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
    _need(actual and actual.value == value, 'The writing payload save could not be read back; preserve its existing identity.')
    return actual


def _context(token, config, saved):
    from . import native_feedback as native
    scope, filed = saved.get('scope') or {}, saved.get('archive') or {}
    _need(saved.get('saved_at') and scope.get('class') in archive.CLASSES
          and filed.get('status') == 'verified' and filed.get('feedback_id') == saved.get('id')
          and filed.get('class_key') == scope['class'], 'Writing feedback needs the verified saved CA report and student archive.')
    day = native._receipt_date(saved.get('submission_receipt'), scope, saved.get('submitted_at'))
    source = saved.get('source', {}).get('file') or {}
    _need(source.get('id') == scope.get('file') and source.get('version') and source.get('modified_at'),
          'Writing feedback needs the exact original source revision.')
    port = configured_store(config)
    original = _read(port, saved.get('id', ''))
    _need(original and original.value == saved, 'Writing observations must come from the unchanged saved native feedback record.')
    owner = _read(port, filed.get('student_key', ''))
    student = owner.value if owner else {}
    person = archive._identity(config, scope['class'], saved.get('archive_identity') or {})
    _need(student.get('status') == 'verified' and student.get('identity_status') == 'verified'
          and student.get('student_key') == filed.get('student_key')
          and student.get('drive_file_id') == filed.get('student_folder_id')
          and archive._same_name(student, person)
          and (not student.get('school_email') or student['school_email'] == person.get('school_email'))
          and (not student.get('student_id') or student['student_id'] == person.get('student_id')),
          'Writing feedback must match the existing verified student folder and school identity.')
    active = archive.report_roster(token, config, scope['class'])
    _need(sum(row.get('student_key') == student['student_key'] for row in active) == 1,
          'This student is outside the current report roster; preserve the historical feedback without creating a new current report.')
    actual, course = archive._location(token, config, scope['class'])
    root = archive._scope_root(config, actual)
    ca_key = key('archivefeedback', [root, saved['id']])
    ca = _read(port, ca_key)
    _need(ca and ca_key in student.get('items', {}) and ca.value.get('status') == 'verified'
          and ca.value.get('student_key') == student['student_key']
          and ca.value.get('drive_file_id') == filed.get('drive_file_id')
          and ca.value.get('feedback_id') == saved['id'] and ca.value.get('class_key') == scope['class']
          and ca.value.get('source_file_id') == source['id']
          and ca.value.get('source_sha256') == saved['source']['sha256'],
          'The saved CA PDF is not attached to this exact verified student record.')
    archive._feedback_file(token, root, course, student, ca.value)
    before = google.drive_file(token, source['id'], archive.SOURCE_FIELDS)
    _need(before.get('trashed') is False and str(before.get('version')) == str(source['version'])
          and before.get('modifiedTime') == source['modified_at']
          and (not source.get('mime_type') or before.get('mimeType') == source['mime_type']),
          'The original changed after the saved writing observations; preserve the earlier report.')
    receipt = saved['submission_receipt']
    copies = [d.value for d in archive._batch_read(port, list(student.get('items', {}))).values()
              if d and d.value.get('kind') == 'work' and d.value.get('status') == 'verified'
              and d.value.get('source_kind') in {'submitted_work_copy', 'submitted_work_export'}
              and d.value.get('student_key') == student['student_key']
              and d.value.get('source_file_id') == source['id']
              and str(d.value.get('source_version')) == str(source['version'])
              and d.value.get('source_modified_at') == source['modified_at']
              and d.value.get('identity_status') == 'verified' and d.value.get('lesson_status') == 'verified'
              and d.value.get('date_basis') == 'form_receipt' and d.value.get('date') == day
              and d.value.get('submitted_at') == receipt['submitted_at']
              and d.value.get('form_id') == receipt['source_form_id']
              and d.value.get('assignment_form_id') in (None, receipt['assignment_form_id'])
              and d.value.get('source_response_id') in (None, receipt['source_response_id'])
              and (d.value.get('source_context') or {}).get('source_response_id') in (None, receipt['source_response_id'])]
    _need(copies, 'The exact submitted revision needs its verified private work snapshot before writing feedback can be filed.')
    copied = copies[0]
    meta = google.drive_file(token, copied['drive_file_id'], archive.FIELDS)
    if copied['source_kind'] == 'submitted_work_export':
        archive._verify_work_export(meta, copied)
    else:
        archive._verify_work_copy(meta, copied)
    _need(google.drive_file(token, source['id'], archive.SOURCE_FIELDS) == before,
          'The original changed during writing-source verification; preserve the prepared report.')
    return port, root, course, student, day


def prepare(saved, student, source_date, *, prepared_at):
    """Preserve the validated four observations; assess no additional history."""
    from . import native_feedback as native
    from .feedback_pdf import render_science_writing
    rows = saved.get('report', {}).get('teacher_dimensions')
    _need(isinstance(rows, list) and len(rows) == 4, 'This saved report has no four-dimension writing analysis; do not rerate it automatically.')
    source, reference = saved['source']['file'], saved['reference']['file']
    _need(reference.get('id') and saved['report'].get('question'), 'Writing feedback needs its saved lesson reference and approved question.')
    prepared_date = datetime.fromisoformat(prepared_at.replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat()
    _need(source_date <= prepared_date, 'The original work date cannot follow report preparation.')
    title = saved['assignment']['assignment_title']
    reference_url = archive._url(reference['id'])
    observations, dimensions = [], []
    for row, dimension, name, label in zip(rows, native.TEACHER_DIMENSIONS, KEYS, LABELS):
        _need(isinstance(row, dict) and set(row) == set(native.SCHEMA['properties']['teacher_dimensions']['items']['properties'])
              and row.get('dimension') == dimension and row.get('level') in native.LEVELS
              and isinstance(row.get('quote'), str) and len(row['quote'].split()) <= 25
              and row.get('explanation') and (row['quote'] or row['level'] == 'Evidence unavailable'),
              'The saved writing analysis must retain its four validated dimensions and exact quotations.')
        observations.append({**deepcopy(row), 'student_key': student['student_key'], 'section': student['section'],
            'source_file_id': source['id'], 'source_revision': str(source['version']),
            'source_modified_at': source['modified_at'], 'date': source_date, 'date_basis': 'form_receipt',
            'submitted_at': saved['submission_receipt']['submitted_at'], 'lesson': title,
            'reference_url': reference_url, 'rubric_version': RUBRIC, 'saved_feedback_id': saved['id']})
        dimensions.append({'key': name, 'label': label, 'level': row['level'], 'example': row['quote'],
            'explanation': row['explanation'], 'lesson': title, 'source_date': source_date,
            'question_id': row['question_id']})
    value = {'schema': 'science-writing/v1', 'report_type': 'science_writing', 'class_key': saved['scope']['class'],
        'prepared_at': prepared_date, 'saved_at': prepared_at,
        'student': {'student_key': student['student_key'], 'student_folder_id': student['drive_file_id'],
                    'name': student['name'], 'section': student['section']},
        'lessons': [{'title': title, 'source_date': source_date, 'source_file_id': source['id'],
            'source_revision': str(source['version']), 'source_url': archive._url(source['id']),
            'reference_url': reference_url, 'date_basis': 'form_receipt',
            'submitted_at': saved['submission_receipt']['submitted_at']}],
        'dimensions': dimensions, 'target': {'title': 'Your next writing move', 'text': saved['report']['question']},
        'note': 'This report describes this submitted source only. Earlier reports remain available in your student folder. It is not a grade.',
        'provenance': {'builder_version': VERSION, 'rubric_version': RUBRIC,
            'publication_day_basis': PUBLICATION_DAY_BASIS,
            'native_feedback_id': saved['id'], 'submission_receipt': deepcopy(saved['submission_receipt']),
            'native_source_capture': deepcopy(saved['source']), 'lesson_reference': deepcopy(saved['reference']),
            'source_observations': observations, 'history_coverage': 'current_source_only',
            'history_not_reconstructed': True, 'earlier_reports_retained': True}}
    value['report_source_sha256'] = content_hash(value)
    value['id'] = 'feedbacksource_' + value['report_source_sha256']
    pdf = render_science_writing(value)
    value['pdf_sha256'] = hashlib.sha256(pdf).hexdigest()
    return value, pdf


def _verified_receipt(token, port, root, course, student, payload):
    ident = key('archivefeedback', [root, payload['id']])
    owner = _read(port, student['student_key'])
    doc = _read(port, ident)
    _need(doc and owner and ident in owner.value.get('items', {})
          and doc.value.get('status') == 'verified' and doc.value.get('report_type') == 'science_writing'
          and doc.value.get('student_key') == student['student_key']
          and doc.value.get('feedback_id') == payload['id']
          and doc.value.get('pdf_sha256') == payload['pdf_sha256']
          and doc.value.get('report_source_sha256') == content_hash(archive._science_payload(payload)),
          'The separate writing PDF has not been confirmed in this student record; preserve its reservation.')
    archive._feedback_file(token, root, course, owner.value, doc.value)
    return archive._feedback_metadata(doc.value, owner.value, course)


def _existing_coverage(token, port, root, course, student, saved):
    """Reuse one proven assessment; legacy same-source coverage is not guessed."""
    source, event = saved['source']['file'], saved['submission_receipt']
    matches, unproven = [], False
    for doc in archive._batch_read(port, list(student.get('items', {}))).values():
        value = doc.value if doc else {}
        if (value.get('status') != 'verified' or value.get('kind') != 'feedback'
                or value.get('report_type') != 'science_writing' or value.get('superseded_by')
                or value.get('student_key') != student['student_key']
                or value.get('class_key') != saved['scope']['class']):
            continue
        related = [s for s in value.get('source_records') or []
                   if s.get('source_file_id') == source['id'] and str(s.get('source_version')) == str(source['version'])
                   and s.get('source_modified_at') == source['modified_at']
                   and s.get('submitted_at') in (None, event['submitted_at'])
                   and s.get('form_id') in (None, event['source_form_id'])]
        if not related:
            continue
        proof = value.get('assessed_sources') or {}
        assessed = [s for s in proof.get('sources') or []
                    if s.get('source_file_id') == source['id'] and str(s.get('source_version')) == str(source['version'])
                    and s.get('source_modified_at') == source['modified_at']
                    and s.get('submitted_at') == event['submitted_at']
                    and s.get('date') == archive._date(event['submitted_at']) and s.get('date_basis') == 'form_receipt'
                    and s.get('source_form_id') == event['source_form_id']
                    and s.get('source_response_id') == event['source_response_id']
                    and s.get('assignment_form_id') == event['assignment_form_id']]
        if (proof.get('schema') != 'science-writing-assessed-sources/v1'
                or not re.fullmatch(r'[a-f0-9]{64}', str(proof.get('payload_sha256') or ''))
                or not re.fullmatch(r'[a-f0-9]{64}', str(proof.get('observations_sha256') or ''))
                or proof.get('payload_sha256') != value.get('report_source_sha256') or len(assessed) != 1
                or not re.fullmatch(r'[a-f0-9]{64}', str(assessed[0].get('observations_sha256') or ''))):
            unproven = True
            continue
        archive._feedback_file(token, root, course, student, value)
        matches.append(value)
    _need(not unproven, 'An existing same-source writing report has unverified assessed coverage; preserve it and reconcile before creating another PDF.')
    _need(len(matches) <= 1, 'Multiple current writing reports assess this exact source event; preserve them for reconciliation.')
    return matches[0] if matches else None


def file_saved(token, write_token, config, saved):
    """Prepare once, resume the existing canonical filer, independently read back."""
    from . import native_feedback as native
    try:
        _need(token and write_token, 'Separate writing feedback needs the authorized private report writer.')
        _need(isinstance(saved.get('report', {}).get('teacher_dimensions'), list)
              and len(saved['report']['teacher_dimensions']) == 4,
              'This saved report has no four-dimension writing analysis; do not rerate it automatically.')
        port, root, course, student, day = _context(token, config, saved)
        ident = key('nativewriting', [saved['id'], VERSION])
        basis = content_hash({k: saved.get(k) for k in ('id', 'scope', 'source', 'reference', 'report',
            'assignment', 'saved_at', 'submitted_at', 'submission_receipt', 'archive_identity')})
        stored = _read(port, ident)
        if stored:
            _need(stored.value.get('basis_sha256') == basis, 'The saved writing basis changed; preserve its existing payload and report.')
            if stored.value.get('status') == 'reused':
                existing = _existing_coverage(token, port, root, course, student, saved)
                _need(existing and existing['record_key'] == stored.value['reused_artifact_key'],
                      'The reused writing report changed; preserve its existing receipt.')
                return {**archive._feedback_metadata(existing, student, course), 'readback_verified': True,
                        'native_feedback_id': saved['id'], 'reused_existing': True,
                        'history_coverage': 'existing_report'}
            payload = stored.value['payload']
            from .feedback_pdf import render_science_writing
            pdf = render_science_writing(payload)
            _need(hashlib.sha256(pdf).hexdigest() == payload['pdf_sha256'], 'The prepared writing PDF changed; preserve the saved artifact.')
        else:
            existing = _existing_coverage(token, port, root, course, student, saved)
            if existing:
                _put(port, ident, {'schema': VERSION, 'native_feedback_id': saved['id'],
                    'basis_sha256': basis, 'status': 'reused', 'reused_artifact_key': existing['record_key']})
                return {**archive._feedback_metadata(existing, student, course), 'readback_verified': True,
                        'native_feedback_id': saved['id'], 'reused_existing': True,
                        'history_coverage': 'existing_report'}
            payload, pdf = prepare(saved, student, day, prepared_at=native.now())
            stored = _put(port, ident, {'schema': VERSION, 'native_feedback_id': saved['id'],
                'basis_sha256': basis, 'payload': payload, 'status': 'prepared'})
        if stored.value.get('status') != 'verified':
            filed = archive.file_science_writing(token, write_token, config, payload, pdf)
            _need(filed.get('status') == 'verified', filed.get('problem') or 'The separate writing PDF filing is held.')
        receipt = _verified_receipt(token, port, root, course, student, payload)
        if stored.value.get('status') != 'verified':
            _put(port, ident, {**stored.value, 'status': 'verified', 'receipt': receipt}, stored)
        return {**receipt, 'readback_verified': True, 'native_feedback_id': saved['id'],
                'history_coverage': 'current_source_only'}
    except (archive.Held, native.Held, google.GoogleError, Conflict, StoreUnavailable, ValueError, KeyError, TypeError) as error:
        return {'status': 'held', 'native_feedback_id': saved.get('id'), 'problem': str(error),
                'history_coverage': 'current_source_only'}
