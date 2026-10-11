"""Current, teacher-only source review details; never score or persist a review.

Discovery issues come from the exact current Agenda/Form selection. A cached
response issue is usable only with fresh source and linked-template fingerprints.
Historical counts-only receipts are deliberately not an input to this module.
"""
from copy import deepcopy
from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, urlsplit

from . import completion, google, morning, work

SCHEMA = 'hub-source-review-context/v1'
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
    return {'file_id': ident, 'url': ('https://drive.google.com/drive/folders/' + ident
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


def notice(scope, row, reason, *, files=None, action=None, assessed=False, basis=None):
    """One shared notice shape for discovery and existing scoring routes."""
    files = files if files is not None else [f for snapshot in [row] + row.get('earlier', [])
                                            for f in snapshot.get('files', [])]
    sources = _sources(files)
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
    return {'kind': 'review', 'affected_scope': 'student' if row else 'lesson',
            'student': student, 'reason': str(reason),
            'sources': sources,
            'slides': [{'file_id': s['file_id'], 'numbers': numbers if assessed and len(sources) == 1 else [],
                        'verified': bool(assessed and len(sources) == 1 and numbers)} for s in sources],
            'slide_note': '' if assessed and len(sources) == 1 and numbers else 'Slide numbers could not be verified.',
            'next_action': action,
            'source_basis': basis or {'kind': 'current_selection'}}


def scope(agenda, config, class_key, week, form_id):
    matches = [item for item in morning.catalog(agenda, config)['assignments']
               if (item['class'], item['week'], item['form']) == (class_key, week, form_id)]
    lessons = [lesson for lesson in agenda.lessons(class_key, week)
               if any(f.get('form_id') == form_id for f in lesson['forms'])]
    if len(matches) != 1 or len(lessons) != 1 or sum(f.get('form_id') == form_id for lesson in lessons for f in lesson['forms']) != 1:
        raise ValueError('review scope')
    item, lesson = matches[0], lessons[0]
    sources = []
    for material in lesson.get('materials', []):
        link = source_link(material.get('url'))
        if link:
            sources.append({**link, 'label': material.get('label') or 'Linked lesson source'})
    form_link = source_link('https://docs.google.com/forms/d/' + form_id + '/edit', form_id)
    return {'class_key': class_key, 'class_name': item['class_name'], 'week': week,
            'form_id': form_id, 'lesson': item['assignment'], 'topic': lesson.get('topic') or item['assignment'],
            'sources': sources + [{**form_link, 'label': 'Original turn-in Form'}]}


def _fingerprint(meta, ident):
    if (not isinstance(meta, dict) or meta.get('id') != ident or meta.get('trashed') is not False
            or meta.get('mimeType') not in (completion.SLIDES, google.PPTX)
            or not meta.get('modifiedTime') or not meta.get('version')):
        return None
    return ident, meta['modifiedTime'], str(meta['version'])


def cached_reviews(token, agenda, class_key, week, form_id, rows):
    """Read existing assessments, fresh-checking both operands without bytes."""
    results, limitations, metadata = {}, [], {}
    cache = dict(completion._results)
    linked = set(completion.lesson_decks(agenda, class_key, week, form_id))
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
            resolution = completion.lesson_sources(token, lesson)
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
        if results and completion.lesson_sources(token, lesson) != resolution:
            stable = False
    except google.GoogleError:
        stable = False
    if not stable:
        results = {}
        limitations.append('Sources changed or could not be verified during this read; previous response-review details were not reused.')
    return results, limitations


def build(current_scope, found, cached=None, *, checked_at=None):
    """Format current evidence without scoring, writes or inferred identities."""
    forms = [f for lesson in found.get('lessons', []) for f in lesson.get('forms', [])
             if f.get('form_id') == current_scope['form_id']]
    if len(forms) != 1 or found.get('class_key') != current_scope['class_key'] or found.get('week') != current_scope['week']:
        raise ValueError('review work scope')
    form = forms[0]
    rows = completion.recheck_candidates(form)
    notices, limitations = [], []
    case_skip = (current_scope['class_key'] == 'forensics' and work.week_number(current_scope['week']) == 1
                 and bool(re.fullmatch(r'(?:work\s+a\s+case|(?:.*\b)?sheppard(?:\b.*)?)',
                                       str(current_scope['lesson']).strip(), re.I)))
    if case_skip:
        return {'schema': SCHEMA, 'checked_at': checked_at or datetime.now(timezone.utc).isoformat(),
                'scope': current_scope, 'notices': [], 'status': 'excluded',
                'limitations': ['The teacher asked to skip Week 01 cases for grading. No case review or grade is requested here. This display does not certify a deployed grading exclusion; Lab Safety and existing grade history are unchanged.'],
                'source_selection': {'submitted_originals': sum(len(r.get('files', [])) for r in form.get('students', []) + form.get('late', [])),
                                     'assessment_performed': False}}
    for row in rows + [r for r in form.get('students', []) + form.get('late', []) if not r.get('matched')]:
        if _exempt(row):
            continue
        reason = row.get('selection_problem')
        if not _student(row)['verified'] and row.get('files'):
            reason = reason or 'This original source could not be matched to a verified student and class section.'
        files = row.get('files', [])
        if reason:
            notices.append(notice(current_scope, row, reason))
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
                    action='Open this exact original and check its availability or lesson match; preserve existing grades.'))
            elif score and score.get('note'):
                notices.append(notice(current_scope, row, score['note'], files=[upload], assessed=True,
                    action=score.get('action') or 'Review the marked response on the verified slide(s), preserving the student’s original answers and current grades.',
                    basis={'kind': 'cached_assessment_verified', 'file_id': ident, 'template_id': score.get('template_id')}))
    for item in form.get('elsewhere', []):
        if item.get('accepted_intake'):
            continue
        matched = [r for r in form.get('missing', []) if item.get('who') == '%s, %s' % (r.get('last'), r.get('first'))
                   and item.get('section') == r.get('section')]
        row = {**matched[0], 'matched': True} if len(matched) == 1 else {}
        if _exempt(row):
            continue
        verified_route = bool(current_scope['form_id'] and item.get('source_form_id') == current_scope['form_id']
                              and item.get('assignment_form_id') and item['assignment_form_id'] != current_scope['form_id'])
        reason = ('This original was received for %s, rather than this lesson.' % item['to']
                  if verified_route and item.get('to') else 'This original’s lesson and turn-in route could not be confirmed.')
        link = source_link(item.get('url'))
        files = [{**link, 'label': item.get('file') or 'Original source'}] if link else []
        notices.append(notice(current_scope, row, reason, files=files,
            action='Review the original turn-in route shown here; preserve current grades.'))
    for reason in [form.get('problem'), found.get('assessment_problem'), found.get('folder_note'), *found.get('problems', [])]:
        if reason:
            notices.append(notice(current_scope, {}, reason, files=current_scope['sources'],
                action='Check the linked Form or lesson source for this class and week; no student or grade is inferred.'))
    for row in found.get('other_files', []):
        if not _exempt(row):
            notices.append(notice(current_scope, row,
                'This source was found in this week’s collection, but its lesson match is unconfirmed; it is not assigned to the selected lesson.',
                action='Confirm this original’s lesson from its actual content and turn-in route; preserve current grades.'))
    submitted = sum(len(row.get('files', [])) for row in form.get('students', []) + form.get('late', []))
    waiting = bool(not submitted and not form.get('elsewhere') and not form.get('problem')
                   and not found.get('other_files') and not found.get('problems') and not found.get('assessment_problem'))
    waiting = waiting and not found.get('folder_note')
    if waiting:
        limitations.append('Waiting for submissions. No student-copy repair or grade is requested before an original is received.')
    elif submitted:
        limitations.append('This view shows current source issues, not a new grading pass. Earlier response checks appear only when the original and linked teacher copy still match. Other responses have not been checked here.')
    return {'schema': SCHEMA, 'checked_at': checked_at or datetime.now(timezone.utc).isoformat(),
            'scope': current_scope, 'notices': notices, 'limitations': limitations,
            'status': 'review' if notices else 'waiting' if waiting else 'not_assessed',
            'source_selection': {'submitted_originals': submitted, 'assessment_performed': False}}


def read(token, config, agenda, class_key, week, form_id):
    current_scope = scope(agenda, config, class_key, week, form_id)
    found = work.lesson_work(token, config, agenda, class_key, week, review_only=True)
    forms = [f for lesson in found.get('lessons', []) for f in lesson.get('forms', []) if f.get('form_id') == form_id]
    if len(forms) != 1:
        raise ValueError('review form')
    cached, limitations = cached_reviews(token, agenda, class_key, week, form_id,
                                         completion.recheck_candidates(forms[0]))
    result = build(current_scope, found, cached)
    result['limitations'].extend(limitations)
    return result


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
