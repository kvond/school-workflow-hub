"""Synthetic end-to-end composition and authenticated transport boundary."""
import copy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.adapters.teaching_results import read_teaching_results
from school_workflow_hub.core.models import AssignmentRecord, EnrollmentRecord, SubmissionRevision
from school_workflow_hub.web import application
from school_workflow_hub.workflows.due_completion import (
    DueAssignment, FolderScan, FolderSubmission, WorkAssessment, process_assignment,
)
from school_workflow_hub.workflows.student_feedback import build_feedback_batch
from school_workflow_hub.workflows.teaching_results import build_teaching_results
from tests.test_student_feedback import case

AGENDA_AT = '2026-09-01T10:00:00-04:00'
CHECKED_AT = '2026-09-01T11:00:00-04:00'
CONFIG = {'HUB_IAP_AUDIENCE': '/projects/123/locations/us-central1/services/synthetic',
          'HUB_ALLOWED_EMAILS': 'teacher@example.invalid'}


def composition_inputs():
    c = case()
    scope = DueAssignment(AssignmentRecord(c.assignment_key, c.class_key, 'Fictional food chains',
                                          '2026-08-31'), 'folder:one', 'mapping:one', '2026-08-31',
                          c.stage, c.readiness, {'content_notes': 'ic:one'})
    fact = FolderSubmission(SubmissionRevision('doc:one', c.assignment_key, c.learner_key,
                                               c.submission_revision, CHECKED_AT),
                            '2026-08-31T10:00:00-04:00', 'e:current')
    scan = FolderScan(scope.folder_id, c.assignment_key, 'snapshot:one', CHECKED_AT, (fact,), True,
                      (EnrollmentRecord(c.class_key, c.learner_key),), True)
    completion = process_assignment(scope, as_of=CHECKED_AT, scan_folder=lambda *_: scan,
                                    assess_work=lambda *_: WorkAssessment('doc:one', c.submission_revision,
                                                                         c.readiness, c.responses))
    return dict(class_key='biology', class_stable_id=c.class_key, assignment_key=c.assignment_key,
                day='2026-09-01', agenda_checked_at=AGENDA_AT, checked_at=CHECKED_AT,
                source_revision='snapshot:one', provenance='synthetic', completion_result=completion,
                feedback_batches=(build_feedback_batch([c], batch_key='batch:one'),),
                expected_submitted_learners=(c.learner_key,), expected_batch_keys=('batch:one',))


def payload():
    return build_teaching_results(**composition_inputs())


class Provider:
    def __init__(self, value):
        self.value = value
        self.reads = 0

    def read_results(self, **kwargs):
        self.reads += 1
        return self.value


class TeachingResultsTests(unittest.TestCase):
    def test_due_to_feedback_to_summary_same_revision(self):
        result = payload()
        self.assertEqual(result['completion']['rows'][0]['completion']['scores']['content_notes']['value'], 10)
        self.assertEqual(result['completion']['rows'][0]['display_name'], 'Alex F.')
        self.assertEqual(len(result['feedback']['reports'][0]['sections']), 5)
        self.assertEqual(result['summary']['participation']['decks_in'], 1)
        self.assertTrue(all(v is False for v in result['capabilities'].values()))

    def test_join_rejects_stale_feedback_and_wrong_lesson(self):
        for key, value in (('submission_revision', 'old'), ('assignment_key', 'wrong')):
            args = composition_inputs()
            args['feedback_batches'][0]['compiled_records'][0][key] = value
            with self.assertRaises(ValueError):
                build_teaching_results(**args)

    def test_compiled_only_needs_no_deck_or_report(self):
        args = composition_inputs()
        args['compiled_records'] = args.pop('feedback_batches')[0]['compiled_records']
        result = build_teaching_results(**args)
        self.assertEqual(result['feedback']['reports'], [])
        self.assertEqual(result['summary']['participation']['decks_in'], 1)

    def test_authentication_precedes_private_provider_read(self):
        provider = Provider(payload())
        with patch('school_workflow_hub.adapters.teaching_results.authorize', side_effect=ConnectionError('sign_in_required')):
            with self.assertRaisesRegex(ConnectionError, 'sign_in_required'):
                self.read(provider)
        self.assertEqual(provider.reads, 0)

    def read(self, provider):
        return read_teaching_results('synthetic-assertion', day='2026-09-01', class_key='biology',
                                     agenda_checked_at=AGENDA_AT, provider=provider, config=CONFIG)

    def test_no_provider_never_loads_fixture(self):
        with patch('school_workflow_hub.adapters.teaching_results.authorize'):
            with self.assertRaisesRegex(ConnectionError, 'teaching_results_not_connected'):
                self.read(None)

    def test_http_rejects_synthetic_stale_scope_and_enabled_actions(self):
        for mutate in (lambda d: None,
                       lambda d: d.update(provenance='source_read', agenda_checked_at='2026-08-01T10:00:00-04:00'),
                       lambda d: (d.update(provenance='source_read'), d['capabilities'].update(run_enabled=True))):
            data = payload(); mutate(data)
            with patch('school_workflow_hub.adapters.teaching_results.authorize'):
                with self.assertRaises(ConnectionError):
                    self.read(Provider(data))

    def test_provider_exception_does_not_expose_private_error(self):
        class Bad:
            def read_results(self, **kwargs):
                raise RuntimeError('private student source body')
        with patch('school_workflow_hub.adapters.teaching_results.authorize'):
            with self.assertRaisesRegex(ConnectionError, '^teaching_results_unavailable$'):
                self.read(Bad())

    def test_http_success_is_no_store_and_never_executes(self):
        data = payload(); data['provenance'] = 'source_read'  # Mock transport proof only.
        captured = {}
        def start(status, headers):
            captured.update(status=status, headers=dict(headers))
        with patch.dict('os.environ', CONFIG), patch('school_workflow_hub.adapters.teaching_results.authorize'):
            body = b''.join(application({'PATH_INFO':'/api/teaching-results','REQUEST_METHOD':'GET',
                'HTTP_X_GOOG_IAP_JWT_ASSERTION':'synthetic-assertion','QUERY_STRING':urlencode({
                    'date':'2026-09-01','class_key':'biology','agenda_checked_at':AGENDA_AT})},
                start, teaching_provider=Provider(data)))
        self.assertEqual(captured['status'], '200 OK')
        self.assertEqual(captured['headers']['Cache-Control'], 'no-store')
        self.assertEqual(json.loads(body)['external_writes'], 0)

    def test_invalid_and_duplicate_query_denied(self):
        for query in ('date=2026-09-01', 'date=2026-09-01&class_key=biology&agenda_checked_at=x&date=2026-09-02'):
            captured = []
            application({'PATH_INFO':'/api/teaching-results','REQUEST_METHOD':'GET','QUERY_STRING':query},
                        lambda status, headers: captured.append(status))
            self.assertEqual(captured, ['400 Bad Request'])

    def test_conflicting_completion_assessment_not_shown_as_same_result(self):
        args = composition_inputs()
        args['feedback_batches'][0]['compiled_records'][0]['completion']['NOTES']['completed'] = 0
        with self.assertRaisesRegex(ValueError, 'completion_feedback_assessment_conflict'):
            build_teaching_results(**args)
