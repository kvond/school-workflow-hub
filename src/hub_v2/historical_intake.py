"""Read-only exact original-event intake; never a retired-Form wildcard.

The private runtime manifest is attributed preparation evidence, not grade or
identity authority. A trusted host must supply fresh canonical IC/email proof
and a current private-config reader. No HTTP payload enables either reader.
Absent readers, incomplete coverage, or changed evidence hold the target.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import re

from . import agenda as agenda_module, completion, google, roster, work
from school_workflow_hub.core.cloud_state import bounded_json, instant
from school_workflow_hub.core.runs import content_hash

CONFIG = 'HUB_HISTORICAL_FORM_EVENTS'
SCHEMA = 'hub-historical-form-events/v1'
META = google.FILE_FIELDS + ',version,md5Checksum,size'
MAX_EVENTS = 20
MAX_AGE = timedelta(minutes=15)
_ID = re.compile(r'[A-Za-z0-9_-]{10,200}\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_host = ContextVar('historical_intake_verified_host', default=None)


class Unverified(ValueError):
    pass


@contextmanager
def verified_host(*, read_config, read_identities):
    """Trusted-host injection, not an HTTP API or an assertion of eligibility.

    read_config() returns current private runtime configuration. read_identities
    (class_key, school_year, emails) must read the existing complete canonical
    roster receipt plus independently observed native email-to-local-ID proof.
    It returns the bounded contract validated by _identities below, including
    receipt and native email proof hashes. A names-only workbook cannot supply
    this contract. The same readers run again at the bookend.
    """
    if not callable(read_config) or not callable(read_identities):
        raise Unverified('Historical intake needs actual read-only host evidence readers.')
    state = _host.set((read_config, read_identities))
    try:
        yield
    finally:
        _host.reset(state)


def _id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _hash(value):
    return isinstance(value, str) and bool(_HASH.fullmatch(value))


def _require(condition, message):
    if not condition:
        raise Unverified(message)


def _metadata(token, fid):
    md = google.drive_file(token, fid, META)
    _require(md.get('id') == fid and md.get('trashed') is False
             and md.get('mimeType') in (completion.SLIDES, google.PPTX)
             and isinstance(md.get('version'), str) and md['version'].isdigit()
             and md.get('name') and isinstance(md.get('parents'), list)
             and md.get('modifiedTime'), 'An exact historical source revision is unavailable.')
    instant(md['modifiedTime'])
    return md


def source_capture(token, fid, *, role):
    """Fresh original revision and full parsed-content proof; no assessment."""
    _require(_id(fid) and role in ('student', 'teacher'), 'Invalid original source scope.')
    before = _metadata(token, fid)
    # Preserve the existing student/teacher native-reader distinction.
    if role == 'teacher':
        md, deck = completion._teacher_template(token, fid)
        _require(all(md.get(k) == before.get(k) for k in
                     ('id', 'mimeType', 'version', 'modifiedTime', 'parents')),
                 'The exact registered teacher revision changed.')
    else:
        deck = completion._read(token, fid, before, source_role='student')
    _require(_metadata(token, fid) == before, 'An original source changed during its content read.')
    _require(isinstance(deck, dict) and isinstance(deck.get('slides'), list)
             and 0 < len(deck['slides']) < completion.MAX_SLIDES,
             'The original deck content is incomplete or reaches the reader slide boundary.')
    return {'metadata': before, 'content_sha256': content_hash(deck)}, deck


def _source_form(token, fid):
    md = google.drive_file(token, fid, META)
    _require(md.get('id') == fid and md.get('mimeType') == 'application/vnd.google-apps.form'
             and md.get('trashed') is False and md.get('version') and md.get('modifiedTime'),
             'The exact original Form is unavailable or retired to trash.')
    definition = google.get_json('https://forms.googleapis.com/v1/forms/' + fid, token)
    _require(definition.get('formId') == fid and definition.get('revisionId'),
             'The exact historical Form definition is unavailable.')
    responses = google.form_responses(token, fid)  # Complete, bounded pagination or exception.
    _require(isinstance(responses, list) and 1 <= len(responses) <= MAX_EVENTS
             and len({r.get('responseId') for r in responses}) == len(responses),
             'Historical Form response coverage is incomplete or ambiguous.')
    _require(google.drive_file(token, fid, META) == md, 'The original Form changed during its response read.')
    return definition, sorted(responses, key=lambda r: str(r.get('responseId'))), md


def _event(definition, responses, entry):
    event = entry['event']
    question = event['question_id']
    declarations = [i.get('questionItem', {}).get('question', {}) for i in definition.get('items', [])]
    _require(sum(q.get('questionId') == question and bool(q.get('fileUploadQuestion'))
                 for q in declarations) == 1, 'The original upload question is not uniquely declared.')
    matches = [r for r in responses if r.get('responseId') == event['response_id']]
    _require(len(matches) == 1, 'The exact original response is unavailable.')
    response = matches[0]
    _require(content_hash(response) == event['sha256']
             and response.get('respondentEmail', '').lower() == event['email']
             and response.get('createTime') == event['created_at']
             and response.get('lastSubmittedTime') == event['last_submitted_at'],
             'The original respondent, timestamp or response content changed.')
    answer = response.get('answers', {}).get(question, {})
    uploads = answer.get('fileUploadAnswers', {}).get('answers', [])
    fid = entry['original']['metadata']['id']
    _require(answer.get('questionId') == question and sum(f.get('fileId') == fid for f in uploads) == 1,
             'The exact original file is not declared once by its original upload question.')
    return response


def _enumeration(responses):
    result = []
    for response in responses:
        for question, answer in response.get('answers', {}).items():
            for upload in answer.get('fileUploadAnswers', {}).get('answers', []):
                result.append((response.get('responseId'), question, upload.get('fileId')))
    _require(len(result) == len(set(result)), 'Historical upload enumeration contains duplicates.')
    return set(result)


def _retired(agenda, source_id):
    _require(not any(f.get('form_id') == source_id
                     for cls, details in agenda.classes.items()
                     for week in {w for _, w in (details.get('layout') or {'rows': []})['rows']}
                     for lesson in agenda.lessons(cls, week) for f in lesson.get('forms', [])),
             'The historical intake Form is currently registered; use its ordinary source contract.')


def _target(config, agenda, entry):
    target = entry['target']; cls, week, fid = target['class_key'], target['week'], target['form_id']
    binding = work.form_teacher_sources(config, agenda, cls, week, fid)
    _require(binding and target['teacher_id'] in binding['teacher_ids'],
             'The historical event needs its exact current registered teacher binding.')
    policy = work.assignment_policy(config, agenda, cls, week, fid)
    _require(not policy.get('problem') and not policy.get('excluded'),
             'Historical intake cannot bypass an existing exact assignment exclusion.')
    ownership = completion.form_source_ownership(config, agenda, cls, week, fid, binding=binding)
    lesson = completion.form_source_scope(agenda, cls, week, fid, binding=binding, ownership=ownership)
    return {'agenda_id': agenda.spreadsheet_id, 'school_year': work._school_year(agenda, week),
            'class_key': cls, 'week': week, 'form_id': fid,
            'teacher_source_folder_id': binding['source_folder_id'],
            'context_sha256': content_hash({'lesson': lesson, 'binding': binding, 'ownership': ownership})}


def _questions(deck, questions):
    _require(isinstance(questions, list) and 2 <= len(questions) <= 20,
             'Historical lesson evidence needs exact reviewed original question objects.')
    keys = set()
    for q in questions:
        _require(isinstance(q, dict) and set(q) == {'slide', 'object_key', 'text_sha256'}
                 and type(q['slide']) is int and q['slide'] > 0 and isinstance(q['object_key'], str)
                 and q['object_key'] and _hash(q['text_sha256']), 'Invalid historical question proof.')
        pair = q['slide'], q['object_key']
        _require(pair not in keys, 'Duplicate historical question object.')
        keys.add(pair)
        units = [u for s in deck['slides'] if s.get('number') == q['slide']
                 for u in s.get('units', []) if u.get('key') == q['object_key']]
        _require(len(units) == 1 and content_hash(units[0].get('text', '')) == q['text_sha256'],
                 'A reviewed original question object changed or is unavailable.')


def manifest(config):
    """Validate a bounded attributed private declaration, never HTTP data."""
    raw = config.get(CONFIG)
    if not raw:
        return None
    try:
        value = json.loads(raw)
        bounded_json(value)
        fields = {'schema', 'agenda_id', 'school_year', 'class_key', 'operator', 'instruction_sha256',
                  'decision_reference', 'verified_at', 'source_form', 'entries'}
        _require(isinstance(value, dict) and set(value) == fields and value['schema'] == SCHEMA,
                 'Invalid historical intake manifest.')
        allowed = {x.strip().lower() for x in config.get('HUB_ALLOWED_EMAILS', '').split(',') if x.strip()}
        _require(value['operator'] in allowed and _hash(value['instruction_sha256'])
                 and isinstance(value['decision_reference'], str) and value['decision_reference'].startswith('https://')
                 and 15 <= len(value['decision_reference']) <= 1000
                 and instant(value['verified_at']) <= datetime.now(timezone.utc)
                 and _id(value['agenda_id']) and value['class_key'] in roster.SECTIONS
                 and re.fullmatch(r'20\d{2}-\d{2}', value['school_year']),
                 'Historical intake requires exact attributed decision evidence.')
        source = value['source_form']
        _require(isinstance(source, dict) and set(source) == {'form_id', 'definition_sha256', 'responses_sha256', 'metadata'}
                 and _id(source['form_id']) and isinstance(source['metadata'], dict) and _hash(source['definition_sha256']) and _hash(source['responses_sha256']),
                 'Historical intake needs its complete original Form proof.')
        entries = value['entries']
        _require(isinstance(entries, list) and 1 <= len(entries) <= MAX_EVENTS,
                 'Historical intake needs a bounded complete event enumeration.')
        seen, files, event_ids = set(), set(), set()
        for entry in entries:
            _require(isinstance(entry, dict) and set(entry) == {'event', 'original', 'target', 'identity'},
                     'Invalid historical original event.')
            event, original, target, identity = (entry[k] for k in ('event', 'original', 'target', 'identity'))
            _require(set(event) == {'response_id', 'question_id', 'email', 'created_at', 'last_submitted_at', 'sha256'}
                     and _id(event['response_id']) and isinstance(event['question_id'], str) and event['question_id']
                     and re.fullmatch(r'[^@\s]+@[^@\s]+', event['email']) and event['email'] == event['email'].lower()
                     and _hash(event['sha256']), 'Invalid original response identity.')
            _require(instant(event['created_at']) <= instant(event['last_submitted_at'] or event['created_at'])
                     <= datetime.now(timezone.utc), 'The original submission timestamps are inconsistent or in the future.')
            _require(set(original) == {'metadata', 'content_sha256', 'questions'}
                     and isinstance(original['metadata'], dict) and _id(original['metadata'].get('id'))
                     and _hash(original['content_sha256']), 'Invalid original source proof.')
            _require(set(target) == {'class_key', 'week', 'form_id', 'teacher_id', 'cycle', 'scope', 'teacher'}
                     and target['class_key'] == value['class_key'] and isinstance(target['week'], str)
                     and _id(target['form_id']) and _id(target['teacher_id']) and target['form_id'] != source['form_id']
                     and isinstance(target['cycle'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,30}', target['cycle'])
                     and isinstance(target['scope'], dict) and isinstance(target['teacher'], dict)
                     and set(target['teacher']) == {'metadata', 'content_sha256', 'questions'},
                     'Invalid exact historical target binding.')
            _require(set(identity) == {'school_email', 'local_student_number', 'membership_id', 'section',
                                      'native_email_proof_sha256'}
                     and identity['school_email'] == event['email']
                     and all(isinstance(identity[k], str) and identity[k] for k in identity)
                     and re.fullmatch(r'\d{2,20}', identity['local_student_number'])
                     and identity['section'] in roster.SECTIONS[value['class_key']]
                     and _hash(identity['native_email_proof_sha256']), 'Native IC email identity evidence is unavailable.')
            key = event['response_id'], event['question_id'], original['metadata']['id']
            _require(key not in seen and key[2] not in files and key[0] not in event_ids,
                     'Historical intake requires one exact upload per original event, without competing routes.')
            seen.add(key); files.add(key[2]); event_ids.add(key[0])
        return value
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        if isinstance(error, Unverified):
            raise
        raise Unverified('Historical intake manifest is incomplete; preserve originals and verify its private evidence.') from None


def _identities(reader, value, entries, students):
    emails = sorted({e['event']['email'] for e in entries})
    current = reader(value['class_key'], value['school_year'], emails)
    bounded_json(current)
    _require(isinstance(current, dict) and set(current) == {'source', 'complete', 'checked_at',
             'canonical_receipt_reference', 'canonical_receipt_sha256', 'identities'}
             and current['source'] == 'infinite_campus' and current['complete'] is True
             and isinstance(current['canonical_receipt_reference'], str) and current['canonical_receipt_reference']
             and _hash(current['canonical_receipt_sha256'])
             and timedelta(0) <= datetime.now(timezone.utc) - instant(current['checked_at']) <= MAX_AGE,
             'Current complete canonical IC/email identity evidence is unavailable.')
    people = current['identities']
    _require(isinstance(people, list) and len(people) == len(emails)
             and all(isinstance(p, dict) for p in people)
             and {p.get('school_email') for p in people} == set(emails),
             'Native IC email identity coverage is incomplete or duplicated.')
    _require(len({p.get('local_student_number') for p in people}) == len(people)
             and len({p.get('membership_id') for p in people}) == len(people),
             'Canonical IC identities have competing email or membership claims.')
    for entry in entries:
        identity = entry['identity']
        person = next(p for p in people if p['school_email'] == identity['school_email'])
        _require(set(person) == set(identity) | {'class_key', 'school_year', 'active', 'first', 'last'}
                 and all(person.get(k) == v for k, v in identity.items())
                 and person['class_key'] == value['class_key'] and person['school_year'] == value['school_year']
                 and person['active'] is True,
                 'An original respondent is not the same current native IC member.')
        matching = [s for s in students if s.get('section') == person['section']
                    and all(roster.plain(s.get(k)) == roster.plain(person[k]) for k in ('first', 'last'))]
        _require(len(matching) == 1 and all(person[k] for k in ('first', 'last')),
                 'The canonical IC member does not match one current workbook row.')
    return current


def verify(token, config, agenda, value, entries, students, *, roster_problems=()):
    """Verify one affected target atomically. All returned events are originals."""
    host = _host.get()
    _require(host is not None, 'Historical intake is waiting for the canonical IC/email evidence reader.')
    config_reader, identity_reader = host
    _require(not roster_problems and students, 'Historical intake requires complete current roster evidence.')
    current_config = config_reader()
    _require(current_config == config, 'The approved historical manifest or runtime context changed.')
    _require(agenda.spreadsheet_id == value['agenda_id'], 'Historical intake belongs to another Agenda.')
    identity = _identities(identity_reader, value, entries, students)
    domains = {d.strip().lower() for d in current_config.get('HUB_SCHOOL_EMAIL_DOMAINS', '').split(',') if d.strip()}
    _require(domains and all(e['event']['email'].rsplit('@', 1)[-1] in domains for e in entries),
             'Historical respondents need verified school email domains.')
    source_id = value['source_form']['form_id']
    definition, responses, source_metadata = _source_form(token, source_id)
    expected = value['source_form']
    _require(content_hash(definition) == expected['definition_sha256']
             and source_metadata == expected['metadata']
             and content_hash(responses) == expected['responses_sha256'], 'Original Form coverage changed or is incomplete.')
    _retired(agenda, source_id)
    complete = {(e['event']['response_id'], e['event']['question_id'], e['original']['metadata']['id'])
                for e in value['entries']}
    _require(_enumeration(responses) == complete, 'The historical Form is not completely enumerated by exact original events.')
    plans, captures, targets = [], {}, {}
    for entry in entries:
        response = _event(definition, responses, entry)
        target = entry['target']; fid = entry['original']['metadata']['id']; teacher = target['teacher_id']
        scope = _target(current_config, agenda, entry)
        _require(scope == target['scope'] and scope['school_year'] == value['school_year'],
                 'The current target Agenda or teacher ownership changed.')
        if teacher not in targets:
            targets[teacher] = source_capture(token, teacher, role='teacher')
        capture, deck = source_capture(token, fid, role='student'); captures[fid] = capture
        teacher_capture, teacher_deck = targets[teacher]
        _require(teacher_capture['metadata'].get('parents') == [scope['teacher_source_folder_id']],
                 'The exact teacher is not in its registered source folder.')
        for actual, approved in ((capture, entry['original']), (teacher_capture, target['teacher'])):
            _require(actual == {k: approved[k] for k in ('metadata', 'content_sha256')},
                     'Reviewed original/teacher content or revision changed.')
        _questions(deck, entry['original']['questions']); _questions(teacher_deck, target['teacher']['questions'])
        # Cycle is a cross-check on reviewed content, never a title-derived route.
        for source in (deck, teacher_deck):
            cycles = {m['code'] for m in completion.markers(source)}
            cycles.update(m.group(1) for s in source['slides'] for u in s['units']
                          for m in completion.INVENTORY.finditer(u.get('text', '')))
            _require(cycles == {target['cycle']}, 'Reviewed lesson content has a different or ambiguous cycle.')
        person = next(p for p in identity['identities'] if p['school_email'] == entry['event']['email'])
        candidate = work.submissions([response], source_id)[0]
        candidate['files'] = [f for f in candidate['files'] if f['id'] == fid]
        # Native ID/email proof chooses the current row. Username heuristics do not.
        candidate['_historical_identity'] = {k: person[k] for k in ('section', 'first', 'last')}
        candidate['_historical_target'] = target['form_id']
        candidate['files'][0].update(assignment_form_id=target['form_id'], accepted_intake=True,
            historical_intake_sha256=content_hash(entry), via='Verified original historical Form')
        plans.append(candidate)
    # Fresh final native bookends, including the actual host configuration and
    # canonical identity evidence. No partial plan escapes a failed check.
    def check():
        fresh = agenda_module.load(token, agenda.spreadsheet_id)
        _retired(fresh, source_id)
        _require(config_reader() == current_config, 'Private configuration changed during historical intake.')
        for entry in entries:
            _require(_target(current_config, fresh, entry) == entry['target']['scope'], 'The target Agenda changed during intake.')
        _require(_source_form(token, source_id) == (definition, responses, source_metadata), 'The original Form changed during intake.')
        for fid, capture in list(captures.items()) + [(fid, c) for fid, (c, _) in targets.items()]:
            _require(_metadata(token, fid) == capture['metadata'], 'An exact original revision changed at readback.')
        _require(_identities(identity_reader, value, entries, students) == identity,
                 'Canonical IC/email identity evidence changed during intake.')
    check()
    return plans, check


def bookend(form):
    """Recheck after ordinary discovery as well, before rows can be returned."""
    check = form.pop('_historical_bookend', None)
    staged = form.pop('_historical_staged', ())
    if check is None:
        return
    try:
        check()
    except (Unverified, google.GoogleError, completion.Unreadable, ValueError, KeyError, TypeError) as error:
        # Drop only this request's staged derived candidates. Original current
        # Form events and their counts remain intact; reserved source IDs are
        # independent, so folder/title routing cannot resurrect failed proof.
        form['found'][:] = [s for s in form['found'] if not any(s is candidate for candidate in staged)]
        for candidate in staged:
            candidate.pop('_historical_identity', None)
            candidate.pop('_historical_target', None)
        form['problem'] = 'Historical intake unverified: ' + str(error)



def attach(token, config, agenda, class_key, week, forms, students, *, roster_problems=()):
    """Inject verified target events before folder discovery; no source writes.

    Return reserved exact historical file IDs so folder/name heuristics cannot
    silently repurpose an approved original when its proof is held. A target's
    problem blocks its scoring/absence zeros; unrelated forms remain untouched.
    """
    value = manifest(config)
    if value is None or value['class_key'] != class_key or value['agenda_id'] != agenda.spreadsheet_id:
        return set()
    reserved = {e['original']['metadata']['id'] for e in value['entries']}
    for form in forms:
        if any(f['id'] in reserved for s in form['found'] for f in s['files']):
            form['problem'] = ('Historical intake unverified: an original file also has a current Form event; '
                               'reconcile its exact original history before scoring.')
    relevant = [e for e in value['entries'] if e['target']['week'] == week]
    if not relevant:
        return reserved
    _require(work._school_year(agenda, week) == value['school_year'], 'Historical intake school year changed.')
    for target_id in sorted({e['target']['form_id'] for e in relevant}):
        matches = [f for f in forms if f.get('form_id') == target_id]
        _require(len(matches) == 1, 'An exact historical target Form is no longer registered.')
        form = matches[0]
        selected = [e for e in relevant if e['target']['form_id'] == target_id]
        try:
            _require(not form.get('problem') and not form.get('_combined_scope'),
                     'Historical intake cannot bypass target availability or combined-assignment scope.')
            plans, check = verify(token, config, agenda, value, selected, students, roster_problems=roster_problems)
            existing = {f['id'] for s in form['found'] for f in s['files']}
            _require(not any(f['id'] in existing for s in plans for f in s['files']),
                     'An original historical file also has a current Form event; reconcile its exact history.')
            form['found'].extend(plans)
            form['_historical_bookend'] = check
            form['_historical_staged'] = plans
        except (Unverified, google.GoogleError, completion.Unreadable, ValueError, KeyError, TypeError) as error:
            form['problem'] = 'Historical intake unverified: ' + str(error)
    return reserved
