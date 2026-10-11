"""Fictional two-deck Form; no provider calls, student records or school IDs."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hub_v2 import app, combined_assessment as combined, completion, google, morning, native_feedback, work
from test_completion_readiness import source, HEADER
from test_hub_v2 import pptx

FORM = 'FICTIONAL_COMBINED_FORM_12345'
OTHER = 'FICTIONAL_OTHER_FORM_12345'
AGENDA = 'FICTIONAL_AGENDA_12345'
FOLDER = 'FICTIONAL_LESSON_FOLDER_12345'
T1, T2 = 'FICTIONAL_TEACHER_A_12345', 'FICTIONAL_TEACHER_B_12345'
S1, S2, S3 = 'FICTIONAL_COPY_A_12345', 'FICTIONAL_COPY_B_12345', 'FICTIONAL_LATER_A_12345'
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def declaration():
    return {'school_year': '2026-27', 'agenda_id': AGENDA, 'class_key': 'forensics', 'week': 'Wk 06',
            'form_id': FORM, 'lesson_folder_id': FOLDER, 'components': [
                {'cycle': 'F06a', 'teacher_id': T1, 'label': 'Photography'},
                {'cycle': 'F06b', 'teacher_id': T2, 'label': 'Searching'}]}


def agenda():
    lesson = {'forms': [{'form_id': FORM}], 'materials': [
        {'label': 'Lesson folder', 'url': 'https://drive.google.com/drive/folders/' + FOLDER},
        {'label': 'Teacher deck', 'url': 'https://docs.google.com/presentation/d/' + T1 + '/edit'},
        {'label': 'Teacher deck', 'url': 'https://docs.google.com/presentation/d/' + T2 + '/edit'}]}
    return SimpleNamespace(spreadsheet_id=AGENDA,
        classes={'forensics': {'name': 'Fictional Forensics'}, 'biology': {'name': 'Fictional Biology'}},
        weeks=[{'label': 'Wk 06', 'monday': '2026-09-28'}], lessons=lambda cls, week: [deepcopy(lesson)])


def event(fid, at='2026-10-01T12:00:00Z', **changes):
    row = {'matched': True, 'first': 'Ann', 'last': 'Avery', 'section': 'A2 Fictional Forensics',
           'matched_by': 'email', 'email': 's.ann.avery@fictional.example', 'source': 'form',
           'submitted_at': at, 'late': False,
           'files': [{'id': fid, 'name': 'No useful filename', 'mime': google.PPTX,
                      'source_form_id': FORM, 'source_response_id': 'RESPONSE_' + fid,
                      'source_submitted_at': at, 'from_folder': False, 'from_shared': False,
                      'trashed': False, 'problem': None, 'other_week': None}], 'earlier': []}
    row.update(changes)
    return row


def form():
    row = event(S1, '2026-10-02T12:00:00Z')
    row['earlier'] = [event(S2)]
    return {'form_id': FORM, 'label': 'Searching and Photography', 'students': [row], 'late': [],
            'missing': [], 'excused': [], 'elsewhere': [], 'window': {'at': '2026-10-14T23:59:59Z'}}


def deck(cycle, answer='', revised=''):
    value = source(answer, header=HEADER.replace('F06', cycle))
    for slide in value['slides']:
        for unit in slide['units']:
            unit['text'] = unit['text'].replace(':F06:', ':' + cycle + ':')
    value['slides'][1]['units'][4]['text'] = revised
    return value


def capture(fid, cycle, answer='', revised=''):
    value = deck(cycle, answer, revised)
    return {'metadata': {'id': fid, 'name': 'No title inference', 'mimeType': google.PPTX, 'trashed': False,
                         'version': '1', 'modifiedTime': '2026-10-01T00:00:00Z'},
            'sha256': combined.digest(value), 'deck': value}


def captures():
    return {T1: capture(T1, 'F06a'), T2: capture(T2, 'F06b'),
            S1: capture(S1, 'F06a', 'My own incorrect but meaningful answer.'),
            S2: capture(S2, 'F06b', '', 'My independent revision.'),
            S3: capture(S3, 'F06a', '', 'My new revision.')}


def sources():
    return {fid: {'cycle': cycle, 'basis': combined.source_basis(captures()[fid])}
            for fid, cycle in ((S1, 'F06a'), (S2, 'F06b'), (S3, 'F06a'))}


class CombinedTests(unittest.TestCase):
    def bind(self, value=None):
        value = value or form()
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(captures()[fid])):
            combined.bind_form('read-only', value, declaration())
        return value

    def test_registry_requires_exact_agenda_form_folder_teachers_and_unique_cycles(self):
        config = {combined.CONFIG: json.dumps([declaration()])}
        self.assertEqual(combined.definition(config, agenda(), 'forensics', 'Wk 06', FORM), declaration())
        self.assertIsNone(combined.definition(config, agenda(), 'forensics', 'Wk 06', OTHER))
        for change in ({'lesson_folder_id': 'WRONG_FOLDER_12345'},
                       {'components': [declaration()['components'][0]] * 2},
                       {'components': [dict(declaration()['components'][0], teacher_id='UNLINKED_TEACHER_12345'), declaration()['components'][1]]},
                       {'class_key': 'biology'}, {'extra': 'not accepted'}):
            with self.subTest(change=change), self.assertRaises(combined.Held):
                combined.definition({combined.CONFIG: json.dumps([{**declaration(), **change}])}, agenda(), 'forensics', 'Wk 06', FORM)
        stale = dict(declaration(), school_year='2025-26')
        self.assertIsNone(combined.definition({combined.CONFIG: json.dumps([stale])}, agenda(), 'forensics', 'Wk 06', FORM))

    def test_different_cycle_events_are_retained_and_later_revision_changes_only_its_cycle(self):
        value = form(); original = deepcopy(value)
        selected = combined.select_rows(value, declaration(), sources(), now=NOW)[0]
        self.assertEqual([c['file_id'] for c in selected['combined_components']], [S1, S2])
        value['students'][0]['earlier'].append(event(S3, '2026-10-03T12:00:00Z'))
        selected = combined.select_rows(value, declaration(), sources(), now=NOW)[0]
        self.assertEqual([c['file_id'] for c in selected['combined_components']], [S3, S2])
        self.assertEqual(original, form())
        self.assertEqual(len(value['students'][0]['earlier']), 2)

    def test_late_candidate_requires_the_existing_explicit_final_entry_recheck(self):
        value = form(); value['window']['at'] = '2026-10-02T23:59:59Z'
        value['students'][0]['earlier'].append(event(S3, '2026-10-03T12:00:00Z', late=True))
        on_time = combined.select_rows(value, declaration(), sources(), now=NOW)[0]
        current = combined.select_rows(value, declaration(), sources(), now=NOW, include_late=True)[0]
        self.assertEqual(on_time['files'][0]['id'], S1)
        self.assertEqual(current['files'][0]['id'], S3)

    def test_repeated_same_file_receipts_cannot_duplicate_a_component(self):
        value = form(); value['students'][0]['earlier'].append(event(S1))
        selected = combined.select_rows(value, declaration(), sources(), now=NOW)[0]
        self.assertEqual(len(selected['combined_components']), 2)

    def test_wrong_form_folder_identity_future_and_empty_history_all_hold(self):
        variants = []
        for key, val in [('source_form_id', OTHER), ('from_folder', True), ('from_shared', True),
                         ('source_response_id', ''), ('trashed', True), ('problem', 'Unavailable')]:
            value = form(); value['students'][0]['earlier'][0]['files'][0][key] = val; variants.append(value)
        for key, val in [('matched_by', 'file name'), ('source', 'folder'), ('email', 's.someone.else@fictional.example'),
                         ('files', [])]:
            value = form(); value['students'][0]['earlier'][0][key] = val; variants.append(value)
        future = form(); future['students'][0]['earlier'].append(event(S3, '9999-10-01T00:00:00Z')); variants.append(future)
        for value in variants:
            with self.subTest(value=value):
                self.assertIn('selection_problem', combined.select_rows(value, declaration(), sources(), now=NOW)[0])

    def test_equal_latest_different_files_and_shared_identity_hold(self):
        value = form(); value['students'][0]['earlier'].append(event(S3, value['students'][0]['submitted_at']))
        self.assertIn('same latest', combined.select_rows(value, declaration(), sources(), now=NOW)[0]['selection_problem'])
        value = form(); other = event(S1, email='s.other.person@fictional.example', last='Other', first='Person')
        value['students'].append(other)
        self.assertTrue(all('different students' in r['selection_problem']
                            for r in combined.select_rows(value, declaration(), sources(), now=NOW)))

    def test_classification_uses_actual_cycles_not_names_and_rejects_mixed_unknown(self):
        teachers = {'F06a': captures()[T1], 'F06b': captures()[T2]}
        self.assertEqual(combined.classify(captures()[S1], teachers), 'F06a')
        wrong = capture(S1, 'F07')
        with self.assertRaises(combined.Held): combined.classify(wrong, teachers)
        mixed = captures()[S1]; mixed['deck']['slides'][1]['units'][-1]['text'] = '[[DRAFT:F06b:Q-MASTERY]]'
        with self.assertRaises(combined.Held): combined.classify(mixed, teachers)

    def test_pooled_counts_are_one_score_not_max_or_average_rounded_scores(self):
        components = []
        for cycle, done, required in [('F06a', 2, 3), ('F06b', 1, 1)]:
            counts = {c: {'done': 0, 'required': 0} for c in completion.CATEGORIES}
            counts['NOTES'] = {'done': done, 'required': required}
            components.append({'cycle': cycle, 'label': cycle, 'source': {'file_id': cycle},
                               'result': {'counts': counts, 'items': [], 'uncoded': []}})
        result = combined.pooled(declaration(), components)
        self.assertEqual(result['notes_score'], 7.5)
        self.assertIsNone(result['revision_score'])
        self.assertNotEqual(result['notes_score'], 10)
        self.assertTrue(result['component_coverage']['both_verified'])

    def test_meaningful_completion_counts_independent_wrong_answers_and_not_printed_directions(self):
        value = self.bind()
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(captures()[fid])):
            result = combined.score_row('read-only', value, combined.candidates(value)[0])
        self.assertEqual((result['notes_score'], result['revision_score']), (5.0, 5.0))
        self.assertEqual(result['counts']['NOTES'], {'done': 1, 'required': 2})
        self.assertEqual(result['counts']['DRAFT'], {'done': 1, 'required': 2})
        self.assertEqual(len(result['aggregate_provenance']['sources']), 2)
        self.assertEqual(len({(i['cycle'], i['category'], i['id']) for i in result['items']}), 4)
        copied = captures()
        prompt = 'Explain the observation using this supplied teacher direction and these original classroom examples.'
        for fid in (T1, S1): copied[fid]['deck']['slides'][1]['units'][0]['text'] = prompt
        copied[S1]['deck']['slides'][1]['units'][2]['text'] = prompt
        value = form()
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(copied[fid])):
            combined.bind_form('read-only', value, declaration())
            result = combined.score_row('read-only', value, combined.candidates(value)[0])
        self.assertEqual(result['counts']['NOTES']['done'], 0)

    def test_partial_component_is_scored_from_found_codes_with_visible_coverage_no_invented_zero(self):
        value = form(); value['students'][0]['earlier'] = []
        value = self.bind(value)
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(captures()[fid])):
            result = combined.score_row('read-only', value, combined.candidates(value)[0])
        self.assertEqual(result['notes_score'], 10)
        self.assertEqual(result['counts']['NOTES']['required'], 1)
        self.assertEqual(result['component_coverage']['missing'], ['F06b'])
        self.assertFalse(result['component_coverage']['both_verified'])
        self.assertIn('Partial coverage', result['missing_code_warning'])

    def test_source_byte_or_teacher_revision_change_holds_without_assessment(self):
        value = self.bind()
        for fid, field in [(S1, 'sha256'), (T2, 'metadata')]:
            changed = captures()
            if field == 'metadata': changed[fid][field]['version'] = '2'
            else: changed[fid][field] = 'f' * 64
            with patch.object(combined, 'read_source', side_effect=lambda token, source: deepcopy(changed[source])), \
                    patch.object(completion, 'assess') as assess:
                result = combined.score_row('read-only', value, combined.candidates(value)[0])
            self.assertEqual(result['code'], 'coverage_incomplete'); assess.assert_not_called()

    def test_stale_teacher_inventory_is_advisory_in_binding_and_fresh_scoring_without_new_requirements(self):
        original = captures()
        original[T1]['deck']['slides'][0]['units'][0]['text'] = original[T1]['deck']['slides'][0]['units'][0]['text'].replace('NOTES=1', 'NOTES=2')
        original[T1]['sha256'] = combined.digest(original[T1]['deck'])
        self.assertEqual(completion.readiness(original[T1]['deck'])['status'], 'NOT_READY')
        self.assertEqual(completion.teacher_readiness(original[T1]['deck'], require_complete_codes=False)['status'], 'READY')
        value = form()
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(original[fid])):
            combined.bind_form('read-only', value, declaration())
            self.assertIsNone(value['combined_assessment']['problem'])
            result = combined.score_row('read-only', value, combined.candidates(value)[0])
        self.assertEqual(result['counts']['NOTES'], {'done': 1, 'required': 2})
        self.assertEqual((result['notes_score'], result['revision_score']), (5.0, 5.0))

    def test_advisory_teacher_policy_keeps_zero_duplicate_and_mixed_cycles_held(self):
        for issue in ('zero', 'duplicate', 'mixed'):
            with self.subTest(issue=issue):
                original = captures(); units = original[T1]['deck']['slides'][1]['units']
                if issue == 'zero':
                    for unit in units: unit['text'] = completion.MARKER.sub('', unit['text'])
                elif issue == 'duplicate':
                    repeated = deepcopy(units[5]); repeated['key'] = 'another-notes-code'
                    units.append(repeated)
                else: units[-1]['text'] = units[-1]['text'].replace(':F06a:', ':F06b:')
                original[T1]['sha256'] = combined.digest(original[T1]['deck'])
                value = form()
                with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(original[fid])), \
                        patch.object(completion, 'assess') as assess:
                    combined.bind_form('read-only', value, declaration())
                self.assertTrue(value['combined_assessment']['problem']); assess.assert_not_called()

    def test_actual_byte_read_has_exact_metadata_bookends(self):
        raw = pptx([[('question', 'A fictional handout', (0, 0, 900, 50))]])
        metadata = captures()[S1]['metadata']
        with patch.object(google, 'drive_file', return_value=metadata) as meta, patch.object(google, 'deck_bytes', return_value=raw) as export:
            read = combined.read_source('read-only', S1)
        self.assertEqual(read['sha256'], hashlib.sha256(raw).hexdigest()); self.assertEqual(meta.call_count, 2)
        export.assert_called_once_with('read-only', S1, google.PPTX)
        with patch.object(google, 'drive_file', side_effect=[metadata, dict(metadata, version='2')]), \
                patch.object(google, 'deck_bytes', return_value=raw), self.assertRaises(combined.Held):
            combined.read_source('read-only', S1)

    def test_helper_and_score_file_use_both_selected_originals_preserving_all_global_holds(self):
        value = self.bind(); history = deepcopy(value)
        value['grade_readiness'] = {'status': 'READY'}
        found = {'lessons': [{'topic': 'Combined', 'forms': [value]}]}
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(captures()[fid])):
            scored = app._score_rows('read-only', agenda(), 'forensics', 'Wk 06', FORM, value, found)
            self.assertEqual(len(scored), 1)
            self.assertEqual(len(scored[0][1]['components']), 2)
            rows = morning.rows(value, scored, NOW)
        self.assertEqual(rows[0]['kind'], 'score')
        self.assertEqual((rows[0]['notes'], rows[0]['revision']), (5.0, 5.0))
        for problem in ({'assessment_problem': 'Wrong assignment'}, {'other_files': [object()]},
                        {'week_policy': {'excluded': True, 'reason': 'Excluded'}}):
            with patch.object(combined, 'read_source') as read:
                result = completion.score_file('read-only', agenda(), 'forensics', 'Wk 06', FORM, S1, {**found, **problem})
            self.assertIsNotNone(result['problem']); read.assert_not_called()
        self.assertEqual({k: v for k, v in value.items() if k != 'grade_readiness'}, history)

    def test_no_combined_absence_zero_and_zero_denominator_is_na(self):
        value = self.bind(); value['missing'] = [{'first': 'Missing', 'last': 'Example', 'section': 'A2 Fictional Forensics'}]
        rows = morning.rows(value, [], NOW)
        self.assertEqual(next(r for r in rows if r['first'] == 'Missing')['kind'], 'none')
        result = combined.pooled(declaration(), [])
        self.assertIsNone(result['notes_score']); self.assertIsNone(result['revision_score'])
        self.assertEqual(result['counts']['NOTES']['required'], 0)

    def test_empty_form_cannot_hide_an_unverified_combined_teacher_as_no_scores_or_record_ready(self):
        value = form(); value['students'] = []
        with patch.object(combined, 'read_source', side_effect=combined.Held('Exact teacher source unavailable.')):
            combined.bind_form('read-only', value, declaration())
        self.assertEqual(completion.submitted_readiness('read-only', value)['status'], 'NOT_READY')
        found = {'lessons': [{'topic': 'Combined', 'forms': [value]}]}
        with patch.object(work, 'week_policy', return_value={'excluded': False, 'problem': None}), \
                patch.object(completion, 'assignment_readiness', return_value={'status': 'READY'}), \
                patch.object(morning.ledger, 'read') as read, patch.object(app, '_score_rows') as score:
            result = morning.make_list('read-only', {}, agenda(), 'forensics', 'Wk 06', FORM,
                                       lambda *args, **kwargs: found, score)
        self.assertEqual(result['code'], 'coverage_incomplete'); read.assert_not_called(); score.assert_not_called()

    def test_record_gate_rechecks_both_exact_components_without_regrading(self):
        value = self.bind()
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(captures()[fid])), \
                patch.object(completion, 'assess') as assess:
            checked = completion.submitted_readiness('read-only', value, require_complete_codes=False)
        self.assertEqual(checked['status'], 'READY')
        self.assertEqual({c['file_id'] for c in checked['copies']}, {S1, S2}); assess.assert_not_called()

    def test_per_file_feedback_keeps_only_that_components_teacher_answers_and_revision(self):
        value = self.bind(); value['grade_readiness'] = {'status': 'READY'}
        found = {'lessons': [{'topic': 'Combined', 'forms': [value]}]}
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(captures()[fid])):
            result = completion.score_file('read-only', agenda(), 'forensics', 'Wk 06', FORM, S1, found,
                                           component_only=True)
        self.assertEqual((result['notes_score'], result['revision_score']), (10, 0))
        self.assertEqual((result['code'], result['template_id'], result['file_id']), ('F06a', T1, S1))
        self.assertNotIn('aggregate_provenance', result)
        self.assertEqual(result['counts']['DRAFT'], {'done': 0, 'required': 1})
        self.assertFalse(any('independent revision' in item.get('answer', '').lower() for item in result['items']))

    def test_title_routing_cannot_reassign_an_explicit_combined_form_or_import_wrong_form(self):
        submission = {'files': [{'name': 'Different exact lesson'}]}
        entries = [{'current': True, 'form': {'_combined_scope': declaration()}, 'found': [submission],
                    'label': 'Combined', 'words': {'combined'}, 'teacher_signature': []},
                   {'current': True, 'form': {}, 'found': [], 'label': 'Different exact lesson',
                    'words': {'different', 'lesson'}, 'teacher_signature': ['different exact lesson']}]
        self.assertEqual(work.wrong_form_moves(entries), [])

    def late_history(self, *, both_on_time=True, no_on_time=False):
        raw = event(S3, '2026-10-03T12:00:00Z', late=True)
        raw['earlier'] = ([] if no_on_time else [event(S2)]) + ([event(S1, '2026-10-02T12:00:00Z')] if both_on_time else [])
        value = form(); value.update(students=[], late=[raw], window={'at': '2026-10-02T23:59:59Z'})
        return value

    def test_constructed_late_raw_history_derives_on_time_flags_and_does_not_double_list_student(self):
        # Defensive imported-history shape: normal work.group already prefers
        # an on-time current row. This is not a claim about live W06 intake.
        value = self.late_history(); raw = deepcopy(value)
        self.bind(value); candidate = combined.candidates(value)[0]
        self.assertFalse(candidate['late'])
        self.assertEqual({f['id'] for f in candidate['files']}, {S1, S2})
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(captures()[fid])):
            score = combined.score_row('read-only', value, candidate)
        rows = morning.rows(value, [(candidate, score)], NOW)
        self.assertEqual([(r['kind'], r['notes'], r['revision']) for r in rows], [('score', 5.0, 5.0)])
        self.assertEqual(value['late'], raw['late']); self.assertEqual(value['students'], raw['students'])
        value['combined_assessment']['on_time'].append(deepcopy(candidate))
        self.assertEqual(morning.rows(value, [(candidate, score)], NOW)[0]['kind'], 'check')

    def test_one_on_time_cycle_plus_later_other_cycle_preserves_partial_and_no_eligible_cycle_is_none(self):
        value = self.late_history(both_on_time=False); self.bind(value)
        candidate = combined.candidates(value)[0]
        self.assertFalse(candidate['late']); self.assertEqual([f['id'] for f in candidate['files']], [S2])
        self.assertEqual({f['id'] for f in combined.candidates(value, include_late=True)[0]['files']}, {S2, S3})
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(captures()[fid])):
            score = combined.score_row('read-only', value, candidate)
        rows = morning.rows(value, [(candidate, score)], NOW)
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]['kind'], 'score')
        self.assertEqual(score['component_coverage']['missing'], ['F06a'])
        value = self.late_history(both_on_time=False, no_on_time=True); self.bind(value)
        self.assertEqual(combined.candidates(value), [])
        rows = morning.rows(value, [], NOW)
        self.assertEqual([(r['kind'], r['notes'], r['revision']) for r in rows], [('none', None, None)])

    def test_each_selected_feedback_component_retains_its_original_receipt_and_no_superseded_source(self):
        value = self.bind(); raw = deepcopy(value)
        found = {'lessons': [{'forms': [value]}]}
        for fid, at in [(S1, '2026-10-02T12:00:00Z'), (S2, '2026-10-01T12:00:00Z')]:
            row, returned = native_feedback._row(found, FORM, fid)
            self.assertIs(returned, value)
            self.assertEqual(row['submitted_at'], at)
            receipt = native_feedback._submission_receipt(row, {'form': FORM, 'file': fid})
            self.assertEqual(receipt['submitted_at'], at)
            self.assertEqual(native_feedback._feedback_name(value, row), 'Ann A.')
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(captures()[fid])):
            scored = app._score_rows('read-only', agenda(), 'forensics', 'Wk 06', FORM, value, found, component_only=True)
        self.assertEqual({s['file_id']: (s['notes_score'], s['revision_score']) for _, s in scored},
                         {S1: (10, 0), S2: (0, 10)})
        self.assertEqual(value, raw)
        changed = form(); changed['students'][0]['earlier'].append(event(S3, '2026-10-03T12:00:00Z')); self.bind(changed)
        with self.assertRaises(native_feedback.Held): native_feedback._row({'lessons': [{'forms': [changed]}]}, FORM, S1)

    def test_feedback_listing_names_both_components_without_duplicate_person_name_or_model_call(self):
        value = self.bind(); found = {'lessons': [{'forms': [value]}]}
        with patch.object(native_feedback, 'configured_store'), patch.object(native_feedback, '_read', return_value=None), \
                patch.object(native_feedback, '_call') as model:
            result = native_feedback.listing({}, {'class': 'forensics', 'week': 'Wk 06', 'form': FORM}, found)
        self.assertEqual([(r['name'], r['file_id'], r['hold']) for r in result['students']],
                         [('Ann A.', S1, ''), ('Ann A.', S2, '')]); model.assert_not_called()


if __name__ == '__main__': unittest.main()
