"""Fake HTTP only: refresh budgets cannot read school sources."""
import json
import sys
import types
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import submission_refresh_reader as bounded
from school_workflow_hub.adapters.hosted_agenda import ConnectionError

ID = "synthetic_source_1234567890"


class Response:
    def __init__(self, chunks=(b"{}",), status=200, headers=None, before_chunk=None):
        self.chunks, self.status_code = chunks, status
        self.headers = headers or {}
        self.closed, self.yielded = False, 0
        self.before_chunk = before_chunk

    def iter_content(self, chunk_size):
        self.chunk_size = chunk_size
        for chunk in self.chunks:
            if self.before_chunk:
                self.before_chunk()
            self.yielded += 1
            yield chunk

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.headers = {}
        self.trust_env = True

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class BudgetTests(unittest.TestCase):
    def reader(self, *responses):
        session = Session(responses)
        fake = types.SimpleNamespace(Session=lambda: session)
        with patch.dict(sys.modules, {"requests": fake}):
            reader = bounded.BudgetedDrive("synthetic-token")
        return reader, session

    def test_metadata_and_children_use_one_authenticated_official_stream(self):
        responses = [Response([json.dumps({"id": ID}).encode()]), Response([b'{"files":[]}'])]
        reader, session = self.reader(*responses)
        self.assertEqual(reader.metadata(ID), {"id": ID})
        self.assertEqual(reader.children_of([ID], None), {"files": []})
        self.assertEqual(session.headers["Authorization"], "Bearer synthetic-token")
        self.assertFalse(session.trust_env)
        self.assertEqual(reader.stats["requests"], 2)
        for url, options in session.calls:
            self.assertTrue(url.startswith("https://www.googleapis.com/drive/v3/files"))
            self.assertTrue(options["stream"])
            self.assertFalse(options["allow_redirects"])
        self.assertTrue(all(r.closed for r in responses))

    def test_native_export_and_pptx_download_use_existing_extractor(self):
        reader, session = self.reader(Response([b"native"]), Response([b"upload"]))
        with patch.object(bounded, "_pptx_text", side_effect=["native text", "uploaded text"]) as extract:
            self.assertEqual(reader.read_deck({"id": ID, "mimeType": bounded.NATIVE}), "native text")
            self.assertEqual(reader.read_deck({"id": ID, "mimeType": bounded.PPTX}), "uploaded text")
        self.assertEqual(extract.call_args_list[0].args, (b"native",))
        self.assertEqual(session.calls[0][0], "https://www.googleapis.com/drive/v3/files/" + ID + "/export")
        self.assertEqual(session.calls[0][1]["params"], {"mimeType": bounded.PPTX})
        self.assertEqual(session.calls[1][1]["params"]["alt"], "media")
        self.assertEqual(reader.stats["exports"], 2)

    def test_form_structure_uses_existing_token_bounded_stream_and_fresh_recheck(self):
        payload = {"formId": ID, "revisionId": "first", "items": []}
        changed = {**payload, "revisionId": "second"}
        reader, session = self.reader(Response([json.dumps(payload).encode()]),
                                      Response([json.dumps(changed).encode()]))
        self.assertEqual(reader.form(ID), payload)
        self.assertEqual(reader.form(ID), payload)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(reader.form(ID, fresh=True), changed)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][0], "https://forms.googleapis.com/v1/forms/" + ID)
        self.assertIn("folderId", session.calls[0][1]["params"]["fields"])
        self.assertEqual(session.headers["Authorization"], "Bearer synthetic-token")
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertTrue(session.calls[0][1]["stream"])

    def test_form_wrong_identity_or_access_failure_has_safe_error(self):
        reader, _ = self.reader(Response([b'{"formId":"wrong"}']))
        with self.assertRaisesRegex(ConnectionError, "submission_form_structure_invalid"):
            reader.form(ID)
        reader, _ = self.reader(Response([b'private provider body'], status=403))
        with self.assertRaisesRegex(ConnectionError, "^submission_form_access_required$"):
            reader.form(ID)

    def test_unsupported_mime_and_external_path_never_issue_request(self):
        reader, session = self.reader()
        with self.assertRaisesRegex(ConnectionError, "intake_deck_unsupported"):
            reader.read_deck({"id": ID, "mimeType": "application/pdf"})
        with self.assertRaisesRegex(ConnectionError, "invalid_submission_scope"):
            reader.get("https://example.invalid/file", {})
        self.assertEqual(session.calls, [])

    def test_streamed_per_file_limit_closes_without_buffering_remainder(self):
        response = Response([b"1234", b"5678", b"not-read"])
        reader, session = self.reader(response)
        with patch.object(bounded, "MAX_FILE_BYTES", 6):
            with self.assertRaisesRegex(ConnectionError, "submission_refresh_budget_exhausted"):
                reader.read_deck({"id": ID, "mimeType": bounded.PPTX})
        self.assertTrue(response.closed)
        self.assertEqual(response.yielded, 2)
        self.assertTrue(reader.exhausted)
        self.assertLessEqual(reader.bytes_read, 6)
        with self.assertRaisesRegex(ConnectionError, "budget_exhausted"):
            reader.metadata(ID)
        self.assertEqual(len(session.calls), 1)

    def test_aggregate_byte_limit_includes_metadata(self):
        first, second = Response([b"{}"]), Response([b"12345"])
        reader, _ = self.reader(first, second)
        with patch.object(bounded, "MAX_TOTAL_BYTES", 6):
            reader.metadata(ID)
            with self.assertRaisesRegex(ConnectionError, "budget_exhausted"):
                reader.read_deck({"id": ID, "mimeType": bounded.PPTX})
        self.assertTrue(second.closed)
        self.assertEqual(reader.bytes_read, 2)

    def test_content_length_blocks_oversized_download_before_first_chunk(self):
        response = Response([b"not-read"], headers={"Content-Length": str(bounded.MAX_FILE_BYTES + 1)})
        reader, _ = self.reader(response)
        with self.assertRaisesRegex(ConnectionError, "budget_exhausted"):
            reader.read_deck({"id": ID, "mimeType": bounded.NATIVE})
        self.assertEqual(response.yielded, 0)
        self.assertTrue(response.closed)

    def test_deadline_before_request_and_during_stream(self):
        reader, session = self.reader()
        with patch.object(bounded.time, "monotonic", return_value=reader.deadline):
            with self.assertRaisesRegex(ConnectionError, "budget_exhausted"):
                reader.check()
        self.assertTrue(reader.exhausted)
        self.assertEqual(session.calls, [])
        clock = [0.0]
        response = Response([b"{}"], before_chunk=lambda: clock.__setitem__(0, 181.0))
        with patch.object(bounded.time, "monotonic", side_effect=lambda: clock[0]):
            reader, _ = self.reader(response)
            with self.assertRaisesRegex(ConnectionError, "budget_exhausted"):
                reader.metadata(ID)
        self.assertTrue(response.closed)
        self.assertEqual(reader.bytes_read, 0)

    def test_request_and_export_limits_prevent_another_http_call(self):
        reader, session = self.reader(Response())
        reader.request_count = bounded.MAX_REQUESTS - 1
        reader.metadata(ID)
        self.assertEqual(reader.request_count, bounded.MAX_REQUESTS)
        with self.assertRaisesRegex(ConnectionError, "budget_exhausted"):
            reader.metadata(ID)
        self.assertEqual(len(session.calls), 1)
        reader, session = self.reader(Response([b"synthetic-pptx"]))
        reader.export_count = bounded.MAX_EXPORTS - 1
        with patch.object(bounded, "_pptx_text", return_value="synthetic text"):
            reader.read_deck({"id": ID, "mimeType": bounded.PPTX})
        self.assertEqual(reader.export_count, bounded.MAX_EXPORTS)
        with self.assertRaisesRegex(ConnectionError, "budget_exhausted"):
            reader.read_deck({"id": ID, "mimeType": bounded.PPTX})
        self.assertEqual(len(session.calls), 1)

    def test_access_scope_and_transport_errors_keep_safe_codes(self):
        body = json.dumps({"error": {"errors": [{"reason": "insufficientPermissions"}]}}).encode()
        response = Response([body], status=403)
        reader, _ = self.reader(response)
        with self.assertRaisesRegex(ConnectionError, "submission_reader_scope_missing"):
            reader.metadata(ID)
        self.assertTrue(response.closed)
        reader, session = self.reader()
        with patch.object(session, "get", side_effect=RuntimeError("private exception content")):
            with self.assertRaisesRegex(ConnectionError, "^submission_source_unavailable$"):
                reader.metadata(ID)
        self.assertFalse(reader.exhausted)


if __name__ == "__main__":
    unittest.main()
