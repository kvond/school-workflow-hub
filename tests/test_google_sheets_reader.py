import io
import copy
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from school_workflow_hub.adapters.google_sheets import (
    MAX_BYTES, SheetsReader, SheetsReadError, _NoRedirect,
    compare_fingerprints, fingerprint_snapshot, validate_ranges,
)
from scripts import read_agenda

SOURCE = "synthetic_sheet_0123456789"
TOKEN = "fictional-test-token"
RANGE = "'Synthetic Agenda'!A1:D10"
KEY = b"f" * 32
GRADING_TITLES = ("Assignments", "Submissions", "Answers", "Class summaries")
GRADING_RANGES = tuple("'" + title + "'!A1:Z1000" for title in GRADING_TITLES)


def sample():
    return {"spreadsheetId": SOURCE, "properties": {"title": "Private fixture title"},
            "sheets": [{"properties": {"sheetId": 7, "title": "Synthetic Agenda", "index": 0},
                        "data": [{"rowData": [{"values": [{"formattedValue": "private fixture text",
                                   "hyperlink": "https://example.invalid/copy"}]}]}]}]}


def grading_sample():
    result = sample()
    result["sheets"] = [{"properties": {"sheetId": index, "title": title}, "data": []}
                        for index, title in enumerate(GRADING_TITLES)]
    return result


class Opener:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return io.BytesIO(result if isinstance(result, bytes) else json.dumps(result).encode())


class SheetsReaderTests(unittest.TestCase):
    def test_metadata_reads_only_one_explicit_source_without_grid(self):
        opener = Opener(sample())
        result = SheetsReader(TOKEN, opener).read(SOURCE)
        request, timeout = opener.calls[0]
        self.assertEqual(result, sample())
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(timeout, 20)
        self.assertEqual(urlsplit(request.full_url).netloc, "sheets.googleapis.com")
        self.assertNotIn("data(", parse_qs(urlsplit(request.full_url).query)["fields"][0])
        self.assertNotIn(TOKEN, request.full_url)
        self.assertNotIn(TOKEN, repr(SheetsReader(TOKEN)))

    def test_grid_read_bounds_and_hyperlink_fields(self):
        opener = Opener(sample())
        SheetsReader(TOKEN, opener).read(SOURCE, (RANGE,))
        query = parse_qs(urlsplit(opener.calls[0][0].full_url).query)
        self.assertEqual(query["ranges"], [RANGE])
        self.assertIn("hyperlink", query["fields"][0])
        self.assertIn("textFormatRuns", query["fields"][0])
        self.assertIn("textFormatRuns(startIndex,format(link(uri)))", query["fields"][0])
        self.assertEqual(query["fields"][0].count("("), query["fields"][0].count(")"))

    def test_dedicated_grading_read_uses_one_bounded_get_for_exact_four_grids(self):
        opener = Opener(grading_sample())
        result = SheetsReader(TOKEN, opener).read_grading_tables(SOURCE, GRADING_RANGES)
        self.assertEqual(result, grading_sample())
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(timeout, 20)
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["ranges"], list(GRADING_RANGES))
        self.assertIn("formattedValue", query["fields"][0])
        self.assertIn("effectiveValue(numberValue)", query["fields"][0])
        generic_opener = Opener()
        with self.assertRaisesRegex(SheetsReadError, "range_limit"):
            SheetsReader(TOKEN, generic_opener).read(SOURCE, GRADING_RANGES)
        self.assertFalse(generic_opener.calls)

    def test_dedicated_grading_bounds_cannot_read_other_or_unbounded_sources(self):
        invalid = [(), GRADING_RANGES[:3], GRADING_RANGES + (GRADING_RANGES[0],), list(GRADING_RANGES),
                   (GRADING_RANGES[0],) * 4]
        for replacement in ("'Other'!A1:Z1000", "'Assignments'!A2:Z1000", "'Assignments'!B1:Z1000",
                            "'Assignments'!A1:AO10", "'Assignments'!A1:AN10001", "'Assignments'!A:Z"):
            invalid.append((replacement, *GRADING_RANGES[1:]))
        for ranges in invalid:
            opener = Opener()
            with self.subTest(ranges=ranges), self.assertRaises(SheetsReadError):
                SheetsReader(TOKEN, opener).read_grading_tables(SOURCE, ranges)
            self.assertFalse(opener.calls)
        opener = Opener(grading_sample())
        maximum = tuple("'" + title + "'!A1:AN10000" for title in GRADING_TITLES)
        SheetsReader(TOKEN, opener).read_grading_tables(SOURCE, maximum)
        self.assertEqual(len(opener.calls), 1)

    def test_dedicated_grading_read_keeps_response_cap_and_rejects_missing_or_duplicate_tabs(self):
        opener = Opener(b"x" * (MAX_BYTES + 1))
        with self.assertRaisesRegex(SheetsReadError, "response_limit"):
            SheetsReader(TOKEN, opener).read_grading_tables(SOURCE, GRADING_RANGES)
        for change in (lambda result: result["sheets"].pop(),
                       lambda result: result["sheets"][1]["properties"].update(title="Assignments"),
                       lambda result: result["sheets"].append(copy.deepcopy(result["sheets"][0]))):
            result = grading_sample(); change(result)
            with self.assertRaisesRegex(SheetsReadError, "invalid_response"):
                SheetsReader(TOKEN, Opener(result)).read_grading_tables(SOURCE, GRADING_RANGES)

    def test_unbounded_malformed_oversized_duplicate_ranges_fail_before_network(self):
        for ranges in (("Sheet1!A:Z",), ("'Sheet1'!A1:Z9999",), (RANGE,) * 2,
                       ("'Sheet1'!B4:A1",), ("'Sheet1'!A0:B1",), (RANGE,) * 5, [RANGE]):
            opener = Opener()
            with self.assertRaises(SheetsReadError):
                SheetsReader(TOKEN, opener).read(SOURCE, ranges)
            self.assertEqual(opener.calls, [])
        self.assertEqual(validate_ranges(("'Teacher''s Agenda'!A1:B2",)), ("'Teacher''s Agenda'!A1:B2",))

    def test_source_id_cannot_redirect_or_inject_queries(self):
        for source in ("https://example.invalid", SOURCE + "?x=y", "", None):
            opener = Opener()
            with self.assertRaises(SheetsReadError):
                SheetsReader(TOKEN, opener).read(source)
            self.assertEqual(opener.calls, [])

    def test_credentials_reject_header_injection(self):
        for token in ("", "a\nb", "a\rb", "two words", None):
            with self.assertRaises(SheetsReadError):
                SheetsReader(token)

    def test_redirect_never_reuses_authorization_at_new_host(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid"))
        opener = Opener(HTTPError("private-url", 302, "private-error", {}, None))
        with self.assertRaisesRegex(SheetsReadError, "^source_unavailable$"):
            SheetsReader(TOKEN, opener).read(SOURCE)
        self.assertEqual(len(opener.calls), 1)

    def test_transient_errors_retry_with_fixed_bound(self):
        sleeps = []
        opener = Opener(HTTPError("private", 429, "secret", {}, None), URLError("private"), sample())
        self.assertEqual(SheetsReader(TOKEN, opener, sleeps.append).read(SOURCE), sample())
        self.assertEqual(sleeps, [1, 2])

    def test_auth_errors_are_not_retried_and_never_leak_body(self):
        for status, code in ((401, "authentication_required"), (403, "access_denied"), (404, "source_not_found")):
            opener = Opener(HTTPError("private-url", status, "private-secret", {}, io.BytesIO(b"private-body")))
            with self.assertRaisesRegex(SheetsReadError, "^" + code + "$"):
                SheetsReader(TOKEN, opener).read(SOURCE)
            self.assertEqual(len(opener.calls), 1)

    def test_exhausted_retries_redact_errors(self):
        opener = Opener(*(URLError("private-token") for _ in range(3)))
        with self.assertRaisesRegex(SheetsReadError, "^source_unavailable$"):
            SheetsReader(TOKEN, opener, lambda _: None).read(SOURCE)
        self.assertEqual(len(opener.calls), 3)

    def test_exhausted_http_status_keeps_safe_throttling_and_transient_diagnostics(self):
        for status in (429, 500, 502, 503, 504):
            errors = [HTTPError("private-url", status, "private-token", {}, io.BytesIO(b"private-body"))
                      for _ in range(3)]
            opener = Opener(*errors); sleeps = []
            code = "source_rate_limited" if status == 429 else "source_transient_failure"
            with self.subTest(status=status), self.assertRaisesRegex(SheetsReadError, "^" + code + "$"):
                SheetsReader(TOKEN, opener, sleeps.append).read(SOURCE)
            self.assertEqual(sleeps, [1, 2])
            self.assertEqual(len(opener.calls), 3)
            self.assertTrue(all(error.closed for error in errors))

    def test_response_limit_and_malformed_wrong_source_fail(self):
        wrong = sample()
        wrong["spreadsheetId"] = "different-source"
        duplicate = sample()
        duplicate["sheets"] *= 2
        for body in (b"x" * (MAX_BYTES + 1), b"invalid private response", b"\xff", [], {}, wrong, duplicate):
            with self.assertRaises(SheetsReadError):
                SheetsReader(TOKEN, Opener(body)).read(SOURCE)

    def test_fingerprint_replay_redacts_all_raw_values_and_detects_edit(self):
        first = fingerprint_snapshot(sample(), KEY, (RANGE,))
        self.assertEqual(compare_fingerprints(first, fingerprint_snapshot(sample(), KEY, (RANGE,)))["status"], "unchanged")
        edited = sample()
        edited["sheets"][0]["data"][0]["rowData"][0]["values"][0]["hyperlink"] = "https://example.invalid/edit"
        second = fingerprint_snapshot(edited, KEY, (RANGE,))
        self.assertEqual(compare_fingerprints(first, second), {"status": "changed", "change_proven": True})
        for secret in (SOURCE, "Private fixture title", "private fixture text", "https://", TOKEN):
            self.assertNotIn(secret, json.dumps(first))
        self.assertFalse(first["student_access_verified"])

    def test_baseline_and_scope_changes_do_not_claim_observed_changes(self):
        first = fingerprint_snapshot(sample(), KEY, (RANGE,))
        self.assertEqual(compare_fingerprints(None, first)["status"], "baseline_required")
        for second in (fingerprint_snapshot(sample(), b"g" * 32, (RANGE,)),
                       fingerprint_snapshot(sample(), KEY)):
            self.assertEqual(compare_fingerprints(first, second), {"status": "scope_changed", "change_proven": False})

    def test_bad_baseline_and_key_are_rejected(self):
        with self.assertRaises(SheetsReadError):
            fingerprint_snapshot(sample(), b"short")
        with self.assertRaises(SheetsReadError):
            compare_fingerprints({}, fingerprint_snapshot(sample(), KEY))

    def test_missing_configuration_cli_is_blocked_without_network(self):
        output = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), patch("sys.argv", ["read_agenda.py"]), \
                patch("sys.stdout", output), patch.object(read_agenda, "SheetsReader") as reader:
            self.assertEqual(read_agenda.main(), 2)
            reader.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["status"], "blocked")

    def test_cli_prints_only_opaque_observation(self):
        output = io.StringIO()
        env = {"HUB_GOOGLE_ACCESS_TOKEN": TOKEN, "HUB_AGENDA_SPREADSHEET_ID": SOURCE,
               "HUB_OBSERVATION_KEY_HEX": KEY.hex()}
        with patch.dict("os.environ", env, clear=True), patch("sys.argv", ["read_agenda.py", "--range", RANGE]), \
                patch("sys.stdout", output), patch.object(read_agenda.SheetsReader, "read", return_value=sample()):
            self.assertEqual(read_agenda.main(), 0)
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertNotIn(SOURCE, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["range_count"], 1)
