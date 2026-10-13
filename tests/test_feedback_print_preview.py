"""Synthetic private HTTPS preview acceptance; no provider or school writes."""
import base64
import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from pypdf import PdfWriter

from hub_v2 import app, feedback_print_preview as preview, google
from school_workflow_hub.core.cloud_state import StoreUnavailable, bounded_json
from tests.test_cloud_state import MemoryCAS


def pdf_bytes(pages=1, text='Synthetic print preview'):
    output, writer = io.BytesIO(), PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    writer.add_metadata({'/Subject': text})
    writer.write(output)
    return output.getvalue()


class FeedbackPrintPreviewTests(unittest.TestCase):
    def setUp(self):
        self.port, self.raw = MemoryCAS(), pdf_bytes()
        self.ids = ['FictionalReportVersion002', 'FictionalReportVersion001']
        self.config = {'HUB_STATE_PROBE_ORIGINS': 'https://school.invalid'}
        self.store = patch.object(preview, 'configured_store', return_value=self.port).start()
        self.assemble = patch.object(preview.native_feedback, 'print_today',
            side_effect=lambda token, config, ids: (self.raw, 'Existing.pdf')).start()
        patch.object(google, 'service_token', return_value='fictional-read-only-token').start()
        self.addCleanup(patch.stopall)

    def prepare(self):
        return preview.prepare('read-only-token', self.config, self.ids)

    def call(self, method, path, *, body=None, query='', origin='https://school.invalid'):
        raw = json.dumps(body).encode() if body is not None else b''
        environ = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query,
            'HTTP_X_GOOG_IAP_JWT_ASSERTION': 'fictional-assertion', 'HTTP_ORIGIN': origin,
            'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        result = {}
        def start(status, headers):
            result.update(status=status, headers=dict(headers))
        result['body'] = b''.join(app.application(environ, start, self.config))
        return result

    def test_exact_preview_uses_private_namespace_and_repeat_reuses_immutable_bytes_without_delivery_changes(self):
        self.port.commit({'dailyfeedbackdates_' + 'a' * 64: ({'unchanged': 'daily index'}, None)})
        prior = deepcopy(self.port.rows)
        one = self.prepare()
        count = self.port.sequence
        again = self.prepare()
        self.assertEqual(one['id'], again['id']); self.assertTrue(again['cached'])
        self.assertEqual(self.port.sequence, count)
        self.assertTrue(one['preview_only']); self.assertFalse(one['daily_document_saved'])
        self.assertFalse(one['delivery_changed']); self.assertEqual(one['source_count'], 2)
        self.assertTrue(one['pdf_url'].startswith('/v2/api/feedback/preview/pdf?id=feedbackprintpreview_'))
        self.assertEqual(preview.pdf(self.config, one['id'])[0], self.raw)
        for ident, value in prior.items():
            self.assertEqual(self.port.rows[ident], value)
        self.assertTrue(all(ident in prior or ident.startswith(('feedbackprintpreview_', 'feedbackpreviewchunk_'))
                            for ident in self.port.rows))
        self.assertEqual(self.assemble.call_args.args[2], sorted(self.ids))

    def test_changed_pdf_bytes_create_another_preview_and_preserve_the_earlier_one(self):
        old_raw, before = self.raw, self.prepare()
        self.raw = pdf_bytes(text='Changed synthetic report version')
        after = self.prepare()
        self.assertNotEqual(before['id'], after['id'])
        self.assertEqual(preview.pdf(self.config, before['id'])[0], old_raw)
        self.assertEqual(preview.pdf(self.config, after['id'])[0], self.raw)

    def test_large_preview_uses_bounded_records_and_batches_with_complete_readback(self):
        self.raw = pdf_bytes(text='x' * (preview.CHUNK_BYTES * 22))
        reads, commits = [], []
        original_read, original_commit = self.port.read, self.port.commit
        def read(ids):
            reads.append(len(ids)); return original_read(ids)
        def commit(values):
            commits.append(len(values)); return original_commit(values)
        with patch.object(self.port, 'read', side_effect=read), patch.object(self.port, 'commit', side_effect=commit):
            result = self.prepare()
            self.assertEqual(preview.pdf(self.config, result['id'])[0], self.raw)
        self.assertGreater(len(self.port.rows), preview.BATCH)
        self.assertLessEqual(max(reads + commits), preview.BATCH)
        for value in self.port.rows.values():
            bounded_json(value.value)

    def test_unavailable_storage_is_checked_before_assembling_and_never_claims_ready(self):
        self.store.side_effect = StoreUnavailable('private-offline')
        with self.assertRaises(StoreUnavailable): self.prepare()
        self.assemble.assert_not_called()

    def test_an_uncertain_commit_is_only_read_back_and_never_retried(self):
        self.port.fail_after_commit = True
        result = self.prepare()
        self.assertEqual(preview.pdf(self.config, result['id'])[0], self.raw)
        self.assertEqual(self.port.sequence, 2, 'one chunk commit and one manifest commit, no retry')
        other = MemoryCAS()
        with patch.object(preview, 'configured_store', return_value=other), \
                patch.object(other, 'commit', side_effect=StoreUnavailable('offline')) as writes:
            with self.assertRaises(StoreUnavailable): self.prepare()
            self.assertEqual(writes.call_count, 1)
            self.assertEqual(other.rows, {})

    def test_incomplete_or_tampered_bytes_never_return_a_pdf(self):
        result = self.prepare()
        original = deepcopy(self.port.rows)
        record = self.port.rows[result['id']].value
        chunk = record['chunk_ids'][0]
        for alteration in ('missing', 'foreign_id', 'wrong_bytes', 'wrong_hash', 'daily_manifest'):
            self.port.rows = deepcopy(original)
            if alteration == 'missing': self.port.rows.pop(chunk)
            elif alteration == 'foreign_id': self.port.rows[chunk].value['preview_id'] = 'feedbackprintpreview_' + 'b' * 64
            elif alteration == 'wrong_bytes': self.port.rows[chunk].value['bytes_b64'] = base64.b64encode(b'%PDFchanged').decode()
            elif alteration == 'wrong_hash': self.port.rows[chunk].value['sha256'] = 'c' * 64
            else: self.port.rows[result['id']].value['preview_only'] = False
            with self.subTest(alteration=alteration), self.assertRaises(preview.Held):
                preview.pdf(self.config, result['id'])

    def test_invalid_or_unbounded_selection_and_private_ids_are_rejected_before_state_access(self):
        for values in ([], self.ids * 2, ['short'], [True], ['https://school.invalid'], self.ids * 51):
            with self.subTest(values=values), self.assertRaises(preview.Held):
                preview.prepare('read', self.config, values)
        self.store.assert_not_called(); self.assemble.assert_not_called()
        for ident in ('dailyfeedbackpacket_' + 'a' * 64, '../private', 'feedbackprintpreview_' + 'a' * 63, True):
            with self.assertRaises(preview.Held): preview.pdf(self.config, ident)
        self.store.assert_not_called()

    def test_oversized_unreadable_or_excessive_page_previews_are_held_before_storage(self):
        for raw in (b'not-pdf', b'%PDF-invalid'):
            self.raw = raw
            with self.assertRaises(preview.Held): self.prepare()
        self.raw = pdf_bytes(pages=2)
        with patch.object(preview, 'MAX_PAGES', 1), self.assertRaises(preview.Held): self.prepare()
        with patch.object(preview, 'MAX_BYTES', 1), self.assertRaises(preview.Held): self.prepare()
        self.assertEqual(self.port.rows, {})

    def test_teacher_authentication_precedes_post_and_get_access_and_same_origin_is_required(self):
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired('sign in')):
            self.assertTrue(self.call('POST', '/v2/api/feedback/preview', body={'ids': self.ids})['status'].startswith('401'))
            self.assertTrue(self.call('GET', '/v2/api/feedback/preview/pdf', query='id=feedbackprintpreview_' + 'a' * 64)['status'].startswith('401'))
        with patch.object(google, 'verify_teacher', return_value='teacher'):
            self.assertTrue(self.call('POST', '/v2/api/feedback/preview', body={'ids': self.ids}, origin='https://foreign.invalid')['status'].startswith('403'))
        self.store.assert_not_called(); self.assemble.assert_not_called()

    def test_https_prepare_and_pdf_routes_return_exact_private_pdf_without_google_write_tokens(self):
        with patch.object(google, 'verify_teacher', return_value='teacher'), \
                patch.object(google, 'service_token', return_value='read-token') as token:
            made = self.call('POST', '/v2/api/feedback/preview', body={'ids': self.ids})
            self.assertEqual(made['status'], '200 OK')
            descriptor = json.loads(made['body'])
            opened = self.call('GET', '/v2/api/feedback/preview/pdf', query='id=' + descriptor['id'])
            self.assertEqual(opened['status'], '200 OK'); self.assertEqual(opened['body'], self.raw)
            self.assertEqual(opened['headers']['Content-Type'], 'application/pdf')
            self.assertEqual(opened['headers']['Cache-Control'], 'no-store')
            self.assertIn('inline; filename="Feedback-print-preview-', opened['headers']['Content-Disposition'])
            self.assertEqual(token.call_count, 1)
            self.assertNotIn('write', token.call_args.kwargs)

    def test_route_payload_and_query_bounds_never_accept_another_saved_namespace(self):
        with patch.object(google, 'verify_teacher', return_value='teacher'):
            for body in ({'ids': self.ids, 'date': '2032-01-01'}, {'ids': ['x' * 26_000]}, {'ids': []}):
                self.assertNotEqual(self.call('POST', '/v2/api/feedback/preview', body=body)['status'], '200 OK')
            for query in ('id=dailyfeedbackpacket_' + 'a' * 64, 'id=x&id=y', 'id=x&other=y'):
                self.assertNotEqual(self.call('GET', '/v2/api/feedback/preview/pdf', query=query)['status'], '200 OK')
            self.assertTrue(self.call('PUT', '/v2/api/feedback/preview', body={'ids': self.ids})['status'].startswith('405'))
        self.assertEqual(self.port.rows, {})

    def test_unavailable_pdf_route_stays_unavailable_and_exposes_no_private_storage_error(self):
        result = self.prepare()
        self.store.side_effect = StoreUnavailable('private-database-details')
        with patch.object(google, 'verify_teacher', return_value='teacher'):
            failed = self.call('GET', '/v2/api/feedback/preview/pdf', query='id=' + result['id'])
        self.assertEqual(failed['status'], '503 Service Unavailable')
        self.assertNotIn(b'private-database-details', failed['body'])


if __name__ == '__main__': unittest.main()
