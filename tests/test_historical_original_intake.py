"""Fictional exact historical events: no live IDs, students, stores or writers."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import io
import zipfile
import unittest
from unittest.mock import Mock, patch

from hub_v2 import completion, google, historical_intake as hi, morning, roster, saved_completion, work
from school_workflow_hub.core.runs import content_hash

AGENDA = 'FICTIONAL_AGENDA_1234567890'
LEGACY = 'FICTIONAL_OLD_FORM_1234567890'
FORMS = ['FICTIONAL_CURRENT_FORM_A_1234567890', 'FICTIONAL_CURRENT_FORM_B_1234567890']
TEACHERS = ['FICTIONAL_TEACHER_A_1234567890', 'FICTIONAL_TEACHER_B_1234567890']
FOLDERS = ['FICTIONAL_FOLDER_A_1234567890', 'FICTIONAL_FOLDER_B_1234567890']
WEEKS = ['Wk 03', 'Wk 05']
CYCLES = ['SYN03', 'SYN05']
NOW = datetime.now(timezone.utc)
STAMP = '2026-09-20T13:00:00Z'


class Agenda:
    spreadsheet_id = AGENDA
    classes = {'forensics': {'name': 'Forensics', 'layout': {'rows': [(13, WEEKS[0]), (15, WEEKS[1])]}}}
    weeks = [{'label': WEEKS[0], 'monday': '2026-09-14'}, {'label': WEEKS[1], 'monday': '2026-09-28'}]

    def __init__(self):
        self.rows = {week: [{'row': 13 + i * 2, 'topic': 'Synthetic exact lesson ' + str(i),
            'edit_url': 'https://fictional.example/agenda',
            'forms': [{'form_id': FORMS[i], 'label': 'Fictional lesson ' + str(i), 'url': 'https://fictional.example/form', 'problem': None}],
            'materials': [{'label': 'Exact source', 'url': 'https://docs.google.com/presentation/d/' + TEACHERS[i] + '/edit'}]}]
            for i, week in enumerate(WEEKS)}

    def lessons(self, cls, week):
        return deepcopy(self.rows.get(week, []))


def metadata(fid, parent):
    return {'id': fid, 'name': 'Unreliable filename', 'mimeType': completion.SLIDES,
            'trashed': False, 'version': '5', 'modifiedTime': STAMP, 'parents': [parent]}


def deck(cycle, teacher=False):
    return {'slides': [{'number': 1, 'units': [{'key': 'question-one', 'text': 'Exact first question for ' + cycle, 'box': None},
            {'key': 'response-one', 'text': '[[NOTES:' + cycle + ':ONE]]\n' + ('' if teacher else 'student words'), 'box': None}]},
        {'number': 2, 'units': [{'key': 'question-two', 'text': 'Exact second question for ' + cycle, 'box': None},
            {'key': 'response-two', 'text': '[[DRAFT:' + cycle + ':TWO]]\n' + ('' if teacher else 'original revision'), 'box': None}]}],
        'size': [120, 90]}


def questions(value):
    return [{'slide': s['number'], 'object_key': u['key'], 'text_sha256': content_hash(u['text'])}
            for s in value['slides'] for u in s['units'] if u['key'].startswith('question')]


class HistoricalIntakeTests(unittest.TestCase):
    def setUp(self):
        self.agenda = Agenda()
        self.config = {'HUB_ALLOWED_EMAILS': 'teacher@fictional.example', 'HUB_SCHOOL_EMAIL_DOMAINS': 'fictional.example',
                       'HUB_FORM_TEACHER_SOURCES': json.dumps([{'school_year': '2026-27', 'agenda_id': AGENDA,
                           'class_key': 'forensics', 'week': WEEKS[i], 'form_id': FORMS[i],
                           'source_folder_id': FOLDERS[i], 'teacher_ids': [TEACHERS[i]]} for i in range(2)])}
        self.definition = {'formId': LEGACY, 'revisionId': 'fictional-revision', 'items': [
            {'questionItem': {'question': {'questionId': 'upload', 'fileUploadQuestion': {'folderId': 'FICTIONAL_ORIGINAL_FOLDER_12345'}}}}]}
        self.responses, self.entries, self.students, self.people, self.decks, self.metadata = [], [], [], [], {}, {}
        for i in range(2):
            self.metadata[TEACHERS[i]] = metadata(TEACHERS[i], FOLDERS[i]); self.decks[TEACHERS[i]] = deck(CYCLES[i], True)
        for i in range(7):
            target = 0 if i < 2 else 1
            fid = 'FICTIONAL_STUDENT_FILE_%02d_12345' % i
            email = 'native.alias%d@fictional.example' % i  # Cannot be guessed from name.
            event = {'responseId': 'FICTIONAL_RESPONSE_%02d_12345' % i,
                     'respondentEmail': email, 'createTime': STAMP, 'lastSubmittedTime': STAMP,
                     'answers': {'upload': {'questionId': 'upload', 'fileUploadAnswers': {'answers': [
                         {'fileId': fid, 'fileName': 'Wrong-looking Week 99 name', 'mimeType': completion.SLIDES}]}}}}
            self.responses.append(event)
            self.metadata[fid] = metadata(fid, 'FICTIONAL_ORIGINAL_FOLDER_12345'); self.decks[fid] = deck(CYCLES[target])
            identity = {'school_email': email, 'local_student_number': str(3000 + i),
                        'membership_id': 'fictional-membership-%d' % i, 'section': 'A2 Forensics',
                        'native_email_proof_sha256': content_hash(['native school contact', i])}
            person = {**identity, 'class_key': 'forensics', 'school_year': '2026-27', 'active': True,
                      'first': 'Synthetic%d' % i, 'last': 'Person%d' % i}
            self.people.append(person)
            self.students.append({k: person[k] for k in ('class_key', 'section', 'first', 'last')})
            entry = {'event': {'response_id': event['responseId'], 'question_id': 'upload', 'email': email,
                              'created_at': STAMP, 'last_submitted_at': STAMP, 'sha256': content_hash(event)},
                     'identity': identity,
                     'original': {'metadata': self.metadata[fid], 'content_sha256': content_hash(self.decks[fid]),
                                  'questions': questions(self.decks[fid])},
                     'target': {'class_key': 'forensics', 'week': WEEKS[target], 'form_id': FORMS[target],
                                'teacher_id': TEACHERS[target], 'cycle': CYCLES[target],
                                'teacher': {'metadata': self.metadata[TEACHERS[target]],
                                            'content_sha256': content_hash(self.decks[TEACHERS[target]]),
                                            'questions': questions(self.decks[TEACHERS[target]])}}}
            entry['target']['scope'] = hi._target(self.config, self.agenda, entry)
            self.entries.append(entry)
        self.metadata[LEGACY] = {**metadata(LEGACY, 'FICTIONAL_FORM_PARENT_12345'), 'mimeType': 'application/vnd.google-apps.form'}
        self.value = {'schema': hi.SCHEMA, 'agenda_id': AGENDA, 'school_year': '2026-27', 'class_key': 'forensics',
                      'operator': 'teacher@fictional.example', 'instruction_sha256': content_hash('approved exact source decision'),
                      'decision_reference': 'https://fictional.example/existing-decision', 'verified_at': STAMP,
                      'source_form': {'form_id': LEGACY, 'metadata': self.metadata[LEGACY], 'definition_sha256': content_hash(self.definition),
                                      'responses_sha256': content_hash(self.responses)}, 'entries': self.entries}
        self.store()
        self.config_reader = Mock(side_effect=lambda: deepcopy(self.config))
        self.identity_reader = Mock(side_effect=self.read_identities)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(google, 'get_json', side_effect=lambda *a, **k: deepcopy(self.definition)))
        self.stack.enter_context(patch.object(google, 'form_responses', side_effect=lambda *a, **k: deepcopy(self.responses)))
        self.meta_reader = self.stack.enter_context(patch.object(google, 'drive_file', side_effect=lambda t, fid, *a: deepcopy(self.metadata[fid])))
        self.read = self.stack.enter_context(patch.object(completion, '_read', side_effect=lambda t, fid, md, **kw: deepcopy(self.decks[fid])))
        self.stack.enter_context(patch.object(completion, '_teacher_template', side_effect=lambda t, fid: (deepcopy(self.metadata[fid]), deepcopy(self.decks[fid]))))
        self.reload = self.stack.enter_context(patch.object(hi.agenda_module, 'load', side_effect=lambda *a: deepcopy(self.agenda)))

    def store(self):
        self.config[hi.CONFIG] = json.dumps(self.value)

    def read_identities(self, cls, year, emails):
        return {'source': 'infinite_campus', 'complete': True, 'checked_at': NOW.isoformat(),
                'canonical_receipt_reference': 'private-canonical-receipt', 'canonical_receipt_sha256': content_hash('current complete capture'),
                'identities': deepcopy([p for p in self.people if p['school_email'] in emails])}

    def forms(self, target=0):
        return [{'form_id': FORMS[target], 'found': [], 'problem': None, 'label': 'Fictional', 'responses': 0}]

    def run_attach(self, target=0, *, host=True, problems=()):
        forms = self.forms(target)
        if host:
            with hi.verified_host(read_config=self.config_reader, read_identities=self.identity_reader):
                reserved = hi.attach('test', self.config, self.agenda, 'forensics', WEEKS[target], forms, self.students, roster_problems=problems)
                hi.bookend(forms[0])
        else:
            reserved = hi.attach('test', self.config, self.agenda, 'forensics', WEEKS[target], forms, self.students)
        return forms[0], reserved

    def assert_held(self, target=0, **kwargs):
        form, reserved = self.run_attach(target, **kwargs)
        self.assertIn('Historical intake unverified', form['problem']); self.assertEqual(form['found'], [])
        self.assertEqual(len(reserved), 7)
        return form

    def test_exact_seven_events_are_split_two_and_five_preserving_event_and_native_identity(self):
        before = deepcopy(self.responses)
        for target, count in ((0, 2), (1, 5)):
            form, reserved = self.run_attach(target)
            self.assertIsNone(form['problem']); self.assertEqual(len(form['found']), count); self.assertEqual(len(reserved), 7)
            for sub in form['found']:
                file = sub['files'][0]
                self.assertEqual(sub['submitted_at'], STAMP); self.assertEqual(file['source_submitted_at'], STAMP)
                self.assertEqual(file['source_form_id'], LEGACY); self.assertEqual(file['assignment_form_id'], FORMS[target])
                self.assertEqual(sub['source'], 'form'); self.assertTrue(sub['event']); self.assertTrue(file['accepted_intake'])
                student, how = work.identify(sub, self.students, {'fictional.example'}, 's.')
                self.assertEqual(how, 'email'); self.assertIsNotNone(student)
                self.assertIsNone(roster.match_email(sub['email'], self.students))
                grouped, missing = work.group([sub], self.students, lesson_week=3)
                self.assertIsNone(grouped[0]['files'][0]['other_week'])
        self.assertEqual(self.responses, before)
        self.assertTrue(all(call.kwargs == {'source_role': 'student'} for call in self.read.call_args_list))

    def test_missing_host_never_uses_names_or_manifest_as_identity(self):
        self.assert_held(host=False)
        self.read.assert_not_called()

    def test_absent_config_is_exact_noop_without_readers(self):
        config = {k: v for k, v in self.config.items() if k != hi.CONFIG}
        self.assertEqual(hi.attach('test', config, self.agenda, 'forensics', WEEKS[0], self.forms(), self.students), set())
        self.meta_reader.assert_not_called()

    def test_partial_workbook_is_unavailable_not_missing_work(self):
        self.assert_held(problems=['partial sheet read'])
        self.read.assert_not_called()

    def test_incomplete_native_identity_is_held(self):
        self.identity_reader.side_effect = lambda *a: {**self.read_identities(*a), 'complete': False}
        self.assert_held()

    def test_stale_native_proof_is_held(self):
        self.identity_reader.side_effect = lambda *a: {**self.read_identities(*a), 'checked_at': (NOW - timedelta(hours=1)).isoformat()}
        self.assert_held()

    def test_changed_native_id_email_section_membership_or_proof_is_held(self):
        for field in ('local_student_number', 'school_email', 'section', 'membership_id', 'native_email_proof_sha256'):
            with self.subTest(field=field):
                old = self.people[0][field]; self.people[0][field] = 'different'
                self.assert_held(); self.people[0][field] = old

    def test_withdrawn_member_is_held(self):
        self.people[0]['active'] = False; self.assert_held()

    def test_duplicate_native_identity_is_held(self):
        self.people[1]['local_student_number'] = self.people[0]['local_student_number']; self.assert_held()

    def test_missing_renamed_or_duplicate_current_row_is_held(self):
        self.students.append(deepcopy(self.students[0])); self.assert_held()
        self.students.pop(); self.students[0]['last'] = 'Other'; self.assert_held()

    def test_incomplete_form_response_list_is_held_atomically(self):
        self.responses.pop(); self.assert_held()

    def test_new_unenumerated_response_is_not_a_blanket_legacy_acceptance(self):
        item = deepcopy(self.responses[0]); item['responseId'] += '_NEW'; self.responses.append(item)
        self.assert_held()

    def test_unenumerated_upload_in_an_approved_event_is_held(self):
        self.responses[0]['answers']['upload']['fileUploadAnswers']['answers'].append({'fileId': 'FICTIONAL_UNAPPROVED_UPLOAD'})
        self.value['source_form']['responses_sha256'] = content_hash(self.responses)
        self.entries[0]['event']['sha256'] = content_hash(self.responses[0]); self.store()
        self.assert_held()

    def test_changed_event_time_email_or_question_is_held(self):
        self.responses[0]['lastSubmittedTime'] = '2026-09-21T13:00:00Z'; self.assert_held()

    def test_partial_paginated_read_failure_never_becomes_empty(self):
        with patch.object(google, 'form_responses', side_effect=google.GoogleError('partial read', reason='incomplete_response_listing')):
            self.assert_held()

    def test_changed_original_revision_content_parent_or_trash_is_held(self):
        fid = self.entries[0]['original']['metadata']['id']
        # Manifest is a serialized immutable copy, separate from live state.
        for field, value in [('version', '6'), ('parents', ['FICTIONAL_DIFFERENT_FOLDER']), ('trashed', True)]:
            with self.subTest(field=field):
                old = self.metadata[fid][field]; self.metadata[fid][field] = value
                self.assert_held(); self.metadata[fid][field] = old
        self.decks[fid]['slides'][0]['units'][1]['text'] += ' changed'; self.assert_held()

    def test_changed_teacher_revision_or_wrong_registered_parent_is_held(self):
        self.metadata[TEACHERS[0]]['version'] = '9'; self.assert_held()

    def test_exact_question_object_must_match_even_with_new_manifest_content_hash(self):
        fid = self.entries[0]['original']['metadata']['id']
        self.decks[fid]['slides'][0]['units'][0]['key'] = 'moved-object'
        self.entries[0]['original']['content_sha256'] = content_hash(self.decks[fid]); self.store(); self.assert_held()

    def test_cycle_must_match_content_not_filename(self):
        self.entries[0]['target']['cycle'] = CYCLES[1]; self.store(); self.assert_held()

    def test_target_agenda_binding_change_is_held(self):
        self.agenda.rows[WEEKS[0]][0]['topic'] = 'Changed daily lesson'; self.assert_held()

    def test_config_changed_at_start_is_held(self):
        changed = dict(self.config, HUB_ASSIGNMENT_FORM_IDS='{}')
        self.config_reader.side_effect = lambda: changed; self.assert_held()

    def test_source_or_config_change_at_final_bookend_discards_all_plans(self):
        current = deepcopy(self.config); later = dict(current, changed='yes')
        self.config_reader.side_effect = [current, later]; self.assert_held()

    def test_identity_bookend_change_discards_all_plans(self):
        original = self.read_identities('forensics', '2026-27', [p['school_email'] for p in self.people[:2]])
        self.identity_reader.side_effect = [original, dict(original, canonical_receipt_sha256='f'*64)]
        self.assert_held()

    def test_after_discovery_bookend_keeps_problem_and_never_claims_scoring(self):
        forms = self.forms()
        with hi.verified_host(read_config=self.config_reader, read_identities=self.identity_reader):
            hi.attach('test', self.config, self.agenda, 'forensics', WEEKS[0], forms, self.students)
            self.assertEqual(len(forms[0]['found']), 2)
            self.metadata[TEACHERS[0]]['version'] = '6'
            hi.bookend(forms[0])
        self.assertIn('Historical intake unverified', forms[0]['problem'])
        self.assertNotIn('_historical_bookend', forms[0])
        self.assertNotIn('_historical_staged', forms[0])
        self.assertEqual(forms[0]['found'], [])

    def test_exact_bound_route_cannot_be_moved_by_filename_heuristics(self):
        form, _ = self.run_attach()
        entry = {'current': True, 'form': form, 'found': form['found'], 'base': {'wrong'}, 'words': {'wrong'}, 'week': WEEKS[0]}
        other = {'current': False, 'found': [], 'base': {'unreliable'}, 'words': {'unreliable'}, 'week': WEEKS[1],
                 'teacher_source_verified': True, 'teacher_source_binding': {'teacher_ids': TEACHERS[1:]},
                 'teacher_signature': {'unreliablefilename'}}
        self.assertEqual(work.wrong_form_moves([entry, other]), [])

    def test_same_file_also_in_current_form_is_held_and_originals_preserved(self):
        forms = self.forms(); forms[0]['found'] = work.submissions(self.responses[:1], FORMS[0])
        before = deepcopy(forms[0]['found'])
        with hi.verified_host(read_config=self.config_reader, read_identities=self.identity_reader):
            reserved = hi.attach('test', self.config, self.agenda, 'forensics', WEEKS[0], forms, self.students)
        self.assertIn('Historical intake unverified', forms[0]['problem']); self.assertEqual(forms[0]['found'], before)
        self.assertEqual(len(reserved), 7)

    def test_unrelated_form_not_held_by_other_target_identity_failure(self):
        self.identity_reader.side_effect = lambda *a: None
        unrelated = {'form_id': 'FICTIONAL_UNRELATED_FORM_12345', 'found': [], 'problem': None}
        forms = self.forms() + [unrelated]
        with hi.verified_host(read_config=self.config_reader, read_identities=self.identity_reader):
            hi.attach('test', self.config, self.agenda, 'forensics', WEEKS[0], forms, self.students)
        self.assertIsNone(unrelated['problem']); self.assertIn('unverified', forms[0]['problem'])

    def test_other_weeks_reserve_exact_files_without_injecting_or_claiming_coverage(self):
        forms = [{'form_id': 'FICTIONAL_OTHER_FORM_12345', 'found': [], 'problem': None}]
        self.assertEqual(len(hi.attach('test', self.config, self.agenda, 'forensics', 'Wk 07', forms, self.students)), 7)
        self.assertIsNone(forms[0]['problem']); self.identity_reader.assert_not_called()

    def test_invalid_manifest_duplicate_route_or_missing_decision_is_rejected(self):
        for change in ('duplicate', 'decision', 'operator'):
            with self.subTest(change=change):
                current = deepcopy(self.value)
                if change == 'duplicate': current['entries'].append(deepcopy(current['entries'][0]))
                elif change == 'decision': del current['decision_reference']
                else: current['operator'] = 'unapproved@fictional.example'
                with self.assertRaises(hi.Unverified): hi.manifest({**self.config, hi.CONFIG: json.dumps(current)})

    def discovery(self, *, host=True, current=None):
        forms = self.forms()
        if current is not None:
            forms[0]['found'] = work.submissions([current], FORMS[0])
            forms[0]['responses'] = 1
        forms[0].update(label='Fictional historical lesson', url='https://fictional.example/current', name='Fictional', folders=set())
        items = [deepcopy(self.metadata[e['original']['metadata']['id']]) for e in self.entries]
        with ExitStack() as scope:
            scope.enter_context(patch.object(google, 'service_account', return_value='fictional-reader'))
            scope.enter_context(patch.object(work, 'read_form', side_effect=lambda *a: deepcopy(forms[0])))
            scope.enter_context(patch.object(work, '_bind_form_teachers'))
            scope.enter_context(patch.object(work, '_metadata', side_effect=lambda t, ids: {fid: deepcopy(self.metadata[fid]) for fid in ids if fid in self.metadata}))
            scope.enter_context(patch.object(work, 'shared_submissions', return_value=([], None)))
            scope.enter_context(patch.object(work, 'lesson_folders', return_value=({}, None)))
            scope.enter_context(patch.object(work, 'list_tree', return_value=(items, {})))
            scope.enter_context(patch.object(work, 'nearby_forms', return_value=[]))
            scope.enter_context(patch('hub_v2.teacher_material.classify_entries', return_value=None))
            if host:
                scope.enter_context(hi.verified_host(read_config=self.config_reader, read_identities=self.identity_reader))
            return work.lesson_work('test', self.config, self.agenda, 'forensics', WEEKS[0],
                                    review_only=True, roster_snapshot=({'forensics': self.students}, []))

    def test_lesson_discovery_reuses_verified_events_before_folder_routing(self):
        result = self.discovery()
        form = result['lessons'][0]['forms'][0]
        self.assertIsNone(form['problem']); self.assertEqual(len(form['students']), 2)
        self.assertEqual(result['other_files'], []); self.assertEqual(form['folder_files'], 0)
        self.assertEqual(form['responses'], 0)  # Current Form count is not rewritten.
        self.assertEqual({f['id'] for row in form['students'] for f in row['files']},
                         {e['original']['metadata']['id'] for e in self.entries[:2]})

    def test_failed_historical_discovery_blocks_zero_and_score_preparation(self):
        result = self.discovery(host=False)
        form = result['lessons'][0]['forms'][0]
        self.assertIn('unverified', form['problem']); self.assertEqual(result['other_files'], [])
        scorer = Mock()
        answer = morning._make_list('test', self.config, self.agenda, 'forensics', WEEKS[0], FORMS[0],
                                    Mock(return_value=result), scorer)
        self.assertEqual(answer['code'], 'coverage_incomplete'); scorer.assert_not_called()

    def test_final_identity_failure_discards_historical_rows_but_preserves_current_event(self):
        current = deepcopy(self.responses[0])
        current['responseId'] = 'FICTIONAL_CURRENT_RESPONSE_12345'
        current['respondentEmail'] = 'synthetic0.person0@fictional.example'
        fid = 'FICTIONAL_CURRENT_ORIGINAL_12345'
        current['answers']['upload']['fileUploadAnswers']['answers'][0].update(fileId=fid, fileName='Current exact copy')
        self.metadata[fid] = metadata(fid, 'FICTIONAL_CURRENT_UPLOAD_FOLDER_12345')
        valid = self.read_identities('forensics', '2026-27', [p['school_email'] for p in self.people[:2]])
        changed = deepcopy(valid); changed['identities'][0]['active'] = False
        self.identity_reader.side_effect = [deepcopy(valid), deepcopy(valid), changed]
        result = self.discovery(current=current)
        form = result['lessons'][0]['forms'][0]
        self.assertIn('Historical intake unverified', form['problem'])
        self.assertEqual(form['responses'], 1)
        self.assertEqual(len(form['students']), 1)
        self.assertEqual(form['students'][0]['email'], current['respondentEmail'])
        self.assertEqual(form['students'][0]['files'][0]['id'], fid)
        self.assertEqual(form['students'][0]['earlier'], [])
        self.assertEqual(result['other_files'], [])  # All seven remain reserved.
        self.assertNotIn('native.alias', json.dumps(form['students']))
        scorer = Mock()
        answer = morning._make_list('test', self.config, self.agenda, 'forensics', WEEKS[0], FORMS[0],
                                    Mock(return_value=result), scorer)
        self.assertEqual(answer['code'], 'coverage_incomplete'); scorer.assert_not_called()

    def test_currently_registered_legacy_form_cannot_use_historical_exception(self):
        self.agenda.rows[WEEKS[1]][0]['forms'].append({'form_id': LEGACY, 'label': 'Current', 'url': 'current', 'problem': None})
        self.assert_held()

    def test_trashed_original_form_is_unavailable(self):
        self.metadata[LEGACY]['trashed'] = True; self.assert_held()

    def test_historical_config_is_in_saved_completion_fingerprint(self):
        self.assertIn(hi.CONFIG, saved_completion._POLICY_SETTINGS)


class FullSlideBoundaryTests(unittest.TestCase):
    def test_actual_201_slide_pptx_reader_truncation_never_authorizes_a_route(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            for number in range(1, 202):
                archive.writestr('ppt/slides/slide%d.xml' % number,
                    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'
                    '<p:sp><p:nvSpPr><p:cNvPr id="1" name="question"/></p:nvSpPr>'
                    '<p:txBody><a:p><a:r><a:t>' + ('Conflicting lesson at tail' if number == 201 else 'Earlier lesson') +
                    '</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
        raw = data.getvalue()
        self.assertEqual(len(completion.read_deck(raw)['slides']), completion.MAX_SLIDES)
        for role in ('student', 'teacher'):
            with self.subTest(role=role):
                fid = 'FICTIONAL_201_SLIDE_' + role.upper() + '_12345'
                md = {**metadata(fid, 'FICTIONAL_SOURCE_FOLDER_12345'), 'mimeType': google.PPTX}
                with patch.object(google, 'drive_file', return_value=md), patch.object(google, 'deck_bytes', return_value=raw):
                    with self.assertRaisesRegex(hi.Unverified, 'slide boundary'):
                        hi.source_capture('test', fid, role=role)


if __name__ == '__main__':
    unittest.main()
