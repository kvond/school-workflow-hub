"""Exact private teacher bindings and case-only policy on fictional resources."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from hub_v2 import app, combined_assessment, completion, google, ledger, morning, review_context, work
from test_completion_readiness import source

AGENDA = 'FICTIONAL_AGENDA_12345'
FIRST = 'FICTIONAL_FIRST_FORM_12345'
SECOND = 'FICTIONAL_SECOND_FORM_12345'
CASE = 'FICTIONAL_CASE_FORM_12345'
SAFETY = 'FICTIONAL_SAFETY_FORM_12345'
FOLDER = 'FICTIONAL_SOURCE_FOLDER_12345'
TEACHER = 'FICTIONAL_EVIDENCE_TEACHER_12345'
ROW_TEACHER = 'FICTIONAL_ROW_TEACHER_12345'
STUDENT = 'FICTIONAL_STUDENT_COPY_12345'


class Agenda:
    spreadsheet_id = AGENDA
    title = 'Fictional Agenda'
    classes = {'forensics': {'name': 'Forensics', 'layout': {'rows': [(10, 'Wk 01'), (16, 'Wk 06')]}},
               'biology': {'name': 'Biology', 'layout': {'rows': [(10, 'Wk 01'), (16, 'Wk 06')]}}}
    weeks = [{'label': 'Wk 01', 'monday': '2026-08-31'}, {'label': 'Wk 06', 'monday': '2026-10-05'}]

    def __init__(self):
        self.rows = {'Wk 01': [{'topic': 'Work a case', 'materials': [], 'forms': [
            {'form_id': CASE, 'label': 'Work a case', 'url': 'case', 'problem': None},
            {'form_id': SAFETY, 'label': 'Lab Safety — Turn in', 'url': 'safety', 'problem': None}]}],
            'Wk 06': [{'topic': 'Searching and photography', 'materials': [{'label': 'Teacher deck',
                'url': 'https://docs.google.com/presentation/d/' + ROW_TEACHER + '/edit'}], 'forms': [
                {'form_id': FIRST, 'label': 'Searching the Scene — Turn in', 'url': 'first', 'problem': None},
                {'form_id': SECOND, 'label': 'Evidence Collection — Turn in', 'url': 'second', 'problem': None}]}]}
        for week, lessons in self.rows.items():
            for lesson in lessons:
                lesson.update(row=10 if week == 'Wk 01' else 16, edit_url='https://fictional.example/agenda')

    def lessons(self, cls, week):
        return deepcopy(self.rows.get(week, []))

    def week_options(self, cls):
        return self.weeks


def binding(**changes):
    return dict(school_year='2026-27', agenda_id=AGENDA, class_key='forensics', week='Wk 06',
                form_id=SECOND, source_folder_id=FOLDER, teacher_ids=[TEACHER], **changes)


def settings(entry=None):
    return {'HUB_FORM_TEACHER_SOURCES': json.dumps([entry or binding()])}


def excluded(**changes):
    entry = dict(school_year='2026-27', agenda_id=AGENDA, class_key='forensics', week='Wk 01',
                 form_id=CASE, purpose='week01_cases', reason='Teacher excludes only these case activities this year.')
    entry.update(changes)
    return {'HUB_ASSIGNMENT_EXCLUSIONS': json.dumps([entry])}


def metadata(fid):
    return {'id': fid, 'name': 'Evidence Collection' if fid == TEACHER else 'Source collection',
            'mimeType': google.FOLDER if fid == FOLDER else completion.SLIDES, 'trashed': False,
            'parents': [] if fid == FOLDER else [FOLDER], 'version': '4', 'modifiedTime': '2026-10-05T12:00:00Z'}


class FormSourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.agenda = Agenda()
        self.config = settings()

    def test_exact_form_and_year_binding_does_not_change_other_form_or_row(self):
        before = deepcopy(self.agenda.rows)
        self.assertEqual(work.form_teacher_sources(self.config, self.agenda, 'forensics', 'Wk 06', SECOND), binding())
        for cls, week, fid in [('forensics', 'Wk 06', FIRST), ('biology', 'Wk 06', SECOND),
                               ('forensics', 'Wk 01', SECOND)]:
            self.assertIsNone(work.form_teacher_sources(self.config, self.agenda, cls, week, fid))
        stale = binding(); stale['school_year'] = '2025-26'
        self.assertIsNone(work.form_teacher_sources(settings(stale), self.agenda, 'forensics', 'Wk 06', SECOND))
        self.assertEqual(self.agenda.rows, before)

    def test_invalid_duplicate_scope_source_or_current_form_never_accepts(self):
        for changes in ({'teacher_ids': [TEACHER, TEACHER]}, {'teacher_ids': [[]]}, {'source_folder_id': ''},
                        {'teacher_ids': []}, {'teacher_ids': ['not an ID']}, {'agenda_id': []}):
            entry = binding(); entry.update(changes)
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                work.form_teacher_sources(settings(entry), self.agenda, 'forensics', 'Wk 06', SECOND)
        double = {'HUB_FORM_TEACHER_SOURCES': json.dumps([binding(), binding()])}
        with self.assertRaises(ValueError):
            work.form_teacher_sources(double, self.agenda, 'forensics', 'Wk 06', SECOND)
        self.agenda.rows['Wk 06'][0]['forms'].append(deepcopy(self.agenda.rows['Wk 06'][0]['forms'][1]))
        with self.assertRaises(ValueError):
            work.form_teacher_sources(self.config, self.agenda, 'forensics', 'Wk 06', SECOND)

    def resolve(self, *, md=None, children=None):
        lesson = self.agenda.lessons('forensics', 'Wk 06')[0]
        with patch.object(google, 'drive_file', side_effect=md or (lambda t, fid, fields: metadata(fid))), \
             patch.object(google, 'folder_children', side_effect=children or (lambda t, fid: [metadata(TEACHER)])), \
             patch.object(google, 'deck_bytes', side_effect=AssertionError('metadata resolution never reads bytes')):
            return completion.lesson_sources('read', lesson, binding=binding())

    def test_exact_current_collection_and_teacher_metadata_bookends(self):
        result = self.resolve()
        self.assertIsNone(result['problem'])
        self.assertEqual([d['id'] for d in result['decks']], [TEACHER])
        self.assertEqual(result['binding'], binding())
        self.assertNotIn(ROW_TEACHER, str(result))

    def test_retired_missing_unknown_trash_wrong_parent_or_mime_source_holds(self):
        for change in ({'trashed': True}, {'trashed': None}, {'version': None}, {'parents': []},
                       {'mimeType': 'application/pdf'}, {'id': 'OTHER_TEACHER_12345'}):
            with self.subTest(change=change):
                result = self.resolve(md=lambda t, fid, fields: {**metadata(fid), **(change if fid == TEACHER else {})})
                self.assertTrue(result['problem'])
        self.assertIsNone(self.resolve(children=Mock(side_effect=AssertionError('no whole-root listing')))['problem'])
        self.assertTrue(self.resolve(md=lambda t, fid, fields: {**metadata(fid), **({'trashed': None} if fid == FOLDER else {})})['problem'])

    def test_folder_or_teacher_drift_holds_without_template_replacement(self):
        count = {}
        def drift(t, fid, fields):
            count[fid] = count.get(fid, 0) + 1
            return {**metadata(fid), 'version': '5' if fid == TEACHER and count[fid] > 1 else '4'}
        self.assertTrue(self.resolve(md=drift)['problem'])
        count.clear()
        def parent_drift(t, fid, fields):
            count[fid] = count.get(fid, 0) + 1
            return {**metadata(fid), 'modifiedTime': 'changed' if fid == FOLDER and count[fid] > 1 else metadata(fid)['modifiedTime']}
        self.assertTrue(self.resolve(md=parent_drift)['problem'])

    def test_omitted_parent_uses_only_exact_bounded_membership_with_bookends(self):
        def omitted(t, fid, fields):
            item = metadata(fid)
            if fid == TEACHER:
                item.pop('parents')
            return item
        with patch.object(google, 'get_json', return_value={'files': [metadata(TEACHER)]}) as read:
            result = self.resolve(md=omitted)
        self.assertIsNone(result['problem'])
        self.assertEqual(read.call_count, 2)
        for call in read.call_args_list:
            self.assertEqual(call.args[0], 'https://www.googleapis.com/drive/v3/files')
            self.assertEqual(call.args[2]['q'], "name = 'Evidence Collection' and mimeType = '%s' and '%s' in parents and trashed = false" % (completion.SLIDES, FOLDER))
            self.assertEqual(call.args[2]['pageSize'], 20)
        self.assertEqual(result['membership_evidence'][TEACHER], [metadata(TEACHER)])

    def test_omitted_parent_absent_foreign_changed_or_incomplete_membership_holds(self):
        def omitted(t, fid, fields):
            item = metadata(fid)
            if fid == TEACHER:
                item.pop('parents')
            return item
        valid = {'files': [metadata(TEACHER)]}
        for page in ({'files': []}, {'files': [{**metadata(TEACHER), 'id': ROW_TEACHER}]},
                     {'files': [{**metadata(TEACHER), 'parents': ['FICTIONAL_FOREIGN_FOLDER_12345']}]},
                     {'files': [metadata(TEACHER), metadata(TEACHER)]},
                     {**valid, 'nextPageToken': 'more'}, {**valid, 'incompleteSearch': True},
                     {'files': [{**metadata(TEACHER), 'version': 'changed'}]}):
            with self.subTest(page=page), patch.object(google, 'get_json', return_value=page):
                self.assertTrue(self.resolve(md=omitted)['problem'])
        with patch.object(google, 'get_json', side_effect=[valid, {'files': []}]):
            self.assertTrue(self.resolve(md=omitted)['problem'])
        with patch.object(google, 'get_json', side_effect=AssertionError('explicit foreign parent cannot use fallback')) as query:
            self.assertTrue(self.resolve(md=lambda t, fid, fields: {**metadata(fid), **({'parents': ['FICTIONAL_FOREIGN_FOLDER_12345']} if fid == TEACHER else {})})['problem'])
        query.assert_not_called()

    def test_readiness_rejects_teacher_change_during_actual_template_consumption(self):
        for change in ({'version': '5'}, {'parents': ['FICTIONAL_FOREIGN_FOLDER_12345']}):
            changed = {'consumed': False}
            def md(t, fid, fields):
                return {**metadata(fid), **(change if fid == TEACHER and changed['consumed'] else {})}
            def consume(t, fid):
                changed['consumed'] = True
                return metadata(fid), source()
            with self.subTest(change=change), patch.object(google, 'drive_file', side_effect=md), \
                 patch.object(completion, '_teacher_template', side_effect=consume), \
                 patch.object(completion, 'assess', side_effect=AssertionError('readiness never scores')):
                result = completion.assignment_readiness('read', self.config, self.agenda, 'forensics', 'Wk 06', SECOND)
            self.assertEqual(result['status'], 'NOT_READY')
            self.assertIn('registered teacher', result['problem'])

    def test_discovery_binds_second_without_giving_it_first_row_teachers(self):
        forms = [{'form_id': FIRST, 'label': 'Searching the Scene', 'name': '', 'signature': set()},
                 {'form_id': SECOND, 'label': 'Evidence Collection', 'name': '', 'signature': set()}]
        with patch.object(google, 'drive_file', side_effect=lambda t, fid, fields: metadata(fid)), \
             patch.object(google, 'folder_children', return_value=[metadata(TEACHER)]):
            work._bind_form_teachers('read', self.config, self.agenda, 'forensics', 'Wk 06', forms)
        work.pair_decks(forms, ['searching the scene'], ['Searching the Scene'])
        self.assertEqual(forms[0]['teacher_signature'], {'searching the scene'})
        self.assertEqual(forms[1]['teacher_signature'], {work.deck_stem('Evidence Collection')})
        self.assertEqual(work.assignment_for_submission({'files': [{'name': 'Copy of Evidence Collection - Fictional learner'}]}, forms), 1)

    def score(self, *, teachers=None, student_deck=None, teacher_change=None):
        teacher_ids = teachers or [TEACHER]
        b = binding(); b['teacher_ids'] = teacher_ids
        row = {'matched': True, 'first': 'Ann', 'last': 'Avery', 'section': 'A2 Forensics',
               'files': [{'id': STUDENT, 'mime': completion.SLIDES}], 'earlier': []}
        form = {'form_id': SECOND, 'students': [row], 'late': [], 'teacher_source_binding': b,
                'grade_readiness': {'status': 'READY', 'source_resolution': {'decks': [{'id': i} for i in teacher_ids]}}}
        found = {'lessons': [{'forms': [form]}]}
        before = deepcopy(found)
        consumed = {'teacher': False}
        def teacher(t, fid):
            consumed['teacher'] = True
            template = source()
            template['slides'][0]['units'][0]['text'] = template['slides'][0]['units'][0]['text'].replace('NOTES=1', 'NOTES=2').replace('NOTES_IDS=Q-MASTERY', 'NOTES_IDS=Q-MASTERY,Q-EXTRA')
            template['slides'].append({'number': 3, 'units': [
                {'key': 'extra', 'text': '[[NOTES:F06:Q-EXTRA]]', 'box': (0, 0, 10, 10), 'title': False},
                {'key': 'extra-answer', 'text': '', 'box': (0, 80, 800, 100), 'title': False}]})
            return metadata(fid), template
        with patch.object(completion, '_teacher_template', side_effect=teacher) as reads, \
             patch.object(google, 'drive_file', side_effect=lambda t, fid, fields: {**metadata(fid), **(teacher_change or {} if fid in teacher_ids and consumed['teacher'] else {})}), \
             patch.object(completion, '_read', return_value=student_deck or source('An observation gives visible evidence.')), \
             patch.object(completion, '_siblings', side_effect=AssertionError('no borrowed sibling teacher')):
            result = completion.score_file('read', self.agenda, 'forensics', 'Wk 06', SECOND, STUDENT, found)
        self.assertEqual(found, before)
        return result, reads

    def test_score_uses_registered_teacher_and_own_coded_component_counts(self):
        result, reads = self.score()
        self.assertIsNone(result['problem'])
        self.assertEqual(result['template_id'], TEACHER)
        self.assertEqual([c.args[1] for c in reads.call_args_list], [TEACHER])
        self.assertEqual(result['counts']['NOTES'], {'done': 1, 'required': 1})
        self.assertEqual(result['counts']['DRAFT'], {'done': 0, 'required': 1})
        self.assertEqual((result['notes_score'], result['revision_score']), (10, 0))

    def test_score_rechecks_cached_binding_after_teacher_bytes_before_assessment(self):
        for change in ({'version': '5'}, {'parents': ['FICTIONAL_FOREIGN_FOLDER_12345']}):
            with self.subTest(change=change), patch.object(completion, 'assess') as assess:
                result, _ = self.score(teacher_change=change)
            self.assertEqual(result['code'], 'deck_not_ready')
            self.assertIn('registered teacher', result['problem'])
            assess.assert_not_called()

    def test_foreign_codes_or_two_indistinguishable_registered_teachers_hold(self):
        foreign = source('Meaningful response')
        for slide in foreign['slides']:
            for obj in slide['units']:
                obj['text'] = obj['text'].replace('F06', 'DIFFERENT')
        result, _ = self.score(student_deck=foreign)
        self.assertEqual(result['code'], 'deck_not_ready')
        result, _ = self.score(teachers=[TEACHER, 'FICTIONAL_SECOND_TEACHER_12345'])
        self.assertEqual(result['code'], 'deck_not_ready')
        self.assertIn('uniquely', result['problem'])

    def test_combined_first_uses_its_exact_binding_after_other_form_row_link_changes(self):
        photo = 'FICTIONAL_PHOTOGRAPHY_TEACHER_12345'
        declared = binding(); declared.update(form_id=FIRST, teacher_ids=[ROW_TEACHER, photo])
        combined = {'school_year': '2026-27', 'agenda_id': AGENDA, 'class_key': 'forensics', 'week': 'Wk 06',
                    'form_id': FIRST, 'lesson_folder_id': FOLDER, 'components': [
                        {'cycle': 'F06', 'teacher_id': ROW_TEACHER, 'label': 'Searching'},
                        {'cycle': 'F06b', 'teacher_id': photo, 'label': 'Photography'}]}
        config = settings(declared); config[combined_assessment.CONFIG] = json.dumps([combined])
        # The shared row can display SECOND's different teacher without
        # silently dropping FIRST's independently declared Photography source.
        self.agenda.rows['Wk 06'][0]['materials'].append({'label': '2nd deck',
            'url': 'https://docs.google.com/presentation/d/' + TEACHER + '/edit'})
        self.assertEqual(combined_assessment.definition(config, self.agenda, 'forensics', 'Wk 06', FIRST), combined)
        for changes in ({'teacher_ids': [ROW_TEACHER, TEACHER]}, {'source_folder_id': 'FICTIONAL_WRONG_SOURCE_FOLDER_12345'}):
            changed = deepcopy(declared); changed.update(changes); config.update(settings(changed))
            with self.subTest(changes=changes), self.assertRaises(combined_assessment.Held):
                combined_assessment.definition(config, self.agenda, 'forensics', 'Wk 06', FIRST)

    def test_review_sources_and_cached_template_follow_exact_binding(self):
        scope = review_context.scope(self.agenda, self.config, 'forensics', 'Wk 06', SECOND)
        ids = {s['file_id'] for s in scope['sources']}
        self.assertEqual(ids, {TEACHER, FOLDER, SECOND})
        row = {'matched': True, 'first': 'Ann', 'last': 'Avery', 'section': 'A2 Forensics', 'files': [{'id': STUDENT}]}
        old = {('FICTIONAL_STUDENT_COPY_12345', metadata(STUDENT)['modifiedTime'], '4'): {
            'template': (ROW_TEACHER, metadata(ROW_TEACHER)['modifiedTime'], '4'), 'student_coding_required': False,
            'result': {'note': 'Slide 2: Previous question check'}}}
        with patch.dict(completion._results, old, clear=True), \
             patch.object(google, 'drive_file', side_effect=lambda t, fid, fields: metadata(fid)), \
             patch.object(google, 'folder_children', return_value=[metadata(TEACHER)]), \
             patch.object(completion, 'assess', side_effect=AssertionError('no scoring on review')):
            results, limitations = review_context.cached_reviews('read', self.agenda, 'forensics', 'Wk 06', SECOND, [row], config=self.config)
        self.assertEqual(results, {})
        self.assertTrue(limitations)


class CaseOnlyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.agenda = Agenda()
        self.config = excluded()

    def test_exact_case_exclusion_never_excludes_lab_safety_other_class_or_year(self):
        self.assertTrue(work.assignment_policy(self.config, self.agenda, 'forensics', 'Wk 01', CASE)['excluded'])
        for cls, week, fid in [('forensics', 'Wk 01', SAFETY), ('biology', 'Wk 01', CASE), ('forensics', 'Wk 06', CASE)]:
            self.assertFalse(work.assignment_policy(self.config, self.agenda, cls, week, fid)['excluded'])
        self.assertFalse(work.assignment_policy(excluded(school_year='2025-26'), self.agenda, 'forensics', 'Wk 01', CASE)['excluded'])
        for config in (excluded(form_id=SAFETY), excluded(purpose='all_week01')):
            self.assertTrue(work.assignment_policy(config, self.agenda, 'forensics', 'Wk 01', json.loads(config['HUB_ASSIGNMENT_EXCLUSIONS'])[0]['form_id'])['problem'])
        self.agenda.rows['Wk 01'][0]['forms'][0]['label'] = 'Lab Safety — Sheppard example'
        self.assertTrue(work.assignment_policy(self.config, self.agenda, 'forensics', 'Wk 01', CASE)['problem'])

    def test_unexcluded_legacy_policy_shape_preserves_receipt_checks_and_case_guard(self):
        # An unexcluded policy has no hold to propagate; the ordinary route
        # keeps its source/readiness checks, while exact private exclusions
        # still enforce their separately validated current Form scope.
        with patch.object(work, 'week_policy', return_value={'excluded': False}):
            self.assertEqual(work.assignment_policy({}, self.agenda, 'forensics', 'Wk 01', SAFETY),
                             {'excluded': False})
            self.assertTrue(work.assignment_policy(self.config, self.agenda, 'forensics', 'Wk 01', CASE)['excluded'])
            self.assertTrue(work.assignment_policy(excluded(form_id=SAFETY), self.agenda,
                                                  'forensics', 'Wk 01', SAFETY)['problem'])

    def test_case_skip_stops_morning_before_discovery_readiness_or_scoring(self):
        load, score = Mock(side_effect=AssertionError('no source discovery')), Mock(side_effect=AssertionError('no scoring'))
        with patch.object(completion, 'assignment_readiness', side_effect=AssertionError('no teacher reads')):
            result = morning.make_list('read', self.config, self.agenda, 'forensics', 'Wk 01', CASE, load, score)
        self.assertEqual(result['code'], 'excluded_week')
        load.assert_not_called(); score.assert_not_called()
        self.assertEqual(completion.assignment_readiness('read', self.config, self.agenda, 'forensics', 'Wk 01', CASE)['code'], 'excluded_week')

    def test_direct_score_and_manual_stamp_cannot_bypass_case_policy(self):
        query = {'class': ['forensics'], 'week': ['Wk 01'], 'form': [CASE], 'file': [STUDENT]}
        with patch.object(google, 'service_token', return_value='read'), patch.object(app, 'load_agenda', return_value=self.agenda), \
             patch.object(app, 'lesson_work', side_effect=AssertionError('no source discovery')):
            self.assertEqual(app.api('api/score', query, self.config)['code'], 'excluded_week')
        payload = {'class': 'forensics', 'week': 'Wk 01', 'form': CASE, 'kind': 'ic'}
        with patch.object(app, '_same_origin_json', return_value=None), patch.object(app, '_json_body', return_value=payload), \
             patch.object(google, 'service_token', return_value='read') as tokens, patch.object(app, 'load_agenda', return_value=self.agenda), \
             patch.object(app, '_form_context', side_effect=AssertionError('no source discovery')), patch.object(ledger, 'record') as record:
            result = app.record_post({}, lambda status, body: (status, body), self.config)
        self.assertEqual((result[0], result[1]['code']), ('409 Conflict', 'excluded_week'))
        self.assertEqual(tokens.call_count, 1)
        record.assert_not_called()

    def test_review_is_nonaction_only_when_exact_case_policy_applies(self):
        scope = review_context.scope(self.agenda, self.config, 'forensics', 'Wk 01', CASE)
        found = {'class_key': 'forensics', 'week': 'Wk 01', 'lessons': [{'forms': [{'form_id': CASE, 'students': [], 'late': []}]}]}
        result = review_context.build(scope, found)
        self.assertEqual((result['status'], result['notices']), ('excluded', []))
        self.assertNotIn('does not certify', result['limitations'][0])
        self.assertNotEqual(review_context.scope(self.agenda, self.config, 'forensics', 'Wk 01', SAFETY)['grade_policy']['excluded'], True)


if __name__ == '__main__':
    unittest.main()
