"""Reused Form lessons on fictional originals and fully mocked school IO."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from hub_v2 import app, completion, google, morning, saved_completion, teacher_material, work
from tests.test_cloud_state import MemoryCAS


AGENDA = 'FictionalReusedAgenda001'
FORM = 'FictionalReusedLessonForm001'
UPLOAD_ROOT = 'FictionalUploadRoot001'
CURRENT_TEACHER = 'FictionalCourtroomTeacher001'
PRIOR_TEACHER = 'FictionalHistoryTeacher001'
CURRENT_FOLDER = 'FictionalCourtroomFolder001'
PRIOR_FOLDER = 'FictionalHistoryFolder001'
CURRENT_TITLE = 'Fictional Courtroom Evidence Workshop'
PRIOR_TITLE = 'Fictional History Laboratory Workshop'
SECTION = 'A2 Forensics'
STAMP = '2026-10-20T12:00:00Z'
NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


class Agenda:
    spreadsheet_id = AGENDA
    title = 'Fictional source-selection Agenda'

    def __init__(self):
        self.weeks = [{'label': 'Wk 06', 'monday': '2026-10-05'},
                      {'label': 'Wk 07', 'monday': '2026-10-12'}]
        self.classes = {'forensics': {'name': 'Forensics', 'layout': {
            'rows': [(6, 'Wk 06'), (7, 'Wk 07')]}}}
        self.rows = {
            'Wk 06': [self.lesson(PRIOR_TITLE, PRIOR_TEACHER, 6)],
            'Wk 07': [self.lesson(CURRENT_TITLE, CURRENT_TEACHER, 7)],
        }

    @staticmethod
    def lesson(title, teacher, row):
        return {'row': row, 'topic': title, 'edit_url': 'https://fictional.example/agenda',
                'materials': [{'label': 'Teacher deck', 'url':
                    'https://docs.google.com/presentation/d/' + teacher + '/edit'}],
                'forms': [{'form_id': FORM, 'label': title + ' — Turn in',
                           'url': 'https://docs.google.com/forms/d/' + FORM + '/edit', 'problem': None}]}

    def lessons(self, cls, week):
        return deepcopy(self.rows.get(week, []))

    def week_options(self, cls):
        return deepcopy(self.weeks)


def learner(index):
    suffix = chr(ord('A') + index)
    return {'class_key': 'forensics', 'first': 'Fictional' + suffix,
            'last': 'Learner' + suffix, 'section': SECTION, 'order': 'header', 'added': ''}


def email(index):
    row = learner(index)
    return 's.%s.%s@school.example' % (row['first'].lower(), row['last'].lower())


def response(index, ident, title, stamp, *, extra=()):
    uploads = [(ident, title), *extra]
    return {'responseId': 'Response_' + ident, 'respondentEmail': email(index),
            'createTime': stamp, 'lastSubmittedTime': stamp,
            'answers': {'fictional_upload': {'fileUploadAnswers': {'answers': [
                {'fileId': fid, 'fileName': 'Copy of ' + name + ' - Fictional learner',
                 'mimeType': completion.SLIDES} for fid, name in uploads]}}}}


def binding(week, teacher, folder):
    return {'school_year': '2026-27', 'agenda_id': AGENDA, 'class_key': 'forensics',
            'week': week, 'form_id': FORM, 'source_folder_id': folder, 'teacher_ids': [teacher]}


class ReusedFormLessonSelectionTests(unittest.TestCase):
    def setUp(self):
        self.agenda = Agenda()
        self.port = MemoryCAS()
        self.port.now = NOW
        self.students = [learner(i) for i in range(7)]
        self.config = {'HUB_STATE_BACKEND': 'firestore',
            'HUB_SCHOOL_EMAIL_DOMAINS': 'school.example',
            'HUB_READER_SERVICE_ACCOUNT': 'reader@fictional.example',
            'HUB_FORM_TEACHER_SOURCES': json.dumps([binding('Wk 07', CURRENT_TEACHER, CURRENT_FOLDER)])}
        self.responses = [response(i, 'FictionalCourtOriginal%03d' % i, CURRENT_TITLE,
                                   '2026-10-12T12:%02d:00Z' % i) for i in range(7)]
        self.responses += [response(i, 'FictionalHistoryOriginal%03d' % i, PRIOR_TITLE,
                                    '2026-10-13T12:%02d:00Z' % i) for i in range(3)]
        self.metadata_changes = {}
        self.extra_metadata = {}
        self.metadata_failures = set()

    def metadata(self, token, fid, fields=None):
        if fid in self.metadata_failures:
            raise google.GoogleError('Fictional source metadata is unavailable.', 503)
        teachers = {CURRENT_TEACHER: (CURRENT_TITLE, CURRENT_FOLDER),
                    PRIOR_TEACHER: (PRIOR_TITLE, PRIOR_FOLDER)}
        if fid in self.extra_metadata:
            value = deepcopy(self.extra_metadata[fid])
        elif fid in teachers:
            name, parent = teachers[fid]
            value = {'id': fid, 'name': name, 'mimeType': completion.SLIDES,
                     'parents': [parent]}
        elif fid in (CURRENT_FOLDER, PRIOR_FOLDER, UPLOAD_ROOT):
            value = {'id': fid, 'name': 'Fictional source collection',
                     'mimeType': google.FOLDER, 'parents': []}
        elif fid == FORM:
            value = {'id': fid, 'name': 'Fictional shared lesson intake',
                     'mimeType': 'application/vnd.google-apps.form', 'parents': []}
        else:
            uploads = [u for r in self.responses for answer in r['answers'].values()
                       for u in answer['fileUploadAnswers']['answers'] if u['fileId'] == fid]
            if not uploads:
                raise AssertionError('Unexpected resource read: ' + fid)
            value = {'id': fid, 'name': uploads[-1]['fileName'], 'mimeType': completion.SLIDES,
                     'parents': [UPLOAD_ROOT]}
        value.update(trashed=False, version='7', modifiedTime=STAMP)
        value.update(self.metadata_changes.get(fid, {}))
        return deepcopy(value)

    @contextmanager
    def io(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(work.roster, 'load',
                side_effect=lambda *_: ({'forensics': deepcopy(self.students)}, [])))
            stack.enter_context(patch.object(google, 'service_account', return_value='reader@fictional.example'))
            def read_responses(token, fid):
                if fid != FORM:
                    raise AssertionError('Unexpected Form read: ' + fid)
                return deepcopy(self.responses)
            forms = stack.enter_context(patch.object(google, 'form_responses', side_effect=read_responses))
            stack.enter_context(patch.object(google, 'drive_file', side_effect=self.metadata))
            stack.enter_context(patch.object(google, 'folder_children', return_value=[]))
            stack.enter_context(patch.object(work, 'lesson_folders', return_value=({UPLOAD_ROOT: 'Fictional uploads'}, None)))
            stack.enter_context(patch.object(work, 'list_tree', return_value=([], {})))
            stack.enter_context(patch.object(work, 'shared_submissions', return_value=([], None)))
            stack.enter_context(patch.object(teacher_material, 'configured_store', return_value=self.port))
            stack.enter_context(patch.object(saved_completion, 'configured_store', return_value=self.port))
            stack.enter_context(patch.object(google, 'get_json', side_effect=AssertionError('No provider network')))
            stack.enter_context(patch.object(google, 'deck_bytes', side_effect=AssertionError('Discovery cannot export')))
            stack.enter_context(patch.object(completion, 'assess', side_effect=AssertionError('No assessment')))
            stack.enter_context(patch.object(completion, 'assignment_readiness', return_value={'status': 'READY'}))
            yield forms

    def discover(self, *, readonly=False):
        if readonly:
            with patch.object(completion, 'assignment_readiness',
                              side_effect=AssertionError('Read-only discovery cannot check readiness')):
                return work.lesson_work('fictional-read', self.config, self.agenda,
                                        'forensics', 'Wk 07', review_only=True)
        return work.lesson_work('fictional-read', self.config, self.agenda,
                                'forensics', 'Wk 07', review_only=readonly)

    def form(self, found):
        forms = [f for lesson in found['lessons'] for f in lesson['forms']]
        self.assertEqual(len(forms), 1, 'One existing Form, without duplicate or virtual targets')
        self.assertEqual(forms[0]['form_id'], FORM)
        return forms[0]

    @staticmethod
    def ids(rows):
        return {f['id'] for row in rows for f in row.get('files', [])}

    def assert_current_seven(self, found):
        form = self.form(found)
        expected = {'FictionalCourtOriginal%03d' % i for i in range(7)}
        self.assertEqual(self.ids(form['students']), expected)
        self.assertEqual(form['late'], [])
        self.assertFalse(any(row.get('selection_problem') for row in form['students']))
        self.assertFalse(found['other_files'])
        self.assertFalse(found['problems'])
        self.assertEqual(self.ids(completion.recheck_candidates(form, now=NOW)), expected)
        return form

    def test_real_discovery_keeps_seven_current_lesson_originals_before_revision_selection(self):
        before = deepcopy(self.responses)
        with self.io() as reads:
            found = self.discover()
        form = self.assert_current_seven(found)
        self.assertEqual([c.args[1] for c in reads.call_args_list], [FORM])
        self.assertEqual(len(form['elsewhere']), 3)
        self.assertEqual(self.responses, before)
        self.assertFalse(self.port.rows, 'Discovery makes no private-store write')
        for item in form['elsewhere']:
            original = item['original_submission']
            self.assertTrue(original['event'])
            self.assertEqual(original['source'], 'form')
            file = original['files'][0]
            raw = next(r for r in before if r['responseId'] == original['key'])
            self.assertEqual((original['email'], original['submitted_at']),
                             (raw['respondentEmail'], raw['lastSubmittedTime']))
            self.assertEqual((file['source_form_id'], file['source_response_id'], file['source_submitted_at']),
                             (FORM, raw['responseId'], raw['lastSubmittedTime']))
            context = item['lesson_context']
            self.assertEqual((context['week'], context['form_id'], context['teacher_ids']),
                             ('Wk 06', FORM, [PRIOR_TEACHER]))
            self.assertEqual((context['class_key'], context['school_year']), ('forensics', '2026-27'))
            self.assertFalse(item['accepted_intake'], 'Same Form context is not an alternate-intake grant')

    def test_routing_only_context_uses_existing_teacher_proof_without_fetching_responses(self):
        with self.io() as reads:
            current = [{'form_id': FORM, 'problem': None, 'teacher_source_binding':
                        binding('Wk 07', CURRENT_TEACHER, CURRENT_FOLDER), '_teacher_source_verified': True}]
            contexts = work.nearby_forms('fictional-read', self.agenda, 'forensics', 'Wk 07',
                                         {FORM}, config=self.config, current_forms=current)
        self.assertEqual(len(contexts), 1)
        self.assertTrue(contexts[0]['routing_context_only'])
        self.assertEqual(contexts[0]['found'], [])
        self.assertEqual(contexts[0]['week'], 'Wk 06')
        self.assertTrue(contexts[0]['teacher_source_verified'])
        reads.assert_not_called()

    def test_input_order_does_not_change_unique_lesson_routing(self):
        with self.io():
            first = self.discover(readonly=True)
            self.responses.reverse()
            second = self.discover(readonly=True)
        self.assert_current_seven(first)
        self.assert_current_seven(second)
        self.assertEqual({x['original_submission']['key'] for x in self.form(first)['elsewhere']},
                         {x['original_submission']['key'] for x in self.form(second)['elsewhere']})

    def test_normal_score_batch_and_saved_read_include_only_current_lesson_seven(self):
        with self.io():
            found = self.discover()
            form = self.assert_current_seven(found)
            results, cache = {}, {}
            for fid in self.ids(form['students']):
                result = {'file_id': fid, 'file_updated': STAMP, 'template_id': CURRENT_TEACHER,
                    'template_updated': STAMP, 'code': 'F07', 'scoring_basis': 'found_codes',
                    'notes_score': 8.0, 'revision_score': 10.0,
                    'counts': {'NOTES': {'done': 4, 'required': 5}, 'BANK': {'done': 0, 'required': 0},
                               'DRAFT': {'done': 1, 'required': 1}}}
                results[fid] = result
                cache[(fid, STAMP, '7')] = {'template': (CURRENT_TEACHER, STAMP, '7'),
                    'student_coding_required': False, 'result': deepcopy(result)}
            def score(token, agenda, cls, week, form_id, file_id, found, **kwargs):
                return deepcopy(results[file_id])
            with patch.object(completion, 'score_file', side_effect=score) as scorer, \
                    patch.dict(completion._results, cache, clear=True), saved_completion.recording(self.config):
                scored = app._score_rows('fictional-read', self.agenda, 'forensics', 'Wk 07', FORM, form, found)
            self.assertEqual({c.args[5] for c in scorer.call_args_list}, set(results))
            self.assertEqual(scorer.call_count, 7)
            self.assertEqual(len(scored), 7)
            self.assertEqual(len(self.port.rows), 7)
            with patch.object(completion, 'score_file', side_effect=AssertionError('Saved read cannot score')), \
                    patch.object(completion, 'assignment_readiness',
                                 side_effect=AssertionError('Saved read cannot check readiness')):
                batch = saved_completion.read_many('fictional-read', self.config, self.agenda,
                                                  'forensics', 'Wk 07', FORM, found)
        self.assertEqual(batch['status'], 'available', batch)
        self.assertEqual(set(batch['results']), set(results))
        self.assertTrue(all(value['status'] == 'saved' for value in batch['results'].values()), batch)

    def test_same_lesson_redraft_remains_latest_and_preserves_older_current_history(self):
        self.responses.append(response(0, 'FictionalCourtRedraft001', CURRENT_TITLE, '2026-10-14T12:00:00Z'))
        with self.io():
            found = self.discover()
        form = self.form(found)
        row = next(r for r in form['students'] if r['first'] == learner(0)['first'])
        self.assertEqual(self.ids([row]), {'FictionalCourtRedraft001'})
        self.assertEqual(self.ids(row['earlier']), {'FictionalCourtOriginal000'})
        self.assertEqual(self.ids(completion.recheck_candidates(form, now=NOW)),
                         {'FictionalCourtRedraft001'} | {'FictionalCourtOriginal%03d' % i for i in range(1, 7)})
        self.assertEqual(len(form['elsewhere']), 3)

    def test_late_same_lesson_recheck_preserves_original_on_time_policy(self):
        self.responses.append(response(0, 'FictionalCourtLateRedraft001', CURRENT_TITLE, '2026-10-29T12:00:00Z'))
        with self.io():
            found = self.discover()
        form = self.form(found)
        row = next(r for r in form['students'] if r['first'] == learner(0)['first'])
        self.assertEqual(self.ids([row]), {'FictionalCourtOriginal000'})
        self.assertFalse(row['late'])
        self.assertEqual(self.ids(row['earlier']), {'FictionalCourtLateRedraft001'})
        selected = next(r for r in completion.recheck_candidates(form, now=NOW) if r['first'] == row['first'])
        self.assertEqual(self.ids([selected]), {'FictionalCourtLateRedraft001'})
        self.assertTrue(selected['late'])
        self.assertEqual(self.ids(selected['earlier']), {'FictionalCourtOriginal000'})

    def assert_held_only_first(self, found):
        form = self.form(found)
        row = next(r for r in form['students'] if r['first'] == learner(0)['first'])
        self.assertTrue(row.get('selection_problem'), row)
        candidates = completion.recheck_candidates(form, now=NOW)
        held = next(r for r in candidates if r['first'] == row['first'])
        self.assertTrue(held.get('selection_problem'))
        self.assertEqual(sum(not r.get('selection_problem') for r in candidates), 6)
        self.assertFalse(found['other_files'])
        self.assertFalse(any(r.get('selection_problem') for r in candidates if r['first'] != row['first']))
        with patch.object(completion, 'score_file', return_value={'notes_score': 8, 'revision_score': 10}) as score:
            scored = app._score_rows('fictional-read', self.agenda, 'forensics', 'Wk 07', FORM, form, found)
        self.assertEqual(score.call_count, 6)
        values = morning.rows(form, scored, NOW)
        affected = next(r for r in values if r['first'] == row['first'])
        self.assertEqual(affected['kind'], 'check')
        self.assertIsNone(affected['notes'])
        self.assertIsNone(affected['revision'])
        return row

    def test_mixed_current_and_foreign_attachments_hold_only_that_student(self):
        self.responses = [r for r in self.responses if r['responseId'] != 'Response_FictionalHistoryOriginal000']
        self.responses.append(response(0, 'FictionalMixedCourt001', CURRENT_TITLE, '2026-10-15T12:00:00Z',
            extra=[('FictionalMixedHistory001', PRIOR_TITLE)]))
        before = deepcopy(self.responses)
        with self.io():
            found = self.discover()
        row = self.assert_held_only_first(found)
        retained = self.ids([row] + row.get('earlier', []))
        self.assertTrue({'FictionalMixedCourt001', 'FictionalMixedHistory001', 'FictionalCourtOriginal000'} <= retained)
        self.assertEqual(self.responses, before)

    def test_unknown_attachment_beside_foreign_lesson_cannot_route_the_whole_event(self):
        self.responses = [r for r in self.responses if r['responseId'] != 'Response_FictionalHistoryOriginal000']
        self.responses.append(response(0, 'FictionalHistoryUnknown001', PRIOR_TITLE, '2026-10-15T12:00:00Z',
            extra=[('FictionalUnknownAttachment001', 'Unclassified supporting material')]))
        with self.io():
            found = self.discover()
        row = self.assert_held_only_first(found)
        self.assertTrue({'FictionalHistoryUnknown001', 'FictionalUnknownAttachment001'} <=
                        self.ids([row] + row.get('earlier', [])))

    def test_two_verified_foreign_context_claims_remain_ambiguous(self):
        self.responses = [r for r in self.responses if r['responseId'] not in
                          ('Response_FictionalHistoryOriginal001', 'Response_FictionalHistoryOriginal002')]
        self.agenda.weeks.insert(0, {'label': 'Wk 05', 'monday': '2026-09-28'})
        self.agenda.classes['forensics']['layout']['rows'].insert(0, (5, 'Wk 05'))
        other = 'FictionalOtherHistoryTeacher001'
        self.agenda.rows['Wk 05'] = [self.agenda.lesson(PRIOR_TITLE, other, 5)]
        self.extra_metadata[other] = {'id': other, 'name': PRIOR_TITLE, 'mimeType': completion.SLIDES,
                                      'parents': [PRIOR_FOLDER]}
        declarations = json.loads(self.config['HUB_FORM_TEACHER_SOURCES'])
        declarations.append(binding('Wk 05', other, PRIOR_FOLDER))
        self.config['HUB_FORM_TEACHER_SOURCES'] = json.dumps(declarations)
        with self.io():
            found = self.discover()
        row = self.assert_held_only_first(found)
        self.assertIn('FictionalHistoryOriginal000', self.ids([row] + row.get('earlier', [])))

    def test_foreign_only_student_is_visible_and_never_an_absence_zero(self):
        self.responses = [r for r in self.responses if r['responseId'] != 'Response_FictionalCourtOriginal000']
        with self.io():
            found = self.discover()
        form = self.form(found)
        self.assertEqual(len(form['students']), 6)
        self.assertTrue(any(x['original_submission']['email'] == email(0) for x in form['elsewhere']))
        scored = [(r, {'notes_score': 8, 'revision_score': 10}) for r in form['students']]
        value = next(r for r in morning.rows(form, scored, NOW) if r['first'] == learner(0)['first'])
        self.assertEqual(value['kind'], 'check')
        self.assertIsNone(value['notes'])
        self.assertIsNone(value['revision'])

    def test_missing_current_binding_keeps_ordinary_shared_form_behavior(self):
        self.config['HUB_FORM_TEACHER_SOURCES'] = '[]'
        with self.io():
            found = self.discover()
        form = self.form(found)
        self.assertEqual(len(form['elsewhere']), 0)
        self.assertEqual(self.ids(form['students']), {'FictionalHistoryOriginal%03d' % i for i in range(3)} |
                         {'FictionalCourtOriginal%03d' % i for i in range(3, 7)})

    def test_unverified_current_or_foreign_teacher_never_enables_new_routing(self):
        for teacher in (CURRENT_TEACHER, PRIOR_TEACHER):
            for change in ({'version': None}, {'modifiedTime': None}, {'trashed': True},
                           {'mimeType': 'application/pdf'}, {'id': 'FictionalWrongSource001'}):
                self.metadata_changes = {teacher: change}
                with self.subTest(teacher=teacher, change=change), self.io():
                    found = self.discover()
                form = self.form(found)
                self.assertEqual(form['elsewhere'], [])
                self.assertIn('FictionalHistoryOriginal000', self.ids(form['students']))

    def test_same_or_overlapping_teacher_authority_does_not_split_one_assignment(self):
        declarations = json.loads(self.config['HUB_FORM_TEACHER_SOURCES'])
        declarations.append(binding('Wk 06', CURRENT_TEACHER, CURRENT_FOLDER))
        self.config['HUB_FORM_TEACHER_SOURCES'] = json.dumps(declarations)
        with self.io() as reads:
            found = self.discover()
        form = self.form(found)
        self.assertEqual(form['elsewhere'], [])
        self.assertEqual(len(form['students']), 7)
        self.assertEqual([c.args[1] for c in reads.call_args_list], [FORM])

    def test_foreign_context_from_another_school_year_cannot_partition_current_events(self):
        self.agenda.weeks[0]['monday'] = '2025-10-06'
        with self.io() as reads:
            found = self.discover()
        form = self.form(found)
        self.assertEqual(form['elsewhere'], [])
        self.assertIn('FictionalHistoryOriginal000', self.ids(form['students']))
        self.assertEqual([c.args[1] for c in reads.call_args_list], [FORM])

    def test_current_context_uses_one_registered_scope_and_foreign_direct_authority(self):
        contexts = morning._form_contexts(self.agenda, 'forensics')[FORM]
        current = morning._current_form_context(self.config, self.agenda, 'forensics', FORM,
                                                contexts, '2026-10-27')
        self.assertEqual(current['week']['label'], 'Wk 07')
        self.assertIsNone(work.form_teacher_sources(self.config, self.agenda, 'forensics', 'Wk 06', FORM))
        self.assertIsNone(completion.form_source_ownership(self.config, self.agenda, 'forensics', 'Wk 06', FORM))
        with self.io():
            direct, resolved = work._direct_routing_source('fictional-read', self.config, self.agenda,
                                                           'forensics', 'Wk 06', FORM)
        self.assertEqual(direct['source_ids'], [PRIOR_TEACHER])
        self.assertEqual([item['id'] for item in resolved['decks']], [PRIOR_TEACHER])
        self.assertNotIn('HUB_INTAKE_DECK_ASSIGNMENTS', self.config)
        self.assertNotIn('HUB_ASSIGNMENT_FORM_IDS', self.config)

    def test_two_registered_contexts_keep_existing_morning_context_hold(self):
        declarations = json.loads(self.config['HUB_FORM_TEACHER_SOURCES'])
        declarations.append(binding('Wk 06', PRIOR_TEACHER, PRIOR_FOLDER))
        self.config['HUB_FORM_TEACHER_SOURCES'] = json.dumps(declarations)
        contexts = morning._form_contexts(self.agenda, 'forensics')[FORM]
        with self.assertRaisesRegex(ValueError, 'one verified current Agenda teacher-source context'):
            morning._current_form_context(self.config, self.agenda, 'forensics', FORM, contexts, '2026-10-27')

    def test_failed_registered_foreign_binding_cannot_fall_back_to_direct_authority(self):
        declarations = json.loads(self.config['HUB_FORM_TEACHER_SOURCES'])
        declarations.append(binding('Wk 06', PRIOR_TEACHER, CURRENT_FOLDER))
        self.config['HUB_FORM_TEACHER_SOURCES'] = json.dumps(declarations)
        with self.io(), patch.object(work, '_direct_routing_source',
                                     side_effect=AssertionError('A failed registered binding cannot fall back')):
            found = self.discover()
        self.assertEqual(self.form(found)['elsewhere'], [])

    @staticmethod
    def target(column, resource, group):
        value = {'section': SECTION, 'column': column, 'section_id': '41', 'term_id': '5',
                 'year': '2027', 'school_id': '7', 'calendar_id': '8', 'structure_id': '9',
                 'assignment_id': resource, 'score_group': group, 'task_id': '1',
                 'label_code': 'FictionalF' if column == 'notes' else 'FictionalS'}
        value['url'] = ('https://school.example.test/campus/sis/control-center/classroom/curriculum/'
            'resource/%s/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/41/'
            'objectSectionID/%s/calendarID/8/structureID/9)?classroomSectionID=41&classroomTermID=5&'
            'year=2027&schoolID=7&calendarID=8&structureID=9' % (resource, resource))
        return value

    def test_real_make_list_resolves_current_context_and_prepares_exact_seven_without_ic_writes(self):
        self.config['HUB_IC_ASSIGNMENT_MAP'] = json.dumps([{'class_key': 'forensics', 'form_id': FORM,
            'targets': [self.target('notes', '101', '201'), self.target('revision', '102', '202')]}])
        loads = []
        def loader(token, config, agenda, cls, week, *, fresh):
            loads.append((cls, week, fresh))
            return self.discover()
        def score(token, agenda, cls, week, fid, file_id, found, **kwargs):
            self.assertTrue(file_id.startswith('FictionalCourtOriginal'), file_id)
            return {'notes_score': 8, 'revision_score': 10}
        with self.io() as reads, patch.object(morning.ledger, 'read', return_value={'configured': True}), \
                patch.object(morning.ledger, 'eastern_now', return_value='2026-10-27T08:00:00-04:00'), \
                patch.object(completion, 'score_file', side_effect=score) as scorer:
            result = morning.make_list('fictional-read', self.config, self.agenda, 'forensics',
                                       'Wk 07', FORM, loader, app._score_rows)
        self.assertEqual(result['code'], 'ready', result)
        self.assertEqual(loads, [('forensics', 'Wk 07', True)])
        self.assertEqual([call.args[1] for call in reads.call_args_list], [FORM])
        self.assertEqual(scorer.call_count, 7)
        self.assertEqual({call.args[5] for call in scorer.call_args_list},
                         {'FictionalCourtOriginal%03d' % i for i in range(7)})
        self.assertEqual(len(result['list']['rows']), 7)
        self.assertTrue(all(row['kind'] == 'score' and (row['notes'], row['revision']) == (8, 10)
                            for row in result['list']['rows']))
        self.assertEqual(len(result['targets']), 2)
        self.assertFalse(self.port.rows, 'Preparation alone creates no grading or IC receipt')

    def test_changed_foreign_source_after_initial_routing_holds_three_and_preserves_other_four(self):
        original_matcher = work.wrong_form_moves
        for change in ({'name': 'Changed fictional lesson title'}, {'version': '8'},
                       {'parents': ['FictionalChangedParent001']}, {'trashed': True}, 'unreadable'):
            self.metadata_changes = {}
            self.metadata_failures = set()
            def changed_after_selection(entries, held=None):
                moves = original_matcher(entries, held)
                if change == 'unreadable':
                    self.metadata_failures.add(PRIOR_TEACHER)
                else:
                    self.metadata_changes[PRIOR_TEACHER] = change
                return moves
            before = deepcopy(self.responses)
            with self.subTest(change=change), self.io(), \
                    patch.object(work, 'wrong_form_moves', side_effect=changed_after_selection):
                found = self.discover()
            form = self.form(found)
            self.assertEqual(form['elsewhere'], [], 'Unverified routes cannot remove originals')
            self.assertFalse(found['other_files'])
            held = [row for row in form['students'] if row.get('selection_problem')]
            self.assertEqual({row['first'] for row in held}, {learner(i)['first'] for i in range(3)})
            for row in held:
                self.assertEqual(len(self.ids([row] + row.get('earlier', []))), 2)
            with patch.object(completion, 'score_file', return_value={'notes_score': 8, 'revision_score': 10}) as scorer:
                scored = app._score_rows('fictional-read', self.agenda, 'forensics', 'Wk 07', FORM, form, found)
            self.assertEqual(scorer.call_count, 4)
            self.assertEqual({call.args[5] for call in scorer.call_args_list},
                             {'FictionalCourtOriginal%03d' % i for i in range(3, 7)})
            values = morning.rows(form, scored, NOW)
            self.assertEqual(sum(row['kind'] == 'check' for row in values), 3)
            self.assertEqual(sum(row['kind'] == 'score' for row in values), 4)
            self.assertFalse(any(row['kind'] == 'zero' for row in values))
            self.assertEqual(self.responses, before)


if __name__ == '__main__':
    unittest.main()
