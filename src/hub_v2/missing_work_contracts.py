"""Print-only, current-source missing-work plans; never grade or delivery writes.

The host supplies read-only IC, archive, due/binding and source proofs. A blank
score, an old contract or an unassessed explanation is not absence evidence.
"""
from datetime import date, datetime, timezone
import re
import unicodedata
from zoneinfo import ZoneInfo

from school_workflow_hub.core.runs import content_hash

SCHEMA = 'hub-missing-work-contract/v1'
MAX_AGE_SECONDS = 3600  # Supportive printed checks only; not grade authority.
SOURCE_CHECKS = ('forms', 'folders', 'shared_history', 'earlier_history', 'unsorted', 'lesson_bindings')
MESSAGE = ("I want to give you useful feedback on your thinking. I could not locate submitted work "
           "for this lesson in the sources checked today. If you submitted it another way, please "
           "show me where to find it. Your ideas matter.")
NOTE = ('This check does not change a grade or deadline, clear an extension, or claim that work is late. '
        'Any existing extension and grading grace period remain in place.')
LINES = ('My next step to show my thinking: __________________________________',
         'When I plan to do it: ______________________________________________',
         'Help I need or where I already submitted it: _________________________',
         '__________________________________________________________________',
         'Student signature: ________________________  Date: _________________')


class CoverageError(ValueError):
    pass


def _name(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).replace('’', "'").split()).casefold()


def _person(row):
    values = tuple(_name(row.get(k)) for k in ('first', 'last', 'section'))
    if not all(values):
        raise CoverageError('A current student name and class section need verification.')
    return values


def _instant(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.utcoffset() is None:
            raise ValueError()
        return stamp.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise CoverageError('The current check needs a verified timestamp.') from None


def _date(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise CoverageError('The original assignment date needs verification.') from None


def _proof(proof, checked, label, *, complete=False):
    if not isinstance(proof, dict) or not proof.get('reference'):
        raise CoverageError(label + ' evidence is unavailable.')
    if complete and proof.get('complete') is not True:
        raise CoverageError(label + ' coverage is incomplete.')
    observed = _instant(proof.get('checked_at'))
    age = (checked - observed).total_seconds()
    if not 0 <= age <= MAX_AGE_SECONDS or observed.astimezone(ZoneInfo('America/New_York')).date() != checked.astimezone(ZoneInfo('America/New_York')).date():
        raise CoverageError(label + ' evidence needs a fresh check today.')


def _roster(proof, checked, cls):
    _proof(proof, checked, 'Infinite Campus roster', complete=True)
    if proof.get('source') != 'infinite_campus' or proof.get('class_key') != cls:
        raise CoverageError('The current class needs its canonical Infinite Campus roster.')
    rows = proof.get('students')
    if not isinstance(rows, list) or not rows or any(r.get('active') not in (True, False) for r in rows):
        raise CoverageError('The active Infinite Campus roster is incomplete.')
    active = [r for r in rows if r['active'] is True]
    if type(proof.get('expected_active_count')) is not int or len(active) != proof['expected_active_count']:
        raise CoverageError('The complete active Infinite Campus count needs verification.')
    people, native = {}, set()
    for row in active:
        person = _person(row)
        ident = str(row.get('ic_student_id') or '')
        if person in people or (ident and ident in native):
            raise CoverageError('Two current Infinite Campus rows claim the same student identity.')
        if ident and not re.fullmatch(r'\d{2,20}', ident):
            raise CoverageError('An Infinite Campus student ID needs verification.')
        people[person] = row
        if ident:
            native.add(ident)
    return people


def _identities(people, archive_proof, checked, cls):
    archive = {}
    if archive_proof is not None:
        _proof(archive_proof, checked, 'Private student archive')
        if archive_proof.get('class_key') != cls:
            raise CoverageError('The private student identity proof belongs to another class.')
        keys = set()
        for row in archive_proof.get('students', []):
            if row.get('verified') is not True:
                continue
            if (row.get('identity_status') == 'identity_unverified'
                    and (row.get('current_ic_name_match_verified') is not True
                         or row.get('private_folder_verified') is not True)):
                continue
            person, ident = _person(row), row.get('student_key')
            if not isinstance(ident, str) or not re.fullmatch(r'archivestudent_[0-9a-f]{64}', ident):
                raise CoverageError('An existing private student record key needs verification.')
            if person in archive or ident in keys:
                raise CoverageError('Two private student records claim the same current identity.')
            archive[person] = row
            keys.add(ident)
    result = {}
    for person, row in people.items():
        prior = archive.get(person)
        native = str(row.get('ic_student_id') or '')
        if prior and native and prior.get('ic_student_id') and str(prior['ic_student_id']) != native:
            raise CoverageError('The IC and private student identities disagree.')
        if not native and not prior:
            raise CoverageError('A current IC name needs one verified existing private student record before printing.')
        result[person] = {k: row[k] for k in ('first', 'last', 'section')}
        result[person].update({'name': row['last'] + ', ' + row['first'], 'ic_student_id': native or None,
                              'student_key': prior['student_key'] if prior else None,
                              'identity_basis': 'verified_ic_student_id' if native else 'current_ic_exact_name_section_and_verified_archive'})
        if row.get('ic_display_name'):
            result[person]['ic_display_name'] = row['ic_display_name']
        if prior:
            result[person].update({'student_folder_id': prior.get('student_folder_id'),
                                  'archive_identity_status': prior.get('identity_status')})
    return result


def _source(found, proof, checked):
    _proof(proof, checked, 'Submitted-work scan', complete=True)
    if proof.get('found_sha256') != content_hash(found):
        raise CoverageError('The submitted-work proof does not match this exact source scan.')
    if any((proof.get('checks') or {}).get(check) is not True for check in SOURCE_CHECKS):
        raise CoverageError('Form, folder, earlier/shared intake and unsorted coverage need a complete lesson-bound check.')
    if (found.get('problems') or found.get('folder_note') or found.get('assessment_problem')
            or (found.get('shared_form') or {}).get('problem')):
        raise CoverageError('A source could not be completely read; this is not evidence of missing work.')
    if found.get('other_files'):
        raise CoverageError('Some submissions still need a confirmed lesson match; no missing-work contracts were inferred.')
    if found.get('folder_reviews') and proof.get('folder_reviews_resolved') is not True:
        raise CoverageError('A folder shared by lessons needs its assignment bindings confirmed.')


def _assignment(lesson, due, checked):
    _proof(due, checked, 'Assignment and due date')
    if due.get('verified') is not True or due.get('binding_verified') is not True or due.get('source') not in ('teacher', 'infinite_campus'):
        raise CoverageError('The exact IC assignment, intake Form and due date need verification.')
    if not due.get('assignment_key') or not due.get('title') or due.get('row') != lesson.get('row'):
        raise CoverageError('The original assignment needs a verified Agenda row and title.')
    forms = lesson.get('forms') or []
    ids = [f.get('form_id') for f in forms]
    if not ids or any(not i for i in ids) or sorted(ids) != sorted(due.get('form_ids') or []):
        raise CoverageError('The assignment proof does not match every intake Form for this lesson.')
    if lesson.get('problem') or any(f.get('problem') for f in forms):
        raise CoverageError('This lesson’s Form responses could not be completely read.')
    original_due = _date(due.get('due_date'))
    opened = _date(due['opened_date']) if due.get('opened_date') else None
    if opened and opened > original_due:
        raise CoverageError('The original assignment opening and due dates disagree.')
    return original_due, opened


def _located(lesson, people):
    observed, submitted, excused, missing, elsewhere = set(), {}, set(), set(), set()
    for form in lesson['forms']:
        for routed in form.get('elsewhere', []):
            who = str(routed.get('who') or '').split(',', 1)
            if len(who) != 2:
                raise CoverageError('Work located elsewhere has an unresolved student identity; do not infer absence.')
            person = _person({'last': who[0], 'first': who[1], 'section': routed.get('section')})
            if person not in people:
                raise CoverageError('Work located elsewhere needs its current IC student matched before absence is inferred.')
            elsewhere.add(person)
            observed.add(person)
        for row in form.get('students', []) + form.get('late', []):
            if row.get('matched') is not True:
                raise CoverageError('A submitted file has an unresolved student identity.')
            person = _person(row)
            if person not in people:  # IC is canonical; old Hub-only rows are retained history.
                continue
            observed.add(person)
            versions = [row] + list(row.get('earlier') or [])
            files = [f for version in versions for f in version.get('files', [])]
            if any(f.get('problem') or f.get('trashed') is not False or f.get('other_week') for f in files):
                raise CoverageError('A submitted file needs access or a lesson match checked before absence is inferred.')
            usable = [f['id'] for f in files if f.get('id')]
            if not usable:
                raise CoverageError('A Form response has no verified submitted file; check that response before printing a missing-work contract.')
            submitted.setdefault(person, set()).update(usable)
        for category, target in (('missing', missing), ('excused', excused)):
            for row in form.get(category, []):
                person = _person(row)
                if person not in people:
                    continue
                if row.get('unsorted'):
                    raise CoverageError('A student has work awaiting a lesson match; do not label it missing.')
                observed.add(person)
                target.add(person)
    if observed != set(people):
        raise CoverageError('The submitted-work scan does not account for every current active IC student.')
    return submitted, excused, missing, elsewhere


def derive_plan(found, *, roster_proof, source_proof, due_proofs, checked_at, archive_proof=None):
    """Return current contracts, located-work closures, skips and contextual holds.

    Caller proofs are trusted host evidence, never browser/user-supplied claims.
    A new dated report ID does not change the stable assignment/student closure
    key. Re-run this function with fresh evidence before each day's reprint.
    """
    checked = _instant(checked_at)
    day = checked.astimezone(ZoneInfo('America/New_York')).date()
    result = {'schema': SCHEMA, 'checked_at': checked_at, 'date': day.isoformat(), 'contracts': [],
              'resolved': [], 'skipped': [], 'coverage_holds': [], 'grade_change': False,
              'delivery_changed': False, 'external_writes': 0, 'provider_calls': 0}
    cls, week = found.get('class_key'), found.get('week')
    seen = set()
    for lesson in found.get('lessons') or [{'row': None, 'topic': 'Selected lesson'}]:
        due = (due_proofs or {}).get(lesson.get('row')) or (due_proofs or {}).get(str(lesson.get('row')))
        context = {'class_key': cls, 'week': week, 'row': lesson.get('row'),
                   'assignment_key': (due or {}).get('assignment_key'),
                   'assignment_title': (due or {}).get('title') or lesson.get('topic')}
        try:
            people = _roster(roster_proof, checked, cls)
            identities = _identities(people, archive_proof, checked, cls)
            original_due, opened = _assignment(lesson, due, checked)
            assignment_key = due['assignment_key']
            if assignment_key in seen:
                continue
            seen.add(assignment_key)
            if original_due >= day:
                result['skipped'].append({**context, 'reason': 'not_due', 'due_date': original_due.isoformat()})
                continue
            _source(found, source_proof, checked)
            submitted, excused, missing, elsewhere = _located(lesson, people)
            planned, closures, skipped = [], [], []
            for person, row in people.items():
                state = (row.get('assignment_states') or {}).get(assignment_key) or {}
                if type(state.get('assigned')) is not bool or type(state.get('exempt')) is not bool:
                    raise CoverageError('Each active student needs the current IC assignment and Exempt flag read back.')
                identity = identities[person]
                learner = identity['ic_student_id'] or identity['student_key']
                contract_key = 'missingworkcontract_' + content_hash([cls, assignment_key, learner])
                base = {**context, 'contract_key': contract_key, 'student': identity}
                if not state['assigned'] or state['exempt'] or person in excused:
                    skipped.append({**base, 'reason': 'exempt' if state['exempt'] else 'not_assigned' if not state['assigned'] else 'new_join'})
                    continue
                if person in submitted:
                    closures.append({**base, 'status': 'resolved', 'reason': 'verified_submission_located',
                                     'source_file_ids': sorted(submitted[person]), 'checked_at': checked_at})
                    continue
                if person in elsewhere:
                    skipped.append({**base, 'reason': 'held_elsewhere'})
                    continue
                if person not in missing:
                    raise CoverageError('A current student has neither a verified submission nor a verified missing-source row.')
                if state.get('not_rostered_on_due_date') is True:
                    skipped.append({**base, 'reason': 'new_join'})
                    continue
                if row.get('start_date'):
                    if _date(row['start_date']) > (opened or original_due):
                        skipped.append({**base, 'reason': 'new_join'})
                        continue
                    if not opened and state.get('assigned_and_not_rostered_on_due_date_false') is not True:
                        raise CoverageError('The assignment opening or explicit IC membership-on-due flag needs verification.')
                elif state.get('assigned_and_not_rostered_on_due_date_false') is not True:
                    raise CoverageError('A missing-source student’s IC start date or explicit membership-on-due flag needs checking for a recent join.')
                extension = state.get('extension_due_at')
                if extension and _instant(extension) >= checked:
                    skipped.append({**base, 'reason': 'active_extension'})
                    continue
                if state.get('extension_status') == 'active' and not extension:
                    skipped.append({**base, 'reason': 'active_extension'})
                    continue
                provenance = {'roster_reference': roster_proof['reference'], 'archive_reference': (archive_proof or {}).get('reference'),
                              'source_reference': source_proof['reference'], 'found_sha256': source_proof['found_sha256'],
                              'assignment_reference': due['reference'], 'assignment_revision': due.get('revision'),
                              'form_ids': list(due['form_ids']), 'checked_at': checked_at,
                              'membership_basis': 'ic_start_date' if row.get('start_date') else 'current_ic_membership_on_due_flags'}
                report = {**base, 'schema': SCHEMA, 'report_type': 'missing_work_contract', 'status': 'open',
                          'prepared_at': day.isoformat(), 'checked_at': checked_at,
                          'title': 'Work not located as of ' + day.isoformat(),
                          'original_due_date': original_due.isoformat(), 'due_date': original_due.isoformat(),
                          'extension_status': state.get('extension_status') or 'unknown', 'message': MESSAGE,
                          'contract_heading': 'My plan to show my thinking', 'contract_lines': list(LINES), 'note': NOTE,
                          'understanding': None, 'explanation': None, 'grade_change': False, 'delivery_changed': False,
                          'source_proof': provenance}
                report['id'] = 'feedbacksource_' + content_hash(report)
                planned.append(report)
            result['contracts'].extend(planned)
            result['resolved'].extend(closures)
            result['skipped'].extend(skipped)
        except CoverageError as error:
            result['coverage_holds'].append({**context, 'reason': str(error), 'status': 'coverage_held'})
    return result
