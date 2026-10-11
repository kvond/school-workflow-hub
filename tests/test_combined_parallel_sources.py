"""Fictional parallel source reads; no provider, school source or writer calls."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hub_v2 import combined_assessment as combined, completion, google
from test_combined_assessment import captures, declaration, event, form, T1, T2, S1


class ParallelSourcesTests(unittest.TestCase):
    def setUp(self):
        self.values = captures()
        self.students = ['FICTIONAL_PARALLEL_COPY_%02d' % i for i in range(8)]
        for fid in self.students:
            self.values[fid] = deepcopy(self.values[S1])
            self.values[fid]['metadata'].update(id=fid, mimeType=completion.SLIDES)
        for teacher in (T1, T2):
            self.values[teacher]['metadata']['mimeType'] = completion.SLIDES
        self.metadata_calls, self.exports = Counter(), Counter()
        self.lock = threading.Lock()
        self.current_phase = 'read'
        self.seen_contexts = []
        self.addCleanup(patch.stopall)

        def metadata(_token, fid, fields):
            self.assertEqual(fields, combined.SOURCE_FIELDS)
            with self.lock:
                self.metadata_calls[fid] += 1
                self.seen_contexts.append(combined._request_captures.get())
            return deepcopy(self.values[fid]['metadata'])

        def raw(_token, fid, _mime):
            with self.lock:
                self.exports[fid] += 1
            return fid.encode()

        self.metadata = patch.object(google, 'drive_file', side_effect=metadata).start()
        self.raw = patch.object(google, 'deck_bytes', side_effect=raw).start()
        patch.object(completion, 'read_deck', side_effect=lambda raw: deepcopy(self.values[raw.decode()]['deck'])).start()
        patch.object(google, 'get_json', side_effect=AssertionError('No provider calls in fictional tests')).start()

    def assignment(self, ids=None):
        value = form()
        value['students'] = [event(fid, first='Student%d' % i, last='Example',
                                   email='s.student%d.example@fictional.example' % i)
                             for i, fid in enumerate(ids or self.students)]
        return value

    def test_binding_reads_four_independent_students_concurrently_and_retains_order(self):
        barrier = threading.Barrier(4)
        active = peak = 0
        raw = self.raw.side_effect

        def concurrent(token, fid, mime):
            nonlocal active, peak
            if fid in self.students:
                with self.lock:
                    active += 1
                    peak = max(peak, active)
                try:
                    barrier.wait(timeout=3)
                    return raw(token, fid, mime)
                finally:
                    with self.lock:
                        active -= 1
            return raw(token, fid, mime)

        self.raw.side_effect = concurrent
        value = self.assignment(list(reversed(self.students)))
        with combined.source_request():
            context = combined._request_captures.get()
            combined.bind_form('fictional-token', value, declaration())
            self.assertIsNone(value['combined_assessment']['problem'])
            self.assertEqual(list(value['combined_assessment']['sources']), sorted(self.students))
            self.assertEqual([r['first'] for r in combined.candidates(value)],
                             [r['first'] for r in value['students']])
        self.assertEqual(peak, 4)
        self.assertEqual(active, 0)
        self.assertEqual(self.exports, Counter({fid: 1 for fid in self.students + [T1, T2]}))
        self.assertTrue(all(capture is context for capture in self.seen_contexts))
        self.assertIsNone(combined._request_captures.get())

    def test_result_order_and_partial_bound_state_match_serial_failure_order(self):
        ids = self.students[:4]
        failure = ids[2]
        original = self.raw.side_effect

        def failing(token, fid, mime):
            if fid == failure:
                with self.lock:
                    self.exports[fid] += 1
                raise google.GoogleError('Fictional access denied', 403, 'insufficientFilePermissions')
            return original(token, fid, mime)

        self.raw.side_effect = failing
        value = self.assignment(list(reversed(ids)))
        with combined.source_request():
            combined.bind_form('fictional-token', value, declaration())
            self.assertEqual(list(value['combined_assessment']['sources']), ids[:2])
            self.assertEqual(value['combined_assessment']['problem'], 'Fictional access denied')
            self.assertTrue(all(r['selection_problem'] == 'Fictional access denied'
                                for r in combined.candidates(value)))
        self.assertEqual(self.exports[failure], 1)

    def test_same_source_jobs_share_one_capture_but_every_reuse_is_fresh_checked(self):
        fid = self.students[0]
        with combined.source_request():
            with combined._source_results(lambda source: combined.read_source('fictional-token', source),
                                          [fid] * 4) as results:
                values = list(results)
            self.assertEqual(len(values), 4)
            self.assertEqual(self.metadata_calls[fid], 8)
            values[0]['deck']['slides'].clear()
            self.assertTrue(values[1]['deck']['slides'])
        self.assertEqual(self.exports[fid], 1)
        self.assertEqual(self.metadata_calls[fid], 9)

    def test_worker_threads_use_distinct_sessions_and_restore_context_after_jobs(self):
        barrier = threading.Barrier(4)
        sessions = {}
        contexts = []
        original_session = google._session

        def reader(fid):
            before = original_session()
            barrier.wait(timeout=3)
            self.assertIs(before, original_session())
            self.assertIs(before.trust_env, False)
            with self.lock:
                sessions[threading.get_ident()] = before
                contexts.append(combined._request_captures.get())
            return fid

        with patch.object(google, '_local', threading.local()), \
                patch('requests.Session', side_effect=lambda: SimpleNamespace(trust_env=True)):
            with combined.source_request():
                context = combined._request_captures.get()
                with combined._source_results(reader, self.students) as results:
                    self.assertEqual(list(results), self.students)
            self.assertEqual(len(sessions), 4)
            self.assertEqual(len({id(s) for s in sessions.values()}), 4)
            self.assertTrue(all(capture is context for capture in contexts))
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertIsNone(pool.submit(combined._request_captures.get).result())
        self.assertIsNone(combined._request_captures.get())

    def test_overlapping_requests_never_share_tokens_or_capture_cache(self):
        barrier = threading.Barrier(2)

        def request(token):
            with combined.source_request():
                context = combined._request_captures.get()
                barrier.wait(timeout=3)
                with combined._source_results(lambda fid: combined.read_source(token, fid),
                                              self.students[:4]) as results:
                    self.assertEqual(len(list(results)), 4)
                self.assertEqual(context.token, token)
            self.assertIsNone(combined._request_captures.get())
            return context

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(request, 'fictional-token-a')
            b = pool.submit(request, 'fictional-token-b')
            self.assertIsNot(a.result(), b.result())
        self.assertEqual(self.exports, Counter({fid: 2 for fid in self.students[:4]}))

    def test_foreign_token_inside_parallel_job_stays_held_without_export(self):
        with self.assertRaises(combined.Held):
            with combined.source_request():
                combined.read_source('fictional-token', self.students[0])
                with combined._source_results(lambda fid: combined.read_source('foreign-token', fid),
                                              self.students[1:4]) as results:
                    list(results)
        self.assertEqual(sum(self.exports.values()), 1)
        self.assertIsNone(combined._request_captures.get())

    def test_final_bookend_is_parallel_and_finishes_before_the_writer(self):
        ids = self.students[:4]
        barrier = threading.Barrier(4)
        original = self.metadata.side_effect
        active = peak = 0

        def metadata(token, fid, fields):
            nonlocal active, peak
            if self.current_phase == 'bookend':
                with self.lock:
                    active += 1
                    peak = max(peak, active)
                try:
                    barrier.wait(timeout=3)
                    return original(token, fid, fields)
                finally:
                    with self.lock:
                        active -= 1
            return original(token, fid, fields)

        self.metadata.side_effect = metadata
        with combined.source_request():
            for fid in ids:
                combined.read_source('fictional-token', fid)
            self.current_phase = 'bookend'
        # Represents the endpoint's record() position, outside the source scope.
        self.assertEqual(active, 0)
        self.assertEqual(peak, 4)
        self.assertIsNone(combined._request_captures.get())
        self.assertEqual(self.metadata_calls, Counter({fid: 3 for fid in ids}))

    def test_final_metadata_change_or_permission_loss_refuses_before_any_writer(self):
        ids = self.students[:4]
        for issue in ('version', 'parents', 'properties', 'permission'):
            self.current_phase = 'read'
            original = self.metadata.side_effect
            appended = []

            def metadata(token, fid, fields):
                value = original(token, fid, fields)
                if self.current_phase == 'bookend' and fid == ids[-1]:
                    if issue == 'permission':
                        raise google.GoogleError('Fictional permission loss', 403, 'insufficientFilePermissions')
                    value[issue] = {'version': '8', 'parents': ['ForeignFolder'],
                                    'properties': {'foreign': 'value'}}[issue]
                return value

            self.metadata.side_effect = metadata
            try:
                with self.subTest(issue=issue), self.assertRaises((combined.Held, google.GoogleError)):
                    with combined.source_request():
                        for fid in ids:
                            combined.read_source('fictional-token', fid)
                        self.current_phase = 'bookend'
                    appended.append('write')
                self.assertEqual(appended, [])
                self.assertIsNone(combined._request_captures.get())
            finally:
                self.metadata.side_effect = original

    def test_original_source_limit_refuses_before_parallel_student_reads(self):
        value = self.assignment(['FICTIONAL_OVER_LIMIT_%03d' % i
                                 for i in range(combined.MAX_SOURCES + 1)])
        with combined.source_request():
            combined.bind_form('fictional-token', value, declaration())
        self.assertIn('too many source', value['combined_assessment']['problem'])
        self.assertEqual(set(self.exports), {T1, T2})

    def test_teachers_stay_one_export_with_every_row_reuse_and_final_fresh_checks(self):
        value = self.assignment(self.students[:1])
        with combined.source_request():
            combined.bind_form('fictional-token', value, declaration())
            row = combined.candidates(value)[0]
            for _ in range(3):
                combined.verify_sources('fictional-token', value, row)
        for fid in (T1, T2, self.students[0]):
            self.assertEqual(self.exports[fid], 1)
            self.assertEqual(self.metadata_calls[fid], 9)

    def test_classification_error_joins_inflight_source_workers_before_returning_hold(self):
        ids = self.students[:4]
        entered, release, refused = threading.Event(), threading.Event(), threading.Event()
        barrier = threading.Barrier(4)
        original = self.raw.side_effect

        def raw(token, fid, mime):
            if fid in ids:
                barrier.wait(timeout=3)
                if fid != ids[0]:
                    entered.set()
                    self.assertTrue(release.wait(timeout=3))
            return original(token, fid, mime)

        self.raw.side_effect = raw

        def classification(capture, teachers):
            refused.set()
            raise combined.Held('Fictional ambiguous source cycle')

        def request():
            value = self.assignment(ids)
            with combined.source_request():
                combined.bind_form('fictional-token', value, declaration())
            self.assertIsNone(combined._request_captures.get())
            return value

        with patch.object(combined, 'classify', side_effect=classification), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(request)
            try:
                self.assertTrue(entered.wait(timeout=3))
                self.assertTrue(refused.wait(timeout=3))
                self.assertFalse(future.done())
            finally:
                release.set()
            value = future.result(timeout=3)
        self.assertEqual(value['combined_assessment']['problem'], 'Fictional ambiguous source cycle')
        self.assertEqual(value['combined_assessment']['sources'], {})
        self.assertEqual(self.exports, Counter({fid: 1 for fid in ids + [T1, T2]}))

    def test_bookend_error_joins_other_metadata_workers_before_scope_can_refuse(self):
        ids = self.students[:4]
        entered, release, refused = threading.Event(), threading.Event(), threading.Event()
        original = self.metadata.side_effect

        def metadata(token, fid, fields):
            if self.current_phase == 'bookend':
                if fid == ids[0]:
                    refused.set()
                    raise google.GoogleError('Fictional final permission loss', 403, 'insufficientFilePermissions')
                entered.set()
                self.assertTrue(release.wait(timeout=3))
            return original(token, fid, fields)

        self.metadata.side_effect = metadata

        def request():
            try:
                with combined.source_request():
                    for fid in ids:
                        combined.read_source('fictional-token', fid)
                    self.current_phase = 'bookend'
            finally:
                self.assertIsNone(combined._request_captures.get())

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(request)
            try:
                self.assertTrue(entered.wait(timeout=3))
                self.assertTrue(refused.wait(timeout=3))
                self.assertFalse(future.done())
            finally:
                release.set()
            with self.assertRaises(google.GoogleError):
                future.result(timeout=3)
        self.assertEqual(self.exports, Counter({fid: 1 for fid in ids}))


if __name__ == '__main__':
    unittest.main()
