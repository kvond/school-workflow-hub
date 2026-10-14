"""Current, teacher-only source review details; never score or persist a review.

Discovery issues come from the exact current Agenda/Form selection. A cached
response issue is usable only with fresh source and linked-template fingerprints.
Historical counts-only receipts are deliberately not an input to this module.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import re
import time
from urllib.parse import parse_qs, urlsplit

from . import agenda as agenda_module
from . import completion, google, morning, work

SCHEMA = 'hub-source-review-context/v1'
OVERVIEW_SCHEMA = 'hub-source-review-context-summary/v1'
MAX_REVIEW_SCOPES = 100
# A typed read timeout repeats the same read-only discovery once, and only while
# the request has time left; gunicorn ends a request at 60 seconds.
READ_ATTEMPTS = 2
RETRY_BUDGET_SECONDS = 20
TEACHER_MATERIAL = ('Uploaded through this Form from the teacher’s own school account. It is teacher material, '
                    'not a student submission: it is left out of student work, counts and missing-work checks, '
                    'and the original stays where it is.')
_TIMEOUT_READS = {'discovery': 'this lesson’s submissions', 'turn_in_folders': 'the turn-in folders',
                  'declared_upload_folders': 'the Form upload folders', 'form_responses': 'the Form responses',
                  'file_metadata': 'submitted file details', 'final_bookend': 'the final Agenda and roster check'}
_ID = re.compile(r'[A-Za-z0-9_-]{10,200}\Z')
_META = 'id,mimeType,trashed,modifiedTime,version,md5Checksum,size'


def source_link(url, file_id=None):
    """Accept a canonical Google resource URL with an exact resource ID."""
    if not isinstance(url, str) or any(ord(c) < 33 for c in url):
        return None
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.username or parts.password or parts.port
                or parts.hostname not in ('docs.google.com', 'drive.google.com')):
            return None
    except ValueError:
        return None
    ident = None
    if parts.hostname == 'docs.google.com':
        match = re.fullmatch(r'/(presentation|document|spreadsheets|forms)/d/([A-Za-z0-9_-]{10,200})(?:/(?:edit|viewform|copy))?/?', parts.path)
        if match:
            ident = match.group(2)
    else:
        match = re.fullmatch(r'/(?:file/d/|drive/folders/)([A-Za-z0-9_-]{10,200})(?:/view)?/?', parts.path)
        if match:
            ident = match.group(1)
        elif parts.path == '/open':
            query = parse_qs(parts.query)
            if set(query) == {'id'} and len(query['id']) == 1 and _ID.fullmatch(query['id'][0]):
                ident = query['id'][0]
    if not ident or (file_id is not None and ident != file_id):
        return None
    # Strip query/fragment transport and page guesses; slides are shown as text.
    if parts.hostname == 'docs.google.com':
        resource_type = {'presentation': 'slides', 'document': 'document', 'spreadsheets': 'spreadsheet',
                         'forms': 'form'}[parts.path.split('/')[1]]
    else:
        resource_type = 'folder' if parts.path.startswith('/drive/folders/') else 'file'
    return {'file_id': ident, 'resource_type': resource_type, 'url': ('https://drive.google.com/drive/folders/' + ident
            if parts.hostname == 'drive.google.com' and parts.path.startswith('/drive/folders/')
            else 'https://docs.google.com/%s/d/%s/edit' % (parts.path.split('/')[1], ident)
            if parts.hostname == 'docs.google.com' else 'https://drive.google.com/open?id=' + ident)}


def _sources(files):
    result, seen = [], set()
    for item in files:
        ident = item.get('id') or item.get('file_id')
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            continue
        url = item.get('url')
        link = source_link(url, ident) if url else source_link('https://drive.google.com/open?id=' + ident, ident)
        if link and ident not in seen:
            seen.add(ident)
            result.append({**link, 'label': str(item.get('name') or item.get('label') or 'Original source')})
    return result


def _student(row):
    verified = (row.get('matched') is True and all(isinstance(row.get(key), str) and row[key].strip()
                for key in ('first', 'last', 'section')))
    return {'first': row['first'] if verified else '', 'last': row['last'] if verified else '',
            'section': row['section'] if verified else '', 'verified': verified}


def _exempt(row):
    # Only explicit current evidence suppresses an action. No flag is inferred
    # from a missing source, score, Form event or roster newcomer.
    return row.get('exempt') is True or (isinstance(row.get('flags'), dict)
                                        and row['flags'].get('exempt') is True)


def _slides(reason):
    # Canonical assess() prefixes its actual response/BANK review locations.
    # This is called only for a current score result or fingerprinted cache,
    # never for filenames, selection errors, receipt counts or teacher text.
    match = re.match(r'^Slides? ([1-9][0-9]*(?:,\s*[1-9][0-9]*)*):', str(reason or ''))
    values = [int(n) for n in re.findall(r'\d+', match.group(1))] if match else []
    return sorted(set(values)) if values and max(values) <= completion.MAX_SLIDES else []


def notice(scope, row, reason, *, files=None, action=None, assessed=False, basis=None, affected_scope=None, issue_kind='source_issue'):
    """One shared notice shape for discovery and existing scoring routes."""
    files = files if files is not None else [f for snapshot in [row] + row.get('earlier', [])
                                            for f in snapshot.get('files', [])]
    sources = _sources(files)
    slide_sources = [s for s in sources if s['resource_type'] in ('slides', 'file')]
    numbers = _slides(reason) if assessed else []
    student = _student(row)
    if action is None:
        if not student['verified'] and files:
            action = 'Confirm the student’s name and class section for this linked original.'
        elif 'identity' in str(reason).lower() or 'identities' in str(reason).lower():
            action = 'Confirm the student’s name and class section for these linked originals.'
        elif any(word in str(reason).lower() for word in ('lesson', 'outside', 'route')):
            action = 'Confirm whether this titled original belongs to this lesson; preserve current grades.'
        else:
            action = ('Open the linked originals and confirm the current revision; preserve existing grades.'
                      if sources else 'Identify the exact original source for this lesson; preserve existing grades.')
    return {'kind': 'review', 'issue_kind': 'assessment' if assessed else issue_kind, 'affected_scope': affected_scope or ('student' if row else 'lesson'),
            'student': student, 'reason': str(reason),
            'sources': sources,
            'slides': [{'file_id': s['file_id'], 'numbers': numbers if assessed and len(sources) == 1 else [],
                        'verified': bool(assessed and len(sources) == 1 and numbers)} for s in slide_sources],
            'slide_note': '' if not slide_sources or assessed and len(sources) == 1 and numbers else 'Slide numbers could not be verified.',
            'next_action': action,
            'source_basis': basis or {'kind': 'current_selection'}}


def scope(agenda, config, class_key, week, form_id, *, catalog=None):
    matches = [item for item in (catalog or morning.catalog(agenda, config))['assignments']
               if (item['class'], item['week'], item['form']) == (class_key, week, form_id)]
    lessons = [lesson for lesson in agenda.lessons(class_key, week)
               if any(f.get('form_id') == form_id for f in lesson['forms'])]
    if len(matches) != 1 or len(lessons) != 1 or sum(f.get('form_id') == form_id for lesson in lessons for f in lesson['forms']) != 1:
        raise ValueError('review scope')
    item, lesson = matches[0], lessons[0]
    sources = []
    binding = work.form_teacher_sources(config, agenda, class_key, week, form_id)
    policy = work.assignment_policy(config, agenda, class_key, week, form_id)
    materials = lesson.get('materials', [])
    if binding:
        materials = [{'label': 'Registered teacher source', 'url': 'https://docs.google.com/presentation/d/' + fid + '/edit'}
                     for fid in binding['teacher_ids']] + [{'label': 'Registered source folder',
                     'url': 'https://drive.google.com/drive/folders/' + binding['source_folder_id']}]
    for material in materials:
        link = source_link(material.get('url'))
        if link:
            sources.append({**link, 'label': material.get('label') or 'Linked lesson source'})
    form_link = source_link('https://docs.google.com/forms/d/' + form_id + '/edit', form_id)
    return {'class_key': class_key, 'class_name': item['class_name'], 'week': week, 'school_year': work._school_year(agenda, week),
            'form_id': form_id, 'lesson': item['assignment'], 'topic': lesson.get('topic') or item['assignment'],
            'sources': sources + [{**form_link, 'label': 'Original turn-in Form'}], 'grade_policy': policy}


def _fingerprint(meta, ident):
    if (not isinstance(meta, dict) or meta.get('id') != ident or meta.get('trashed') is not False
            or meta.get('mimeType') not in (completion.SLIDES, google.PPTX)
            or not meta.get('modifiedTime') or not meta.get('version')):
        return None
    return ident, meta['modifiedTime'], str(meta['version'])


def cached_reviews(token, agenda, class_key, week, form_id, rows, *, config=None):
    """Read existing assessments, fresh-checking both operands without bytes."""
    results, limitations, metadata = {}, [], {}
    cache = dict(completion._results)
    binding = work.form_teacher_sources(config or {}, agenda, class_key, week, form_id)
    linked = set(binding['teacher_ids'] if binding else completion.lesson_decks(agenda, class_key, week, form_id))
    required = completion.student_coding_required(agenda, class_key, week, form_id)
    lesson = next(lesson for lesson in agenda.lessons(class_key, week)
                  if any(f.get('form_id') == form_id for f in lesson['forms']))
    resolution = None

    def meta(ident):
        if ident not in metadata:
            metadata[ident] = google.drive_file(token, ident, _META)
        return metadata[ident]

    for row in rows:
        if _exempt(row) or row.get('selection_problem') or not _student(row)['verified']:
            continue
        files = row.get('files', [])
        if len(files) != 1 or not files[0].get('id'):
            continue
        ident = files[0]['id']
        candidates = [(key, value) for key, value in cache.items()
                      if key[0] == ident and (value.get('result') or {}).get('note')]
        if not candidates:
            continue
        if resolution is None:
            resolution = completion.lesson_sources(token, lesson, binding=binding) if binding else completion.lesson_sources(token, lesson)
            if resolution.get('problem'):
                limitations.append('Earlier response checks were not reused: ' + resolution['problem'])
                return {}, limitations
            linked &= {item['id'] for item in resolution['decks']}
        try:
            key = _fingerprint(meta(ident), ident)
            cached = cache.get(key)
            template = cached.get('template') if cached else None
            if (not template or template[0] not in linked or cached.get('student_coding_required') != required
                    or _fingerprint(meta(template[0]), template[0]) != template):
                limitations.append('Previous response-review details are not current for source ' + ident + '.')
                continue
            results[ident] = deepcopy(cached['result'])
        except google.GoogleError as error:
            limitations.append('Previous response-review details could not be verified for source %s: %s' % (ident, error))
    # Any source or template drift discards every cached note from this read.
    try:
        stable = all(google.drive_file(token, ident, _META) == before for ident, before in metadata.items())
        if results and (completion.lesson_sources(token, lesson, binding=binding) if binding else completion.lesson_sources(token, lesson)) != resolution:
            stable = False
    except google.GoogleError:
        stable = False
    if not stable:
        results = {}
        limitations.append('Sources changed or could not be verified during this read; previous response-review details were not reused.')
    return results, limitations


def _review_state(result):
    """Separate teacher repair requests from verified waiting and failed reads."""
    status = result['status']
    result['teacher_actionable'] = bool(status == 'review' and result.get('notices'))
    originals = result['source_selection'].get('submitted_originals')
    result['intake_status'] = (status if status in ('waiting', 'excluded', 'unavailable')
                              else 'received' if isinstance(originals, int) and originals > 0 else 'unavailable')
    return result


def _excluded(current_scope, *, submitted_originals=None, checked_at=None):
    """A verified exact policy needs no source repair or provider-derived issue."""
    return _review_state({'schema': SCHEMA, 'checked_at': checked_at or datetime.now(timezone.utc).isoformat(),
        'scope': current_scope, 'notices': [], 'status': 'excluded',
        'limitations': [(current_scope.get('grade_policy') or {}).get('reason') or
                        'This exact assignment is excluded from current review; original work and history are retained.'],
        'source_selection': {'submitted_originals': submitted_originals, 'assessment_performed': False}})


def build(current_scope, found, cached=None, *, checked_at=None):
    """Format current evidence without scoring, writes or inferred identities."""
    forms = [f for lesson in found.get('lessons', []) for f in lesson.get('forms', [])
             if f.get('form_id') == current_scope['form_id']]
    if len(forms) != 1 or found.get('class_key') != current_scope['class_key'] or found.get('week') != current_scope['week']:
        raise ValueError('review work scope')
    form = forms[0]
    rows = completion.recheck_candidates(form)
    notices, limitations, matched_other_lessons = [], [], []
    policy = current_scope.get('grade_policy') or {}
    case_skip = (current_scope['class_key'] == 'forensics' and work.week_number(current_scope['week']) == 1
                 and bool(re.fullmatch(r'(?:work\s+a\s+case|(?:.*\b)?sheppard(?:\b.*)?)',
                                       str(current_scope['lesson']).strip(), re.I)))
    if policy.get('excluded') or case_skip:
        return _review_state({'schema': SCHEMA, 'checked_at': checked_at or datetime.now(timezone.utc).isoformat(),
                'scope': current_scope, 'notices': [], 'status': 'excluded',
                'limitations': [policy['reason'] + ' No assessment is requested; Lab Safety and original grade history are unchanged.' if policy.get('excluded') else 'The teacher asked to skip Week 01 cases for grading. No case review or grade is requested here. This display does not certify a deployed grading exclusion; Lab Safety and existing grade history are unchanged.'],
                'source_selection': {'submitted_originals': sum(len(r.get('files', [])) for r in form.get('students', []) + form.get('late', [])),
                                     'assessment_performed': False}})
    for row in rows + [r for r in form.get('students', []) + form.get('late', []) if not r.get('matched')]:
        if _exempt(row):
            continue
        reason = row.get('selection_problem')
        if not _student(row)['verified'] and row.get('files'):
            reason = reason or 'This original source could not be matched to a verified student and class section.'
        files = row.get('files', [])
        if reason:
            from . import source_history_dispositions
            notices.append(notice(current_scope, row, reason,
                issue_kind=source_history_dispositions.issue_kind(reason) if _student(row)['verified'] else 'identity_unverified'))
            continue
        for upload in files:
            ident = upload.get('id')
            reason = upload.get('problem')
            if upload.get('trashed'):
                reason = 'The selected original is in trash; it was not restored.'
            elif upload.get('other_week'):
                reason = 'The selected source names Week %s, outside this lesson; its assignment match needs review.' % upload['other_week']
            score = (cached or {}).get(ident)
            if reason:
                notices.append(notice(current_scope, row, reason, files=[upload],
                    action='Open this exact original and check its availability or lesson match; preserve existing grades.',
                    issue_kind='source_week_mismatch' if upload.get('other_week') and not upload.get('trashed') and not upload.get('problem') else 'source_availability'))
            elif score and score.get('note'):
                notices.append(notice(current_scope, row, score['note'], files=[upload], assessed=True,
                    action=score.get('action') or 'Review the marked response on the verified slide(s), preserving the student’s original answers and current grades.',
                    basis={'kind': 'cached_assessment_verified', 'file_id': ident, 'template_id': score.get('template_id')}))
    for item in form.get('elsewhere', []):
        if item.get('accepted_intake'):
            continue
        identities = form.get('students', []) + form.get('late', []) + form.get('missing', []) + form.get('excused', [])
        matched = [r for r in identities if item.get('who') == '%s, %s' % (r.get('last'), r.get('first'))
                   and item.get('section') == r.get('section')]
        # The same person may be represented in more than one current list,
        # but identical names alone cannot collapse distinct students.
        identities = [(r.get('student_id') or r.get('id'), str(r.get('email') or '').strip().lower()) for r in matched]
        one_person = len(matched) == 1 or (bool(matched) and len(set(identities)) == 1
                                          and any(identities[0]))
        row = {**matched[0], 'matched': True} if one_person else {}
        if _exempt(row):
            continue
        verified_route = bool(current_scope['form_id'] and item.get('source_form_id') == current_scope['form_id']
                              and item.get('assignment_form_id') and item['assignment_form_id'] != current_scope['form_id'])
        reason = ('This original was received for %s, rather than this lesson.' % item['to']
                  if verified_route and item.get('to') else 'This original’s lesson and turn-in route could not be confirmed.')
        link = source_link(item.get('url'))
        files = [{**link, 'label': item.get('file') or 'Original source'}] if link else []
        adjacent = item.get('matched_other_lesson') or {}
        adjacent_files = _sources(item.get('matched_source_files') or [])
        if verified_route and _student(row)['verified']:
            if (adjacent.get('schema') == 'hub-nearby-lesson-match/v1'
                    and adjacent.get('matching_kind') == 'canonical_nearby_full_title'
                    and current_scope.get('school_year') and adjacent.get('school_year') == current_scope['school_year']
                    and adjacent.get('class_key') == current_scope['class_key']
                    and adjacent.get('week') != current_scope['week']
                    and adjacent.get('form_id') == item.get('assignment_form_id')
                    and adjacent.get('source_file_ids') == [f['file_id'] for f in adjacent_files]
                    and adjacent_files and isinstance(adjacent.get('lesson'), str) and adjacent['lesson']):
                target_link = source_link('https://docs.google.com/forms/d/' + adjacent['form_id'] + '/edit', adjacent['form_id'])
                if target_link:
                    information = notice(current_scope, row,
                        'This original is matched to %s in %s; it remains linked here as source context.' % (adjacent['lesson'], adjacent['week']),
                        files=adjacent_files, affected_scope='student', issue_kind='source_route_resolved')
                    information.pop('next_action', None)
                    information.update(kind='matched_other_lesson', matching_kind=adjacent['matching_kind'],
                        target={k: adjacent[k] for k in ('class_key', 'school_year', 'week', 'form_id', 'lesson')})
                    information['target'].update(class_name=current_scope['class_name'], url=target_link['url'])
                    matched_other_lessons.append(information)
                    continue
        detail = notice(current_scope, row, reason, files=files,
            action='Review the original turn-in route shown here; preserve current grades.', affected_scope='student',
            issue_kind='source_route_resolved' if verified_route else 'source_route_unverified')
        destinations = [(lesson, target) for lesson in found.get('lessons', [])
                        for target in lesson.get('forms', [])
                        if target.get('form_id') == item.get('assignment_form_id')]
        if (verified_route and detail['student']['verified'] and files and len(destinations) == 1
                and not destinations[0][1].get('problem')):
            lesson, target = destinations[0]
            target_link = source_link('https://docs.google.com/forms/d/' + target['form_id'] + '/edit', target['form_id'])
            if target_link:
                label = target.get('label') or lesson.get('topic') or 'Linked current lesson'
                if source_link(label, target['form_id']):
                    label = lesson.get('topic') or 'Linked current lesson'
                information = {k: deepcopy(v) for k, v in detail.items() if k != 'next_action'}
                information.update(kind='matched_other_lesson',
                    reason='This original is matched to %s in %s; it remains linked here as source context.' % (label, current_scope['week']),
                    target={'class_key': current_scope['class_key'], 'class_name': current_scope['class_name'],
                            'week': current_scope['week'], 'form_id': target['form_id'], 'lesson': label, 'url': target_link['url']})
                matched_other_lessons.append(information)
                continue
        notices.append(detail)
    for reason in [form.get('problem'), found.get('assessment_problem'), found.get('folder_note'), *found.get('problems', [])]:
        if reason:
            notices.append(notice(current_scope, {}, reason, files=current_scope['sources'],
                action='Check the linked Form or lesson source for this class and week; no student or grade is inferred.'))
    for row in found.get('other_files', []):
        if not _exempt(row):
            notices.append(notice(current_scope, row,
                'This source was found in this week’s collection, but its lesson match is unconfirmed; it is not assigned to the selected lesson.',
                action='Confirm this original’s lesson from its actual content and turn-in route; preserve current grades.',
                issue_kind='source_issue' if _student(row)['verified'] else 'identity_unverified'))
    teacher_material = []
    for row in form.get('teacher_uploads', []) + found.get('teacher_uploads', []):
        files = [f for snapshot in [row] + row.get('earlier', []) for f in snapshot.get('files', [])]
        if not _sources(files):
            continue
        detail = notice(current_scope, {}, TEACHER_MATERIAL, files=files, action='', affected_scope='lesson',
                        issue_kind='teacher_material')
        detail.pop('next_action')
        detail['kind'] = 'teacher_material'
        teacher_material.append(detail)
    submitted = sum(len(row.get('files', [])) for row in form.get('students', []) + form.get('late', []))
    waiting = bool(not submitted and not form.get('elsewhere') and not form.get('problem')
                   and not found.get('other_files') and not found.get('problems') and not found.get('assessment_problem'))
    waiting = waiting and not found.get('folder_note')
    if waiting:
        limitations.append('Waiting for submissions. No student-copy repair or grade is requested before an original is received.')
    elif submitted:
        limitations.append('This view shows current source issues, not a new grading pass. Earlier response checks appear only when the original and linked teacher copy still match. Other responses have not been checked here.')
    return _review_state({'schema': SCHEMA, 'checked_at': checked_at or datetime.now(timezone.utc).isoformat(),
            'scope': current_scope, 'notices': notices, 'matched_other_lessons': matched_other_lessons, 'limitations': limitations,
            'status': 'review' if notices else 'waiting' if waiting else 'not_assessed',
            'source_selection': {'submitted_originals': submitted, 'assessment_performed': False},
            **({'teacher_material': teacher_material} if teacher_material else {})})


def _rate_limited(reason):
    return bool(re.search(r'\b429\b|rate[- ]limit|rateLimitExceeded|Google is limiting requests', str(reason), re.I))


def _timeout_failure(reads, attempts):
    """The typed timeout record a reader uses to offer a retry, never a conclusion."""
    return {'reason': 'read_timeout', 'reads': sorted(set(reads)), 'attempts': attempts, 'retry': True}


def _timeout_reason(failure):
    reads = [_TIMEOUT_READS.get(read, 'a current source') for read in failure['reads']] or ['a current source']
    return ('Check timed out — retry. Google didn’t answer in time while reading %s (%d attempt%s). '
            'Nothing was concluded about this lesson’s submissions.'
            % (' and '.join(reads), failure['attempts'], '' if failure['attempts'] == 1 else 's'))


def _unavailable(current_scope, reason, *, checked_at=None, read_failure=None):
    policy = current_scope.get('grade_policy') or {}
    if policy.get('excluded') and not policy.get('problem'):
        return _excluded(current_scope, checked_at=checked_at)
    if read_failure:
        reason = _timeout_reason(read_failure)
        action = ('Retry this check. A timed-out read is not missing student work; no student identity decision, '
                  'missing-work finding or grade change follows from it.')
    else:
        action = ('Read again after Google allows requests. No source repair or student identity decision is requested.'
                  if _rate_limited(reason) else
                  'Refresh when this source read is available. No student identity decision or grade change is requested from an incomplete read.')
    return _review_state({'schema': SCHEMA, 'checked_at': checked_at or datetime.now(timezone.utc).isoformat(),
            'scope': current_scope, 'status': 'unavailable',
            'notices': [notice(current_scope, {}, reason, files=current_scope['sources'], action=action, affected_scope='lesson')],
            'limitations': [str(reason), action, 'Current source details are unavailable; no claim about student identity or missing work is made.'],
            'source_selection': {'submitted_originals': None, 'assessment_performed': False},
            **({'read_failure': read_failure} if read_failure else {})})


def _roster_problem(snapshot, classes):
    by_class, problems = snapshot
    if problems:
        return 'The current roster could not be verified: ' + ' '.join(problems)
    if any(not by_class.get(cls) for cls in classes):
        return 'The current class roster is empty or unavailable; student identities were not reviewed.'
    return None


def _agenda_basis(agenda):
    return deepcopy((agenda.spreadsheet_id, agenda.title, agenda.problems, agenda.weeks, agenda.classes))


def _bookend(token, config, agenda_basis, catalog, roster_snapshot, reload_agenda, started=None):
    """(problem, typed timeout failure or None) from the final Agenda/roster reread."""
    started = time.monotonic() if started is None else started
    for attempt in range(1, READ_ATTEMPTS + 1):
        try:
            after_agenda = reload_agenda()
            after_catalog = morning.catalog(after_agenda, config)
            after_roster = work.roster.load(token, config)
            break
        except google.GoogleError as error:
            if not google.read_timed_out(error):
                return 'The final Agenda or roster read could not be verified: ' + str(error), None
            if attempt == READ_ATTEMPTS or time.monotonic() - started >= RETRY_BUDGET_SECONDS:
                return ('The final Agenda or roster read timed out: ' + str(error),
                        _timeout_failure(['final_bookend'], attempt))
    problem = _roster_problem(after_roster, {item['class'] for item in catalog['assignments']})
    if problem:
        return problem, None
    if (_agenda_basis(after_agenda) != agenda_basis or after_catalog != catalog
            or after_roster != roster_snapshot):
        return 'The Agenda or roster changed during this read. Refresh to read its current source details.', None
    return None, None


def _timed_out_reads(found):
    """Typed timeouts recorded by discovery, including per-file metadata reads."""
    reads = set(found.get('read_timeouts') or [])
    rows = [row for lesson in found.get('lessons', []) for form in lesson.get('forms', [])
            for row in form.get('students', []) + form.get('late', [])] + found.get('other_files', [])
    if any(upload.get('read_timeout') for row in rows for snapshot in [row] + row.get('earlier', [])
           for upload in snapshot.get('files', [])):
        reads.add('file_metadata')
    return reads


def _discover(token, config, agenda, class_key, week, roster_snapshot, started):
    """Read-only discovery, repeated once after a typed timeout while time allows.

    A timeout that remains is returned as a typed failure, not as missing work."""
    for attempt in range(1, READ_ATTEMPTS + 1):
        try:
            found = work.lesson_work(token, config, agenda, class_key, week, review_only=True, roster_snapshot=roster_snapshot)
        except google.GoogleError as error:
            found = {'review_unavailable': [str(error)],
                     **({'read_timeouts': ['discovery']} if google.read_timed_out(error) else {})}
        reads = _timed_out_reads(found)
        if not reads:
            return found
        if attempt == READ_ATTEMPTS or time.monotonic() - started >= RETRY_BUDGET_SECONDS:
            return {**found, 'read_failure': _timeout_failure(reads, attempt)}



def _former_context(token, config, current_scope, found, result, roster_snapshot):
    """Present only exact verified withdrawn originals as retained history."""
    from . import student_archive
    from school_workflow_hub.core.cloud_state import StoreUnavailable
    if result.get("status") == "unavailable":
        return result
    forms = [f for lesson in found.get("lessons", []) for f in lesson.get("forms", [])
             if f.get("form_id") == current_scope["form_id"]]
    rows = [r for f in forms for r in f.get("students", []) + f.get("late", [])] + found.get("other_files", [])
    eligible = {upload.get("id") for row in rows if not row.get("matched")
                for upload in row.get("files", []) if upload.get("id")}
    def eligible_notice(detail):
        return (detail.get("affected_scope") == "student" and not detail.get("student", {}).get("verified")
                and detail.get("issue_kind") == "identity_unverified"
                and detail.get("sources")
                and all(source.get("file_id") in eligible and source_link(source.get("url"), source.get("file_id"))
                        for source in detail["sources"]))
    ids = {source["file_id"] for detail in result["notices"] if eligible_notice(detail)
           for source in detail["sources"]}
    try:
        retained = student_archive.review_former_sources(
            token, config, current_scope["class_key"], ids, roster_snapshot)
    except (google.GoogleError, StoreUnavailable) as error:
        return _unavailable(current_scope, "The private former-student source read is unavailable: " + str(error))
    except student_archive.Held as error:
        return _unavailable(current_scope, str(error))
    if not retained:
        return result
    output = deepcopy(result)
    notices, history, seen = [], [], set()
    for detail in output["notices"]:
        sources = detail.get("sources", [])
        # Mixed unknown/current originals still need their existing exact review.
        if not eligible_notice(detail) or any(source.get("file_id") not in retained for source in sources):
            notices.append(detail)
            continue
        for source in sources:
            fid = source["file_id"]
            if fid in seen:
                continue
            seen.add(fid)
            record = retained[fid]
            slides = [s for s in detail.get("slides", []) if s.get("file_id") == fid]
            history.append({"kind": "former_student_work", "affected_scope": "student",
                "student": {k: record[k] for k in ("first", "last", "section")} |
                           {"verified": False, "former": True, "history_verified": True},
                "reason": "Former-student work retained. The original and verified filed copy remain available; historical filing does not assign this source to the selected lesson.",
                "sources": [source,
                    {"file_id": record["copy_id"], "label": "Open filed work copy",
                     "url": student_archive._url(record["copy_id"])}],
                "slides": slides,
                "history_basis": {k: record[k] for k in ("student_key", "copy_record_key")}})
    output["notices"], output["former_student_work"] = notices, history
    return output

def _exclude_resolved_targets(config, agenda, current_scope, result):
    """Keep exact excluded target history out of current teacher requests."""
    visible, history = [], []
    for detail in result.get('matched_other_lessons', []):
        target = detail.get('target') or {}
        try:
            link = source_link(target.get('url'), target.get('form_id'))
            if (target.get('class_key') != current_scope['class_key'] or not target.get('week')
                    or not link or link['resource_type'] != 'form'):
                raise ValueError('unverified target')
            policy = work.assignment_policy(config, agenda, target['class_key'], target['week'], target['form_id'])
        except (ValueError, TypeError, KeyError):
            policy = {}
        if policy.get('excluded') and not policy.get('problem'):
            history.append({**detail, 'teacher_actionable': False, 'target_grade_policy': policy})
        else:
            visible.append(detail)
    if history:
        result = deepcopy(result)
        result['matched_other_lessons'] = visible
        result['excluded_assignment_history'] = result.get('excluded_assignment_history', []) + history
    return result


def _read_found(token, config, agenda, current_scope, found, *, roster_snapshot=None):
    if (current_scope.get('grade_policy') or {}).get('problem'):
        return _unavailable(current_scope, current_scope['grade_policy']['problem'])
    if (current_scope.get('grade_policy') or {}).get('excluded'):
        return _excluded(current_scope)
    if found.get('read_failure') or _timed_out_reads(found):
        # A slow provider is a retry, never a missing original or an identity question.
        failure = found.get('read_failure') or _timeout_failure(_timed_out_reads(found), 1)
        return _unavailable(current_scope, '', read_failure=failure)
    if found.get('review_unavailable'):
        return _unavailable(current_scope, ' '.join(found['review_unavailable']))
    form_id = current_scope['form_id']
    forms = [f for lesson in found.get('lessons', []) for f in lesson.get('forms', []) if f.get('form_id') == form_id]
    if len(forms) != 1:
        raise ValueError('review form')
    candidates = forms[0].get('students', []) + forms[0].get('late', []) + found.get('other_files', [])
    problems = [forms[0].get('problem'), found.get('folder_note'), *found.get('problems', []),
                *(f.get('problem') for row in candidates for snapshot in [row] + row.get('earlier', [])
                  for f in snapshot.get('files', []))]
    availability = [p for p in (forms[0].get('problem'), found.get('folder_note'), *found.get('problems', [])) if p]
    rate_problem = next((p for p in problems if p and _rate_limited(p)), None)
    if rate_problem or availability:
        return _unavailable(current_scope, rate_problem or ' '.join(availability))
    cached, limitations = cached_reviews(token, agenda, current_scope['class_key'], current_scope['week'], form_id,
                                         completion.recheck_candidates(forms[0]), config=config)
    rate_problem = next((p for p in limitations if _rate_limited(p)), None)
    if rate_problem:
        return _unavailable(current_scope, rate_problem)
    result = build(current_scope, found, cached)
    result['limitations'].extend(limitations)
    from . import source_history_dispositions
    result = source_history_dispositions.apply(token, config, agenda, current_scope, found, result, roster_snapshot)
    result = _former_context(token, config, current_scope, found, result, roster_snapshot)
    return _review_state(_exclude_resolved_targets(config, agenda, current_scope, result))


def read(token, config, agenda, class_key, week, form_id, *, reload_agenda=None):
    started = time.monotonic()
    catalog = morning.catalog(agenda, config)
    current_scope = scope(agenda, config, class_key, week, form_id, catalog=catalog)
    roster_snapshot = work.roster.load(token, config)
    problem = _roster_problem(roster_snapshot, [class_key])
    if problem:
        return _unavailable(current_scope, problem)
    before_agenda, before_roster = _agenda_basis(agenda), deepcopy(roster_snapshot)
    found = _discover(token, config, agenda, class_key, week, roster_snapshot, started)
    result = _read_found(token, config, agenda, current_scope, found, roster_snapshot=roster_snapshot)
    problem, failure = _bookend(token, config, before_agenda, catalog, before_roster,
                                reload_agenda or (lambda: agenda_module.load(token, config['HUB_AGENDA_SPREADSHEET_ID'])),
                                started)
    return _unavailable(current_scope, problem, read_failure=failure) if problem else result


def overview(token, config, agenda, *, reload_agenda=None):
    """One bounded current overview, sharing discovery within this request only."""
    started = time.monotonic()
    catalog = morning.catalog(agenda, config)
    assignments = catalog['assignments']
    checked_at = datetime.now(timezone.utc).isoformat()
    if len(assignments) > MAX_REVIEW_SCOPES:
        return {'schema': OVERVIEW_SCHEMA, 'checked_at': checked_at, 'reviews': [], 'status': 'unavailable',
                'limitations': ['The current review scope is too large for one read. No student identity or missing-work decision is requested.']}
    scopes = [scope(agenda, config, item['class'], item['week'], item['form'], catalog=catalog) for item in assignments]
    roster_snapshot = work.roster.load(token, config)
    problem = _roster_problem(roster_snapshot, {item['class'] for item in assignments})
    if problem:
        return {'schema': OVERVIEW_SCHEMA, 'checked_at': checked_at, 'reviews': [_unavailable(s, problem, checked_at=checked_at) for s in scopes],
                'status': 'unavailable', 'limitations': [problem]}
    before_agenda, before_roster = _agenda_basis(agenda), deepcopy(roster_snapshot)
    groups = list(dict.fromkeys((s['class_key'], s['week']) for s in scopes))

    def discover(key):
        return _discover(token, config, agenda, *key, roster_snapshot, started)

    with ThreadPoolExecutor(max_workers=2) as pool:
        found_by_scope = dict(zip(groups, pool.map(discover, groups)))
    reviews = [_read_found(token, config, agenda, s, found_by_scope[s['class_key'], s['week']], roster_snapshot=roster_snapshot) for s in scopes]
    problem, failure = _bookend(token, config, before_agenda, catalog, before_roster,
                                reload_agenda or (lambda: agenda_module.load(token, config['HUB_AGENDA_SPREADSHEET_ID'])),
                                started)
    if problem:
        reviews = [_unavailable(s, problem, checked_at=checked_at, read_failure=failure) for s in scopes]
    return {'schema': OVERVIEW_SCHEMA, 'checked_at': checked_at, 'reviews': reviews,
            'status': 'unavailable' if problem else 'current', 'limitations': [problem] if problem else [],
            **({'read_failure': failure} if problem and failure else {})}


def for_score(agenda, class_key, week, form_id, file_id, found, score):
    """Attach context to an already authorized score result, without assessing."""
    if not (score.get('note') or score.get('problem')) or score.get('code') == 'excluded_week':
        return score
    forms = [(lesson, f) for lesson in found.get('lessons', []) for f in lesson.get('forms', []) if f.get('form_id') == form_id]
    if len(forms) != 1:
        return score
    lesson, form = forms[0]
    rows = [r for r in form.get('students', []) + form.get('late', [])
            if any(f.get('id') == file_id for snapshot in [r] + r.get('earlier', []) for f in snapshot.get('files', []))]
    row = rows[0] if len(rows) == 1 else {}
    if _exempt(row):
        return score
    files = [f for snapshot in [row] + row.get('earlier', []) for f in snapshot.get('files', []) if f.get('id') == file_id]
    label = form.get('label') or lesson.get('topic')
    label_link = source_link(label, form_id)
    if label_link and label_link['url'].startswith('https://docs.google.com/forms/d/'):
        label = lesson.get('topic') or 'Lesson title unavailable'
    current_scope = {'class_key': class_key, 'class_name': agenda.classes[class_key]['name'],
                     'week': week, 'form_id': form_id, 'lesson': label,
                     'topic': lesson.get('topic')}
    detail = notice(current_scope, row, score.get('problem') or score['note'], files=files,
                    assessed=not score.get('problem'), action=score.get('action'),
                    basis={'kind': 'current_score_result', 'file_id': file_id, 'file_updated': score.get('file_updated')})
    return {**score, 'review_context': {'schema': SCHEMA, 'scope': current_scope, 'notice': detail}}
