"""Teacher's own Form uploads and timed-out source reads, on fictional data only.

A teacher-account upload preserves the normal student identity/source checks. A typed provider read timeout is repeated once while the
request has time, then reported as "timed out, retry", never as missing work.
No Google call, model call, grade write or Drive move is made.
"""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch
import unittest

from hub_v2 import app, google, native_feedback, review_context as rc, roster, work
from hub_v2 import daily_feedback_packet
from test_hub_v2 import DIRECT, ROSTER, fixture, response
from test_review_context import FORM, complete_roster, found, row, scope

TEACHER = 'teacher@school.example'
CONFIG = {'HUB_READER_SERVICE_ACCOUNT': 'reader@fictional.example', 'HUB_SCHOOL_EMAIL_DOMAINS': 'school.example',
          'HUB_ALLOWED_EMAILS': ' Teacher@School.example , home.teacher@fictional.example,'}
SLIDES = 'application/vnd.google-apps.presentation'


def timeout():
    return google.GoogleError("Google didn't answer in time. Try again.", reason='read_timeout')


def teacher_row(**extra):
    return {'matched': False, 'teacher_upload': True, 'first': '', 'last': '', 'section': '', 'email': TEACHER,
            'source': 'form', 'matched_by': None, 'typed': ['kvd'], 'submitted_at': '2026-10-13T15:38:00Z', 'late': False,
            'earlier': [], 'files': [{'id': 'FICTIONAL_TEACHER_EXAMPLE_1', 'name': 'Body tissues example - Teacher',
                                      'mime': google.PPTX, 'url': 'https://drive.google.com/open?id=FICTIONAL_TEACHER_EXAMPLE_1'}],
            **extra}


class TeacherOriginSafetyTests(unittest.TestCase):
    def test_teacher_upload_for_a_new_or_ambiguous_student_keeps_identity_checks(self):
        students, _ = roster.parse_tab(ROSTER, 'forensics', 'A2 Forensics')
        for typed in [('New Student Not Yet Mirrored',), ('Ana',), ('kvd',), ()]:
            submitted = work.submissions([response(TEACHER, '2026-10-13T15:38:00Z',
                ('FICTIONAL_UPLOAD', 'Example deck'), typed=typed)])
            rows, _ = work.group(submitted, students, {'school.example'})
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]['matched'])
            self.assertFalse(rows[0].get('teacher_upload'))
            self.assertEqual(rows[0]['files'][0]['id'], 'FICTIONAL_UPLOAD')

    def test_a_teacher_submission_for_an_identified_student_is_preserved(self):
        students, _ = roster.parse_tab(ROSTER, 'forensics', 'A2 Forensics')
        submitted = work.submissions([response(TEACHER, '2026-10-13T15:38:00Z',
            ('FICTIONAL_UPLOAD', 'Deck'), typed=('Ana Rivera',))])
        rows, _ = work.group(submitted, students, {'school.example'})
        self.assertTrue(rows[0]['matched'])
        self.assertEqual(rows[0]['last'], 'Rivera')
        self.assertFalse(rows[0].get('teacher_upload'))


    def test_allowed_teacher_email_does_not_remove_an_unknown_learner_from_discovery(self):
        students, _ = roster.parse_tab(ROSTER, 'forensics', 'A2 Forensics')
        uploaded = response(TEACHER, '2026-09-22T14:00:00Z', ('NEW_LEARNER', 'Deck'),
                            typed=('New Student Not Yet Mirrored',))
        with patch.object(work.roster, 'load', return_value=({'forensics': students}, [])), \
                patch.object(google, 'form_responses', side_effect=lambda t, fid: [uploaded]
                             if fid == 'FICTIONALFORMID1234567890abc' else []), \
                patch.object(google, 'drive_file', return_value={'name': 'Deck', 'trashed': False, 'mimeType': SLIDES}), \
                patch.object(work, '_now', return_value=datetime(2026, 9, 24, 12, tzinfo=timezone.utc)):
            result = work.lesson_work('token', CONFIG, fixture(), 'forensics', 'Wk 04')
        form = result['lessons'][0]['forms'][0]
        self.assertEqual([r['files'][0]['id'] for r in form['students']], ['NEW_LEARNER'])
        self.assertFalse(form['students'][0]['matched'])
        self.assertNotIn('teacher_uploads', form)
        self.assertEqual(result['roster_check']['flags'][0]['kind'], 'no_roster')
        self.assertIsNone(form['window'])


class ReviewTimeoutTests(unittest.TestCase):
    def test_typed_timeouts_only(self):
        self.assertTrue(google.read_timed_out(timeout()))
        for error in [google.GoogleError("Google didn't answer in time. Try again."),
                      google.GoogleError('denied', 403, 'read_timeout'), ValueError('read_timeout')]:
            self.assertFalse(google.read_timed_out(error))
        self.assertEqual(work._unreadable(timeout())['read_timeout'], True)
        self.assertNotIn('read_timeout', work._unreadable(google.GoogleError('slow')))

    def test_discovery_repeats_once_after_a_timeout_and_uses_the_fresh_result(self):
        with patch.object(work, 'lesson_work', side_effect=[timeout(), found()]) as discovery:
            result = rc._discover('fictional', {}, fixture(), 'forensics', 'Wk 04', complete_roster(), started=rc.time.monotonic())
        self.assertEqual(discovery.call_count, 2)
        self.assertNotIn('read_failure', result)
        self.assertEqual(result['lessons'][0]['forms'][0]['students'][0]['last'], 'Rivera')

    def test_a_repeated_timeout_is_a_retry_state_not_missing_work_or_an_identity_question(self):
        slow = found([row(matched=False, first='', last='', section='')], read_timeouts=['turn_in_folders'],
                     folder_note="The turn-in folders couldn't be read (Google didn't answer in time. Try again.), so only Form uploads were read.")
        with patch.object(work, 'lesson_work', side_effect=[deepcopy(slow), deepcopy(slow), AssertionError('bounded')]) as discovery:
            current = rc._discover('fictional', {}, fixture(), 'forensics', 'Wk 04', complete_roster(), started=rc.time.monotonic())
        self.assertEqual(discovery.call_count, 2)
        self.assertEqual(current['read_failure'], {'reason': 'read_timeout', 'reads': ['turn_in_folders'], 'attempts': 2, 'retry': True})
        result = rc._read_found('fictional', {}, fixture(), scope(), current, roster_snapshot=complete_roster())
        self.assertEqual((result['status'], result['intake_status'], result['teacher_actionable']), ('unavailable', 'unavailable', False))
        self.assertEqual(result['read_failure']['attempts'], 2)
        self.assertEqual(result['source_selection']['submitted_originals'], None)
        detail = result['notices'][0]
        self.assertEqual(detail['affected_scope'], 'lesson')
        self.assertTrue(detail['reason'].startswith('Check timed out — retry.'))
        self.assertIn('the turn-in folders (2 attempts)', detail['reason'])
        self.assertIn('not missing student work', detail['next_action'])
        for claim in ('Confirm the student', 'only Form uploads were read', 'Waiting for submissions'):
            self.assertNotIn(claim, str(result))

    def test_no_repeat_after_the_time_budget_or_for_a_non_timeout_error(self):
        with patch.object(work, 'lesson_work', side_effect=[timeout(), AssertionError('over budget')]) as discovery:
            late = rc._discover('fictional', {}, fixture(), 'forensics', 'Wk 04', complete_roster(),
                                started=rc.time.monotonic() - rc.RETRY_BUDGET_SECONDS)
        self.assertEqual(discovery.call_count, 1)
        self.assertEqual((late['read_failure']['reads'], late['read_failure']['attempts']), (['discovery'], 1))
        denied = google.GoogleError('The dashboard account does not have access.', 403)
        with patch.object(work, 'lesson_work', side_effect=[denied, AssertionError('no repeat')]) as discovery:
            failed = rc._discover('fictional', {}, fixture(), 'forensics', 'Wk 04', complete_roster(), started=rc.time.monotonic())
        self.assertEqual(discovery.call_count, 1)
        self.assertNotIn('read_failure', failed)
        result = rc._read_found('fictional', {}, fixture(), scope(), failed)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('read_failure', result)
        self.assertIn('does not have access', result['notices'][0]['reason'])

    def test_a_timed_out_file_read_never_becomes_a_student_source_request(self):
        source = found([row(files=[{**row()['files'][0], 'problem': "Google didn't answer in time. Try again.", 'read_timeout': True}])])
        result = rc._read_found('fictional', {}, fixture(), scope(), source)
        self.assertEqual((result['status'], result['read_failure']['reads']), ('unavailable', ['file_metadata']))
        self.assertFalse(any(n['affected_scope'] == 'student' for n in result['notices']))
        self.assertNotIn('Open this exact original', str(result))

    def test_overview_keeps_other_lessons_current_when_one_discovery_times_out(self):
        agenda = fixture()
        with patch.object(work.roster, 'load', return_value=complete_roster()), \
                patch.object(work, 'lesson_work', side_effect=lambda *a, **k: (_ for _ in ()).throw(timeout())
                             if a[4] == 'Wk 04' else found()) as discovery, \
                patch.object(rc.morning, 'catalog', return_value={'assignments': [
                    {'class': 'forensics', 'class_name': 'Forensics', 'week': 'Wk 04', 'form': FORM, 'assignment': 'Reasoning'}],
                    'problems': [], 'exclusions': []}):
            result = rc.overview('fictional', {}, agenda, reload_agenda=lambda: agenda)
        self.assertEqual(discovery.call_count, 2)
        self.assertEqual(result['status'], 'current')
        self.assertNotIn('read_failure', result)
        review = result['reviews'][0]
        self.assertEqual((review['status'], review['read_failure']['attempts']), ('unavailable', 2))
        self.assertIn('this lesson’s submissions', review['notices'][0]['reason'])

    def test_final_bookend_timeout_marks_the_overview_for_retry_without_names(self):
        agenda = fixture()
        reloads = []
        def reload():
            reloads.append(1)
            raise timeout()
        def discover(token, config, current, cls, week, **kwargs):
            result = found([row(selection_problem='Needs a revision check')])
            result.update(class_key=cls, week=week)
            result['lessons'][0]['forms'] = [{**result['lessons'][0]['forms'][0], 'form_id': f['form_id']}
                                           for lesson in current.lessons(cls, week) for f in lesson['forms']]
            return result
        with patch.object(work.roster, 'load', return_value=complete_roster()), \
                patch.object(work, 'lesson_work', side_effect=discover):
            result = rc.overview('fictional', {}, agenda, reload_agenda=reload)
        self.assertEqual(len(reloads), 2)
        self.assertEqual((result['status'], result['read_failure']['reads']), ('unavailable', ['final_bookend']))
        self.assertTrue(all(r['read_failure']['reason'] == 'read_timeout' for r in result['reviews']))
        self.assertNotIn('Rivera', str(result))

    def test_single_scope_read_uses_the_same_bounded_retry(self):
        with patch.object(work.roster, 'load', return_value=complete_roster()), \
                patch.object(work, 'lesson_work', side_effect=[timeout(), timeout(), AssertionError('bounded')]) as discovery, \
                patch.object(google, 'service_token', return_value='fictional'), \
                patch.object(app, 'load_agenda', return_value=fixture()):
            result = app.api('api/review-context', {'class': ['forensics'], 'week': ['Wk 04'], 'form': [FORM]}, {})
        self.assertEqual(discovery.call_count, 2)
        self.assertEqual((result['status'], result['read_failure']['retry']), ('unavailable', True))

    def test_lesson_work_records_typed_folder_and_form_timeouts(self):
        students, _ = roster.parse_tab(ROSTER, 'forensics', 'A2 Forensics')
        def responses(token, form_id):
            if form_id == 'FICTIONALFORMID0987654321xyz':
                raise timeout()
            return [response('s.ana.rivera@school.example', '2026-09-22T14:00:00Z', ('ANA', 'Deck'))]
        with patch.object(work.roster, 'load', return_value=({'forensics': students}, [])), \
                patch.object(google, 'form_responses', side_effect=responses), \
                patch.object(work, 'lesson_folders', return_value=({'WEEKFOLDER': 'Wk 04'}, None)), \
                patch.object(google, 'folder_children', side_effect=timeout()), \
                patch.object(google, 'drive_file', return_value={'name': 'Deck', 'trashed': False, 'mimeType': SLIDES}):
            result = work.lesson_work('token', CONFIG, fixture(), 'forensics', 'Wk 04', review_only=True,
                                      roster_snapshot=({'forensics': students}, []))
        self.assertEqual(result['read_timeouts'], ['form_responses', 'turn_in_folders'])
        self.assertIn("Google didn't answer in time", result['folder_note'])


class FeedbackSourceTimeoutTests(unittest.TestCase):
    saved = {'schema': 'x', 'configured': True, 'status': 'ready', 'date': '2026-10-11', 'pdf_url': '/saved'}

    def test_a_catalog_timeout_is_read_once_more_then_continues_normally(self):
        with patch.object(native_feedback, '_feedback_files', side_effect=[timeout(), {'configured': False, 'files': [], 'problem': 'Not connected.'}]) as catalog:
            result = native_feedback.today_files('token', {}, date_et='2026-10-14')
        self.assertEqual(catalog.call_count, 2)
        self.assertEqual((result['configured'], result['date']), (False, '2026-10-14'))

    def test_a_repeated_timeout_keeps_the_saved_document_and_claims_nothing_new_or_printed(self):
        with patch.object(native_feedback, '_feedback_files', side_effect=[timeout(), timeout(), AssertionError('bounded')]) as catalog, \
                patch.object(daily_feedback_packet, 'status', return_value=self.saved):
            result = native_feedback.today_files('token', {}, date_et='2026-10-14')
        self.assertEqual(catalog.call_count, 2)
        self.assertEqual(result['source_check'], {'status': 'timed_out', 'reason': 'read_timeout', 'attempts': 2, 'retry': True})
        self.assertEqual((result['date'], result['requested_date'], result['files']), ('2026-10-11', '2026-10-14', []))
        self.assertIs(result['current_sources_verified'], False)
        self.assertEqual(result['daily_packet'], self.saved)
        self.assertNotIn('pending_files', result)
        self.assertNotIn('delivery', result)
        self.assertIn('timed out (2 attempts)', result['source_problem'])

    def test_a_timeout_without_a_saved_document_still_returns_a_retry_state(self):
        for status in [{'configured': True, 'status': 'not_saved', 'date': '2026-10-14'},
                       google.GoogleError("Google didn't answer in time.", reason='read_timeout')]:
            with patch.object(native_feedback, '_feedback_files', side_effect=timeout()), \
                    patch.object(daily_feedback_packet, 'status', side_effect=[status] if isinstance(status, Exception) else None,
                                 return_value=status):
                result = native_feedback.today_files('token', {}, date_et='2026-10-14')
            self.assertEqual((result['date'], result['source_check']['status']), ('2026-10-14', 'timed_out'))

    def test_other_source_failures_keep_their_existing_behaviour(self):
        denied = google.GoogleError('denied', 403)
        with patch.object(native_feedback, '_feedback_files', side_effect=[denied, AssertionError('no repeat')]), \
                patch.object(daily_feedback_packet, 'status', return_value={'status': 'not_saved', 'date': '2026-10-14'}):
            with self.assertRaises(google.GoogleError):
                native_feedback.today_files('token', {}, date_et='2026-10-14')
        with patch.object(native_feedback, '_feedback_files', side_effect=denied), \
                patch.object(daily_feedback_packet, 'status', return_value=self.saved):
            result = native_feedback.today_files('token', {}, date_et='2026-10-14')
        self.assertEqual(result['source_problem'], 'denied')
        self.assertNotIn('source_check', result)

    def test_printing_refuses_a_timed_out_source_check(self):
        with patch.object(native_feedback, '_feedback_files', side_effect=timeout()), \
                patch.object(daily_feedback_packet, 'status', return_value=self.saved), \
                patch.object(google, 'printable_pdf_bytes', side_effect=AssertionError('no print')):
            with self.assertRaisesRegex(native_feedback.Held, 'timed out'):
                native_feedback.print_today('token', {}, ['FictionalFeedback123'], date_et='2026-10-14')


if __name__ == '__main__':
    unittest.main()
