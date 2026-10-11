"""Verified current Form uploads may retain a strictly older routed archive.

Synthetic cases reproduce the six saved receipt/folder shapes without private
identifiers. No source reads, scoring, grade writes or history mutation.
"""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from hub_v2 import completion, google

FORM = 'FICTIONAL_CANONICAL_FORM_12345'
NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def fixture(index=0, *, current='2026-09-25T12:52:39.973626Z',
            prior='2026-09-16T19:35:00Z', stamp='09-16 15.35'):
    username = 's.student%d.example' % index
    current_file = {'id': 'CURRENT_FICTIONAL_%d' % index, 'name': 'Actual uploaded lesson',
                    'mime': google.PPTX if index % 2 else completion.SLIDES,
                    'source_form_id': FORM, 'source_response_id': 'ACTUAL_FORM_EVENT_%d' % index,
                    'source_submitted_at': current, 'trashed': False, 'problem': None,
                    'other_week': None, 'from_folder': False, 'from_shared': False, 'late': False}
    old_file = {'id': 'OLDER_FICTIONAL_%d' % index,
                'name': '%s — %s — Earlier lesson copy' % (username, stamp),
                'mime': completion.SLIDES, 'trashed': False, 'problem': None,
                'other_week': None, 'from_folder': True, 'from_shared': False, 'late': False}
    old = {'source': 'folder', 'submitted_at': prior, 'files': [old_file],
           'email': '', 'matched_by': 'file name', 'late': False}
    row = {'matched': True, 'last': 'Example%d' % index, 'first': 'Student%d' % index,
           'section': 'Fictional Science', 'matched_by': 'email',
           'email': username + '@fictional.example', 'source': 'form',
           'submitted_at': current, 'files': [current_file], 'late': False, 'earlier': [old]}
    return {'form_id': FORM, 'students': [row], 'late': []}


class OlderRoutedFolderHistoryTests(unittest.TestCase):
    def check(self, form):
        before = deepcopy(form)
        result = completion.recheck_candidates(form, now=NOW)
        self.assertEqual(form, before, 'Raw work and all history must stay intact.')
        return result[0]

    def held(self, form):
        self.assertIn('selection_problem', self.check(form))

    def test_six_current_canonical_upload_shapes_supersede_only_older_archives(self):
        cases = [('2026-09-25T12:52:39.973626Z', '2026-09-16T19:35:00Z', '09-16 15.35'),
                 ('2026-09-23T14:00:54.833719Z', '2026-09-14T14:06:00Z', '09-14 10.06'),
                 ('2026-09-23T15:36:45.122266Z', '2026-09-16T16:40:00Z', '09-16 12.40'),
                 ('2026-09-25T15:13:20.089516Z', '2026-09-16T15:44:00Z', '09-16 11.44'),
                 ('2026-09-23T16:10:38.153164Z', '2026-09-16T16:15:00Z', '09-16 12.15'),
                 ('2026-09-25T15:40:00Z', '2026-09-14T14:15:00Z', '09-14 10.15')]
        for index, (current, prior, stamp) in enumerate(cases):
            with self.subTest(index=index):
                form = fixture(index, current=current, prior=prior, stamp=stamp)
                result = self.check(form)
                self.assertNotIn('selection_problem', result)
                self.assertEqual(result['files'], form['students'][0]['files'])
                self.assertEqual(result['earlier'], form['students'][0]['earlier'])
                self.assertEqual(result['submitted_at'], current)
                self.assertNotIn('source_response_id', result['earlier'][0]['files'][0])

    def test_current_upload_requires_exact_canonical_form_file_event_email_and_flags(self):
        for change in ('wrong_form', 'no_form', 'no_response', 'wrong_event_time', 'not_email',
                       'no_email', 'not_matched', 'current_shared', 'current_folder', 'current_trashed',
                       'current_wrong_week', 'current_problem', 'current_bad_mime', 'no_file_id'):
            with self.subTest(change=change):
                form = fixture(); row = form['students'][0]; upload = row['files'][0]
                if change == 'wrong_form': upload['source_form_id'] = 'OTHER_FORM'
                elif change == 'no_form': form.pop('form_id')
                elif change == 'no_response': upload.pop('source_response_id')
                elif change == 'wrong_event_time': upload['source_submitted_at'] = '2026-09-24T12:00:00Z'
                elif change == 'not_email': row['matched_by'] = 'file name'
                elif change == 'no_email': row['email'] = ''
                elif change == 'not_matched': row['matched'] = False
                elif change == 'current_shared': upload['from_shared'] = True
                elif change == 'current_folder': upload['from_folder'] = True
                elif change == 'current_trashed': upload['trashed'] = True
                elif change == 'current_wrong_week': upload['other_week'] = 7
                elif change == 'current_problem': upload['problem'] = 'Not readable'
                elif change == 'current_bad_mime': upload['mime'] = 'application/pdf'
                else: upload['id'] = ''
                if change in ('not_matched', 'no_file_id'):
                    result = completion.recheck_candidates(form, now=NOW)
                    self.assertTrue(not result or result[0].get('selection_problem'))
                else: self.held(form)

    def test_equal_newer_unknown_future_and_inconsistent_routing_dates_hold(self):
        cases = [('2026-09-25T12:52:39.973626Z', '09-25 08.52'),
                 ('2026-09-26T12:00:00Z', '09-26 08.00'),
                 ('2027-09-16T19:35:00Z', '09-16 15.35'),
                 ('', '09-16 15.35'), ('2026-09-16', '09-16 15.35'),
                 ('2026-09-16T19:35:00Z', '09-15 15.35'),
                 ('2026-09-16T19:35:00Z', '99-16 15.35')]
        for prior, stamp in cases:
            with self.subTest(prior=prior, stamp=stamp): self.held(fixture(prior=prior, stamp=stamp))
        form = fixture(); form['students'][0]['earlier'][0]['files'][0]['name'] = 's.student0.example — No received date'
        self.held(form)

    def test_older_file_identity_shared_alias_and_unavailable_archives_hold(self):
        for change in ('different_username', 'no_username', 'shared', 'not_folder', 'wrong_week',
                       'trashed', 'problem', 'other_email', 'wrong_match', 'bad_mime', 'old_form',
                       'old_response', 'old_event', 'two_files', 'unknown_flags'):
            with self.subTest(change=change):
                form = fixture(); old = form['students'][0]['earlier'][0]; file = old['files'][0]
                if change == 'different_username': file['name'] = file['name'].replace('student0', 'another')
                elif change == 'no_username': file['name'] = 'Earlier copy — 09-16 15.35 — lesson'
                elif change == 'shared': file['from_shared'] = True
                elif change == 'not_folder': file['from_folder'] = False
                elif change == 'wrong_week': file['other_week'] = 5
                elif change == 'trashed': file['trashed'] = True
                elif change == 'problem': file['problem'] = 'Unavailable'
                elif change == 'other_email': old['email'] = 'another@fictional.example'
                elif change == 'wrong_match': old['matched_by'] = 'name typed in the Form'
                elif change == 'bad_mime': file['mime'] = 'application/pdf'
                elif change == 'old_form': file['source_form_id'] = 'UNVERIFIED_OTHER_FORM'
                elif change == 'old_response': file['source_response_id'] = 'UNKNOWN_EVENT'
                elif change == 'old_event': file['source_submitted_at'] = old['submitted_at']
                elif change == 'two_files': old['files'].append({**file, 'id': 'OTHER_ARCHIVE_FILE'})
                else: file.pop('from_shared')
                self.held(form)

    def test_unverified_older_form_is_never_relabelled_as_folder_history(self):
        form = fixture(); form['students'][0]['earlier'][0]['source'] = 'form'
        self.held(form)

    def test_time_embedded_in_original_title_is_not_a_routed_submission_stamp(self):
        for name in ('s.student0.example — Original lesson title — 09-16 15.35 — still title text',
                     's.student0.example — Earlier lesson 09-16 15.35 — title',
                     's.student0.example — 09-16 15.35 — '):
            with self.subTest(name=name):
                form = fixture(); form['students'][0]['earlier'][0]['files'][0]['name'] = name
                self.held(form)

    def test_real_current_identity_conflicts_duplicate_latest_and_multiple_decks_hold(self):
        for change in ('identity', 'same_latest', 'multiple_current', 'future_form'):
            with self.subTest(change=change):
                form = fixture(); row = form['students'][0]
                if change == 'multiple_current': row['files'].append({**row['files'][0], 'id': 'SECOND_CURRENT_FILE'})
                else:
                    later = deepcopy(row); later.pop('earlier'); later['files'][0]['id'] = 'LATEST_FORM_FILE'
                    if change == 'identity': later['email'] = 'another@fictional.example'
                    elif change == 'future_form': later['submitted_at'] = later['files'][0]['source_submitted_at'] = '2027-01-01T00:00:00Z'
                    row['earlier'].append(later)
                self.held(form)

    def test_case_only_username_difference_preserves_canonical_receipt_and_archive(self):
        form = fixture(); row = form['students'][0]; row['email'] = row['email'].upper()
        self.assertNotIn('selection_problem', self.check(form))


if __name__ == '__main__': unittest.main()
