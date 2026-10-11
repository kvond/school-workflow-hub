"""Review-only source acknowledgements on fictional original/IC captures."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock, patch

from hub_v2 import completion, google, review_context, source_history_dispositions as dispositions
from school_workflow_hub.core.cloud_state import Conflict, Document, Snapshot, StoreUnavailable
from school_workflow_hub.core.runs import content_hash
from test_form_source_bindings_and_case_policy import Agenda, SECOND, STUDENT, metadata

OLDER = 'FICTIONAL_OLDER_ORIGINAL_12345'
NOW = datetime.now(timezone.utc)
HASH = 'a' * 64


def captures():
    row = {'matched': True, 'first': 'Ann', 'last': 'Avery', 'section': 'A2 Fictional',
           'email': '', 'source': 'folder', 'matched_by': 'file name', 'submitted_at': '',
           'files': [{'id': STUDENT, 'mime': completion.SLIDES, 'name': 's.ann.avery — Original'}],
           'earlier': [{'source': 'folder', 'matched_by': 'file name', 'submitted_at': '',
                        'files': [{'id': OLDER, 'mime': completion.SLIDES, 'name': 's.ann.avery — Older original'}]}]}
    form = {'form_id': SECOND, 'label': 'Evidence Collection', 'students': [row], 'late': [], 'missing': [], 'excused': []}
    found = {'class_key': 'forensics', 'week': 'Wk 06', 'lessons': [{'forms': [form]}]}
    scope = {'class_key': 'forensics', 'class_name': 'Forensics', 'week': 'Wk 06', 'form_id': SECOND,
             'lesson': 'Evidence Collection', 'sources': [], 'grade_policy': {}}
    roster = ({'forensics': [{k: row[k] for k in ('first', 'last', 'section')}]}, [])
    result = review_context.build(scope, found)
    return row, found, scope, roster, result


def receipt(*, basis='manual_saved_history', native_at=None):
    row, found, scope, roster, result = captures()
    current = dispositions.current_basis(Agenda(), {}, scope, found, row, roster)
    native_at = native_at or NOW - timedelta(minutes=5)
    at = native_at.isoformat()
    value = {'schema': dispositions.SCHEMA, 'scope': current['scope'],
             'identity': {**current['identity'], 'school_email': 's.ann.avery@fictional.example',
                          'student_id': '12345', 'section_id': '101'},
             'roster_sha256': current['roster_sha256'], 'candidate_sha256': current['candidate_sha256'],
             'issue_kind': 'source_history_unordered', 'issue_sha256': content_hash(result['notices'][0]['reason']),
             'sources': [{'file_id': fid, 'metadata': metadata(fid), 'metadata_sha256': content_hash(metadata(fid)),
                          'content_sha256': HASH, 'identity_sha256': HASH, 'original_event': {}}
                         for fid in (STUDENT, OLDER)],
             'basis': basis, 'instruction_sha256': HASH, 'operator': 'teacher@fictional.example',
             'verified_at': at, 'native_saved': {'verified_at': at, 'save_verified_at': at,
                 'evidence_sha256': HASH, 'student_id': '12345', 'section_id': '101',
                 'components': [{'column': col, 'assignment_id': ident, 'score_group': '401', 'term_id': '501',
                                 'calendar_id': '854', 'structure_id': '854', 'school_id': '55', 'task_id': '13', 'year': '2027',
                                 'points': 10, 'value': 10, 'saved': True}
                                for col, ident in (('notes', '301'), ('revision', '302'))]}}
    targets = []
    for component in value['native_saved']['components']:
        ident = component['assignment_id']
        targets.append({'section': 'A2 Fictional', 'column': component['column'], 'section_id': '101',
            **{k: str(component[k]) for k in ('assignment_id', 'score_group', 'term_id', 'calendar_id', 'structure_id', 'school_id', 'task_id', 'year')},
            'label_code': 'W07F' if component['column'] == 'notes' else 'W07S',
            'url': 'https://ic.fictional.example/campus/sis/control-center/classroom/curriculum/resource/%s/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/101/objectSectionID/%s/calendarID/854/structureID/854)?classroomSectionID=101&classroomTermID=501&year=2027&schoolID=55&calendarID=854&structureID=854' % (ident, ident)})
    value['native_saved']['targets'] = targets
    current['expected_targets'] = deepcopy(targets)
    current['native_instruction_sha256'] = HASH
    if basis == 'maximum_saved_as_of':
        current['mapped_targets'] = deepcopy(targets)
    for original in value['sources']:
        attribution = {'method': 'teacher_authorized_exact_original', 'file_id': original['file_id'],
            'metadata_sha256': original['metadata_sha256'], 'instruction_sha256': HASH,
            **{k: value['identity'][k] for k in ('first', 'last', 'section', 'school_email')}}
        original['identity_attribution'] = attribution
        original['identity_sha256'] = content_hash(attribution)
    value['record_key'] = dispositions.receipt_key(value)
    return value, current, result['notices'][0]


def configured(value):
    return {dispositions.CONFIG: json.dumps([{'class_key': 'forensics', 'week': 'Wk 06', 'form_id': SECOND,
                                            'record_key': value['record_key'], 'native_targets': value['native_saved']['targets'],
                                            'instruction_sha256': value['instruction_sha256']}])}


def apply(value, *, found=None, scope=None, roster=None, result=None, md=None):
    row, actual_found, actual_scope, actual_roster, actual_result = captures()
    store = Mock()
    store.read.return_value = Snapshot({value['record_key']: Document(value, '1')}, NOW.isoformat())
    with patch.object(dispositions, 'configured_store', return_value=store), \
         patch.object(google, 'drive_file', side_effect=md or (lambda token, fid, fields: metadata(fid))), \
         patch.object(completion, 'assess', side_effect=AssertionError('no assessment')), \
         patch.object(google, 'deck_bytes', side_effect=AssertionError('no source bytes')):
        output = dispositions.apply('read', configured(value), Agenda(), scope or actual_scope,
                                    found or actual_found, result or actual_result, roster or actual_roster)
    store.commit.assert_not_called()
    return output


class SourceHistoryDispositionTests(unittest.TestCase):
    def test_durable_manual_ack_retains_links_events_and_as_of_time_not_live_grade_claim(self):
        value, basis, detail = receipt(native_at=NOW - timedelta(days=10))
        before = captures()[1]
        self.assertEqual(dispositions.verify_current(value, basis, detail, {fid: metadata(fid) for fid in (STUDENT, OLDER)}, now=NOW), value)
        out = apply(value)
        self.assertEqual(out['status'], 'history_retained')
        self.assertEqual(out['notices'], [])
        retained = out['retained_history'][0]
        self.assertNotIn('next_action', retained)
        self.assertEqual(retained['sources'], detail['sources'])
        self.assertEqual(retained['slides'], detail['slides'])
        self.assertIn(value['native_saved']['verified_at'], retained['reason'])
        self.assertNotIn('currently', retained['reason'])
        self.assertFalse(out['source_selection']['assessment_performed'])
        self.assertEqual(captures()[1], before)
        self.assertTrue(all(source['original_event'] == {} for source in value['sources']))

    def test_maximum_is_exact_two_ten_point_fields_and_short_same_day_only(self):
        value, basis, detail = receipt(basis='maximum_saved_as_of')
        md = {fid: metadata(fid) for fid in (STUDENT, OLDER)}
        dispositions.verify_current(value, basis, detail, md, now=NOW)
        wrong = deepcopy(basis); wrong['mapped_targets'][0]['assignment_id'] = '999'
        with self.assertRaises(ValueError):
            dispositions.verify_current(value, wrong, detail, md, now=NOW)
        missing = deepcopy(basis); missing['mapped_targets'] = []
        with self.assertRaises(ValueError):
            dispositions.verify_current(value, missing, detail, md, now=NOW)
        for minutes in (31, 1440):
            with self.assertRaises(ValueError):
                dispositions.verify_current(value, basis, detail, md, now=NOW + timedelta(minutes=minutes))
        for field, bad in (('value', 9.9), ('value', None), ('points', 20), ('saved', False)):
            changed = deepcopy(value); changed['native_saved']['components'][0][field] = bad
            changed['record_key'] = dispositions.receipt_key(changed)
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                dispositions.validate(changed)

    def test_changed_source_parent_version_trash_or_unavailable_retains_current_notice(self):
        value, _, _ = receipt()
        for change in ({'parents': ['FICTIONAL_OTHER_PARENT_12345']}, {'version': '5'}, {'trashed': True}):
            out = apply(value, md=lambda token, fid, fields: {**metadata(fid), **(change if fid == STUDENT else {})})
            self.assertEqual(len(out['notices']), 1)
            self.assertEqual(out.get('retained_history'), [])
        out = apply(value, md=Mock(side_effect=google.GoogleError('Google is limiting requests (429).', 429)))
        self.assertEqual(len(out['notices']), 1)
        self.assertFalse(out.get('retained_history'))
        self.assertEqual(out['status'], 'unavailable')
        self.assertIn('after Google allows', out['notices'][0]['next_action'])
        self.assertNotIn('Confirm the student', ' '.join(out['limitations']))

    def test_changed_identity_roster_scope_or_new_original_invalidates_ack(self):
        value, _, _ = receipt()
        for change in ('identity', 'roster', 'scope', 'new_original'):
            row, found, scope, roster, result = captures()
            if change == 'identity':
                found['lessons'][0]['forms'][0]['students'][0]['email'] = 'different@fictional.example'
            elif change == 'roster':
                roster[0]['forensics'].append({'first': 'Ann', 'last': 'Avery', 'section': 'A2 Fictional'})
            elif change == 'scope':
                scope['lesson'] = 'Different teacher lesson'
            else:
                found['lessons'][0]['forms'][0]['students'][0]['earlier'].append({'source': 'folder', 'files': [{'id': 'FICTIONAL_NEW_ORIGINAL_12345'}]})
            out = apply(value, found=found, scope=scope, roster=roster, result=result)
            self.assertEqual(len(out['notices']), 1)
            self.assertFalse(out.get('retained_history'))

    def test_mapping_or_form_event_change_cannot_lend_previous_proof(self):
        value, basis, detail = receipt()
        changed = deepcopy(basis); changed['scope']['context_sha256'] = 'b' * 64
        with self.assertRaises(ValueError):
            dispositions.verify_current(value, changed, detail, {fid: metadata(fid) for fid in (STUDENT, OLDER)}, now=NOW)
        changed = deepcopy(basis); changed['original_events'][STUDENT] = {'source_form_id': SECOND}
        with self.assertRaises(ValueError):
            dispositions.verify_current(value, changed, detail, {fid: metadata(fid) for fid in (STUDENT, OLDER)}, now=NOW)

    def test_ack_never_suppresses_assessment_identity_access_lessonwide_or_new_issue(self):
        value, _, detail = receipt()
        row, found, scope, roster, result = captures()
        for issue in ('assessment', 'identity_unverified', 'source_availability', 'source_route_unverified', 'source_issue'):
            other = {**deepcopy(detail), 'issue_kind': issue, 'reason': 'A different current source problem.'}
            result['notices'] = [detail, other]
            out = apply(value, result=result)
            self.assertEqual(out['notices'], [other])
            self.assertEqual(len(out['retained_history']), 1)
        lesson = {**deepcopy(detail), 'affected_scope': 'lesson'}
        result['notices'] = [lesson]
        self.assertEqual(apply(value, result=result)['notices'], [lesson])
        self.assertEqual(dispositions.issue_kind('Submission history has conflicting student identities; confirm the current revision.'), 'source_issue')

    def test_altered_native_destination_or_unbound_allocation_attribution_rejects(self):
        value, basis, detail = receipt()
        md = {fid: metadata(fid) for fid in (STUDENT, OLDER)}
        wrong = deepcopy(value)
        wrong['native_saved']['components'][0].update(assignment_id='99999999', term_id='88888888')
        wrong['record_key'] = dispositions.receipt_key(wrong)
        with self.assertRaises(ValueError):
            dispositions.verify_current(wrong, basis, detail, md, now=NOW)
        wrong = deepcopy(value); wrong['sources'][0]['identity_attribution']['school_email'] = 'other@fictional.example'
        wrong['sources'][0]['identity_sha256'] = content_hash(wrong['sources'][0]['identity_attribution'])
        wrong['record_key'] = dispositions.receipt_key(wrong)
        with self.assertRaises(ValueError):
            dispositions.verify_current(wrong, basis, detail, md, now=NOW)
        unknown = deepcopy(basis); unknown['native_instruction_sha256'] = None
        with self.assertRaises(ValueError):
            dispositions.verify_current(value, unknown, detail, md, now=NOW)
        # The current AX capture supplies exact names/section/components,
        # not a private person ID; an absent ID must stay absent.
        absent = deepcopy(value); absent['identity'].pop('student_id'); absent['native_saved'].pop('student_id')
        absent['record_key'] = dispositions.receipt_key(absent)
        dispositions.verify_current(absent, basis, detail, md, now=NOW)

    def test_final_metadata_bookend_discards_partial_ack(self):
        value, _, _ = receipt()
        counts = {}
        def read(token, fid, fields):
            counts[fid] = counts.get(fid, 0) + 1
            return {**metadata(fid), **({'version': 'changed'} if counts[fid] > 2 else {})}
        out = apply(value, md=read)
        self.assertEqual(len(out['notices']), 1)
        self.assertFalse(out.get('retained_history'))
        self.assertIn('could not be checked', ' '.join(out['limitations']))

    def test_unavailable_private_store_is_read_limit_not_a_new_revision_question(self):
        value, _, _ = receipt()
        row, found, scope, roster, result = captures()
        store = Mock(); store.read.side_effect = StoreUnavailable('Private source history is temporarily unavailable.')
        with patch.object(dispositions, 'configured_store', return_value=store), patch.object(google, 'drive_file') as md:
            output = dispositions.apply('read', configured(value), Agenda(), scope, found, result, roster)
        self.assertEqual(output['status'], 'unavailable')
        self.assertFalse(output['notices'][0]['student']['verified'])
        self.assertNotIn('confirm the current revision', output['notices'][0]['reason'])
        self.assertIn('No student identity decision', output['notices'][0]['next_action'])
        md.assert_not_called(); store.commit.assert_not_called()
        # A genuinely absent receipt does not imply unavailable proof or
        # create an acknowledgement; preserve the actual source notice.
        store.read.side_effect = None; store.read.return_value = Snapshot({}, NOW.isoformat())
        with patch.object(dispositions, 'configured_store', return_value=store), patch.object(google, 'drive_file') as md:
            output = dispositions.apply('read', configured(value), Agenda(), scope, found, result, roster)
        self.assertEqual(output['notices'], result['notices'])
        self.assertFalse(output['retained_history']); md.assert_not_called()

    def test_host_import_is_exact_once_CAS_and_reread_no_school_action(self):
        value, basis, detail = receipt()
        port = Mock()
        doc = Document(value, '1')
        port.read.side_effect = [Snapshot({}, NOW.isoformat()), Snapshot({value['record_key']: doc}, NOW.isoformat())]
        self.assertEqual(dispositions.import_verified(port, value, basis, detail, {fid: metadata(fid) for fid in (STUDENT, OLDER)}, now=NOW), value['record_key'])
        port.commit.assert_called_once_with({value['record_key']: (value, None)})
        port.reset_mock(); port.read.side_effect = None; port.read.return_value = Snapshot({value['record_key']: doc}, NOW.isoformat())
        dispositions.import_verified(port, value, basis, detail, {fid: metadata(fid) for fid in (STUDENT, OLDER)}, now=NOW)
        port.commit.assert_not_called()
        port.read.return_value = Snapshot({value['record_key']: Document({**value, 'operator': 'other@fictional.example'}, '2')}, NOW.isoformat())
        with self.assertRaises(Conflict):
            dispositions.import_verified(port, value, basis, detail, {fid: metadata(fid) for fid in (STUDENT, OLDER)}, now=NOW)
        port.commit.assert_not_called()

    def test_current_review_integration_is_model_free_read_only_and_preserves_grade_candidates(self):
        value, _, _ = receipt()
        row, found, scope, roster, result = captures()
        old_candidates = completion.recheck_candidates(found['lessons'][0]['forms'][0])
        store = Mock(); store.read.return_value = Snapshot({value['record_key']: Document(value, '1')}, NOW.isoformat())
        with patch.object(dispositions, 'configured_store', return_value=store), \
             patch.object(google, 'drive_file', side_effect=lambda token, fid, fields: metadata(fid)), \
             patch.object(completion, 'assess') as assess, patch.object(completion, 'score_file') as score, \
             patch.object(google, 'deck_bytes') as byte_read, patch.object(google, 'append_rows') as append:
            output = review_context._read_found('read', configured(value), Agenda(), scope, found, roster_snapshot=roster)
        self.assertEqual(output['status'], 'history_retained')
        self.assertEqual(completion.recheck_candidates(found['lessons'][0]['forms'][0]), old_candidates)
        self.assertTrue(old_candidates[0]['selection_problem'])
        assess.assert_not_called(); score.assert_not_called(); byte_read.assert_not_called(); append.assert_not_called(); store.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
