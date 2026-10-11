"""Fictional on-time folder/later canonical Form shape; no source or grade reads."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from hub_v2 import completion
from tests.test_completion_older_routed_folder_history import fixture, NOW

def folder_preferred():
    form = fixture(current='2026-09-29T21:59:26Z', prior='2026-09-16T15:38:00Z', stamp='09-16 11.38')
    original = form['students'][0]
    archive = original['earlier'][0]
    submitted = {k: deepcopy(v) for k, v in original.items() if k not in ('earlier', 'matched', 'first', 'last', 'section')}
    submitted['late'] = submitted['files'][0]['late'] = True
    row = {**original, **deepcopy(archive), 'earlier': [submitted]}
    form['students'] = [row]
    return form

class PreferredFolderRecheck(unittest.TestCase):
    def check(self, form, *, now=NOW):
        before = deepcopy(form)
        result = completion.recheck_candidates(form, now=now)
        self.assertEqual(form, before)
        return result

    def held(self, form):
        result = self.check(form)
        self.assertTrue(not result or result[0].get('selection_problem'))

    def test_only_recheck_promotes_exact_later_canonical_event_and_retains_preferred_archive(self):
        form = folder_preferred(); preferred = form['students'][0]; submitted = preferred['earlier'][0]
        result = self.check(form)[0]
        self.assertNotIn('selection_problem', result)
        for key in ('files', 'email', 'source', 'submitted_at', 'late', 'matched_by'):
            self.assertEqual(result[key], submitted[key])
        self.assertEqual(result['earlier'], [{k: v for k, v in preferred.items() if k != 'earlier'}])
        self.assertEqual(form['students'][0]['files'][0]['id'], 'OLDER_FICTIONAL_0')
        self.assertTrue(result['late'])

    def test_older_unknown_unstamped_embedded_equal_newer_archive_stays_held(self):
        for change in ('unknown', 'no_stamp', 'embedded', 'equal', 'newer'):
            form = folder_preferred(); row = form['students'][0]
            if change == 'unknown': row['submitted_at'] = ''
            elif change == 'no_stamp': row['files'][0]['name'] = 's.student0.example — Earlier source'
            elif change == 'embedded': row['files'][0]['name'] = 's.student0.example — Lesson — 09-16 11.38 — more title'
            elif change == 'equal': row['submitted_at'] = '2026-09-29T21:59:26Z'; row['files'][0]['name'] = 's.student0.example — 09-29 17.59 — Earlier source'
            else: row['submitted_at'] = '2026-10-01T15:38:00Z'; row['files'][0]['name'] = 's.student0.example — 10-01 11.38 — Later source'
            with self.subTest(change=change): self.held(form)

    def test_unverified_or_wrong_canonical_email_Form_file_event_never_promotes(self):
        for change in ('other_email', 'wrong_Form', 'missing_event', 'different_time', 'name_match', 'no_email'):
            form = folder_preferred(); event = form['students'][0]['earlier'][0]; upload = event['files'][0]
            if change == 'other_email': event['email'] = 'another@student.example'
            elif change == 'wrong_Form': upload['source_form_id'] = 'OTHER_CANONICAL_FORM'
            elif change == 'missing_event': upload.pop('source_response_id')
            elif change == 'different_time': upload['source_submitted_at'] = '2026-09-28T12:00:00Z'
            elif change == 'name_match': event['matched_by'] = 'file name'
            else: event['email'] = ''
            with self.subTest(change=change): self.held(form)

    def test_future_empty_multiple_and_ambiguous_latest_upload_hold(self):
        for change in ('future', 'empty', 'multiple', 'same_time_different_file'):
            form = folder_preferred(); row = form['students'][0]; event = row['earlier'][0]
            if change == 'future': event['submitted_at'] = event['files'][0]['source_submitted_at'] = '2027-01-01T00:00:00Z'
            elif change == 'empty': event['files'] = []
            elif change == 'multiple': event['files'].append({**event['files'][0], 'id': 'OTHER_FILE'})
            else:
                other = deepcopy(event); other['files'][0]['id'] = 'OTHER_FILE'; row['earlier'].append(other)
            with self.subTest(change=change): self.held(form)

    def test_each_extra_unknown_history_must_pass_same_archive_guard(self):
        form = folder_preferred(); extra = deepcopy(form['students'][0]); extra.pop('earlier')
        extra['files'][0]['id'] = 'UNVERIFIED_EXTRA'; extra['files'][0]['name'] = 'Unstamped source'
        form['students'][0]['earlier'].append(extra)
        self.held(form)

    def test_trash_wronglesson_shared_unknownflags_and_shared_current_file_hold(self):
        for target in ('preferred', 'event'):
            for change in ('trash', 'wrong_lesson', 'shared', 'unknownflags', 'problem'):
                form = folder_preferred(); row = form['students'][0]
                file = (row if target == 'preferred' else row['earlier'][0])['files'][0]
                if change == 'trash': file['trashed'] = True
                elif change == 'wrong_lesson': file['other_week'] = 9
                elif change == 'shared': file['from_shared'] = True
                elif change == 'unknownflags': file.pop('from_folder')
                else: file['problem'] = 'Not readable'
                with self.subTest(target=target, change=change): self.held(form)
        form = folder_preferred(); other = deepcopy(form['students'][0]); other['first'] = 'Another'; other['last'] = 'Student'
        form['students'].append(other)
        self.assertTrue(all(r.get('selection_problem') for r in self.check(form)))

    def test_foreign_or_conflicting_verified_event_history_is_not_normalized(self):
        for change in ('other_email', 'other_Form'):
            form = folder_preferred(); row = form['students'][0]; other = deepcopy(row['earlier'][0])
            other['submitted_at'] = other['files'][0]['source_submitted_at'] = '2026-09-28T12:00:00Z'
            other['files'][0]['id'] = 'OTHER_EVENT_FILE'
            if change == 'other_email': other['email'] = 'another@student.example'
            else: other['files'][0]['source_form_id'] = 'OTHER_FORM'
            row['earlier'].append(other)
            with self.subTest(change=change): self.held(form)

    def test_identical_folder_coded_answers_are_not_metadata_proof_or_new_policy(self):
        form = folder_preferred(); form['students'][0]['earlier'][0]['source'] = 'folder'
        self.held(form)

if __name__ == '__main__': unittest.main()
