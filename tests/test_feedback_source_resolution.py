"""Fictional retained source captures for repeated feedback reads (#240).

No provider, school source or writer calls. Export counts are asserted, not
only output shapes.
"""
from collections import Counter
from copy import deepcopy
from email.utils import formatdate
import hashlib
import threading
import unittest
from unittest.mock import patch

from hub_v2 import app, combined_assessment as combined, completion, feedback_source_resolution as retry, google
from test_combined_assessment import captures, declaration, event, form, T1, T2, S1

TOKEN = 'fictional-token'


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RetainedSourcesTests(unittest.TestCase):
    def setUp(self):
        self.values = captures()
        self.students = ['FICTIONAL_RETAINED_COPY_%02d' % i for i in range(12)]
        for fid in self.students:
            self.values[fid] = deepcopy(self.values[S1])
            self.values[fid]['metadata'].update(id=fid, mimeType=completion.SLIDES)
        for teacher in (T1, T2):
            self.values[teacher]['metadata']['mimeType'] = completion.SLIDES
        self.metadata_calls, self.exports = Counter(), Counter()
        self.lock = threading.Lock()
        self.quota = None
        self.clock = Clock()
        self.store = combined.RetainedSources(clock=self.clock)
        self.addCleanup(patch.stopall)

        def metadata(_token, fid, fields):
            self.assertEqual(fields, combined.SOURCE_FIELDS)
            with self.lock:
                self.metadata_calls[fid] += 1
            return deepcopy(self.values[fid]['metadata'])

        def raw(_token, fid, _mime):
            with self.lock:
                if self.quota is not None:
                    if self.quota <= 0:
                        raise google.GoogleError('Google is rate-limiting requests.', 429)
                    self.quota -= 1
                self.exports[fid] += 1
            return fid.encode()

        self.metadata = patch.object(google, 'drive_file', side_effect=metadata).start()
        self.raw = patch.object(google, 'deck_bytes', side_effect=raw).start()
        patch.object(completion, 'read_deck',
                     side_effect=lambda raw: deepcopy(self.values[raw.decode()]['deck'])).start()
        patch.object(google, 'get_json', side_effect=AssertionError('No provider calls in fictional tests')).start()

    def assignment(self):
        value = form()
        value['students'] = [event(fid, first='Student%d' % i, last='Example',
                                   email='s.student%d.example@fictional.example' % i)
                             for i, fid in enumerate(self.students)]
        return value

    def bind(self, store=None):
        value = self.assignment()
        if store is None:
            combined.bind_form(TOKEN, value, declaration())
        else:
            with combined.retained_sources(store):
                combined.bind_form(TOKEN, value, declaration())
        return value

    def read(self, fid, token=TOKEN):
        with combined.retained_sources(self.store):
            return combined.read_source(token, fid)

    def test_repeated_whole_form_reads_hit_quota_without_retention(self):
        # Former path: every listing, draft and save exports every original.
        self.quota = len(self.students) + 2
        first = self.bind()
        self.assertIsNone(first['combined_assessment']['problem'])
        second = self.bind()
        self.assertIn('rate-limiting', second['combined_assessment']['problem'])
        self.assertEqual(second['grade_readiness']['code'], 'coverage_incomplete')

    def test_retained_reads_export_once_and_fresh_check_every_reuse(self):
        self.quota = len(self.students) + 2
        first = self.bind(self.store)
        self.assertEqual(self.exports, Counter({fid: 1 for fid in self.students + [T1, T2]}))
        calls = sum(self.metadata_calls.values())
        second = self.bind(self.store)
        third = self.bind(self.store)
        for value in (second, third):
            self.assertIsNone(value['combined_assessment']['problem'])
            self.assertEqual(value['combined_assessment']['sources'], first['combined_assessment']['sources'])
            self.assertEqual(value['combined_assessment']['teachers'], first['combined_assessment']['teachers'])
        self.assertEqual(self.exports, Counter({fid: 1 for fid in self.students + [T1, T2]}))
        # Each reuse still reads current metadata before and after.
        self.assertGreaterEqual(sum(self.metadata_calls.values()) - calls, 2 * 2 * (len(self.students) + 2))

    def test_changed_metadata_re_exports_and_never_serves_the_old_capture(self):
        fid = self.students[0]
        first = self.read(fid)
        for field, new in [('version', '99'), ('modifiedTime', '2026-10-09T12:00:00Z'),
                           ('parents', ['FictionalOtherFolder01']), ('properties', {'moved': 'yes'})]:
            with self.subTest(field=field):
                self.values[fid]['metadata'][field] = new
                self.values[fid]['deck'] = {'slides': [{'units': [{'text': field}]}]}
                again = self.read(fid)
                self.assertEqual(again['metadata'][field], new)
                self.assertEqual(again['deck'], self.values[fid]['deck'])
                self.assertNotEqual(again['deck'], first['deck'])
        self.assertEqual(self.exports[fid], 5)

    def test_change_during_reuse_holds_and_evicts(self):
        fid = self.students[0]
        self.read(fid)
        counter = [0]
        original = self.metadata.side_effect

        def changes_after_first(token, file_id, fields):
            counter[0] += 1
            value = original(token, file_id, fields)
            if counter[0] == 2:
                value['version'] = '100'
            return value

        self.metadata.side_effect = changes_after_first
        with self.assertRaises(combined.Held):
            self.read(fid)
        self.metadata.side_effect = original
        self.read(fid)
        self.assertEqual(self.exports[fid], 2)

    def test_unreadable_trashed_or_unsupported_source_is_never_hidden(self):
        fid = self.students[0]
        self.read(fid)
        for change in ({'trashed': True}, {'mimeType': 'application/pdf'}, {'version': ''}):
            with self.subTest(change=change):
                saved = deepcopy(self.values[fid]['metadata'])
                self.values[fid]['metadata'].update(change)
                with self.assertRaises(combined.Held):
                    self.read(fid)
                self.values[fid]['metadata'] = saved
        self.metadata.side_effect = google.GoogleError('Actual permission denied', 403, 'insufficientFilePermissions')
        with self.assertRaises(google.GoogleError):
            self.read(fid)
        self.assertEqual(self.exports[fid], 1)

    def test_reader_identity_scopes_every_capture(self):
        fid = self.students[0]
        self.read(fid)
        self.read(fid, token='other-fictional-token')
        self.read(fid)
        self.assertEqual(self.exports[fid], 2)
        self.assertFalse(any(TOKEN in str(key) for key in self.store.values))

    def test_office_bytes_are_retained_only_with_the_provider_checksum(self):
        fid = self.students[0]
        self.values[fid]['metadata']['mimeType'] = google.PPTX
        self.read(fid); self.read(fid)
        self.assertEqual(self.exports[fid], 2)
        self.values[fid]['metadata'].update(md5Checksum=hashlib.md5(fid.encode()).hexdigest(),
                                            size=str(len(fid.encode())))
        self.read(fid); self.read(fid)
        self.assertEqual(self.exports[fid], 3)

    def test_expiry_and_entry_bound(self):
        fid = self.students[0]
        self.read(fid)
        self.clock.now += combined.RETAIN_SECONDS
        self.read(fid)
        self.assertEqual(self.exports[fid], 2)
        small = combined.RetainedSources(entries=3, clock=self.clock)
        with combined.retained_sources(small):
            for i, student in enumerate(self.students[:5]):
                self.clock.now += 1
                combined.read_source(TOKEN, student)
        self.assertEqual(len(small.values), 3)
        self.assertEqual({key[1] for key in small.values}, set(self.students[2:5]))

    def test_captures_cannot_be_mutated_through_a_returned_copy(self):
        fid = self.students[0]
        self.read(fid)['deck']['slides'].clear()
        self.assertEqual(self.read(fid)['deck'], self.values[fid]['deck'])

    def test_identical_concurrent_reads_share_one_export(self):
        fid = self.students[0]
        started, release = threading.Event(), threading.Event()
        raw = self.raw.side_effect

        def slow(token, file_id, mime):
            started.set()
            release.wait(timeout=3)
            return raw(token, file_id, mime)

        self.raw.side_effect = slow
        results, errors = [], []

        def worker():
            try:
                results.append(self.read(fid))
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        threads[0].start()
        started.wait(timeout=3)
        for thread in threads[1:]:
            thread.start()
        release.set()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 4)
        self.assertEqual(self.exports[fid], 1)
        self.assertEqual(self.store.flights, {})

    def test_request_scope_bookend_still_checks_retained_reuse(self):
        fid = self.students[0]
        self.read(fid)
        with self.assertRaises(combined.Held):
            with combined.retained_sources(self.store), combined.source_request():
                combined.read_source(TOKEN, fid)
                self.values[fid]['metadata']['version'] = '101'
        self.assertEqual(self.exports[fid], 1)

    def test_no_store_keeps_the_former_export_per_read(self):
        fid = self.students[0]
        combined.read_source(TOKEN, fid)
        combined.read_source(TOKEN, fid)
        self.assertEqual(self.exports[fid], 2)

    def test_app_feedback_scope_is_one_shared_store(self):
        with app.feedback_sources():
            first = combined._retained_store.get()
        with app.feedback_sources():
            self.assertIs(combined._retained_store.get(), first)
        self.assertIsNone(combined._retained_store.get())
        self.assertIsInstance(first, combined.RetainedSources)

    def test_feedback_source_export_retries_429_then_checks_source_bookend(self):
        fid = self.students[0]
        raw, calls = self.raw.side_effect, []
        budget = retry.ReadBudget(clock=self.clock, sleep=calls.append)

        def limited_once(*args):
            if not calls:
                raise google.GoogleError('Limited', 429, retry_after='1')
            return raw(*args)

        self.raw.side_effect = limited_once
        with patch.object(retry, 'ReadBudget', return_value=budget):
            self.read(fid)
        self.assertEqual(calls, [1.0])
        self.assertEqual(self.raw.call_count, 2)
        self.assertEqual(self.metadata_calls[fid], 2)
        self.assertEqual(self.exports[fid], 1)

    def test_revision_change_during_export_retry_still_holds_and_is_not_retained(self):
        fid = self.students[0]

        def change(_delay):
            self.values[fid]['metadata']['version'] = '99'

        budget = retry.ReadBudget(clock=self.clock, sleep=change)
        raw, count = self.raw.side_effect, [0]

        def limited_once(*args):
            count[0] += 1
            if count[0] == 1:
                raise google.GoogleError('Limited', 429)
            return raw(*args)

        self.raw.side_effect = limited_once
        with patch.object(retry, 'ReadBudget', return_value=budget):
            with self.assertRaisesRegex(combined.Held, 'changed'):
                self.read(fid)
        self.assertEqual(self.store.values, {})

    def test_non_feedback_sources_do_not_retry(self):
        self.raw.side_effect = google.GoogleError('Limited', 429)
        with self.assertRaises(google.GoogleError):
            combined.read_source(TOKEN, self.students[0])
        self.assertEqual(self.raw.call_count, 1)


class SourceReadRetryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.delays = []
        self.budget = retry.ReadBudget(clock=self.clock, wall_clock=lambda: 1_000_000, sleep=self.delays.append)

    def operation(self, errors):
        from unittest.mock import Mock
        return Mock(side_effect=errors)

    def test_retry_after_seconds_and_http_date_are_honored(self):
        for header in ('1', formatdate(1_000_001, usegmt=True)):
            self.setUp()
            operation = self.operation([google.GoogleError('Limited', 429, retry_after=header), 'read'])
            with retry.source_reads(self.budget):
                self.assertEqual(retry.read(operation), 'read')
            self.assertEqual(self.delays, [1.0])
            self.assertEqual(operation.call_count, 2)

    def test_missing_retry_after_uses_two_bounded_delays_and_precise_hold(self):
        operation = self.operation([google.GoogleError('Limited', 429)] * 3)
        with retry.source_reads(self.budget), self.assertRaisesRegex(google.GoogleError, 'feedback source reads') as held:
            retry.read(operation)
        self.assertEqual(held.exception.status, 429)
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(self.delays, [.5, 1.0])

    def test_long_invalid_or_unbounded_guidance_holds_without_early_retry(self):
        headers = ('3', '99999999999999999999999999999999999999999999', 'not a date',
                   '', '-1', '0.5', 'Fri, 21 Nov 1997 09:55:06', 1, '1' * 101,
                   formatdate(1_000_003, usegmt=True))
        for header in headers:
            with self.subTest(header=header):
                operation = self.operation([google.GoogleError('Limited', 429, retry_after=header)])
                with retry.source_reads(self.budget), self.assertRaises(google.GoogleError):
                    retry.read(operation)
                self.assertEqual(operation.call_count, 1)
        self.assertEqual(self.delays, [])

    def test_other_errors_never_retry_and_scopes_are_reset(self):
        for status, reason in ((403, 'userRateLimitExceeded'), (500, None), (None, 'read_timeout')):
            with self.subTest(status=status):
                operation = self.operation([google.GoogleError('Unavailable', status, reason)])
                with retry.source_reads(self.budget), self.assertRaises(google.GoogleError):
                    retry.read(operation)
                self.assertEqual(operation.call_count, 1)
        self.assertIsNone(retry._budget.get())
        operation = self.operation([google.GoogleError('Limited', 429)])
        with self.assertRaises(google.GoogleError):
            retry.read(operation)
        self.assertEqual(operation.call_count, 1)

    def test_nested_scopes_and_concurrent_workers_share_one_total_budget(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextvars import copy_context
        failures, calls = [], []
        lock = threading.Lock()

        def operation():
            with lock:
                calls.append(True)
            raise google.GoogleError('Limited', 429)

        with retry.source_reads(self.budget), retry.source_reads():
            with ThreadPoolExecutor(max_workers=4) as pool:
                jobs = [pool.submit(copy_context().run, retry.read, operation) for _ in range(6)]
                for job in jobs:
                    try:
                        job.result()
                    except google.GoogleError as error:
                        failures.append(error)
        self.assertTrue(failures)
        self.assertEqual(len(calls), 8)
        self.assertEqual(sorted(self.delays), [.5, 1.0])

    def test_expired_request_budget_never_retries(self):
        self.clock.now += retry.MAX_ELAPSED_SECONDS
        operation = self.operation([google.GoogleError('Limited', 429)])
        with retry.source_reads(self.budget), self.assertRaises(google.GoogleError):
            retry.read(operation)
        self.assertEqual(operation.call_count, 1)
        self.assertEqual(self.delays, [])

    def test_oversleep_does_not_start_a_read_after_the_request_deadline(self):
        self.clock.now += retry.MAX_ELAPSED_SECONDS - .6

        def oversleep(delay):
            self.clock.now += delay + .2

        self.budget.sleep = oversleep
        operation = self.operation([google.GoogleError('Limited', 429), 'too late'])
        with retry.source_reads(self.budget), self.assertRaisesRegex(google.GoogleError, 'feedback source reads'):
            retry.read(operation)
        self.assertEqual(operation.call_count, 1)

    def test_provider_error_preserves_only_retry_header_and_typed_reason(self):
        from types import SimpleNamespace
        response = SimpleNamespace(status_code=429, headers={'Retry-After': '2'},
            json=lambda: {'error': {'errors': [{'reason': 'rateLimitExceeded'}]}})
        error = google._response_error(response)
        self.assertEqual((error.status, error.reason, error.retry_after), (429, 'rateLimitExceeded', '2'))
        self.assertNotIn('2', str(error))


if __name__ == '__main__':
    unittest.main()
