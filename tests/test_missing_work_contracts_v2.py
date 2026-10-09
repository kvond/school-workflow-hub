"""Current supportive print plans on fictional IC/source/archive proofs."""
from copy import deepcopy
import json
import unittest

from hub_v2 import missing_work_contracts as contracts
from school_workflow_hub.core.runs import content_hash

STAMP = '2026-10-09T15:00:00Z'
ASSIGNMENT = 'biology:fictional-cycle-02'


def inputs():
    students = [{'first': first, 'last': 'Fictional', 'section': 'A4 Biology', 'active': True,
                 'ic_student_id': str(10 + n), 'start_date': '2026-09-01',
                 'assignment_states': {ASSIGNMENT: {'assigned': True, 'exempt': False}}}
                for n, first in enumerate(('Ana', 'Bo', 'Cal', 'Dee', 'Eli'))]
    students[2]['assignment_states'][ASSIGNMENT]['exempt'] = True
    students[3]['start_date'] = '2026-10-01'
    students[4]['assignment_states'][ASSIGNMENT].update({'extension_status': 'active', 'extension_due_at': '2026-10-12T23:00:00Z'})
    row = lambda student: {k: student[k] for k in ('first', 'last', 'section')}
    form = {'form_id': 'FictionalForm12345', 'label': 'Fictional ecology — Turn in', 'problem': None,
            'students': [{**row(students[0]), 'matched': True, 'files': [{'id': 'FictionalFile12345', 'trashed': False}], 'earlier': []}],
            'late': [], 'missing': [row(s) for s in students[1:]], 'excused': [], 'elsewhere': []}
    found = {'class_key': 'biology', 'week': 'Wk 02', 'lessons': [{'row': 7, 'topic': 'Fictional ecology', 'forms': [form]}],
             'problems': [], 'folder_note': None, 'assessment_problem': None, 'other_files': [], 'folder_reviews': [],
             'shared_form': {'problem': None}, 'roster_size': 5}
    roster = {'source': 'infinite_campus', 'class_key': 'biology', 'complete': True, 'expected_active_count': 5,
              'reference': 'fictional-roster-readback', 'checked_at': STAMP, 'students': students}
    source = {'reference': 'fictional-source-readback', 'checked_at': STAMP, 'complete': True,
              'found_sha256': content_hash(found), 'checks': {k: True for k in contracts.SOURCE_CHECKS}}
    due = {7: {'source': 'infinite_campus', 'reference': 'fictional-assignment-readback', 'revision': 'v1',
               'checked_at': STAMP, 'verified': True, 'binding_verified': True, 'row': 7,
               'assignment_key': ASSIGNMENT, 'title': 'Cycle 02 · Fictional ecology', 'form_ids': ['FictionalForm12345'],
               'due_date': '2026-09-16', 'opened_date': '2026-09-10'}}
    return found, {'roster_proof': roster, 'source_proof': source, 'due_proofs': due, 'checked_at': STAMP}


class MissingWorkContractsTests(unittest.TestCase):
    def plan(self, mutate=None, *, rebind=True):
        found, kwargs = inputs()
        if mutate:
            mutate(found, kwargs)
        if rebind:
            kwargs['source_proof']['found_sha256'] = content_hash(found)
        return contracts.derive_plan(found, **kwargs)

    def assertHeld(self, plan, text):
        self.assertEqual(plan['contracts'], [])
        self.assertEqual(plan['resolved'], [])
        self.assertEqual(len(plan['coverage_holds']), 1)
        self.assertIn(text, plan['coverage_holds'][0]['reason'])
        self.assertEqual(plan['coverage_holds'][0]['assignment_title'], 'Cycle 02 · Fictional ecology')
        self.assertEqual(plan['coverage_holds'][0]['class_key'], 'biology')

    def test_complete_current_scan_prints_only_missing_and_preserves_unknown_extension_and_grade_states(self):
        found, kwargs = inputs()
        before = deepcopy((found, kwargs))
        result = contracts.derive_plan(found, **kwargs)
        self.assertEqual((found, kwargs), before)
        self.assertEqual(result['coverage_holds'], [])
        self.assertEqual([r['student']['first'] for r in result['contracts']], ['Bo'])
        report = result['contracts'][0]
        self.assertEqual(report['report_type'], 'missing_work_contract')
        self.assertEqual(report['extension_status'], 'unknown')
        self.assertEqual(report['due_date'], '2026-09-16')
        self.assertEqual(report['prepared_at'], '2026-10-09')
        self.assertIn('not located', report['title'])
        self.assertIn('does not change a grade or deadline', report['note'])
        self.assertIn('Student signature', report['contract_lines'][-1])
        self.assertIsNone(report['understanding'])
        self.assertIsNone(report['explanation'])
        self.assertFalse(report['grade_change'])
        self.assertFalse(report['delivery_changed'])
        self.assertEqual(result['external_writes'], 0)
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(result['resolved'][0]['source_file_ids'], ['FictionalFile12345'])
        self.assertEqual({s['reason'] for s in result['skipped']}, {'exempt', 'new_join', 'active_extension'})
        self.assertNotIn('score', report)

    def test_late_or_earlier_verified_submission_closes_contract_without_using_grade(self):
        def mutate(found, kwargs):
            form = found['lessons'][0]['forms'][0]
            row = {**form['missing'].pop(0), 'matched': True, 'files': [],
                   'earlier': [{'files': [{'id': 'FictionalEarlier12345', 'trashed': False}]}]}
            form['late'].append(row)
        result = self.plan(mutate)
        self.assertEqual(result['contracts'], [])
        closure = next(r for r in result['resolved'] if r['student']['first'] == 'Bo')
        self.assertEqual(closure['source_file_ids'], ['FictionalEarlier12345'])

    def test_source_access_and_coverage_gaps_hold_once_not_one_todo_per_student(self):
        for field, value in (('problems', ['Form denied']), ('folder_note', 'Folder could not be opened'),
                             ('assessment_problem', 'Unresolved route'), ('other_files', [{'matched': True}])):
            with self.subTest(field=field):
                self.assertHeld(self.plan(lambda f, k: f.update({field: value})),
                                'source' if field != 'other_files' else 'lesson match')
        for key in contracts.SOURCE_CHECKS:
            with self.subTest(check=key):
                self.assertHeld(self.plan(lambda f, k: k['source_proof']['checks'].update({key: False})), 'coverage')

    def test_source_digest_and_form_assignment_binding_cannot_be_relabelled(self):
        self.assertHeld(self.plan(lambda f, k: f.update({'folder_files': 99}), rebind=False), 'exact source scan')
        self.assertHeld(self.plan(lambda f, k: k['due_proofs'][7].update({'form_ids': ['OtherFictionalForm12345']})), 'every intake Form')
        self.assertHeld(self.plan(lambda f, k: k['due_proofs'][7].update({'binding_verified': False})), 'exact IC assignment')

    def test_current_roster_is_canonical_hub_only_missing_rows_are_dropped_and_current_gaps_hold(self):
        def add_history(found, kwargs):
            found['lessons'][0]['forms'][0]['missing'].append({'first': 'Old', 'last': 'Student', 'section': 'A4 Biology'})
        result = self.plan(add_history)
        self.assertEqual([r['student']['first'] for r in result['contracts']], ['Bo'])
        self.assertHeld(self.plan(lambda f, k: f['lessons'][0]['forms'][0]['missing'].pop(0)), 'every current active IC student')
        self.assertHeld(self.plan(lambda f, k: k['roster_proof'].update({'expected_active_count': 6})), 'active Infinite Campus count')

    def test_exempt_flags_must_be_explicit_and_new_join_date_is_required_only_for_missing(self):
        self.assertHeld(self.plan(lambda f, k: k['roster_proof']['students'][1]['assignment_states'][ASSIGNMENT].pop('exempt')),
                        'Exempt flag')
        self.assertHeld(self.plan(lambda f, k: k['roster_proof']['students'][1].pop('start_date')), 'start date')
        result = self.plan(lambda f, k: k['roster_proof']['students'][0].pop('start_date'))
        self.assertEqual(len(result['contracts']), 1)

    def test_unmatched_held_elsewhere_pending_lesson_and_unavailable_uploads_are_not_absence(self):
        changes = [lambda f, k: f['lessons'][0]['forms'][0].update({'elsewhere': [{'who': 'Bo Fictional'}]}),
                   lambda f, k: f['lessons'][0]['forms'][0]['missing'][0].update({'unsorted': True}),
                   lambda f, k: f['lessons'][0]['forms'][0]['students'][0].update({'matched': False}),
                   lambda f, k: f['lessons'][0]['forms'][0]['students'][0]['files'][0].update({'problem': 'Unreadable'}),
                   lambda f, k: f['lessons'][0]['forms'][0]['students'][0]['files'][0].update({'trashed': True}),
                   lambda f, k: f['lessons'][0]['forms'][0]['students'][0].update({'files': []})]
        for change in changes:
            with self.subTest(change=change):
                result = self.plan(change)
                self.assertEqual(result['contracts'], [])
                self.assertEqual(len(result['coverage_holds']), 1)

    def test_print_only_exact_ic_name_fallback_requires_unique_verified_archive_key(self):
        def fallback(found, kwargs):
            student = kwargs['roster_proof']['students'][1]
            student.pop('ic_student_id')
            kwargs['archive_proof'] = {'class_key': 'biology', 'reference': 'fictional-private-folder-readback', 'checked_at': STAMP,
                'students': [{**{k: student[k] for k in ('first', 'last', 'section')},
                              'student_key': 'archivestudent_' + 'b' * 64, 'verified': True, 'identity_status': 'verified'}]}
        result = self.plan(fallback)
        self.assertEqual(len(result['contracts']), 1)
        self.assertEqual(result['contracts'][0]['student']['identity_basis'], 'current_ic_exact_name_section_and_verified_archive')
        self.assertIsNone(result['contracts'][0]['student']['ic_student_id'])
        self.assertHeld(self.plan(lambda f, k: k['roster_proof']['students'][1].pop('ic_student_id')), 'existing private student record')
        for change in ('duplicate', 'unverified', 'wrong_name', 'wrong_section'):
            def invalid(found, kwargs):
                fallback(found, kwargs)
                row = kwargs['archive_proof']['students'][0]
                if change == 'duplicate':
                    kwargs['archive_proof']['students'].append(deepcopy(row))
                elif change == 'unverified':
                    row['identity_status'] = 'identity_unverified'
                else:
                    row['first' if change == 'wrong_name' else 'section'] = 'Different'
            with self.subTest(change=change):
                self.assertEqual(self.plan(invalid)['contracts'], [])

    def test_routed_other_lesson_file_does_not_hide_current_located_work_and_known_missing_person_is_held(self):
        def located(found, kwargs):
            found['lessons'][0]['forms'][0]['elsewhere'] = [{'who': 'Fictional, Ana', 'section': 'A4 Biology',
                                                          'accepted_intake': False, 'to': 'Other lesson'}]
        result = self.plan(located)
        self.assertEqual(len(result['contracts']), 1)
        self.assertEqual(result['coverage_holds'], [])
        self.assertTrue(any(r['student']['first'] == 'Ana' for r in result['resolved']))
        def held(found, kwargs):
            found['lessons'][0]['forms'][0]['elsewhere'] = [{'who': 'Fictional, Bo', 'section': 'A4 Biology',
                                                          'accepted_intake': False, 'to': 'Other lesson'}]
        result = self.plan(held)
        self.assertEqual(result['contracts'], [])
        self.assertTrue(any(r['student']['first'] == 'Bo' and r['reason'] == 'held_elsewhere' for r in result['skipped']))

    def test_duplicate_ic_names_and_ids_hold_instead_of_filename_alias_matching(self):
        for field in ('name', 'id'):
            def change(found, kwargs):
                a, b = kwargs['roster_proof']['students'][:2]
                if field == 'name':
                    b.update({k: a[k] for k in ('first', 'last', 'section')})
                else:
                    b['ic_student_id'] = a['ic_student_id']
            self.assertHeld(self.plan(change), 'same student identity')

    def test_current_ic_proof_can_bind_private_name_filed_record_for_printing_without_promoting_old_identity(self):
        found, kwargs = inputs()
        student = kwargs['roster_proof']['students'][1]
        student.pop('ic_student_id')
        kwargs['archive_proof'] = {'class_key': 'biology', 'reference': 'fictional-current-private-record-readback',
            'checked_at': STAMP, 'students': [{**{k: student[k] for k in ('first', 'last', 'section')},
                'student_key': 'archivestudent_' + 'a' * 64, 'student_folder_id': 'FictionalFolder12345',
                'verified': True, 'identity_status': 'identity_unverified', 'private_folder_verified': True,
                'current_ic_name_match_verified': True}]}
        before = deepcopy(kwargs['archive_proof'])
        result = contracts.derive_plan(found, **kwargs)
        self.assertEqual(len(result['contracts']), 1)
        person = result['contracts'][0]['student']
        self.assertEqual(person['archive_identity_status'], 'identity_unverified')
        self.assertEqual(person['identity_basis'], 'current_ic_exact_name_section_and_verified_archive')
        self.assertEqual(kwargs['archive_proof'], before)
        kwargs['archive_proof']['students'][0]['private_folder_verified'] = False
        self.assertHeld(contracts.derive_plan(found, **kwargs), 'existing private student record')

    def test_blank_ic_start_date_needs_explicit_current_membership_flag_not_an_invented_date(self):
        def mutate(found, kwargs):
            student = kwargs['roster_proof']['students'][1]
            student['start_date'] = ''
            student['assignment_states'][ASSIGNMENT]['assigned_and_not_rostered_on_due_date_false'] = True
        result = self.plan(mutate)
        self.assertEqual(len(result['contracts']), 1)
        self.assertEqual(result['contracts'][0]['source_proof']['membership_basis'], 'current_ic_membership_on_due_flags')
        def excluded(found, kwargs):
            mutate(found, kwargs)
            kwargs['roster_proof']['students'][1]['assignment_states'][ASSIGNMENT]['not_rostered_on_due_date'] = True
        result = self.plan(excluded)
        self.assertEqual(result['contracts'], [])
        self.assertTrue(any(r['student']['first'] == 'Bo' and r['reason'] == 'new_join' for r in result['skipped']))

    def test_unknown_opening_date_uses_explicit_ic_membership_on_due_proof_without_inventing_open_date(self):
        def mutate(found, kwargs):
            kwargs['due_proofs'][7]['opened_date'] = None
            for student in kwargs['roster_proof']['students']:
                student['assignment_states'][ASSIGNMENT]['assigned_and_not_rostered_on_due_date_false'] = True
        result = self.plan(mutate)
        self.assertEqual(len(result['contracts']), 1)
        self.assertEqual(result['contracts'][0]['student']['first'], 'Bo')
        self.assertTrue(any(r['student']['first'] == 'Dee' and r['reason'] == 'new_join' for r in result['skipped']))
        def unproven(found, kwargs):
            mutate(found, kwargs)
            kwargs['roster_proof']['students'][1]['assignment_states'][ASSIGNMENT].pop('assigned_and_not_rostered_on_due_date_false')
        self.assertHeld(self.plan(unproven), 'membership-on-due flag')

    def test_each_day_reprints_current_open_contract_with_stable_closure_key_and_new_proof_id(self):
        first = self.plan()['contracts'][0]
        found, kwargs = inputs()
        tomorrow = '2026-10-10T15:00:00Z'
        kwargs['checked_at'] = tomorrow
        for proof in (kwargs['roster_proof'], kwargs['source_proof'], kwargs['due_proofs'][7]):
            proof['checked_at'] = tomorrow
        second = contracts.derive_plan(found, **kwargs)['contracts'][0]
        self.assertEqual(first['contract_key'], second['contract_key'])
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(second['prepared_at'], '2026-10-10')
        self.assertEqual(first['due_date'], second['due_date'])
        self.assertHeld(self.plan(lambda f, k: k.update({'checked_at': tomorrow})), 'fresh check today')

    def test_future_due_and_extension_skip_and_not_assessed_is_not_a_missing_source_row(self):
        result = self.plan(lambda f, k: k['due_proofs'][7].update({'due_date': '2026-10-14'}))
        self.assertEqual(result['contracts'], [])
        self.assertEqual(result['coverage_holds'], [])
        self.assertEqual(result['skipped'][0]['reason'], 'not_due')
        self.assertHeld(self.plan(lambda f, k: f['lessons'][0]['forms'][0].update({'missing': [], 'not_assessed': [{'first': 'Bo'}]})),
                        'every current active IC student')


if __name__ == '__main__':
    unittest.main()
